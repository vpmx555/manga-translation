"""Per-page event envelopes, including silent panels and overflow bubbles."""
import base64
import io
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from ..storage.io import atomic_bytes, file_hash, fingerprint


def bounds(value):
    return value['bbox'] if isinstance(value, dict) else value


def intersects(a, b):
    return min(a[2], b[2]) > max(a[0], b[0]) and min(a[3], b[3]) > max(a[1], b[1])


def magi_tail_evidence(rows, pages):
    """Use MAGI's explicit text-tail links; never infer a tail from geometry."""
    by_page = {p['id']: p for p in pages}
    evidence = {}
    for row in rows:
        page = by_page[row['page_id']]
        linked = {}
        for source in row.get('source_boxes', []):
            ti = source.get('text_index')
            if type(ti) is not int:
                continue
            for text_index, tail_index in page.get('text_tail_associations', []):
                if text_index != ti:
                    continue
                if type(tail_index) is not int or not 0 <= tail_index < len(page.get('tails', [])):
                    raise ValueError('MAGI text-tail association references an unknown tail')
                tail = linked.setdefault(tail_index, {'tail_index': tail_index, 'label': 'TAIL '+str(tail_index),
                    'bbox': list(bounds(page['tails'][tail_index])), 'source_text_ids': []})
                uid = source.get('source_text_id', source.get('id'))
                if uid is not None and uid not in tail['source_text_ids']:
                    tail['source_text_ids'].append(uid)
        evidence[row['id']] = {'source': 'MAGIv2', 'page_id': row['page_id'],
            'coordinate_system': 'original_page_pixels', 'has_tail': bool(linked),
            'tails': [linked[i] for i in sorted(linked)]}
    return evidence


def page_regions(event_rows, all_rows, pages):
    tail_evidence = magi_tail_evidence(event_rows, pages)
    positions = {p['id']: i for i, p in enumerate(pages)}
    first, last = event_rows[0], event_rows[-1]
    start, end = positions[first['page_id']], positions[last['page_id']]
    if end < start:
        raise ValueError('Event pages are not in source reading order')
    result = []
    for pi in range(start, end+1):
        page = pages[pi]
        panels = page.get('panels', [])
        order = sorted(range(len(panels)), key=lambda i: (
            panels[i].get('reading_order', i) if isinstance(panels[i], dict) else i, i))
        a = order.index(first['panel_index']) if pi == start and first.get('panel_index') in order else 0
        b = order.index(last['panel_index']) if pi == end and last.get('panel_index') in order else len(order)-1
        if pi == start == end:
            a, b = min(a, b), max(a, b)
        selected = set(order[a:b+1])
        targets = [r for r in event_rows if r['page_id'] == page['id']]
        selected.update(i for r in targets for i in [r.get('panel_index'), *r.get('panel_indices', [])]
                        if type(i) is int and 0 <= i < len(panels))
        boxes = [bounds(panels[i]) for i in selected]
        for group in page.get('bubble_groups', []):
            if selected.intersection(group.get('panel_indices', [])) or any(
                    intersects(group['bbox'], bounds(panels[i])) for i in selected):
                boxes.append(group['bbox'])
        boxes.extend(s['bbox'] for r in targets for s in r.get('source_boxes', [r]))
        # A linked tail can extend outside its panel/bubble; retain its full bbox.
        boxes.extend(t['bbox'] for r in targets for t in tail_evidence[r['id']]['tails'])
        if not boxes:
            boxes = [[0, 0, page['width'], page['height']]]
        envelope = [max(0, math.floor(min(b[0] for b in boxes))),
                    max(0, math.floor(min(b[1] for b in boxes))),
                    min(page['width'], math.ceil(max(b[2] for b in boxes))),
                    min(page['height'], math.ceil(max(b[3] for b in boxes)))]
        context = [r['id'] for r in all_rows if r['page_id'] == page['id']
                   and r['id'] not in {t['id'] for t in event_rows}
                   and (r.get('panel_index') in selected or selected.intersection(r.get('panel_indices', []))
                        or intersects(r['bbox'], envelope))]
        result.append({'page_id': page['id'], 'page_index': pi, 'bbox': envelope,
                       'panel_indices': [i for i in order if i in selected],
                       'panels': [{'panel_index': i, 'reading_order': order.index(i)+1,
                                   'bbox': list(bounds(panels[i]))} for i in order if i in selected],
                       'tails': [{'tail_index': i, 'label': 'TAIL '+str(i), 'bbox': list(bounds(tail))}
                                 for i, tail in enumerate(page.get('tails', []))
                                 if intersects(bounds(tail), envelope)],
                       'context_ids': context})
    return result


