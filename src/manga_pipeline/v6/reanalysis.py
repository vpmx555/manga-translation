"""Two independent text-only review halves merged against one frozen snapshot."""
import copy
from concurrent.futures import ThreadPoolExecutor

from ..storage.io import atomic_json, fingerprint, read_json
from . import merge
from .anchors import route
from .name_normalization import apply
from .policy import EVIDENCE_CONFIDENCE_PROMPT
from .requests import Calls
from .reanalysis_input import packet, partition
from .semantic import snapshot
from .story_contract import SCORE, TEXT, object_schema, row_schema, score, short_title, validate_row
from .story_graph import assemble, reference_people

PROMPT = '''Review ONLY the half owned by scope.target_ids and scope.event_ids.
Two batches independently read the SAME frozen analyze snapshot; you do not
receive the other batch's corrections. Do not edit context_scenes, boundary_context
or any ID outside your scope. Owned OCR/decisions/evidence are complete. The
other half is provided as scene summaries and adjacent event text, read-only.
No images are supplied. Evaluate semantic consistency from the evidence; no
special anchor weight or anchor-priority rule applies to this review stage.
Person display names are provisional analyze output, not proof of identity.
Speaker identity does not prove name ownership. Repeated summaries do not create
independent evidence. Thought may
have caption/narration presentation. Speakers/listeners use visual person IDs;
only reference mentions may name a reference-only person without MAGI/crops.
Bubble-tail metadata is immutable MAGIv2 detection output supplied to analyze,
not an observation made by an LLM. If bubble_tail.has_tail=false, this target
has NO tail: never invent one, borrow another target's tail or claim that you
observed one. Do not change tail links/bboxes; no images are supplied here.
Return ONLY corrections by utterance/event ID, not a rewritten graph or OCR.
Each correction supplies the complete replacement semantic row and a reason.
Retain all mention IDs owned by that utterance. Confidence may be reassessed.
speaker_cluster_ref must map to speaker_person_id in clusters; otherwise use null.
Only visual people may be speakers/listeners. Reference-only named people may
be targets of reference mentions only. Do not invent IDs or drop owned mentions.
Review competing evidence, not confidence ranks. You MAY correct a decision
even when your NEW confidence is LOWER than the OLD confidence; an old 10/10
does not lock a mistaken decision. Score honestly without inflating confidence
to obtain acceptance. Event prose/title updates need their own confidence and requires
lists every corrected utterance on which they depend. No boundary changes.
Only edit owned events. Do NOT propagate/replay later events that consumed old
memory. No new causal/motive edges. All generated titles, descriptions, evidence,
explanations and reasons MUST be in English; preserve quoted OCR/names. No thinking.
JSON only.'''

PROMPT += '\n' + EVIDENCE_CONFIDENCE_PROMPT


def schema(state, candidates, ids, events):
    replacement = row_schema(None, None, list(candidates), ids)
    # Membership is checked against the saved chapter after decoding. Repeating
    # every ID in JSON-schema enums consumes context without adding information.
    replacement['properties']['utterance_id'] = TEXT
    replacement['properties']['mentions']['items']['properties']['candidate_id'] = TEXT
    return object_schema({'corrections': {'type': 'array', 'items': object_schema({
        'replacement': replacement,
        'reason': TEXT})}, 'event_updates': {'type': 'array', 'items': object_schema({
            'event_id': TEXT, 'title': TEXT, 'description': TEXT, 'confidence': SCORE,
            'requires': {'type': 'array', 'items': TEXT}, 'reason': TEXT})}})


