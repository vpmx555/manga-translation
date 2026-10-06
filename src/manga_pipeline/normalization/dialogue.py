"""Conservative normalization preserving every source box and original order."""
from __future__ import annotations
import copy
import re
import unicodedata

from ..extraction.document import validate_raw
from ..policies import is_essential_policy
from ..storage.io import fingerprint as document_fingerprint

CONTENT_TYPES = {"dialogue", "narration", "thought", "unknown", "ui", "watermark", "sfx"}
EXCLUDED_TYPES = {"ui", "watermark", "sfx"}

def classify_content(record: dict) -> tuple[str, bool, list[str]]:
    """A deliberately narrow heuristic, NOT a complete manga SFX classifier."""
    text = re.sub(r"\s+", " ", record["text"]).strip()
    linked = record.get("character_detection_index") is not None or record.get("speaker_id") is not None
    if not text:
        return "unknown", False, ["empty_ocr"]
    candidate = None
    if re.search(r"(?:https?://|www\.)\S+|\b(?:scanlated by|scanlation credits)\b", text, re.I):
        candidate = "watermark"
    elif re.fullmatch(r"(?:sort by(?: newest| oldest| top)?|sign in to comment|log in to comment|"
                      r"report this comment|load more comments|next chapter|previous chapter)", text, re.I):
        candidate = "ui"
    elif re.match(r"^(?:sfx|sound effect)\s*:", text, re.I):
        candidate = "sfx"
    if candidate:
        # Linked or externally supplied dialogue may quote UI/URLs/SFX: retain for review.
        if not linked and record.get("association_source") != "user_supplied_json":
            return candidate, False, [f"explicit_{candidate}_pattern"]
        return "unknown", True, [f"possible_{candidate}_requires_review"]
    if re.match(r"^(?:meanwhile\b|(?:\d+|one|two|three|many|several) (?:years?|days?|hours?) later\b)", text, re.I):
        return "narration", True, ["temporal_narration_candidate"]
    if linked and record.get("is_essential_text") is True:
        return "dialogue", True, ["magi_linked_essential_candidate"]
    return "unknown", True, ["content_type_requires_review"]


