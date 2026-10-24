"""Chapter-wide name occurrences and retained, labelled panel/bank evidence."""
import copy
import hashlib
import io
import math
from collections import defaultdict

from PIL import Image, ImageDraw, ImageFont

from ..extraction.document import panel_for_box
from ..storage.io import atomic_bytes, file_hash, fingerprint
from . import persons
from .identity_runtime import EvidenceIntegrityError, retain_crops
from .identity_v2 import membership, confirmed


class BindingStore:
    """Reuse durable v2 requests in an isolated stage subdirectory."""
    def __init__(self, store):
        self.store = store

    def __getattr__(self, name):
        return getattr(self.store, name)

    def artifact_path(self, stage):
        if stage == 'build_identity':
            return self.store.artifact_path(stage).parent / 'name-binding' / 'result.json'
        return self.store.artifact_path(stage)


def bank_people(state, metadata):
    members, _ = membership(state)
    by_mid = defaultdict(set)
    for pid, mids in members.items():
        for mid in mids:
            record = metadata.get('characters', {}).get(mid)
            if record is not None and not record.get('disabled'):
                by_mid[mid].add(pid)
    # A bank ID with conflicting semantic ownership is not a new-name candidate.
    return {mid: next(iter(pids)) for mid, pids in by_mid.items() if len(pids) == 1}


def panel_indices(row, page):
    panels = page.get('panels', [])
    indices = [row.get('panel_index'), *row.get('panel_indices', [])]
    indices.extend(b.get('panel_index') for b in row.get('source_boxes', []))
    selected = {i for i in indices if type(i) is int and 0 <= i < len(panels)}
    if not selected and row.get('bbox'):
        index = panel_for_box(row['bbox'], panels)
        if index is not None:
            selected.add(index)
    return sorted(selected)


def panel_characters(page, index):
    ids = page.get('character_ids', [None] * len(page.get('characters', [])))
    return [(i, box, str(ids[i]) if i < len(ids) and ids[i] is not None else None)
            for i, box in enumerate(page.get('characters', []))
            if panel_for_box(box, page.get('panels', [])) == index]


def retain_panel(store, page, index, metadata):
    """Retain exactly the labelled pixels sent to the model, with source mapping."""
    source = page['image_path']
    try:
        source_hash = file_hash(source)
        with Image.open(source) as opened:
            image = opened.convert('RGB')
    except (OSError, ValueError) as exc:
        raise EvidenceIntegrityError('Missing/corrupt name-binding source panel') from exc
    box = page['panels'][index]
    if len(box) != 4 or any(not math.isfinite(float(v)) for v in box):
        raise EvidenceIntegrityError('Invalid source panel geometry')
    left, top = max(0, math.floor(box[0])-8), max(0, math.floor(box[1])-8)
    right, bottom = min(image.width, math.ceil(box[2])+8), min(image.height, math.ceil(box[3])+8)
    if right <= left or bottom <= top:
        raise EvidenceIntegrityError('Empty source panel')
    image = image.crop((left, top, right, bottom))
    annotations = []
    draw, font = ImageDraw.Draw(image), ImageFont.load_default(size=20)
    for detection, bounds, mid in panel_characters(page, index):
        if mid is None or mid not in metadata.get('characters', {}):
            continue
        clipped = (max(0, bounds[0]-left), max(0, bounds[1]-top),
                   min(image.width-1, bounds[2]-left), min(image.height-1, bounds[3]-top))
        if clipped[2] <= clipped[0] or clipped[3] <= clipped[1]:
            continue
        draw.rectangle(clipped, outline='red', width=3)
        label = 'MAGI ' + mid
        x, y = clipped[:2]
        label_box = draw.textbbox((x, y), label, font=font)
        draw.rectangle(label_box, fill='white')
        draw.text((x, y), label, fill='red', font=font)
        annotations.append({'magi_id': mid, 'character_index': detection, 'bbox': list(bounds)})
    maximum = min(768, store.manifest['config']['llm']['image_max_side'])
    image.thumbnail((maximum, maximum), Image.Resampling.LANCZOS)
    binary = io.BytesIO()
    image.save(binary, format='PNG')
    data = binary.getvalue()
    digest = hashlib.sha256(data).hexdigest()
    relative = store.artifact_path('build_identity').parent.relative_to(store.directory) / 'reference-crops' / (digest+'.png')
    destination = store.directory / relative
    if destination.exists() and destination.read_bytes() != data:
        raise EvidenceIntegrityError('Frozen panel image changed')
    atomic_bytes(destination, data)
    return {'kind': 'panel', 'crop_id': 'panel:'+fingerprint([page['id'], index, digest])[:24],
            'file': relative.as_posix(), 'retained_sha256': digest, 'sha256': source_hash,
            'source_file': str(source), 'path': str(source), 'page_id': page['id'],
            'panel_index': index, 'bbox': [left, top, right, bottom], 'annotations': annotations,
            'magi_id': 'panel', 'distance': 0}


