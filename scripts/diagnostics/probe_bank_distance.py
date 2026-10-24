"""Replay bank assignment on committed geometry, with cached MAGI crop embeddings."""
import argparse
import html
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--embedding-cache', type=Path, help='Reuse verified crop embeddings from an earlier diagnostic')
    parser.add_argument('--distances', type=float, nargs=2, default=[0.65, 0.4])
    parser.add_argument('--margin', type=float, default=0.02)
    parser.add_argument('--pending-max-page-gap', type=int, choices=[1, 2], default=1)
    parser.add_argument('--detection-input', type=Path, help='Completed fresh character-detection diagnostic')
    args = parser.parse_args()
    if any(not math.isfinite(v) or v < 0 for v in [*args.distances, args.margin]) or args.distances[0] == args.distances[1]:
        parser.error('Distances must be distinct finite nonnegative values; margin must be finite and nonnegative')
    names = [f'distance-{v:.2f}' for v in args.distances]
    if names[0] == names[1]:
        parser.error('Distances must differ at two decimal places')
    import numpy as np
    import torch
    from PIL import Image, ImageDraw
    from transformers import AutoModel
    from manga_pipeline.bank.identity import CharacterBank
    from manga_pipeline.bank.assignment import DynamicCharacterAssigner
    from manga_pipeline.extraction.magi import MODEL, REVISION, MODEL_FINGERPRINT
    from manga_pipeline.storage.io import file_hash, fingerprint

    manifest = json.loads((args.run / 'manifest.json').read_text(encoding='utf-8-sig'))
    title = manifest['source'].get('story_title', manifest['source']['story']) + ' — ' + manifest['source']['chapter']
    artifacts = list(args.run.glob('[0-9][0-9]_extract/result.json'))
    if len(artifacts) != 1:
        raise ValueError('Expected one committed extraction artifact')
    artifact = artifacts[0]
    if file_hash(artifact) != manifest['steps']['extract']['artifact_hash']:
        raise ValueError('Extract checksum mismatch')
    raw = json.loads(artifact.read_text(encoding='utf-8-sig'))
    if args.detection_input:
        detection = json.loads(args.detection_input.read_text(encoding='utf-8'))
        if (detection.get('status') != 'completed' or detection['source_run'] != str(args.run.resolve())
                or detection['source_extract_hash'] != manifest['steps']['extract']['artifact_hash']
                or detection['source_layout_hash'] != manifest['steps']['layout']['artifact_hash']):
            raise ValueError('Fresh detection provenance mismatch or incomplete')
        raw = detection['raw']
    if raw['model_fingerprint'] != MODEL_FINGERPRINT:
        raise ValueError('Embedding model differs from committed extraction')
    images, paths, keys = [], [], []
    for saved in manifest['source']['images']:
        path = args.source / saved['path'].replace('\\', '/').rsplit('/', 1)[-1]
        if file_hash(path) != saved['sha256']:
            raise ValueError('Source checksum mismatch: ' + str(path))
        with Image.open(path) as image:
            images.append(np.asarray(image.convert('L').convert('RGB')))
        paths.append(path)
        keys.append(manifest['source']['chapter'] + '/' + path.name)
    pages = raw['pages']
    if len(pages) != len(images):
        raise ValueError('Geometry/source page count mismatch')
    args.output.mkdir(parents=True, exist_ok=True)
    cache = args.embedding_cache or args.output / 'embeddings'
    cache.mkdir(exist_ok=True)
    vectors, model = [], None
    torch.set_num_threads(4)
    for index, (image, page, saved) in enumerate(zip(images, pages, manifest['source']['images']), 1):
        contract = fingerprint([MODEL_FINGERPRINT, saved['sha256'], page['characters']])
        target = cache / f'{index:03d}-{contract}.npy'
        if target.exists():
            values = np.load(target, allow_pickle=False)
        elif not page['characters']:
            values = np.empty((0, 768), dtype=np.float32)
            np.save(target, values)
        else:
            if model is None:
                print('Loading cached MAGI crop encoder on CPU', flush=True)
                model = AutoModel.from_pretrained(MODEL, revision=REVISION,
                    trust_remote_code=True, local_files_only=True).eval().to('cpu')
            with torch.inference_mode():
                values = model.predict_crop_embeddings([image], [page['characters']])[0].detach().cpu().numpy()
            np.save(target, values)
        if len(values) != len(page['characters']) or not np.isfinite(values).all():
            raise ValueError('Invalid cached embeddings')
        vectors.append(torch.from_numpy(values))
        print(f'Embeddings {index}/{len(pages)}: {len(values)} characters', flush=True)
    del model

    class CachedModel:
        _character_assignment_panels = [p['panels'] for p in pages]

        def predict_crop_embeddings(self, images, boxes):
            return vectors

    assignments, summaries, galleries, traces = {}, {}, [], {}
    for distance in args.distances:
        name = f'distance-{distance:.2f}'
        bank_root = args.output / name / 'banks'
        # Replays always start empty; never append to a previous diagnostic bank.
        if (bank_root / raw['story_id'] / 'metadata.json').exists():
            raise ValueError('Replay bank already exists; select a new output directory')
        with CharacterBank(bank_root, raw['story_id'], model_fingerprint=MODEL_FINGERPRINT) as bank:
            bank.config['max_distance'] = distance
            bank.config['min_margin'] = args.margin
            bank.config['pending_max_page_gap'] = args.pending_max_page_gap
            assigner = DynamicCharacterAssigner(bank, keys, panel_assignment='overlap')
            assigner.assign_names_to_characters(CachedModel(), images,
                [p['characters'] for p in pages], {}, [p['character_cluster_labels'] for p in pages])
            assignments[name] = assigner.last_character_ids
            traces[name] = assigner.last_assignment_trace
            summaries[name] = {'max_distance': distance, 'min_margin': args.margin,
                'prototype_strategy': bank.config.get('prototype_strategy', 'legacy'),
                'pending_max_page_gap': args.pending_max_page_gap, 'characters': len(bank.characters),
                'crop_counts': {identity: len(record['crop_paths']) for identity, record in bank.characters.items()},
                'appearance_counts': {identity: record.get('appearance_count', 0) for identity, record in bank.characters.items()},
                'pending': len(bank.pending), 'assigned_detections': sum(x is not None for x in assigner.last_character_ids),
                'unassigned_detections': sum(x is None for x in assigner.last_character_ids)}
            cards = []
            for identity, record in sorted(bank.characters.items(), key=lambda pair: (-pair[1].get('appearance_count', 0), int(pair[0]))):
                previews = ''.join('<img style="width:auto;height:160px;max-width:240px;object-fit:contain" loading="lazy" src="'+html.escape((bank.directory / crop).relative_to(args.output).as_posix(), quote=True)+'">' for crop in record['crop_paths'])
                cards.append(f'<div><h3>MAGI {identity} — {record.get("appearance_count", 0)} lần xuất hiện</h3>{previews}</div>')
            galleries.append(f'<section><h2>Bank distance {distance}</h2>{"".join(cards)}</section>')

    result = {'scope': ('Fresh MAGI character detection with verified Koharu panels; no OCR or analysis' if args.detection_input
                       else 'Fresh-bank assignment replay; committed detections/clusters/panels reused; no OCR or analysis'),
        'detection_thresholds': detection['thresholds'] if args.detection_input else None,
        'source_run': str(args.run.resolve()), 'model': MODEL_FINGERPRINT,
        'pages': len(pages), 'summary': summaries, 'assignments': assignments,
        'assignment_policy': 'page-frozen-frequent-id-then-distance-v1', 'assignment_traces': traces}
    (args.output / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    visual = args.output / 'pages'
    visual.mkdir(exist_ok=True)
    sections, offset = [], 0
    for index, (image, page) in enumerate(zip(images, pages), 1):
        rgb = Image.fromarray(image)
        draw = ImageDraw.Draw(rgb)
        rows = []
        for ci, box in enumerate(page['characters']):
            before = assignments[names[0]][offset + ci]
            after = assignments[names[1]][offset + ci]
            label = f'C{ci+1}: {before if before is not None else "?"} / {after if after is not None else "?"}'
            draw.rectangle(tuple(box), outline='red', width=2)
            xy = (int(box[0]), max(0, int(box[1]) - 13))
            bounds = draw.textbbox(xy, label)
            draw.rectangle(bounds, fill='white')
            draw.text(xy, label, fill='black')
            rows.append(f'<tr><td>C{ci+1}</td><td>{before}</td><td>{after}</td></tr>')
        offset += len(page['characters'])
        rgb.save(visual / f'{index:03d}.jpg', quality=90)
        sections.append(f'<section><h2>Trang {index}</h2><a href="pages/{index:03d}.jpg"><img loading="lazy" src="pages/{index:03d}.jpg"></a><table><tr><th>Detection</th><th>{args.distances[0]:.2f}</th><th>{args.distances[1]:.2f}</th></tr>{"".join(rows)}</table></section>')
    note = f'Hai bank tạo mới độc lập; số giữa hai bank không nhất thiết cùng người. Margin = {args.margin}; pending chỉ nối trong khoảng cách tối đa {args.pending_max_page_gap} trang. Nhãn ảnh: C detection: ID ở {args.distances[0]:.2f} / ID ở {args.distances[1]:.2f}; ? là pending. Dùng lại khung/cluster đã lưu, không chạy lại detection.'
    if args.detection_input:
        note = note.replace('Dùng lại khung/cluster đã lưu, không chạy lại detection.',
            f'Detection MAGI chạy mới với character_detection_threshold = {detection["thresholds"]["character_detection_threshold"]}; panel Koharu được dùng lại.')
    (args.output / 'review.html').write_text('<!doctype html><meta charset="utf-8"><title>'+html.escape(title)+' — bank distance</title><style>body{font-family:Arial;max-width:1200px;margin:auto}section{border-top:1px solid #aaa;margin-top:30px}img{max-width:850px;width:100%}td,th{padding:5px;border:1px solid #ccc}table{border-collapse:collapse}</style><h1>'+html.escape(title)+' — bank distance</h1><p>'+html.escape(note)+'</p><pre>'+html.escape(json.dumps(summaries, indent=2))+'</pre>'+''.join(galleries)+''.join(sections), encoding='utf-8')
    (args.output / 'review.md').write_text('# '+title+' — bank distance\n\n'+note+'\n\n[Review ảnh toàn chương](review.html)\n\n```json\n'+json.dumps(summaries, indent=2)+'\n```\n', encoding='utf-8')
    print(json.dumps(summaries), flush=True)


if __name__ == '__main__':
    main()