def normalize_document(raw: dict, *, overrides: dict | None = None, merge_groups: list | None = None,
                       progress=None, essential_only=False, analysis_policy=None, noise_rules=None) -> dict:
    policy = analysis_policy or ("essential-v1" if essential_only else "legacy")
    if essential_only != is_essential_policy(policy):
        raise ValueError("Normalization policy must match essential_only")
    validate_raw(raw)
    overrides = overrides or {}
    known = {row["id"] for row in raw["texts"]}
    if set(overrides) - known:
        raise ValueError("Content overrides reference unknown source IDs")
    utterances, excluded, translation_only = [], [], []
    if progress is not None:
        progress.reset(total=len(raw["texts"]), unit="text")
    for source in raw["texts"]:
        noise_reason, noise_review = None, False
        if noise_rules is not None and source["id"] not in overrides:
            from .noise import decide
            decision, noise_kind, noise_reason = decide(source["text"], noise_rules)
            noise_review = decision == "review"
            if decision == "exclude":
                excluded.append({"source_text_id": source["id"], "text_original": source["text"],
                    "page": source.get("page"), "bbox": copy.deepcopy(source.get("bbox")),
                    "panel_index": source.get("panel_index"), "content_type": noise_kind,
                    "reasons": [noise_reason], "review_status": "rule_excluded"})
                if progress is not None:
                    progress.advance()
                continue
        if essential_only and source.get("is_essential_text") is not True:
            if source["id"] in overrides:
                override = overrides[source["id"]]
                if (not isinstance(override, dict) or override.get("content_type") not in CONTENT_TYPES
                        or override.get("confirmed") is not True):
                    raise ValueError("Override requires supported content_type and confirmed=true")
                if override["content_type"] in EXCLUDED_TYPES:
                    excluded.append({"source_text_id": source["id"], "text_original": source["text"],
                        "content_type": override["content_type"], "reasons": ["confirmed_content_override"],
                        "review_status": "confirmed"})
                    if progress is not None:
                        progress.advance()
                    continue
            entry = {"source_text_id": source["id"], "page": source.get("page"),
                                     "reading_order": source.get("reading_order"), "text": source["text"],
                                     "speaker_id": None, "addressee_ids": []}
            if noise_review:
                entry.update(content_review_status="needs_review", normalization_reasons=[noise_reason])
            if noise_rules is not None:
                entry.update(page_id=source.get("page_id"), text_index=source.get("text_index"),
                             bbox=copy.deepcopy(source.get("bbox")), panel_index=source.get("panel_index"))
            translation_only.append(entry)
            if progress is not None:
                progress.advance()
            continue
        kind, keep, reasons = classify_content(source)
        if essential_only:
            kind, keep, reasons = "unknown", bool(source["text"].strip()), ["magi_essential"]
        review = "provisional"
        if noise_review:
            review = "needs_review"
            reasons.append(noise_reason)
        if source["id"] in overrides:
            override = overrides[source["id"]]
            if not isinstance(override, dict) or override.get("content_type") not in CONTENT_TYPES:
                raise ValueError("Override requires a supported content_type")
            if override.get("confirmed") is not True:
                raise ValueError("Content overrides must be explicitly confirmed")
            kind = override["content_type"]
            keep = kind not in EXCLUDED_TYPES and bool(source["text"].strip())
            reasons, review = ["confirmed_content_override"], "confirmed"
        if not keep:
            excluded.append({"source_text_id": source["id"], "content_type": kind,
                             "reasons": reasons, "review_status": review})
            if progress is not None:
                progress.advance()
            continue
        row = copy.deepcopy(source)
        row.update({"id": f"{source['id']}:u", "text_original": source["text"],
                    "text": re.sub(r"\s+", " ", unicodedata.normalize("NFC", source["text"])).strip(),
                    "source_text_ids": [source["id"]],
                    "source_boxes": [{"source_text_id": source["id"], "page": source.get("page"),
                                      "page_id": source.get("page_id"), "bbox": copy.deepcopy(source.get("bbox")),
                                      "panel_index": source.get("panel_index")}],
                    "content_type": kind, "content_review_status": review,
                    "normalization_reasons": reasons})
        utterances.append(row)
        if progress is not None:
            progress.advance()
    positions = {row["source_text_ids"][0]: i for i, row in enumerate(utterances)}
    grouped, consumed = {}, set()
    for group in merge_groups or []:
        if group.get("confirmed") is not True or not group.get("reason"):
            raise ValueError("Merge groups require confirmed=true and a reason")
        ids = group.get("source_text_ids", [])
        if len(ids) < 2 or len(ids) != len(set(ids)) or any(x not in positions for x in ids):
            raise ValueError("Merge groups must reference distinct retained source boxes")
        indices = [positions[x] for x in ids]
        if indices != list(range(indices[0], indices[0] + len(ids))) or consumed.intersection(indices):
            raise ValueError("Merge groups must be contiguous, ordered and nonoverlapping")
        rows = [utterances[i] for i in indices]
        first = rows[0]
        if first.get("panel_index") is None or first.get("bbox") is None:
            raise ValueError("Merging requires known source geometry and panel")
        def identity(record):
            if record.get("speaker_id") is not None:
                return ("bank", record["speaker_id"])
            if record.get("speaker_cluster_id") is not None:
                return ("visual_cluster", record["speaker_cluster_id"])
            if record.get("character_detection_index") is not None:
                return ("detection", record.get("page_id"), record["character_detection_index"])
            return None
        if identity(first) is None:
            raise ValueError("Unknown speakers are not evidence of a shared utterance")
        if any((r.get("page_id"), r.get("panel_index"), identity(r), r["content_type"])
               != (first.get("page_id"), first.get("panel_index"), identity(first), first["content_type"]) for r in rows):
            raise ValueError("Merging across page, panel, content type or speaker is unsupported")
        combined = copy.deepcopy(first)
        combined.update({"text": " ".join(r["text"] for r in rows),
                         "text_original": [r["text_original"] for r in rows], "source_text_ids": ids,
                         "source_boxes": [box for r in rows for box in r["source_boxes"]],
                         "bbox": None, "merge_reason": group["reason"], "merge_status": "confirmed"})
        grouped[indices[0]] = combined
        consumed.update(indices)
    utterances = [grouped[i] if i in grouped else row for i, row in enumerate(utterances)
                  if i not in consumed or i in grouped]
    candidates = []
    for left, right in zip(utterances, utterances[1:]):
        same_geometry = (left.get("page_id") is not None and left.get("page_id") == right.get("page_id")
                         and left.get("panel_index") is not None and left.get("panel_index") == right.get("panel_index"))
        same_detection = (left.get("character_detection_index") is not None
                          and left.get("character_detection_index") == right.get("character_detection_index"))
        same_tail = left.get("tail_index") is not None and left.get("tail_index") == right.get("tail_index")
        if same_geometry and same_detection and same_tail and not re.search(r"[.!?…][\"')\]]?$", left["text"]):
            candidates.append({"source_text_ids": left["source_text_ids"] + right["source_text_ids"],
                               "reason": "adjacent_same_panel_detection_tail_incomplete_text", "confirmed": False})
    for index, row in enumerate(utterances):
        row["reading_order"] = index
    return {"schema_version": 1, "kind": "normalized_dialogue", "document_id": raw["document_id"],
            "story_id": raw.get("story_id"), "chapter_id": raw.get("chapter_id"), "reading_direction": "rtl",
            "raw_fingerprint": document_fingerprint(raw), "raw_document_id": raw["document_id"],
            "utterances": utterances, "excluded": excluded, "translation_only": translation_only,
            "analysis_policy": policy,
            "merge_candidates": candidates,
            "limitations": ["Content heuristics do not identify every UI/watermark/SFX instance; review unknowns.",
                            "Source boxes and reading order are preserved.",
                            "No semantic speaker correction or addressee inference."]}


def validate_normalized(document: dict) -> None:
    if document.get("schema_version") != 1 or document.get("kind") != "normalized_dialogue":
        raise ValueError("Expected normalized_dialogue schema v1")
    rows = document.get("utterances")
    if not isinstance(rows, list):
        raise ValueError("Normalized dialogue requires utterances")
    ids = [row.get("id") for row in rows]
    if any(not isinstance(i, str) or not i for i in ids) or len(ids) != len(set(ids)):
        raise ValueError("Utterance IDs must be unique")
    if any(not isinstance(row.get("text"), str) or not row["text"].strip() for row in rows):
        raise ValueError("Utterance text must be nonempty")
    if any(row.get("reading_order") != i for i, row in enumerate(rows)):
        raise ValueError("Utterances must be stored in reading order")


def context_for_utterance(document: dict, index: int, *, radius: int = 2) -> list[dict]:
    """Return source-order context, including turns across page boundaries."""
    validate_normalized(document)
    rows = document["utterances"]
    if not 0 <= index < len(rows) or radius < 0:
        raise ValueError("Invalid utterance index/context radius")
    return copy.deepcopy(rows[max(0, index - radius):min(len(rows), index + radius + 1)])
