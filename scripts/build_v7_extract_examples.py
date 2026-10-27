"""Render extract documentation from saved JSON/images/bank; never load a model."""
import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import shutil
import textwrap

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageOps


BG = '#eef2f6'
INK = '#16263b'
MUTED = '#516277'
BLUE = '#1266bd'
RED = '#b9233d'
TEAL = '#007e7e'
ORANGE = '#b75400'
COLORS = ['#007e7e', '#8044b5', '#bd5211', '#1266bd', '#ab2857', '#577d10']


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def font(size=24, bold=False):
    base = Path('C:/Windows/Fonts')
    for filename in (('segoeuib.ttf' if bold else 'segoeui.ttf'), 'arial.ttf'):
        if (base / filename).exists():
            return ImageFont.truetype(str(base / filename), size)
    return ImageFont.load_default(size=size)


def write(draw, xy, value, size=24, fill=INK, bold=False):
    draw.text(xy, str(value), font=font(size, bold), fill=fill)


def lines(draw, xy, value, width=55, size=24, fill=INK):
    y = xy[1]
    for paragraph in str(value).split('\n'):
        for line in textwrap.wrap(paragraph, width=width) or ['']:
            write(draw, (xy[0], y), line, size, fill)
            y += size + 7
    return y


def sheet(width, height, title, subtitle):
    picture = Image.new('RGB', (width, height), BG)
    draw = ImageDraw.Draw(picture)
    draw.rectangle((0, 0, width, 108), fill=INK)
    write(draw, (26, 14), title, 32, 'white', True)
    write(draw, (26, 61), subtitle, 21, '#d5e2f2')
    return picture