def apply_corrections(analyzed, response, sources, state, candidates, scope=None):
    effective = copy.deepcopy(analyzed)
    by_id = {r['utterance_id']: r for r in effective['utterances']}
    accepted, rejected, seen = [], [], set()
    allowed_targets = set(scope['target_ids']) if scope is not None else set(by_id)
    allowed_events = set(scope['event_ids']) if scope is not None else None
    updates = response.get('corrections')
    if not isinstance(updates, list) or not isinstance(response.get('event_updates'), list):
        raise ValueError('Reanalyze must return correction and event-update arrays')
    for proposal in updates:
        try:
            replacement = proposal['replacement']
            uid = replacement.get('utterance_id')
            if uid not in by_id or uid in seen:
                raise ValueError('Unknown or duplicate correction target')
            if uid not in allowed_targets:
                raise ValueError('Correction target is outside this review half')
            seen.add(uid)
            before = copy.deepcopy(by_id[uid])
            after = validate_row(replacement, sources[uid], state, candidates)
            # Merge must resolve the corrected speaker, not a copied old projection.
            after.pop('inferred_speaker', None)
            after.pop('speaker_resolution', None)
            # Text-only review cannot replace a retained visual observation.
            after.pop('bubble_tail', None)
            if 'bubble_tail' in before:
                after['bubble_tail'] = copy.deepcopy(before['bubble_tail'])
            for key in ('scene_id', 'event_id', 'batch_id'):
                if key in before:
                    after[key] = before[key]
            by_id[uid] = after
            accepted.append({'utterance_id': uid, 'before': before, 'after': copy.deepcopy(after),
                             'reason': proposal.get('reason', '')})
        except (ValueError, KeyError, TypeError) as exc:
            rejected.append({'proposal': copy.deepcopy(proposal), 'reason': str(exc)})
    events = {e['event_id']: e for s in effective['scenes'] for e in s['events']}
    accepted_ids = {c['utterance_id'] for c in accepted}
    seen_events = set()
    for proposal in response['event_updates']:
        try:
            eid = proposal['event_id']
            if eid not in events or eid in seen_events:
                raise ValueError('Unknown or duplicate event update')
            if allowed_events is not None and eid not in allowed_events:
                raise ValueError('Event update is outside this review half')
            seen_events.add(eid)
            event = events[eid]
            if (not isinstance(proposal.get('requires'), list) or
                    any(uid not in event['target_ids'] or uid not in accepted_ids for uid in proposal['requires'])):
                raise ValueError('Event prose depends on a correction not applied to this event')
            score(proposal['confidence'])
            if not isinstance(proposal.get('description'), str):
                raise ValueError('Event description must be text')
            before = {k: copy.deepcopy(event.get(k)) for k in ('title', 'description', 'description_confidence')}
            event.update(title=short_title(proposal.get('title'), event['title']),
                         description=proposal['description'], description_confidence=proposal['confidence'])
            accepted.append({'event_id': eid, 'before': before, 'after': {k: event[k] for k in before},
                             'reason': proposal.get('reason', '')})
        except (ValueError, KeyError, TypeError) as exc:
            rejected.append({'proposal': copy.deepcopy(proposal), 'reason': str(exc)})
    effective['utterances'] = [by_id[r['utterance_id']] for r in analyzed['utterances']]
    return effective, accepted, rejected


