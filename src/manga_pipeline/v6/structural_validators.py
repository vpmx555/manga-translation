"""Structural adapters for the simplified name/profile/relationship contract.

Supplied references describe request inputs, never evidence cited by the model.
Semantic decisions belong to the model; unusable fields are isolated for review.
"""
import copy

POLICY = 'chapter-name-binding-simple-v2'


def enabled(config):
    return config.get('identity_name_binding_policy') in {POLICY, 'analyze-authoritative-v3'}


def sources(request):
    refs = {r['id'] for segment in request.get('evidence_segments', []) for r in segment.get('rows', [])}
    refs.update(c['crop_id'] for c in request.get('crops', []))
    refs.update(p['ref'] for p in request.get('profile_refs', []))
    if isinstance(request.get('previous'), dict) and isinstance(request['previous'].get('version'), str):
        refs.add('previous:'+request['previous']['version'])
    return sorted(refs)


def references(item, request):
    supplied = sources(request)
    cited = item.get('evidence_refs', [])
    cited = [r for r in cited if isinstance(r, str) and r in supplied] if isinstance(cited, list) else []
    return {'evidence_refs': cited, 'model_evidence_refs': cited, 'supplied_evidence_refs': supplied}


def text(item, key, warnings, location):
    value = item.get(key)
    if isinstance(value, str) and value.strip():
        return value
    warnings.append({'reason': 'invalid_field', 'location': location, 'field': key, 'value': value})
    return 'unknown'


def profile(value, request):
    from .identity_profiles import FIELDS
    pid = request['identity']['person_id']
    if not isinstance(value, dict) or value.get('person_id') != pid:
        raise ValueError('Unknown profile identity')
    attributes = value.get('attributes')
    if not isinstance(attributes, dict):
        raise ValueError('Profile has no usable attributes object')
    warnings, result = [], {}
    for field in FIELDS:
        item = attributes.get(field)
        if not isinstance(item, dict):
            warnings.append({'reason': 'invalid_attribute', 'field': field, 'value': item})
            item = {}
        result[field] = {key: text(item, key, warnings, field) for key in ('value', 'status', 'basis')}
        result[field].update(references(item, request))
    return {'person_id': pid, 'attributes': result, 'reviews': warnings}


def relationship(value, request):
    if not isinstance(value, dict):
        raise ValueError('Relationship response must be an object')
    ids = list(request['identities'])
    if len(ids) != 2 or len(set(ids)) != 2:
        raise ValueError('Relationship request must contain two identities')
    warnings, edges, relations = [], {}, []
    def items(field):
        data = value.get(field)
        if isinstance(data, list):
            return data
        warnings.append({'reason': 'invalid_array', 'field': field})
        return []
    for field in ('address_edges', 'relations'):
        for index, item in enumerate(items(field)):
            if (not isinstance(item, dict) or not isinstance(item.get('from_id'), str)
                    or not isinstance(item.get('to_id'), str)
                    or {item['from_id'], item['to_id']} != set(ids)):
                warnings.append({'reason': 'invalid_edge_ids', 'field': field, 'index': index, 'value': item})
                continue
            keys = ('self', 'address', 'status', 'basis', 'reason') if field == 'address_edges' else (
                'category', 'relationship', 'role_from', 'role_to', 'status', 'reason')
            record = {'from_id': item['from_id'], 'to_id': item['to_id'],
                      **{k: text(item, k, warnings, f'{field}:{index}') for k in keys},
                      **references(item, request)}
            if field == 'relations':
                symmetric = item.get('symmetric')
                if type(symmetric) is not bool:
                    warnings.append({'reason': 'invalid_symmetric', 'index': index, 'value': symmetric})
                    symmetric = False
                record['symmetric'] = symmetric
                relations.append(record)
            else:
                direction = (record['from_id'], record['to_id'])
                if direction in edges:
                    warnings.append({'reason': 'edge_superseded', 'previous': copy.deepcopy(edges[direction])})
                edges[direction] = record
    for a, b in (ids, ids[::-1]):
        if (a, b) not in edges:
            warnings.append({'reason': 'missing_direction', 'from_id': a, 'to_id': b})
            edges[a, b] = {'from_id': a, 'to_id': b,
                **{k: 'unknown' for k in ('self', 'address', 'status', 'basis', 'reason')},
                **references({}, request)}
    return {'address_edges': list(edges.values()), 'relations': relations, 'reviews': warnings}


def validate_profile(value, request):
    if value.get('person_id') != request['identity']['person_id'] or not isinstance(value.get('attributes'), dict):
        raise ValueError('Malformed normalized profile')


def validate_relationship(value, request):
    ids = set(request['identities'])
    if (not isinstance(value.get('address_edges'), list) or not isinstance(value.get('relations'), list)
            or any({e['from_id'], e['to_id']} != ids for e in [*value['address_edges'], *value['relations']])):
        raise ValueError('Malformed normalized relationship')


def schema_without_proofs(value):
    """Keep guided JSON shape while allowing free text and optional citations."""
    value = copy.deepcopy(value)
    def walk(node):
        if isinstance(node, dict):
            node.pop('enum', None)
            if 'required' in node:
                node['required'] = [k for k in node['required'] if k != 'evidence_refs']
            for child in node.values():
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)
    walk(value)
    return value
