"""Detailed Vietnamese reviews for v6 runs (offline, no inference).

Reads saved ``result.json`` artifacts and writes human-readable
``review_detailed.md`` files per step + one per run. Never touches
``result.json`` / ``manifest.json`` / banks.

Why this exists: ``run.py review`` for v6 calls
``manga_pipeline.v6.stages.write_review`` which writes brief English
summaries (status + policy + a bullet per utterance). The old pipeline
(v2-v5) wrote compact Vietnamese tables via
``manga_pipeline.review.renderer.Review`` (speaker, listeners,
mention_type -> ID, evidence, warnings). This script brings that style
to v6 schemas, with focus on:

- analyze: speaker, listeners, mention_type, visual_support,
  resolution/repair/errors, calling_events, validation issues.
- identity: person names, character crops, grouped appearance_refs
  (page/bbox/source_bank_id) with file paths.

Reusable::

    python scripts/v6_detailed_review.py --run-dir <run> [--run-dir <run2> ...]
        [--bank-root live/banks] [--overwrite]

Default output is ``review_detailed.md`` next to each existing
``review.md`` (originals untouched). ``--overwrite`` replaces
``review.md`` instead.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))

from manga_pipeline.review.renderer import LABELS, cell, link, runtime, table  # noqa: E402
from manga_pipeline.storage.runs import V6_STEPS  # noqa: E402

TITLES = {
    "layout": "Koharu — panel va bubble",
    "extract": "MAGI + OCR — text va nhan vat",
    "bubble_geometry": "Hinh hoc bubble va mapping OCR",
    "normalize": "Chuan hoa va gop cau",
    "scan": "Tim ung vien ten",
    "analyze": "Phan tich semantic, nguoi noi/nghe va ten",
    "identity": "Lien ket danh tinh nhan vat",
    "export": "Xuat thoai va hieu ung",
    "inpaint": "Xoa chu, tao nen sach",
    "translate": "Dich natural va localized",
    "typeset": "Dan chu va kiem tra render",
}

CONTENT_VI = {
    "dialogue": "Thoai", "narration": "Loi ke", "thought": "Suy nghi",
    "sfx": "Am thanh", "action_state": "Hanh dong/trang thai",
    "ui": "Giao dien", "watermark": "Watermark", "unknown": "Chua ro",
}
MENTION_VI = {
    "direct_address": "Goi truc tiep", "introduction": "Gioi thieu",
    "reference": "Nhac den", "unknown": "Chua ro kieu",
}


def read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default if default is not None else {}


def page_ref(row: dict, uid: str) -> str:
    page = row.get("page", "?")
    # v6 utterance ids look like "<doc>:p6:g10:u" — keep the local part.
    local = ":".join(str(uid).split(":")[-3:]) if ":" in str(uid) else str(uid)
    try:
        return f"P{int(page):02d}/{local}"
    except (ValueError, TypeError):
        return f"P{page}/{local}"


class DetailedReview:
    """Renders detailed reviews for one v6 run directory."""

    def __init__(self, run_dir: Path, bank_root: Path | None = None):
        self.root = run_dir.resolve()
        self.manifest = read_json(self.root / "manifest.json")
        self.steps = [s for s in V6_STEPS]
        self.dirs = {}
        for i, step in enumerate(self.steps):
            matches = sorted(self.root.glob(f"[0-9][0-9]_{step}"))
            self.dirs[step] = matches[0] if matches else self.root / f"{i + 1:02d}_{step}"
        self.data = {}
        for step in self.steps:
            result = self.dirs[step] / "result.json"
            if result.is_file():
                self.data[step] = read_json(result)
        # text lookup: normalize utterances > export utterances > geometry texts
        self.texts: dict[str, dict] = {}
        for key in ("bubble_geometry", "extract"):
            for t in self.data.get(key, {}).get("texts", []):
                self.texts.setdefault(t.get("id"), t)
        self.rows: dict[str, dict] = {}
        for key in ("normalize", "export"):
            for u in self.data.get(key, {}).get("utterances", []):
                self.rows.setdefault(u.get("id", u.get("utterance_id")), u)
        state = self.data.get("identity", self.data.get("analyze", {})).get("person_state", {})
        self.persons: dict = state.get("person_state", state).get("persons", state.get("persons", {}))
        if not isinstance(self.persons, dict):
            self.persons = {}
        self.redirects: dict = (state.get("person_state", state) or {}).get("redirects", state.get("redirects", {}))
        # person_id -> sorted bank character ids (tu appearance_refs.source_bank_id)
        self.person_to_banks: dict[str, list] = {}
        # full cluster_ref -> bank character id
        self.cluster_to_bank: dict[str, object] = {}
        for pid, person in self.persons.items():
            banks = sorted({r.get("source_bank_id") for r in (person or {}).get("appearance_refs", [])
                            if r.get("source_bank_id") is not None})
            self.person_to_banks[pid] = banks
            for r in (person or {}).get("appearance_refs", []):
                if r.get("cluster_ref") and r.get("source_bank_id") is not None:
                    self.cluster_to_bank.setdefault(r["cluster_ref"], r["source_bank_id"])
        # page_id -> so trang (tu normalize/export)
        self.page_id_to_num: dict[str, object] = {}
        for key in ("normalize", "export"):
            for u in self.data.get(key, {}).get("utterances", []):
                if u.get("page_id") is not None:
                    self.page_id_to_num.setdefault(u["page_id"], u.get("page"))
        # event_id -> source_utterance_id (de hien thi ref cau thay vi hash dai)
        self.event_to_utt: dict[str, str] = {}
        for key in ("analyze", "identity"):
            state_events = (self.data.get(key, {}).get("person_state", {}) or {}).get("calling_events", {})
            if isinstance(state_events, dict):
                for eid, ev in state_events.items():
                    if isinstance(ev, dict) and ev.get("source_utterance_id"):
                        self.event_to_utt.setdefault(eid, ev["source_utterance_id"])
            for ev in self.data.get(key, {}).get("calling_events", []):
                if ev.get("event_id") and ev.get("source_utterance_id"):
                    self.event_to_utt.setdefault(ev["event_id"], ev["source_utterance_id"])
        # MAGI visual bank (crops) resolution
        story = self.manifest.get("source", {}).get("story", "")
        self.bank_dir: Path | None = None
        candidates = []
        if bank_root is not None:
            candidates.append(Path(bank_root) / story)
            candidates.append(Path(bank_root))
        mp = self.manifest.get("bank_path")
        if mp:
            candidates.append(Path(mp))
        for cand in candidates:
            if cand.is_dir() and (cand / "metadata.json").is_file():
                self.bank_dir = cand
                break
        self.bank_meta = read_json(self.bank_dir / "metadata.json") if self.bank_dir else {}
        self.bank_chars = self.bank_meta.get("characters", {}) if isinstance(self.bank_meta, dict) else {}

    # -- shared helpers -------------------------------------------------
    def row(self, uid: str) -> dict:
        return self.rows.get(uid, self.texts.get(uid, {"id": uid}))

    @staticmethod
    def local_cluster(ref) -> str:
        """Rut gon cluster_ref dai thanh dang p6:c3."""
        if not ref:
            return "—"
        parts = str(ref).split(":")
        return ":".join(parts[-2:]) if len(parts) >= 2 else str(ref)

    def person_name(self, pid) -> str | None:
        if not pid:
            return None
        return (self.persons.get(pid, {}) or {}).get("primary_name")

    def person_bank_label(self, pid) -> str:
        """Nhan dang person kieu pipeline cu: 'ID 2 — eliza', khong dung ma hash dai."""
        if not pid:
            return "Chua ro"
        banks = self.person_to_banks.get(pid, [])
        name = self.person_name(pid)
        bank = "/".join(str(b) for b in banks) if banks else ""
        if bank and name:
            return f"ID {bank} — {name}"
        if bank:
            return f"ID {bank}"
        return name or "Chua ro"

    def speaker_label(self, u: dict) -> str:
        """Nguoi noi: uu tien bank char cua cum hien tai, kem ten neu co."""
        pid = u.get("speaker_person_id")
        if not pid:
            return "Chua ro"
        bank = self.cluster_to_bank.get(u.get("speaker_cluster_ref") or "")
        if bank is None:
            banks = self.person_to_banks.get(pid, [])
            bank = "/".join(str(b) for b in banks) if banks else None
        name = self.person_name(pid)
        if bank is not None and name:
            return f"ID {bank} — {name}"
        if bank is not None:
            return f"ID {bank}"
        return name or "Chua ro"

    def identity_name(self, pid) -> str:
        """Ten hien thi o identity: ten nguoi, hoac bank char neu chua co ten."""
        name = self.person_name(pid)
        if name:
            return name
        banks = self.person_to_banks.get(pid, [])
        if banks:
            return f"chua ten (bank {', '.join(str(b) for b in banks)})"
        return "chua ten"

    def utt_ref(self, uid: str) -> str:
        """Doi utterance_id dai thanh ref cau P06/p6:g3:u."""
        return page_ref(self.row(uid), uid)

    def event_ref(self, eid: str) -> str:
        uid = self.event_to_utt.get(eid)
        return self.utt_ref(uid) if uid else str(eid)[:8]

    def page_title(self, page_id: str) -> str:
        num = self.page_id_to_num.get(page_id)
        local = str(page_id).split(":")[-1] if ":" in str(page_id) else str(page_id)
        return f"P{int(num):02d} · {local}" if isinstance(num, int) else local

    def crop_links(self, source_bank_id, base: Path, limit: int = 3) -> str:
        if source_bank_id is None or not self.bank_dir:
            return "—"
        char = self.bank_chars.get(str(source_bank_id), {})
        paths = [self.bank_dir / p for p in char.get("crop_paths", [])[:limit]]
        previews = [link(f"Anh {i + 1}", p, base) for i, p in enumerate(paths) if p.is_file()]
        if previews:
            return " · ".join(previews)
        return f"bank crops thieu (char {source_bank_id})"

    def vis_link(self, page: int, base: Path) -> str:
        for cand in (
            self.dirs["extract"] / "visualizations" / f"page_{page}.png",
            self.dirs["layout"] / "overlays" / f"page-{page}.png",
        ):
            if cand.is_file():
                return link(f"Vis trang {page}", cand, base)
        return "—"

    def failures_block(self, step: str) -> str:
        data = self.data.get(step, {})
        parts = []
        failures = data.get("failures", []) or []
        if failures:
            parts.append(table(["Vi tri", "Loi"], (
                (", ".join(f.get("target_ids", [f.get("utterance_id", f.get("page", step))]))
                 if isinstance(f, dict) else str(f),
                 f.get("error", str(f)) if isinstance(f, dict) else "")
                for f in failures)))
        err = self.manifest.get("steps", {}).get(step, {}).get("error")
        if err:
            parts.append("Loi buoc: " + cell(err) + "\n")
        return "\n".join(parts)

    # -- semantic table (analyze + export) ------------------------------
    def clean_evidence(self, text: str | None, limit: int = 160) -> str:
        """Cat ngan evidence va thay ma person:hash dai bang nhan bank (ID 2 — eliza)."""
        import re as _re
        text = (text or "")[:limit]
        def _sub(match):
            pid = match.group(0)
            if pid in self.persons:
                return self.person_bank_label(pid)
            cands = [k for k in self.persons if k.startswith(pid)]
            return self.person_bank_label(cands[0]) if len(cands) == 1 else pid[:14]
        return _re.sub(r"person:[0-9a-f]{8,}", _sub, text)

    def semantic_rows(self, utterances: list[dict]):
        for u in utterances:
            uid = u.get("utterance_id", u.get("id", "—"))
            src = self.row(uid)
            mentions = []
            for m in u.get("mentions", []):
                surf = m.get("raw_surface", m.get("name_key", m.get("candidate_id", "")))
                mtype = MENTION_VI.get(m.get("mention_type", ""), m.get("mention_type", ""))
                tgt = self.person_bank_label(m.get("target_person_id"))
                vis = m.get("visual_support", "")
                comps = ",".join(f"{c.get('kind', '')}:{c.get('value', '')}" for c in m.get("components", []))
                extra = f" [{comps}]" if comps else ""
                conf = "" if m.get("confirmed_person_name", True) else " (chua xac nhan)"
                mentions.append(f"{surf} / {mtype} → {tgt} [{vis}]{extra}{conf}")
            listeners = u.get("addressee_person_ids", u.get("addressee_ids", [])) or []
            errors = u.get("semantic_errors", []) or []
            if u.get("semantic_resolution") == "unresolved" and not errors:
                # validation table holds the reason; keep pointer
                errors = ["xem bang Validation"]
            cluster = self.local_cluster(u.get("speaker_cluster_ref")) if u.get("speaker_cluster_ref") else ""
            yield (
                page_ref(src, uid),
                src.get("text", u.get("text", "")),
                CONTENT_VI.get(u.get("content_type", ""), u.get("content_type", "")),
                u.get("speech_style", "—"),
                self.speaker_label(u) + (f" [{cluster}]" if cluster else ""),
                "; ".join(self.person_bank_label(p) for p in listeners) or "Chua ro",
                "; ".join(mentions) or "—",
                self.clean_evidence(u.get("evidence", "")),
                u.get("semantic_resolution", "resolved"),
                u.get("repair_count", 0),
                u.get("semantic_needs_review", False),
                "; ".join(errors) or "—",
            )

    def semantic_table(self, utterances: list[dict]) -> str:
        headers = ["Cau", "Noi dung", "Loai", "Speech style", "Nguoi noi",
                   "Nguoi nghe", "Ten / kieu nhac / ID", "Bang chung",
                   "Resolution", "Repair", "Review", "Ly do"]
        groups: dict = {}
        for u in utterances:
            uid = u.get("utterance_id", u.get("id", ""))
            groups.setdefault(self.row(uid).get("page", "?"), []).append(u)
        parts = []
        for page in sorted(groups, key=lambda p: str(p).zfill(5)):
            rows = list(self.semantic_rows(groups[page]))
            parts.append(f"### Trang {page} — {len(rows)} cau\n")
            parts.append(table(headers, rows))
        return "\n".join(parts) if parts else "Khong co.\n"

    def analyze_summary(self, utterances: list[dict]) -> str:
        total = len(utterances)
        unres = [u for u in utterances if u.get("semantic_resolution") != "resolved"]
        review = [u for u in utterances if u.get("semantic_needs_review")]
        repaired = [u for u in utterances if (u.get("repair_count") or 0) > 0]
        by_type = Counter(u.get("content_type", "?") for u in utterances)
        by_mtype = Counter(m.get("mention_type", "?") for u in utterances for m in u.get("mentions", []))
        err = Counter(e for u in utterances for e in (u.get("semantic_errors", []) or []))
        lines = [
            f"Tong {total} cau · resolved {total - len(unres)} · "
            f"unresolved {len(unres)} · can review {len(review)} · da repair {len(repaired)}.",
            "Loai cau: " + ", ".join(f"{k}={v}" for k, v in sorted(by_type.items())) + ".",
            "Kieu nhac ten: " + (", ".join(f"{k}={v}" for k, v in sorted(by_mtype.items())) or "khong co mention") + ".",
        ]
        if err:
            lines.append("Loi semantic hay gap:")
            lines.append(table(["Loi", "So cau"], ([k, v] for k, v in err.most_common(10))))
        return "\n".join(lines) + "\n"

    # -- per-step renderers ---------------------------------------------
    def render_analyze(self, base: Path) -> str:
        data = self.data.get("analyze", {})
        utts = data.get("utterances", [])
        parts = ["## Tom tat\n", self.analyze_summary(utts),
                 "## Từng cau (nguoi noi / nguoi nghe / kieu nhac ten)\n",
                 self.semantic_table(utts),
                 "## Bang chung goi ten\n",
                 table(["Event", "Cau nguon", "Ten", "Nguoi goi", "Cum nguoi nhan",
                        "Person dich", "Visual", "Bang chung"],
                       ((str(e.get("event_id", ""))[:8], self.utt_ref(e.get("source_utterance_id", "")),
                         e.get("name_key", ""), self.person_bank_label(e.get("caller_person_id")),
                         self.local_cluster(e.get("recipient_cluster_ref")),
                         self.person_bank_label(e.get("target_person_id")),
                         e.get("visual_support", ""), self.clean_evidence(e.get("evidence", "")))
                        for e in data.get("calling_events", []))),
                 "## Validation / unresolved\n",
                 table(["Target", "Resolution", "Loi / ly do"],
                       ((self.utt_ref(x["target_id"]) if x.get("target_id") else str(x.get("pair", "")),
                         x.get("resolution", x.get("reason", "")),
                         "; ".join(x.get("errors", [])) if x.get("errors") else str(x.get("reason", "")))
                        for x in data.get("semantic_validation_issues", [])))]
        fb = self.failures_block("analyze")
        if fb:
            parts += ["## Loi thuc thi\n", fb]
        return "\n".join(parts)

    def render_identity(self, base: Path) -> str:
        data = self.data.get("identity", {})
        proposals = data.get("proposals", [])
        stat = Counter(p.get("same_person_status", "?") for p in proposals)
        parts = [
            "MAGI cluster bank va semantic person registry la hai lop rieng; "
            "khong ghi ten semantic vao bank MAGI.\n",
            f"Proposals {len(proposals)} · " + ", ".join(f"{k}={v}" for k, v in sorted(stat.items())) +
            f" · persons {len(self.persons)} · redirects {len(self.redirects)}.\n",
            "## De xuat gop (proposal)\n",
            table(["Ten", "Bank char", "Clusters", "Trang thai", "Co so",
                   "Lan goi xac nhan", "Event refs (cau nguon)", "Mau thuan / mo ho"],
                  ((self.identity_name(p.get("person_id")),
                    "/".join(str(b) for b in self.person_to_banks.get(p.get("person_id"), [])) or "—",
                    "; ".join(self.local_cluster(c)
                              for c in p.get("cluster_refs", [p.get("cluster_ref", "")])),
                    p.get("same_person_status", ""), p.get("decision_basis", ""),
                    p.get("confirmed_event_count", 0),
                    "; ".join(self.event_ref(e) for e in (p.get("event_refs", []) or [])) or "—",
                    "; ".join(p.get("contradictions", []) or []) or
                    ("mo ho: " + ", ".join(self.person_bank_label(x)
                                           for x in p.get("ambiguous_target_persons", []))
                     if p.get("ambiguous_target_persons") else "—"))
                   for p in proposals)),
        ]
        # per-person detail with appearance groups + crops
        for pid, person in sorted(self.persons.items(), key=lambda kv: (not (kv[1] or {}).get("primary_name"), str(kv[0]))):
            refs = person.get("appearance_refs", []) or []
            by_page: dict = {}
            for r in refs:
                by_page.setdefault(r.get("page_id", "?"), []).append(r)
            parts.append(f"### {self.identity_name(pid)}\n")
            forms = "; ".join(
                f"{f.get('surface', '')} ({f.get('kind', '')}/{f.get('status', '')})"
                for f in person.get("name_forms", [])) or "—"
            redir = self.redirects.get(pid)
            redir_label = self.person_bank_label(redir) if redir else ""
            parts.append(f"Bank char: "
                         f"{'/'.join(str(b) for b in self.person_to_banks.get(pid, [])) or '—'} · "
                         f"Name forms: {cell(forms)}" +
                         (f" · Redirect → {redir_label}" if redir else "") + "\n")
            parts.append(table(["Trang", "So det", "Vi tri gom (bbox / char / bank char)", "Anh crop", "Anh trang"],
                               ((self.page_title(page_id),
                                 len(rs),
                                 "; ".join(
                                     f"{r.get('bbox', '')} c{r.get('character_index', '?')}→bank:{r.get('source_bank_id', '?')}"
                                     for r in rs[:4]) + (" …" if len(rs) > 4 else ""),
                                 self.crop_links(rs[0].get("source_bank_id"), base),
                                 self.vis_link(self._page_no(rs[0]), base))
                                for page_id, rs in sorted(by_page.items()))))
        if self.redirects:
            parts.append("## Redirects\n")
            parts.append(table(["Cu", "Moi"], ((self.person_bank_label(k), self.person_bank_label(v))
                                               for k, v in sorted(self.redirects.items()))))
        fb = self.failures_block("identity")
        if fb:
            parts += ["## Loi thuc thi\n", fb]
        return "\n".join(parts)

    @staticmethod
    def _page_no(ref: dict) -> int:
        page_id = str(ref.get("page_id", ""))
        try:
            return int(page_id.rsplit(":p", 1)[1])
        except (ValueError, IndexError):
            return 0

    def render_scan(self, base: Path) -> str:
        data = self.data.get("scan", {})
        cands = data.get("candidates", [])
        by_src = Counter(c.get("source", "?") for c in cands)
        parts = [f"Tong {len(cands)} ung vien (" +
                 ", ".join(f"{k}={v}" for k, v in sorted(by_src.items())) +
                 "). Day chua phai ket luan ten–ID; doi chieu voi analyze.\n"]
        rows = []
        for c in cands:
            uid = c.get("utterance_id", "")
            rows.append((page_ref(self.row(uid), uid),
                         c.get("raw_surface", c.get("name", "")),
                         f"{c.get('start', '')}:{c.get('end', '')}",
                         c.get("source", ""),
                         "; ".join(self.person_bank_label(p)
                                   for p in c.get("candidate_person_ids", [])) or "—",
                         (self.row(uid).get("text", "") or "")[:160]))
        parts.append(table(["Cau", "Ten / surface", "Span", "Nguon", "Person ung vien", "Nguyen cau"], rows))
        fb = self.failures_block("scan")
        if fb:
            parts += ["## Loi thuc thi\n", fb]
        return "\n".join(parts)

    def render_export(self, base: Path) -> str:
        data = self.data.get("export", {})
        return ("### Thoai / narration / thought\n\n" + self.semantic_table(data.get("utterances", [])) +
                "\n### Hieu ung — khong render ban dich vao anh\n\n" +
                self.semantic_table(data.get("effects", [])))

    def render_translate(self, base: Path) -> str:
        data = self.data.get("translate", {})
        parts = []
        all_rows = list(data.get("utterances", [])) + list(data.get("effects", []))
        flagged = [(u, s) for u in all_rows for s in ("natural", "localized")
                   if u.get("translations", {}).get(s, {}).get("needs_review")]
        parts.append(f"Tong {len(data.get('utterances', []))} thoai + {len(data.get('effects', []))} hieu ung · "
                     f"{len(flagged)} co needs_review · {len(data.get('failures', []))} failures.\n")
        if flagged:
            rows = []
            for u, style in flagged[:200]:
                uid = u.get("utterance_id", u.get("id", ""))
                t = u.get("translations", {}).get(style, {})
                rows.append((page_ref(self.row(uid), uid), style,
                             (u.get("text", "") or "")[:120],
                             (t.get("translation", "") or "")[:160],
                             self.clean_evidence(t.get("review_reason", ""), 400)))
            parts.append(table(["Cau", "Style", "Nguon", "Ban dich", "Ly do review"], rows))
            if len(flagged) > 200:
                parts.append(f"… con {len(flagged) - 200} co review (xem result.json).\n")
        for style in ("natural", "localized"):
            rows = []
            for u in all_rows:
                uid = u.get("utterance_id", u.get("id", ""))
                t = u.get("translations", {}).get(style, {})
                if not t.get("translation"):
                    rows.append((page_ref(self.row(uid), uid),
                                 (u.get("text", "") or "")[:120], "THIEU BAN DICH"))
            if rows:
                parts.append(f"### Thieu ban dich {style}: {len(rows)} cau\n")
                parts.append(table(["Cau", "Nguon", "Trang thai"], rows))
        fb = self.failures_block("translate")
        if fb:
            parts += ["## Loi thuc thi\n", fb]
        return "\n".join(parts)

    def render_typeset(self, base: Path) -> str:
        data = self.data.get("typeset", {})
        pages = data.get("pages", [])
        nplans = sum(len(p.get("plans", [])) for p in pages)
        nrev = sum(len(p.get("needs_review", [])) for p in pages)
        stat = Counter(r.get("status", "?") for p in pages for r in p.get("plans", []))
        reasons = Counter(r.get("reason", "?") for p in pages for r in p.get("needs_review", []))
        parts = [f"Tong {len(pages)} trang render · {nplans} plans · {nrev} can review · " +
                 ", ".join(f"{k}={v}" for k, v in sorted(stat.items())) + ".\n",
                 "Ly do review: " + (", ".join(f"{k}={v}" for k, v in reasons.most_common()) or "—") + ".\n"]
        for page in pages:
            files = page.get("files", {})
            rendered = [self.root / name for name in files
                        if name.endswith("rendered.png")]
            links = " · ".join(link(Path(str(r)).name, r, base) for r in rendered[:4] if r.is_file())
            parts.append(f"### Trang {page.get('page')} · {page.get('style')} · {cell(page.get('render_kind'))}\n")
            if links:
                parts.append(links + "\n")
            needs = {(r.get("utterance_id"), r.get("style")): r.get("reason", "")
                     for r in page.get("needs_review", [])}
            rows = []
            for plan in page.get("plans", []):
                uid = plan.get("utterance_id", "")
                key = (uid, page.get("style"))
                rows.append((page_ref(self.row(uid), uid),
                             (plan.get("translation", "") or "")[:120],
                             plan.get("status", ""),
                             ((plan.get("font", {}) or {}).get("size_px", "")),
                             needs.get(key, "")))
            parts.append(table(["Cau", "Ban dich render", "Trang thai", "Font px", "Ly do review"], rows))
        fb = self.failures_block("typeset")
        if fb:
            parts += ["## Loi thuc thi\n", fb]
        return "\n".join(parts)

    def render_compact(self, step: str, base: Path) -> str:
        data = self.data.get(step, {})
        if step == "layout":
            rows = [(p.get("page", ""), len(p.get("panels", [])),
                     len(p.get("bubbles", [])),
                     self.vis_link(p.get("page", 0), base)) for p in data.get("pages", [])]
            return (f"Tong {len(data.get('pages', []))} trang. Toa do theo anh goc.\n" +
                    table(["Trang", "Panels", "Bubbles", "Anh"], rows))
        if step == "extract":
            texts = data.get("texts", [])
            ess = sum(1 for t in texts if t.get("is_essential_text"))
            rows = [(page_ref(t, t.get("id", "")), (t.get("text", "") or "")[:120],
                     f"{t.get('speaker_id', '')}/{t.get('speaker_cluster_id', '')}",
                     t.get("is_essential_text", "")) for t in texts[:500]]
            out = [f"Tong {len(texts)} text ({ess} essential). MAGI association chi la goi y.\n",
                   table(["Text", "OCR", "MAGI ID / cluster", "Essential"], rows)]
            if len(texts) > 500:
                out.append(f"… con {len(texts) - 500} text (xem result.json).\n")
            out.append(self.vis_link(1, base) + "\n")
            return "\n".join(out)
        if step == "bubble_geometry":
            pages = data.get("pages", [])
            texts = data.get("texts", [])
            unmapped = [t for t in texts if t.get("mapping_status") not in ("mapped", None)]
            out = [f"Tong {len(pages)} trang · {len(texts)} text · {len(unmapped)} mapping can chu y.\n"]
            if unmapped:
                out.append(table(["Text", "Group", "Mapping", "Coverage"],
                                 ((page_ref(t, t.get("id", "")), t.get("group_id", ""),
                                   t.get("mapping_status", ""), t.get("coverage", ""))
                                  for t in unmapped[:100])))
            fb = self.failures_block(step)
            if fb:
                out += ["## Loi thuc thi\n", fb]
            return "\n".join(out)
        if step == "normalize":
            data_n = data
            rows = [(page_ref(self.row(u.get("id", "")), u.get("id", "")),
                     str(u.get("text_original", ""))[:120], (u.get("text", "") or "")[:120],
                     u.get("content_review_status", "")) for u in data_n.get("utterances", [])]
            out = [f"Giu {len(data_n.get('utterances', []))} cau · loai {len(data_n.get('excluded', []))} · "
                   f"translation_only {len(data_n.get('translation_only', []))} · "
                   f"effects {len(data_n.get('effects', []))}.\n"]
            changed = [r for r in rows if r[1] != r[2]]
            out.append(f"### Cau doi text/gop: {len(changed)}\n")
            out.append(table(["Cau", "Truoc", "Sau", "Review"], changed[:200] or [["—", "—", "—", "—"]]))
            if data_n.get("excluded"):
                out.append(table(["Nguon", "Ly do"],
                                 ((str(x.get("source_text_ids", x.get("source_text_id", ""))),
                                   str(x.get("reasons", ""))) for x in data_n.get("excluded", []))))
            return "\n".join(out)
        if step == "inpaint":
            rows = []
            for page in data.get("pages", []):
                clean = self.root / str(page.get("clean_path", ""))
                rows.append((page.get("page", ""), page.get("aot_calls", 0),
                             link("Nen sach", clean, base) if clean.is_file() else "thieu",
                             sum(1 for d in page.get("decisions", []) if d.get("requires_review"))))
            return (f"Tong {len(data.get('pages', []))} trang nen sach.\n" +
                    table(["Trang", "AOT calls", "Nen sach", "Can review"], rows))
        return "Chua co noi dung chi tiet (buoc chua commit hoac thieu result.json).\n"

    # -- file writers ---------------------------------------------------
    def render_step(self, step: str, base: Path, overview: Path) -> str:
        data = self.data.get(step, {})
        item = self.manifest.get("steps", {}).get(step, {})
        idx = self.steps.index(step) + 1
        parts = [f"# {idx:02d} · {step} — {TITLES[step]}\n",
                 f"{LABELS.get(item.get('status', 'pending'), item.get('status', ''))} · "
                 f"thoi gian {runtime(item.get('runtime_seconds'))} · "
                 f"policy `{data.get('policy', data.get('geometry_policy', '—'))}`.\n",
                 link("JSON du lieu", self.dirs[step] / "result.json", base) + "\n"]
        if step not in self.data:
            parts.append("Chua co ket qua completed/partial (thieu result.json).\n")
        elif step == "analyze":
            parts.append(self.render_analyze(base))
        elif step == "identity":
            parts.append(self.render_identity(base))
        elif step == "scan":
            parts.append(self.render_scan(base))
        elif step == "export":
            parts.append(self.render_export(base))
        elif step == "translate":
            parts.append(self.render_translate(base))
        elif step == "typeset":
            parts.append(self.render_typeset(base))
        else:
            parts.append(self.render_compact(step, base))
        parts.append(link("← Tong quan", overview, base) + "\n")
        return "\n".join(parts)

    def render_index(self, filename: str) -> str:
        source = self.manifest.get("source", {})
        steps_state = self.manifest.get("steps", {})
        measured = [s.get("runtime_seconds") for s in steps_state.values()
                    if isinstance(s.get("runtime_seconds"), (int, float))]
        parts = [f"# {cell(source.get('story_title', source.get('story', '')))} · "
                 f"{cell(source.get('chapter', ''))} — review chi tiet\n",
                 f"Run: {cell(self.root.name)} · "
                 f"Trang thai: {cell(LABELS.get(self.manifest.get('status'), self.manifest.get('status')))} · "
                 f"Tong run-time: {runtime(sum(measured)) if measured else '—'} · "
                 f"{len(measured)}/{len(self.steps)} buoc co timing.\n",
                 "Ban doc gon tu result.json da luu, khong chay inference. "
                 "Sua review khong doi ket qua pipeline.\n",
                 "## Cac buoc\n",
                 table(["Buoc", "Trang thai", "Run-time", "Ket qua", "Mo file"],
                       ((f"{i + 1:02d} · {step}",
                         LABELS.get(steps_state.get(step, {}).get("status", "pending"), "Chua chay"),
                         runtime(steps_state.get(step, {}).get("runtime_seconds")),
                         self.summarize(step),
                         link("Chi tiet", self.dirs[step] / filename, self.root))
                        for i, step in enumerate(self.steps)))]
        parts.append("## Can kiem tra\n")
        parts.append(self.needs_check())
        parts.append("## Nhan vat (tom tat)\n")
        named = sum(1 for p in self.persons.values() if (p or {}).get("primary_name"))
        parts.append(f"Persons {len(self.persons)} · co ten {named} · "
                     f"chua ten {len(self.persons) - named} · redirects {len(self.redirects)}. "
                     f"Chi tiet anh + vi tri gom xem ở {link('identity', self.dirs['identity'] / filename, self.root)}.\n")
        return "\n".join(parts)

    def summarize(self, step: str) -> str:
        d = self.data.get(step, {})
        if step in ("layout", "inpaint"):
            return f"{len(d.get('pages', []))} trang"
        if step == "bubble_geometry":
            texts = d.get("texts", [])
            mapped = sum(1 for t in texts if t.get("mapping_status") == "mapped")
            return f"{len(d.get('pages', []))} trang, {mapped}/{len(texts)} text mapped"
        if step == "extract":
            return f"{len(d.get('pages', []))} trang, {len(d.get('texts', []))} text"
        if step == "normalize":
            return f"{len(d.get('utterances', []))} cau giu"
        if step == "scan":
            return f"{len(d.get('candidates', []))} ung vien"
        if step == "analyze":
            u = d.get("utterances", [])
            un = sum(1 for x in u if x.get("semantic_resolution") != "resolved")
            return f"{len(u)} cau, {un} unresolved"
        if step == "identity":
            return f"{len(d.get('proposals', []))} proposals, {len(self.persons)} persons"
        if step == "export":
            return f"{len(d.get('utterances', []))} cau + {len(d.get('effects', []))} effects"
        if step == "translate":
            return f"{len(d.get('utterances', []))} cau + {len(d.get('effects', []))} effects"
        if step == "typeset":
            n = sum(len(p.get("needs_review", [])) for p in d.get("pages", []))
            return f"{len(d.get('pages', []))} trang, {n} can review"
        return "—"

    def needs_check(self) -> str:
        a = self.data.get("analyze", {})
        utts = a.get("utterances", [])
        un = [u for u in utts if u.get("semantic_resolution") != "resolved"]
        t = self.data.get("typeset", {})
        trev = sum(len(p.get("needs_review", [])) for p in t.get("pages", []))
        lines = [f"- Analyze unresolved: {len(un)}/{len(utts)} cau."]
        if un:
            by_err: Counter = Counter(e for u in un for e in (u.get("semantic_errors", []) or ["(xem validation)"]))
            lines.append(table(["Loi", "So cau"], ([k, v] for k, v in by_err.most_common(8))))
            lines.append("Mau unresolved: " + "; ".join(
                page_ref(self.row(u.get("utterance_id", "")), u.get("utterance_id", "")) for u in un[:12]) +
                (" …" if len(un) > 12 else "") + "\n")
        lines.append(f"- Typeset can review: {trev} (xem chi tiet 11_typeset).")
        lines.append(f"- Validation issues: {len(a.get('semantic_validation_issues', []))} · "
                     f"calling events: {len(a.get('calling_events', []))} · "
                     f"translate failures: {len(self.data.get('translate', {}).get('failures', []))}.")
        return "\n".join(lines) + "\n"

    def write(self, filename: str = "review_detailed.md") -> Path:
        for step in self.steps:
            base = self.dirs[step]
            base.mkdir(parents=True, exist_ok=True)
            (base / filename).write_text(
                self.render_step(step, base, self.root / filename), encoding="utf-8")
        path = self.root / filename
        path.write_text(self.render_index(filename), encoding="utf-8")
        return path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", action="append", required=True, type=Path,
                        help="Mot run v6 (co the lap lai nhieu lan)")
    parser.add_argument("--bank-root", type=Path, default=None,
                        help="Thu muc banks (mac dinh: <outputs>/../../banks)")
    parser.add_argument("--overwrite", action="store_true",
                        help="Ghi de review.md thay vi review_detailed.md")
    args = parser.parse_args(argv)
    filename = "review.md" if args.overwrite else "review_detailed.md"
    for run_dir in args.run_dir:
        bank_root = args.bank_root
        if bank_root is None:
            # <live>/outputs/<story>/<chapter>/<run> -> <live>/banks
            try:
                bank_root = run_dir.resolve().parents[3] / "banks"
            except IndexError:
                bank_root = None
        review = DetailedReview(run_dir, bank_root)
        path = review.write(filename)
        print(f"Wrote {path} "
              f"({len(review.data.get('analyze', {}).get('utterances', []))} analyze utterances, "
              f"{len(review.persons)} persons)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
