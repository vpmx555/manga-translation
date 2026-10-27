"""Build documentation figures from saved layout detections, without inference."""
import argparse
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import shutil

from PIL import Image, ImageDraw, ImageFont


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir', type=Path, required=True)
    parser.add_argument('--source-folder', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--page', type=int, default=4)
    args = parser.parse_args()
    run, output = args.run_dir.resolve(), args.output.resolve()
    artifacts = list(run.glob('[0-9][0-9]_layout/result.json'))
    if len(artifacts) != 1:
        raise ValueError('Expected one layout artifact')
    artifact = artifacts[0]
    result = json.loads(artifact.read_text(encoding='utf-8-sig'))
    page = next(p for p in result['pages'] if p['page'] == args.page)
    source = args.source_folder / PurePosixPath(page['source']['path'].replace('\\', '/')).name
    if sha(source) != page['source']['sha256']:
        raise ValueError('Source page checksum mismatch')
    output.mkdir(parents=True, exist_ok=True)

    def saved_file(relative, expected):
        path = (run / relative).resolve()
        if not path.is_relative_to(run) or sha(path) != expected:
            raise ValueError('Saved layout asset missing or changed: '+relative)
        return path

    overlay_relative = next(p for p in page['files'] if '/overlays/' in p)
    overlay = saved_file(overlay_relative, page['files'][overlay_relative])
    bubble = page['bubbles'][0]
    mask = saved_file(bubble['mask_path'], bubble['mask_sha256'])
    shutil.copyfile(source, output / ('input'+source.suffix))
    shutil.copyfile(overlay, output / 'overview.png')
    shutil.copyfile(mask, output / 'bubble-mask-full.png')
    with Image.open(source) as im:
        image = im.convert('RGB')
    with Image.open(overlay) as im:
        annotated = im.convert('RGB')

    def zoom(im, box, name, padding=16):
        x1, y1, x2, y2 = box
        bounds = (max(0, math.floor(x1-padding)), max(0, math.floor(y1-padding)),
                  min(im.width, math.ceil(x2+padding)), min(im.height, math.ceil(y2+padding)))
        crop = im.crop(bounds)
        crop.resize((crop.width*2, crop.height*2), Image.Resampling.NEAREST).save(output / name)

    zoom(annotated, page['panels'][0]['bbox'], 'panel-zoom.png')
    zoom(annotated, bubble['bbox'], 'bubble-zoom.png')
    with Image.open(mask) as im:
        zoom(im, bubble['bbox'], 'bubble-mask-zoom.png')
    if page.get('effects_candidates'):
        effect = page['effects_candidates'][0]['bbox']
        diagnostic = image.copy()
        ImageDraw.Draw(diagnostic).rectangle(effect, outline='#e08000', width=2)
        zoom(diagnostic, effect, 'effect-candidate-zoom.png', padding=32)

    ordered = image.copy()
    draw = ImageDraw.Draw(ordered)
    font = ImageFont.load_default()
    for location in ('C:/Windows/Fonts/arial.ttf', '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'):
        if Path(location).is_file():
            font = ImageFont.truetype(location, 28)
            break
    centers = []
    for index, panel in enumerate(page['panels']):
        x1, y1, x2, y2 = panel['bbox']
        draw.rectangle((x1, y1, x2, y2), outline='#165dcc', width=5)
        center = ((x1+x2)/2, (y1+y2)/2)
        centers.append(center)
        label = 'P'+str(index)
        draw.rectangle((x1+6, y1+6, x1+76, y1+45), fill='white')
        draw.text((x1+10, y1+8), label, fill='#165dcc', font=font)
    for a, b in zip(centers, centers[1:]):
        draw.line((a, b), fill='#f08000', width=5)
        angle = math.atan2(b[1]-a[1], b[0]-a[0])
        draw.polygon([b, (b[0]-24*math.cos(angle-.45), b[1]-24*math.sin(angle-.45)),
                      (b[0]-24*math.cos(angle+.45), b[1]-24*math.sin(angle+.45))], fill='#f08000')
    ordered.save(output / 'panel-reading-order.png')
    (output / f'layout-page-{args.page:02d}.json').write_text(json.dumps(page, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    manifest = {'source_artifact': str(artifact), 'artifact_sha256': sha(artifact),
        'page': args.page, 'source_sha256': sha(source), 'model': result['model'],
        'revision': result['revision'], 'model_asset_sha256': page['model_asset_sha256'],
        'coordinate_space': result['coordinate_space'], 'generator_sha256': sha(__file__),
        'model_requests': 0, 'assets': {p.name: sha(p) for p in sorted(output.iterdir())
            if p.is_file() and p.name != 'manifest.json'}}
    (output / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({'output': str(output), 'page': args.page, 'panels': len(page['panels']),
        'bubbles': len(page['bubbles']), 'effects_candidates': len(page.get('effects_candidates', [])),
        'model_requests': 0}))


if __name__ == '__main__':
    main()
