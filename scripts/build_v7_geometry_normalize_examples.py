"""Render geometry/normalize teaching figures from saved artifacts, without models."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import textwrap

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont, ImageOps


BLUE = '#2563eb'
RED = '#dc2626'
ORANGE = '#b45309'
GREEN = '#087f5b'
PURPLE = '#7c3aed'
CYAN = '#0369a1'
INK = '#172b45'
MUTED = '#52647a'
FONT_DIR = Path('C:/Windows/Fonts')


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def font(size=20, bold=False):
    return ImageFont.truetype(str(FONT_DIR / ('arialbd.ttf' if bold else 'arial.ttf')), size)


def tag(image, xy, text, color, size=18):
    draw = ImageDraw.Draw(image)
    face = font(size, True)
    box = draw.textbbox((0, 0), text, font=face)
    x, y = xy
    draw.rounded_rectangle((x, y, x + box[2] + 12, y + size + 10), radius=4, fill=color)
    draw.text((x + 6, y + 3), text, fill='white', font=face)


def tint(image, mask, color):
    image.paste(Image.new('RGB', image.size, color), (0, 0), mask.point(lambda v: int(v * .12)))
    edge = ImageChops.subtract(mask.filter(ImageFilter.MaxFilter(5)), mask.filter(ImageFilter.MinFilter(3)))
    image.paste(Image.new('RGB', image.size, color), (0, 0), edge)


def candidate(image, record, color):
    draw = ImageDraw.Draw(image)
    points = [tuple(p) for p in record['contour']]
    draw.line(points + points[:1], fill=color, width=3, joint='curve')


def panels(image, records, indices, crop=None):
    draw = ImageDraw.Draw(image)
    for index in indices:
        box = records[index]
        draw.rectangle(box, outline=BLUE, width=3)
        x = max(box[0] + 8, crop[0] + 8) if crop else box[0] + 8
        y = max(box[1] + 8, crop[1] + 8) if crop else box[1] + 8
        tag(image, (x, y), 'panel ' + str(index), BLUE, 16)


def text_boxes(image, rows, labels=True):
    draw = ImageDraw.Draw(image)
    for row, color in zip(rows, [PURPLE, CYAN]):
        draw.rectangle(row['bbox'], outline=color, width=3)
        if labels:
            x, y = row['bbox'][:2]
            tag(image, (x - 47, y), row['id'].split(':')[-1], color, 16)


def figure(path, title, subtitle, cards, body_height=640):
    """Equal crops and equal scales make every before/after directly comparable."""
    width, gap, margin = 420, 18, 24
    result = Image.new('RGB', (margin * 2 + len(cards) * width + (len(cards) - 1) * gap,
                               body_height + 232), '#edf2f7')
    draw = ImageDraw.Draw(result)
    draw.text((margin, 17), title, font=font(28, True), fill=INK)
    draw.text((margin, 57), subtitle, font=font(19), fill=MUTED)
    for i, card in enumerate(cards):
        heading, picture, notes = card
        x, y = margin + i * (width + gap), 98
        draw.rounded_rectangle((x, y, x + width, y + body_height + 108), radius=12, fill='white')
        draw.text((x + 15, y + 14), heading, font=font(22, True), fill=INK)
        if isinstance(picture, Image.Image):
            thumb = ImageOps.contain(picture, (width - 24, body_height - 18), Image.Resampling.LANCZOS)
            result.paste(thumb, (x + (width - thumb.width) // 2, y + 55 + (body_height - thumb.height) // 2))
        else:
            offset = y + 76
            for line, color, bold in picture:
                draw.text((x + 20, offset), line, font=font(21, bold), fill=color)
                offset += 33
        for j, note in enumerate(notes):
            draw.text((x + 15, y + body_height + 62 + j * 25), note, font=font(18), fill=MUTED)
    result.save(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir', type=Path, required=True)
    parser.add_argument('--source-folder', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    run, output = args.run_dir.resolve(), args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    receipts = {}

    def verify(path, expected=None):
        path = path.resolve()
        actual = sha(path)
        if expected is not None and actual != expected:
            raise ValueError('Checksum mismatch: ' + str(path))
        receipts[str(path)] = actual
        return path

    def load(relative):
        path = verify(run / relative)
        return json.loads(path.read_text(encoding='utf-8-sig'))

    manifest = load('manifest.json')
    layout = load('01_layout/result.json')
    raw = load('02_extract/result.json')
    geometry = load('03_bubble_geometry/result.json')
    normalized = load('04_normalize/result.json')

    def case(page_number, group_suffix):
        receipt = manifest['source']['images'][page_number - 1]
        filename = PurePosixPath(receipt['path'].replace('\\', '/')).name
        source = verify(args.source_folder / filename, receipt['sha256'])
        with Image.open(source) as original:
            image = original.convert('RGB')
        shutil.copyfile(source, output / f'input-page-{page_number}{source.suffix}')
        page = next(p for p in geometry['pages'] if p['page'] == page_number)
        lp = next(p for p in layout['pages'] if p['page'] == page_number)
        group = next(g for g in page['bubble_groups'] if g['id'].endswith(':' + group_suffix))
        record = group['refined_mask']
        mask_path = (run / record['mask_path']).resolve()
        if not mask_path.is_relative_to(run):
            raise ValueError('Mask outside run')
        with Image.open(verify(mask_path, record['mask_sha256'])) as saved:
            mask = saved.convert('L')
        if mask.size != image.size:
            raise ValueError('Mask coordinate system differs from source image')
        rows = [t for t in geometry['texts'] if t.get('group_id') == group['id']]
        utterances = [u for u in normalized['utterances'] if u['group_id'] == group['id']]
        candidates = [b for b in lp['bubbles'] if b['id'] in group['candidate_ids']]
        data = {'page': page_number, 'panels': page['panels'], 'layout_candidates': candidates,
                'group': group, 'mapped_texts': rows, 'utterances': utterances,
                'extract_texts': [t for t in raw['texts'] if t['id'] in {r['id'] for r in rows}]}
        return image, mask, data

    original, mask, double = case(17, 'g7')
    crop = (260, 260, 545, 725)
    selected = {'p17:b14': RED, 'p17:b16': ORANGE}
    before = original.copy()
    for b in double['layout_candidates']:
        if b['id'] in selected:
            candidate(before, b, selected[b['id']])
    tag(before, (329, 273), 'b14', RED)
    tag(before, (350, 684), 'b16', ORANGE)
    after = original.copy()
    tint(after, mask, GREEN)
    tag(after, (329, 273), 'g7', GREEN)
    figure(output / 'geometry-double-bubble.png', 'Bubble đôi: hai vùng phát hiện → một group vật lý',
           'Trang 17 · cùng vùng crop, cùng tỷ lệ · đỏ/cam: candidate layout · xanh lá: mask geometry', [
               ('1. Ảnh gốc', original.crop(crop), ['Hai phần phồng nối liền nhau.', 'Không phải hai bubble độc lập.']),
               ('2. Layout: b14 và b16', before.crop(crop), ['Hai phát hiện từ hai panel crop.', 'Một phần trên, một phần dưới.']),
               ('3. Geometry: group g7', after.crop(crop), ['Giữ toàn bộ vùng bubble nối liền.', '5 candidate nguồn → 1 group.']),
           ])

    layout_input = original.copy()
    panels(layout_input, double['panels'], double['group']['panel_indices'])
    for b in double['layout_candidates']:
        candidate(layout_input, b, selected.get(b['id'], '#64748b'))
    tag(layout_input, (329, 273), 'b14', RED)
    tag(layout_input, (350, 684), 'b16', ORANGE)
    layout_input.save(output / 'layout-input-page-17.png')

    cross_original, cross_mask, crossing = case(11, 'g2')
    cross_crop = (38, 955, 395, 1305)
    cross_before = cross_original.copy()
    panels(cross_before, crossing['panels'], [4, 5], cross_crop)
    for b in crossing['layout_candidates']:
        if b['id'] in ('p11:b4', 'p11:b5'):
            candidate(cross_before, b, RED if b['id'].endswith('b4') else ORANGE)
    tag(cross_before, (155, 978), 'b4', RED)
    tag(cross_before, (230, 1250), 'b5', ORANGE)
    cross_after = cross_original.copy()
    panels(cross_after, crossing['panels'], [4, 5], cross_crop)
    tint(cross_after, cross_mask, GREEN)
    tag(cross_after, (155, 978), 'g2', GREEN)
    figure(output / 'geometry-multi-panel.png', 'Bubble qua ranh giới panel: giữ một vùng thống nhất',
           'Trang 11 · xanh dương: panel 4/5 · đỏ/cam: các mảnh crop · xanh lá: group g2', [
               ('1. Ảnh gốc', cross_original.crop(cross_crop), ['Một bubble nằm trên cả hai panel.', 'Rãnh trắng không cắt đôi bubble.']),
               ('2. Layout theo panel', cross_before.crop(cross_crop), ['b4 và b5 chỉ thấy từng phần.', 'Đường xanh là bbox panel đã lưu.']),
               ('3. Geometry: group g2', cross_after.crop(cross_crop), ['Một group, panel_indices = [4, 5].', 'Giữ trọn mask qua ranh giới.']),
           ], body_height=440)

    texts = double['mapped_texts']
    source_boxes = original.copy()
    text_boxes(source_boxes, texts)
    source_boxes.save(output / 'extract-input-page-17.png')
    mapped = original.copy()
    tint(mapped, mask, GREEN)
    text_boxes(mapped, texts)
    tag(mapped, (329, 273), 'g7', GREEN)
    mapped.save(output / 'geometry-output-page-17.png')
    utterance, = double['utterances']
    if [t['id'] for t in texts] != utterance['source_text_ids']:
        raise ValueError('Example text order differs from frozen normalized output')
    record = [('id: p17:g7:u', GREEN, True), ('', INK, False),
              ('text (giữ nguyên OCR):', INK, True)]
    record += [(line, INK, False) for line in textwrap.wrap(utterance['text'], width=24)]
    record += [('', INK, False), ('source_text_ids:', INK, True),
              ('[p17:t2, p17:t4]', PURPLE, False), ('', INK, False),
              ('panel_index: 2', INK, False), ('panel_indices: [0, 2, 3]', INK, False),
              ('content_type: unknown', MUTED, False)]
    figure(output / 'normalize-before-after.png', 'Normalize: hai text box → một utterance có nguồn',
           'Trang 17 · tím: t2 · xanh lam: t4 · xanh lá: group g7 · ID trong hình được rút gọn', [
               ('1. Text box từ extract', source_boxes.crop(crop), ['t2 và t4 là hai box OCR riêng.', 'Nội dung và bbox nguồn được giữ.']),
               ('2. Mapping từ geometry', mapped.crop(crop), ['Cả hai box cùng thuộc group g7.', 'Geometry chưa ghép nội dung chữ.']),
               ('3. Output của normalize', record, ['Ghép theo thứ tự nguồn: t2 → t4.', 'Không sửa OCR hay chốt speaker.']),
           ])

    selected_data = {'id_display': 'Figure labels omit the document prefix only.',
                     'double_bubble': double, 'multi_panel': crossing}
    (output / 'examples.json').write_text(json.dumps(selected_data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    record = {'source_run': str(run), 'source_artifacts': receipts,
              'generator_sha256': sha(__file__), 'model_requests': 0,
              'figures': {
                  'geometry-double-bubble.png': {'page': 17, 'crop': crop, 'highlighted_candidates': list(selected),
                                                  'all_candidates': double['group']['candidate_ids']},
                  'geometry-multi-panel.png': {'page': 11, 'crop': cross_crop, 'highlighted_candidates': ['p11:b4', 'p11:b5']},
                  'normalize-before-after.png': {'page': 17, 'crop': crop, 'utterance_id': utterance['id']},
              },
              'assets': {p.name: sha(p) for p in sorted(output.iterdir()) if p.is_file() and p.name != 'manifest.json'}}
    (output / 'manifest.json').write_text(json.dumps(record, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'output': str(output), 'assets': len(record['assets']), 'model_requests': 0}))


if __name__ == '__main__':
    main()
