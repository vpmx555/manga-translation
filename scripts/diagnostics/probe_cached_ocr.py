"""OCR-only replay with verified source boxes, resumable batches and crop review."""
import argparse
import copy
import hashlib
import html
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--batch-size', type=int, default=16)
    args = parser.parse_args()
    if args.batch_size < 1:
        parser.error('Batch size must be positive')
    import numpy as np
    import torch
    from PIL import Image, ImageDraw
    from manga_pipeline.extraction.hayai import DEFAULT_CONFIG, PROCESSOR, PROCESSOR_REVISION, text_crop
    from manga_pipeline.extraction.core_ocr import MangaOcrEngine
    from manga_pipeline.storage.io import atomic_json, file_hash, fingerprint
    from manga_pipeline.runtime.resources import is_oom

    started = time.time()
    torch.set_num_threads(4)
    config = dict(DEFAULT_CONFIG, batch_size=args.batch_size, python=sys.executable)
    manifest = json.loads((args.run / 'manifest.json').read_text(encoding='utf-8-sig'))
    artifact = next(args.run.glob('[0-9][0-9]_extract/result.json'))
    if file_hash(artifact) != manifest['steps']['extract']['artifact_hash']:
        raise ValueError('Committed extract checksum mismatch')
    raw = json.loads(artifact.read_text(encoding='utf-8-sig'))
    images = []
    for saved in manifest['source']['images']:
        path = args.source / saved['path'].replace('\\', '/').rsplit('/', 1)[-1]
        if file_hash(path) != saved['sha256']:
            raise ValueError('Source image checksum mismatch: ' + str(path))
        with Image.open(path) as image:
            images.append(np.asarray(image.convert('L').convert('RGB')))
    if len(images) != len(raw['pages']):
        raise ValueError('Page count mismatch')
    args.output.mkdir(parents=True, exist_ok=True)
    crops_dir, batches_dir = args.output / 'crops', args.output / 'batches'
    crops_dir.mkdir(exist_ok=True)
    batches_dir.mkdir(exist_ok=True)
    rows, crops = [], []
    for row in raw['texts']:
        crop = text_crop(images[row['page']-1], row['bbox'])
        crop_path = f'crops/p{row["page"]:03d}-t{row["text_index"]+1:03d}.png'
        crop.save(args.output / crop_path)
        crop_hash = hashlib.sha256(np.asarray(crop).tobytes()).hexdigest()
        rows.append({'id': row['id'], 'page': row['page'], 'text_index': row['text_index'],
            'bbox': row['bbox'], 'old_text': row['text'], 'crop': crop_path,
            'crop_sha256': crop_hash, 'is_essential_text': row['is_essential_text']})
        crops.append(crop)
    contract = {'policy': 'ocr-only-core-v1', 'source_run': str(args.run.resolve()),
        'extract_hash': manifest['steps']['extract']['artifact_hash'],
        'config': config, 'processor': PROCESSOR, 'processor_revision': PROCESSOR_REVISION,
        'targets': [{'id': r['id'], 'bbox': r['bbox'], 'crop_sha256': r['crop_sha256']} for r in rows]}
    request_hash = fingerprint(contract)
    contract_path = args.output / 'request.json'
    if contract_path.exists() and fingerprint(json.loads(contract_path.read_text(encoding='utf-8'))) != request_hash:
        raise ValueError('Diagnostic OCR inputs changed; use a new output directory')
    atomic_json(contract_path, contract)
    device = 'cuda' if torch.cuda.is_available() else 'cpu'
    print(f'OCR ONLY: pages={len(images)}, targets={len(rows)}, device={device}, batch={args.batch_size}', flush=True)
    report = {'status': 'running', 'source_run': str(args.run.resolve()), 'config': config,
        'old_config': raw.get('ocr'), 'device': device, 'pages': len(images), 'targets': len(rows),
        'completed': 0, 'request_hash': request_hash, 'batch_events': [], 'results': rows}
    atomic_json(args.output / 'result.json', report)
    engine = None
    start, cap = 0, args.batch_size
    while start < len(rows):
        batch = rows[start:start+cap]
        ids = [r['id'] for r in batch]
        batch_hash = fingerprint([request_hash, ids])
        receipt = batches_dir / f'{start:04d}-{len(batch):02d}.json'
        texts = None
        if receipt.exists():
            saved = json.loads(receipt.read_text(encoding='utf-8'))
            if saved['request_hash'] != batch_hash or saved['result_hash'] != fingerprint(saved['texts']):
                raise ValueError('Corrupt or changed OCR batch checkpoint')
            texts = saved['texts']
        if texts is None:
            if engine is None:
                print('Loading pinned Hayai assets in local isolated environment', flush=True)
                engine = MangaOcrEngine(model_name=config['model'], revision=config['revision'],
                    processor_name=PROCESSOR, processor_revision=PROCESSOR_REVISION, device=device,
                    max_num_patches=config['max_num_patches'], max_new_tokens=config['max_new_tokens'])
                engine.load()
            batch_started = time.time()
            try:
                texts = engine.predict_batch(crops[start:start+len(batch)])
            except Exception as exc:
                if is_oom(exc) and len(batch) > 1:
                    cap = max(1, len(batch)//2)
                    report['batch_events'].append({'start': start, 'failed_size': len(batch), 'next_size': cap, 'reason': str(exc)[:300]})
                    if device == 'cuda':
                        torch.cuda.empty_cache()
                    atomic_json(args.output / 'result.json', report)
                    print(f'OCR batch reduction: {len(batch)} -> {cap}', flush=True)
                    continue
                report.update(status='failed', error=str(exc), failed_start=start)
                atomic_json(args.output / 'result.json', report)
                raise
            if len(texts) != len(batch) or any(not isinstance(t, str) for t in texts):
                raise ValueError('OCR returned invalid batch alignment')
            atomic_json(receipt, {'request_hash': batch_hash, 'ids': ids, 'texts': texts,
                'result_hash': fingerprint(texts), 'seconds': time.time()-batch_started})
        for row, text in zip(batch, texts):
            row['new_text'] = text
            row['changed'] = row['old_text'] != text
            row['content_changed'] = ' '.join(row['old_text'].split()) != text
        start += len(batch)
        report.update(completed=start, active_batch_size=cap, elapsed_seconds=time.time()-started)
        atomic_json(args.output / 'result.json', report)
        print(f'OCR {start}/{len(rows)} complete; last batch={len(batch)}', flush=True)
    updated = copy.deepcopy(raw)
    for target, row in zip(updated['texts'], rows):
        target['text'] = row['new_text']
    updated['ocr'] = config
    updated['diagnostic'] = {'scope': 'OCR only; committed detection/layout/character bank unchanged',
                             'source_run': str(args.run.resolve()), 'request_hash': request_hash}
    atomic_json(args.output / 'ocr-result.json', updated)
    pages_dir = args.output / 'pages'
    pages_dir.mkdir(exist_ok=True)
    sections = []
    transcript = []
    for page, image in enumerate(images, 1):
        view = Image.fromarray(image)
        draw = ImageDraw.Draw(view)
        table = []
        transcript.append(f'--- Page {page} ---')
        for row in rows:
            if row['page'] != page:
                continue
            color = 'red' if row['content_changed'] else 'orange' if row['changed'] else 'green'
            draw.rectangle(tuple(row['bbox']), outline=color, width=2)
            x, y = row['bbox'][:2]
            draw.text((int(x), max(0, int(y)-12)), f'T{row["text_index"]+1}', fill=color, stroke_width=1, stroke_fill='white')
            label = f'P{page} T{row["text_index"]+1}'
            essential = 'essential' if row['is_essential_text'] else 'nonessential'
            table.append(f'<tr><td>{label}<br>{essential}</td><td><img loading="lazy" class="crop" src="{row["crop"]}"></td><td>{html.escape(row["old_text"])}</td><td>{html.escape(row["new_text"])}</td></tr>')
            transcript.append(f'T{row["text_index"]+1}: {row["new_text"]}')
        view.save(pages_dir / f'{page:03d}.jpg', quality=90)
        sections.append(f'<section id="p{page}"><h2>Trang {page}</h2><details><summary>Ảnh box OCR</summary><img class="page" loading="lazy" src="pages/{page:03d}.jpg"></details><table><tr><th>Vị trí</th><th>Crop</th><th>OCR cũ</th><th>OCR mới</th></tr>{"".join(table)}</table></section>')
    report.update(status='completed', changed=sum(r['changed'] for r in rows),
        content_changed=sum(r['content_changed'] for r in rows), elapsed_seconds=time.time()-started)
    atomic_json(args.output / 'result.json', report)
    (args.output / 'transcript.txt').write_text('\n'.join(transcript)+'\n', encoding='utf-8')
    header = '<!doctype html><meta charset="utf-8"><title>Iris Zero — OCR only</title><style>body{font:16px Arial;max-width:1500px;margin:auto}table{width:100%;border-collapse:collapse}td,th{border:1px solid #ccc;padding:8px;text-align:left;white-space:pre-wrap;vertical-align:top}td{width:25%}img.crop{max-width:300px;max-height:300px}img.page{max-width:100%}section{margin-top:30px}</style>'
    stats = f'{len(images)} trang; {len(rows)} box; {report["changed"]} khác bản cũ, trong đó {report["content_changed"]} khác sau chuẩn hóa khoảng trắng. Đây là số khác biệt, không phải số lỗi được sửa.'
    (args.output / 'review.html').write_text(header+'<h1>Iris Zero chương 1 — chỉ OCR</h1><p>'+html.escape(stats)+'</p><p>Dùng nguyên box chữ của run cũ. Không chạy detection, analyze, translate hoặc sửa bank.</p>'+''.join(sections), encoding='utf-8')
    (args.output / 'review.md').write_text('# Iris Zero chương 1 — OCR only\n\n'+stats+'\n\n[Review crop và OCR cũ/mới](review.html)\n\n[Transcript mới](transcript.txt)\n', encoding='utf-8')
    print(json.dumps({k:report[k] for k in ('status','completed','changed','content_changed','elapsed_seconds')}), flush=True)


if __name__ == '__main__':
    main()
