"""Single-attempt chapter scan with authoritative, sequential name replacements."""
import copy
import unicodedata

from ..storage.io import atomic_json, fingerprint
from .requests import Calls
from .semantic_contract import object_schema
from .name_normalization import registry_context
from .scene_wire import mapping

POLICY = 'canonical-scan-v3'
PROMPT = '''Read the chapter's CURRENT OCR. In ONE response propose scene starts,
person-name groups and names. Each name record has text_id, old_name, new_name,
group_id (or null). new_name is your corrected spelling and becomes current OCR;
it need not be an original substring. Use exact source spellings for old_name
when possible, without character offsets. Corrections replace ALL matches in
the chapter, ignoring case, whitespace and hyphens, IN YOUR RETURNED ORDER;
later corrections also see text created by earlier ones. Return each source
surface once per text. Only correct names, not whole sentences or pronouns.
Group surname, given name, full/reversed forms and OCR variants when context
identifies the same person. Use the most complete name as canonical_name.
Keep unrelated/uncertain people separate; no fixed number of groups. Names
without a group remain ungrouped for review. Groups are not MAGI identities.
Scenes use scene_target rows, follow conversation continuity, and start with
the first scene target. Summaries and reasons should be brief. Use supplied uN
IDs for texts and short group IDs you define. Do not return offsets, crop refs,
evidence refs or a rewritten document. JSON only.'''


def units(value):
    """Case/spacing/hyphen key with Unicode source-position provenance."""
    result, positions = [], []
    i = 0
    while i < len(value):
        end = i + 1
        while end < len(value) and unicodedata.combining(value[end]):
            end += 1
        for char in unicodedata.normalize('NFC', value[i:end]).casefold():
            if not char.isspace() and char not in '-–—':
                result.append(char)
                positions.append((i, end))
        i = end
    return ''.join(result), positions


def find_all(text, old):
    key, positions = units(text)
    target, _ = units(old)
    if not target:
        return []
    result, offset = [], 0
    while True:
        found = key.find(target, offset)
        if found < 0:
            return result
        span = (positions[found][0], positions[found + len(target)-1][1])
        if span not in result:
            result.append(span)
        offset = found + len(target)


def move_span(start, end, matches, replacement):
    def point(pos, right):
        delta = 0
        for a, b in matches:
            inside = (a < pos <= b) if right else (a <= pos < b)
            if inside:
                return a + delta + (len(replacement) if right else 0)
            if b <= pos:
                delta += len(replacement) - (b-a)
        return pos + delta
    return point(start, False), point(end, True)


def schema():
    string = {'type': 'string'}
    return object_schema({
        'scenes': {'type': 'array', 'items': object_schema({'start_id': string, 'summary': string})},
        'groups': {'type': 'array', 'items': object_schema({'group_id': string, 'canonical_name': string, 'reason': string})},
        'names': {'type': 'array', 'items': object_schema({'text_id': string, 'old_name': string,
            'new_name': string, 'group_id': {'type': ['string', 'null']}})}})


