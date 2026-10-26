"""Versioned character profiles from ten main-member crops and grounded dialogue."""
import copy

from ..storage.io import atomic_json, fingerprint, read_json
from ..storage.locking import exclusive_lock
from . import persons
from .identity_v2 import POLICY, confirmed, membership, main_member
from .identity_runtime import freeze, retain_crops, run_jobs, evidence_refs, checked_evidence
from .semantic_contract import object_schema

PROFILE_POLICY = 'character-profiles-v2'
FIELDS = ('appearance', 'apparent_age_group', 'presentation', 'personality',
          'formality', 'brevity', 'roles', 'communication_behaviors')
PROMPT = '''Create or update ONE character profile in Vietnamese from the supplied
confirmed identity, previous profile, labelled representative crops and source
dialogue/descriptions. MAGI members are already linked: never change identities.
Infer shared appearance from ALL supplied crops, not a single expression. Apparent
age/presentation and personality inferred from images are provisional hypotheses,
not facts about exact age, gender, family or authority. These shared appearance
attributes form the default address anchor; explicit story evidence takes priority.
Use appearance, apparent_age_group, presentation, personality, formality, brevity,
roles, communication_behaviors ONLY. No habitual word choices or speech-tone field.
Do not choose pair-specific pronouns here. Each attribute has value, status,
basis and evidence_refs. Status confirmed requires dialogue evidence. Cite ONLY
supplied utterance/crop refs, or previous:<version> for unchanged old attributes.
For visual inferences cite labelled crops; apparent_age_group value must be one of
child, adolescent, young_adult, adult, older_adult, unknown. Use presentation value
male_presenting, female_presenting or unknown (an appearance estimate, not
confirmed biological gender). Unknown attributes use value unknown, status unknown,
basis unknown. No unsupported roles. JSON only.'''


def profile_path(store):
    return persons.registry_path(store).with_name('profiles-v2.json')


def empty_profiles():
    return {'policy': PROFILE_POLICY, 'records': {}, 'operations': []}


def read_profiles(path):
    result = read_json(path) if path.exists() else empty_profiles()
    if result.get('policy') != PROFILE_POLICY:
        raise ValueError('Incompatible profile store; old graphs cannot seed v2')
    return result


def eligible(state, metadata, minimum, prior_ids=()):
    members, observations = membership(state)
    prior = {persons.canonical(state, p) for p in prior_ids if p in state['persons'] or p in state.get('redirects', {})}
    result = {}
    for pid, mids in sorted(members.items()):
        if not confirmed(state, pid) or (len(observations[pid]) < minimum and pid not in prior):
            continue
        result[pid] = {'person_id': pid, 'primary_name': state['persons'][pid]['primary_name'],
            'aliases': sorted({f['surface'] for f in state['persons'][pid].get('name_forms', [])
                               if f.get('status') == 'confirmed'}),
            'magi_ids': sorted(mids), 'main_magi_id': main_member(mids, metadata),
            'chapter_appearances': len(observations[pid])}
    return result


def source_rows(document):
    from .identity_projection import document_view
    document = document_view(document)
    fields = ('id', 'text', 'content_type', 'speaker_person_id', 'addressee_ids',
              'mentions', 'semantic_resolution', 'scene_id', 'reading_order', 'communication_evidence')
    return [{k: row.get(k) for k in fields} for row in sorted(document['utterances'],
            key=lambda r: r.get('reading_order', 0))]


def profile_evidence(rows, pid, state):
    relevant = []
    for i, row in enumerate(rows):
        own = row.get('speaker_person_id') == pid and row.get('content_type') not in {'narration', 'ui', 'watermark', 'sfx'}
        named = any(m.get('confirmed_person_name') and m.get('target_person_id')
                    and persons.canonical(state, m['target_person_id']) == pid for m in row.get('mentions') or [])
        if row.get('semantic_resolution') == 'unresolved' or not (own or named):
            continue
        relevant.append({'priority': 2 if named else 1, 'trigger': row['id'],
            'rows': rows[max(0, i-1):min(len(rows), i+2)]})
    # Stable IDs/content, not crop changes or run timestamps, trigger updates.
    triggers = [next(r for r in s['rows'] if r['id'] == s['trigger']) for s in relevant]
    hashes = sorted({fingerprint([r['id'], r['text'], r['speaker_person_id'],
        sorted(m.get('target_person_id') for m in r.get('mentions') or []
               if m.get('confirmed_person_name') and m.get('target_person_id'))]) for r in triggers})
    return relevant, hashes


