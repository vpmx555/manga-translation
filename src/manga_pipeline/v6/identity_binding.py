"""Ground new names in chapter evidence before profiles; no inference retries."""
import copy
from collections import Counter, defaultdict

from ..storage.io import atomic_json, fingerprint, read_json
from . import persons
from .identity_binding_evidence import BindingStore, collect
from .identity_runtime import freeze, images, run_jobs, EvidenceIntegrityError
from .identity_v2 import confirmed
from .requests import Calls
from .semantic_contract import object_schema

POLICY = 'chapter-multimodal-name-binding-v1'
TASK = 'identity_name_binding_v1'
PROMPT = '''Identify the owner of each supplied name OCCURRENCE from the labelled
panels, bank crops, chapter source text and frozen analyze metadata. The registry
may have NO names: an empty known-name list or prior confirmed_person_name=false
does NOT reject a new name. Bind only supplied MAGI IDs with direct evidence:
self_introduction, narrator_introduction, or direct_address by another character.
One explicit, uniquely grounded introduction/call is sufficient. Mention_type is
already decided by analyze: never reclassify it. A narrator reference can introduce
a character, but a third-person reference is NOT a call to the listener. For an
off-panel recipient use ONLY the supplied individual off_panel_magi_id; never
guess from a group, unknown listener or conversational alternation. People without
a MAGI bank ID are out of scope. Cite the occurrence utterance, relevant panel and
owner bank crop. Images are labelled MAGI, not confirmed character names.
Decide each occurrence separately; retain ambiguous occurrences as unresolved.
If the alias group combines DIFFERENT people, return group_status=suspect; do NOT
split/rewrite the group. A person can have several names, and several MAGI IDs may
be the same person: different IDs alone do NOT prove different people. Never
invent aliases or change a confirmed identity. Optionally identify the visible
speaker for a direct call; do not change a resolved analyze speaker. JSON only.'''


def enabled(config):
    return config.get('v6', {}).get('identity_name_binding_policy') in {POLICY, 'chapter-name-binding-simple-v2'}


def schema(request):
    mids = sorted(request['candidates'])
    decision = object_schema({
        'occurrence_id': {'type': 'string', 'enum': [o['id'] for o in request['occurrences']]},
        'status': {'type': 'string', 'enum': ['confirmed', 'unresolved', 'not_person_name']},
        'owner_magi_id': {'type': ['string', 'null'], 'enum': [*mids, None]},
        'speaker_magi_id': {'type': ['string', 'null'], 'enum': [*mids, None]},
        'evidence_kind': {'type': 'string', 'enum': [
            'self_introduction', 'narrator_introduction', 'direct_address', 'unknown']},
        'evidence_refs': {'type': 'array', 'items': {'type': 'string'}},
        'reason': {'type': 'string'}})
    count = len(request['occurrences'])
    return object_schema({'group_status': {'type': 'string', 'enum': ['consistent', 'suspect']},
        'group_reason': {'type': 'string'},
        'decisions': {'type': 'array', 'minItems': count, 'maxItems': count, 'items': decision}})