def parse(value, document, essential, ledger, state):
    if not isinstance(value, dict) or 'rejected_response' in value:
        raise ValueError('Scan response is not a usable JSON object')
    rows = document['utterances']
    original = {r['id']: r for r in rows}
    current = {uid: row['text'] for uid, row in original.items()}
    warnings, groups, records = [], {}, []
    def warn(kind, **detail):
        warnings.append({'reason': kind, **detail})
    def array(field):
        data = value.get(field, [])
        if not isinstance(data, list):
            warn('invalid_field_type', field=field)
            return []
        return data
    for index, group in enumerate(array('groups')):
        if not isinstance(group, dict) or not isinstance(group.get('group_id'), str) or not group['group_id']:
            warn('invalid_group', index=index)
            continue
        if not isinstance(group.get('canonical_name'), str) or not group['canonical_name'].strip():
            warn('invalid_group_name', index=index)
            continue
        gid = group['group_id']
        if gid in groups:
            warn('group_superseded', group_id=gid, previous=groups[gid])
        groups[gid] = {'group_id': gid, 'canonical_name': group['canonical_name'],
            'evidence': group.get('reason') if isinstance(group.get('reason'), str) else '',
            'evidence_refs': [], 'aliases': [], 'members': [], 'status': 'proposed'}
    anchors, rules, rule_keys = [], [], set()
    for index, item in enumerate(array('names')):
        if not isinstance(item, dict):
            warn('invalid_name_item', index=index)
            continue
        text_id = item.get('text_id')
        uid = ledger['rows'].get(text_id) if isinstance(text_id, str) else None
        old, new = item.get('old_name'), item.get('new_name')
        if uid not in original or not isinstance(old, str) or not old.strip() or not isinstance(new, str) or not new.strip():
            warn('invalid_name_data', index=index, item=item)
            continue
        gid = item.get('group_id')
        if not isinstance(gid, str) or gid not in groups:
            warn('ungrouped_name', index=index, group_id=gid)
            gid = None
        record = {'index': index, 'utterance_id': uid, 'old_name': old, 'new_name': new, 'group_id': gid}
        records.append(record)
        for occurrence, (a, b) in enumerate(find_all(original[uid]['text'], old)):
            anchors.append({'record': record, 'start': a, 'end': b, 'original_start': a, 'original_end': b,
                            'occurrence': occurrence})
        if not any(a['record'] is record for a in anchors):
            anchors.append({'record': record, 'start': None, 'end': None,
                            'original_start': None, 'original_end': None, 'occurrence': 0})
        key = (units(old)[0], new)
        if key not in rule_keys:
            rules.append({'old_name': old, 'new_name': new, 'source_index': index})
            rule_keys.add(key)
    history = []
    for number, rule in enumerate(rules):
        total = 0
        for uid, text in list(current.items()):
            matches = find_all(text, rule['old_name'])
            if not matches:
                continue
            after = text
            for a, b in reversed(matches):
                after = after[:a] + rule['new_name'] + after[b:]
            for anchor in anchors:
                if anchor['record']['utterance_id'] == uid and anchor['start'] is not None:
                    anchor['start'], anchor['end'] = move_span(anchor['start'], anchor['end'], matches, rule['new_name'])
            history.append({'rule_index': number, 'utterance_id': uid, 'original': rule['old_name'],
                'replacement': rule['new_name'], 'status': 'applied', 'matches': [list(m) for m in matches],
                'before': text, 'after': after, 'count': len(matches)})
            current[uid] = after
            total += len(matches)
        if not total:
            warn('no_match', rule_index=number, **rule)
    candidates = {}
    for anchor in anchors:
        record = anchor['record']
        uid, a, b = record['utterance_id'], anchor['start'], anchor['end']
        surface = current[uid][a:b] if a is not None else record['new_name']
        gid = record['group_id']
        cid = fingerprint([POLICY, uid, a, b, surface, record['index'], anchor['occurrence']])[:20]
        aliases = [record['old_name'], record['new_name'], surface]
        if gid:
            group = groups[gid]
            group['aliases'] = sorted(set([*group['aliases'], *aliases, group['canonical_name']]))
            group['evidence_refs'] = sorted(set([*group['evidence_refs'], uid]))
        item = {'id': cid, 'utterance_id': uid, 'name': surface, 'key': surface.casefold(),
            'raw_surface': original[uid]['text'][anchor['original_start']:anchor['original_end']]
                if anchor['original_start'] is not None else record['old_name'],
            'corrected_surface': surface, 'start': a, 'end': b,
            'original_start': anchor['original_start'], 'original_end': anchor['original_end'],
            'source': 'llm_scan_v3', 'candidate_person_ids': [], 'name_suspect': False,
            'group_id': gid, 'location_status': 'mapped' if a is not None else 'unmapped'}
        key = (uid, a, b, surface) if a is not None else (uid, record['old_name'], surface)
        if key in candidates:
            warn('name_assignment_superseded', previous=candidates[key]['id'], current=cid)
        candidates[key] = item
    for item in candidates.values():
        gid = item['group_id']
        if gid:
            groups[gid]['members'].append({'candidate_id': item['id'], 'utterance_id': item['utterance_id'],
                'start': item['original_start'], 'end': item['original_end']})
            item['alias_groups'] = [{k: groups[gid][k] for k in ('group_id', 'canonical_name', 'aliases', 'evidence', 'status')}]
        else:
            item['alias_groups'] = []
    essential_ids = [r['id'] for r in essential]
    markers = {}
    for entry in array('scenes'):
        if (not isinstance(entry, dict) or not isinstance(entry.get('start_id'), str)
                or ledger['rows'].get(entry['start_id']) not in essential_ids):
            warn('invalid_scene_marker', item=entry)
            continue
        uid = ledger['rows'][entry['start_id']]
        markers[essential_ids.index(uid)] = entry.get('summary') if isinstance(entry.get('summary'), str) else 'unknown'
    if essential_ids and 0 not in markers:
        markers[0] = 'unknown'
        warn('unresolved_scene_start', text_id=essential_ids[0])
    starts = sorted(markers)
    scenes = [{'scene_id': 'scene:'+str(i+1), 'summary': markers[start],
               'target_ids': essential_ids[start:starts[i+1] if i+1 < len(starts) else len(essential_ids)],
               'resolution': 'unresolved' if markers[start] == 'unknown' else 'model'} for i, start in enumerate(starts)]
    normalization = {'policy': POLICY, 'source_hash': fingerprint(rows),
        'canonical_rows': [{'id': r['id'], 'text': current[r['id']]} for r in rows],
        'canonical_hash': fingerprint([{'id': r['id'], 'text': current[r['id']]} for r in rows]),
        'groups': list(groups.values()), 'patches': history, 'replacement_rules': rules,
        'rejected': [], 'repairs': [], 'suspicions': [], 'warnings': warnings,
        'grouping_coverage': {'detected': len(candidates), 'grouped': sum(bool(c['group_id']) for c in candidates.values()),
            'ungrouped': [c['id'] for c in candidates.values() if not c['group_id']]}}
    normalization['grouping_coverage']['complete'] = not normalization['grouping_coverage']['ungrouped']
    return {'policy': POLICY, 'candidates': list(candidates.values()), 'scenes': scenes,
        'scene_source_hash': fingerprint(document), 'scene_wire': ledger,
        'name_normalization': normalization, 'failures': [], 'needs_review': warnings,
        'review_status': 'completed_with_review' if warnings else 'completed', 'automatic_retries': 0}


