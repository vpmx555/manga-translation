"""Source-only routing and anchors; no inferred state participates."""
import copy

from . import persons


def route(rows, raw, state, bank):
    texts = {t["id"]: t for t in raw["texts"]}
    pages = {p["id"]: p for p in raw["pages"]}
    output = []
    for index, source in enumerate(rows):
        row = copy.deepcopy(source)
        caption = any(h.get("bubble_style") == "caption_hint" for h in row.get("visual_evidence", []))
        originals, anchors = [], []
        for identity in row["source_text_ids"]:
            text = texts[identity]
            page = pages[text["page_id"]]
            for ref, observations in state["observations"].items():
                if not ref.startswith(state["extraction_scope"] + ":"):
                    continue
                for obs in observations:
                    if obs["page_id"] != text["page_id"] or obs["character_index"] != text.get("character_detection_index"):
                        continue
                    record = {"source_text_id": identity, "cluster_ref": ref,
                              "bank_id": obs.get("source_bank_id"),
                              "person_id": persons.canonical(state, state["cluster_to_person"][ref])}
                    originals.append(record)
                    ti, tail, char = text.get("text_index"), text.get("tail_index"), text.get("character_detection_index")
                    bank_id = text.get("speaker_id")
                    stable = bank.get("characters", {}).get(str(bank_id))
                    if (not caption and ti is not None and tail is not None and char is not None
                            and [ti, tail] in page.get("text_tail_associations", [])
                            and [ti, char] in page.get("text_character_associations", [])
                            and 0 <= tail < len(page.get("tails", []))
                            and 0 <= char < len(page.get("characters", []))
                            and stable is not None and not stable.get("disabled")
                            and bank_id == record["bank_id"]):
                        anchors.append({**record, "tail_index": tail, "character_index": char})
        people = {a["person_id"] for a in anchors}
        row.update(essential_index=index+1, analysis_branch="caption" if caption else "dialogue",
                   routing_reason="caption_hint" if caption else "non_caption_hint_or_missing_geometry",
                   original_speaker=originals, speaker_anchor={"locked": bool(anchors) and len(people) == 1,
                   "person_id": next(iter(people)) if len(people) == 1 else None, "sources": anchors,
                   "conflicting_person_ids": sorted(people) if len(people) > 1 else [],
                   "origin": "MAGIv2_text_tail_character_stable_bank"})
        output.append(row)
    return output