def validate(value, request):
    if (not isinstance(value, dict) or set(value) != {'group_status', 'group_reason', 'decisions'}
            or value['group_status'] not in {'consistent', 'suspect'}
            or not isinstance(value['group_reason'], str)
            or value['group_status'] == 'suspect' and not value['group_reason'].strip()
            or not isinstance(value['decisions'], list)):
        raise ValueError('Invalid name-binding response')
    occurrences = {o['id']: o for o in request['occurrences']}
    decisions = value['decisions']
    if (len(decisions) != len(occurrences) or any(not isinstance(d, dict) for d in decisions)
            or Counter(d.get('occurrence_id') for d in decisions) != Counter(occurrences.keys())):
        raise ValueError('Name occurrence coverage lost or duplicated')
    crops = {c['crop_id']: c for c in request['crops']}
    refs = set(crops) | {r['id'] for o in occurrences.values() for r in o['context']}
    for decision in decisions:
        if (set(decision) != {'occurrence_id', 'status', 'owner_magi_id', 'speaker_magi_id',
                             'evidence_kind', 'evidence_refs', 'reason'}
                or decision['status'] not in {'confirmed', 'unresolved', 'not_person_name'}
                or not isinstance(decision['reason'], str) or not decision['reason'].strip()
                or not isinstance(decision['evidence_refs'], list)
                or any(r not in refs for r in decision['evidence_refs'])):
            raise ValueError('Invalid name occurrence decision/evidence')
        owner, caller = decision['owner_magi_id'], decision['speaker_magi_id']
        if decision['status'] != 'confirmed':
            if owner is not None or caller is not None or decision['evidence_kind'] != 'unknown':
                raise ValueError('Unresolved name cannot supply an owner or caller')
            continue
        occurrence = occurrences[decision['occurrence_id']]
        if owner not in occurrence['allowed_magi_ids'] or owner not in request['candidates']:
            raise ValueError('Name owner is outside the occurrence candidate pool')
        evidence = set(decision['evidence_refs'])
        bank_refs = {r for r, c in crops.items() if c.get('kind') == 'bank' and c['magi_id'] == owner}
        if occurrence['utterance_id'] not in evidence or not bank_refs.intersection(evidence):
            raise ValueError('Confirmed name needs its source utterance and owner crop')
        panel_refs = {r for r in occurrence['panel_refs'] if r in crops and
                      any(a['magi_id'] == owner for a in crops[r].get('annotations', []))}
        visible = owner in occurrence['visible_magi_ids']
        if visible and not panel_refs.intersection(evidence):
            raise ValueError('Visible name owner needs its labelled panel evidence')
        kind = decision['evidence_kind']
        if kind == 'direct_address':
            if occurrence['mention_type'] != 'direct_address' or occurrence['content_type'] != 'dialogue':
                raise ValueError('A third-person reference is not a direct name call')
            if not visible and owner != occurrence['off_panel_magi_id']:
                raise ValueError('Off-panel name owner lacks a resolved individual listener')
        elif kind == 'self_introduction':
            if occurrence['mention_type'] != 'introduction' or occurrence['content_type'] != 'dialogue' or not visible:
                raise ValueError('Self introduction needs an analyzed introduction and visible owner')
            known = occurrence['analysis_speaker_id']
            if known and known != request['candidates'][owner]['person_id']:
                raise ValueError('Self introduction contradicts a resolved speaker')
            if caller not in (None, owner):
                raise ValueError('A self introduction cannot name a different caller')
        elif kind == 'narrator_introduction':
            if (occurrence['content_type'] != 'narration' and occurrence['speaker_kind'] != 'narrator') or not visible:
                raise ValueError('Narrator introduction needs a narrated, visible subject')
            if caller is not None:
                raise ValueError('Narrator introduction cannot create a character caller')
        else:
            raise ValueError('Confirmed name needs a supported introduction/call')
        if caller is not None:
            caller_bank = {r for r, c in crops.items() if c.get('kind') == 'bank' and c['magi_id'] == caller}
            caller_panel = {r for r in occurrence['panel_refs'] if r in crops and
                            any(a['magi_id'] == caller for a in crops[r].get('annotations', []))}
            if (caller not in occurrence['visible_magi_ids'] or caller not in request['candidates']
                    or not caller_bank.intersection(evidence) or not caller_panel.intersection(evidence)):
                raise ValueError('Supplemental speaker needs labelled panel and bank evidence')


def make_request(group, occurrences, evidence, state, metadata, cap):
    mids = sorted({m for o in occurrences for m in o['allowed_magi_ids']})
    panels = {c['crop_id']: c for c in evidence['panel_crops']}
    needed_panels = sorted({r for o in occurrences for r in o['panel_refs']})
    all_crops = [panels[r] for r in needed_panels]
    for mid in sorted(mids, key=lambda m: (-metadata['characters'][m].get('appearance_count', 0), m)):
        all_crops.extend(evidence['bank_crops'].get(mid, []))
    unique = {c['crop_id']: c for c in all_crops}
    all_crops = list(unique.values())
    crops = all_crops[:cap]
    candidates = {}
    for mid in mids:
        pid = evidence['people'][mid]
        person = state['persons'][pid]
        candidates[mid] = {'magi_id': mid, 'person_id': pid, 'primary_name': person.get('primary_name'),
                           'aliases': [f['surface'] for f in person.get('name_forms', [])]}
    request = {'policy': POLICY, 'group_id': group['group_id'], 'canonical_name': group['canonical_name'],
        'aliases': group['aliases'], 'occurrences': copy.deepcopy(occurrences),
        'candidates': candidates, 'crops': crops, 'evidence_segments': [],
        'omitted_images': [c['crop_id'] for c in all_crops[cap:]]}
    return request, len(all_crops)


