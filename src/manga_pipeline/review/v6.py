"""Detailed Vietnamese reviews of all v6 stages, including offline replicas."""
import json
import hashlib
import re
from collections import Counter
from pathlib import Path, PurePosixPath

from .renderer import LABELS, cell, link, runtime, table
from ..storage.io import atomic_bytes, file_hash, read_json
from ..storage.runs import flow_steps


TITLES = {
    "layout": "Koharu — panel và bubble", "extract": "MAGI + OCR — text và nhân vật",
    "bubble_geometry": "Hình học bubble và mapping OCR", "normalize": "Chuẩn hóa và gộp câu",
    "scan": "Tìm ứng viên tên", "analyze": "Phân tích semantic, người nói/nghe và tên",
    "identity": "Liên kết danh tính nhân vật", "export": "Xuất thoại và hiệu ứng",
    "build_identity": "Liên kết identity và profile nhân vật", "relationship": "Quan hệ và xưng hô có hướng",
    "inpaint": "Xóa chữ, tạo nền sạch", "translate": "Dịch natural",
    "typeset": "Dàn chữ và kiểm tra render",
}


def value(item):
    if isinstance(item, (list, tuple)):
        return "; ".join(value(x) for x in item)
    if isinstance(item, dict):
        return json.dumps(item, ensure_ascii=False, separators=(",", ":"))
    return "—" if item is None else str(item)


