"""Structural wire contracts for graph, event analysis and review patches."""
import copy
import math

from .semantic_contract import object_schema, unresolved

SCORE = {'type': 'number', 'minimum': 0, 'maximum': 10}
TEXT = {'type': 'string'}
ROLES = ['dialogue', 'thought', 'narration', 'sfx', 'action_state', 'ui', 'watermark', 'unknown']
KINDS = ['person', 'narrator', 'others']
RECIPIENTS = ['individual', 'group', 'self', 'audience', 'unknown', 'not_applicable']
LINKS = ['next', 'continues', 'related', 'references']


def enum(values, nullable=False):
    if values is None:
        return {'type': ['string', 'null'] if nullable else 'string'}
    values = list(dict.fromkeys(values))
    if nullable:
        values.append(None)
    return {'type': ['string', 'null'] if nullable else 'string', 'enum': values}


def row_schema(people, clusters, candidates, targets):
    person = enum(people, True)
    mention = object_schema({
        'candidate_id': enum(candidates) if candidates else TEXT,
        'confirmed_person_name': {'type': 'boolean'},
        'mention_type': enum(['direct_address', 'introduction', 'reference', 'unknown']),
        'target_person_id': person, 'recipient_cluster_ref': enum(clusters, True),
        'visual_support': enum(['compatible', 'different_appearance', 'unknown']),
        'components': {'type': 'array', 'items': object_schema({
            'kind': enum(['given_name', 'middle_name', 'surname', 'nickname', 'full_name']), 'value': TEXT})},
        'confidence': object_schema({'mention_type': SCORE, 'target': SCORE}), 'evidence': TEXT})
    return object_schema({
        'utterance_id': enum(targets), 'content_type': enum(ROLES),
        'speech_style': enum(['normal', 'whisper', 'shout', 'thought', 'caption', 'unknown']),
        'speaker_kind': enum(KINDS), 'speaker_person_id': person,
        'speaker_cluster_ref': enum(clusters, True), 'addressee_type': enum(RECIPIENTS),
        'addressee_person_ids': {'type': 'array', 'items': enum(people) if people else TEXT,
                                **({'maxItems': 0} if people == [] else {})},
        'mentions': {'type': 'array', 'items': mention, **({'maxItems': 0} if not candidates else {})},
        'confidence': object_schema({k: SCORE for k in ('speaker', 'addressee', 'content_type', 'speech_style')}),
        'evidence': TEXT, 'evidence_refs': {'type': 'array', 'items': TEXT}})


def score(value):
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 10:
        raise ValueError('Confidence must be a finite number from 0 to 10')
    return value


def short_title(value, fallback):
    return ' '.join((str(value or fallback).split() or [fallback])[:4])


def validate_row(value, source, state, candidates):
    """Check references and shape, not proof spans, name ownership or caption semantics."""
    item = copy.deepcopy(value)
    if not isinstance(item, dict) or item.get('utterance_id') != source['id']:
        raise ValueError('Unknown or missing utterance ID')
    for field, allowed in (('content_type', ROLES), ('speaker_kind', KINDS), ('addressee_type', RECIPIENTS),
                           ('speech_style', ['normal', 'whisper', 'shout', 'thought', 'caption', 'unknown'])):
        if item.get(field) not in allowed:
            raise ValueError('Invalid ' + field)
    for key in ('speaker', 'addressee', 'content_type', 'speech_style'):
        score(item.get('confidence', {}).get(key))
    people, observations = state['persons'], state['cluster_to_person']
    pid, ref = item.get('speaker_person_id'), item.get('speaker_cluster_ref')
    if pid is not None and (pid not in people or people[pid].get('story_reference_only')):
        raise ValueError('Speaker must use an existing visual person reference')
    if (item['speaker_kind'] == 'person') != (pid is not None):
        raise ValueError('Person speaker must select a person')
    if ref is not None and (ref not in observations or observations[ref] != pid):
        raise ValueError('Speaker and cluster IDs disagree')
    addresses = item.get('addressee_person_ids')
    if not isinstance(addresses, list) or len(addresses) != len(set(addresses)) or any(
            p not in people or people[p].get('story_reference_only') for p in addresses):
        raise ValueError('Addressees must use existing visual person references')
    expected = {k for k, c in candidates.items() if c['utterance_id'] == source['id']}
    mentions = item.get('mentions')
    if not isinstance(mentions, list) or len(mentions) != len(expected) or {
            m.get('candidate_id') for m in mentions if isinstance(m, dict)} != expected:
        raise ValueError('Mention IDs must match this utterance')
    for mention in mentions:
        mid = mention.get('target_person_id')
        kind = mention.get('mention_type')
        if kind not in {'direct_address', 'introduction', 'reference', 'unknown'}:
            raise ValueError('Invalid mention type; dialogue cannot be narrator_introduction')
        if mid is not None and (mid not in people or
                (people[mid].get('story_reference_only') and kind != 'reference')):
            raise ValueError('Only reference may name a person without visual observations')
        cluster = mention.get('recipient_cluster_ref')
        if cluster is not None and (cluster not in observations or observations[cluster] != mid):
            raise ValueError('Mention and cluster IDs disagree')
        if type(mention.get('confirmed_person_name')) is not bool or not isinstance(mention.get('components'), list):
            raise ValueError('Invalid name decision shape')
        if mention['confirmed_person_name'] and mid is None:
            raise ValueError('A confirmed name must select its target person')
        for key in ('mention_type', 'target'):
            score(mention.get('confidence', {}).get(key))
        if not isinstance(mention.get('evidence'), str):
            raise ValueError('Mention reason must be text')
    if not isinstance(item.get('evidence'), str) or not isinstance(item.get('evidence_refs'), list):
        raise ValueError('Missing decision explanation')
    item.update(semantic_resolution='resolved', semantic_response_used=True, repair_count=0,
                semantic_needs_review=any(c < 6 for c in item['confidence'].values()),
                semantic_errors=[], speaker_merge_policy='inference-first-v1')
    for key in ('scene_id', 'event_id', 'essential_index', 'analysis_branch', 'speaker_anchor', 'original_speaker', 'bubble_tail'):
        if key in source:
            item[key] = copy.deepcopy(source[key])
    return item


def failed_row(source, candidates, error):
    value = unresolved(source['id'], candidates, [str(error)])
    value.update(speaker_kind='others', addressee_type='unknown', repair_count=0,
                 confidence={k: 0 for k in ('speaker', 'addressee', 'content_type', 'speech_style')})
    for key in ('scene_id', 'event_id', 'essential_index', 'analysis_branch', 'speaker_anchor', 'original_speaker', 'bubble_tail'):
        if key in source:
            value[key] = copy.deepcopy(source[key])
    return value


def decisions(row):
    """Atomic semantic decisions and their independent confidence values."""
    fields = {'speaker': ('speaker_kind', 'speaker_person_id', 'speaker_cluster_ref'),
              'addressee': ('addressee_type', 'addressee_person_ids'),
              'content_type': ('content_type',), 'speech_style': ('speech_style',)}
    result = {k: ({f: row.get(f) for f in names}, row.get('confidence', {}).get(k, 0))
              for k, names in fields.items()}
    for mention in row.get('mentions', []):
        cid = mention['candidate_id']
        result['mention_type:' + cid] = (mention.get('mention_type'), mention.get('confidence', {}).get('mention_type', 0))
        result['mention_target:' + cid] = ({k: mention.get(k) for k in
            ('confirmed_person_name', 'target_person_id', 'recipient_cluster_ref', 'components')},
            mention.get('confidence', {}).get('target', 0))
    return result
