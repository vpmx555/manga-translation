"""Render rejected name-binding responses without accepting or altering them."""
import argparse
import html
import json
import os
from pathlib import Path
import sys
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
from manga_pipeline.v6.identity_binding import validate


def local(path):
    value = str(Path(path).resolve())
    return Path('\\\\?\\' + value) if os.name == 'nt' and not value.startswith('\\\\?\\') else Path(value)


def read(path):
    return json.loads(local(path).read_text(encoding='utf-8'))


def checks(decision, occurrence, request):
    """Exhaustive diagnostic checks for v1; the official validator still owns acceptance."""
    refs = set(decision.get('evidence_refs', []))
    crops = {c['crop_id']: c for c in request['crops']}
    allowed_refs = set(crops) | {c['id'] for o in request['occurrences'] for c in o['context']}
    errors = []

    def require(ok, message):
        if not ok:
            errors.append(message)

    owner, caller, kind = (decision.get(k) for k in ('owner_magi_id', 'speaker_magi_id', 'evidence_kind'))
    require(bool(str(decision.get('reason', '')).strip()), 'Thiếu lý do.')
    require(refs.issubset(allowed_refs), 'Có reference không tồn tại trong packet.')
    if decision.get('status') != 'confirmed':
        require(owner is None, 'Chưa xác nhận nhưng vẫn gán owner.')
        require(caller is None, 'Chưa xác nhận nhưng vẫn gán caller.')
        require(kind == 'unknown', 'Chưa xác nhận nhưng evidence_kind không phải unknown.')
        return errors
    bank = lambda mid: [k for k, c in crops.items() if c.get('kind') == 'bank' and c['magi_id'] == mid]
    panel = lambda mid: [k for k, c in crops.items() if k in occurrence['panel_refs'] and
                        any(a['magi_id'] == mid for a in c.get('annotations', []))]
    visible = owner in occurrence['visible_magi_ids']
    require(owner in occurrence['allowed_magi_ids'] and owner in request['candidates'],
            'Owner ngoài danh sách ứng viên của occurrence.')
    require(occurrence['utterance_id'] in refs, 'Thiếu reference câu nguồn: ' + occurrence['utterance_id'])
    require(bool(refs.intersection(bank(owner))), 'Thiếu reference bank crop của owner MAGI ' + str(owner))
    if visible:
        require(bool(refs.intersection(panel(owner))), 'Thiếu reference panel có nhãn owner MAGI ' + str(owner))
    if kind == 'direct_address':
        require(occurrence['mention_type'] == 'direct_address' and occurrence['content_type'] == 'dialogue',
                'Gọi trực tiếp không khớp mention_type/content_type từ analyze.')
        require(visible or owner == occurrence['off_panel_magi_id'], 'Owner ngoài panel không có listener cá nhân đã xác nhận.')
    elif kind == 'self_introduction':
        require(occurrence['mention_type'] == 'introduction' and occurrence['content_type'] == 'dialogue' and visible,
                'Tự giới thiệu cần introduction/dialogue và owner hiện diện.')
        person = request['candidates'].get(owner, {}).get('person_id')
        require(not occurrence['analysis_speaker_id'] or occurrence['analysis_speaker_id'] == person,
                'Tự giới thiệu mâu thuẫn speaker đã biết.')
        require(caller in (None, owner), 'Tự giới thiệu không được gán caller khác owner.')
    elif kind == 'narrator_introduction':
        require((occurrence['content_type'] == 'narration' or occurrence['speaker_kind'] == 'narrator') and visible,
                'Narrator introduction cần lời dẫn truyện và owner hiện diện; đây không phải lời giới thiệu hợp lệ của narrator.')
        require(caller is None, 'Narrator introduction không được có caller nhân vật.')
    else:
        require(False, 'Confirmed nhưng evidence_kind không được hỗ trợ.')
    if caller is not None:
        require(caller in occurrence['visible_magi_ids'] and caller in request['candidates'],
                'Caller không thuộc nhân vật hiện diện/ứng viên.')
        require(bool(refs.intersection(bank(caller))), 'Thiếu reference bank crop của caller MAGI ' + str(caller))
        require(bool(refs.intersection(panel(caller))), 'Thiếu reference panel của caller MAGI ' + str(caller))
    return errors


