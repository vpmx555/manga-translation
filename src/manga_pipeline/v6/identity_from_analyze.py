"""Deterministic name registration from immutable analyze decisions; no POSTs."""
import copy
from collections import Counter, defaultdict

from ..storage.io import atomic_json, fingerprint, read_json
from . import persons
from .identity_registration import chapter_counts, replace_names
from .identity_v2 import confirmed, main_member

POLICY = 'analyze-authoritative-v3'


def enabled(config):
    return config.get('v6', {}).get('identity_name_binding_policy') == POLICY


def rank(record):
    types = Counter(o['mention_type'] for o in record['occurrences'])
    return (-len(record['occurrences']), -types['introduction'], -types['direct_address'],
            -types['reference'], min(o['order'] for o in record['occurrences']), record['group_id'])


def choose(records):
    ordered = sorted(records, key=rank)
    return ordered[0], ordered


def register(analyzed, scanned, raw, metadata, prior, minimum, *, minimum_events=3):
    """Pure operation. Registry links never modify the analyze/source snapshot."""
    state = copy.deepcopy(analyzed['person_state'])
    prior_named = {persons.canonical(prior, p) for p in prior.get('persons', {}) if confirmed(prior, p)}
    # Analyze name forms are observations, not a pre-existing identity consensus.
    state['name_index'] = {}
    for pid, person in state['persons'].items():
        baseline = prior.get('persons', {}).get(pid, {}) if pid in prior_named else {}
        person.update(primary_name=baseline.get('primary_name'), name_forms=copy.deepcopy(baseline.get('name_forms', [])),
                      components=copy.deepcopy(baseline.get('components', [])))
        for form in person['name_forms']:
            for key in {form['surface'], form['base_name']}:
                state['name_index'].setdefault(key, []).append(pid)
    people, by_person = {}, defaultdict(set)
    for ref, observations in state.get('observations', {}).items():
        if ref not in state['cluster_to_person']:
            continue
        pid = persons.canonical(state, state['cluster_to_person'][ref])
        for observation in observations:
            mid = observation.get('source_bank_id')
            if mid is not None and str(mid) in metadata['characters']:
                people[str(mid)] = pid
                by_person[pid].add(str(mid))
    groups = {g['group_id']: copy.deepcopy(g) for g in scanned.get('name_normalization', {}).get('groups', [])}
    candidates = {c['id']: c for c in scanned.get('candidates', [])}
    votes, reviews, seen = defaultdict(dict), [], set()
    for number, row in enumerate(analyzed['utterances']):
        for mention in row.get('mentions', []):
            candidate = candidates.get(mention.get('candidate_id'), {})
            gid = candidate.get('group_id')
            if gid is None:
                aliases = candidate.get('alias_groups') or mention.get('alias_groups') or []
                gid = aliases[0].get('group_id') if len(aliases) == 1 else None
            owner = mention.get('target_person_id')
            if not mention.get('confirmed_person_name') or owner not in state['persons'] or gid not in groups:
                reviews.append({'reason': 'unresolved_analyze_name_owner_or_group',
                                'utterance_id': row['utterance_id'], 'candidate_id': mention.get('candidate_id')})
                continue
            owner = persons.canonical(state, owner)
            mids = set(by_person.get(owner, ()))
            ref = mention.get('recipient_cluster_ref')
            if ref in state.get('observations', {}) and persons.canonical(state, state['cluster_to_person'].get(ref)) == owner:
                explicit = {str(o['source_bank_id']) for o in state['observations'][ref] if o.get('source_bank_id') is not None}
                mids.intersection_update(explicit)
            start = candidate.get('original_start', candidate.get('start'))
            end = candidate.get('original_end', candidate.get('end'))
            occurrence = (row['utterance_id'], start, end) if type(start) is int and type(end) is int else (
                row['utterance_id'], mention.get('candidate_id'))
            for mid in sorted(mids):
                key = (mid, gid, occurrence)
                if key in seen:
                    continue
                seen.add(key)
                record = votes[mid].setdefault(gid, {'group_id': gid, 'canonical_name': groups[gid]['canonical_name'], 'occurrences': []})
                record['occurrences'].append({'utterance_id': row['utterance_id'], 'candidate_id': mention.get('candidate_id'),
                    'mention_type': mention.get('mention_type', 'unknown'), 'owner_person_id': owner,
                    'order': (number, start if type(start) is int else 0, end if type(end) is int else 0),
                    'source_occurrence': list(occurrence)})
    counts = chapter_counts(raw)
    winners, selections = {}, []
    for mid, options in sorted(votes.items()):
        winner, ordered = choose(list(options.values()))
        support = len(winner['occurrences'])
        eligible = people[mid] in prior_named or (counts.get(mid, 0) >= minimum and support >= minimum_events)
        selection = {'magi_id': mid, 'group_id': winner['group_id'], 'canonical_name': winner['canonical_name'],
            'chapter_crop_count': counts.get(mid, 0), 'eligible': eligible, 'support': support,
            'minimum_confirmed_mentions': minimum_events,
            'candidates': ordered, 'tie_break': 'count,introduction,direct_address,reference,first_occurrence',
            'needs_review': len(ordered) > 1}
        selections.append(selection)
        if len(ordered) > 1:
            reviews.append({'reason': 'competing_analyze_name_groups', **copy.deepcopy(selection)})
        if eligible:
            winners[mid] = winner
        else:
            reason = 'below_crop_threshold' if counts.get(mid, 0) < minimum else 'below_confirmed_mention_threshold'
            reviews.append({'reason': reason, **copy.deepcopy(selection)})
    proposals = []
    for gid in sorted({w['group_id'] for w in winners.values()}):
        mids = [m for m, w in winners.items() if w['group_id'] == gid]
        names = [groups[gid]['canonical_name'], *groups[gid].get('aliases', [])]
        anchors = set()
        for name in names:
            try:
                form = persons.name_form(name)
            except (ValueError, TypeError):
                continue
            anchors.update(persons.canonical(state, p) for key in (form['surface'], form['base_name'])
                           for p in state['name_index'].get(key, []) if persons.canonical(state, p) in prior_named)
        new_mids = [m for m in mids if people[m] not in prior_named]
        if len(anchors) > 1:
            reviews.append({'reason': 'multiple_existing_identities_for_group', 'group_id': gid, 'person_ids': sorted(anchors)})
            continue  # Never merge established identities; each new record still gets its selected name below.
        target = next(iter(anchors)) if anchors else people[main_member(new_mids, metadata)] if new_mids else None
        for mid in new_mids:
            pid = persons.canonical(state, people[mid])
            if target is None or pid == target:
                continue
            refs = [ref for ref, p in state['cluster_to_person'].items() if persons.canonical(state, p) == pid]
            if not refs:
                continue
            proposal = {'cluster_ref': refs[0], 'cluster_refs': sorted(refs), 'person_id': target,
                'magi_id': mid, 'group_id': gid, 'same_person_status': 'supported',
                'decision_basis': POLICY, 'confirmed_event_count': len(winners[mid]['occurrences']),
                'event_refs': [o['candidate_id'] for o in winners[mid]['occurrences']],
                'contradictions': [], 'source_cluster_bank_mutated': False}
            before = len(state.get('link_reviews', []))
            state = persons.apply_links(state, [proposal])
            if len(state.get('link_reviews', [])) == before:
                proposals.append(proposal)
            else:
                reviews.extend(state['link_reviews'][before:])
    # Assign names once per final identity, not once per occurrence or group job.
    per_identity = defaultdict(dict)
    for mid, winner in winners.items():
        pid = persons.canonical(state, people[mid])
        record = per_identity[pid].setdefault(winner['group_id'], {**copy.deepcopy(winner), 'occurrences': []})
        record['occurrences'].extend(winner['occurrences'])
    for pid, options in sorted(per_identity.items()):
        for record in options.values():
            record['occurrences'] = list({tuple(o['source_occurrence']): o for o in record['occurrences']}.values())
        winner, ordered = choose(list(options.values()))
        if len(ordered) > 1:
            reviews.append({'reason': 'existing_identity_name_conflict', 'person_id': pid, 'candidates': ordered})
        reviews.extend(replace_names(state, pid, winner['canonical_name'], groups[winner['group_id']].get('aliases', []),
                                     fingerprint([POLICY, pid, winner])))
    for selection in selections:
        selection['identity_id'] = persons.canonical(state, people[selection['magi_id']]) if selection['eligible'] else None
    return state, {'selections': selections, 'proposals': proposals, 'needs_review': reviews,
                   'chapter_crop_counts': counts, 'groups': list(groups.values())}


