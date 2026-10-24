"""Small review tables without checkpoint metadata, hashes or detection arrays."""
import html
import math
import os
import re
import unicodedata
from pathlib import Path
from urllib.parse import quote

from ..storage.io import atomic_bytes, read_json

LABELS = {
    "completed": "Hoàn thành", "partial": "Còn lỗi", "failed": "Lỗi",
    "running": "Đang chạy / chưa commit", "pending": "Chưa chạy",
    "interrupted": "Đã ngắt; có thể resume",
    "dialogue": "Thoại", "narration": "Lời kể", "thought": "Suy nghĩ",
    "unknown": "Chưa rõ", "ui": "Giao diện", "watermark": "Watermark", "sfx": "Âm thanh",
    "new": "Tên mới", "known": "Đã biết", "bank_conflict": "Trùng nhiều ID",
    "direct_address": "Gọi trực tiếp", "self_introduction": "Tự giới thiệu",
    "third_person": "Nhắc người khác", "narration_introduction": "Lời kể giới thiệu",
    "narration_reference": "Lời kể nhắc đến", "linked": "Đã gán",
    "skipped": "Bỏ qua", "protected": "Giữ tên thủ công",
    "introduction": "Giới thiệu", "reference": "Nhắc đến", "known": "Đã biết", "unresolved": "Chưa gán",
}
REASONS = {
    "name_already_mapped": "Tên đã có ID",
    "no_stable_id_in_panel": "Panel chưa có ID ổn định",
    "multiple_new_names_one_id": "Nhiều tên mới nhưng chỉ có một ID",
    "one_stable_id_one_new_name": "Một tên mới, một ID trong panel",
    "vlm_narration_check": "VLM xét panel",
    "vlm_introduction_panel": "VLM ghép người được giới thiệu", "llm_direct_address": "LLM kết luận người được gọi",
    "direct_listener_unresolved": "Chưa xác định ID người được gọi", "reference_without_known_id": "Tên nhắc đến chưa có ID",
    "unresolved_text_pair": "Cặp thoại còn mâu thuẫn", "not_a_confirmed_person_mention": "Chưa xác nhận là tên người",
    "conflicting_names": "Các tên khác nhau chọn cùng ID",
    "explicit_ui_pattern": "Text giao diện", "explicit_watermark_pattern": "Watermark",
    "explicit_sfx_pattern": "Âm thanh", "empty_ocr": "OCR rỗng",
}


def cell(value):
    if value is None:
        return "—"
    text = " ".join(str(value).split())
    text = "".join(c for c in text if not unicodedata.category(c).startswith("C"))
    return html.escape(text, quote=False).replace("|", "\\|") or "—"


def table(headers, rows):
    rows = list(rows)
    if not rows:
        return "Không có.\n"
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    lines.extend("| " + " | ".join(cell(v) for v in row) + " |" for row in rows)
    return "\n".join(lines) + "\n"


def runtime(value):
    if type(value) not in {int, float} or not math.isfinite(value) or value < 0:
        return "—"
    if value < 60:
        return f"{value:.3f}s"
    hours, remainder = divmod(int(value), 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours}h {minutes:02d}m {seconds:02d}s" if hours else f"{minutes}m {seconds:02d}s"


def link(label, target, base):
    target, base = Path(target).resolve(), Path(base).resolve()
    try:
        href = os.path.relpath(target, base).replace("\\", "/")
    except ValueError:
        href = target.as_uri()
    return f"[{cell(label)}]({quote(href, safe='/:')})"


def reference(row):
    match = re.search(r":p(\d+):t(\d+)", row.get("id", ""))
    page = row.get("page") or (int(match[1]) if match else 0)
    index = row.get("text_index")
    if index is None:
        index = int(match[2]) if match else row.get("reading_order", 0)
    return f"P{int(page):02d}/T{int(index) + 1:02d}"