def generate_failure_review(run, output, stage_dir=None):
    run, output = Path(run).resolve(), Path(output).resolve()
    if stage_dir is None:
        stage_dir = next(iter(run.glob('*_build_identity')), run / '07_build_identity')
    directory = Path(stage_dir) / 'name-binding'
    result_path = directory / 'result.json'
    if local(result_path).exists() and read(result_path).get('policy') == 'analyze-authoritative-v3':
        return authoritative_review(output, read(result_path))
    manifest_path = run / 'manifest.json'
    if (manifest_path.exists() and read(manifest_path).get('config', {}).get('v6', {}).get(
            'identity_name_binding_policy') == 'chapter-name-binding-simple-v2'):
        return simplified_review(run, output, directory)
    packets = {}
    for file in (directory / 'packed').glob('*.json'):
        value = read(file)
        request = value['request']
        packets[tuple(sorted(o['id'] for o in request['occurrences']))] = (request, file.stem)
    results = []
    for file in (directory / 'requests').glob('*.json'):
        journal = read(file)
        response = journal.get('response')
        if not response or not isinstance(response.get('decisions'), list):
            continue
        key = tuple(sorted(d.get('occurrence_id', '') for d in response['decisions']))
        if key not in packets:
            continue
        request, decision_id = packets[key]
        receipt_file = directory / 'decisions' / (decision_id + '.json')
        receipt = read(receipt_file).get('record', {}) if local(receipt_file).exists() else {}
        if receipt.get('status') == 'completed':
            continue
        try:
            validate(response, request)
            first_error = 'Validator hiện tại chấp nhận response; xem receipt lịch sử.'
        except Exception as exc:
            first_error = str(exc)
        occurrences = {o['id']: o for o in request['occurrences']}
        rows = []
        for decision in response['decisions']:
            occurrence = occurrences[decision['occurrence_id']]
            images = []
            # All images actually supplied for this child request are available for review.
            for crop in request['crops']:
                path = (run / crop['file']).resolve()
                path.relative_to(run)
                images.append({**crop, 'url': quote(Path(os.path.relpath(path, output)).as_posix(), safe='/:'),
                               'available': local(path).exists(), 'cited': crop['crop_id'] in decision['evidence_refs']})
            rows.append({'occurrence': occurrence, 'model_decision': decision,
                         'diagnostic_errors': checks(decision, occurrence, request), 'images': images})
        results.append({'name': request['canonical_name'], 'aliases': request['aliases'],
            'request_id': journal['request_id'], 'receipt_id': decision_id,
            'receipt_status': receipt.get('status', 'pending'), 'receipt_error': receipt.get('error'),
            'validator_first_error': first_error, 'group_status': response['group_status'],
            'group_reason': response['group_reason'], 'rows': rows, 'raw_response': response})
    results.sort(key=lambda r: (r['name'], r['request_id']))
    payload = {'run_id': run.name, 'warning': 'Raw model output bị từ chối; không phải identity đã xác nhận.',
               'requests': results, 'request_count': len(results), 'decision_count': sum(len(r['rows']) for r in results)}
    output.mkdir(parents=True, exist_ok=True)
    local(output / 'errors.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    esc = lambda v: html.escape(str(v if v is not None else '—'))
    pretty = lambda v: esc(json.dumps(v, ensure_ascii=False, indent=2))
    sections, markdown = [], ['# Review toàn bộ response name-binding bị từ chối', '', payload['warning'], '',
                             f"Run {run.name}: {payload['request_count']} request, {payload['decision_count']} decision.", '',
                             '[HTML có ảnh](errors.html) · [JSON đầy đủ](errors.json)', '']
    for i, result in enumerate(results, 1):
        content = [f'<section id="r{i}"><h2>{i}. {esc(result["name"])}</h2>',
                   f'<p>Alias: {esc(", ".join(result["aliases"]))}</p>',
                   f'<p class="bad">Receipt: {esc(result["receipt_status"])} · {esc(result["receipt_error"])}</p>',
                   f'<p><b>Lỗi đầu tiên khi replay validator hiện tại:</b> {esc(result["validator_first_error"])}</p>',
                   f'<p><b>Nhận định nhóm của model:</b> {esc(result["group_status"])} — {esc(result["group_reason"])}</p>']
        markdown += [f'## {i}. {result["name"]}', '', f'Request: `{result["request_id"]}`', '',
                     f'Lỗi receipt: {result["receipt_error"]}', '', f'Lý do nhóm của model: {result["group_reason"]}', '']
        for j, row in enumerate(result['rows'], 1):
            o, d, errors = row['occurrence'], row['model_decision'], row['diagnostic_errors']
            content += [f'<article><h3>{i}.{j} · {esc(o["surface"])}</h3><blockquote>{esc(o["text"])}</blockquote>',
                f'<p><b>Model trả:</b> {esc(d["status"])} · Owner MAGI {esc(d["owner_magi_id"])} · Caller MAGI {esc(d["speaker_magi_id"])} · {esc(d["evidence_kind"])}</p>',
                f'<p><b>Lý do nguyên văn:</b> {esc(d["reason"])}</p>',
                f'<p><b>Analyze:</b> {esc(o["content_type"])} / {esc(o["mention_type"])} / {esc(o["speaker_kind"])}<br>',
                f'<b>MAGI cho phép:</b> {esc(o["allowed_magi_ids"])} · <b>Hiện diện:</b> {esc(o["visible_magi_ids"])} · <b>Listener ngoài panel:</b> {esc(o["off_panel_magi_id"])}</p>',
                '<h4>Tất cả điều kiện không đạt</h4><ul>' + ''.join(f'<li>{esc(e)}</li>' for e in errors) + '</ul>' if errors else
                '<p>Không phát hiện lỗi ở decision riêng này; request vẫn bị loại vì decision khác hoặc receipt chưa hoàn tất.</p>',
                f'<details open><summary>Reference model trích dẫn / câu nguồn bắt buộc</summary><pre>{pretty({"source_required": o["utterance_id"], "cited": d["evidence_refs"]})}</pre></details>',
                f'<details><summary>Context thoại được cung cấp</summary><pre>{pretty(o["context"])}</pre></details>',
                '<h4>Ảnh thực sự gửi trong request</h4><p>Viền xanh: model đã cite ảnh. Bấm ảnh để phóng to. Các ảnh còn lại cũng có trong packet.</p><div class="images">']
            for image in row['images']:
                label = 'Panel có nhãn MAGI' if image['kind'] == 'panel' else 'Bank MAGI ' + str(image['magi_id'])
                content.append(f'<figure class="{"cited" if image["cited"] else ""}"><img loading="lazy" src="{esc(image["url"])}" alt="{esc(label)}" onclick="zoom(this.src)"><figcaption>{esc(label)} · {"đã cite" if image["cited"] else "chưa cite"}<br>{esc(image["crop_id"])}</figcaption></figure>')
            content += ['</div></article>']
            markdown += [f'### {i}.{j} — {o["surface"]}', '', f'> {o["text"]}', '',
                f'Model: {d["status"]}; owner MAGI {d["owner_magi_id"]}; caller MAGI {d["speaker_magi_id"]}; {d["evidence_kind"]}.', '',
                'Lý do nguyên văn: ' + d['reason'], '',
                f'Analyze: {o["content_type"]}/{o["mention_type"]}; MAGI cho phép: {o["allowed_magi_ids"]}.', '',
                *['- '+e for e in errors], '', 'References model đã cite: '+json.dumps(d['evidence_refs'], ensure_ascii=False), '']
        content += [f'<details><summary>Raw JSON nguyên văn của toàn response</summary><pre>{pretty(result["raw_response"])}</pre></details></section>']
        markdown += ['```json', json.dumps(result['raw_response'], ensure_ascii=False, indent=2), '```', '']
        sections.append('\n'.join(content))
    navigation = ' · '.join(f'<a href="#r{i}">{i}. {esc(r["name"])}</a>' for i, r in enumerate(results, 1))
    document = '<!doctype html><html lang="vi"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Review tất cả name-binding lỗi</title>' + STYLE
    document += f'<header><h1>Response bị validator từ chối</h1><p class="bad">{esc(payload["warning"])}</p><p>{esc(run.name)} · {len(results)} request · {payload["decision_count"]} decision</p><p><a href="index.html">Identity / relationship</a> · <a href="errors.md">Markdown đầy đủ</a> · <a href="errors.json">JSON cho agent</a></p><nav>{navigation}</nav></header><main>'
    document += ''.join(sections) + '</main><dialog id="zoom"><button onclick="this.parentNode.close()">Đóng ảnh</button><br><img id="large"></dialog><script>function zoom(src){document.getElementById("large").src=src;document.getElementById("zoom").showModal()}</script></html>'
    local(output / 'errors.html').write_text(document, encoding='utf-8')
    local(output / 'errors.md').write_text('\n'.join(markdown)+'\n', encoding='utf-8')
    return payload


def simplified_review(run, output, directory):
    """Review stored per-item warnings without replaying historical proof rules."""
    output.mkdir(parents=True, exist_ok=True)
    results, sections = [], []
    esc = lambda value: html.escape(str(value))
    pretty = lambda value: esc(json.dumps(value, ensure_ascii=False, indent=2))
    for file in sorted((directory / 'decisions').glob('*.json')):
        record = read(file).get('record', {})
        decision = record.get('result', {})
        if record.get('status') == 'completed' and not decision.get('reviews'):
            continue
        packet_path = directory / 'packed' / file.name
        packet = read(packet_path).get('request', {}) if local(packet_path).exists() else {}
        images = []
        for crop in packet.get('crops', []):
            path = run / crop['file']
            images.append({'crop_id': crop['crop_id'], 'kind': crop.get('kind', 'bank'),
                'magi_id': crop.get('magi_id'), 'available': local(path).exists(),
                'url': quote(Path(os.path.relpath(path, output)).as_posix(), safe='/:'),
                'source_role': 'supplied_input'})
        entry = {'request_id': record.get('batch_id'), 'status': record.get('status'),
                 'error': record.get('error'), 'characters': decision.get('characters', []),
                 'reviews': decision.get('reviews', []), 'images': images,
                 'occurrences': packet.get('occurrences', []),
                 'minimum_chapter_crops': packet.get('minimum_chapter_crops'),
                 'candidates': packet.get('candidates', {})}
        results.append(entry)
        sections.append('<section><h2>'+esc(entry['request_id'])+'</h2><p>'+esc(entry['status'])+
                        '</p><pre>'+pretty({k: v for k, v in entry.items() if k != 'images'})+'</pre>'+
                        '<h3>Ảnh đầu vào được cung cấp — không phải ảnh model đã trích dẫn</h3><div class="images">'+
                        ''.join('<figure><img loading="lazy" src="'+esc(i['url'])+'"><figcaption>'+esc(i['crop_id'])+
                                '</figcaption></figure>' for i in images if i['available'])+'</div></section>')
    payload = {'version': 'structural-name-binding-review-v2', 'request_count': len(results),
               'decision_count': sum(len(r['characters']) for r in results), 'results': results,
               'warning': 'Kết quả hợp lệ được giữ lại. Lỗi từng mục được lưu để review; không replay validator bằng chứng cũ.'}
    local(output / 'errors.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    local(output / 'errors.md').write_text('# Review identity — lỗi và mục cần xem\n\n'+payload['warning']+
        '\n\n[HTML](errors.html) · [JSON](errors.json)\n\n```json\n'+json.dumps(payload, ensure_ascii=False, indent=2)+'\n```\n', encoding='utf-8')
    document = '<!doctype html><html lang="vi"><meta charset="utf-8"><title>Identity cần review</title>'+STYLE
    document += ('<header><h1>Identity: lỗi và mục cần review</h1><p>'+esc(payload['warning'])+
                 '</p><a href="index.html">Identity / relationship</a></header><main>'+''.join(sections)+'</main></html>')
    local(output / 'errors.html').write_text(document, encoding='utf-8')
    return payload


def authoritative_review(output, result):
    """Render code registrations, including conflicts and below-threshold candidates."""
    output.mkdir(parents=True, exist_ok=True)
    selections = result.get('selections', [])
    payload = {'version': 'analyze-authoritative-review-v3', 'request_count': 0,
               'decision_count': len(selections), 'results': selections,
               'reviews': result.get('needs_review', []),
               'warning': 'Đăng ký từ analyze bằng code; không có request gán lại owner. '
                          'Hiển thị cả ứng viên chưa đạt gate và các nhóm tên cạnh tranh.'}
    serialized = json.dumps(payload, ensure_ascii=False, indent=2)
    local(output / 'errors.json').write_text(serialized, encoding='utf-8')
    local(output / 'errors.md').write_text('# Review đăng ký identity\n\n' + payload['warning'] +
        '\n\n[HTML](errors.html) · [JSON](errors.json)\n\n```json\n' + serialized + '\n```\n', encoding='utf-8')
    local(output / 'errors.html').write_text('<!doctype html><html lang="vi"><meta charset="utf-8">' +
        '<title>Review đăng ký identity</title>' + STYLE + '<header><h1>Review đăng ký identity</h1><p>' +
        html.escape(payload['warning']) + '</p><a href="index.html">Identity / relationship</a></header>' +
        '<main><pre>' + html.escape(serialized) + '</pre></main></html>', encoding='utf-8')
    return payload


STYLE = '''<style>body{font:16px system-ui;background:#101827;color:#e5edf8;margin:0}header,main{padding:24px 4vw}header{background:#17253b}a{color:#8bd0ff}nav{line-height:2}section{border-top:3px solid #52718f;margin:25px 0;padding-top:15px}article{background:#19283e;border:1px solid #3a526f;border-radius:12px;padding:20px;margin:20px 0}p,li{line-height:1.7}.bad{color:#ffb6a8}blockquote{border-left:4px solid #7fbde3;padding:14px;background:#243951;margin-left:0}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:13px}.images{display:flex;gap:12px;flex-wrap:wrap}figure{margin:8px 0;border:2px solid #526078;padding:8px;border-radius:8px}.cited{border-color:#54e4ad}figure img{height:210px;max-width:100%;object-fit:contain;cursor:zoom-in;background:white}figcaption{max-width:360px;font-size:12px;overflow-wrap:anywhere}details{margin:12px 0}dialog{background:#17253b;color:white;max-width:95vw}dialog img{max-width:90vw;max-height:85vh}dialog::backdrop{background:#000c}button{padding:10px;cursor:pointer}</style>'''


if __name__ == '__main__':
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--run-dir', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    report = generate_failure_review(args.run_dir, args.output)
    print(json.dumps({'requests': report['request_count'], 'decisions': report['decision_count'], 'html': str(args.output / 'errors.html')}))
