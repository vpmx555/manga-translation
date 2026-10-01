"""Lossless MAGI export, conservative normalization, and source-box provenance."""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import unicodedata
from pathlib import Path


CONTENT_TYPES = {"dialogue", "narration", "thought", "unknown", "ui", "watermark", "sfx"}
EXCLUDED_TYPES = {"ui", "watermark", "sfx"}


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, value, *, overwrite: bool = False) -> None:
    """Refuse accidental replacement; serialize fully before opening a new file."""
    data = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w" if overwrite else "x", encoding="utf-8") as handle:
        handle.write(data)


def document_fingerprint(document: dict) -> str:
    data = json.dumps(document, sort_keys=True, ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def _box(value) -> list[float]:
    box = [float(x) for x in value]
    if len(box) != 4 or not all(math.isfinite(x) for x in box):
        raise ValueError(f"Invalid bounding box: {value}")
    if box[2] < box[0] or box[3] < box[1]:
        raise ValueError(f"Reversed bounding box: {value}")
    return box


def panel_for_box(box, panels) -> int | None:
    """Assign to the panel covering most of this text box, or leave unassigned."""
    x1, y1, x2, y2 = box
    areas = [max(0, min(x2, p[2]) - max(x1, p[0]))
             * max(0, min(y2, p[3]) - max(y1, p[1])) for p in panels]
    if not areas or max(areas) <= 0:
        return None
    return max(range(len(areas)), key=areas.__getitem__)


def build_raw_document(images, page_paths, results, *, story_id: str, chapter_id: str,
                       model_fingerprint: str, character_ids=None) -> dict:
    """Export every OCR box, including nonessential text, from ordered MAGI results.

    character_ids is supplied by DynamicCharacterAssigner, not parsed from names.
    MAGI's visual clusters are PAGE-LOCAL. IDs below namespace them by page.
    """
    if not (len(images) == len(page_paths) == len(results)):
        raise ValueError("Images, paths and prediction pages must align")
    if character_ids is not None and len(character_ids) != len(results):
        raise ValueError("Bank identity pages must align with prediction pages")
    document_id = hashlib.sha256(f"{story_id}\0{chapter_id}".encode()).hexdigest()[:16]
    pages, records = [], []
    for page_index, (image, path, result) in enumerate(zip(images, page_paths, results)):
        page_id = f"{document_id}:p{page_index + 1}"
        boxes = [_box(x) for x in result["texts"]]
        panels = [_box(x) for x in result.get("panels", [])]
        characters = [_box(x) for x in result.get("characters", [])]
        tails = [_box(x) for x in result.get("tails", [])]
        ocr = result["ocr"]
        essential = result["is_essential_text"]
        clusters = result["character_cluster_labels"]
        names = result.get("character_names", ["Other"] * len(characters))
        if not len(boxes) == len(ocr) == len(essential):
            raise ValueError(f"OCR/text/essential lengths differ on page {page_index + 1}")
        if not len(characters) == len(clusters) == len(names):
            raise ValueError(f"Character metadata lengths differ on page {page_index + 1}")
        bank_ids = character_ids[page_index] if character_ids is not None else [None] * len(characters)
        if len(bank_ids) != len(characters):
            raise ValueError("Bank IDs must align with character detections")
        associations = {}
        tail_links = {}
        for key, destination, count in (("text_character_associations", associations, len(characters)),
                                         ("text_tail_associations", tail_links, len(tails))):
            for pair in result.get(key, []):
                t, c = map(int, pair)
                if not 0 <= t < len(boxes) or not 0 <= c < count or t in destination:
                    raise ValueError(f"Invalid or duplicate {key}: {pair}")
                destination[t] = c
        pages.append({"id": page_id, "page": page_index + 1, "image_path": str(Path(path).resolve()),
                      "width": int(image.shape[1]), "height": int(image.shape[0]),
                      "panels": panels, "characters": characters, "tails": tails,
                      "character_cluster_labels": [int(x) for x in clusters],
                      "character_ids": bank_ids, "character_display_labels": names,
                      "text_character_associations": result.get("text_character_associations", []),
                      "text_tail_associations": result.get("text_tail_associations", [])})
        for index, (text, box) in enumerate(zip(ocr, boxes)):
            if not isinstance(text, str):
                raise ValueError("OCR values must be strings")
            c = associations.get(index)
            identity = bank_ids[c] if c is not None else None
            status = "unlinked" if c is None else (
                "resolved" if identity is not None else (
                    "pending_identity" if character_ids is not None else "identity_unavailable"))
            records.append({
                "id": f"{page_id}:t{index}", "page_id": page_id, "page": page_index + 1,
                "text_index": index, "reading_order": len(records), "text": text,
                "bbox": box, "panel_index": panel_for_box(box, panels),
                "character_detection_index": c,
                "speaker_cluster_id": f"{page_id}:c{int(clusters[c])}" if c is not None else None,
                "speaker_id": identity, "speaker_display_label": names[c] if c is not None else "Other",
                "speaker_status": status, "tail_index": tail_links.get(index),
                "is_essential_text": bool(essential[index]),
                "speaker_affinity": None,
                "speaker_affinity_source": "not_exposed_by_magi_prediction",
                "association_source": "magi_text_character_associations",
            })
    return {"schema_version": 1, "kind": "magi_raw", "document_id": document_id,
            "story_id": story_id, "chapter_id": chapter_id, "reading_direction": "rtl",
            "reading_order_source": "magi_sorted_panels_and_texts",
            "model_fingerprint": model_fingerprint, "pages": pages, "texts": records}


def import_utterances(value, *, document_id: str) -> dict:
    """Adapter for user-supplied JSON. Never invent geometry or visual links."""
    if isinstance(value, dict):
        value = value.get("utterances", value.get("dialogues", value))
    if not isinstance(value, list) or not all(isinstance(row, dict) for row in value):
        raise ValueError("Expected a record array or an object with utterances/dialogues")
    records = []
    for index, row in enumerate(value):
        if not isinstance(row.get("text"), str):
            raise ValueError(f"Record {index}: text must be a string")
        record = copy.deepcopy(row)
        record["source_record"] = copy.deepcopy(row)
        record.update({"id": f"{document_id}:t{index}", "source_record_index": index,
                       "reading_order": index, "bbox": None, "panel_index": None,
                       "speaker_cluster_id": None, "speaker_status": "external",
                       "association_source": "user_supplied_json", "is_essential_text": None})
        # Original array order is authoritative: scene-local order numbers can reset.
        records.append(record)
    return {"schema_version": 1, "kind": "imported_raw", "document_id": document_id,
            "chapter_id": document_id, "reading_direction": "rtl",
            "reading_order_source": "input_array_order", "pages": [], "texts": records}


def validate_raw(raw: dict) -> None:
    if raw.get("kind") not in {"magi_raw", "imported_raw"} or raw.get("schema_version") != 1:
        raise ValueError("Unsupported raw JSON schema")
    if raw.get("reading_direction") != "rtl":
        raise ValueError("Only right-to-left manga is supported")
    texts = raw.get("texts")
    if not isinstance(texts, list):
        raise ValueError("Raw JSON must contain texts")
    ids = [row.get("id") for row in texts]
    if any(not isinstance(x, str) or not x for x in ids) or len(ids) != len(set(ids)):
        raise ValueError("Source text IDs must be unique nonempty strings")
    if any(not isinstance(row.get("text"), str) for row in texts):
        raise ValueError("Source text must be a string")
    if any(row.get("reading_order") != i for i, row in enumerate(texts)):
        raise ValueError("Texts must be stored in their declared reading order")


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


def normalize_document(raw: dict, *, overrides: dict | None = None, merge_groups: list | None = None) -> dict:
    validate_raw(raw)
    overrides = overrides or {}
    known = {row["id"] for row in raw["texts"]}
    if set(overrides) - known:
        raise ValueError("Content overrides reference unknown source IDs")
    utterances, excluded = [], []
    for source in raw["texts"]:
        kind, keep, reasons = classify_content(source)
        review = "provisional"
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
            continue
        row = copy.deepcopy(source)
        row.update({"id": f"{source['id']}:u", "text_original": source["text"],
                    "source_order_in_scene": source.get("order_in_scene"),
                    "source_scene_id": source.get("scene_id"),
                    "text": re.sub(r"\s+", " ", unicodedata.normalize("NFC", source["text"])).strip(),
                    "source_text_ids": [source["id"]],
                    "source_boxes": [{"source_text_id": source["id"], "page": source.get("page"),
                                      "page_id": source.get("page_id"), "bbox": copy.deepcopy(source.get("bbox")),
                                      "panel_index": source.get("panel_index")}],
                    "content_type": kind, "content_review_status": review,
                    "normalization_reasons": reasons, "scene_id": None,
                    "scene_status": "not_run", "order_in_scene": None})
        utterances.append(row)
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
            "chapter_id": raw.get("chapter_id"), "reading_direction": "rtl",
            "raw_fingerprint": document_fingerprint(raw), "raw_document_id": raw["document_id"],
            "utterances": utterances, "excluded": excluded, "merge_candidates": candidates,
            "scene_config": None,
            "limitations": ["Content heuristics do not identify every UI/watermark/SFX instance; review unknowns.",
                            "No visual scene inference; source boxes are preserved for future inpaint.",
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
    """Future translation helper: uncertain scene cuts never remove nearby turns."""
    validate_normalized(document)
    rows = document["utterances"]
    if not 0 <= index < len(rows) or radius < 0:
        raise ValueError("Invalid utterance index/context radius")
    return copy.deepcopy(rows[max(0, index - radius):min(len(rows), index + radius + 1)])