def plan_jobs(store, provider, evidence, state, metadata):
    calls = Calls(store, provider, 'build_identity', allow_uncertain=True)
    cfg = store.manifest['config']
    reserve = cfg['v6']['identity_binding_output_tokens']
    generation = fingerprint([cfg['llm'], cfg.get('vllm', {}), PROMPT])
    cap = cfg['vllm']['max_images_per_prompt']
    jobs, skipped, budgets = [], [], []

    def visit(group, occurrences, suffix):
        request, image_count = make_request(group, occurrences, evidence, state, metadata, cap)
        request['generation_identity'] = generation
        report = calls.budget(TASK, PROMPT, request, schema(request), reserve,
                              images(store, request['crops']))
        # Reserve space for every structured decision as well as input tokens.
        fits = report['estimate'] <= report['limit'] and 256+384*len(occurrences) <= reserve
        if (not fits or image_count > cap) and len(occurrences) > 1:
            middle = len(occurrences)//2
            visit(group, occurrences[:middle], suffix+'0')
            visit(group, occurrences[middle:], suffix+'1')
            return
        key = 'name-binding:'+group['group_id']+':'+(suffix or 'root')
        if not fits:
            budgets.append({'batch_id': key, 'group_id': group['group_id'], 'status': 'budget_failed',
                'post_sent': False, 'occurrence_ids': [o['id'] for o in occurrences], 'budget': report,
                'reason': 'Single indivisible name occurrence exceeds input/output budget'})
            return
        jobs.append({'batch_id': key, 'group_id': group['group_id'], 'task': TASK, 'request': request,
                     'crops': request['crops'], 'budget': report})

    for group in evidence['groups']:
        eligible = []
        for occurrence in group['occurrences']:
            reason = 'suspect_alias_group' if group['suspect'] else occurrence['skip_reason']
            if reason:
                skipped.append({'group_id': group['group_id'], 'occurrence_id': occurrence['id'], 'reason': reason})
            else:
                eligible.append(occurrence)
        if eligible:
            visit(group, eligible, '')
    return {'jobs': jobs, 'skipped': skipped, 'budget_failures': budgets}