class V6Review:
    def __init__(self, directory, *, source_folder=None, bank_root=None):
        self.root = Path(directory).resolve()
        self.manifest = read_json(self.root / "manifest.json")
        if self.manifest.get("pipeline_version") != 6:
            raise ValueError("Detailed v6 review requires a v6 manifest")
        self.data, self.directories = {}, {}
        self.steps = flow_steps(self.manifest['config'])
        for step in self.steps:
            matches = sorted(self.root.glob(f"[0-9][0-9]_{step}"))
            self.directories[step] = matches[0].name if matches else f"{self.steps.index(step)+1:02d}_{step}"
            item = self.manifest.get("steps", {}).get(step, {})
            if item.get("status") not in {"completed", "partial"}:
                continue
            artifact = self.root / self.directories[step] / "result.json"
            if not artifact.is_file():
                raise ValueError(f"Stage replica is not complete/checksum matched: {step}")
            payload = artifact.read_bytes()
            if hashlib.sha256(payload).hexdigest() != item.get("artifact_hash"):
                raise ValueError(f"Stage replica is not complete/checksum matched: {step}")
            self.data[step] = json.loads(payload.decode("utf-8-sig"))
        raw = self.data.get("bubble_geometry", self.data.get("extract", {}))
        self.pages = raw.get("pages", [])
        normalized = self.data.get('normalize', {})
        correction = self.data.get('scan', {}).get('name_normalization')
        if correction and correction.get('policy') in {'canonical-scan-v3', 'canonical-scan-v4'}:
            from ..v6.name_normalization import apply
            normalized = apply(normalized, correction)
        self.rows = {r["id"]: r for r in normalized.get("utterances", [])}
        self.rows.update({r["id"]: r for r in self.data.get("export", {}).get("utterances", [])})
        self.texts = {r["id"]: r for r in raw.get("texts", [])}
        state = (self.data.get('build_identity', {}).get('person_state') or self.data.get('identity', {}).get('person_state')
                 or self.data.get('analyze', {}).get('person_state'))
        snapshot = self.root / 'snapshots/persons-v6.json'
        if not state:
            state = read_json(snapshot).get('state', {}) if snapshot.is_file() else {}
        self.person_state = state
        self.persons = state.get("persons", {})
        self.source_folder = Path(source_folder) if source_folder else None
        self.bank = Path(bank_root) / self.manifest["source"]["story"] if bank_root else None
        bank_file = self.bank / "metadata.json" if self.bank else None
        self.bank_data = read_json(bank_file) if bank_file and bank_file.is_file() else {}
        self.cluster_banks = {}
        for ref, observations in state.get('observations', {}).items():
            for observation in observations:
                bank_id = observation.get('source_bank_id')
                if bank_id is None:
                    continue
                for key in (ref, observation.get('source_cluster_id')):
                    if key:
                        self.cluster_banks.setdefault(key, set()).add(str(bank_id))

    @staticmethod
    def bank_label(numbers):
        numbers = sorted(set(numbers), key=lambda n: (not str(n).isdigit(), int(n) if str(n).isdigit() else str(n)))
        return 'MAGI ' + '/'.join(numbers) if numbers else 'Chưa có số MAGI'

    def person(self, identity):
        if identity is None:
            return "Chưa rõ"
        if identity in {"narrator", "audience", "public_audience", "self", "others", "unknown"}:
            return {"narrator": "Người kể", "audience": "Độc giả / khán giả", "public_audience": "Độc giả / khán giả",
                    "self": "Bản thân", "others": "Chưa gán danh tính", "unknown": "Chưa rõ"}[identity]
        identity = str(identity)
        seen = set()
        while identity in self.person_state.get('redirects', {}) and identity not in seen:
            seen.add(identity)
            identity = self.person_state['redirects'][identity]
        person = self.persons.get(identity, {})
        banks = sorted({str(r["source_bank_id"]) for r in person.get("appearance_refs", []) if r.get("source_bank_id") is not None})
        name = person.get("primary_name")
        if identity in self.bank_data.get('characters', {}):
            banks.append(identity)
        label = self.bank_label(banks)
        return label + (" — " + name if name else "")

    def cluster(self, reference):
        return self.bank_label(self.cluster_banks.get(reference, []))

    def display_identifiers(self, text):
        """Translate character identifiers in prose/JSON too; keep utterance IDs."""
        text = re.sub(r'(?:[0-9a-f]{16}:)?[0-9a-f]{16}:p\d+:c\d+',
                      lambda match: self.cluster(match.group()), text)
        return re.sub(r'person:[0-9a-f]{20}(?![0-9a-f])',
                      lambda match: self.person(match.group()), text)

    def speaker(self, row):
        if row.get('speaker_person_id') is None and row.get('speaker_kind') == 'narrator':
            return 'Người kể'
        return self.person(row.get('speaker_person_id'))

    def anchor(self, row):
        anchor = row.get('speaker_anchor') or {}
        if anchor.get('conflicting_person_ids'):
            return 'Nguồn mâu thuẫn: ' + value([self.person(p) for p in anchor['conflicting_person_ids']])
        return self.person(anchor['person_id']) if anchor.get('person_id') is not None else 'Chưa xác định từ nguồn'

    def trace(self, item, key, *aliases):
        """Read stored provenance only; never invent routing/scenes from content/style."""
        for record in (item, item.get("analysis_trace", {}), self.row(item)):
            if not isinstance(record, dict):
                continue
            for name in (key, *aliases):
                if name in record:
                    return record[name]
        return None

    def analyze_trace(self, rows, base):
        data = self.data.get("analyze", {})
        parts = ["### Independent analysis batches", "",
            table(["Scene", "English summary", "Các câu"],
                  ((item["scene_id"], item.get("summary", "Historical scene: summary unavailable"), value([self.sentence(key) for key in item.get("target_ids", [])]))
                   for item in data.get("scenes", []))),
            table(["Batch", "Scene", "Branch", "Các câu", "Cluster pool", "Anchor exception"],
                  ((item["batch_id"], item["request"]["scene_id"], item["request"]["analysis_branch"],
                    value([self.sentence(r["id"]) for r in item["rows"]]), value(list(dict.fromkeys(self.cluster(ref) for ref in item['clusters']))), item["anchor_exception"])
                   for item in data.get("batches", []))),
            self.page_tables(rows, ["Câu", "Batch", "Scene", "Branch", "Nhân vật từ nguồn", "Người nói sau merge", "Resolution", "Errors"],
                lambda row: (self.sentence(row), row.get("batch_id"), row.get("scene_id"), row.get("analysis_branch"),
                    self.anchor(row), self.speaker(row), row.get("semantic_resolution"), value(row.get("semantic_errors", []))), base)]
        return "\n".join(parts)

    def source(self, number):
        images = self.manifest.get("source", {}).get("images", [])
        if number < 1 or number > len(images):
            return None
        saved = images[number-1]["path"]
        if self.source_folder:
            candidate = self.source_folder / PurePosixPath(saved.replace("\\", "/")).name
            if candidate.is_file():
                return candidate
        candidate = Path(saved)
        return candidate if candidate.is_file() else None

    def row(self, item):
        key = item.get("utterance_id", item.get("id", item.get("source_text_id")))
        return self.rows.get(key, self.texts.get(key, item))

    def ref(self, item):
        row = self.row(item)
        return f"P{row.get('page', '?')} · {item.get('utterance_id', item.get('id', item.get('source_text_id', '—')))}"

    def sentence(self, item):
        if not isinstance(item, dict):
            item = {"id": item}
        row = self.row(item)
        text = row.get("text") or item.get("text") or "Chưa có nội dung câu"
        return f"P{row.get('page', '?')} · {text}"

    def image_links(self, page, base):
        links = []
        original = self.source(page.get("page", 0))
        if original:
            links.append(link("Trang gốc", original, base))
        for name in page.get("files", {}):
            if "/overlays/" in name or name.endswith("rendered.png") or name.endswith("clean.png"):
                path = self.root / name
                if path.is_file():
                    links.append(link(Path(name).name, path, base))
        return " · ".join(links)

    def page_tables(self, rows, headers, render, base):
        grouped = {}
        for row in rows:
            number = self.row(row).get("page", row.get("page", 0))
            grouped.setdefault(number, []).append(row)
        parts = []
        for number, records in sorted(grouped.items(), key=lambda pair: str(pair[0]).zfill(5)):
            source = self.source(number) if isinstance(number, int) else None
            parts.extend(["### " + (link(f"Trang {number}", source, base) if source else f"Trang {number}"), "",
                          table(headers, (render(record) for record in records))])
        return "\n".join(parts) if parts else "Không có.\n"

    def semantic_table(self, rows, base):
        def render(row):
            source = self.row(row)
            mentions = [f"{m.get('raw_surface', m.get('name_key', m.get('candidate_id', '')))} / {m.get('mention_type', '')} → {self.person(m.get('target_person_id'))}"
                        for m in row.get("mentions", [])]
            listeners = row.get("addressee_person_ids", row.get("addressee_ids", []))
            return (self.ref(row), source.get("text"), row.get("content_type"), row.get("speech_style"),
                self.speaker(row), value([self.person(p) for p in listeners]),
                value(mentions), row.get("evidence"), row.get("semantic_resolution", "resolved"),
                row.get("repair_count", 0), row.get("semantic_needs_review", False), value(row.get("semantic_errors", [])))
        rendered = self.page_tables(rows, ["Câu", "Nội dung", "Loại", "Speech style", "Người nói", "Người nghe",
            "Tên / vai trò / ID", "Bằng chứng", "Resolution", "Repair", "Review", "Lý do"], render, base)
        if any(self.trace(row, "analysis_branch") in {"caption", "dialogue"} for row in rows):
            rendered = "\n".join("### Nhánh " + branch + "\n\n" + self.page_tables(
                [row for row in rows if self.trace(row, "analysis_branch") == branch],
                ["Câu", "Nội dung", "Loại", "Speech style", "Người nói", "Người nghe", "Tên / vai trò / ID", "Bằng chứng", "Resolution", "Repair", "Review", "Lý do"], render, base)
                for branch in ("caption", "dialogue")) + "\n\n" + self.page_tables(
                [row for row in rows if self.trace(row, "analysis_branch") not in {"caption", "dialogue"}],
                ["Câu", "Nội dung", "Loại", "Speech style", "Người nói", "Người nghe", "Tên / vai trò / ID", "Bằng chứng", "Resolution", "Repair", "Review", "Lý do"], render, base)
        return rendered

    def render_step(self, step, base, overview):
        data = self.data.get(step, {})
        item = self.manifest.get("steps", {}).get(step, {})
        status = item.get("status", "pending")
        parts = [f"## {self.steps.index(step)+1:02d} · {step} — {TITLES[step]}", "",
            f"Trạng thái: {LABELS.get(status, status)} · Thời gian: {runtime(item.get('runtime_seconds'))}", "",
            "Policy: " + cell(data.get("policy", data.get("geometry_policy", "—"))), ""]
        artifact = self.root / self.directories[step] / "result.json"
        if artifact.is_file():
            parts.extend([link("JSON dữ liệu", artifact, base), ""])
        if step not in self.data:
            parts.append("Chưa có kết quả completed/partial đã đối chiếu checksum.\n")
        elif step == "layout":
            parts.append(f"{len(data.get('pages', []))} trang. Tọa độ theo ảnh gốc; bubble là bằng chứng visual.")
            for page in data.get("pages", []):
                parts.extend([f"### Trang {page['page']} · {page.get('width')} × {page.get('height')}", "", self.image_links(page, base), "",
                    table(["Panel", "BBox", "Confidence"], ((i, value(p.get("bbox")), p.get("confidence")) for i, p in enumerate(page.get("panels", [])))),
                    table(["Bubble", "BBox", "Panel", "Confidence", "Nguồn detection"], ((b.get("id"), value(b.get("bbox")), b.get("panel_index"), b.get("confidence"), b.get("source_pass")) for b in page.get("bubbles", [])))])
        elif step == "extract":
            parts.append("Tất cả text OCR; MAGI association là gợi ý người nói, chưa phải kết luận semantic.")
            parts.append(self.page_tables(data.get("texts", []), ["Text", "OCR", "BBox", "Panel", "Nhân vật MAGI", "Essential", "Nguồn association"],
                lambda r: (self.ref(r), r.get("text"), value(r.get("bbox")), r.get("panel_index"),
                           self.bank_label([str(r['speaker_id'])] if r.get('speaker_id') is not None else []), r.get("is_essential_text"), r.get("association_source")), base))
            for page in data.get("pages", []):
                visual = self.root / self.directories["extract"] / "visualizations" / f"page_{page['page']}.png"
                if visual.is_file():
                    parts.append(link(f"Trang {page['page']} — text/character", visual, base))
                parts.append(f"Trang {page['page']}: {len(page.get('characters', []))} character detection; {len(page.get('tails', []))} tail.")
        elif step == "bubble_geometry":
            for page in data.get("pages", []):
                parts.extend([f"### Trang {page['page']}", "", self.image_links(page, base), "",
                    "Backend: " + cell(value(page.get("geometry_compute_backend", {}))), "",
                    table(["Group", "BBox", "Parts", "Connections", "Pixel suy dựng"], ((g.get("id"), value(g.get("bbox")),
                        value([p.get("id") for p in g.get("parts", [])]), len(g.get("connections", [])), g.get("inferred_pixel_count")) for g in page.get("bubble_groups", [])))])
            parts.append(self.page_tables(data.get("texts", []), ["Text", "OCR", "Group", "Part IDs", "Mapping", "Coverage"],
                lambda r: (self.ref(r), r.get("text"), r.get("group_id"), value(r.get("part_ids", [])), r.get("mapping_status"), r.get("coverage")), base))
        elif step == "normalize":
            parts.append(self.page_tables(data.get("utterances", []), ["Câu", "Text gốc", "Text sau chuẩn hóa", "Group", "Nguồn OCR", "Lý do", "Review"],
                lambda r: (self.ref(r), value(r.get("text_original")), r.get("text"), r.get("group_id"), value(r.get("source_text_ids", [])),
                           value(r.get("normalization_reasons", [])), r.get("content_review_status")), base))
            parts.extend(["### Text bị loại", "", table(["Nguồn", "Text", "Lý do"], ((value(r.get("source_text_ids", r.get("source_text_id"))),
                self.row(r).get("text"), value(r.get("reasons", []))) for r in data.get("excluded", []))),
                "### Text chỉ dành cho dịch / effects", "", table(["ID", "Text", "Loại"], ((r.get("id"), r.get("text"), r.get("content_type"))
                    for r in [*data.get("translation_only", []), *data.get("effects", [])]))])
        elif step == "scan":
            parts.append(self.page_tables(data.get("candidates", []), ["Câu", "Tên / surface", "Span", "Nguồn", "Person ứng viên", "Nguyên câu"],
                lambda r: (self.ref(r), r.get("raw_surface", r.get("name")), value([r.get("start"), r.get("end")]), r.get("source"),
                           value([self.person(p) for p in r.get("candidate_person_ids", [])]), self.row(r).get("text")), base))
        elif step == "analyze":
            parts.append(self.analyze_trace(data.get("utterances", []), base))
            parts.append("### Kết quả semantic đã lưu — null speaker không đại diện một danh tính dùng chung")
            parts.append(self.semantic_table(data.get("utterances", []), base))
            style_counts = Counter(row.get("speech_style", "unknown") for row in data.get("utterances", []))
            parts.extend(["### Speech style và evidence thực tế", "", table(["Style", "Số câu"], sorted(style_counts.items())),
                "Policy conservative-shout chỉ được ghi nhận theo artifact; không áp lại nhãn style cho batch cũ.", "",
                self.page_tables(data.get("utterances", []), ["Câu", "Style", "Style evidence / cue đã lưu"],
                    lambda row: (self.ref(row), row.get("speech_style"), row.get("speech_style_evidence", "Chưa lưu cue riêng")), base)])
            parts.extend(["### Bằng chứng gọi tên", "", table(["Event", "Câu", "Tên", "Caller", "Recipient cluster", "Person", "Visual support", "Bằng chứng"],
                ((e.get("event_id"), e.get("source_utterance_id"), e.get("name_key"), self.person(e.get("caller_person_id")), e.get("recipient_cluster_ref"),
                  self.person(e.get("target_person_id")), e.get("visual_support"), e.get("evidence")) for e in data.get("calling_events", []))),
                "### Validation / unresolved", "", table(["Câu", "Resolution", "Lỗi / lý do"], ((self.sentence(x.get("target_id")), x.get("resolution"),
                    value(x.get("errors", x.get("reason")))) for x in data.get("semantic_validation_issues", [])))])
        elif step in {"identity", 'build_identity'}:
            parts.append("MAGI cluster bank và semantic person registry là hai lớp riêng; không ghi tên semantic vào bank MAGI.")
            binding = data.get('name_binding')
            if binding:
                parts.extend(['### Xác định chủ sở hữu tên', '', value(binding['summary']), '',
                    table(['Tên', 'Nguyên câu', 'MAGI', 'Bằng chứng', 'Lý do', 'References'],
                        ((c['occurrence']['surface'], self.sentence(c['occurrence']['utterance_id']),
                          'MAGI ' + c['owner_magi_id'], c['evidence_kind'], c['reason'], value(c['evidence_refs']))
                         for c in binding['accepted'])),
                    '### Tên chưa phân giải / bị bỏ qua', '',
                    table(['Nhóm / lượt tên', 'Lý do'],
                        ((r.get('group_id', r.get('occurrence_id', r.get('batch_id', ''))),
                          value(r.get('reason', r.get('error')))) for r in binding['needs_review']))])
            parts.append(table(["Person", "Clusters", "Same-person status", "Cơ sở", "Lần gọi xác nhận", "Event refs", "Mâu thuẫn"],
                ((self.person(p.get("person_id")), value(p.get("cluster_refs", [p.get("cluster_ref")])), p.get("same_person_status"),
                  p.get("decision_basis"), p.get("confirmed_event_count"), value(p.get("event_refs", [])), value(p.get("contradictions", []))) for p in data.get("proposals", []))))
            parts.append(table(["Person", "Tên chính", "Name forms", "Redirect"], ((pid, p.get("primary_name"),
                value([f.get("surface") for f in p.get("name_forms", [])]), data.get("person_state", {}).get("redirects", {}).get(pid)) for pid, p in self.persons.items())))
            if self.bank:
                parts.extend(["### Ảnh nhân vật từ bank đã đồng bộ", "", table(["ID MAGI", "Tên trong bank", "Ảnh đối chiếu"],
                    ((pid, record.get("display_name"), " · ".join(link(f"Ảnh {i+1}", self.bank / name, base)
                        for i, name in enumerate(record.get("crop_paths", [])[:3]) if (self.bank / name).is_file()))
                        for pid, record in self.bank_data.get("characters", {}).items()))])
            for pid, profile in data.get('profiles', {}).items():
                parts.extend([f"### Profile {self.person(pid)}", '', 'Version: ' + cell(profile['version']), '',
                    table(['Đặc tính', 'Giá trị', 'Trạng thái', 'Căn cứ', 'Evidence'],
                        ((field, a['value'], a['status'], a['basis'], value(a['evidence_refs']))
                         for field, a in profile['attributes'].items()))])
        elif step == 'relationship':
            from ..v6.relationship_v2 import mermaid
            parts.extend(['### Sơ đồ xưng hô có hướng', '', mermaid(data), ''])
            parts.extend(['### Xưng hô — mỗi chiều là một quyết định độc lập', '',
                table(['Người nói', 'Người nghe', 'Tự xưng', 'Gọi', 'Trạng thái', 'Căn cứ', 'Evidence', 'Lý do'],
                    ((self.person(e['from_id']), self.person(e['to_id']), e['self'], e['address'],
                      e['status'], e['basis'], value(e['evidence_refs']), e['reason']) for e in data.get('address_table', []))),
                '### Quan hệ xã hội', '', table(['Từ', 'Đến', 'Loại', 'Quan hệ', 'Vai trò từ', 'Vai trò đến', 'Đối xứng', 'Trạng thái', 'Evidence'],
                    ((self.person(e['from_id']), self.person(e['to_id']), e['category'], e['relationship'],
                      e.get('role_from'), e.get('role_to'), e['symmetric'], e['status'], value(e['evidence_refs'])) for e in data.get('social_relations', [])))])
        elif step == "export":
            parts.extend(["### Thoại / narration / thought", "", self.semantic_table(data.get("utterances", []), base),
                "### Hiệu ứng — không render bản dịch vào ảnh", "", self.semantic_table(data.get("effects", []), base)])
            parts.append(self.analyze_trace(data.get("utterances", []), base))
        elif step == "inpaint":
            for page in data.get("pages", []):
                clean = self.root / page.get("clean_path", "")
                parts.extend([f"### Trang {page['page']} · AOT calls: {page.get('aot_calls', 0)}", "",
                    link("Nền sạch", clean, base) if clean.is_file() else "Nền sạch chưa đồng bộ.", "",
                    table(["Nguồn OCR", "Bubble", "Cách xóa", "Nền / lý do", "Review"], ((r.get("source_text_id"), r.get("bubble_id"), r.get("method"),
                        value(r.get("background", {})), r.get("requires_review")) for r in page.get("decisions", [])))])
        elif step == "translate":
            for style in ("natural",):
                parts.extend([f"### Bản {style}", ""])
                def translated(r):
                    t = r.get("translations", {}).get(style, {})
                    return (self.ref(r), r.get("text"), r.get("content_type"), self.person(r.get("speaker_person_id")),
                        value([self.person(p) for p in r.get("addressee_ids", [])]), t.get("translation"), t.get("needs_review"), t.get("review_reason"))
                parts.append(self.page_tables([*data.get("utterances", []), *data.get("effects", [])],
                    ["Câu", "Nguồn", "Loại", "Người nói", "Người nghe", "Bản dịch", "Review", "Lý do"], translated, base))
        elif step == "typeset":
            parts.append("Fitted, overflow và unresolved là các trạng thái riêng. Xem ảnh cùng safe/glyph masks để kiểm tra hình thức.")
            for page in data.get("pages", []):
                parts.extend([f"### Trang {page['page']} · {page['style']} · {cell(page.get('render_kind'))}", "", self.image_links(page, base), "",
                    table(["Câu", "Bản dịch render", "Trạng thái", "Mode", "Font px", "Part / dòng", "Shorten attempts", "Review semantic"],
                        ((self.ref(r), r.get("translation"), r.get("status"), r.get("typeset_mode", self.manifest.get("config", {}).get("v6", {}).get("typeset_mode", "ocr-box")),
                          r.get("font", {}).get("size_px"), value([f"{p.get('part_id')}: " + " / ".join(l.get("text", "") for l in p.get("lines", [])) for p in r.get("parts", [])]),
                          value([a.get("error", a.get("review_reason", "")) for a in r.get("attempts", [])]), r.get("semantic_review_required", False)) for r in page.get("plans", []))),
                    table(["Câu", "Style", "Lý do review"], ((r.get("utterance_id"), r.get("style"), r.get("reason")) for r in page.get("needs_review", [])))])
        if step in {'build_identity', 'relationship'}:
            for crop in data.get('reference_crops', []):
                label = (f"Panel {crop['page_id']}:{crop['panel_index']}" if crop.get('kind') == 'panel'
                         else f"MAGI {crop['magi_id']} · d={crop['distance']:.6f}")
                parts.append(f"- {label} · " +
                             link('Crop', self.root / crop['file'], base))
            for issue in [*data.get('needs_review', []), *data.get('crop_issues', [])]:
                parts.append('- Cần review: ' + cell(value(issue)))
        failures = [*data.get("failures", []), *data.get('inference_failures', [])]
        if failures or item.get("error"):
            parts.extend(["### Lỗi thực thi", "", table(["Vị trí", "Lỗi"], ((value(r.get("target_ids", r.get("utterance_id", r.get("page", step)))),
                r.get("error", value(r))) for r in failures)), cell(item.get("error", ""))])
        parts.extend(["", link("← Tổng quan", overview, base), ""])
        return self.display_identifiers("\n".join(parts))

    def write(self, output):
        output = Path(output).resolve()
        source = self.manifest["source"]
        parts = [f"# {cell(source.get('story_title', source.get('story')))} · {cell(source.get('chapter'))} — review v6", "",
            f"Run: {cell(self.root.name)} · Trạng thái: {cell(LABELS.get(self.manifest.get('status'), self.manifest.get('status')))}", "",
            "Review chỉ trình bày kết quả đã lưu, không chạy inference và không thay thế kiểm tra chất lượng/approval stage.", "",
            "Nhân vật dùng số trong bank MAGI (ví dụ MAGI 5). Chưa có số MAGI nghĩa là chưa có liên kết bank; P24 là trang 24.", "",
            "Model đã chạy: " + cell(self.manifest.get("config", {}).get("llm", {}).get("model")) +
            " · Analyze policy: " + cell(self.data.get("analyze", {}).get("policy")), "",
             f"## Tổng quan {len(self.steps)} bước", "",
            table(["Bước", "Trạng thái", "Thời gian", "Kết quả", "Review"], ((f"{i+1:02d} · {step}",
                LABELS.get(self.manifest.get("steps", {}).get(step, {}).get("status", "pending"), "Chưa chạy"),
                runtime(self.manifest.get("steps", {}).get(step, {}).get("runtime_seconds")),
                f"{len(self.data.get(step, {}).get('pages', []))} trang · {len(self.data.get(step, {}).get('utterances', []))} câu · {len(self.data.get(step, {}).get('failures', []))} lỗi",
                 link("Chi tiết", output / self.directories[step] / "review.md", output)) for i, step in enumerate(self.steps))),
            "Tổng thời gian stage ghi nhận: " + runtime(sum(item.get("runtime_seconds", 0) or 0 for item in self.manifest.get("steps", {}).values())), ""]
        for step in self.steps:
            base = output / self.directories[step]
            atomic_bytes(base / "review.md", self.render_step(step, base, output / "review.md").encode("utf-8"))
            # Complete details also appear in the root review, with root-relative links.
            parts.append(self.render_step(step, output, output / "review.md"))
        atomic_bytes(output / "review.md", self.display_identifiers("\n".join(parts)+"\n").encode("utf-8"))
        return output / "review.md"
