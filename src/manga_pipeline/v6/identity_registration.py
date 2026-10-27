"""Deterministic identity registration utilities; no model name binding."""
from collections import defaultdict
from . import persons

def chapter_counts(raw):
    observations = defaultdict(set)
    for page in raw.get('pages', []):
        for index, mid in enumerate(page.get('character_ids', [])):
            boxes = page.get('characters', [])
            if mid is not None and index < len(boxes):
                observations[str(mid)].add((page['id'], tuple(boxes[index])))
    return {mid: len(values) for mid, values in observations.items()}



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
