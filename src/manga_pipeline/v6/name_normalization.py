"""Current OCR snapshot and compact prior-name context for graph stages."""
import copy
from ..storage.io import fingerprint

def registry_context(state):
    return [{"person_id": pid, "primary_name": person.get("primary_name"),
             "aliases": [f["surface"] for f in person.get("name_forms", [])]}
            for pid, person in sorted(state.get("persons", {}).items())
            if person.get("primary_name") or person.get("name_forms")]



def apply(document, normalization):
    result = copy.deepcopy(document)
    rows = result["utterances"]
    if normalization.get('policy') == 'graph-name-corrections-v7':
        values = normalization['canonical_rows']
        if fingerprint(values) != normalization['canonical_hash']:
            raise ValueError('Canonical OCR snapshot changed')
        current = [{'id': r['id'], 'text': r['text']} for r in rows]
        if normalization.get('source_hash') != fingerprint(rows) and fingerprint(current) != normalization['canonical_hash']:
            raise ValueError('Canonical OCR input version changed')
        canonical = {r['id']: r['text'] for r in values}
        if set(canonical) != {r['id'] for r in rows}:
            raise ValueError('Canonical OCR ID coverage changed')
        for row in rows:
            if row['text'] != canonical[row['id']]:
                row['ocr_raw_text'] = row.get('ocr_raw_text', row['text'])
            row['text'] = canonical[row['id']]
        result['canonical_ocr_version'] = normalization['canonical_hash']
        return result
    raise ValueError('Historical OCR normalization requires its frozen source')