def reconcile(state, evidence, jobs, outcomes, metadata):
    """Resolve the complete set at once; completion order is never arbitration."""
    state = copy.deepcopy(state)
    prior_confirmed = {p for p in state['persons'] if confirmed(state, p)}
    lookup = {j['batch_id']: j for j in jobs}
    groups = {g['group_id']: g for g in evidence['groups']}
    blocked = {g['group_id'] for g in evidence['groups'] if g['suspect']}
    claims, reviews = defaultdict(list), []
    for result in sorted(outcomes, key=lambda r: r['batch_id']):
        if result['status'] != 'completed':
            continue
        job = lookup[result['batch_id']]
        gid, response = job['group_id'], result['result']
        if response['group_status'] == 'suspect':
            blocked.add(gid)
            reviews.append({'group_id': gid, 'reason': 'suspect_alias_group', 'evidence': response['group_reason']})
        by_id = {o['id']: o for o in job['request']['occurrences']}
        for decision in response['decisions']:
            if decision['status'] == 'confirmed':
                claims[gid].append({**decision, 'occurrence': by_id[decision['occurrence_id']],
                    'batch_id': job['batch_id'], 'decision_id': fingerprint([job['batch_id'], decision])})
    # Validate known name ownership against the initial snapshot, not names
    # added by a different request in this pass.
    for gid, items in claims.items():
        for claim in items:
            pid = evidence['people'][claim['owner_magi_id']]
            form = persons.name_form(claim['occurrence']['name'])
            owners = {persons.canonical(state, p) for key in (form['surface'], form['base_name'])
                      for p in state.get('name_index', {}).get(key, [])}
            if owners and pid not in owners and any(p in prior_confirmed for p in owners):
                blocked.add(gid)
                reviews.append({'group_id': gid, 'reason': 'known_name_owner_conflict', 'claim': claim})
    # Connected components expose contradictions across independently returned
    # groups without choosing an arbitrary winning group.
    parent = {p: p for p in state['persons']}

    def root(p):
        while parent[p] != p:
            p = parent[p]
        return p

    for gid, items in sorted(claims.items()):
        if gid in blocked:
            continue
        people = sorted({evidence['people'][c['owner_magi_id']] for c in items})
        for p in people[1:]:
            parent[root(p)] = root(people[0])
    components = defaultdict(set)
    for p in parent:
        components[root(p)].add(p)
    forbidden = [set(persons.canonical(state, p) for p in pair['person_ids'])
                 for pair in state.get('cannot_links', [])]
    invalid = {r for r, ps in components.items() if len(ps & prior_confirmed) > 1
               or any(pair.issubset(ps) for pair in forbidden)}
    for gid, items in claims.items():
        if any(root(evidence['people'][c['owner_magi_id']]) in invalid for c in items):
            blocked.add(gid)
            reviews.append({'group_id': gid, 'reason': 'identity_or_cannot_link_conflict'})
    proposals, accepted = [], []
    for r, people in sorted(components.items()):
        active = [c for gid, cs in claims.items() if gid not in blocked for c in cs
                  if root(evidence['people'][c['owner_magi_id']]) == r]
        if not active or r in invalid:
            continue
        targets = people & prior_confirmed
        mids = {c['owner_magi_id'] for c in active}
        main = min(mids, key=lambda m: (-metadata['characters'][m].get('appearance_count', 0), m))
        target = next(iter(targets)) if targets else evidence['people'][main]
        # Link before adding confirmed forms so two newly named bank members
        # can share one identity; pre-existing confirmed identities stay protected.
        for pid in sorted(people-{target}):
            refs = sorted(ref for ref, p in state['cluster_to_person'].items()
                          if persons.canonical(state, p) == pid)
            if not refs:
                continue
            proposal = {'cluster_ref': refs[0], 'cluster_refs': refs, 'person_id': target,
                'same_person_status': 'supported', 'decision_basis': POLICY,
                'event_refs': sorted({c['decision_id'] for c in active}), 'confirmed_event_count': len(active),
                'contradictions': [], 'source_cluster_bank_mutated': False}
            state = persons.apply_links(state, [proposal])
            proposals.append(proposal)
        for claim in active:
            pid = persons.canonical(state, evidence['people'][claim['owner_magi_id']])
            persons.add_name(state, pid, claim['occurrence']['name'], source=claim['decision_id'])
            accepted.append({**claim, 'person_id': pid})
    registry = copy.deepcopy(state.get('name_groups_v1', {'policy': POLICY, 'groups': {}}))
    if registry.get('policy') != POLICY:
        raise EvidenceIntegrityError('Incompatible name-group registry')
    for gid, group in groups.items():
        bindings = sorted([{'occurrence_id': c['occurrence_id'], 'person_id': c['person_id'],
            'magi_id': c['owner_magi_id'], 'decision_id': c['decision_id']} for c in accepted
            if lookup[c['batch_id']]['group_id'] == gid], key=lambda c: c['occurrence_id'])
        history = registry['groups'].setdefault(gid, [])
        previous = history[-1] if history else {}
        prior_owners = previous.get('known_person_ids', [b['person_id'] for b in previous.get('bindings', [])])
        known_people = sorted({persons.canonical(state, p) for p in
                               [*prior_owners, *[b['person_id'] for b in bindings]]
                               if p in state['persons']})
        record = {'group_id': gid, 'canonical_name': group['canonical_name'], 'aliases': group['aliases'],
            'status': 'suspect' if gid in blocked else 'bound' if known_people else 'unbound',
            'bindings': bindings, 'known_person_ids': known_people, 'occurrences': group['occurrences']}
        record['version'] = fingerprint(record)
        if not history or history[-1]['version'] != record['version']:
            history.append(record)
    state['name_groups_v1'] = registry
    return state, accepted, proposals, reviews, blocked