def collect(store, analyzed, scanned, document, raw, metadata):
    """Collect grouped current names; retain strict evidence gates for v1 only."""
    state = analyzed['person_state']
    relaxed = store.manifest['config'].get('v6', {}).get('identity_name_binding_policy') == 'chapter-name-binding-simple-v2'
    people = bank_people(state, metadata)
    pid_mids = defaultdict(list)
    for mid, pid in people.items():
        pid_mids[pid].append(mid)
    pages = {p['id']: p for p in raw.get('pages', [])}
    source = sorted(document.get('utterances', []), key=lambda r: r.get('reading_order', 0))
    rows = {r['id']: r for r in source}
    semantic = {r['utterance_id']: r for r in analyzed.get('utterances', [])}
    mentions = {(r['utterance_id'], m.get('candidate_id')): m
                for r in analyzed.get('utterances', []) for m in r.get('mentions', [])}
    normalization = scanned.get('name_normalization') or {}
    groups, panels, bank_crops, crop_issues = {}, {}, {}, []
    position = {r['id']: i for i, r in enumerate(source)}
    for candidate in scanned.get('candidates', []):
        uid = candidate['utterance_id']
        row, item = rows.get(uid), semantic.get(uid, {})
        if row is None:
            raise EvidenceIntegrityError('Name occurrence source utterance is missing')
        start, end = candidate['start'], candidate['end']
        if not relaxed and (type(start) is not int or type(end) is not int or not 0 <= start < end <= len(row['text'])
                or row['text'][start:end].casefold() != candidate.get('corrected_surface', candidate.get('raw_surface', candidate['name'])).casefold()):
            raise EvidenceIntegrityError('Name occurrence span differs from frozen source')
        try:
            form = persons.name_form(candidate['name'])
        except (ValueError, TypeError):
            if not relaxed:
                raise
            crop_issues.append({'candidate_id': candidate['id'], 'reason': 'unusable_name_field'})
            continue
        aliases = copy.deepcopy(candidate.get('alias_groups', []))
        if not aliases and not relaxed:
            aliases = [g for g in normalization.get('groups', []) if any(
                m['utterance_id'] == uid and m['start'] == candidate.get('original_start', start)
                and m['end'] == candidate.get('original_end', end) for m in g.get('members', []))]
        if not aliases:
            if relaxed:
                continue  # Ungrouped names stay in scan review; no semantic fallback groups.
            aliases = [{'canonical_name': form['base_name'], 'aliases': [form['surface']], 'status': 'proposed'}]
        for alias in aliases:
            names = []
            for name in [alias['canonical_name'], *alias.get('aliases', []), form['surface']]:
                try:
                    names.append(persons.name_form(name)['surface'])
                except (ValueError, TypeError):
                    if not relaxed:
                        raise
                    crop_issues.append({'candidate_id': candidate['id'], 'reason': 'unusable_alias_field', 'value': name})
            names = sorted(set(names))
            try:
                canonical = persons.name_form(alias['canonical_name'])['base_name']
            except (ValueError, TypeError):
                canonical = form['base_name']
            gid = 'name-group:'+fingerprint([store.manifest['source']['story'],
                alias.get('group_id') or form['base_name'], canonical])[:24]
            group = groups.setdefault(gid, {'group_id': gid, 'canonical_name': alias['canonical_name'],
                'aliases': [], 'occurrences': [], 'suspect': False})
            group['aliases'] = sorted(set(group['aliases']) | set(names))
            group['suspect'] |= not relaxed and (alias.get('status') == 'suspect' or len(aliases) > 1)
            mention = mentions.get((uid, candidate['id']), {})
            page = pages.get(row.get('page_id'))
            indices = panel_indices(row, page) if page else []
            visible, panel_refs = set(), []
            for index in indices:
                characters = panel_characters(page, index)
                visible.update(mid for _, _, mid in characters if mid in people)
                key = (page['id'], index)
                if key not in panels:
                    try:
                        panels[key] = retain_panel(store, page, index, metadata)
                    except EvidenceIntegrityError:
                        if not relaxed:
                            raise
                        crop_issues.append({'page_id': page['id'], 'panel_index': index, 'reason': 'optional_panel_unavailable'})
                        continue
                panel_refs.append(panels[key])
            matching_people = {persons.canonical(state, pid) for name in names for key in
                (persons.name_form(name)['surface'], persons.name_form(name)['base_name'])
                for pid in state.get('name_index', {}).get(key, [])}
            known = {mid for mid, pid in people.items() if pid in matching_people and confirmed(state, pid)}
            listener = None
            targets = {persons.canonical(state, pid) for pid in item.get('addressee_person_ids', [])}
            speaker = persons.canonical(state, item.get('speaker_person_id'))
            speaker_mids = set(pid_mids.get(speaker, []))
            # The exception is for a genuinely off-panel individual, not an
            # unresolved group or a third-person reference.
            if (mention.get('mention_type') == 'direct_address' and item.get('semantic_resolution') == 'resolved'
                    and item.get('addressee_type') == 'individual' and len(targets) == 1
                    and visible and visible.issubset(speaker_mids)):
                target = next(iter(targets))
                options = pid_mids.get(target, [])
                if target != speaker and options:
                    listener = min(options, key=lambda m: (-metadata['characters'][m].get('appearance_count', 0), m))
            allowed = sorted(visible | known | ({listener} if listener else set()))
            if relaxed:
                allowed = sorted(people)
            for mid in allowed:
                if mid not in bank_crops:
                    crops, issues = retain_crops(store, 'build_identity', mid, 1)
                    bank_crops[mid] = [{**c, 'kind': 'bank'} for c in crops]
                    crop_issues.extend({'magi_id': mid, **e} for e in issues)
            reason = None
            if relaxed:
                reason = None if allowed else 'no_candidate_with_magi_bank_id'
            elif mention.get('mention_type') not in {'direct_address', 'introduction', 'reference'}:
                reason = 'missing_or_unknown_mention_type'
            elif item.get('content_type') not in {'dialogue', 'narration', 'thought'}:
                reason = 'ineligible_content_type'
            elif not allowed:
                reason = 'no_candidate_with_magi_bank_id'
            i = position[uid]
            context = [{'id': r['id'], 'text': r['text']} for r in source[max(0, i-1):i+2]]
            group['occurrences'].append({'id': candidate['id'], 'utterance_id': uid,
                'surface': candidate.get('corrected_surface', candidate['name']) if relaxed else row['text'][start:end],
                'name': form['surface'], 'start': start, 'end': end,
                'original_start': candidate.get('original_start', start),
                'original_end': candidate.get('original_end', end), 'text': row['text'],
                'mention_type': mention.get('mention_type', 'unknown'), 'content_type': item.get('content_type'),
                'speaker_kind': item.get('speaker_kind'), 'analysis_resolution': item.get('semantic_resolution'),
                'analysis_speaker_id': speaker, 'analysis_addressee_ids': sorted(targets),
                'analysis_addressee_type': item.get('addressee_type'),
                'visible_magi_ids': sorted(visible), 'allowed_magi_ids': allowed,
                'off_panel_magi_id': listener, 'panel_refs': [c['crop_id'] for c in panel_refs],
                'context': context, 'name_suspect': candidate.get('name_suspect', False), 'skip_reason': reason})
    return {'groups': sorted(groups.values(), key=lambda g: g['group_id']), 'people': people,
            'panel_crops': list(panels.values()), 'bank_crops': bank_crops, 'crop_issues': crop_issues}