def schema(request):
    attribute = object_schema({'value': {'type': 'string'},
        'status': {'type': 'string', 'enum': ['confirmed', 'provisional', 'unknown']},
        'basis': {'type': 'string', 'enum': ['dialogue', 'visual', 'context', 'unknown']},
        'evidence_refs': {'type': 'array', 'items': {'type': 'string'}}})
    attributes = {f: copy.deepcopy(attribute) for f in FIELDS}
    attributes['apparent_age_group']['properties']['value']['enum'] = [
        'child', 'adolescent', 'young_adult', 'adult', 'older_adult', 'unknown']
    attributes['presentation']['properties']['value']['enum'] = ['male_presenting', 'female_presenting', 'unknown']
    return object_schema({'person_id': {'type': 'string', 'enum': [request['identity']['person_id']]},
                          'attributes': object_schema(attributes)})


def validate(value, request):
    if not isinstance(value, dict) or set(value) != {'person_id', 'attributes'}:
        raise ValueError('Invalid character profile fields')
    if value['person_id'] != request['identity']['person_id'] or set(value['attributes']) != set(FIELDS):
        raise ValueError('Profile identity/attribute coverage changed')
    refs = evidence_refs(request)
    previous = request.get('previous')
    if previous:
        refs.add('previous:' + previous['version'])
    dialogue = {r['id'] for s in request.get('evidence_segments', []) for r in s['rows']}
    visual = {c['crop_id'] for c in request.get('crops', [])}
    for field, item in value['attributes'].items():
        if (set(item) != {'value', 'status', 'basis', 'evidence_refs'}
                or not isinstance(item['value'], str) or not item['value'].strip()
                or item['status'] not in {'confirmed', 'provisional', 'unknown'}
                or item['basis'] not in {'dialogue', 'visual', 'context', 'unknown'}):
            raise ValueError('Invalid profile attribute')
        checked_evidence(item, refs, required=item['status'] != 'unknown')
        if item['status'] == 'unknown' and (item['value'] != 'unknown' or item['basis'] != 'unknown'):
            raise ValueError('Unknown profile attribute must remain unknown')
        inherited = previous and item['evidence_refs'] == ['previous:' + previous['version']]
        if inherited and item != {**previous['attributes'][field],
                                  'evidence_refs': item['evidence_refs']}:
            raise ValueError('Inherited profile attribute changed without new evidence')
        if item['status'] == 'confirmed' and not inherited and (item['basis'] != 'dialogue' or not dialogue.intersection(item['evidence_refs'])):
            raise ValueError('Confirmed profile attribute needs dialogue evidence')
        if item['basis'] == 'visual' and not inherited and not visual.intersection(item['evidence_refs']):
            raise ValueError('Appearance inference requires a supplied crop')
        if item['status'] != 'unknown' and item['basis'] == 'unknown':
            raise ValueError('Non-unknown profile attribute requires an evidence basis')
        if field == 'roles' and item['status'] != 'unknown' and not inherited and item['basis'] != 'dialogue':
            raise ValueError('Social roles require story evidence, not visual estimates')
    age = value['attributes']['apparent_age_group']['value']
    if age not in {'child', 'adolescent', 'young_adult', 'adult', 'older_adult', 'unknown'}:
        raise ValueError('Invalid apparent age group')
    if value['attributes']['presentation']['value'] not in {'male_presenting', 'female_presenting', 'unknown'}:
        raise ValueError('Invalid apparent presentation')


