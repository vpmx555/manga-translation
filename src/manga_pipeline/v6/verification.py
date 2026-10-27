"""Structural verification of frozen chapter graphs, event order and effective analysis."""
from ..storage.io import fingerprint
from .policy import POLICY
from .name_normalization import apply


def verify(artifacts, config, frozen, bank, plan):
    graph, analyzed = artifacts['graph'], artifacts['analyze']
    document = apply(artifacts['normalize'], graph['name_normalization'])
    if graph['graph_source_hash'] != fingerprint(artifacts['normalize']):
        raise ValueError('Graph normalization source changed')
    if graph['person_snapshot_hash'] != fingerprint(frozen):
        raise ValueError('Graph person snapshot changed')
    if (analyzed['policy'] != POLICY or plan['policy'] != POLICY
            or analyzed['analysis_plan_hash'] != fingerprint(plan)
            or plan['graph_hash'] != fingerprint(graph)
            or plan['source_hash'] != fingerprint(document)
            or plan['snapshot_hash'] != fingerprint(frozen)):
        raise ValueError('Frozen event analysis inputs changed')
    expected = [r['id'] for r in document['utterances']]
    if [r['utterance_id'] for r in analyzed['utterances']] != expected:
        raise ValueError('Event semantic coverage/order changed')
    if [u for s in graph['scenes'] for e in s['events'] for u in e['target_ids']] != expected:
        raise ValueError('Graph hierarchy coverage/order changed')
    if len(graph['scenes']) != len(analyzed['scenes']):
        raise ValueError('Analyze changed scene boundaries')
    event_ids = []
    for before, after in zip(graph['scenes'], analyzed['scenes']):
        if before['scene_id'] != after['scene_id'] or before['target_ids'] != after['target_ids']:
            raise ValueError('Analyze changed scene ownership')
        if len(before['events']) != len(after['events']) or len(after['title'].split()) > 4:
            raise ValueError('Analyze changed event count/title contract')
        previous, memory = None, {'description': '', 'facts': [], 'characters': [], 'unresolved': []}
        for old, event in zip(before['events'], after['events']):
            if (old['event_id'] != event['event_id'] or old['target_ids'] != event['target_ids']
                    or not 1 <= len(event['target_ids']) <= 8 or len(event['title'].split()) > 4):
                raise ValueError('Analyze changed frozen event targets')
            if event['previous_event'] != previous or event['memory_input'] != memory:
                raise ValueError('Event context chain changed')
            if event['status'] == 'completed':
                memory = event['memory_output']
                previous = {'event_id': event['event_id'], 'description': event['description'],
                            'source_ids': event['target_ids']}
            event_ids.append(event['event_id'])
    if [b['batch_id'] for b in analyzed['batches']] != event_ids:
        raise ValueError('Analyze batch/event mapping changed')
    if 'reanalyze' in artifacts:
        reviewed = artifacts['reanalyze']
        if reviewed['fallback_to_analyze'] and fingerprint(reviewed['analysis']) != fingerprint(analyzed):
            raise ValueError('Reanalyze fallback changed analyze')
        if [r['utterance_id'] for r in reviewed['analysis']['utterances']] != expected:
            raise ValueError('Reanalyze changed utterance coverage')
    return {'scenes': len(analyzed['scenes']), 'events': len(event_ids),
            'utterances': len(expected), 'unresolved': len(analyzed['unresolved_targets'])}
