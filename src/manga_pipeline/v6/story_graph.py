"""Chapter graph structure and deterministic links; no identity bank mutation."""
import copy

from ..storage.io import fingerprint
from .story_contract import LINKS


def reference_people(state, groups):
    result = copy.deepcopy(state)
    for group in groups:
        pid = 'reference:' + group['group_id']
        result['persons'][pid] = {'person_id': pid, 'primary_name': group['canonical_name'],
            'name_forms': [], 'components': [], 'appearance_refs': [], 'status': 'provisional',
            'story_reference_only': True, 'group_id': group['group_id']}
    return result


def connections(scenes, proposed=()):
    nodes = {s['scene_id'] for s in scenes} | {e['event_id'] for s in scenes for e in s['events']}
    output = []
    for scene in scenes:
        output.extend({'type': 'contains', 'from': scene['scene_id'], 'to': e['event_id']} for e in scene['events'])
        output.extend({'type': 'next', 'from': a['event_id'], 'to': b['event_id']}
                      for a, b in zip(scene['events'], scene['events'][1:]))
    output.extend({'type': 'next', 'from': a['scene_id'], 'to': b['scene_id']}
                  for a, b in zip(scenes, scenes[1:]))
    for edge in proposed:
        if (isinstance(edge, dict) and edge.get('type') in LINKS and edge.get('from') in nodes
                and edge.get('to') in nodes and edge['from'] != edge['to']):
            output.append(copy.deepcopy(edge))
    return list({fingerprint(e): e for e in output}.values())


def assemble(document, scenes, state, records=(), proposed=()):
    catalog = {}
    for pid, person in state['persons'].items():
        magi = {str(o['source_bank_id']) for ref, observations in state.get('observations', {}).items()
                if state['cluster_to_person'].get(ref) == pid
                for o in observations if o.get('source_bank_id') is not None}
        catalog[pid] = {'person_id': pid, 'name': person.get('primary_name'), 'magi_ids': sorted(magi),
                        'reference_only': bool(person.get('story_reference_only'))}
    links = connections(scenes, proposed)
    for row in records:
        uid = row['utterance_id']
        event = row.get('event_id')
        if event:
            links.append({'type': 'contains', 'from': event, 'to': uid})
        for role, ids in [('speaker', [row.get('speaker_person_id')]),
                          ('addressee', row.get('addressee_person_ids', []))]:
            for pid in ids:
                if pid in catalog:
                    links.append({'type': role, 'from': uid, 'to': pid,
                                  'confidence': row.get('confidence', {}).get(role, 0)})
        for m in row.get('mentions', []):
            if m.get('target_person_id') in catalog:
                links.append({'type': 'mentions', 'from': uid, 'to': m['target_person_id'],
                    'candidate_id': m['candidate_id'], 'mention_type': m['mention_type'],
                    'confidence': m.get('confidence', {}).get('target', 0)})
    chapter = {'chapter_id': document.get('document_id', fingerprint(document)[:16]),
               'story_id': document.get('story_id'), 'chapter_label': document.get('chapter_id'),
               'scene_ids': [s['scene_id'] for s in scenes]}
    links.extend({'type': 'contains', 'from': chapter['chapter_id'], 'to': s['scene_id']} for s in scenes)
    return {'chapter': chapter, 'scenes': copy.deepcopy(scenes), 'characters': catalog, 'links': links}
