"""Versioned social relations and independently grounded directed address edges."""
import copy
import re

from ..storage.io import atomic_json, fingerprint, read_json
from ..storage.locking import exclusive_lock
from . import persons
from .identity_v2 import POLICY
from .identity_profiles import source_rows
from .identity_runtime import freeze, retain_crops, run_jobs, evidence_refs, checked_evidence
from .semantic_contract import object_schema

GRAPH_POLICY = 'directed-relationships-v2'
CATEGORIES = ('family', 'friendship', 'romance', 'school_work', 'authority', 'unknown')
PROMPT = '''Infer Vietnamese forms of address and social relationships for ONE
pair of confirmed identities. Use the supplied profiles, labelled crops and DIRECT
dialogue segments. Never relink identities. Co-occurrence/name mentions alone do
not establish communication or a social relationship. Return zero or more separate
social relations: category family/friendship/romance/school_work/authority/unknown,
specific relationship, from_id/to_id, role_from/role_to, symmetric, status,
evidence_refs, reason. Social roles must be supported by story evidence.
Keep distinct relations separate; school peers do not imply same class.
Return exactly TWO independent address edges, A->B and B->A, with from_id/to_id,
self/address, status confirmed/provisional/unknown, basis dialogue/context/
visual_age_estimate/unknown, evidence_refs, reason. Never copy the reverse edge.
Use Vietnamese pronouns, not Watashi/Boku/Kun. A named address must name the TARGET,
not the speaker. Explicit story evidence overrides appearance estimates. Appearance
profiles are default anchors only; do not invent factual gender, age or family roles.
Confirmed decisions require supplied dialogue evidence. Visual address estimates
must cite BOTH profile refs (whose anchors were inferred from multiple crops).
Keep unknown when insufficient. Retain prior confirmed decisions if new evidence
contradicts them and record the conflict. All citations must be supplied refs.
Unknown edges have self/address unknown, status unknown, basis unknown. JSON only.'''


def graph_path(store):
    return persons.registry_path(store).with_name('relationships-v2.json')


def empty_graph():
    return {'policy': GRAPH_POLICY, 'identities': {}, 'pairs': {}, 'operations': []}


def pair_key(a, b):
    return fingerprint(sorted([a, b]))


def mermaid(result):
    """Human review uses MAGI bank numbers; person IDs remain machine keys."""
    def safe(text):
        return re.sub(r'["\\<>|`\r\n\[\]]', ' ', str(text))
    nodes = {pid: 'n' + fingerprint(pid)[:12] for pid in result.get('identities', {})}
    lines = ['```mermaid', 'flowchart LR']
    for pid, node in nodes.items():
        item = result['identities'][pid]
        label = 'MAGI ' + '/'.join(item.get('magi_ids', [])) + ' · ' + item.get('primary_name', '')
        lines.append(f'  {node}["{safe(label)}"]')
    for edge in result.get('address_table', []):
        if edge['from_id'] in nodes and edge['to_id'] in nodes:
            label = safe(edge['self'] + ' / ' + edge['address'] + ' · ' + edge['status'])
            lines.append(f'  {nodes[edge["from_id"]]} -->|"{label}"| {nodes[edge["to_id"]]}')
    return '\n'.join([*lines, '```'])


def direct_segments(rows, a, b):
    selected = []
    for i, row in enumerate(rows):
        extra = any({e['from_id'], e['to_id']} == {a, b} for e in row.get('communication_evidence') or [])
        if row.get('semantic_resolution') != 'resolved' and not extra:
            continue
        speaker = row.get('speaker_person_id')
        addressees = set(row.get('addressee_ids') or [])
        if extra or (speaker == a and b in addressees) or (speaker == b and a in addressees):
            selected.append({'priority': 1, 'trigger': row['id'],
                             'rows': rows[max(0, i-1):min(len(rows), i+2)]})
    return selected