def build_profiles(store, document, provider):
    state = document['person_registry_snapshot']
    cfg = store.manifest['config']['v6']
    directory = store.artifact_path('build_identity').parent
    registry = profile_path(store)

    def plan():
        prior = read_profiles(registry)
        graph_path = registry.with_name('relationships-v2.json')
        graph = read_json(graph_path) if graph_path.exists() else {'identities': {}}
        candidates = eligible(state, read_json(store.manifest['bank_path']), cfg['relationship_min_appearances'],
                              graph.get('identities', {}))
        rows = source_rows(document)
        jobs, issues, retained, reused = [], [], [], {}
        for pid, identity in candidates.items():
            history = prior['records'].get(pid, [])
            previous = history[-1] if history else None
            evidence, hashes = profile_evidence(rows, pid, state)
            if previous and not set(hashes)-set(previous.get('evidence_hashes', [])):
                reused[pid] = previous
                continue
            crops, errors = retain_crops(store, 'build_identity', identity['main_magi_id'], cfg['profile_crops'])
            retained.extend(crops)
            issues.extend({'person_id': pid, **e} for e in errors)
            request = {'policy': PROFILE_POLICY, 'identity': identity, 'previous': previous,
                       'evidence_segments': evidence, 'crops': crops}
            jobs.append({'batch_id': 'profile:' + pid, 'task': 'character_profile_v2', 'request': request,
                         'crops': crops, 'evidence_hashes': sorted(set(hashes) | set((previous or {}).get('evidence_hashes', [])))})
        return {'prior': prior, 'eligible': candidates, 'jobs': jobs, 'reused': reused,
                'crop_issues': issues, 'reference_crops': retained}

    frozen = freeze(directory / 'profile-plan.json', [document, cfg], plan)
    from . import structural_validators as structural
    simple = structural.enabled(cfg)
    prompt = ("Create or update this character profile from the supplied current OCR, images and prior profile. "
              "Return person_id and attributes using the requested JSON shape. Infer freely from context; "
              "use unknown when uncertain. Evidence citations are optional; code records supplied sources. "
              "The new complete profile replaces the prior semantic profile. JSON only.") if simple else PROMPT
    results = run_jobs(store, provider, 'build_identity', frozen['jobs'], prompt,
                       (lambda r: structural.schema_without_proofs(schema(r))) if simple else schema,
                       structural.validate_profile if simple else validate,
                       cfg['profile_output_tokens'], cfg['profile_concurrency'],
                       normalize=structural.profile if simple else None)
    profiles = copy.deepcopy(frozen['reused'])
    failures, updates, reviews = [], {}, []
    by_key = {j['batch_id']: j for j in frozen['jobs']}
    for result in results:
        job = by_key[result['batch_id']]
        pid = job['request']['identity']['person_id']
        previous = job['request']['previous']
        if result['status'] != 'completed':
            failures.append(result)
            if previous:
                profiles[pid] = previous
            continue
        accepted, conflicts = ((copy.deepcopy(result['result']), result['result'].get('reviews', []))
                               if simple else reconcile_profile(previous, result['result']))
        reviews.extend(conflicts)
        record = {**accepted, 'source_run': store.directory.name, 'conflicts': conflicts,
                  'chapter': store.manifest['source']['chapter'], 'evidence_hashes': job['evidence_hashes'],
                  'reference_crops': job['crops'], 'previous_version': (previous or {}).get('version')}
        record['version'] = fingerprint(record)
        profiles[pid] = updates[pid] = record
    publish_versions(registry, frozen['prior'], updates, fingerprint([store.directory.name, frozen]), 'records')
    return {'profiles': profiles, 'eligible_identities': frozen['eligible'],
            'profile_snapshot_hash': fingerprint(profiles), 'inference_failures': failures,
            'profile_reviews': reviews,
            'reference_crops': frozen['reference_crops'], 'crop_issues': frozen['crop_issues']}


def reconcile_profile(previous, fresh):
    fresh = copy.deepcopy(fresh)
    conflicts = []
    if previous:
        for field, old in previous['attributes'].items():
            new = fresh['attributes'][field]
            if old['status'] == 'confirmed' and old['value'] != new['value']:
                conflicts.append({'type': 'profile_attribute_conflict', 'person_id': fresh['person_id'],
                                  'field': field, 'previous': old, 'proposed': new})
                fresh['attributes'][field] = copy.deepcopy(old)
    return fresh, conflicts


def publish_versions(path, prior, updates, operation, field):
    with exclusive_lock(path.with_suffix('.lock')):
        current = read_json(path) if path.exists() else copy.deepcopy(prior)
        if operation in current.get('operations', []):
            return
        if fingerprint(current) != fingerprint(prior):
            raise ValueError('Story semantic versions changed concurrently; use a fresh run')
        for key, record in sorted(updates.items()):
            current.setdefault(field, {}).setdefault(key, []).append(record)
        current.setdefault('operations', []).append(operation)
        atomic_json(path.parent / 'history-v2' / (operation + '.json'), {'before': prior, 'updates': updates})
        atomic_json(path, current)