def build(store, provider):
    analyzed, scanned = store.read('analyze'), store.read('graph')
    directory = store.artifact_path('reanalyze').parent
    result = {'policy': 'chapter-reanalyze-halves-v7', 'status': 'failed', 'analysis': copy.deepcopy(analyzed),
              'accepted': [], 'rejected': [], 'failures': [], 'fallback_to_analyze': True,
              'batch_reviews': [], 'batch_failures': [], 'output_language': 'en',
              'automatic_retries': 0, 'analyze_artifact_hash': store.manifest['steps']['analyze']['artifact_hash']}
    atomic_json(directory / 'before.json', analyzed['story_graph'])
    try:
        document = apply(store.read('normalize'), scanned['name_normalization'])
        frozen = snapshot(store, store.read('extract'))
        state = reference_people(frozen['state'], scanned['name_normalization']['groups'])
        source_rows = route(document['utterances'], store.read('extract'), state, read_json(store.manifest['bank_path']))
        sources = {r['id']: r for r in source_rows}
        for row in analyzed['utterances']:
            for key in ('scene_id', 'event_id'):
                if key in row:
                    sources[row['utterance_id']][key] = row[key]
        candidates = {c['id']: c for c in scanned['candidates']}
        scopes = partition(analyzed)
        plan = {'policy': result['policy'], 'analyze_artifact_hash': result['analyze_artifact_hash'],
                'batches': scopes, 'independent': True, 'output_language': 'en'}
        atomic_json(directory / 'plan.json', plan)
        atomic_json(directory / 'packet.json', {'policy': result['policy'], 'batches': [
            {**s, 'directory': 'batches/'+s['batch_id'].replace(':', '-')} for s in scopes]})
        from . import reanalyze_wire
        calls = Calls(store, provider, 'reanalyze', allow_uncertain=True)

        def review_half(scope):
            batch_dir = directory / 'batches' / scope['batch_id'].replace(':', '-')
            review = {'scope': copy.deepcopy(scope), 'status': 'failed', 'accepted': [], 'rejected': []}
            try:
                request = packet(analyzed, document, candidates, state, scope)
                atomic_json(batch_dir / 'packet.json', request)
                wire, ledger, forward = reanalyze_wire.encode(request)
                atomic_json(batch_dir / 'wire-packet.json', wire)
                atomic_json(batch_dir / 'reference-ledger.json', ledger)
                owned = {cid: c for cid, c in candidates.items() if c['utterance_id'] in scope['target_ids']}
                response_schema = reanalyze_wire.replace_ids(
                    schema(state, owned, scope['target_ids'], scope['event_ids']), forward)
                review['request_hash'] = fingerprint([PROMPT, wire, response_schema])
                value = calls.call(scope['batch_id'], 'reanalyze_chapter_v7',
                    PROMPT+'\n'+reanalyze_wire.PROMPT, wire, response_schema,
                    reserve=store.manifest['config']['v6']['reanalyze_output_tokens'], oom_retry=False)
                value = reanalyze_wire.decode(value, ledger)
                if not isinstance(value, dict):
                    raise ValueError('Reanalyze returned no usable object')
                _, accepted, rejected = apply_corrections(analyzed, value, sources, state, owned, scope)
                review.update(status='completed_with_review' if rejected else 'completed',
                              accepted=accepted, rejected=rejected)
            except Exception as exc:
                review.update(error=str(exc), error_type=type(exc).__name__)
            atomic_json(batch_dir / 'result.json', review)
            return review

        slots = store.manifest['config'].get('runtime', {}).get('llm_slots', 2) or 2
        with ThreadPoolExecutor(max_workers=max(1, min(2, slots)), thread_name_prefix='review-half') as executor:
            reviews = list(executor.map(review_half, scopes))
        result['batch_reviews'] = reviews
        result['batch_failures'] = [{'batch_id': r['scope']['batch_id'], 'error': r['error'],
                               'error_type': r['error_type']} for r in reviews if r['status'] == 'failed']
        if reviews and all(r['status'] == 'failed' for r in reviews):
            raise ValueError('All review halves failed; retaining analyze unchanged')
        accepted = [p for r in reviews for p in r['accepted']]
        rejected = [p for r in reviews for p in r['rejected']]
        effective = copy.deepcopy(analyzed)
        by_id = {r['utterance_id']: r for r in effective['utterances']}
        events = {e['event_id']: e for s in effective['scenes'] for e in s['events']}
        # Half ownership is disjoint. Merge only accepted patches, never worker snapshots.
        for change in accepted:
            if 'utterance_id' in change:
                by_id[change['utterance_id']] = copy.deepcopy(change['after'])
            else:
                events[change['event_id']].update(copy.deepcopy(change['after']))
        effective['utterances'] = [by_id[r['utterance_id']] for r in analyzed['utterances']]
        # Rebuild projections from the frozen source, never retain names added by rejected old rows.
        rows, new_state, calling = merge.merge(list(sources.values()), [effective['utterances']], state,
                                               candidates, store.read('extract'))
        effective.update(utterances=rows, person_state=new_state, calling_events=calling,
                         unresolved_targets=[r['utterance_id'] for r in rows if r['semantic_resolution'] != 'resolved'])
        effective['semantic_coverage_complete'] = not effective['unresolved_targets']
        effective['story_graph'] = assemble(document, effective['scenes'], new_state, rows, scanned['story_graph']['links'])
        result.update(status='completed_with_review' if rejected or result['batch_failures'] or effective['unresolved_targets'] else 'completed',
                      analysis=effective, accepted=accepted, rejected=rejected, fallback_to_analyze=False)
    except Exception as exc:
        result.update(error=str(exc), error_type=type(exc).__name__)
    result['review_status'] = 'completed' if result['status'] == 'completed' else 'completed_with_review'
    atomic_json(directory / 'after.json', result['analysis']['story_graph'])
    atomic_json(directory / 'changes.json', {'accepted': result['accepted'], 'rejected': result['rejected']})
    return result
