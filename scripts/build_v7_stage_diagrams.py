"""Render explanatory stage diagrams as SVG; no model or pipeline execution."""
from html import escape
from pathlib import Path
import json
import textwrap


STAGES = [
 ('01_layout', 'Layout', ['Ảnh nhiều trang', 'Checksum nguồn'], ['Koharu: toàn trang + crop panel', 'Đưa tọa độ về ảnh gốc', 'Lưu mask và checkpoint'], ['Panel theo thứ tự', 'Ứng viên bubble + mask', 'Ứng viên hiệu ứng']),
 ('02_extract', 'Extract', ['Ảnh + panel từ layout', 'Mô hình và character bank'], ['MAGI phát hiện text/nhân vật', 'GLM-OCR đọc các vùng text', 'Đối chiếu nhân vật với bank'], ['Text + OCR detail', 'Character/cluster/MAGI ID', 'Association hình học']),
 ('03_bubble_geometry', 'Bubble geometry', ['Ứng viên bubble và mask', 'Text/character từ extract'], ['Gom group, tách part/bridge', 'Phân tích hình dạng', 'Map text theo vị trí'], ['Group + part + mask', 'Text → group/part', 'Role evidence ban đầu']),
 ('04_normalize', 'Normalize', ['Text box theo thứ tự đọc', 'Mapping group/part/panel'], ['Gộp text thành utterance', 'Giữ các source box', 'Chọn panel chính/liên quan'], ['Utterance ID ổn định', 'Text và nguồn gốc', 'BBox + panel mapping']),
 ('05_graph', 'Graph', ['Toàn bộ utterance', 'Panel/MAGI metadata + registry'], ['Một request toàn chapter', 'Nhóm tên + sửa spelling', 'Đóng băng scene/event'], ['OCR hiện hành + lịch sử', 'Chapter → scene → event', 'Giả thuyết nhân vật']),
 ('06_analyze', 'Analyze', ['Graph + utterance hiện hành', 'Ảnh event và nhãn MAGI'], ['Scene chạy song song', 'Event tuần tự trong scene', 'Memory nối event cùng scene'], ['Speaker/listener/mention', 'Confidence từng quyết định', 'Mô tả event/scene cập nhật']),
 ('07_reanalyze', 'Reanalyze', ['Toàn chương sau analyze', 'Text + confidence + references'], ['Một request text-only', 'Đề xuất patch theo ID', 'So confidence trước/sau'], ['Accepted/rejected patches', 'Effective analysis snapshot', 'Fallback nếu request lỗi']),
 ('08_build_identity', 'Build identity', ['Effective analysis', 'Mention + crop bank'], ['Đăng ký tên theo analyze', 'Gộp thành viên MAGI hợp lệ', 'Dựng profile identity'], ['Identity registry/projection', 'Profile + source references', 'Review và snapshot hash']),
 ('09_relationship', 'Relationship', ['Identity + profile', 'Giao tiếp trực tiếp + crop'], ['Chọn cặp có tương tác', 'Suy luận social relation', 'Tách hai hướng xưng hô'], ['Social relation A/B', 'Address A → B', 'Address B → A']),
 ('10_export', 'Export', ['Current OCR + semantic hiệu lực', 'Identity/profile/relationship'], ['Gắn projection riêng', 'Giữ scene/event và nguồn', 'Xuất giao diện dữ liệu chung'], ['dialogue.json', 'effects.json', 'Semantic source + hashes']),
 ('11_inpaint', 'Inpaint · tùy chọn', ['Ảnh nguồn', 'Vùng chữ/mask đã export'], ['Chuẩn bị mask xóa chữ', 'Chạy backend inpaint', 'Giữ checkpoint theo trang'], ['Nền sạch', 'Mask và receipt', 'Ảnh cho typeset']),
 ('12_translate', 'Translate', ['Export + scene/event', 'Xưng hô/profile/glossary'], ['Reuse membership event', 'Batch bổ sung tối đa 8 câu', 'Dịch natural song song'], ['Bản dịch natural theo ID', 'Lịch sử override/cache', 'Các câu lỗi cần review']),
 ('13_typeset', 'Typeset', ['Bản dịch natural', 'Ảnh gốc hoặc nền inpaint'], ['Chọn vùng và font', 'Wrap/fit tiếng Việt', 'Ghi nhận vùng không fit'], ['Trang đã dàn chữ', 'Render/checkpoint từng trang', 'Review fit/glyph'])
]


def render(title, columns):
    parts = ['<svg xmlns="http://www.w3.org/2000/svg" width="1260" height="350" viewBox="0 0 1260 350">',
        '<rect width="1260" height="350" fill="#f5f8fc"/>',
        '<style>text{font-family:Arial,sans-serif;fill:#16324b}.h{font-size:22px;font-weight:bold}.b{font-size:18px}.s{font-size:15px;fill:#536779}</style>',
        f'<text x="30" y="40" class="h">{escape(title)}</text>',
        '<defs><marker id="a" markerWidth="10" markerHeight="10" refX="8" refY="3" orient="auto"><path d="M0,0 L0,6 L9,3 z" fill="#3b6b9d"/></marker></defs>']
    for index, (label, items) in enumerate(zip(['INPUT', 'XỬ LÝ', 'OUTPUT'], columns)):
        x = 30+index*420
        parts += [f'<rect x="{x}" y="70" width="360" height="220" rx="14" fill="white" stroke="#85a8c8" stroke-width="2"/>',
                  f'<text x="{x+20}" y="104" class="h">{label}</text>']
        y = 139
        for item in items:
            for line in textwrap.wrap(item, width=34):
                parts.append(f'<text x="{x+20}" y="{y}" class="b">{escape(line)}</text>')
                y += 25
            y += 9
        if index < 2:
            parts.append(f'<path d="M{x+365},180 L{x+409},180" stroke="#3b6b9d" stroke-width="3" marker-end="url(#a)"/>')
    parts.append('<text x="30" y="327" class="s">Sơ đồ giải thích luồng dữ liệu của code; không phải kết quả suy luận trên một trang cụ thể.</text></svg>')
    return '\n'.join(parts)+'\n'


if __name__ == '__main__':
    root = Path(__file__).resolve().parents[1]
    output = root / 'docs/v7/examples/flow'
    output.mkdir(parents=True, exist_ok=True)
    for key, title, source, steps, target in STAGES:
        (output / (key+'.svg')).write_text(render(title, [source, steps, target]), encoding='utf-8')
    print(json.dumps({'output': str(output), 'diagrams': len(STAGES), 'model_requests': 0}))