def build(store, provider, analyzed):
    snapshot = store.directory / 'snapshots/persons-v6.json'
    prior = read_json(snapshot)['state'] if snapshot.exists() else {'persons': {}}
    metadata = read_json(store.manifest['bank_path'])
    state, data = register(analyzed, store.read('graph'), store.read('extract'), metadata, prior,
                           store.manifest['config']['v6']['identity_min_chapter_crops'],
                           minimum_events=store.manifest['config']['v6']['min_confirmed_call_events'])
    named = {persons.canonical(state, p) for p in state['persons'] if confirmed(state, p)}
    previous = {persons.canonical(prior, p) for p in prior['persons'] if confirmed(prior, p)}
    result = {'policy': POLICY, **data, 'summary': {'groups': len(data['groups']),
        'requests': 0, 'request_statuses': {}, 'automatic_retries': 0,
        'confirmed_occurrences': len({(o['utterance_id'], o['candidate_id']) for s in data['selections']
                                     for c in s['candidates'] for o in c['occurrences']}),
        'named_identities': len(named), 'new_named_identities': len(named-previous),
        'named_bank_identities_total': len(named), 'chapter_crop_counts': data['chapter_crop_counts']},
        'accepted': [], 'effective_updates': {}, 'communication_evidence': [], 'outcomes': [],
        'inference_failures': [], 'reference_crops': [], 'crop_issues': [],
        'review_status': 'completed_with_review' if data['needs_review'] else 'completed'}
    result['snapshot_hash'] = fingerprint(result)
    atomic_json(store.artifact_path('build_identity').parent / 'name-binding/result.json', result)
    return state, result
