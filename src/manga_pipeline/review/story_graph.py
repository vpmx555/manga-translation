"""Rebuild an offline graph review from saved artifacts; never creates a provider."""
import argparse
import copy
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
from types import SimpleNamespace


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024*1024), b''):
            h.update(chunk)
    return h.hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def build_review(artifact, output, *, source_folder=None, max_image_side=1400, stage_links=None):
    artifact, output = Path(artifact).resolve(), Path(output).resolve()
    if not 256 <= max_image_side <= 4096:
        raise ValueError('max-image-side must be 256..4096')
    payload = read(artifact)
    effective = payload.get('analysis', payload)
    graph = effective.get('story_graph')
    if not isinstance(graph, dict) or not isinstance(graph.get('scenes'), list):
        raise ValueError('Artifact has no chapter story_graph hierarchy')
    run = artifact.parent.parent if re.match(r'^\d\d_', artifact.parent.name) else artifact.parent
    manifest_file = run / 'manifest.json'
    manifest = read(manifest_file) if manifest_file.exists() else {}
    used = [{'file': str(artifact), 'sha256': digest(artifact)}]

    def load(stage):
        files = list(run.glob('[0-9][0-9]_'+stage+'/result.json'))
        if not files:
            return {}
        if len(files) != 1:
            raise ValueError('Ambiguous stage: '+stage)
        path = files[0]
        actual = digest(path)
        expected = manifest.get('steps', {}).get(stage, {}).get('artifact_hash')
        if expected and actual != expected:
            raise ValueError('Replica checksum mismatch: '+stage)
        used.append({'file': str(path), 'sha256': actual})
        return read(path)

    if manifest:
        stage = artifact.parent.name.split('_', 1)[-1]
        expected = manifest.get('steps', {}).get(stage, {}).get('artifact_hash')
        if expected and expected != used[0]['sha256']:
            raise ValueError('Graph artifact checksum mismatch')
    normalized = load('normalize')
    rows = {r['id']: copy.deepcopy(r) for r in normalized.get('utterances', [])}
    for row in effective.get('name_normalization', {}).get('canonical_rows', []):
        target = rows.setdefault(row['id'], {'id': row['id']})
        if target.get('text') != row['text']:
            target['text_before_graph'] = target.get('text')
        target['text'] = row['text']
    warnings, event_count, target_count, maximum, seen = [], 0, 0, 0, set()
    for scene in graph['scenes']:
        if len(scene['title'].split()) > 4:
            warnings.append('Scene title exceeds four words: '+scene['scene_id'])
        for event in scene['events']:
            event_count += 1
            ids = event['target_ids']
            target_count += len(ids)
            maximum = max(maximum, len(ids))
            if not 1 <= len(ids) <= 8:
                warnings.append('Event target count outside 1..8: '+event['event_id'])
            if seen.intersection(ids):
                warnings.append('Repeated targets: '+event['event_id'])
            seen.update(ids)
            if len(event['title'].split()) > 4:
                warnings.append('Event title exceeds four words: '+event['event_id'])
    if rows and seen != set(rows):
        warnings.append('Hierarchy does not cover exactly the available normalized/current OCR rows')
    output.mkdir(parents=True, exist_ok=True)
    pictures = {}
    if source_folder:
        source_folder = Path(source_folder).resolve()
        geometry = load('bubble_geometry') or load('extract')
        if not geometry.get('pages') or not all(rows.get(uid, {}).get('bbox') for uid in seen):
            warnings.append('No geometry/normalized boxes available; images were not generated')
        else:
            from ..v6.event_visuals import render
            originals = manifest.get('source', {}).get('images', [])
            sources = []
            for index, page in enumerate(geometry['pages']):
                receipt = originals[index] if index < len(originals) else {'path': page['image_path']}
                name = PurePosixPath(str(receipt['path']).replace('\\', '/')).name
                path = source_folder / name
                if not path.is_file():
                    raise ValueError('Source page missing: '+str(path))
                actual = digest(path)
                if receipt.get('sha256') and actual != receipt['sha256']:
                    raise ValueError('Source page checksum mismatch: '+name)
                sources.append({'path': str(path), 'sha256': actual})
            virtual = SimpleNamespace(directory=output, manifest={
                'source': {'images': sources}, 'config': {'v6': {'event_image_max_side': max_image_side}}},
                artifact_path=lambda _: output / 'evidence' / 'result.json')
            for scene in graph['scenes']:
                for event in scene['events']:
                    targets = [rows[uid] for uid in event['target_ids']]
                    pictures[event['event_id']] = render(virtual, event, targets, list(rows.values()), geometry)
            used.extend({'file': s['path'], 'sha256': s['sha256']} for s in sources)
    scenes = copy.deepcopy(graph['scenes'])
    decisions = {r['utterance_id']: r for r in effective.get('utterances', [])}
    changes = {p['utterance_id']: p for p in payload.get('accepted', []) if 'utterance_id' in p}
    # Retain saved evidence for offline analyze/reanalyze reviews without re-rendering sources.
    for scene in scenes:
        for event in scene['events']:
            if event['event_id'] in pictures:
                continue
            retained = []
            for packet in event.get('image_packets', []):
                original = (run / packet['file']).resolve()
                if not original.is_relative_to(run) or not original.is_file():
                    warnings.append('Retained event image unavailable: '+event['event_id'])
                    continue
                checksum = digest(original)
                if packet.get('retained_sha256') and checksum != packet['retained_sha256']:
                    raise ValueError('Retained event image checksum mismatch: '+event['event_id'])
                destination = output / 'evidence' / 'images' / (checksum+original.suffix.lower())
                destination.parent.mkdir(parents=True, exist_ok=True)
                if destination.exists() and digest(destination) != checksum:
                    raise ValueError('Copied evidence image checksum mismatch: '+event['event_id'])
                if not destination.exists():
                    shutil.copyfile(original, destination)
                retained.append({**packet, 'file': destination.relative_to(output).as_posix()})
                used.append({'file': str(original), 'sha256': checksum})
            if retained:
                pictures[event['event_id']] = retained
    for scene in scenes:
        for event in scene['events']:
            for uid in event['target_ids']:
                row = rows.setdefault(uid, {'id': uid, 'text': ''})
                decision = decisions.get(uid, {})
                row['semantic'] = {k: copy.deepcopy(decision[k]) for k in (
                    'content_type', 'speech_style', 'speaker_kind', 'speaker_person_id', 'addressee_type',
                    'addressee_person_ids', 'confidence', 'mentions', 'evidence', 'speaker_anchor', 'bubble_tail',
                    'semantic_resolution', 'semantic_needs_review', 'semantic_errors') if k in decision}
                if uid in changes:
                    patch = changes[uid]
                    before, after = patch.get('before', {}), patch.get('after', {})
                    row['change'] = {'reason': patch.get('reason'), 'fields': {
                        k: {'before': before.get(k), 'after': after.get(k)} for k in sorted(set(before) | set(after))
                        if before.get(k) != after.get(k)}}
                if row.get('page') is None:
                    match = re.search(r':p(\d+):', uid)
                    row['page'] = int(match.group(1)) if match else None
    stage = artifact.parent.name.split('_', 1)[-1] if re.match(r'^\d\d_', artifact.parent.name) else 'graph'
    data = {'policy': payload.get('policy'), 'chapter': graph.get('chapter', {}), 'scenes': scenes,
        'stage': stage, 'status': payload.get('status', payload.get('review_status', 'completed')),
        'fallback': payload.get('fallback_to_analyze', False), 'error': payload.get('error'),
        'stage_links': stage_links or {}, 'export_path': 'review-data.json',
        'accepted_count': len(payload.get('accepted', [])), 'rejected': payload.get('rejected', []),
        'issues': {k: payload[k] for k in ('batch_failures',) if payload.get(k)},
        'characters': graph.get('characters', {}), 'links': graph.get('links', []), 'utterances': rows,
        'groups': effective.get('name_normalization', {}).get('groups', []), 'pictures': pictures,
        'warnings': warnings, 'stats': {'scenes': len(scenes), 'events': event_count,
            'utterances': target_count, 'maximum_event_size': maximum,
            'groups': len(effective.get('name_normalization', {}).get('groups', []))}}
    template = Path(__file__).with_name('story_graph.html')
    serialized = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    rendered = template.read_text(encoding='utf-8').replace('__GRAPH_DATA__', serialized.replace('<', '\\u003c'))
    (output / 'index.html').write_text(rendered, encoding='utf-8')
    (output / 'review-data.json').write_text(json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2)+'\n', encoding='utf-8')
    report = {'artifact': str(artifact), 'inputs': used, 'stats': data['stats'], 'warnings': warnings,
        'renderer_sha256': digest(__file__), 'template_sha256': digest(template), 'model_requests': 0,
        'review': str(output / 'index.html')}
    (output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2)+'\n', encoding='utf-8')
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument('--run-dir', type=Path)
    target.add_argument('--artifact', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--source-folder', type=Path)
    parser.add_argument('--max-image-side', type=int, default=1400)
    args = parser.parse_args(argv)
    artifact = args.artifact
    if args.run_dir:
        paths = list(args.run_dir.glob('[0-9][0-9]_graph/result.json'))
        if len(paths) != 1:
            parser.error('run-dir must contain exactly one graph/result.json')
        artifact = paths[0]
    report = build_review(artifact, args.output, source_folder=args.source_folder, max_image_side=args.max_image_side)
    print(json.dumps({k: report[k] for k in ('review', 'stats', 'warnings', 'model_requests')}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
