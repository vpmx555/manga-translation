"""One text-only chapter call builds the frozen scene/event graph and name ledger."""
from ..storage.io import atomic_json, fingerprint
from . import graph_names
from .name_normalization import registry_context
from .policy import EVIDENCE_CONFIDENCE_PROMPT
from .requests import Calls
from .semantic import snapshot
from .graph_wire import mapping
from .story_contract import SCORE, TEXT, enum, object_schema, score, short_title
from .story_graph import assemble, reference_people

POLICY = 'chapter-story-graph-v7'
PROMPT = graph_names.PROMPT + '''
Build a TWO-LEVEL narrative hierarchy: scenes contain ordered events. A scene is
a broad continuous situation, not every panel or camera cut. Each event is a
coherent consecutive exchange/explanation/action and contains at most EIGHT
utterance IDs, including narration/thought/caption. Events cannot cross scenes.
Return scene and event start_id markers in source order, covering ALL targets.
The FIRST scene MUST start at the first target ID (normally u0); its FIRST
event MUST use that same ID. Each later scene's first event MUST start at that
scene's start_id. Include cover/back-cover text, ISBN/credits, cast descriptions,
author notes and other non-narrative text as separate opening/closing scenes
and events where appropriate. NEVER skip them or start at the first story page.
Every scene/event has title UNDER FIVE WORDS and a concise prose summary.
All generated titles, summaries, evidence and reasons MUST be in English; preserve quoted
OCR/names. No images are supplied. Do NOT propose character identities, speaker
assignments, MAGI-to-name mappings or somebody hypotheses, even with high
confidence. Summaries describe the exchange/topic without assigning utterances
or mentioned names to visual people. Name groups organize textual name forms
only; they do not identify speakers. Speaker identification belongs to the later
image-grounded analyze stage. Link events/scenes only with
next/continues/related/references, not
causal/motive edges. Graph IDs are assigned by code; proposed links use supplied
utterance start markers. Do not output utterance semantic analysis yet.'''

PROMPT += '\n' + EVIDENCE_CONFIDENCE_PROMPT


def schema():
    result = graph_names.schema()
    event = object_schema({'start_id': TEXT, 'title': TEXT, 'summary': TEXT, 'confidence': SCORE})
    result['properties']['scenes']['items'] = object_schema({'start_id': TEXT, 'title': TEXT,
        'summary': TEXT, 'confidence': SCORE, 'events': {'type': 'array', 'items': event}})
    result['properties']['links'] = {'type': 'array', 'items': object_schema({
        'from_start_id': TEXT, 'to_start_id': TEXT, 'type': enum(['next', 'continues', 'related', 'references']),
        'confidence': SCORE, 'reason': TEXT})}
    result['required'] = list(result['properties'])
    return result


def hierarchy(value, rows, ledger, maximum=8):
    ids = [r['id'] for r in rows]
    positions = {uid: i for i, uid in enumerate(ids)}
    def position(marker):
        uid = ledger['rows'].get(marker, marker)
        if uid not in positions:
            raise ValueError('Unknown graph start marker: ' + str(marker))
        return positions[uid]
    scenes, starts_to_nodes = [], {}
    markers = value.get('scenes', [])
    starts = [position(s['start_id']) for s in markers]
    if ids and (not starts or starts[0] != 0 or starts != sorted(set(starts))):
        raise ValueError('Scenes must cover source order, beginning with first utterance')
    for si, (entry, start) in enumerate(zip(markers, starts)):
        end = starts[si+1] if si+1 < len(starts) else len(ids)
        sid = 'scene:' + str(si+1)
        events = entry.get('events', [])
        event_starts = [position(e['start_id']) for e in events]
        if (not event_starts or event_starts[0] != start or event_starts != sorted(set(event_starts))
                or any(not start <= x < end for x in event_starts)):
            raise ValueError('Events must cover their scene in source order')
        scene = {'scene_id': sid, 'title': short_title(entry.get('title'), 'Scene '+str(si+1)),
            'summary': entry['summary'], 'initial_description': entry['summary'],
            'description': entry['summary'], 'description_confidence': score(entry['confidence']),
            'target_ids': ids[start:end], 'events': [], 'hypotheses': [], 'resolution': 'model'}
        for ei, (item, event_start) in enumerate(zip(events, event_starts)):
            event_end = event_starts[ei+1] if ei+1 < len(events) else end
            previous = None
            for part, offset in enumerate(range(event_start, event_end, maximum), 1):
                eid = sid + ':event:' + str(len(scene['events'])+1)
                title = short_title(item.get('title'), 'Event')
                if event_end-event_start > maximum:
                    title = ' '.join(title.split()[:3]) + ' ' + str(part)
                event = {'event_id': eid, 'scene_id': sid, 'title': title,
                    'initial_description': item['summary'], 'description': item['summary'],
                    'description_confidence': score(item['confidence']),
                    'target_ids': ids[offset:min(offset+maximum, event_end)], 'status': 'pending'}
                if previous:
                    event['continuation_of'] = previous
                previous = eid
                scene['events'].append(event)
                starts_to_nodes.setdefault(ledger['rows'].get(item['start_id'], item['start_id']), eid)
        scenes.append(scene)
    links = []
    for edge in value.get('links', []):
        a = starts_to_nodes.get(ledger['rows'].get(edge['from_start_id'], edge['from_start_id']))
        b = starts_to_nodes.get(ledger['rows'].get(edge['to_start_id'], edge['to_start_id']))
        if a and b and a != b:
            links.append({'from': a, 'to': b, 'type': edge['type'],
                          'confidence': score(edge['confidence']), 'reason': edge['reason']})
    return scenes, links