def effective_updates(analyzed, state, accepted, people):
    rows = {r['utterance_id']: r for r in analyzed['utterances']}
    suggestions = defaultdict(lambda: {'speaker': set(), 'targets': set(), 'claims': [], 'names': []})
    reviews = []
    for claim in accepted:
        occurrence = claim['occurrence']
        uid, kind = occurrence['utterance_id'], claim['evidence_kind']
        proposal = suggestions[uid]
        proposal['names'].append({'candidate_id': occurrence['id'], 'person_id': claim['person_id'],
            'decision_id': claim['decision_id'], 'evidence_refs': claim['evidence_refs'],
            'evidence': claim['reason'], 'evidence_kind': kind})
        proposal['claims'].append(claim['decision_id'])
        caller = claim['speaker_magi_id']
        if kind == 'self_introduction':
            proposal['speaker'].add(persons.canonical(state, claim['person_id']))
        elif kind == 'direct_address':
            proposal['targets'].add(persons.canonical(state, claim['person_id']))
            if caller:
                proposal['speaker'].add(persons.canonical(state, people[caller]))
    updates, communication = {}, []
    for uid, proposal in sorted(suggestions.items()):
        row = rows[uid]
        original = {'speaker_person_id': row.get('speaker_person_id'), 'speaker_kind': row.get('speaker_kind'),
                    'addressee_person_ids': row.get('addressee_person_ids', []),
                    'addressee_type': row.get('addressee_type')}
        update = {'source': POLICY, 'decision_ids': sorted(set(proposal['claims'])),
                  'original': original, 'names': proposal['names'], 'fields': {}}
        speaker = persons.canonical(state, row.get('speaker_person_id'))
        conflicts = []
        if proposal['speaker']:
            if len(proposal['speaker']) != 1:
                conflicts.append('conflicting_supplemental_speakers')
            else:
                supplied = next(iter(proposal['speaker']))
                if speaker and speaker != supplied or row.get('speaker_kind') == 'narrator':
                    conflicts.append('resolved_speaker_conflict')
                elif not speaker:
                    speaker = supplied
                    update['fields'].update(speaker_person_id=speaker, speaker_kind='person')
        targets = {persons.canonical(state, p) for p in row.get('addressee_person_ids', [])}
        if proposal['targets']:
            if targets and not proposal['targets'].issubset(targets):
                conflicts.append('resolved_addressee_conflict')
            elif not targets:
                if row.get('addressee_type') not in {None, 'unknown'}:
                    conflicts.append('resolved_addressee_type_conflict')
                else:
                    targets = proposal['targets']
                    update['fields'].update(addressee_person_ids=sorted(targets),
                        addressee_type='individual' if len(targets) == 1 else 'group')
        if conflicts:
            reviews.append({'utterance_id': uid, 'reason': conflicts, 'proposed': update})
            update['fields'] = {}
        elif speaker:
            for target in sorted(proposal['targets']):
                if target != speaker:
                    communication.append({'utterance_id': uid, 'from_id': speaker, 'to_id': target,
                        'source': POLICY, 'decision_ids': update['decision_ids']})
        updates[uid] = update
    return updates, communication, reviews


def build(store, provider, analyzed):
    """Return staged registry changes; identity_v2 commits them atomically."""
    if store.manifest['config']['v6'].get('identity_name_binding_policy') == 'chapter-name-binding-simple-v2':
        from .identity_binding_simple import build as simple_build
        return simple_build(store, provider, analyzed)
    from .name_normalization import apply
    isolated = BindingStore(store)
    directory = isolated.artifact_path('build_identity').parent
    scanned, raw = store.read('scan'), store.read('extract')
    document = copy.deepcopy(store.read('normalize'))
    if scanned.get('name_normalization'):
        document = apply(document, scanned['name_normalization'])
    metadata = read_json(store.manifest['bank_path'])
    state = analyzed['person_state']

    def prepare():
        evidence = collect(isolated, analyzed, scanned, document, raw, metadata)
        return {'evidence': evidence, **plan_jobs(isolated, provider, evidence, state, metadata)}

    plan = freeze(directory / 'plan.json', [analyzed, scanned, raw, document, metadata,
        store.manifest['config']], prepare)
    # Keep every retained planning image in the integrity surface, including
    # sources omitted from an individual child packet.
    evidence = plan['evidence']
    retained = [*evidence['panel_crops'], *[c for cs in evidence['bank_crops'].values() for c in cs]]
    images(isolated, retained)
    cfg = store.manifest['config']['v6']
    outcomes = run_jobs(isolated, provider, 'build_identity', plan['jobs'], PROMPT, schema, validate,
                        cfg['identity_binding_output_tokens'], cfg['identity_binding_concurrency'])
    state, accepted, proposals, reviews, blocked = reconcile(state, evidence, plan['jobs'], outcomes, metadata)
    updates, communication, conflicts = effective_updates(analyzed, state, accepted, evidence['people'])
    failures = [r for r in outcomes if r['status'] != 'completed']
    before = {persons.canonical(analyzed['person_state'], p) for p in analyzed['person_state']['persons']
              if confirmed(analyzed['person_state'], p)}
    named = {persons.canonical(state, p) for p in evidence['people'].values() if confirmed(state, p)}
    summary = {'groups': len(evidence['groups']),
        'occurrences': len({o['id'] for g in evidence['groups'] for o in g['occurrences']}),
        'eligible_occurrences': len({o['id'] for j in plan['jobs'] for o in j['request']['occurrences']}
                                    | {o for b in plan['budget_failures'] for o in b['occurrence_ids']}),
        'requests': len(plan['jobs']), 'request_statuses': dict(Counter(r['status'] for r in outcomes)),
        'confirmed_occurrences': len(accepted), 'named_identities': len({c['person_id'] for c in accepted}),
        'new_named_identities': len({c['person_id'] for c in accepted}-before),
        'named_bank_identities_total': len(named),
        'skipped_occurrences': len(plan['skipped']), 'suspect_groups': len(blocked),
        'budget_failures': len(plan['budget_failures']), 'automatic_retries': 0}
    needs_review = [*reviews, *conflicts, *plan['skipped'], *plan['budget_failures'], *failures]
    unresolved = [dict(group_id=j['group_id'], **d) for result in outcomes if result['status'] == 'completed'
                  for j in plan['jobs'] if j['batch_id'] == result['batch_id']
                  for d in result['result']['decisions'] if d['status'] != 'confirmed']
    needs_review.extend(unresolved)
    if summary['groups'] and not named:
        needs_review.append({'reason': 'zero_confirmed_name_identities'})
    result = {'policy': POLICY, 'summary': summary, 'accepted': accepted, 'effective_updates': updates,
        'communication_evidence': communication, 'needs_review': needs_review,
        'inference_failures': failures, 'groups': evidence['groups'], 'skipped': plan['skipped'],
        'outcomes': outcomes, 'budget_failures': plan['budget_failures'],
        'reference_crops': retained, 'crop_issues': evidence['crop_issues'],
        'name_registry': state['name_groups_v1'], 'proposals': proposals,
        'review_status': 'completed_with_review' if needs_review else 'completed'}
    result['snapshot_hash'] = fingerprint(result)
    atomic_json(directory / 'result.json', result)
    atomic_json(store.directory / 'snapshots/name-groups-v1.json', state['name_groups_v1'])
    return state, result