def schema(request):
    ids = list(request['identities'])
    person = {'type': 'string', 'enum': ids}
    status = {'type': 'string', 'enum': ['confirmed', 'provisional', 'unknown']}
    refs = {'type': 'array', 'items': {'type': 'string'}}
    edge = object_schema({'from_id': person, 'to_id': person, 'self': {'type': 'string'},
        'address': {'type': 'string'}, 'status': status,
        'basis': {'type': 'string', 'enum': ['dialogue', 'context', 'visual_age_estimate', 'unknown']},
        'evidence_refs': refs, 'reason': {'type': 'string'}})
    relation = object_schema({'category': {'type': 'string', 'enum': list(CATEGORIES)},
        'relationship': {'type': 'string'}, 'from_id': person, 'to_id': person,
        'role_from': {'type': 'string'}, 'role_to': {'type': 'string'},
        'symmetric': {'type': 'boolean'}, 'status': status,
        'evidence_refs': refs, 'reason': {'type': 'string'}})
    return object_schema({'relations': {'type': 'array', 'items': relation, 'maxItems': 8},
                          'address_edges': {'type': 'array', 'items': edge, 'minItems': 2, 'maxItems': 2}})


PRONOUNS = {'tôi', 'tớ', 'mình', 'tao', 'ta', 'con', 'cháu', 'em', 'anh', 'chị', 'cô', 'chú',
            'bác', 'ông', 'bà', 'thầy', 'cậu', 'bạn', 'mày', 'ngài', 'quý vị', 'mọi người',
            'chúng tôi', 'chúng ta', 'chúng mình', 'tụi mình', 'các bạn', 'các cậu', 'unknown'}
PRONOUNS.update({'mẹ', 'bố', 'ba', 'má', 'cha', 'tía', 'u', 'bu', 'cụ', 'dì', 'dượng',
    'mợ', 'thím', 'sếp', 'thầy giáo', 'cô giáo', 'ông nội', 'bà nội', 'ông ngoại', 'bà ngoại',
    'anh trai', 'chị gái', 'em trai', 'em gái', 'các em', 'các anh', 'các chị', 'các cô',
    'các chú', 'các con', 'mấy đứa', 'chúng con', 'chúng cháu', 'tụi em', 'bọn em',
    'bọn tôi', 'chúng tớ', 'bọn tớ', 'tụi tớ', 'bọn tao', 'tụi tao', 'chúng mày',
    'các người', 'nhà ngươi', 'ngươi', 'quý ngài', 'các vị', 'các ông', 'các bà',
    'đàn anh', 'đàn chị', 'tiền bối', 'hậu bối'})


def valid_address(term, target):
    if term.casefold() in PRONOUNS:
        return True
    # Proper names may retain honorifics, but must resolve to the addressed identity.
    name = term.strip()
    for prefix in sorted(PRONOUNS, key=len, reverse=True):
        if name.casefold().startswith(prefix + ' '):
            name = name[len(prefix)+1:]
            break
    try:
        normalized = persons.name_form(name)['base_name']
        return normalized in {persons.name_form(n)['base_name'] for n in
                              [target['primary_name'], *target.get('aliases', [])] if n}
    except ValueError:
        return False