def place(picture, image, rect, contain=True):
    x, y, w, h = map(int, rect)
    scaled = ImageOps.contain(image, (w, h)) if contain else ImageOps.fit(image, (w, h))
    picture.paste(scaled, (x + (w - scaled.width) // 2, y + (h - scaled.height) // 2))


def badge(draw, xy, value, color, size=21):
    x, y = map(int, xy)
    face = font(size, True)
    bounds = draw.textbbox((0, 0), value, font=face)
    w, h = bounds[2] + 10, size + 10
    draw.rectangle((x, y, x+w, y+h), fill=color)
    draw.text((x+5, y+2), value, font=face, fill='white')


def center(box):
    return ((box[0] + box[2])/2, (box[1] + box[3])/2)


def box(draw, bounds, label, color, label_below=False):
    draw.rectangle(bounds, outline=color, width=4)
    y = bounds[3] - 30 if label_below else max(0, bounds[1])
    badge(draw, (max(0, bounds[0]), y), label, color)


def tail_box(draw, bounds, label):
    draw.rectangle(bounds, outline=TEAL, width=3)
    x, y = bounds[2]+12, bounds[3]+12
    draw.line((center(bounds), (x, y)), fill=TEAL, width=2)
    badge(draw, (x, y), label, TEAL)


def crop(image, bounds, pad=8):
    x1, y1, x2, y2 = bounds
    return image.crop((max(0, math.floor(x1)-pad), max(0, math.floor(y1)-pad),
                       min(image.width, math.ceil(x2)+pad), min(image.height, math.ceil(y2)+pad)))


def overlay(image, page, texts, mode):
    picture = image.copy()
    draw = ImageDraw.Draw(picture)
    if mode == 'panels':
        for i, bounds in enumerate(page['panels']):
            box(draw, bounds, 'P'+str(i), BLUE)
    elif mode == 'characters':
        for i, bounds in enumerate(page['characters']):
            k = page['character_cluster_labels'][i]
            box(draw, bounds, f'C{i} / K{k}', COLORS[k % len(COLORS)], bool(i % 2))
    elif mode == 'bank':
        for i, bounds in enumerate(page['characters']):
            identity = page['character_ids'][i]
            box(draw, bounds, f'C{i}: bank {identity if identity is not None else "null"}',
                TEAL if identity is not None else ORANGE, bool(i % 2))
    else:
        target = 'text_character_associations' if mode == 'character-links' else 'text_tail_associations'
        if mode in ('character-links', 'tail-links'):
            boxes = page['characters'] if mode == 'character-links' else page['tails']
            for ti, index in page[target]:
                text = next(t for t in texts if t['text_index'] == ti)
                draw.line((center(text['bbox']), center(boxes[index])), fill=ORANGE, width=3)
            for i, bounds in enumerate(boxes):
                if mode == 'tail-links':
                    cx, cy = center(bounds)
                    draw.ellipse((cx-23, cy-23, cx+23, cy+23), outline=TEAL, width=4)
                if mode == 'tail-links':
                    tail_box(draw, bounds, 'L'+str(i))
                else:
                    box(draw, bounds, 'C'+str(i), TEAL)
        for text in texts:
            ti = text['text_index']
            label = f'T{ti}'
            if mode == 'texts':
                label += f" / R{text['reading_order']}"
            color = RED if text['is_essential_text'] else MUTED
            draw.rectangle(text['bbox'], outline=color, width=3)
            badge(draw, (max(0, text['bbox'][0]), max(0, text['bbox'][1]-32)), label, color)
    return picture


def page_figure(image, title, subtitle):
    picture = sheet(1020, 1570, title, subtitle)
    place(picture, image, (28, 130, 964, 1400))
    return picture


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir', type=Path, required=True)
    parser.add_argument('--source-folder', type=Path, required=True)
    parser.add_argument('--bank-dir', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    run, bank_dir, out = args.run_dir.resolve(), args.bank_dir.resolve(), args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    extract_path, = run.glob('[0-9][0-9]_extract/result.json')
    raw = json.loads(extract_path.read_text(encoding='utf-8-sig'))
    manifest = json.loads((run/'manifest.json').read_text(encoding='utf-8-sig'))
    bank = json.loads((bank_dir/'metadata.json').read_text(encoding='utf-8-sig'))
    receipts = {}

    def record(path):
        receipts[str(path)] = sha(path)

    for path in (extract_path, run/'manifest.json', bank_dir/'metadata.json', bank_dir/'embeddings/vectors.npz'):
        record(path)
    images, pages, texts = {}, {}, {}

    def load_page(number):
        if number not in images:
            page = next(p for p in raw['pages'] if p['page'] == number)
            receipt = manifest['source']['images'][number-1]
            source = args.source_folder / PurePosixPath(receipt['path'].replace('\\', '/')).name
            if sha(source) != receipt['sha256']:
                raise ValueError(f'Source checksum differs: {source}')
            record(source)
            with Image.open(source) as original:
                images[number] = original.convert('L').convert('RGB')
            if images[number].size != (page['width'], page['height']):
                raise ValueError('Image dimensions differ from extract')
            pages[number] = page
            texts[number] = [t for t in raw['texts'] if t['page'] == number]
        return images[number], pages[number], texts[number]

    image, page, rows = load_page(12)
    source = args.source_folder / '12.webp'
    shutil.copyfile(source, out/'input-12.webp')
    views = {mode: overlay(image, page, rows, mode) for mode in
             ('panels', 'texts', 'characters', 'character-links', 'tail-links', 'bank')}
    titles = {
        'panels': ('01 · Panel và thứ tự hình học', 'P: panel Koharu; panel_source=koharu'),
        'texts': ('02 · Text box và thứ tự đọc', 'T: text_index; R: reading_order toàn chapter; đỏ: essential'),
        'characters': ('03 · Character detection và cluster', 'C: detection; K: cluster cục bộ; cùng màu = cùng cluster'),
        'character-links': ('04 · Text → character', 'Cam: association qua chỉ số; không phải hướng tail trên ảnh'),
        'tail-links': ('05 · Text → tail', 'L: tail_index; vòng xanh giúp thấy tail nhỏ; cam: association'),
        'bank': ('Bank ID trên các detection trang 12', 'ID bền vững khác K; null là chưa xác nhận vào bank'),
    }
    for mode, (title, subtitle) in titles.items():
        page_figure(views[mode], title, subtitle).save(out/(mode+'.png'))

    atlas = sheet(1560, 1780, 'MAGIv2 extract · Toàn bộ nhóm output',
                  'Trang 12 · 6 panel / 12 text / 7 character / 3 cluster / 4 tail / 12 T→C / 5 T→L')
    draw = ImageDraw.Draw(atlas)
    for i, mode in enumerate(('panels', 'texts', 'characters', 'character-links', 'tail-links')):
        x, y = 20 + (i % 3)*515, 130 + (i//3)*820
        write(draw, (x+8, y), titles[mode][0], 23, INK, True)
        place(atlas, views[mode], (x+10, y+42, 485, 704))
    x, y = 1050, 950
    write(draw, (x+8, y), '06 · OCR / cờ / nguồn dữ liệu', 23, INK, True)
    details = [
        'T0 · R56 · essential=true', rows[0]['text'],
        'T5 · R61 · character=C0', 'tail=L2 · cluster=K0',
        'T6 · R62 · character=C0', 'tail=L2 · cùng tail với T5',
        'OCR: GLM-OCR riêng', 'ocr_detail: raw_text, token,', 'empty, truncated',
        'Bbox: pixel ảnh gốc', 'reading_direction: rtl',
        'speaker_affinity: null', 'Điểm affinity không được export',
        'Bank ID và prototype:', 'xem docs 02,2_extract_bank',
    ]
    for i, value in enumerate(details):
        write(draw, (x+12, y+62+i*39), value, 23, MUTED if i % 2 else INK)
    atlas.save(out/'magiv-output.png')

    clusters = sheet(1560, 1140, 'Cluster trên trang 12 · Các crop thành viên',
                     'Cùng K trong trang là một observation · chưa dùng tên hay số bank để gom')
    d = ImageDraw.Draw(clusters)
    for row_index, k in enumerate(sorted(set(page['character_cluster_labels']))):
        members = [i for i, label in enumerate(page['character_cluster_labels']) if label == k]
        y = 130 + row_index*330
        write(d, (25, y), f'K{k}', 30, COLORS[k], True)
        write(d, (25, y+46), f'{len(members)} crop', 22, MUTED)
        for col, ci in enumerate(members):
            x = 190 + col*440
            d.rounded_rectangle((x, y, x+414, y+305), radius=10, fill='white', outline=COLORS[k], width=3)
            place(clusters, crop(image, page['characters'][ci]), (x+12, y+12, 390, 246))
            write(d, (x+18, y+261), f'P12 / C{ci} / K{k}', 24, COLORS[k], True)
    clusters.save(out/'cluster-crops.png')

    cases = [(12, 0), (12, 5), (12, 6), (8, 1), (8, 2), (8, 0), (11, 4)]
    cards = sheet(1560, 1640, 'Text/OCR · Crop và record tương ứng',
                  'Giữ nguyên OCR lịch sử, kể cả spelling lạ hoặc chuỗi rỗng · không chạy lại model')
    d = ImageDraw.Draw(cards)
    case_records = []
    for i, (pn, ti) in enumerate(cases):
        im, pg, ts = load_page(pn)
        t = next(t for t in ts if t['text_index'] == ti)
        case_records.append(t)
        x, y = 22+(i % 2)*770, 130+(i//2)*370
        d.rounded_rectangle((x, y, x+744, y+348), radius=12, fill='white')
        place(cards, crop(im, t['bbox']), (x+14, y+46, 255, 236))
        write(d, (x+16, y+10), f'P{pn} / T{ti} / R{t["reading_order"]}', 25, RED, True)
        yy = lines(d, (x+288, y+48), t['text'] or '(OCR rỗng)', 30, 23)
        c, tail = t['character_detection_index'], t['tail_index']
        c, tail = ('null' if value is None else value for value in (c, tail))
        lines(d, (x+288, max(y+166, yy+12)),
              f'essential={str(t["is_essential_text"]).lower()}\ncharacter={c} / tail={tail}\n{t["speaker_status"]}', 32, 21, MUTED)
        detail = t.get('ocr_detail', {})
        write(d, (x+16, y+307), f'empty={detail.get("empty")} · truncated={detail.get("truncated")}', 20, MUTED)
    cards.save(out/'ocr-cases.png')

    zooms = sheet(1560, 980, 'Một tail có thể liên kết nhiều text',
                  'Trang 12: [T5,L2] và [T6,L2] · bbox L2 là output MAGI, không vẽ lại tail')
    d = ImageDraw.Draw(zooms)
    related = [t for t in rows if t['text_index'] in (5, 6)]
    bounds = [t['bbox'] for t in related]+[page['tails'][2], page['characters'][0]]
    union = [min(b[0] for b in bounds), min(b[1] for b in bounds), max(b[2] for b in bounds), max(b[3] for b in bounds)]
    isolated = image.copy()
    dd = ImageDraw.Draw(isolated)
    for t in related:
        dd.rectangle(t['bbox'], outline=RED, width=3)
        badge(dd, (t['bbox'][0], max(0, t['bbox'][1]-32)), 'T'+str(t['text_index']), RED)
    box(dd, page['characters'][0], 'C0 / K0', TEAL)
    tail_box(dd, page['tails'][2], 'L2')
    place(zooms, crop(isolated, union, 55), (25, 145, 970, 770))
    lines(d, (1030, 165), 'T5 → C0\nT6 → C0\n\nT5 → L2\nT6 → L2\n\nC0 thuộc K0.\n\nHai bảng association độc lập; không có bảng tail → character trực tiếp.', 28, 27)
    zooms.save(out/'tail-zoom.png')

    cross = sheet(1560, 880, 'Bank ID qua trang · Cluster label chỉ cục bộ',
                  'Các ID dưới đây lấy từ result đã lưu · đây là assignment thị giác, chưa phải tên nhân vật')
    d = ImageDraw.Draw(cross)
    cross_records = []
    for row_index, identity in enumerate((2, 5)):
        y = 135+row_index*355
        write(d, (25, y), f'BANK {identity}', 30, TEAL, True)
        for col, pn in enumerate((12, 13, 14)):
            im, pg, _ = load_page(pn)
            ci = next(i for i, value in enumerate(pg['character_ids']) if value == identity)
            k = pg['character_cluster_labels'][ci]
            x = 220+col*440
            place(cross, crop(im, pg['characters'][ci]), (x, y, 410, 273))
            write(d, (x+10, y+282), f'P{pn} / C{ci} / K{k} → ID {identity}', 23, TEAL, True)
            cross_records.append({'page': pn, 'character_index': ci, 'cluster': k, 'bank_id': identity})
    cross.save(out/'bank-cross-page.png')

    def bank_crop(relative):
        path = (bank_dir/relative).resolve()
        if not path.is_relative_to(bank_dir):
            raise ValueError('Crop outside bank directory')
        record(path)
        with Image.open(path) as original:
            return original.convert('RGB')

    identities = sorted(bank['characters'], key=int)
    pending_states = Counter(p.get('state', 'unknown') for p in bank['pending'].values())
    inventory = sheet(1560, 1300, 'Bank đã lưu · Character ID, crop và pending',
                      f'{len(identities)} character ID · {len(bank["pending"])} pending record · nhãn số khi display_name=null')
    d = ImageDraw.Draw(inventory)
    for i, identity in enumerate(identities):
        item = bank['characters'][identity]
        x, y = 20+(i % 7)*220, 130+(i//7)*300
        d.rounded_rectangle((x, y, x+208, y+282), radius=10, fill='white')
        if item['crop_paths']:
            place(inventory, bank_crop(item['crop_paths'][0]), (x+12, y+40, 184, 180))
        write(d, (x+12, y+7), 'ID '+identity, 25, TEAL, True)
        write(d, (x+12, y+223), f'{len(item["crop_paths"])} crop / {item["appearance_count"]} lần', 19, MUTED)
        write(d, (x+12, y+250), 'disabled='+str(item['disabled']), 18, MUTED)
    y = 1055
    write(d, (25, y), 'PENDING KHÔNG PHẢI CHARACTER ID', 27, ORANGE, True)
    lines(d, (25, y+47), ' · '.join(f'{state}: {count}' for state, count in sorted(pending_states.items()))+
          '\nclosed vẫn là record lưu lịch sử; không tự promotion thành ID.', 95, 25, MUTED)
    inventory.save(out/'bank-output.png')

    embedding_path = bank_dir/'embeddings/vectors.npz'
    with np.load(embedding_path, allow_pickle=False) as saved:
        vectors = {key: saved[key] for key in saved.files}
    embedding_summary = []
    for key, values in vectors.items():
        embedding_summary.append({'key': key, 'shape': list(values.shape), 'dtype': str(values.dtype)})
    proto = sheet(1680, 1510, 'Prototype bank · Vector đi cùng crop và source key',
                  'Mỗi ID dưới đây giữ 16 mẫu đã chọn · số p là chỉ số prototype, không phải detection index')
    d = ImageDraw.Draw(proto)
    for col, identity in enumerate((2, 5)):
        item = bank['characters'][str(identity)]
        key = 'character_'+str(identity)
        # Follow the actual storage key rather than assuming a spelling.
        matches = [k for k in vectors if k in (f'character_{identity}', f'character:{identity}')]
        if not matches:
            raise ValueError(f'Cannot find character embedding key for {identity}')
        key = matches[0]
        values = vectors[key]
        if len(values) != len(item['prototype_crop_paths']):
            raise ValueError('Vector/crop mapping differs')
        left = 22+col*835
        write(d, (left, 128), f'BANK {identity} · {values.shape} · {values.dtype}', 26, TEAL, True)
        for i, relative in enumerate(item['prototype_crop_paths']):
            x, y = left+(i % 4)*202, 180+(i//4)*245
            d.rounded_rectangle((x, y, x+190, y+231), radius=8, fill='white')
            place(proto, bank_crop(relative), (x+8, y+8, 174, 170))
            source_key = item['prototype_source_keys'][i]
            write(d, (x+9, y+179), f'p{i} · {source_key[:8] if source_key else "null"}', 20, MUTED)
        # Heatmap is a literal slice of saved vectors, not a projection or similarity score.
        sample = values[:, :64]
        limit = float(np.abs(sample).max()) or 1
        scaled = np.clip(sample/limit, -1, 1)
        rgb = np.empty((*scaled.shape, 3), dtype=np.uint8)
        rgb[:, :, 0] = 245-np.maximum(-scaled, 0)*180
        rgb[:, :, 1] = 245-np.abs(scaled)*160
        rgb[:, :, 2] = 245-np.maximum(scaled, 0)*180
        heatmap = Image.fromarray(rgb).resize((768, 192), Image.Resampling.NEAREST)
        proto.paste(heatmap, (left+12, 1200))
        write(d, (left+12, 1400), '16 hàng prototype × 64 chiều đầu · xanh âm / đỏ dương', 20, MUTED)
    proto.save(out/'bank-prototypes.png')

    # Match a pending record to an exported detection by the exact saved source key.
    pending_examples = []
    for pn, ci in ((8, 7), (12, 4)):
        im, pg, _ = load_page(pn)
        page_key = 'chapter-001/'+f'{pn:02}.webp'
        image_digest = hashlib.sha256(np.asarray(im).tobytes()).digest()
        source_key = hashlib.sha256(page_key.encode()+image_digest+
                                   np.asarray(pg['characters'][ci], dtype=np.float32).tobytes()).hexdigest()
        matches = [p for p in bank['pending'].values() if source_key in p.get('appearance_keys', [])]
        if len(matches) != 1:
            raise ValueError(f'Pending source key missing/ambiguous: P{pn} C{ci}')
        pending_examples.append({'page': pn, 'character_index': ci, 'cluster': pg['character_cluster_labels'][ci],
                                 'bank_id': pg['character_ids'][ci], 'source_key': source_key, 'record': matches[0]})
    pending_picture = sheet(1560, 770, 'Pending · Có detection và cluster, chưa có bank ID',
                            'Đối chiếu detection ↔ pending bằng source key chính xác; trạng thái lấy từ bank đã lưu')
    d = ImageDraw.Draw(pending_picture)
    for i, item in enumerate(pending_examples):
        left = 25+i*770
        pg = pages[item['page']]
        ci = item['character_index']
        place(pending_picture, crop(images[item['page']], pg['characters'][ci]), (left, 145, 310, 435))
        record_item = item['record']
        lines(d, (left+335, 155), f'P{item["page"]} / C{ci} / K{item["cluster"]}\ncharacter_id=null\n\npending {record_item["id"][:8]}…\nstate={record_item["state"]}\norigin={record_item["origin_page_key"]}\n\nsource key:\n{item["source_key"][:16]}…', 30, 24)
        place(pending_picture, bank_crop(record_item['crop_paths'][0]), (left+30, 595, 190, 132))
        write(d, (left+240, 628), 'Crop pending đã lưu', 24, ORANGE, True)
    pending_picture.save(out/'bank-pending.png')

    def save_json(name, value):
        (out/name).write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')

    save_json('magiv-pages.json', {'document': {k: v for k, v in raw.items() if k not in ('pages', 'texts')},
                                  'pages': [pages[k] for k in sorted(pages)],
                                  'texts': [t for k in sorted(texts) for t in texts[k]]})
    save_json('ocr-cases.json', case_records)
    save_json('bank-example.json', {'note': 'Saved bank from batch r4; not a fabricated before/after extraction trace.',
                                   'config': bank['config'], 'model_fingerprint': bank['model_fingerprint'],
                                   'characters': {k: bank['characters'][k] for k in ('2', '5')},
                                   'cross_page': cross_records, 'pending_examples': pending_examples,
                                   'character_count': len(identities), 'pending_count': len(bank['pending']),
                                   'pending_states': dict(pending_states), 'embedding_arrays': embedding_summary})
    record_file = {'kind': 'historical-extract-and-bank-evidence', 'source_run': str(run),
                   'bank_directory': str(bank_dir), 'pages': sorted(pages), 'model_requests': 0,
                   'note': 'Read saved artifacts only. OCR and assignments are historical predictions, not accuracy claims.',
                   'source_artifacts': [{'file': path, 'sha256': digest} for path, digest in sorted(receipts.items())],
                   'generator_sha256': sha(__file__),
                   'assets': {p.name: sha(p) for p in sorted(out.iterdir()) if p.is_file() and p.name != 'manifest.json'}}
    save_json('manifest.json', record_file)
    print(json.dumps({'output': str(out), 'assets': len(record_file['assets']), 'pages': sorted(pages),
                      'character_ids': len(identities), 'pending': len(bank['pending']), 'model_requests': 0}))


if __name__ == '__main__':
    main()
