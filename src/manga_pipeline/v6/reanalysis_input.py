"""Two disjoint review halves with full owned decisions and compact context."""
import copy


def partition(analyzed):
    events = [e for s in analyzed['scenes'] for e in s['events']]
    expected = [r['utterance_id'] for r in analyzed['utterances']]
    covered = [uid for e in events for uid in e['target_ids']]
    if covered != expected or len(set(covered)) != len(covered):
        raise ValueError('Review ownership must cover utterances once in source order')
    if not events:
        return []
    groups = [events]
    if len(events) > 1:
        cumulative = 0
        distances = []
        for index, event in enumerate(events[:-1], 1):
            cumulative += len(event['target_ids'])
            distances.append((abs(len(covered)-2*cumulative), index))
        cut = min(distances)[1]
        groups = [events[:cut], events[cut:]]
    return [{'batch_id': 'half:'+str(i+1), 'event_ids': [e['event_id'] for e in group],
             'target_ids': [uid for e in group for uid in e['target_ids']]}
            for i, group in enumerate(groups)]


def packet(analyzed, document, candidates, state, scope):
    """Keep semantic evidence for the owned half; no images or duplicate histories."""
    target_ids, event_ids = set(scope['target_ids']), set(scope['event_ids'])
    texts = {r['id']: r['text'] for r in document['utterances']}
    decisions = []
    for row in analyzed['utterances']:
        if row['utterance_id'] not in target_ids:
            continue
        item = {k: copy.deepcopy(row.get(k)) for k in (
            'utterance_id', 'content_type', 'speech_style', 'speaker_kind', 'speaker_person_id',
            'speaker_cluster_ref', 'addressee_type', 'addressee_person_ids', 'confidence',
            'evidence', 'evidence_refs', 'semantic_resolution')}
        anchor = row.get('speaker_anchor')
        if anchor:
            item['speaker_anchor'] = {k: copy.deepcopy(anchor[k]) for k in
                ('person_id', 'conflicting_person_ids') if k in anchor}
        tail = row.get('bubble_tail')
        if tail is not None:
            # The text-only reviewer needs presence/links, not pixel coordinates.
            item['bubble_tail'] = {'has_tail': tail['has_tail'],
                                  'tail_indices': [t['tail_index'] for t in tail['tails']]}
        item['mentions'] = [{k: copy.deepcopy(m.get(k)) for k in (
            'candidate_id', 'confirmed_person_name', 'mention_type', 'target_person_id',
            'recipient_cluster_ref', 'visual_support', 'components', 'confidence', 'evidence')}
            for m in row.get('mentions', [])]
        decisions.append(item)
    scenes, context_scenes = [], []
    all_events = [e for s in analyzed['scenes'] for e in s['events']]
    for scene in analyzed['scenes']:
        selected = [e for e in scene['events'] if e['event_id'] in event_ids]
        summary = {k: copy.deepcopy(scene[k]) for k in ('scene_id', 'title', 'description')}
        if not selected:
            context_scenes.append(summary)
            continue
        summary['events'] = [{k: copy.deepcopy(e.get(k)) for k in (
            'event_id', 'title', 'description', 'description_confidence', 'target_ids', 'status', 'error')}
            for e in selected]
        scenes.append(summary)
    first = next(i for i, e in enumerate(all_events) if e['event_id'] == scope['event_ids'][0])
    last = next(i for i, e in enumerate(all_events) if e['event_id'] == scope['event_ids'][-1])
    neighbors = [all_events[i] for i in (first-1, last+1) if 0 <= i < len(all_events)]
    boundary_context = [{'event_id': e['event_id'], 'description': e['description'],
        'texts': [{'id': uid, 'text': texts[uid]} for uid in e['target_ids']]} for e in neighbors]
    people = {pid: {k: copy.deepcopy(p.get(k)) for k in ('name', 'magi_ids', 'reference_only')}
              for pid, p in analyzed['story_graph']['characters'].items()}
    clusters = [{'cluster_ref': ref, 'person_id': pid} for ref, pid in state['cluster_to_person'].items()
                if ref.startswith(state['extraction_scope']+':')]
    mentions = [{k: copy.deepcopy(c[k]) for k in ('id', 'utterance_id', 'name', 'group_id') if k in c}
                for c in candidates.values() if c['utterance_id'] in target_ids]
    groups = [{k: copy.deepcopy(g[k]) for k in ('group_id', 'canonical_name', 'aliases') if k in g}
              for g in analyzed.get('name_normalization', {}).get('groups', [])]
    return {'policy': 'chapter-reanalyze-halves-v7', 'scope': copy.deepcopy(scope), 'output_language': 'en',
        'scenes': scenes, 'context_scenes': context_scenes, 'boundary_context': boundary_context,
        'texts': [{'id': uid, 'text': texts[uid]} for uid in scope['target_ids']],
        'decisions': decisions, 'people': people, 'clusters': clusters, 'name_groups': groups,
        'mentions': mentions, 'event_failures': [copy.deepcopy(f) for f in analyzed.get('event_failures', [])
                                               if f['event_id'] in event_ids]}