def project(item, linked):
    """Apply a frozen effective layer while preserving original analyze facts."""
    binding = linked.get('name_binding')
    if binding is None:
        return item
    if binding.get('snapshot_hash') != fingerprint({k: v for k, v in binding.items() if k != 'snapshot_hash'}):
        raise EvidenceIntegrityError('Name-binding snapshot changed')
    update = binding['effective_updates'].get(item['utterance_id'])
    if update is None:
        return item
    original = {'speaker_person_id': item.get('speaker_person_id'), 'speaker_kind': item.get('speaker_kind'),
        'addressee_person_ids': item.get('addressee_person_ids', []), 'addressee_type': item.get('addressee_type')}
    if original != update['original']:
        raise EvidenceIntegrityError('Effective identity source differs from analyze')
    output = copy.deepcopy(item)
    output['analyze_original'] = {**original, 'mentions': copy.deepcopy(item.get('mentions', [])),
                                  'speaker_cluster_ref': item.get('speaker_cluster_ref')}
    output['identity_binding'] = {'source': POLICY, 'snapshot_hash': binding['snapshot_hash'],
                                 'decision_ids': update['decision_ids'], 'fields': sorted(update['fields'])}
    for key, value in update['fields'].items():
        if key == 'speaker_person_id':
            if item.get(key) is not None or item.get('speaker_kind') == 'narrator':
                raise EvidenceIntegrityError('Effective identity would overwrite a known speaker')
            output['speaker_cluster_ref'] = None
        elif key == 'addressee_person_ids':
            if item.get(key) or item.get('addressee_type') not in {None, 'unknown'}:
                raise EvidenceIntegrityError('Effective identity would overwrite a known addressee')
        elif key not in {'speaker_kind', 'addressee_type'}:
            raise EvidenceIntegrityError('Unsupported effective semantic field')
        output[key] = copy.deepcopy(value)
    bound = {n['candidate_id']: n for n in update['names']}
    for mention in output.get('mentions', []):
        name = bound.get(mention.get('candidate_id'))
        if name:
            mention.update(confirmed_person_name=True, target_person_id=name['person_id'],
                           identity_binding_decision=name['decision_id'], evidence=name['evidence'])
    output['communication_evidence'] = [copy.deepcopy(c) for c in binding['communication_evidence']
                                        if c['utterance_id'] == item['utterance_id']]
    return output
