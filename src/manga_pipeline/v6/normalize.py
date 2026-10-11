"""One physical group becomes one utterance; all raw observations survive."""
import copy
import re
import unicodedata
from ..extraction.document import validate_raw
from ..storage.io import fingerprint


def normalize(raw):
    validate_raw(raw)
    pages = {p["id"]: p for p in raw["pages"]}
    groups = {g["id"]: g for p in raw["pages"] for g in p.get("bubble_groups", [])}
    collected, excluded = {}, []
    for source in raw["texts"]:
        text = re.sub(r"\s+", " ", unicodedata.normalize("NFC", source["text"])).strip()
        if not text:
            excluded.append({"source_text_id": source["id"], "reason": "empty_ocr", "source": copy.deepcopy(source)})
            continue
        gid = source.get("group_id")
        collected.setdefault(gid or source["id"], []).append((source, text))
    rows = []
    for key, pairs in sorted(collected.items(), key=lambda x: x[1][0][0]["reading_order"]):
        sources = [s for s, _ in pairs]
        first = sources[0]
        group = groups.get(key)
        raw_links = [{"source_text_id": s["id"], "speaker_cluster_id": s.get("speaker_cluster_id"),
                      "source_speaker_id": s.get("speaker_id"),
                      "character_detection_index": s.get("character_detection_index"),
                      "association_source": s.get("association_source")} for s in sources]
        row = {"id": key+":u", "group_id": key if group else None,
               "page": first.get("page"), "page_id": first.get("page_id"),
               "panel_index": first.get("panel_index"),
               "panel_indices": copy.deepcopy(group.get("panel_indices", [])) if group else [first.get("panel_index")],
               "bbox": copy.deepcopy(group["bbox"] if group else first.get("bbox")),
               "text": " ".join(t for _, t in pairs),
               "text_original": [s["text"] for s in sources],
               "source_text_ids": [s["id"] for s in sources],
               "source_boxes": [copy.deepcopy(s) | {"source_text_id": s["id"]} for s in sources],
               "raw_character_candidates": raw_links,
               "part_ids": [p["id"] for p in group["parts"]] if group else [],
               "visual_evidence": [copy.deepcopy(p["visual_evidence"]) | {"part_id": p["id"]}
                                   for p in group["parts"]] if group else [],
               "role_evidence": [copy.deepcopy(s.get("role_evidence", {})) for s in sources],
               "content_type": "unknown", "content_review_status": "provisional",
               "speaker_id": None, "speaker_person_id": None, "speaker_display_label": "Other",
               "addressee_ids": [], "is_essential_text": any(s.get("is_essential_text") for s in sources),
               "reading_order": len(rows), "normalization_reasons": ["physical_group_v6" if group else "standalone_text"]}
        rows.append(row)
    result = {"schema_version": 1, "pipeline_version": 6, "kind": "normalized_dialogue",
              "document_id": raw["document_id"], "story_id": raw.get("story_id"),
              "chapter_id": raw.get("chapter_id"), "reading_direction": "rtl",
              "raw_fingerprint": fingerprint(raw), "analysis_policy": "essential-v3",
              "utterances": rows, "source_boxes": copy.deepcopy(raw["texts"]),
              "bubble_groups": copy.deepcopy(list(groups.values())), "excluded": excluded,
              "translation_only": [], "effects": [], "merge_candidates": [], "failures": []}
    retained = [i for row in rows for i in row["source_text_ids"]]
    if len(retained) != len(set(retained)):
        raise ValueError("A source text was duplicated across utterances")
    return result