def build(store, provider):
    document, raw = store.read('normalize'), store.read('extract')
    frozen = snapshot(store, raw)
    rows = document['utterances']
    registry = registry_context(frozen['state'])
    # No vectors, crops or raw bank histories enter the chapter request.
    state = frozen['state']
    page_numbers = {p['id']: i+1 for i, p in enumerate(raw['pages'])}
    people = {pid: {'name': p.get('primary_name'), 'observations': [
        {'page': page_numbers[o['page_id']], 'bbox': [round(x) for x in o['bbox']],
         'magi_id': o.get('source_bank_id')}
        for ref, observations in state['observations'].items() if state['cluster_to_person'].get(ref) == pid
        for o in observations if ref.startswith(state['extraction_scope']+':') and o['page_id'] in page_numbers]}
        for pid, p in state['persons'].items()}
    people = {pid: p for pid, p in people.items() if p['observations'] or p['name']}
    ledger = mapping(rows, [{'person_id': pid} for pid in people])
    forward = {uid: compact for compact, uid in ledger['rows'].items()}
    person_wire = {pid: compact for compact, pid in ledger['persons'].items()}
    request = {'policy': POLICY,
        'coverage_contract': {'first_target_id': forward[rows[0]['id']] if rows else None,
            'last_target_id': forward[rows[-1]['id']] if rows else None,
            'target_count': len(rows), 'include_non_narrative_targets': True,
            'description_language': 'en'},
        'target_columns': ['id', 'text', 'page', 'primary_panel', 'panels', 'bbox'],
        'targets': [[forward[r['id']], r['text'], page_numbers[r['page_id']], r.get('panel_index'),
                     r.get('panel_indices', []), [round(x) for x in r['bbox']]] for r in rows],
        'name_registry': [{**p, 'person_id': person_wire[p['person_id']]} for p in registry if p['person_id'] in person_wire],
        'person_columns': ['name', 'observations'], 'observation_columns': ['page', 'magi_id', 'bbox'],
        'people': {person_wire[pid]: [p['name'], [[o['page'], o['magi_id'], o['bbox']] for o in p['observations']]]
                   for pid, p in people.items()},
        'panels': [{'page': i+1, 'panels': [[round(x) for x in (b['bbox'] if isinstance(b, dict) else b)]
                                         for b in p['panels']]} for i, p in enumerate(raw['pages'])]}
    directory = store.artifact_path('graph').parent
    atomic_json(directory / 'packet.json', request)
    atomic_json(directory / 'reference-ledger.json', ledger)
    prompt = PROMPT + ('\nWire tables: targets is an ARRAY OF ROW ARRAYS following target_columns; '
        'EVERY target row is a scene target. people values follow person_columns, and their observations '
        'follow observation_columns. No text or observation has been omitted by this table encoding. '
        'Use column id (uN) for start_id/text_id/source_ids. People metadata is not a speaker assignment.')
    value = (Calls(store, provider, 'graph').call('chapter', 'graph_chapter_v7', prompt,
             request, schema(), reserve=store.manifest['config']['v6']['graph_output_tokens'], oom_retry=False)
             if rows else {'scenes': [], 'groups': [], 'names': [], 'links': []})
    names = graph_names.parse(value, document, rows, ledger, frozen['state'])
    scenes, links = hierarchy(value, rows, ledger, store.manifest['config']['v6']['analyze_batch_size'])
    named = reference_people(frozen['state'], names['name_normalization']['groups'])
    result = {**names, 'policy': POLICY, 'output_language': 'en', 'scenes': scenes,
        'story_graph': assemble(document, scenes, named, proposed=links),
        'person_snapshot_hash': fingerprint(frozen), 'graph_source_hash': fingerprint(document)}
    atomic_json(directory / 'ocr-current.json', names['name_normalization']['canonical_rows'])
    atomic_json(directory / 'replacement-history.json', names['name_normalization']['patches'])
    atomic_json(directory / 'reference-ledger.json', ledger)
    return result