def validate(value, request):
    if not isinstance(value, dict) or set(value) != {'relations', 'address_edges'}:
        raise ValueError('Invalid relationship fields')
    ids = set(request['identities'])
    refs = evidence_refs(request)
    dialogue = {r['id'] for s in request.get('evidence_segments', []) for r in s['rows']}
    profiles = {p['ref'] for p in request.get('profile_refs', [])}
    if not isinstance(value['address_edges'], list) or len(value['address_edges']) != 2:
        raise ValueError('Exactly two directed address edges are required')
    directions = []
    for edge in value['address_edges']:
        if set(edge) != {'from_id', 'to_id', 'self', 'address', 'status', 'basis', 'evidence_refs', 'reason'}:
            raise ValueError('Invalid directed address fields')
        direction = (edge['from_id'], edge['to_id'])
        if set(direction) != ids or edge['status'] not in {'confirmed', 'provisional', 'unknown'}:
            raise ValueError('Unknown or self-referential directed edge')
        directions.append(direction)
        if edge['basis'] not in {'dialogue', 'context', 'visual_age_estimate', 'unknown'}:
            raise ValueError('Invalid address evidence basis')
        checked_evidence(edge, refs, required=edge['status'] != 'unknown')
        if edge['status'] == 'confirmed' and (edge['basis'] != 'dialogue' or not dialogue.intersection(edge['evidence_refs'])):
            raise ValueError('Confirmed address requires dialogue evidence')
        if edge['basis'] == 'visual_age_estimate' and (len(profiles) != 2 or not profiles.issubset(edge['evidence_refs'])
                                                       or edge['status'] != 'provisional'):
            raise ValueError('Appearance address needs two profile anchors and provisional status')
        if edge['status'] == 'unknown':
            if any(edge[k] != 'unknown' for k in ('self', 'address', 'basis')):
                raise ValueError('Unknown address must remain unknown')
        elif (not valid_address(edge['self'], request['identities'][edge['from_id']]) or edge['self'] == 'unknown'
              or edge['address'] == 'unknown' or not valid_address(edge['address'], request['identities'][edge['to_id']])):
            raise ValueError('Address is not Vietnamese or names the wrong identity')
    if len(set(directions)) != 2:
        raise ValueError('Address directions are duplicated')
    if not isinstance(value['relations'], list) or len(value['relations']) > 8:
        raise ValueError('Invalid social relations')
    for rel in value['relations']:
        if (set(rel) != {'category', 'relationship', 'from_id', 'to_id', 'role_from', 'role_to', 'symmetric', 'status', 'evidence_refs', 'reason'}
                or rel['category'] not in CATEGORIES or type(rel['symmetric']) is not bool
                or rel['status'] not in {'confirmed', 'provisional', 'unknown'}
                or {rel['from_id'], rel['to_id']} != ids):
            raise ValueError('Invalid social relation')
        if rel['category'] == 'unknown' and (rel['status'] != 'unknown' or rel['relationship'] != 'unknown'):
            raise ValueError('Unknown social relationship must remain unknown')
        checked_evidence(rel, refs, required=rel['status'] != 'unknown')
        if rel['status'] != 'unknown' and not dialogue.intersection(rel['evidence_refs']):
            raise ValueError('Social relation cannot be established from images alone')


def appearance_fallback(a, b, profiles):
    ages = {'child': 0, 'adolescent': 1, 'young_adult': 2, 'adult': 3, 'older_adult': 4}
    edges = []
    for source, target in ((a, b), (b, a)):
        ps, pt = profiles.get(source), profiles.get(target)
        edge = {'from_id': source, 'to_id': target, 'self': 'unknown', 'address': 'unknown',
                'status': 'unknown', 'basis': 'unknown', 'evidence_refs': [],
                'reason': 'Insufficient valid profile/address evidence'}
        if ps and pt:
            sa, ta = (p['attributes']['apparent_age_group']['value'] for p in (ps, pt))
            if sa in ages and ta in ages:
                if ages[sa] == ages[ta]:
                    edge.update(self='tôi', address='cậu')
                else:
                    older = ps if ages[sa] > ages[ta] else pt
                    presentation = older['attributes']['presentation']['value']
                    male = presentation in {'male_presenting', 'nam'}
                    female = presentation in {'female_presenting', 'nữ'}
                    if male or female:
                        age = older['attributes']['apparent_age_group']['value']
                        term = ('ông' if male else 'bà') if age == 'older_adult' else (
                            ('chú' if male else 'cô') if abs(ages[sa]-ages[ta]) >= 2 else ('anh' if male else 'chị'))
                        edge.update(self=term if older is ps else 'em', address='em' if older is ps else term)
                if edge['self'] != 'unknown':
                    edge.update(status='provisional', basis='visual_age_estimate',
                        evidence_refs=['profile:' + p['person_id'] + ':' + p['version'] for p in (ps, pt)],
                        reason='Fallback from frozen shared-appearance profiles; requires review')
        edges.append(edge)
    return {'relations': [], 'address_edges': edges, 'fallback': True}


