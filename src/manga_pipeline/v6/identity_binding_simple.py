"""Model-owned names, mechanical chapter-count gate, unchanged identity linking."""
import copy
from collections import Counter, defaultdict

from ..storage.io import atomic_json, fingerprint, read_json
from . import persons
from .identity_binding_evidence import BindingStore, collect
from .identity_runtime import freeze, images, run_jobs
from .requests import Calls
from .identity_v2 import confirmed
from .semantic_contract import object_schema

POLICY = 'chapter-name-binding-simple-v2'
TASK = 'identity_name_binding_simple_v2'
PROMPT = '''Assign this name group's names to MAGI characters using current OCR,
chapter context, bank metadata and any supplied images. Names may be corrected
by scan; do not reject them because raw OCR used another spelling. A name called,
mentioned or introduced can identify a character. Analyze labels are context,
not vetoes. Return complete character name objects: magi_id, primary_name (null
if unknown), aliases and reason. No source IDs, crop citations, panel citations,
caller proof, occurrence decisions or evidence_kind are required. Code counts
chapter crops and gates NEW identities at the configured minimum; do not invent
counts or person IDs. Do not merge existing identities or MAGI IDs. JSON only.'''


def chapter_counts(raw):
    observations = defaultdict(set)
    for page in raw.get('pages', []):
        for index, mid in enumerate(page.get('character_ids', [])):
            boxes = page.get('characters', [])
            if mid is not None and index < len(boxes):
                observations[str(mid)].add((page['id'], tuple(boxes[index])))
    return {mid: len(values) for mid, values in observations.items()}


def schema(request):
    return object_schema({'characters': {'type': 'array', 'items': object_schema({
        'magi_id': {'type': 'string'}, 'primary_name': {'type': ['string', 'null']},
        'aliases': {'type': 'array', 'items': {'type': 'string'}}, 'reason': {'type': 'string'}})}})


def normalize(value, request):
    if not isinstance(value, dict) or not isinstance(value.get('characters'), list):
        raise ValueError('Name response has no usable characters array')
    reviews, records, decisions = [], [], []
    latest = {}
    for index, item in enumerate(value['characters']):
        if isinstance(item, dict) and str(item.get('magi_id')) in request['candidates']:
            mid = str(item['magi_id'])
            if mid in latest:
                reviews.append({'reason': 'character_superseded', 'magi_id': mid, 'previous': latest[mid][1]})
            latest[mid] = (index, item)
        else:
            latest['invalid:'+str(index)] = (index, item)
    for index, item in sorted(latest.values(), key=lambda entry: entry[0]):
        if not isinstance(item, dict):
            reviews.append({'reason': 'invalid_character_data', 'index': index})
            continue
        mid = str(item.get('magi_id'))
        candidate = request['candidates'].get(mid)
        if candidate is None:
            reviews.append({'reason': 'unknown_magi_id', 'index': index, 'value': item})
            continue
        primary = item.get('primary_name')
        record = {**copy.deepcopy(item), 'magi_id': mid, 'chapter_crop_count': candidate['chapter_crop_count']}
        if not isinstance(primary, str) or not primary.strip() or primary.casefold() in {'unknown', 'other', 'others'}:
            record['resolution'] = 'no_name_assignment'
            records.append(record)
            reviews.append({'reason': 'no_name_assignment', 'character': record})
            continue
        if not candidate['existing_named_identity'] and candidate['chapter_crop_count'] < request['minimum_chapter_crops']:
            record['resolution'] = 'below_crop_threshold'
            records.append(record)
            reviews.append({'reason': 'below_crop_threshold', 'character': record})
            continue
        aliases = item.get('aliases', [])
        if not isinstance(aliases, list):
            reviews.append({'reason': 'invalid_aliases', 'magi_id': mid})
            aliases = []
        invalid_aliases = [a for a in aliases if not isinstance(a, str) or not a.strip()]
        if invalid_aliases:
            reviews.append({'reason': 'invalid_alias_fields', 'magi_id': mid, 'values': invalid_aliases})
        aliases = [a for a in aliases if isinstance(a, str) and a.strip()]
        reason = item.get('reason') if isinstance(item.get('reason'), str) else ''
        records.append({**record, 'resolution': 'named', 'aliases': aliases})
        for occurrence in request['occurrences']:
            supplied = sorted({r['id'] for r in occurrence['context']} | {c['crop_id'] for c in request['crops']})
            decisions.append({'occurrence_id': occurrence['id'], 'status': 'confirmed', 'owner_magi_id': mid,
                'speaker_magi_id': None, 'evidence_kind': 'unknown', 'reason': reason,
                'evidence_refs': [], 'supplied_evidence_refs': supplied, 'model_evidence_refs': [],
                'identity_name': primary, 'identity_aliases': aliases})
    return {'characters': records, 'decisions': decisions, 'group_status': 'consistent', 'group_reason': '',
            'reviews': reviews}


