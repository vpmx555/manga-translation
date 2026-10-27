"""Lossless compact references for the chapter graph, never OCR rewriting."""
import copy
from ..storage.io import fingerprint

POLICY = 'chapter-reference-wire-v7'


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