def reconcile(previous, fresh):
    """A confirmed decision is never silently overwritten by a contradiction."""
    fresh = copy.deepcopy(fresh)
    conflicts = []
    if not previous:
        return fresh, conflicts
    old = previous['decision']
    by_dir = {(e['from_id'], e['to_id']): e for e in old['address_edges']}
    for i, edge in enumerate(fresh['address_edges']):
        before = by_dir.get((edge['from_id'], edge['to_id']))
        if before and before['status'] == 'confirmed' and (before['self'], before['address']) != (edge['self'], edge['address']):
            conflicts.append({'type': 'address_conflict', 'previous': before, 'proposed': edge})
            fresh['address_edges'][i] = before
    old_confirmed = [r for r in old['relations'] if r['status'] == 'confirmed']
    for rel in old_confirmed:
        signature = lambda r: (r['category'], r['relationship'].strip().casefold(), r.get('role_from'), r.get('role_to'),
            frozenset((r['from_id'], r['to_id'])) if r['symmetric'] else (r['from_id'], r['to_id']), r['symmetric'])
        equivalent = [r for r in fresh['relations'] if signature(r) == signature(rel)]
        if equivalent and any(r['status'] == 'confirmed' for r in equivalent):
            continue
        if rel not in fresh['relations']:
            # Free-text subtypes cannot be proven equivalent or contradictory by
            # string matching. Retain confirmed facts and expose competing claims.
            matching = [r for r in fresh['relations'] if r['category'] == rel['category']]
            if matching:
                conflicts.append({'type': 'social_relation_review', 'previous': rel, 'proposed': matching})
                fresh['relations'] = [r for r in fresh['relations'] if r['category'] != rel['category']]
            fresh['relations'].append(rel)
    return fresh, conflicts


