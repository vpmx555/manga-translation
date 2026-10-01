"""Narrative scene boundaries from original text and visual evidence."""
from __future__ import annotations

import copy

from dialogue_data import document_fingerprint
from reasoning_batching import PROMPT_PREFIX, TaskRunner
from reasoning_client import BudgetExceeded, ResourceLimit
from reasoning_schemas import SCHEMAS, validate_rows


VISUAL_PROMPT = (
    "Describe only the supplied panel/page: observable setting, explicit time cues and event. "
    "Null is allowed. Do not infer character relationships or treat OCR guesses as verified. "
    "Cite the panel/page or supplied OCR IDs; do not name unverified characters."
)
BOUNDARY_PROMPT = (
    "For every target gap classify whether a NEW NARRATIVE SCENE starts at right_id. "
    "The labels have these exact meanings: no_boundary = left_id and right_id belong to the SAME scene; "
    "boundary = right_id starts a DIFFERENT scene, supported by a narrative break in time, place or event; "
    "uncertain = evidence does not establish either decision. "
    "Sequential text flow, continued thoughts, a reply, or the next panel of the same interaction mean "
    "no_boundary, NOT boundary. A gap between text boxes is not itself a scene boundary. "
    "Example: a question followed by its answer in the same conversation -> no_boundary. "
    "Example: explicit 'the next morning' after the previous evening -> boundary. "
    "Your reason must justify the chosen label: continuity supports no_boundary; only a narrative break supports boundary. "
    "A scene is continuous "
    "narrative time/place/interaction, NOT merely a topic. Page/panel/speaker changes alone do not split scenes. "
    "Use visual changes, explicit temporal narration, event continuity and question-answer continuation. "
    "MAGI speaker assignments and visual observations may be wrong. Consider both sides. "
    "With insufficient evidence return uncertain, never force a desired scene count."
)


def visual_observations(chapter, reasoner) -> dict:
    if chapter.text_only:
        return {}
    cards = {}
    for ref, block in chapter.blocks.items():
        page = chapter.pages[block["page_id"]]
        related = [row for row in chapter.rows if row.get("page_id") == block["page_id"] and
                   (block["panel_index"] is None or row.get("panel_index") == block["panel_index"])]
        data = {"targets": [block], "ocr": [{"id": row["id"], "text": row["text"],
                  "bbox": row.get("bbox"), "content_type": row.get("content_type")} for row in related],
                "page": {"id": page["id"], "page": page["page"], "source_bbox": block["bbox"]},
                "images_in_order": [{"ref": ref}]}
        allowed = {ref, page["id"]} | {r["id"] for r in related}
        def check(result):
            validate_rows(result, [ref], allowed)
        side = reasoner.config.image_max_side
        while True:
            image = chapter.encode_image(ref, side)
            try:
                value = reasoner.infer("visual", PROMPT_PREFIX + VISUAL_PROMPT,
                                       data, SCHEMAS["visual"], [image], check)
                break
            except ResourceLimit:
                if side <= 384:
                    raise
                side = max(384, side // 2)
            except BudgetExceeded:
                # OCR-heavy panels still have the source image; avoid repeating the full transcript here.
                if not data["ocr"]:
                    raise
                data["ocr"] = []
                data["ocr_omitted_due_to_budget"] = True
                allowed = {ref, page["id"]}
        cards[ref] = value["rows"][0]
    return cards


def segment_scenes(chapter, reasoner, *, observations: dict | None = None) -> dict:
    cards = observations if observations is not None else visual_observations(chapter, reasoner)
    runner = TaskRunner(reasoner, chapter, cards)
    rows = chapter.rows
    gaps = [{"id": f"gap:{left['id']}->{right['id']}", "left_id": left["id"], "right_id": right["id"],
             "before_index": index} for index, (left, right) in enumerate(zip(rows, rows[1:]), start=1)]
    def anchors(gap):
        return [gap["before_index"] - 1, gap["before_index"]]
    decisions = runner.execute("boundaries", BOUNDARY_PROMPT, gaps, rows, anchors)
    # Review ownership seams and ambiguous gaps individually with both adjacent turns.
    seams = [gap for i, gap in enumerate(gaps) if decisions[gap["id"]]["decision"] == "uncertain" or
             (i > 0 and i % reasoner.config.max_targets == 0)]
    if seams:
        reviewed = runner.execute("boundaries", BOUNDARY_PROMPT + " Independently review this seam; abstain on unresolved disagreement.",
                                  seams, rows, anchors)
        for gap in seams:
            first, second = decisions[gap["id"]], reviewed[gap["id"]]
            first["review"] = copy.deepcopy(second)
            if first["decision"] != second["decision"]:
                first["decision"] = "uncertain"
                first["reason"] = "Boundary passes disagree; retain soft continuity and request review."
                first["evidence_refs"] = sorted(set(first["evidence_refs"]) | set(second["evidence_refs"]))
    output = copy.deepcopy(chapter.document)
    output["kind"] = "scene_dialogue"
    output["utterances"] = copy.deepcopy(rows)
    output["source_fingerprint"] = chapter.source_fingerprint
    output["scene_boundaries"] = [{**gap, **decisions[gap["id"]]} for gap in gaps]
    output["visual_observations"] = cards
    output["scene_config"] = {"method": "vlm_narrative_boundaries", "model": reasoner.config.model,
                              "model_digest": reasoner.digest, "text_only_ablation": chapter.text_only,
                              "uncertain_boundary_policy": "soft_merge_with_review"}
    current, order = 1, 0
    for index, row in enumerate(output["utterances"]):
        if index and decisions[gaps[index - 1]["id"]]["decision"] == "boundary":
            current, order = current + 1, 0
        row.update({"scene_id": f"{output['document_id']}:s{current:04d}", "scene_status": "predicted",
                    "order_in_scene": order, "original_order": index, "corrected_order": index})
        order += 1
    uncertain = {gap["before_index"] for gap in gaps if decisions[gap["id"]]["decision"] == "uncertain"}
    affected = {output["utterances"][i]["scene_id"] for i in uncertain}
    for row in output["utterances"]:
        if row["scene_id"] in affected:
            row["scene_status"] = "needs_review"
    output["reasoning_warnings"] = runner.warnings
    return output
