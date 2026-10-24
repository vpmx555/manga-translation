"""Conservative semantic identity links; the visual MAGI bank is read-only."""
import copy
from collections import defaultdict
from pathlib import Path

import numpy as np

from ..bank.prototypes import normalized
from ..storage.io import atomic_json, fingerprint, read_json
from . import persons

POLICY = 'identity-relationship-v2'


def confirmed(state, pid):
    person = state['persons'][persons.canonical(state, pid)]
    return bool(person.get('primary_name') and (
        person.get('status') == 'confirmed'
        or any(f.get('status') == 'confirmed' for f in person.get('name_forms', []))))


def membership(state):
    members, observations = defaultdict(set), defaultdict(set)
    for ref, items in state.get('observations', {}).items():
        if ref not in state['cluster_to_person']:
            continue
        pid = persons.canonical(state, state['cluster_to_person'][ref])
        for item in items:
            mid = item.get('source_bank_id')
            if mid is not None:
                members[pid].add(str(mid))
                if ref.startswith(state.get('extraction_scope', '') + ':'):
                    observations[pid].add((item['page_id'], tuple(item['bbox'])))
    return members, observations


def main_member(ids, metadata):
    active = [str(i) for i in ids if not metadata.get('characters', {}).get(str(i), {}).get('disabled')]
    if not active:
        return None
    return min(active, key=lambda i: (-metadata['characters'].get(i, {}).get('appearance_count', 0),
                                      int(i) if i.isdigit() else float('inf'), i))


def vectors(bank_path, metadata):
    root = Path(bank_path).parent
    path = root / 'embeddings/vectors.npz'
    if not path.exists():
        return {}, [{'error': 'Missing retained bank embeddings', 'path': str(path)}]
    result, issues = {}, []
    with np.load(path, allow_pickle=False) as arrays:
        for mid, rec in metadata.get('characters', {}).items():
            if rec.get('disabled'):
                continue
            key = 'character_' + mid
            if key not in arrays:
                issues.append({'magi_id': mid, 'error': 'Missing character embeddings'})
                continue
            a = arrays[key]
            paths = rec.get('prototype_crop_paths', [])
            if len(paths) != len(a):
                issues.append({'magi_id': mid, 'error': 'Crop/vector mapping mismatch'})
                continue
            disabled = set(rec.get('disabled_prototype_indices', []))
            disabled_paths = set(rec.get('disabled_crop_paths', []))
            enabled = []
            for i, p in enumerate(paths):
                if not p or i in disabled or p in disabled_paths:
                    continue
                crop = (root / p).resolve()
                if not crop.is_relative_to(root.resolve()) or not crop.is_file():
                    issues.append({'magi_id': mid, 'error': 'Missing or unsafe paired crop', 'path': p})
                    continue
                enabled.append(i)
            try:
                if enabled:
                    result[mid] = {'values': normalized(a[enabled]), 'indices': enabled, 'paths': paths}
            except ValueError as exc:
                issues.append({'magi_id': mid, 'error': str(exc)})
    return result, issues


def guarded_name_links(state, proposals):
    """Calling evidence cannot move an already named identity to another one."""
    accepted, reviews = [], []
    for proposal in proposals:
        target = persons.canonical(state, proposal['person_id'])
        sources = {persons.canonical(state, state['cluster_to_person'][ref])
                   for ref in proposal.get('cluster_refs', [proposal['cluster_ref']])
                   if ref in state['cluster_to_person']}
        if not confirmed(state, target) or any(p != target and confirmed(state, p) for p in sources):
            reviews.append({**proposal, 'same_person_status': 'needs_review',
                            'reason': 'confirmed_identity_is_immutable'})
        else:
            accepted.append(proposal)
    return persons.apply_links(state, accepted), accepted, reviews