def build(store, provider):
    from .semantic import export
    linked = store.read('build_identity')
    document = export(store.read('normalize'), store.read('analyze'), linked)
    identities, profiles = linked['eligible_identities'], linked['profiles']
    cfg = store.manifest['config']['v6']
    path = graph_path(store)
    directory = store.artifact_path('relationship').parent

    def plan():
        prior = read_json(path) if path.exists() else empty_graph()
        if prior.get('policy') != GRAPH_POLICY:
            raise ValueError('Old relationship graphs cannot seed v2')
        rows = source_rows(document)
        pairs = set()
        for row in rows:
            for communication in row.get('communication_evidence') or []:
                a, b = communication['from_id'], communication['to_id']
                if a in identities and b in identities and a != b:
                    pairs.add(tuple(sorted((a, b))))
            if row.get('semantic_resolution') != 'resolved' or row.get('speaker_person_id') not in identities:
                continue
            for target in row.get('addressee_ids') or []:
                if target in identities and target != row['speaker_person_id']:
                    pairs.add(tuple(sorted((row['speaker_person_id'], target))))
        jobs, reused, issues, retained = [], {}, [], []
        for a, b in sorted(pairs):
            key = pair_key(a, b)
            segments = direct_segments(rows, a, b)
            history = prior['pairs'].get(key, [])
            previous = history[-1] if history else None
            profile_refs = [{'person_id': pid, 'ref': 'profile:' + pid + ':' + profiles[pid]['version']}
                            for pid in (a, b) if pid in profiles]
            source_hash = fingerprint([segments, profile_refs])
            if previous and previous.get('source_hash') == source_hash:
                reused[key] = previous
                continue
            crops = []
            for pid in (a, b):
                selected, errors = retain_crops(store, 'relationship', identities[pid]['main_magi_id'],
                                                cfg['relationship_crops_per_magi'])
                crops.extend({**c, 'person_id': pid} for c in selected)
                issues.extend({'person_id': pid, **e} for e in errors)
            retained.extend(crops)
            request = {'policy': GRAPH_POLICY, 'identities': {p: identities[p] for p in (a, b)},
                       'profiles': {p: profiles[p] for p in (a, b) if p in profiles},
                       'profile_refs': profile_refs, 'evidence_segments': segments,
                       'crops': crops, 'previous': previous}
            jobs.append({'batch_id': 'relationship:' + key, 'task': 'relationship_vi_v2',
                         'request': request, 'crops': crops, 'source_hash': source_hash, 'pair_key': key})
        return {'prior': prior, 'jobs': jobs, 'reused': reused, 'identities': identities,
                'crop_issues': issues, 'reference_crops': retained}

    frozen = freeze(directory / 'plan.json', [linked, document, cfg], plan)
    from . import structural_validators as structural
    simple = structural.enabled(cfg)
    prompt = ("Infer social relationships and TWO independent directed Vietnamese address edges for this pair. "
              "Use the supplied profiles, images and dialogue. Infer freely; use unknown when uncertain. "
              "Return relations and address_edges in the requested JSON shape. Citations are optional; "
              "code records supplied sources. The new complete decision replaces the prior decision. JSON only.") if simple else PROMPT
    results = run_jobs(store, provider, 'relationship', frozen['jobs'], prompt,
                       (lambda r: structural.schema_without_proofs(schema(r))) if simple else schema,
                       structural.validate_relationship if simple else validate,
                       cfg['relationship_output_tokens'], cfg['relationship_concurrency'],
                       normalize=structural.relationship if simple else None)
    current_pairs = copy.deepcopy(frozen['reused'])
    failures, conflicts, updates = [], [], {}
    by_key = {j['batch_id']: j for j in frozen['jobs']}
    for result in results:
        job = by_key[result['batch_id']]
        a, b = job['request']['identities']
        previous = job['request']['previous']
        if result['status'] != 'completed':
            failures.append(result)
            if previous:
                current_pairs[job['pair_key']] = previous
                continue
            decision = appearance_fallback(a, b, profiles)
        else:
            decision = result['result']
        decision, issues = ((copy.deepcopy(decision), decision.get('reviews', []))
                            if simple else reconcile(previous, decision))
        conflicts.extend(issues)
        record = {'a': a, 'b': b, 'decision': decision, 'source_hash': job['source_hash'],
                  'conflicts': issues,
                  'proposed_decision': result.get('result') if issues else None,
                  'source_run': store.directory.name, 'chapter': store.manifest['source']['chapter'],
                  'previous_version': (previous or {}).get('version'), 'inference_status': result['status']}
        record['version'] = fingerprint(record)
        current_pairs[job['pair_key']] = record
        if result['status'] == 'completed':
            updates[job['pair_key']] = record
    operation = fingerprint([store.directory.name, frozen, updates])
    with exclusive_lock(path.with_suffix('.lock')):
        graph = read_json(path) if path.exists() else copy.deepcopy(frozen['prior'])
        if operation not in graph.get('operations', []):
            if fingerprint(graph) != fingerprint(frozen['prior']):
                raise ValueError('Relationship versions changed concurrently; create a fresh run')
            graph['identities'].update(identities)
            for key, record in sorted(updates.items()):
                graph['pairs'].setdefault(key, []).append(record)
            graph['operations'].append(operation)
            atomic_json(path.parent / 'history-v2' / (operation + '.json'), {'before': frozen['prior'], 'updates': updates})
            atomic_json(path, graph)
    # Include historical pairs even without a new current-chapter interaction.
    selected_pairs = {key: versions[-1] for key, versions in frozen['prior']['pairs'].items() if versions}
    selected_pairs.update(current_pairs)
    selected_pairs = dict(sorted(selected_pairs.items()))
    address = [copy.deepcopy(edge) | {'pair_version': record['version']} for record in selected_pairs.values()
               for edge in record['decision']['address_edges']]
    social = [copy.deepcopy(rel) | {'pair_version': record['version']} for record in selected_pairs.values()
              for rel in record['decision']['relations']]
    address.sort(key=lambda e: (e['from_id'], e['to_id']))
    social.sort(key=lambda r: (r['from_id'], r['to_id'], r['category'], r['relationship']))
    result = {'policy': GRAPH_POLICY, 'identities': {**frozen['prior']['identities'], **identities},
              'pairs': selected_pairs, 'address_table': address, 'social_relations': social,
              'profile_snapshot_hash': linked['profile_snapshot_hash'], 'inference_failures': failures,
              'needs_review': conflicts, 'crop_issues': frozen['crop_issues'],
              'reference_crops': frozen['reference_crops'], 'failures': []}
    result['snapshot_hash'] = fingerprint(result)
    atomic_json(store.directory / 'snapshots/relationships-v2.json', result)
    return result
