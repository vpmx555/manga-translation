"""Render analyze input/output and scheduling figures from retained local artifacts."""
import argparse
import json
from pathlib import Path, PurePosixPath
import shutil

from PIL import Image, ImageOps

from build_v7_graph_examples import Canvas, COLORS, MUTED, INK, sha


SPEAKER, LISTENER, MEMORY = '#087f5b', '#2563eb', '#7c3aed'


def paste(c, image, box):
    x, y, width, height = box
    thumb = ImageOps.contain(image, (width, height), Image.Resampling.LANCZOS)
    c.image.paste(thumb, (x + (width - thumb.width) // 2, y + (height - thumb.height) // 2))


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

    def load(path):
        return json.loads(verify(path).read_text(encoding='utf-8-sig'))

    def export(name, value):
        (output / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

    manifest = load(run / 'manifest.json')
    analysis = load(run / '06_analyze/result.json')
    graph = load(run / '05_graph/result.json')
    normalized = load(run / '04_normalize/result.json')
    source_rows = {r['id']: r for r in normalized['utterances']}
    decisions = {r['utterance_id']: r for r in analysis['utterances']}
    packet = None
    for path in sorted((run / '06_analyze/packets').glob('*.json')):
        candidate = json.loads(path.read_text(encoding='utf-8-sig'))
        if candidate['event_id'] == 'scene:5:event:1':
            packet = load(path)
            break
    if packet is None:
        raise ValueError('Primary retained event packet missing')
    first, second = packet['targets'][:2]
    a, b = decisions[first['id']], decisions[second['id']]
    if a['speaker_person_id'] != b['addressee_person_ids'][0] or b['speaker_person_id'] != a['addressee_person_ids'][0]:
        raise ValueError('Primary example is not a reciprocal dialogue pair')
    people = packet['visual_people']['people']
    def magi(person_id):
        ids = people[person_id]['magi_ids']
        if len(ids) != 1:
            raise ValueError('Ambiguous display mapping')
        return 'MAGI ' + ids[0]
    speaker, listener = magi(a['speaker_person_id']), magi(a['addressee_person_ids'][0])
    images = []
    for record in packet['images']:
        saved = (run / record['file']).resolve()
        if not saved.is_relative_to(run):
            raise ValueError('Image outside run')
        saved = verify(saved, record['retained_sha256'])
        name = 'model-page-' + str(record['page_index'] + 1) + saved.suffix
        shutil.copyfile(saved, output / name)
        with Image.open(saved) as image:
            images.append(image.convert('RGB'))
    def source(page):
        receipt = manifest['source']['images'][page - 1]
        filename = PurePosixPath(receipt['path'].replace('\\', '/')).name
        path = verify(args.source_folder / filename, receipt['sha256'])
        with Image.open(path) as image:
            return image.convert('RGB'), path
    original, source_path = source(6)
    shutil.copyfile(source_path, output / ('source-page-6' + source_path.suffix))
    portraits = {}
    for pid in [a['speaker_person_id'], a['addressee_person_ids'][0]]:
        observations = [o for cluster in packet['visual_people']['clusters'].values() if cluster['person_id'] == pid
                        for o in cluster['observations'] if o['page_id'].endswith(':p6')]
        observation = min(observations, key=lambda o: o['bbox'][1])
        x1, y1, x2, y2 = observation['bbox']
        crop = original.crop((int(x1), int(y1), int(x2) + 1, int(y2) + 1))
        portraits[pid] = crop
        crop.save(output / ('portrait-' + magi(pid).lower().replace(' ', '-') + '.png'))
    export('input-packet.json', packet)
    export('dialogue-decisions.json', {'event_id': packet['event_id'], 'targets': [first, second],
        'decisions': [a, b], 'visual_people': packet['visual_people']})
    mention_example = next(row for row in analysis['utterances'] if row['utterance_id'].endswith(':p9:g6:u'))
    export('auxiliary-fields.json', {'dialogue': [a, b], 'mention_example': mention_example,
        'mention_text': source_rows[mention_example['utterance_id']]['text'],
        'note': 'Retained r11 outputs illustrate field values, not execution of the new auxiliary-first prompt.'})
    memory_scene = next(s for s in analysis['scenes'] if s['scene_id'] == 'scene:6')
    memory_events = memory_scene['events']
    memory_packets = {}
    for path in sorted((run / '06_analyze/packets').glob('*.json')):
        candidate = json.loads(path.read_text(encoding='utf-8-sig'))
        if candidate['event_id'] in {e['event_id'] for e in memory_events}:
            memory_packets[candidate['event_id']] = load(path)
    for event in memory_events:
        saved_input = memory_packets[event['event_id']]
        if saved_input['scene_memory'] != event['memory_input'] or saved_input['previous_event'] != event['previous_event']:
            raise ValueError('Retained input differs from memory event record')
    for previous, current in zip(memory_events, memory_events[1:]):
        if current['memory_input'] != previous['memory_output'] or current['previous_event']['description'] != previous['description']:
            raise ValueError('Memory handoff differs from retained event sequence')
    export('memory-events.json', memory_events)
    export('memory-inputs.json', [{key: p[key] for key in (
        'event_id', 'scene_id', 'scene_initial_description', 'scene_current_description',
        'previous_event', 'scene_memory')} for p in (memory_packets[e['event_id']] for e in memory_events)])
    failed = next(r for r in analysis['utterances'] if r['utterance_id'].endswith(':p17:g7:u'))
    failure = next(f for f in analysis['event_failures'] if f['event_id'] == failed['event_id'])
    export('unresolved-decision.json', {'decision': failed, 'event_failure': failure})

    c = Canvas(1500, 640, 'Graph dựng khung truyện; analyze tìm ai nói và nói với ai',
               'Cùng một câu thoại, analyze xác định người nói và người nhận lời. Ví dụ từ kết quả r11 đã lưu.')
    c.node(45, 185, 370, 'Graph: scene → event', 'Khung và tóm tắt tình huống', 'scene', height=105)
    c.arrow([(417, 235), (505, 235)], COLORS['scene'])
    c.node(510, 185, 480, 'Câu cần phân tích · p6:g1:u', 'NOW THAT YOU MENTION IT…', 'utterance', height=105)
    c.arrow([(992, 235), (1104, 235)], SPEAKER)
    c.node(1110, 185, 345, 'Speaker: ' + speaker, 'Người phát ngôn', 'story', height=105)
    c.arrow([(751, 292), (751, 430), (1104, 430)], LISTENER)
    c.node(1110, 380, 345, 'Addressee: ' + listener, 'Người được hướng lời tới', 'chapter', height=105)
    c.wrap(45, 500, 'Analyze giữ cách chia scene/event của graph. Nhãn MAGI giúp chỉ đúng nhân vật trên ảnh, chưa tự xác nhận tên thật.', 1400, 24)
    c.text(30, 580, 'Hình giải thích kết quả đã lưu, không phải bản ghi từng bước suy luận bên trong model.', 21, color=MUTED)
    c.save(output, 'role-overview.png')

    c = Canvas(1500, 750, 'Đầu vào: câu cần phân tích và bối cảnh của nó',
               'Trích scene:5:event:1. Model nhận cả phần chữ và ảnh trong một lần gọi.')
    c.box((30, 123, 1470, 310), COLORS['scene'])
    c.text(52, 143, 'Tóm tắt scene: phân cảnh này đang nói về điều gì?', 23, True, COLORS['scene'])
    c.wrap(52, 190, packet['scene_initial_description'], 1370, 25)
    for i, row in enumerate([first, second]):
        y = 350 + i * 165
        c.box((30, y, 1470, y + 140), COLORS['utterance'])
        c.text(53, y + 15, row['id'].split(':', 1)[1], 22, True)
        c.wrap(53, y + 60, row['text'], 1360, 27)
    c.text(30, 705, 'Hình phóng hai câu đầu; model vẫn nhận đủ 7 utterance của event.', 21, color=MUTED)
    c.save(output, 'input-transcript.png')
    c = Canvas(1500, 900, 'Input ảnh: các vùng trang thực sự đã gửi model',
               'Event đi từ trang 6 đến trang 8; giữ cả trang 7 trung gian, dù không có target của event.')
    for i, (record, image) in enumerate(zip(packet['images'], images)):
        x = 28 + i * 495
        c.box((x, 124, x + 454, 830))
        c.text(x + 15, 140, 'Trang ' + str(record['page_index'] + 1), 25, True)
        paste(c, image, (x + 10, 190, 434, 620))
    c.text(30, 855, 'Ảnh được sao chép đúng checksum. Khung panel/MAGI/TAIL là lớp nhãn đã có trong input lịch sử.', 21, color=MUTED)
    c.save(output, 'input-image-pages.png')

    c = Canvas(1500, 690, 'Đầu ra: câu này là ai nói, đang nói với ai?',
               'Các trường chính trong kết quả r11. File JSON đầy đủ giữ cả lý do và dữ liệu nguồn.')
    c.box((30, 128, 530, 600), COLORS['utterance'])
    c.text(55, 150, 'Utterance nguồn', 26, True)
    c.wrap(55, 205, first['text'], 445, 26)
    c.text(55, 385, 'id: p6:g1:u', 23)
    c.text(55, 435, 'Chưa xác định người nói/người nghe.', 22, color=MUTED)
    c.arrow([(532, 365), (620, 365)], SPEAKER)
    c.box((625, 128, 1465, 600), SPEAKER)
    c.text(652, 150, 'Quyết định r11 đã lưu', 26, True, SPEAKER)
    fields = [('speaker_kind', a['speaker_kind']), ('speaker_person_id', a['speaker_person_id']),
              ('addressee_type', a['addressee_type']), ('addressee_person_ids', json.dumps(a['addressee_person_ids'])),
              ('confidence.speaker', str(a['confidence']['speaker']) + '/10'),
              ('confidence.addressee', str(a['confidence']['addressee']) + '/10')]
    for i, (key, value) in enumerate(fields):
        y = 205 + i * 57
        c.text(652, y, key, 20, True, MUTED)
        c.text(995, y, value, 20)
    c.text(30, 640, 'MAGI 4/3 là nhãn dễ đọc của các person ID này trong packet; danh tính tên vẫn chưa được xác nhận.', 21, color=MUTED)
    c.save(output, 'output-before-after.png')

    def turn_figure(name, decision, target, title):
        c = Canvas(1500, 800, title, 'Crop nhân vật từ bbox quan sát đã lưu; câu thoại và các vai trò lấy từ output r11.')
        sp, ad = decision['speaker_person_id'], decision['addressee_person_ids'][0]
        for x, pid, role, color in [(30, sp, 'SPEAKER', SPEAKER), (1110, ad, 'ADDRESSEE', LISTENER)]:
            c.box((x, 130, x + 355, 670), color, width=3)
            c.text(x + 18, 148, role + ' · ' + magi(pid), 25, True, color)
            paste(c, portraits[pid], (x + 20, 210, 315, 430))
        c.box((440, 210, 1055, 570), COLORS['utterance'])
        c.text(465, 233, target['id'].split(':', 1)[1], 24, True)
        c.wrap(465, 300, target['text'], 555, 30, True)
        c.arrow([(387, 390), (434, 390)], SPEAKER)
        c.arrow([(1057, 390), (1104, 390)], LISTENER)
        c.text(457, 620, 'speaker: ' + str(decision['confidence']['speaker']) + '/10', 24, True, SPEAKER)
        c.text(755, 620, 'addressee: ' + str(decision['confidence']['addressee']) + '/10', 24, True, LISTENER)
        c.wrap(30, 710, 'Mũi tên diễn giải attribution đã lưu, không phải đường đuôi bubble. Hai quyết định được đánh giá riêng, không tự luân phiên theo câu.', 1420, 22, color=MUTED)
        c.save(output, name)
    turn_figure('speaker-detail.png', a, first, 'Câu đầu: MAGI 4 nói với MAGI 3')
    turn_figure('addressee-detail.png', b, second, 'Câu đáp: MAGI 3 trở thành speaker, MAGI 4 là addressee')

    c = Canvas(1500, 670, 'Đọc lời thoại trước, dùng ảnh để kiểm tra lại',
               'Đây là cách ưu tiên bằng chứng; model nhận cả chữ và ảnh trong một lần gọi.')
    stages = [('1. Đọc bối cảnh', 'Đọc các câu được gửi, tóm tắt scene, event trước và bộ ghi nhớ.', 'scene'),
              ('2. Chọn nhãn', 'Phân tích content_type, speech_style và vai trò từng tên được nhắc.', 'utterance'),
              ('3. Đặt giả thuyết', 'Dùng các nhãn để xét riêng người nói và người nhận lời.', 'event'),
              ('4. Đối chiếu', 'Kiểm tra nguồn và ảnh. Sửa nhãn hoặc vai trò nếu có mâu thuẫn.', 'chapter'),
              ('5. Ghi kết quả', 'Chốt nhãn, các vai trò, lý do và mức chắc chắn riêng.', 'story')]
    for i, (title, body, kind) in enumerate(stages):
        x = 25 + i * 298
        c.box((x, 170, x + 260, 490), COLORS[kind])
        c.text(x + 14, 193, title, 23, True, COLORS[kind])
        c.wrap(x + 14, 252, body, 232, 23)
        if i:
            c.arrow([(x - 38, 330), (x - 7, 330)], COLORS['utterance'])
    c.wrap(30, 525, 'Source anchor có sức nặng 8/10, không phải khóa cứng. Đổi lựa chọn cần bằng chứng rõ và source ID/MAGI label.', 1400, 23)
    c.text(30, 619, 'Các bước là hướng dẫn trong một lần gọi model; không thêm request hay thay schema output.', 21, color=MUTED)
    c.save(output, 'text-first-policy.png')

    c = Canvas(1500, 820, 'Chọn nhãn hỗ trợ trước, rồi dùng nhãn để xác định hai vai trò',
               'Phân tích từ text trước; những phần cần ảnh sẽ được kiểm tra và chốt sau. Các nhãn dưới đây là nhãn thật của schema.')
    cards = [('content_type', ['dialogue · thought · narration', 'sfx · action_state · ui', 'watermark · unknown'],
              'Phân biệt lời nói, suy nghĩ, lời dẫn hoặc chữ không phải giao tiếp; từ đó xét loại người nói/người nhận lời phù hợp.', 'scene'),
             ('speech_style', ['normal · whisper · shout', 'thought · caption · unknown'],
              'Hiểu cách biểu đạt. Caption có thể chứa thought; dấu chấm than không tự chứng minh shout hay danh tính người nói.', 'event'),
             ('mentions[].mention_type', ['direct_address · introduction', 'reference · unknown'],
              'Gọi trực tiếp gợi ý người nhận lời. Nhắc người thứ ba chưa xác định người nói hoặc người nhận lời.', 'chapter')]
    for i, (title, labels, body, kind) in enumerate(cards):
        x = 30 + i * 495
        c.box((x, 150, x + 455, 710), COLORS[kind])
        c.text(x + 20, 170, title, 23, True, COLORS[kind])
        for j, label in enumerate(labels):
            c.text(x + 20, 235 + j * 35, label, 21, True)
        c.wrap(x + 20, 405, body, 410, 24)
    c.text(30, 756, 'Các nhãn là kết quả phân tích cần đối chiếu; không tự trở thành bằng chứng độc lập hay một danh tính MAGI mới.', 21, color=MUTED)
    c.save(output, 'supporting-information.png')

    c = Canvas(1500, 820, 'Ví dụ các nhãn hỗ trợ trong kết quả đã lưu',
               'Ví dụ r11 minh họa giá trị từng field; không phải kiểm chứng model đã chạy theo prompt mới.')
    examples = [
        ('content_type = ' + a['content_type'], first['text'],
         'Đây là lời nói trong cuộc trao đổi: cần tìm người nói và người nhận lời.', 'scene', 'p6:g1:u'),
        ('speech_style = ' + b['speech_style'], second['text'],
         'Nhãn normal được lưu. Dấu hỏi/chấm than hoặc sự ngạc nhiên riêng lẻ không đủ để chốt shout.', 'event', 'p6:g2:u'),
        ('mention_type = ' + mention_example['mentions'][0]['mention_type'], source_rows[mention_example['utterance_id']]['text'],
         'Tên Koyuki được dùng để gọi trực tiếp: gợi ý người nhận lời, vẫn cần đối chiếu person ID với nguồn/ảnh.', 'chapter', 'p9:g6:u'),
    ]
    for i, (title, quote, use, kind, uid) in enumerate(examples):
        x = 30 + i * 495
        c.box((x, 145, x + 455, 705), COLORS[kind])
        y = c.wrap(x + 18, 166, title, 414, 23, True, COLORS[kind])
        c.text(x + 18, y + 12, uid, 19, color=MUTED)
        c.wrap(x + 18, 290, quote, 412, 25, True)
        c.wrap(x + 18, 465, use, 412, 23)
    c.text(30, 757, 'Hai câu trang 6 có mentions = []: không có tên được cấp để phân loại; không tự tạo một mention giả.', 21, color=MUTED)
    c.save(output, 'auxiliary-labels-example.png')

    c = Canvas(1500, 780, 'Luồng xử lý: scene song song, event trong scene nối tiếp',
               'Mỗi scene có bộ ghi nhớ riêng. Các event trong cùng scene phải chờ nhau để giữ mạch đối thoại.')
    c.node(35, 128, 1425, 'Bối cảnh chung của chapter', 'Các scene bắt đầu từ cùng bản graph đã chốt; ghi nhớ của scene này không chuyển sang scene khác.', 'chapter')
    for i, sid in enumerate(['scene:5', 'scene:6', 'scene:12']):
        y = 265 + i * 140
        gs = next(s for s in graph['scenes'] if s['scene_id'] == sid)
        c.text(40, y + 25, sid, 26, True, COLORS['scene'])
        for j, e in enumerate(gs['events']):
            x = 235 + j * 417
            c.node(x, y, 362, e['event_id'], str(len(e['target_ids'])) + ' utterance', 'event')
            if j:
                c.arrow([(x - 55, y + 40), (x - 7, y + 40)], MEMORY)
    c.text(30, 714, 'Event sau nhận tóm tắt scene, tóm tắt event trước và bộ ghi nhớ hợp lệ gần nhất của cùng scene.', 21, color=MUTED)
    c.save(output, 'scene-scheduling.png')

    c = Canvas(1650, 1400, 'Mỗi event biết scene đang diễn ra gì và event trước vừa xảy ra gì',
               'Ví dụ scene:6 r11. Câu tiếng Việt là diễn giải rút gọn; packet và kết quả tiếng Anh được giữ nguyên trong JSON.')
    scene_notes = [
        'Koyuki đến chỗ Toru trong lớp và tỏ tình, khiến Toru và cả lớp bất ngờ.',
        'Koyuki đến chỗ Toru trong lớp và tỏ tình, khiến Toru và cả lớp bất ngờ.',
        "Koyuki tỏ tình với Toru. Cả lớp xôn xao vì cô chọn Toru, một người không có Iris.",
    ]
    event_notes = [
        'Koyuki tỏ tình với Toru; cả lớp phản ứng bất ngờ.',
        'Các bạn trong lớp liên tục hỏi và bàn tán về lời tỏ tình.',
        'Koyuki nói lớn lời tỏ tình; Toru phản ứng sốc và bối rối.',
    ]
    for i, e in enumerate(memory_events):
        p = memory_packets[e['event_id']]
        x = 30 + i * 550
        c.box((x, 130, x + 490, 1295), MEMORY)
        c.text(x + 18, 151, 'EVENT ' + str(i + 1), 28, True, MEMORY)
        c.text(x + 18, 192, e['event_id'], 20, color=MUTED)
        c.text(x + 18, 244, 'ĐẦU VÀO CỦA EVENT', 24, True)
        c.box((x + 14, 294, x + 476, 490), LISTENER, '#f5f8ff')
        c.text(x + 30, 308, '1. Tóm tắt scene hiện tại', 23, True, LISTENER)
        c.text(x + 30, 344, 'scene_current_description', 17, color=MUTED)
        c.wrap(x + 30, 382, scene_notes[i], 427, 23)
        c.box((x + 14, 514, x + 476, 700), '#b45309', '#fff8ef')
        c.text(x + 30, 528, '2. Tóm tắt event trước', 23, True, '#b45309')
        c.text(x + 30, 564, 'previous_event.description', 17, color=MUTED)
        c.wrap(x + 30, 601, event_notes[i - 1] if p['previous_event'] else 'Chưa có: đây là event đầu tiên.', 427, 23)
        c.box((x + 14, 724, x + 476, 904), MEMORY, '#faf5ff')
        c.text(x + 30, 738, '3. Thông tin đã ghi nhớ', 23, True, MEMORY)
        c.text(x + 30, 774, 'scene_memory', 17, color=MUTED)
        memory_note = ('Chưa có ghi nhớ riêng của scene.' if not p['scene_memory']['facts'] else
                       'Đã có 3 ý: lời tỏ tình, phản ứng của lớp và việc Toru không có Iris.')
        c.wrap(x + 30, 810, memory_note, 427, 23)
        c.text(x + 18, 938, 'SAU KHI XỬ LÝ EVENT', 24, True, SPEAKER)
        c.wrap(x + 18, 984, event_notes[i], 449, 24)
        fact = e['memory_output']['facts'][0]
        c.wrap(x + 18, 1102, 'Cập nhật tóm tắt scene và bộ ghi nhớ (' + str(len(e['memory_output']['facts'])) + ' ý), kèm nguồn tham chiếu.', 449, 23)
        c.wrap(x + 18, 1204, 'Ý về lời tỏ tình có ' + str(len(fact['source_ids'])) + ' nguồn; độ chắc chắn ' + str(fact['confidence']) + '/10.', 449, 20, color=MUTED)
        if i < len(memory_events) - 1:
            c.arrow([(x + 491, 1255), (x + 520, 1255), (x + 520, 273), (x + 544, 273)], MEMORY)
    c.wrap(30, 1323, 'Mũi tên: tóm tắt và ghi nhớ vừa cập nhật trở thành đầu vào cho event sau. Câu thoại và ảnh của event hiện tại vẫn được gửi riêng cùng các phần này.', 1570, 22, color=MUTED)
    c.save(output, 'event-memory.png')

    failure_source, _ = source(17)
    bbox = source_rows[failed['utterance_id']]['bbox']
    x1, y1, x2, y2 = bbox
    crop = failure_source.crop((int(x1) - 30, int(y1) - 30, int(x2) + 30, int(y2) + 30))
    c = Canvas(1500, 720, 'Ví dụ phụ: chưa có attribution được chấp nhận',
               'Bubble đôi p17:g7:u trong r11: event trả output không hoàn chỉnh; semantic response bị từ chối.')
    paste(c, crop, (30, 132, 400, 500))
    c.box((480, 135, 1465, 605), '#b45309')
    c.text(510, 158, 'Fallback nguồn — không phải quyết định thành công', 27, True, '#b45309')
    values = [('speaker_kind', failed['speaker_kind']), ('speaker_person_id', 'null'),
              ('addressee_type', failed['addressee_type']), ('addressee_person_ids', '[]'),
              ('confidence speaker / addressee', '0 / 0')]
    for i, (key, value) in enumerate(values):
        c.text(510, 235 + i * 58, key + ': ' + value, 25)
    c.wrap(510, 540, failed['evidence'], 910, 21, color=MUTED)
    c.text(30, 660, 'Phân biệt lỗi response với attribution có confidence thấp. Event sau dùng memory hợp lệ gần nhất và biết khoảng thiếu.', 21, color=MUTED)
    c.save(output, 'unresolved-example.png')

    prompt_source = Path(__file__).resolve().parents[1] / 'src/manga_pipeline/v6/event_analysis.py'
    record = {'source_run': str(run), 'source_artifacts': receipts, 'generator_sha256': sha(__file__),
              'drawing_helper_sha256': sha(Path(__file__).with_name('build_v7_graph_examples.py')),
              'current_prompt_source': {'path': str(prompt_source), 'sha256': sha(prompt_source)},
              'model_requests': 0, 'note': 'Historical decisions are retained verbatim. Policy diagrams are editorial explanations of the current policy.',
              'assets': {p.name: sha(p) for p in sorted(output.iterdir()) if p.is_file() and p.name != 'manifest.json'}}
    export('manifest.json', record)
    print(json.dumps({'output': str(output), 'assets': len(record['assets']), 'figures': len(list(output.glob('*.png'))), 'model_requests': 0}))


if __name__ == '__main__':
    main()
