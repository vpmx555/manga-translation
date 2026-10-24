"""Compare real thanhdat crops, reconstructed crops and cached MAGI boxes."""
import argparse
import hashlib
import html
import json
import math
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))


def overlap(a, b):
    intersection = max(0, min(a[2], b[2])-max(a[0], b[0]))*max(0, min(a[3], b[3])-max(a[1], b[1]))
    union = (a[2]-a[0])*(a[3]-a[1])+(b[2]-b[0])*(b[3]-b[1])-intersection
    return intersection/union if union else 0


def normalized(text):
    return ' '.join(text.split())


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--reference', type=Path, required=True)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    import numpy as np
    import torch
    from PIL import Image
    from manga_pipeline.extraction.hayai import DEFAULT_CONFIG, PROCESSOR, PROCESSOR_REVISION, text_crop
    from manga_pipeline.extraction.core_ocr import MangaOcrEngine
    from manga_pipeline.storage.io import atomic_json, file_hash, fingerprint
    from manga_pipeline.runtime.resources import is_oom
    torch.set_num_threads(4)
    started = time.time()
    config = dict(DEFAULT_CONFIG, python=sys.executable)
    args.output.mkdir(parents=True, exist_ok=True)
    for name in ('crops', 'cache'):
        (args.output/name).mkdir(exist_ok=True)
    manifest = json.loads((args.run/'manifest.json').read_text(encoding='utf-8-sig'))
    artifact = next(args.run.glob('[0-9][0-9]_extract/result.json'))
    if file_hash(artifact) != manifest['steps']['extract']['artifact_hash']:
        raise ValueError('Source extraction hash mismatch')
    raw = json.loads(artifact.read_text(encoding='utf-8'))
    source = {}
    main_by_page = {}
    for index, record in enumerate(manifest['source']['images'], 1):
        name = Path(record['path'].replace('\\', '/')).name
        page = Path(name).stem
        if not (args.reference/page/'result.json').is_file():
            continue
        path = args.source/name
        if file_hash(path) != record['sha256']:
            raise ValueError('Original image hash mismatch: '+str(path))
        with Image.open(path) as image:
            source[page] = image.convert('RGB')
        main_by_page[page] = [r for r in raw['texts'] if r['page'] == index]
    tasks, references = [], []
    unique = {}

    def add(variant, page, identity, image, baseline):
        image = image.convert('RGB')
        pixel_hash = fingerprint([image.size, hashlib.sha256(np.asarray(image).tobytes()).hexdigest()])
        cache_key = fingerprint([config, PROCESSOR, PROCESSOR_REVISION, pixel_hash])
        identity = f'{variant}-{page}-{identity}'
        crop = f'crops/{identity}.png'
        image.save(args.output/crop)
        task = {'key': identity, 'variant': variant, 'page': page, 'baseline': baseline,
                'crop': crop, 'size': list(image.size), 'pixel_hash': pixel_hash, 'cache_key': cache_key}
        tasks.append(task)
        unique.setdefault(cache_key, {'image': image, 'tasks': []})['tasks'].append(task)
        return task

    main_tasks = {}
    for page, rows in main_by_page.items():
        gray = np.asarray(source[page].convert('L').convert('RGB'))
        for row in rows:
            main_tasks[row['id']] = add('main_magi_gray', page, row['text_index']+1,
                text_crop(gray, row['bbox']), row['text'])

    for directory in sorted(p for p in args.reference.iterdir() if p.is_dir() and p.name.isdigit()):
        data = json.loads((directory/'result.json').read_text(encoding='utf-8'))
        page = directory.name
        if page in source and source[page].size != (data['image_size']['width'], data['image_size']['height']):
            raise ValueError('Reference/original dimensions differ')
        for detection in data['detections']:
            text = detection.get('ocr_text', '')
            if not text.strip():
                continue
            crop_path = next((directory/'crops').glob(f'crop_{detection["id"]:02d}_*.jpg'))
            with Image.open(crop_path) as crop:
                task = add('reference_jpeg_rgb', page, detection['id'], crop, text)
            reference = {'page': page, 'detection': detection['id'], 'text': text, 'bbox': detection['bbox'],
                         'label': detection['label'], 'is_bubble': detection['is_bubble'],
                         'jobs': {'reference_jpeg_rgb': task['key']}, 'reference_crop_sha256': file_hash(crop_path)}
            box = detection['bbox']
            if page in source:
                image = source[page]
                padded = (max(0, math.floor(box[0])-4), max(0, math.floor(box[1])-4),
                          min(image.width, math.ceil(box[2])+4), min(image.height, math.ceil(box[3])+4))
                for variant, candidate in (
                    ('reference_pad4_rgb', image.crop(padded)),
                    ('reference_pad4_gray', image.convert('L').convert('RGB').crop(padded)),
                    ('reference_no_pad_rgb', image.crop(tuple(box)))):
                    reference['jobs'][variant] = add(variant, page, detection['id'], candidate, text)['key']
                best = max(main_by_page[page], key=lambda r: overlap(box, r['bbox']), default=None)
                score = overlap(box, best['bbox']) if best else 0
                reference['best_main_iou'] = score
                if best and score >= .3:
                    reference['main_match'] = main_tasks[best['id']]['key']
            references.append(reference)
    request = {'config': config, 'source_extract_hash': manifest['steps']['extract']['artifact_hash'],
        'source_run': str(args.run.resolve()), 'reference': str(args.reference.resolve()),
        'tasks': [{k: v for k, v in t.items() if k != 'cache_key'} for t in tasks]}
    request_file = args.output/'request.json'
    if request_file.exists() and fingerprint(json.loads(request_file.read_text(encoding='utf-8'))) != fingerprint(request):
        raise ValueError('Inputs changed; choose a new diagnostic output')
    atomic_json(request_file, request)
    pending = []
    for key, item in unique.items():
        cache = args.output/'cache'/(key+'.json')
        if cache.exists():
            value = json.loads(cache.read_text(encoding='utf-8'))
            if value['key'] != key or value['hash'] != fingerprint(value['text']):
                raise ValueError('Corrupt OCR cache')
            for task in item['tasks']:
                task['new_text'] = value['text']
        else:
            pending.append((key, item))
    report = {'status': 'running', 'config': config, 'variants': sorted({t['variant'] for t in tasks}),
        'references': references, 'tasks': tasks, 'unique_crops': len(unique), 'completed_unique': len(unique)-len(pending)}
    atomic_json(args.output/'result.json', report)
    print(f'Compare OCR only: references={len(references)}, tasks={len(tasks)}, unique={len(unique)}, pending={len(pending)}', flush=True)
    engine = None
    cap = config['batch_size']
    start = 0
    while start < len(pending):
        batch = pending[start:start+cap]
        if engine is None:
            engine = MangaOcrEngine(model_name=config['model'], revision=config['revision'],
                processor_name=PROCESSOR, processor_revision=PROCESSOR_REVISION, device='cpu',
                max_num_patches=config['max_num_patches'], max_new_tokens=config['max_new_tokens'])
            engine.load()
        try:
            texts = engine.predict_batch([item['image'] for _, item in batch])
        except Exception as exc:
            if is_oom(exc) and len(batch) > 1:
                cap = max(1, len(batch)//2)
                print(f'Reduce batch to {cap}', flush=True)
                continue
            raise
        for (key, item), text in zip(batch, texts):
            atomic_json(args.output/'cache'/(key+'.json'), {'key': key, 'text': text, 'hash': fingerprint(text)})
            for task in item['tasks']:
                task['new_text'] = text
        start += len(batch)
        report['completed_unique'] = len(unique)-len(pending)+start
        atomic_json(args.output/'result.json', report)
        print(f'OCR {report["completed_unique"]}/{len(unique)} unique crops', flush=True)
    indexed = {t['key']: t for t in tasks}
    summary = {}
    for variant in report['variants']:
        rows = [t for t in tasks if t['variant'] == variant]
        summary[variant] = {'count': len(rows), 'matches_reference_or_old_after_whitespace': sum(normalized(t['baseline']) == normalized(t['new_text']) for t in rows)}
    matches = [r for r in references if r.get('main_match')]
    summary['matched_regions'] = {'count': len(matches),
        'main_new_equals_reference': sum(normalized(indexed[r['main_match']]['new_text']) == normalized(r['text']) for r in matches),
        'same_jpeg_new_equals_reference': sum(normalized(indexed[r['jobs']['reference_jpeg_rgb']]['new_text']) == normalized(r['text']) for r in matches)}
    report.update(status='completed', summary=summary, elapsed_seconds=time.time()-started)
    atomic_json(args.output/'result.json', report)
    sections = []
    for r in references:
        jobs = dict(r['jobs'])
        if r.get('main_match'):
            jobs['main_magi_gray'] = r['main_match']
        rows = ['<tr><th>OCR thanhdat đã gửi</th><td>'+html.escape(r['text'])+'</td></tr>']
        pictures = []
        for variant, key in jobs.items():
            task = indexed[key]
            rows.append('<tr><th>'+variant+'</th><td>'+html.escape(task['new_text'])+'</td></tr>')
            if variant in ('reference_jpeg_rgb','main_magi_gray'):
                pictures.append('<figure><img loading="lazy" src="'+task['crop']+'"><figcaption>'+variant+': '+str(task['size'])+'</figcaption></figure>')
        sections.append(f'<section><h2>Trang {r["page"]} · D{r["detection"]}</h2><div class="pictures">'+''.join(pictures)+'</div><table>'+''.join(rows)+'</table></section>')
    header = '<!doctype html><meta charset="utf-8"><title>After School — OCR comparison</title><style>body{font:16px Arial;max-width:1350px;margin:auto}section{border-top:1px solid #aaa;margin-top:25px}.pictures{display:flex;flex-wrap:wrap}img{max-width:330px;max-height:420px}table{width:100%;border-collapse:collapse}th,td{border:1px solid #ccc;padding:8px;text-align:left;white-space:pre-wrap}th{width:300px}</style>'
    notice = 'OCR-only local, Hayai Nova/384 patches/batch16. Kết quả thanhdat là bản đối chiếu, không phải ground truth. 01/13 chỉ có crop JPEG tham chiếu; không có ảnh gốc local tương ứng. Các cặp MAGI được ghép bằng IoU >= 0.3, cần xem crop để xác nhận cùng vùng.'
    (args.output/'review.html').write_text(header+'<h1>After School — đối chiếu OCR thực tế</h1><p>'+notice+'</p><pre>'+html.escape(json.dumps(summary, ensure_ascii=False, indent=2))+'</pre>'+''.join(sections), encoding='utf-8')
    print(json.dumps(summary), flush=True)


if __name__ == '__main__':
    main()
