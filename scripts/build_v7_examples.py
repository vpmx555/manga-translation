"""Publish real, traceable pipeline examples for docs; never call a model."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=Path('docs/v7/examples'))
    parser.add_argument('--page', type=int, default=11)
    args = parser.parse_args()
    root, output, page = args.run.resolve(), args.output.resolve(), args.page
    output.mkdir(parents=True, exist_ok=True)
    entries = []
    for name, source, description in [
        ('input.webp', args.input / f'{page:02d}.webp', 'Ảnh đầu vào nguyên bản.'),
        ('layout.png', root / f'01_layout/overlays/page-{page}.png', 'Overlay Koharu: panel và bubble.'),
        ('extract.png', root / f'02_extract/visualizations/page_{page}.png', 'MAGI: character/text boxes và số nhân vật trong bank.'),
    ]:
        if not source.is_file():
            continue
        shutil.copyfile(source, output / name)
        entries.append({'file': name, 'source': str(source.resolve()),
                        'sha256': hashlib.sha256(source.read_bytes()).hexdigest(), 'description': description})
    geometry = root / '03_bubble_geometry/overlays'
    matches = list(geometry.glob(f'*-p{page}.png'))
    if matches:
        source = matches[0]
        shutil.copyfile(source, output / 'geometry.png')
        entries.append({'file': 'geometry.png', 'source': str(source),
                        'sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
                        'description': 'Bubble geometry: nhóm bubble, hình học và vùng text.'})
    stages = {}
    for name in ('02_extract', '04_normalize', '05_scan', '06_analyze'):
        stages[name] = json.loads((root / name / 'result.json').read_text(encoding='utf-8'))
    utterances = [u for u in stages['04_normalize']['utterances'] if u['page'] == page]
    ids = {u['id'] for u in utterances}
    evidence = {'page': page, 'ocr': [t for t in stages['02_extract']['texts'] if t['page'] == page],
                'utterances': utterances,
                'names': [c for c in stages['05_scan']['candidates'] if c['utterance_id'] in ids],
                'analyze': [u for u in stages['06_analyze']['utterances'] if u['utterance_id'] in ids]}
    (output / 'page-evidence.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    manifest = {'source_run': str(root), 'page': page, 'kind': 'historical-upstream-example',
                'note': 'Ảnh và JSON thật; không phải kết quả kiểm chứng bootstrap identity mới.', 'assets': entries}
    (output / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    lines = ['# Ví dụ thật theo pipeline v7', '',
        f'Nguồn: `{root.name}` · Iris Zero chapter 1 · trang {page}.', '',
        'Đây là dữ liệu upstream đã chạy, dùng để giải thích luồng dữ liệu. Chất lượng OCR/analyze có thể còn lỗi; '
        'các ảnh này không chứng minh chất lượng identity–relationship của batch mới.', '',
        'Nguồn và SHA-256: [manifest.json](manifest.json). Dữ liệu đối chiếu: [page-evidence.json](page-evidence.json).', '']
    for asset in entries:
        lines += [f"## {asset['description']}", '', f"![{asset['description']}]({asset['file']})", '']
    lines += ['## Từ ảnh đến dữ liệu ngữ nghĩa', '',
        '1. `layout` xác định panel/bubble; `extract` phát hiện nhân vật, gắn MAGI bank ID và nhận OCR.',
        '2. `bubble_geometry` gom vùng; `normalize` tạo utterance với nguồn bbox/text giữ nguyên để truy vết.',
        '3. `scan` chia scene, lấy tên và span sửa tên; `analyze` suy luận speaker/addressee/mention_type theo batch.',
        '4. `build_identity` gắn tên với MAGI bằng panel + crop + thoại, rồi tạo profile cho identity đủ điều kiện.',
        '5. `relationship` dùng profile và giao tiếp trực tiếp để tạo quan hệ xã hội cùng hai chiều xưng hô độc lập.',
        '6. `export` cố định snapshot; `inpaint` xóa chữ; `translate` dịch natural; `typeset` đặt chữ Việt.', '',
        '## Review hai bước identity–relationship', '',
        'Chạy `deployment/runpod/identity_review.py` với `--run-dir`, `--output`, `--source-folder`, `--bank-root`.',
        'Kết quả: `build_identity.html`, `relationship.html`, `review.md`, `report.json`. HTML không cần model hoặc CDN.',
        'Build identity có tên → MAGI, panel/crop bằng chứng, profile, lý do và trường hợp chưa phân giải. '
        'Relationship có mũi tên riêng từng chiều; bấm cạnh để xem xưng/gọi, lý do, thoại và ảnh.',
        'Nếu chưa có identity hoặc chưa commit, trang hiển thị rõ trạng thái đó. JSON giữ ID để agent truy vết; giao diện dùng số MAGI.', '']
    (output / 'README.md').write_text('\n'.join(lines), encoding='utf-8')
    print(json.dumps({'output': str(output), 'images': len(entries), 'utterances': len(utterances)}))


if __name__ == '__main__':
    main()