def validate(value, request):
    if not isinstance(value, dict) or not isinstance(value.get('decisions'), list):
        raise ValueError('Malformed normalized name data')


def replace_names(state, pid, primary, aliases, source):
    valid = []
    reviews = []
    for value in [primary, *aliases]:
        try:
            persons.name_form(value)
            valid.append(value)
        except (ValueError, TypeError):
            reviews.append({'reason': 'unusable_name_field', 'person_id': pid, 'value': value})
    if not valid:
        return reviews
    for key, owners in list(state.get('name_index', {}).items()):
        remaining = [p for p in owners if persons.canonical(state, p) != pid]
        if remaining:
            state['name_index'][key] = remaining
        else:
            del state['name_index'][key]
    state['persons'][pid].update(primary_name=None, name_forms=[])
    for name in dict.fromkeys(valid):
        persons.add_name(state, pid, name, source=source)
    state['persons'][pid]['primary_name'] = valid[0]
    return reviews


def build(store, provider, analyzed):
    from . import identity_binding as legacy
    from .name_normalization import apply
    isolated = BindingStore(store)
    directory = isolated.artifact_path('build_identity').parent
    scanned, raw = store.read('scan'), store.read('extract')
    document = store.read('normalize')
    if scanned.get('name_normalization'):
        document = apply(document, scanned['name_normalization'])
    state = copy.deepcopy(analyzed['person_state'])
    metadata = read_json(store.manifest['bank_path'])
    counts = chapter_counts(raw)
    minimum = store.manifest['config']['v6'].get('identity_min_chapter_crops', 8)
    snapshot_path = store.directory / 'snapshots/persons-v6.json'
    registry_path = persons.registry_path(store)
    prior_state = (read_json(snapshot_path)['state'] if snapshot_path.exists() else
                   read_json(registry_path) if registry_path.exists() else {'persons': {}})
    prior_named = {persons.canonical(prior_state, p) for p in prior_state['persons'] if confirmed(prior_state, p)}
    # Analyze may have proposed names; only this stage creates a NEW named identity.
    for pid in state['persons']:
        if pid not in prior_named:
            for key, owners in list(state.get('name_index', {}).items()):
                left = [p for p in owners if p != pid]
                if left:
                    state['name_index'][key] = left
                else:
                    del state['name_index'][key]
            state['persons'][pid].update(primary_name=None, name_forms=[])
    input_analyzed = {**analyzed, 'person_state': state}

    def prepare():
        evidence = collect(isolated, input_analyzed, scanned, document, raw, metadata)
        jobs, budgets = [], []
        cap = store.manifest['config']['vllm']['max_images_per_prompt']
        cfg = store.manifest['config']
        reserve = cfg['v6']['identity_binding_output_tokens']
        generation = fingerprint([cfg['llm'], cfg.get('vllm', {}), PROMPT])
        calls = Calls(isolated, provider, 'build_identity', allow_uncertain=True)

        def visit(group, occurrences, suffix='root'):
            request, image_count = legacy.make_request(group, occurrences, evidence, state, metadata, cap)
            request.update(policy=POLICY, minimum_chapter_crops=minimum)
            for mid, candidate in request['candidates'].items():
                candidate.update(chapter_crop_count=counts.get(mid, 0),
                    existing_named_identity=confirmed(state, candidate['person_id']))
            report = calls.budget(TASK, PROMPT, {**request, 'generation_identity': generation},
                                  schema(request), reserve, images(isolated, request['crops']))
            fits = report['estimate'] <= report['limit']
            if (not fits or image_count > cap) and len(occurrences) > 1:
                middle = len(occurrences)//2
                visit(group, occurrences[:middle], suffix+'0')
                visit(group, occurrences[middle:], suffix+'1')
                return
            key = 'name-simple:'+group['group_id']+':'+suffix
            if not fits:
                budgets.append({'batch_id': key, 'group_id': group['group_id'], 'status': 'budget_failed',
                                'post_sent': False, 'reason': 'Indivisible name context exceeds budget',
                                'budget': report, 'occurrence_ids': [o['id'] for o in occurrences]})
                return
            jobs.append({'batch_id': key, 'group_id': group['group_id'],
                         'task': TASK, 'request': request, 'crops': request['crops'], 'budget': report})
        for group in evidence['groups']:
            occurrences = [o for o in group['occurrences'] if not o['skip_reason']]
            if occurrences:
                visit(group, occurrences)
        return {'evidence': evidence, 'jobs': jobs, 'budget_failures': budgets}

    plan = freeze(directory / 'plan.json', [input_analyzed, scanned, raw, document, metadata,
        store.manifest['config']], prepare)
    evidence = plan['evidence']
    retained = [*evidence['panel_crops'], *[c for values in evidence['bank_crops'].values() for c in values]]
    images(isolated, retained)
    cfg = store.manifest['config']['v6']
    outcomes = run_jobs(isolated, provider, 'build_identity', plan['jobs'], PROMPT, schema, validate,
        cfg['identity_binding_output_tokens'], cfg['identity_binding_concurrency'], normalize=normalize)
    state, accepted, proposals, reviews, blocked = legacy.reconcile(state, evidence, plan['jobs'], outcomes, metadata)
    for outcome in sorted(outcomes, key=lambda o: o['batch_id']):
        if outcome['status'] == 'completed':
            reviews.extend(outcome['result']['reviews'])
    for claim in accepted:
        reviews.extend(replace_names(state, claim['person_id'], claim['identity_name'], claim['identity_aliases'], claim['decision_id']))
    updates, communication, conflicts = legacy.effective_updates(analyzed, state, accepted, evidence['people'])
    failures = [o for o in outcomes if o['status'] != 'completed'] + plan['budget_failures']
    skipped = [{'group_id': g['group_id'], 'occurrence_id': o['id'], 'reason': o['skip_reason']}
               for g in evidence['groups'] for o in g['occurrences'] if o['skip_reason']]
    ungrouped = [c['id'] for c in scanned.get('candidates', []) if not c.get('alias_groups')]
    reviews.extend({'reason': 'ungrouped_name', 'candidate_id': cid} for cid in ungrouped)
    needs_review = [*reviews, *conflicts, *failures, *skipped, *evidence['crop_issues']]
    named = {persons.canonical(state, p) for p in evidence['people'].values() if confirmed(state, p)}
    result = {'policy': POLICY, 'summary': {'groups': len(evidence['groups']),
        'occurrences': sum(len(g['occurrences']) for g in evidence['groups']), 'requests': len(plan['jobs']),
        'request_statuses': dict(Counter(o['status'] for o in outcomes)), 'confirmed_occurrences': len(accepted),
        'named_identities': len({c['person_id'] for c in accepted}), 'new_named_identities': len(named-prior_named),
        'named_bank_identities_total': len(named), 'automatic_retries': 0, 'chapter_crop_counts': counts},
        'accepted': accepted, 'effective_updates': updates, 'communication_evidence': communication,
        'needs_review': needs_review, 'inference_failures': failures, 'outcomes': outcomes,
        'groups': evidence['groups'], 'skipped': skipped, 'budget_failures': plan['budget_failures'], 'reference_crops': retained,
        'crop_issues': evidence['crop_issues'], 'name_registry': state['name_groups_v1'], 'proposals': proposals,
        'review_status': 'completed_with_review' if needs_review else 'completed'}
    result['snapshot_hash'] = fingerprint(result)
    atomic_json(directory / 'result.json', result)
    atomic_json(store.directory / 'snapshots/name-groups-v1.json', state['name_groups_v1'])
    return state, result
