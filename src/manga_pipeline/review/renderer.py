"""Small review tables without checkpoint metadata, hashes or detection arrays."""
import html
import math
import os
import re
import unicodedata
from pathlib import Path
from urllib.parse import quote

from ..storage.io import atomic_bytes, read_json
from ..policies import is_essential_policy

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


class Review:
    def __init__(self, store):
        self.store, self.root = store, store.directory
        self.data = {}
        for step in store.steps:
            item = store.manifest["steps"].get(step, {})
            if item.get("status") in {"completed", "partial"}:
                store._check_artifact(step, item)
                self.data[step] = read_json(store.artifact_path(step))
        self.raw = self.data.get("extract", {})
        self.normalized = self.data.get("normalize", {})
        self.raw_rows = {r["id"]: r for r in self.raw.get("texts", [])}
        self.rows = {r["id"]: r for r in self.normalized.get("utterances", [])}
        self.candidates = {c["id"]: c for c in self.data.get("scan", {}).get("candidates", [])}
        self.classified = {r["utterance_id"]: r for r in self.data.get("analyze", self.data.get("classify", {})).get("utterances", [])}
        bank = Path(store.manifest["bank_path"])
        self.bank = read_json(bank) if bank.is_file() else {}
        snapshot = self.root / "snapshots" / "export_bank.json"
        self.export_bank = read_json(snapshot) if snapshot.exists() else self.bank

    def speaker(self, identity, *, name=None, bank=None):
        special = {"others": "others — Chưa gán ID", "narrator": "narrator — Người dẫn truyện",
                   "groups": "groups — Nhiều người cùng nói",
                   "public_audience": "public_audience — Độc giả / người xem",
                   "self": "self — Chính người nói (chưa có ID)", "unknown": "unknown — Chưa rõ"}
        if identity in special:
            return special[identity]
        if identity in {"UNKNOWN", "NARRATOR", "NOT_APPLICABLE"}:
            return {"UNKNOWN": "Chưa rõ", "NARRATOR": "Người dẫn truyện", "NOT_APPLICABLE": "Không áp dụng"}[identity]
        if identity is None:
            return "Chưa rõ"
        if name is None:
            name = (bank if bank is not None else self.bank).get("characters", {}).get(str(identity), {}).get("display_name")
        return f"ID {identity}" + (f" — {name}" if name else "")

    def potential(self, row, field):
        return "; ".join(self.speaker(x["id"], name=x.get("name")) +
                         (" / " + ", ".join(x["inferred_aliases"]) if x.get("inferred_aliases") else "") +
                         " (" + ", ".join(x.get("sources", [])) + ")"
                         for x in row.get(field, [])) or "Không có ID ổn định"

    def dialogue_table(self, rows, *, classified=False):
        def render(item):
            source = self.row(item["utterance_id"]) if classified else item
            names = "; ".join(m["name"] + " — " + LABELS.get(m["mention_type"], m["mention_type"])
                              + (f" → ID {m['name_target_id']}" if m.get("name_target_id") is not None else "")
                              for m in item.get("mentions", []))
            speaker = self.speaker(item.get("speaker_id"))
            original = source.get("source_speaker_id", source.get("speaker_id"))
            if original != item.get("speaker_id"):
                speaker += " (MAGI: " + self.speaker(original) + ")"
            return (reference(source), source.get("text"), LABELS.get(item.get("content_type"), item.get("content_type")),
                    speaker,
                    ", ".join(self.speaker(x) for x in item.get("addressee_ids", [])),
                    self.potential(item, "speaker_candidates"), self.potential(item, "listener_candidates"),
                    item.get("addressee_type"), names or "—", item.get("evidence"))
        return table(["Câu", "Nội dung", "Loại", "Người nói", "Người nghe", "Ứng viên nói",
                      "Ứng viên nghe", "Addressee type", "Tên / vai trò / nối ID", "Bằng chứng"], map(render, rows))

    def row(self, identity):
        return self.rows.get(identity, self.raw_rows.get(identity, {"id": identity}))

    def analyzed_table(self, rows, *, classified=False):
        def render(item):
            source = self.row(item["utterance_id"]) if classified else item
            names = "; ".join(m["name"] + " / " + LABELS.get(m["mention_type"], m["mention_type"])
                + (f" → ID {m['name_target_id']}" if m.get("name_target_id") is not None else " → chưa gán")
                for m in item.get("mentions", []))
            warnings = []
            for warning in item.get("warnings", []):
                if warning["code"] == "normalized_addressee_ids":
                    warnings.append(f"Chuẩn hóa {warning.get('before')} → {warning.get('after')}")
                elif warning["code"] == "unresolved_after_repair":
                    warnings.append("Chưa giải quyết: " + ", ".join(warning.get("issues", [])))
                elif warning["code"] == "equal_priority_introduction_names":
                    warnings.append("Hai tên giới thiệu cùng ưu tiên; giữ tên hiện tại")
                else:
                    warnings.append(warning["code"])
            recipients = item.get("addressee_type", "unknown") + ": " + ", ".join(
                self.speaker(x) for x in item.get("addressee_ids", []))
            return (reference(source), source.get("text"), LABELS.get(item.get("content_type"), "Chưa rõ"),
                    self.speaker(item.get("speaker_id")), recipients, self.potential(item, "identity_candidates"),
                    names or "—", item.get("evidence"), "; ".join(dict.fromkeys(warnings)) or "—")
        return table(["Câu", "Nội dung", "Loại", "Người nói", "Đối tượng tiếp nhận", "Ứng viên ID / nguồn",
                      "Tên / vai trò / ID", "Bằng chứng", "Cảnh báo"], map(render, rows))

    def pages(self, rows, headers, render, base):
        groups = {}
        for row in rows:
            groups.setdefault(row.get("page", 0), []).append(row)
        parts = []
        for number, page_rows in groups.items():
            page = next((p for p in self.raw.get("pages", []) if p.get("page") == number), {})
            visual = self.root / "01_extract" / "visualizations" / f"page_{number}.png"
            source = Path(page.get("image_path", ""))
            title = f"Trang {number}" + (f" · {source.name}" if page.get("image_path") else "")
            image = visual if visual.is_file() else source
            parts.append("## " + (link(title, image, base) if image.is_file() else cell(title)))
            parts.append(table(headers, (render(row) for row in page_rows)))
        return "\n".join(parts) if parts else "Không có câu.\n"

    def failures(self, step):
        output = self.data.get(step, {})
        failures = output.get("failures", [])
        parts = []
        if failures:
            parts.append("## Mục lỗi\n")
            parts.append(table(["Vị trí", "Lỗi"], (
                (reference(self.row(x["utterance_id"])) if x.get("utterance_id") else x.get("panel", "Bước"),
                 x.get("error", "Lỗi chưa rõ")) for x in failures)))
        error = self.store.manifest["steps"].get(step, {}).get("error")
        if error:
            parts.append("Lỗi bước: " + cell(error) + "\n")
        return "\n".join(parts)

    def render_step(self, step):
        output = self.data.get(step, {})
        base = self.root / self.store.directories[step]
        status = self.store.manifest["steps"].get(step, {}).get("status", "pending")
        parts = [f"# {self.store.steps.index(step) + 1:02d} · {step}\n", LABELS.get(status, status) + ".\n"]
        if step not in self.data:
            parts.append("Chưa có kết quả đã commit cho bước này.\n")
        elif step == "extract":
            rows = output.get("texts", [])
            if is_essential_policy(self.store.manifest["config"].get("dialogue_analysis")):
                rows = [r for r in rows if r.get("is_essential_text") is True]
                parts.append("Chỉ hiển thị essential; text phụ vẫn giữ trong raw để dịch.\n")
            parts.append(f"{len(output.get('pages', []))} trang · {len(rows)} text hiển thị. Người nói theo MAGI.\n")
            parts.append(self.pages(rows, ["Câu", "Người nói", "Nội dung"],
                                    lambda r: (reference(r), self.speaker(r.get("speaker_id")), r.get("text")), base))
        elif step == "normalize":
            rows = output.get("utterances", [])
            changed = [r for r in rows if isinstance(r.get("text_original"), list) or r.get("text") != r.get("text_original")]
            parts.append(f"Giữ {len(rows)} câu · loại {len(output.get('excluded', []))} text · {len(changed)} câu đổi text/gộp.\n")
            if is_essential_policy(output.get("analysis_policy")):
                parts.append(f"{len(output.get('translation_only', []))} text không essential được lưu riêng để dịch, không đưa vào mô hình phân tích.\n")
            parts.append("## Text thay đổi\n")
            parts.append(table(["Câu", "Trước", "Sau"], ((reference(r),
                " / ".join(r["text_original"]) if isinstance(r["text_original"], list) else r.get("text_original"), r.get("text")) for r in changed)))
            parts.append("## Text bị loại\n")
            parts.append(table(["Câu", "Text", "Lý do"], ((reference(self.row(x["source_text_id"])),
                self.row(x["source_text_id"]).get("text"), "; ".join(REASONS.get(v, v) for v in x.get("reasons", []))) for x in output.get("excluded", []))))
            parts.append("Text giữ nguyên xem ở " + link("extract", self.root / "01_extract" / "review.md", base) + ".\n")
            flagged = [r for r in [*rows, *output.get("translation_only", [])] if r.get("content_review_status") == "needs_review"]
            if flagged:
                parts.append("## Text nghi ngờ nhưng được giữ\n")
                parts.append(table(["ID", "Text", "Lý do"], ((r.get("id", r.get("source_text_id")), r["text"],
                    "; ".join(r.get("normalization_reasons", []))) for r in flagged)))
        elif step == "scan":
            candidates = output.get("candidates", [])
            parts.append(f"{len(candidates)} ứng viên tên. Cần kiểm tra tên người / SFX / OCR sai.\n")
            parts.append(table(["Câu", "Tên tìm thấy", "Confidence NER", "Mapping", "Nguyên câu"], (
                (reference(self.row(c["utterance_id"])), c.get("name"),
                 f"{c['confidence']:.4f}" if c.get("confidence") is not None else "—",
                 self.speaker(c["known_id"]) if c.get("known_id") is not None else LABELS.get(c.get("state"), "Chưa có ID"),
                 self.row(c["utterance_id"]).get("text")) for c in candidates)))
        elif step == "analyze":
            parts.append("Text essential · context 5 câu · bộ nhớ 10 lượt · tên đã ghép được dùng từ batch kế tiếp. Group không cần đủ thành viên.\n")
            parts.append(self.analyzed_table(output.get("utterances", []), classified=True))
            parts.append("## Liên kết tên\n")
            parts.append(table(["Câu", "Tên", "Kết quả", "Nguồn / lý do"], (
                (reference(self.row(self.candidates.get(x.get("candidate_id"), {}).get("utterance_id", ""))),
                 x.get("name"), LABELS.get(x.get("status"), x.get("status"))
                 + (f" → ID {x['character_id']}" if x.get("character_id") is not None else ""),
                 REASONS.get(x.get("reason"), x.get("reason"))) for x in output.get("links", []))))
        elif step == "classify":
            if output.get("policy") in {"essential-dialogue-v1", "essential-dialogue-v2"}:
                parts.append("Mọi câu essential · context 5 câu · bộ nhớ 10 câu trước. Ứng viên nói/nghe là các lựa chọn được cung cấp, không phải thứ hạng.\n")
                if output.get("policy") == "essential-dialogue-v2":
                    parts.append("single = một người · group = nhiều người trong truyện · audience = độc giả/người xem · self = tự nói · unknown = chưa rõ loại người nghe. others không phải một danh tính cố định.\n")
                parts.append(self.dialogue_table(output.get("utterances", []), classified=True))
            else:
                parts.append("Chỉ các câu có tên; loại câu và vai trò của tên.\n")
                parts.append(table(["Câu", "Tên", "Loại câu", "Vai trò tên", "Nguyên câu"], (
                (reference(self.row(item["utterance_id"])), m.get("name"), LABELS.get(item.get("content_type"), "Chưa rõ"),
                 "Đã biết / bỏ inference" if m.get("skipped") else LABELS.get(m.get("mention_type"), "Chưa rõ"),
                 self.row(item["utterance_id"]).get("text"))
                    for item in output.get("utterances", []) for m in item.get("mentions", []))))
        elif step == "link":
            parts.append("Ghép tên / alias vào ID ổn định từ lời kể hoặc kết luận gọi trực tiếp của LLM.\n")
            records = []
            for result in output.get("links", []):
                c = self.candidates.get(result.get("candidate_id"), {})
                item = self.classified.get(c.get("utterance_id"), {})
                reason = result.get("reason", "")
                if reason == "known_or_not_narration":
                    reason = "Tên đã biết / trùng nhiều ID" if c.get("state") != "new" else (
                        "Câu không phải lời kể" if item.get("content_type") != "narration" else "Vai trò tên không đủ điều kiện ghép")
                else:
                    reason = REASONS.get(reason, reason)
                status = LABELS.get(result.get("status"), result.get("status", "Chưa rõ"))
                if result.get("character_id") is not None:
                    status += f" → ID {result['character_id']}"
                if result.get("status") == "protected":
                    reason = f"Giữ tên: {result.get('current_name', '')}"
                records.append((reference(self.row(c["utterance_id"])) if c.get("utterance_id") else "—",
                                c.get("name", result.get("name")), status, reason))
            parts.append(table(["Câu", "Tên", "Kết quả", "Lý do"], records))
        elif step == "export":
            rows = output.get("utterances", [])
            unknown = sum(r.get("voice_type") == "unknown" for r in rows)
            parts.append(f"{len(rows)} câu · {unknown} câu chưa rõ người nói.\n")

            def render(row):
                speaker = "Người dẫn truyện" if row.get("voice_type") == "narrator" else self.speaker(
                    row.get("speaker_id"), name=row.get("speaker_name"), bank=self.export_bank)
                listeners = ", ".join(self.speaker(x, bank=self.export_bank) for x in row.get("addressee_ids", []))
                if not listeners:
                    listeners = "Không áp dụng" if row.get("addressee_type") == "not_applicable" else "Chưa rõ"
                return reference(row), speaker, listeners, row.get("text")

            if is_essential_policy(output.get("analysis_policy")):
                parts.append(self.analyzed_table(rows) if output.get("analysis_policy") == "essential-v3" else self.dialogue_table(rows))
                parts.append(link("Text dành riêng cho dịch", base / "translation_only.json", base) + "\n")
            else:
                parts.append(self.pages(rows, ["Câu", "Người nói", "Người nghe", "Nội dung"], render, base))
        elif step == "translate":
            directory = Path(output["directory"])
            for style in ("natural", "localized"):
                parts.append(link("Review " + style, directory / f"review_{style}.md", base) + "\n")
                parts.append(link("JSON " + style, directory / f"{style}.json", base) + "\n")
            parts.append(link("Phiên bản dịch gần nhất", directory.parent / "latest.json", base) + "\n")
        parts.append(self.failures(step))
        parts.append("P08/T04 = trang 8, text nguồn thứ 4.\n")
        parts.append(link("← Tổng quan", self.root / "review.md", base) + "\n")
        return "\n".join(parts)

    def summary(self, step):
        data = self.data.get(step, {})
        if step == "extract":
            return f"{len(data.get('pages', []))} trang, {len(data.get('texts', []))} text"
        if step == "normalize":
            return f"{len(data.get('utterances', []))} câu giữ, {len(data.get('excluded', []))} text loại"
        if step == "scan":
            return f"{len(data.get('candidates', []))} ứng viên tên"
        if step == "analyze":
            return f"{len(data.get('utterances', []))} câu, {len(data.get('warnings', []))} cảnh báo"
        if step == "classify":
            return f"{len(data.get('utterances', []))} câu được xét"
        if step == "link":
            links = data.get("links", [])
            return f"{sum(x.get('status') == 'linked' for x in links)} mention gán tên, {sum(x.get('status') != 'linked' for x in links)} chưa gán / bỏ qua"
        if step == "translate":
            return f"{sum(len(rows) for rows in data.get('styles', {}).values())} bản dịch, {len(data.get('failures', []))} lỗi"
        return f"{len(data.get('utterances', []))} câu cuối"

    def render_index(self, available_steps=()):
        source = self.store.manifest["source"]
        status = self.store.manifest.get("status", "pending")
        parts = [f"# {cell(source.get('story_title', source.get('story', 'Truyện')))} · {cell(source.get('chapter', 'Chapter'))}\n",
                 LABELS.get(status, status) + ". Mở Markdown Preview để đọc bảng (VS Code: Ctrl+Shift+V).\n",
                 "## Các bước\n"]
        measured = [item["runtime_seconds"] for item in
                    (self.store.manifest["steps"].get(step, {}) for step in self.store.steps)
                    if runtime(item.get("runtime_seconds")) != "—"]
        total = runtime(sum(measured)) if measured else "—"
        parts.insert(2, f"**Tổng run-time đã ghi nhận: {total}** · {len(measured)}/{len(self.store.steps)} bước có timing.\n")
        parts.insert(3, "Thời gian cộng dồn qua retry; bước được dùng lại không cộng thêm. “—” là chưa có dữ liệu timing.\n")
        headers = ["Bước", "Trạng thái", "Run-time", "Kết quả", "Mở file"]
        lines = ["| " + " | ".join(headers) + " |", "| --- | --- | --- | --- | --- |"]
        for index, step in enumerate(self.store.steps, 1):
            item = self.store.manifest["steps"].get(step, {})
            state = item.get("status", "pending")
            review = self.root / self.store.directories[step] / "review.md"
            open_link = link("Kiểm tra", review, self.root) if review.exists() or step in available_steps else "—"
            summary = self.summary(step) if step in self.data else "—"
            lines.append(f"| {index:02d} · {step} | {cell(LABELS.get(state, state))} | {runtime(item.get('runtime_seconds'))} | {cell(summary)} | {open_link} |")
        parts.append("\n".join(lines) + "\n")
        visual = self.root / "01_extract" / "visualizations"
        if visual.is_dir():
            parts.append(link("Ảnh MAGI", visual, self.root) + "\n")
        if "export" in self.data:
            rows = self.data["export"].get("utterances", [])
            policy = self.data["export"].get("analysis_policy")
            unresolved = (sum(any(x in {"UNKNOWN", "unknown"} for x in r.get("addressee_ids", [])) for r in rows)
                          if is_essential_policy(policy) else sum(r.get("addressee_type") == "unspecified" for r in rows))
            parts.append("## Cần kiểm tra\n")
            parts.append(f"- {sum(r.get('voice_type') == 'unknown' for r in rows)} câu chưa rõ người nói.\n"
                         f"- {unresolved} câu chưa rõ hoặc chưa đủ ID người nghe.\n")
        parts.append("## Nhân vật trong bank hiện tại\n")
        lines = ["| ID | Tên | Ảnh đối chiếu |", "| --- | --- | --- |"]
        bank_path = Path(self.store.manifest["bank_path"])
        for key, character in self.bank.get("characters", {}).items():
            if character.get("disabled"):
                continue
            crops = [bank_path.parent / p for p in character.get("crop_paths", [])]
            previews = [link(f"Ảnh {i + 1}", p, self.root) for i, p in enumerate(crops[:3]) if p.is_file()]
            lines.append(f"| {cell(character.get('id', key))} | {cell(character.get('display_name') or 'Chưa có tên')} | {' · '.join(previews) or '—'} |")
        parts.append("\n".join(lines) + "\n" if len(lines) > 2 else "Chưa có ID ổn định.\n")
        parts.append("Review là bản đọc gọn; JSON/targets vẫn dùng cho xử lý và resume. Sửa review không đổi kết quả pipeline.\n")
        return "\n".join(parts)


def write_reviews(store, *, steps=None):
    review = Review(store)
    selected = store.steps if steps is None else steps
    for step in selected:
        if step in store.manifest["steps"]:
            atomic_bytes(store.directory / store.directories[step] / "review.md", review.render_step(step).encode("utf-8"))
    atomic_bytes(store.directory / "review.md", review.render_index().encode("utf-8"))
    return store.directory / "review.md"
