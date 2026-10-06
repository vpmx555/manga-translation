"""Lossless MAGI export and its source schema."""
from __future__ import annotations
import copy
import hashlib
import math
from pathlib import Path

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