def visual_links(state, metadata, retained, threshold):
    """Freeze main members; minimum crop matching never propagates via new links."""
    members, _ = membership(state)
    anchors = {pid: main_member(ids, metadata) for pid, ids in members.items() if confirmed(state, pid)}
    owners = defaultdict(set)
    for pid, mids in members.items():
        for mid in mids:
            owners[mid].add(pid)
    result = copy.deepcopy(state)
    proposals, reviews = [], []
    for mid in sorted(owners, key=lambda x: (int(x) if x.isdigit() else float('inf'), x)):
        source_people = owners[mid]
        if any(confirmed(state, p) for p in source_people) or mid not in retained:
            continue
        if any(confirmed(result, persons.canonical(result, p)) for p in source_people):
            continue
        refs = sorted(ref for ref, rows in state.get('observations', {}).items()
                      if any(str(o.get('source_bank_id')) == mid for o in rows)
                      and ref in state['cluster_to_person']
                      and persons.canonical(state, state['cluster_to_person'][ref]) in source_people)
        candidates = []
        query = retained[mid]
        for pid, main in anchors.items():
            if main is None or main == mid or main not in retained:
                continue
            bank = retained[main]
            distances = np.linalg.norm(query['values'][:, None, :] - bank['values'][None, :, :], axis=2)
            x, y = np.unravel_index(np.argmin(distances), distances.shape)
            candidates.append({'person_id': pid, 'main_magi_id': main,
                'min_crop': float(distances[x, y]),
                'query_crop': query['paths'][query['indices'][x]],
                'anchor_crop': bank['paths'][bank['indices'][y]]})
        candidates.sort(key=lambda c: (c['min_crop'], c['person_id']))
        qualifying = [c for c in candidates if c['min_crop'] < threshold]
        if not qualifying or not refs:
            continue
        best = qualifying[0]
        proposal = {'cluster_ref': refs[0], 'cluster_refs': refs, 'person_id': best['person_id'],
            'magi_id': mid, 'main_magi_id': best['main_magi_id'], 'min_crop': best['min_crop'],
            'threshold': threshold, 'query_crop': best['query_crop'], 'anchor_crop': best['anchor_crop'],
            'candidates': candidates, 'same_person_status': 'supported',
            'decision_basis': 'frozen_main_magi_min_crop', 'confirmed_event_count': 0,
            'event_refs': [], 'contradictions': [], 'source_cluster_bank_mutated': False}
        # apply_links also enforces semantic cannot-links already in the registry.
        before_reviews = len(result.get('link_reviews', []))
        result = persons.apply_links(result, [proposal])
        if len(result.get('link_reviews', [])) > before_reviews:
            reviews.extend(result['link_reviews'][before_reviews:])
        else:
            proposals.append(proposal)
    return result, proposals, reviews, anchors


def build(store, provider):
    from . import semantic
    from .identity_profiles import build_profiles
    analyzed = store.read('analyze')
    directory = store.artifact_path('build_identity').parent
    frozen_path = directory / 'identity-plan.json'
    source_hash = fingerprint([analyzed, store.manifest['config']['v6']])
    from . import identity_binding
    use_binding = identity_binding.enabled(store.manifest['config'])
    if use_binding:
        source_hash = fingerprint([source_hash, store.read('scan'), store.read('extract'), store.read('normalize')])
    if frozen_path.exists():
        frozen = read_json(frozen_path)
        if frozen['source_hash'] != source_hash:
            raise ValueError('Frozen identity linking inputs changed')
    else:
        binding = None
        if use_binding:
            state, binding = identity_binding.build(store, provider, analyzed)
        else:
            state = copy.deepcopy(analyzed['person_state'])
        for event in analyzed['calling_events']:
            state.setdefault('calling_events', {})[event['event_id']] = event
        proposed = persons.propose(state, [], minimum=store.manifest['config']['v6']['min_confirmed_call_events'])
        state, names, reviews = guarded_name_links(state, proposed)
        metadata = read_json(store.manifest['bank_path'])
        retained, issues = vectors(store.manifest['bank_path'], metadata)
        state, visual, visual_reviews, anchors = visual_links(state, metadata, retained,
            store.manifest['config']['v6']['identity_visual_min_crop'])
        frozen = {'source_hash': source_hash, 'person_state': state,
            'proposals': [*((binding or {}).get('proposals', [])), *names, *visual],
            'needs_review': [*reviews, *visual_reviews, *issues], 'frozen_main_members': anchors,
            'operation_id': fingerprint([POLICY, store.directory.name, state])}
        if binding is not None:
            frozen['name_binding'] = binding
        atomic_json(frozen_path, frozen)
    committed = persons.commit(persons.registry_path(store), frozen['person_state'],
        operation_id=frozen['operation_id'], source_bank=store.manifest['bank_path'],
        expected_source_snapshot=analyzed['source_bank_snapshot'])
    result = {'policy': POLICY, 'person_state': committed, 'proposals': frozen['proposals'],
        'operation_id': frozen['operation_id'], 'person_registry_path': str(persons.registry_path(store)),
        'source_cluster_bank_mutated': False, 'source_bank_snapshot': analyzed['source_bank_snapshot'],
        'frozen_main_members': frozen['frozen_main_members'], 'needs_review': frozen['needs_review'], 'failures': []}
    if use_binding:
        binding = frozen['name_binding']
        result.update(name_binding=binding, binding_snapshot_hash=binding['snapshot_hash'])
        result['needs_review'].extend(binding['needs_review'])
    document = semantic.export(store.read('normalize'), analyzed, result)
    profiles = build_profiles(store, document, provider)
    result.update(profiles)
    if use_binding:
        result['reference_crops'] = [*binding['reference_crops'], *result.get('reference_crops', [])]
        result['crop_issues'] = [*binding['crop_issues'], *result.get('crop_issues', [])]
    result['needs_review'].extend(profiles.get('inference_failures', []))
    result['needs_review'].extend(profiles.get('profile_reviews', []))
    result['review_status'] = 'completed_with_review' if result['needs_review'] else 'completed'
    return result