def build(store, document, raw, provider):
    from .semantic import snapshot
    frozen = snapshot(store, raw)
    rows = document['utterances']
    essential = [r for r in rows if r.get('is_essential_text') is True]
    registry = registry_context(frozen['state'])
    ledger = mapping(rows, registry)
    forward = {v: k for k, v in ledger['rows'].items()}
    request = {'wire_contract': POLICY, 'targets': [{'id': forward[r['id']], 'text': r['text'],
        'scene_target': r.get('is_essential_text') is True} for r in rows], 'name_registry': registry}
    directory = store.artifact_path('scan').parent
    atomic_json(directory / 'scene-wire.json', ledger)
    if rows:
        value = Calls(store, provider, 'scan').call('scan:canonical-v3', 'analyze_scene_v6', PROMPT,
            request, schema(), reserve=store.manifest['config']['v6'].get('scan_output_tokens', 8192), oom_retry=False)
    else:
        value = {'scenes': [], 'groups': [], 'names': []}
    result = parse(value, document, essential, ledger, frozen['state'])
    result['person_snapshot_hash'] = fingerprint(frozen)
    atomic_json(directory / 'ocr-current.json', {'policy': POLICY, 'rows': result['name_normalization']['canonical_rows']})
    atomic_json(directory / 'replacement-history.json', result['name_normalization']['patches'])
    return result