def render(store, event, event_rows, all_rows, geometry):
    pages = geometry['pages']
    packets = page_regions(event_rows, all_rows, pages)
    directory = store.artifact_path('analyze').parent / 'images'
    maximum = store.manifest['config']['v6']['event_image_max_side']
    font = None
    for path in ('C:/Windows/Fonts/arial.ttf', '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'):
        if Path(path).exists():
            font = ImageFont.truetype(path, 20)
            break
    for packet in packets:
        page = pages[packet['page_index']]
        receipt = store.manifest['source']['images'][packet['page_index']]
        if file_hash(receipt['path']) != receipt['sha256']:
            raise ValueError('Event source image changed')
        key = fingerprint(['event-envelope-magi-tails-v7', packet, receipt['sha256'],
                           page.get('character_ids'), page.get('characters'), maximum])
        path = directory / (key + '.jpg')
        if not path.exists():
            with Image.open(receipt['path']) as source:
                image = source.convert('RGB')
            draw = ImageDraw.Draw(image)
            for panel in packet['panels']:
                i, box = panel['panel_index'], panel['bbox']
                draw.rectangle(tuple(box), outline=(220, 50, 40), width=3)
                draw.text((box[0]+3, box[1]+3), 'P'+str(page.get('page', packet['page_index']+1))+
                          ':'+str(i+1)+' / #'+str(panel['reading_order']), fill=(220, 50, 40),
                          font=font, stroke_width=1, stroke_fill='white')
            for i, char in enumerate(page.get('characters', [])):
                ids = page.get('character_ids', [])
                mid = ids[i] if i < len(ids) else None
                if mid is None or not intersects(bounds(char), packet['bbox']):
                    continue
                box = bounds(char)
                draw.rectangle(tuple(box), outline=(0, 140, 50), width=2)
                draw.text((box[0]+3, box[1]+25), 'MAGI '+str(mid), fill=(0, 110, 35),
                          font=font, stroke_width=2, stroke_fill='white')
            for tail in packet['tails']:
                box = tail['bbox']
                draw.rectangle(tuple(box), outline=(190, 30, 180), width=3)
                draw.text((box[0]+2, max(0, box[1]-23)), tail['label'], fill=(160, 20, 150),
                          font=font, stroke_width=1, stroke_fill='white')
            image = image.crop(tuple(packet['bbox']))
            original_size = image.size
            image.thumbnail((maximum, maximum))
            packet['render_size'] = list(image.size)
            packet['scale'] = image.width / original_size[0]
            data = io.BytesIO()
            image.save(data, format='JPEG', quality=90)
            atomic_bytes(path, data.getvalue())
        else:
            with Image.open(path) as image:
                packet['render_size'] = list(image.size)
                packet['scale'] = image.width / (packet['bbox'][2]-packet['bbox'][0])
        x0, y0, x1, y1 = packet['bbox']
        sx, sy = packet['render_size'][0]/(x1-x0), packet['render_size'][1]/(y1-y0)
        for tail in packet['tails']:
            a, b, c, d = tail['bbox']
            tail['render_bbox'] = [(a-x0)*sx, (b-y0)*sy, (c-x0)*sx, (d-y0)*sy]
        packet.update(file=path.relative_to(store.directory).as_posix(), retained_sha256=file_hash(path))
    return packets


def load_images(store, packets):
    output = []
    for packet in packets:
        path = (store.directory / packet['file']).resolve()
        if not path.is_relative_to(store.directory.resolve()) or file_hash(path) != packet['retained_sha256']:
            raise ValueError('Event image checksum mismatch')
        output.append(base64.b64encode(path.read_bytes()).decode('ascii'))
    return output
