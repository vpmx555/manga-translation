"""Build graph documentation figures and input exports from a saved chapter run."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import shutil

from PIL import Image, ImageDraw, ImageFont


COLORS = {'story': '#0f766e', 'chapter': '#2563eb', 'scene': '#7c3aed',
          'event': '#b45309', 'utterance': '#475569'}
INK, MUTED, LINE = '#172b45', '#52647a', '#a8b6c7'
STORY_DESCRIPTION = 'Toàn bộ truyện Iris Zero; các chapter cùng story_id được tổ chức dưới node này.'


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def font(size=22, bold=False):
    return ImageFont.truetype('C:/Windows/Fonts/' + ('arialbd.ttf' if bold else 'arial.ttf'), size)


class Canvas:
    def __init__(self, width, height, title, subtitle):
        self.image = Image.new('RGB', (width, height), '#edf2f7')
        self.draw = ImageDraw.Draw(self.image)
        self.text(30, 22, title, 30, True)
        self.text(30, 66, subtitle, 20, color=MUTED)

    def text(self, x, y, text, size=22, bold=False, color=INK):
        self.draw.text((x, y), text, font=font(size, bold), fill=color)

    def wrap(self, x, y, text, width, size=22, bold=False, color=INK, leading=None):
        face, line, lines = font(size, bold), '', []
        for word in text.split():
            attempt = (line + ' ' + word).strip()
            if line and self.draw.textlength(attempt, font=face) > width:
                lines.append(line)
                line = word
            else:
                line = attempt
        if line:
            lines.append(line)
        for line in lines:
            self.text(x, y, line, size, bold, color)
            y += leading or size + 9
        return y

    def box(self, xy, color=LINE, fill='white', radius=12, width=2):
        self.draw.rounded_rectangle(xy, radius=radius, outline=color, width=width, fill=fill)

    def arrow(self, points, color=LINE, width=3):
        self.draw.line(points, fill=color, width=width)
        (x0, y0), (x, y) = points[-2:]
        angle = math.atan2(y - y0, x - x0)
        self.draw.polygon([(x, y), (x - 11 * math.cos(angle - .45), y - 11 * math.sin(angle - .45)),
                           (x - 11 * math.cos(angle + .45), y - 11 * math.sin(angle + .45))], fill=color)

    def node(self, x, y, width, title, subtitle, kind, muted=False, height=80):
        color = '#94a3b8' if muted else COLORS[kind]
        self.box((x, y, x + width, y + height), color)
        self.text(x + 13, y + 10, title, 21, True, color)
        if height <= 80:
            display = subtitle
            if self.draw.textlength(display, font=font(16)) > width - 26:
                while display and self.draw.textlength(display + '…', font=font(16)) > width - 26:
                    display = display[:-1]
                display = display.rstrip() + '…'
            self.text(x + 13, y + 42, display, 16, color=MUTED)
        else:
            self.wrap(x + 13, y + 42, subtitle, width - 26, 16, color=MUTED)

    def save(self, output, name):
        self.image.save(output / name)


def name_figures(output, graph):
    normalization = graph['name_normalization']
    group = next(g for g in normalization['groups'] if g['group_id'] == 'g1')
    reference = graph['story_graph']['characters']['reference:' + group['group_id']]
    if not reference['reference_only'] or reference['magi_ids']:
        raise ValueError('Name example is not reference-only')
    repairs = [next(p for p in normalization['patches'] if p['original'] == name and p['status'] == 'applied')
               for name in ['TORU', 'MIZU- SHIMA', 'TORU-KUN']]
    c = Canvas(1500, 660, 'Một luồng tên gọi: sửa OCR → alias → nhân vật giả định',
               'Cùng request graph đề xuất name records và groups; code áp dụng sửa và tạo thực thể chỉ có tên.')
    stages = [('Tên trong transcript', 'TORU · MIZU- SHIMA · TORU-KUN', 'utterance'),
              ('Sửa đúng dạng gọi', 'Toru · Mizushima · Toru-kun', 'utterance'),
              ('Nhóm tên g1', 'canonical_name: Toru Mizushima', 'scene'),
              ('reference:g1', 'Nhân vật giả định ở mức tên', 'story')]
    for i, (title, subtitle, kind) in enumerate(stages):
        x = 30 + i * 372
        c.node(x, 185, 328, title, subtitle, kind, height=105)
        if i:
            c.arrow([(x - 44, 236), (x - 8, 236)], COLORS['story'])
    c.box((32, 350, 1468, 592), COLORS['chapter'])
    c.text(56, 370, 'Hai đầu ra liên quan trong cùng luồng', 25, True)
    c.wrap(56, 420, 'Text hiện hành: sửa spelling/spacing/hyphen, giữ cách gọi và honorific trong câu.', 1360, 24)
    c.wrap(56, 480, 'Alias/nhóm: giả định nhiều cách gọi cùng chỉ một người; canonical_name là metadata nhóm.', 1360, 24)
    c.text(56, 545, 'Chưa nối reference:g1 với bất kỳ nhân vật MAGIv2 nào.', 24, True, COLORS['story'])
    c.save(output, 'names-flow.png')
    c = Canvas(1500, 650, 'Sửa tên gọi khác với thay mọi tên bằng tên đầy đủ',
               'Ba phép sửa đã áp dụng trong r11; alias giữ các dạng nguồn, dạng sửa và tên chuẩn của nhóm.')
    for x, title in [(45, 'Tên nguồn'), (505, 'Text sau sửa OCR'), (1020, 'Nhóm giả định')]:
        c.text(x, 134, title, 25, True)
    for i, patch in enumerate(repairs):
        y = 195 + i * 125
        c.node(35, y, 365, patch['original'], patch['utterance_id'].split(':', 1)[1], 'utterance')
        c.arrow([(402, y + 40), (484, y + 40)], COLORS['chapter'])
        c.node(488, y, 365, patch['replacement'], 'Giữ dạng gọi, không mở rộng tên', 'chapter')
        c.arrow([(855, y + 40), (995, y + 40)], COLORS['scene'])
    c.box((1000, 195, 1465, 525), COLORS['scene'])
    c.text(1024, 217, group['canonical_name'], 28, True, COLORS['scene'])
    c.text(1024, 270, 'group_id: g1 · status: proposed', 22)
    c.wrap(1024, 320, 'Toru / Mizushima / Toru-kun là những dạng gọi được đề xuất cùng nhóm.', 405, 24)
    c.wrap(1024, 435, 'canonical_name chỉ là tên metadata, không chèn vào mọi câu.', 405, 21, color=MUTED)
    c.text(35, 595, 'Tên chưa chắc cùng người được giữ riêng hoặc chưa nhóm; chưa suy ra người nói từ một tên được nhắc.', 21, color=MUTED)
    c.save(output, 'name-corrections-aliases.png')
    c = Canvas(1500, 660, 'Nhân vật giả định chỉ có tên — chưa gán MAGI',
               'Node này là một giả định quy chiếu văn bản, bổ sung cho hierarchy truyện; không phải tầng narrative thứ sáu.')
    c.node(35, 215, 385, 'Nhóm g1 · aliases', 'Toru · Mizushima · Toru-kun', 'scene', height=100)
    c.arrow([(422, 265), (504, 265)], COLORS['story'])
    c.box((510, 140, 1465, 545), COLORS['story'], width=3)
    c.text(540, 166, reference['person_id'], 28, True, COLORS['story'])
    c.text(540, 215, 'name: ' + reference['name'], 27, True)
    c.text(540, 275, 'reference_only: true', 26)
    c.text(540, 321, 'magi_ids: []', 26)
    c.wrap(540, 390, 'Chưa có crop/MAGI được gán cho thực thể này. Việc ai nói câu nào vẫn cần analyze và bằng chứng độc lập.', 860, 26)
    c.text(35, 595, 'Không có cạnh từ node giả định sang bất kỳ MAGI ID nào. Các nhóm tên khác được giữ như những thực thể riêng.', 21, color=MUTED)
    c.save(output, 'name-reference-only.png')
    (output / 'name-flow.json').write_text(json.dumps({'group': group, 'reference': reference,
        'applied_corrections': repairs, 'groups': normalization['groups']}, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    run, output = args.run_dir.resolve(), args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    receipts = {}

    def load(relative):
        path = run / relative
        receipts[str(path)] = sha(path)
        return json.loads(path.read_text(encoding='utf-8-sig'))

    graph = load('05_graph/result.json')
    packet = load('05_graph/packet.json')
    ledger = load('05_graph/reference-ledger.json')
    normalized = load('04_normalize/result.json')
    manifest = load('manifest.json')
    scenes = graph['scenes']
    chapter = graph['story_graph']['chapter']
    rows = {r['id']: r for r in normalized['utterances']}
    forward = {uid: short for short, uid in ledger['rows'].items()}
    target_map = {row[0]: dict(zip(packet['target_columns'], row)) for row in packet['targets']}
    maximum = manifest['config']['v6']['analyze_batch_size']
    targets = [uid for s in scenes for e in s['events'] for uid in e['target_ids']]
    if targets != [ledger['rows'][row[0]] for row in packet['targets']]:
        raise ValueError('Frozen graph does not cover the packet exactly in source order')
    if any(len(e['target_ids']) > maximum for s in scenes for e in s['events']):
        raise ValueError('Saved event exceeds the configured event limit')
    utterance = next(r for r in rows.values() if r['id'].endswith(':p17:g7:u'))
    scene = next(s for s in scenes if utterance['id'] in s['target_ids'])
    event = next(e for e in scene['events'] if utterance['id'] in e['target_ids'])
    single_scene = next(s for s in scenes if s['scene_id'] == 'scene:12')
    single, = single_scene['events']
    event_count = sum(len(s['events']) for s in scenes)
    chapter_description = f"{chapter['chapter_label']} trong lượt graph này chứa {len(scenes)} scene, {event_count} event và {len(targets)} utterance."
    selected_rows = [target_map[forward[uid]] for uid in scene['target_ids']]
    short_id = lambda uid: uid.removeprefix(chapter['chapter_id'] + ':')

    # The full input stays lossless; only the human-readable transcript omits metadata.
    for source_name, target_name in [('packet.json', 'packet-full.json'),
                                     ('reference-ledger.json', 'reference-ledger.json'),
                                     ('ocr-current.json', 'ocr-current.json'),
                                     ('result.json', 'result.json'),
                                     ('replacement-history.json', 'replacement-history.json')]:
        path = run / '05_graph' / source_name
        receipts[str(path)] = sha(path)
        shutil.copyfile(path, output / target_name)
    transcript = ['TRANSCRIPT MAGI ĐÃ NORMALIZE — BẢN DỄ ĐỌC',
                  'Giữ nguyên text input; lược metadata, không có nhãn speaker được suy đoán.', '']
    for wire in packet['targets']:
        row = dict(zip(packet['target_columns'], wire))
        transcript.append(f"[{row['id']} | trang {row['page']} | panel {row['primary_panel']}] {row['text']}")
    (output / 'transcript-normalized.txt').write_text('\n'.join(transcript) + '\n', encoding='utf-8')
    selected = {'chapter': chapter, 'counts': {'scenes': len(scenes), 'events': event_count,
                'utterances': len(targets), 'name_groups': len(graph['name_normalization']['groups'])},
                'editorial_descriptions': {'story': STORY_DESCRIPTION, 'chapter': chapter_description},
                'scene': scene, 'event': event, 'utterance': utterance, 'single_event': single,
                'packet_rows': selected_rows, 'maximum_event_utterances': maximum,
                'note': 'Story/chapter descriptions are explanatory captions, not generated graph fields.'}
    (output / 'selected-nodes.json').write_text(json.dumps(selected, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

    c = Canvas(1600, 1020, 'Graph gồm 5 tầng — từ toàn truyện đến từng utterance',
               'Một node cha chứa nhiều node con; sơ đồ chọn vài nhánh để nhìn rõ cấu trúc.')
    ys = [135, 305, 475, 655, 835]
    names = [('story', '1. Truyện'), ('chapter', '2. Chapter'), ('scene', '3. Scene'),
             ('event', '4. Event'), ('utterance', '5. Utterance')]
    for y, (kind, name) in zip(ys, names):
        c.box((20, y - 14, 1580, y + 114), '#e1e8f0', '#f8fafc')
        c.text(36, y + 22, name, 23, True, COLORS[kind])
    # Edges first, so node boxes cover their ends cleanly.
    for a, b in [((750, 215), (750, 305)), ((750, 215), (1130, 305)), ((1430, 215), (1430, 305)),
                 ((750, 385), (360, 475)), ((750, 385), (790, 475)), ((750, 385), (1400, 475)),
                 ((360, 555), (360, 655)), ((790, 555), (670, 655)), ((790, 555), (930, 655)),
                 ((790, 555), (1190, 655)), ((1400, 555), (1440, 655))]:
        c.arrow([a, (a[0], (a[1] + b[1]) // 2), (b[0], (a[1] + b[1]) // 2), b])
    c.node(620, ys[0], 260, 'Iris Zero', 'story_id: iris-zero', 'story')
    c.node(1310, ys[0], 240, 'Truyện khác', 'Ví dụ cấu trúc', 'story', True)
    c.node(620, ys[1], 260, 'chapter-001', 'Chapter đang xử lý', 'chapter')
    c.node(1010, ys[1], 240, 'Chapter khác', 'Ví dụ cấu trúc', 'chapter', True)
    c.node(1310, ys[1], 240, 'Chapter minh họa', 'Thuộc truyện khác', 'chapter', True)
    lookup = {s['scene_id']: s for s in scenes}
    for x, sid in [(230, 'scene:7'), (660, scene['scene_id']), (1270, single_scene['scene_id'])]:
        c.node(x, ys[2], 260, sid, lookup[sid]['title'], 'scene')
    event_nodes = [(230, lookup['scene:7']['events'][0]),
                   *[(540 + i * 260, e) for i, e in enumerate(scene['events'])], (1320, single)]
    for x, e in event_nodes:
        label = e['event_id'].replace('scene:', 's').replace(':event:', ':e')
        c.node(x, ys[3], 235, label, f"{len(e['target_ids'])} utterance", 'event')
        for i, uid in enumerate(e['target_ids'][:2]):
            ux = x + i * 118
            c.arrow([(x + 117, ys[3] + 80), (x + 117, 810), (ux + 54, 810), (ux + 54, ys[4])])
            c.node(ux, ys[4], 109, forward[uid], 'text nguồn', 'utterance', height=74)
    c.text(34, 962, 'Node mờ: minh họa khả năng mở rộng. Node thật: trích r11; mỗi nhánh chỉ vẽ vài utterance.', 20, color=MUTED)
    c.save(output, 'five-level-hierarchy.png')

    # Nested overview: every saved scene/event/utterance is represented, without unreadable edge crossings.
    c = Canvas(1580, 1420, 'Output thật: Iris Zero · chapter-001 · graph r11',
               f'{len(scenes)} scene · {event_count} event · {len(targets)} utterance · {len(graph["name_normalization"]["groups"])} nhóm tên')
    c.box((24, 112, 1556, 1374), COLORS['story'], '#f4fbf9')
    c.text(46, 126, 'TRUYỆN  iris-zero', 22, True, COLORS['story'])
    c.box((42, 173, 1538, 1345), COLORS['chapter'], '#f5f8ff')
    c.text(62, 186, 'CHAPTER  chapter-001', 23, True, COLORS['chapter'])
    c.text(650, 190, 'Ô tím = scene  ·  Ô cam = event  ·  Mỗi chấm = 1 utterance', 19, color=MUTED)
    for i, s in enumerate(scenes):
        x, y = 62 + (i % 4) * 366, 237 + (i // 4) * 181
        c.box((x, y, x + 348, y + 165), COLORS['scene'])
        c.text(x + 12, y + 9, s['scene_id'], 20, True, COLORS['scene'])
        c.wrap(x + 12, y + 35, s['title'], 324, 18, True, leading=23)
        for j, e in enumerate(s['events']):
            ex, ey = x + 12 + j * 81, y + 88
            c.box((ex, ey, ex + 74, ey + 64), COLORS['event'], '#fff8ef', radius=6)
            c.text(ex + 7, ey + 4, 'e' + str(j + 1), 17, True, COLORS['event'])
            for k in range(len(e['target_ids'])):
                dx, dy = ex + 9 + (k % 4) * 15, ey + 34 + (k // 4) * 14
                c.draw.ellipse((dx, dy, dx + 7, dy + 7), fill=COLORS['utterance'])
    c.text(30, 1385, 'Các title giữ nguyên output model; số thứ tự eN có phạm vi trong scene. Đây chưa phải kết quả analyze.', 19, color=MUTED)
    c.save(output, 'output-chapter.png')

    def zoom(name, tier, breadcrumb, node_id, title, description, description_label,
             fields, children, footer):
        c = Canvas(1500, 740, 'Phóng node · ' + tier, breadcrumb)
        color = COLORS[name]
        c.box((30, 125, 960, 655), color, width=3)
        c.text(56, 148, node_id, 22, True, color)
        y = c.wrap(56, 192, title, 875, 34, True, color)
        c.text(56, y + 24, description_label, 20, True, MUTED)
        y = c.wrap(56, y + 61, description, 864, 26)
        for field in fields:
            y = c.wrap(56, y + 24, field, 864, 21, color=MUTED)
        c.text(1010, 133, 'Node liên kết', 22, True)
        for i, (ctitle, ctext, ckind, muted) in enumerate(children):
            yy = 190 + i * 108
            c.arrow([(962, 355), (984, 355), (984, yy + 40), (1008, yy + 40)], COLORS[ckind])
            c.node(1008, yy, 458, ctitle, ctext, ckind, muted)
        c.wrap(30, 681, footer, 1420, 20, color=MUTED)
        c.save(output, 'zoom-' + name + '.png')

    zoom('story', 'Tầng truyện', 'Toàn bộ truyện → nhiều chapter', 'story_id: ' + chapter['story_id'],
         'Iris Zero', STORY_DESCRIPTION, 'Description · diễn giải của tài liệu',
         ['Phạm vi: xuyên chapter, không chỉ chapter đang chạy.'],
         [('chapter-001', 'Chapter thật trong artifact r11', 'chapter', False),
          ('Chapter khác', 'Minh họa cấu trúc nhiều chapter', 'chapter', True)],
         'Tầng truyện được liên kết bằng chapter.story_id; artifact hiện tại không lưu một node story có description riêng.')
    zoom('chapter', 'Tầng chapter', 'iris-zero → ' + chapter['chapter_label'],
         'chapter_id: ' + chapter['chapter_id'], chapter['chapter_label'], chapter_description,
         'Description · diễn giải từ số liệu artifact', ['scene_ids: danh sách các scene theo thứ tự nguồn.'],
         [(s['scene_id'], s['title'], 'scene', False) for s in [lookup['scene:7'], scene, single_scene]],
         'Chỉ phóng ba scene tiêu biểu; chapter thật có 22 scene. Description trên là chú thích, không phải field model sinh.')
    zoom('scene', 'Tầng scene', 'iris-zero → chapter-001 → ' + scene['scene_id'], scene['scene_id'],
         scene['title'], scene['description'], 'description · nguyên văn output graph',
         [f"target_ids: {len(scene['target_ids'])} utterance · events: {len(scene['events'])}",
          'Một phân cảnh rộng; đổi panel/góc nhìn không tự tạo scene mới.'],
         [(e['event_id'], f"{e['title']} · {len(e['target_ids'])} utterance", 'event', False) for e in scene['events']],
         'Các phần Morning Gossip 1/2/3 vẫn thuộc cùng một scene; không phải ba lần thay đổi phân cảnh.')
    zoom('event', 'Tầng event', 'chapter-001 → ' + scene['scene_id'] + ' → ' + event['event_id'],
         event['event_id'], event['title'], event['description'], 'description · nguyên văn output graph',
         [f"target_ids: {len(event['target_ids'])} utterance liên tiếp · status: {event['status']}",
          'Một cụm trao đổi cùng chủ đề; ở đây là phần đầu của chuỗi Morning Gossip.'],
         [(forward[uid], rows[uid]['text'][:47] + ('…' if len(rows[uid]['text']) > 47 else ''), 'utterance', False)
          for uid in event['target_ids'][:3]] + [('Các utterance còn lại', '5 node nữa, vẫn theo source order', 'utterance', True)],
         'u83/u84/u85 là ID rút gọn của packet. Event chứa 8 node; hình chỉ phóng ba node đầu.')
    zoom('utterance', 'Tầng utterance', scene['scene_id'] + ' → ' + event['event_id'] + ' → ' + forward[utterance['id']],
         forward[utterance['id']] + ' ↔ ' + short_id(utterance['id']), 'Một đơn vị text sau normalize',
         target_map[forward[utterance['id']]]['text'], 'text · nguyên văn input cho graph',
         ['Diễn giải: một utterance ghép từ hai box trong cùng bubble đôi.',
          'Chưa có description semantic hay speaker do graph quyết định.'],
         [(short_id(uid), 'Text box nguồn trước normalize', 'utterance', False) for uid in utterance['source_text_ids']],
         'Các box nguồn bên phải là provenance, không phải tầng thứ sáu của graph. Một utterance không nhất thiết chỉ có một câu văn.')

    c = Canvas(1500, 790, 'Event đơn và event nối tiếp',
               'Ranh giới chủ đề và giới hạn xử lý là hai điều khác nhau; các ví dụ lấy nguyên từ r11.')
    c.box((28, 119, 1472, 328), COLORS['scene'])
    c.text(50, 139, single_scene['scene_id'] + ' · ' + single_scene['title'], 25, True, COLORS['scene'])
    c.text(50, 186, 'Event đơn', 25, True)
    c.node(360, 178, 620, single['event_id'], single['title'] + ' · 6 utterance', 'event', height=94)
    c.text(1020, 193, '6 ≤ 8', 29, True, COLORS['event'])
    c.text(1020, 239, 'Không cần phần tiếp theo.', 22, color=MUTED)
    c.box((28, 355, 1472, 704), COLORS['scene'])
    c.text(50, 374, scene['scene_id'] + ' · ' + scene['title'], 25, True, COLORS['scene'])
    c.text(50, 418, 'Cùng một cụm trao đổi dài: 18 utterance → 8 + 8 + 2', 25, True)
    for i, e in enumerate(scene['events']):
        x = 70 + i * 480
        eid = e['event_id'].replace('scene:', 's').replace(':event:', ':e')
        c.node(x, 476, 395, eid, e['title'], 'event', height=102)
        c.text(x + 14, 545, str(len(e['target_ids'])) + ' utterance', 20, True, COLORS['event'])
        if i:
            c.arrow([(x - 85, 527), (x - 9, 527)], COLORS['event'])
            c.text(x + 8, 600, 'continuation_of:', 20, True, MUTED)
            c.text(x + 8, 632, e['continuation_of'], 19, color=MUTED)
        else:
            c.text(x + 8, 600, 'Phần đầu của chuỗi.', 20, color=MUTED)
    c.text(30, 739, '≤ 8 utterance/event giữ batch analyze hữu hạn; các phần chạy nối tiếp trong scene và truyền memory.', 21, color=MUTED)
    c.save(output, 'event-continuations.png')

    name_figures(output, graph)
    source_file = Path(__file__).resolve().parents[1] / 'src/manga_pipeline/v6/graph.py'
    record = {'source_run': str(run), 'source_artifacts': receipts,
              'current_policy_source': {'path': str(source_file), 'sha256': sha(source_file)},
              'generator_sha256': sha(__file__), 'model_requests': 0,
              'editorial_notes': {'five_level_hierarchy': 'Conceptual story/chapter containers plus saved scene/event/utterance nodes.',
                                  'muted_nodes': 'Illustrative other stories/chapters, not extra processed data.',
                                  'descriptions': 'Only scene/event descriptions are quoted model descriptions; other captions are editorial.'},
              'assets': {p.name: sha(p) for p in sorted(output.iterdir()) if p.is_file() and p.name != 'manifest.json'}}
    (output / 'manifest.json').write_text(json.dumps(record, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'output': str(output), 'assets': len(record['assets']), 'scenes': len(scenes),
                      'events': event_count, 'utterances': len(targets), 'model_requests': 0}))


if __name__ == '__main__':
    main()
