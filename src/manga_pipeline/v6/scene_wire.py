"""Lossless compact references for the joint chapter scan, never OCR rewriting."""
import copy
from ..storage.io import fingerprint

POLICY = 'scene-name-wire-v2'


def mapping(rows, registry):
    ids = [r['id'] for r in rows]
    people = [r['person_id'] for r in registry]
    if any(not isinstance(x, str) or not x for x in [*ids, *people]) or set(ids) & set(people):
        raise ValueError('Invalid or colliding scene/registry source IDs')
    if len(ids) != len(set(ids)) or len(people) != len(set(people)):
        raise ValueError('Duplicate scene/registry source IDs')
    return {'policy': POLICY, 'source_hash': fingerprint(rows),
            'rows': {f'u{i}': uid for i, uid in enumerate(ids)},
            'persons': {f'p{i}': pid for i, pid in enumerate(people)}}


def encode(request, schema, rows):
    registry = request.get('name_registry', [])
    ledger = mapping(rows, registry)
    forward = {v: k for group in ('rows', 'persons') for k, v in ledger[group].items()}
    essential = {r['id'] for r in request['targets']}
    value = {'wire_contract': POLICY, 'source_hash': ledger['source_hash'],
             'targets': [{'id': forward[r['id']], 'text': r['text'],
                          'scene_target': r['id'] in essential} for r in rows]}
    if 'name_registry' in request:
        value['name_registry'] = [{**r, 'person_id': forward[r['person_id']]} for r in registry]
    def compact(node, field=None):
        if isinstance(node, list):
            return [compact(x, field) for x in node]
        if not isinstance(node, dict):
            return node
        if field in {'utterance_id', 'start_id'} and node.get('type') == 'string':
            return {'type': 'string', 'pattern': '^u[0-9]+$'}
        return {k: compact(v, k) for k, v in node.items()}
    return value, compact(schema), ledger


def decode(value, ledger):
    """Translate reference fields only; never touch text, names or offsets."""
    if not isinstance(value, dict):
        raise ValueError('Invalid compact scene response')
    def reference(ref, *, person=False):
        allowed = {**ledger['rows'], **(ledger['persons'] if person else {})}
        if not isinstance(ref, str) or ref not in allowed:
            raise ValueError('Unknown compact source reference: ' + repr(ref))
        return allowed[ref]
    def walk(node):
        if isinstance(node, list):
            return [walk(x) for x in node]
        if not isinstance(node, dict):
            return copy.deepcopy(node)
        result = {}
        for key, item in node.items():
            if key in {'start_id', 'utterance_id'}:
                result[key] = reference(item)
            elif key == 'evidence_refs':
                if not isinstance(item, list):
                    raise ValueError('Invalid compact evidence refs')
                result[key] = [reference(ref, person=True) for ref in item]
            else:
                result[key] = walk(item)
        return result
    return walk(value)
