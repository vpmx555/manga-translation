"""Stage 3A-C and constrained 3D, preserving source records and review status."""
from __future__ import annotations

import copy

from dialogue_ordering import reorder_dialogue
from reasoning_batching import TaskRunner
from reasoning_schemas import id_key


SPEAKER_PROMPT = (
    "Stage 3A: correct the utterance's speaker association using bubble/tail geometry, actual image "
    "and dialogue context. Select a supplied visual cluster and/or identity candidate; off-screen speech "
    "is allowed. Do not recluster faces, invent identities, infer relationships or force Other into a person. "
    "Prefer null cluster and identity for unknown. A retained uncertain candidate is only a proposal, "
    "not an effective identity. A supplied MAGI association is fallible."
)
IDENTITY_PROMPT = (
    "Stage 3B: resolve identity from Stage 3A hypotheses and supplied source evidence. speaker_id must "
    "be a supplied candidate or null; never invent a new ID. speaker_name is separate and nullable. "
    "Names/aliases require the supplied bank/profile or explicit text evidence, never model world knowledge. "
    "Name proposals do not update the bank. Unknown identity/name is an acceptable outcome."
)
ADDRESSEE_PROMPT = (
    "Stage 3C: resolve whom the utterance addresses, using speaker hypotheses, images, nearby turns and "
    "explicit forms of address. Use individual (one ID), group (two or more), self (speaker ID), "
    "unspecified (no IDs), or not_applicable (no IDs, e.g. narration). IDs must be supplied candidates. "
    "The next speaker is not automatically the addressee. Unknown returns unspecified and an empty list. "
    "Do not infer a relationship graph, gender or personality."
)


def validate_scenes(chapter, scenes: dict) -> None:
    if scenes.get("kind") != "scene_dialogue" or scenes.get("document_id") != chapter.document["document_id"]:
        raise ValueError("Expected scene_dialogue for this chapter")
    if scenes.get("source_fingerprint") != chapter.source_fingerprint:
        raise ValueError("Scene fingerprint differs from the normalized source")
    rows = scenes.get("utterances", [])
    if [r.get("id") for r in rows] != [r["id"] for r in chapter.rows]:
        raise ValueError("Scene artifact must preserve original utterance coverage/order")
    for original, row in zip(chapter.rows, rows):
        for key in ("text", "text_original", "source_text_ids", "source_boxes", "bbox", "panel_index"):
            if original.get(key) != row.get(key):
                raise ValueError(f"Scene artifact changed source field {key}")
        if not isinstance(row.get("scene_id"), str) or not row["scene_id"]:
            raise ValueError("Every utterance must have a scene ID")
    seen, previous = set(), None
    for row in rows:
        scene = row["scene_id"]
        if scene != previous and scene in seen:
            raise ValueError("Scene membership must be contiguous")
        seen.add(scene)
        previous = scene


def validate_reconstructed(chapter, scenes: dict, output: dict) -> None:
    if output.get("kind") != "structured_dialogue" or output.get("document_id") != chapter.document["document_id"]:
        raise ValueError("Invalid structured dialogue document")
    original = {r["id"]: r for r in scenes["utterances"]}
    rows = output["utterances"]
    if len(rows) != len(original) or len({r["id"] for r in rows}) != len(rows) or {r["id"] for r in rows} != set(original):
        raise ValueError("Reconstruction changed utterance coverage")
    for index, row in enumerate(rows):
        source = original[row["id"]]
        for key in ("text", "text_original", "source_text_ids", "source_boxes", "bbox", "panel_index", "page_id", "page", "scene_id", "original_order"):
            if row.get(key) != source.get(key):
                raise ValueError(f"{row['id']}: reconstruction changed {key}")
        # Each physical slot must retain its source page and scene.
        slot = scenes["utterances"][index]
        if (row.get("page_id"), row.get("page"), row["scene_id"]) != (slot.get("page_id"), slot.get("page"), slot["scene_id"]):
            raise ValueError("Ordering crossed a physical page or scene boundary")
        if row["corrected_order"] != index or row["reading_order"] != index:
            raise ValueError("Corrected reading order must be contiguous")
        if row.get("speaker_id") is not None and id_key(row["speaker_id"]) not in chapter.profiles:
            raise ValueError("Unknown speaker identity in reconstructed dialogue")
        if any(id_key(identity) not in chapter.profiles for identity in row.get("addressee_ids", [])):
            raise ValueError("Unknown addressee identity in reconstructed dialogue")
        if row.get("speaker_name") is not None and not isinstance(row["speaker_name"], str):
            raise ValueError("speaker_name must be a string or null")


def reconstruct_dialogue(chapter, scenes: dict, reasoner, *, correct_order: bool = True, step_callback=None) -> dict:
    validate_scenes(chapter, scenes)
    output = copy.deepcopy(scenes)
    output["kind"] = "structured_dialogue"
    rows = output["utterances"]
    runner = TaskRunner(reasoner, chapter, scenes.get("visual_observations", {}))
    history = []
    def snapshot(stage):
        if step_callback is not None:
            artifact = {**output, "utterances": rows, "stage3_history": history}
            step_callback(stage, copy.deepcopy(artifact))
    for row in rows:
        row["original_speaker_id"] = row.get("speaker_id")
        row["original_speaker_cluster_id"] = row.get("speaker_cluster_id")
        row["original_speaker_name"] = row.get("speaker_name")
        row["original_association"] = {k: row.get(k) for k in
                ("association_source", "character_detection_index", "tail_index", "speaker_status")}
        row["original_addressee_type"] = row.get("addressee_type")
        row["original_addressee_ids"] = copy.deepcopy(row.get("addressee_ids", []))
        row.pop("addressee_type", None)
        row.pop("addressee_ids", None)
        row["review_flags"] = []
        row["evidence_refs"] = []
        row["reasoning"] = {}
    def inference(task, prompt, indexes, check):
        targets = [{"id": rows[i]["id"]} for i in indexes]
        positions = {r["id"]: i for i, r in enumerate(rows)}
        return runner.execute(task, prompt, targets, rows, lambda t: [positions[t["id"]]], check)
    def identity_check(value, target, data):
        supplied = {id_key(p["id"]) for p in data["identity_candidates"]}
        if value["speaker_id"] is not None and id_key(value["speaker_id"]) not in supplied:
            raise ValueError("Identity is not a supplied candidate")
    def speaker_check(value, target, data):
        identity_check(value, target, data)
        supplied_clusters = {c["id"] for c in data.get("visual_cluster_candidates", [])}
        if value["speaker_cluster_id"] is not None and value["speaker_cluster_id"] not in supplied_clusters:
            raise ValueError("Speaker cluster was not supplied in this source window")
    def addressee_check(value, target, data):
        supplied = {id_key(p["id"]) for p in data["identity_candidates"]}
        ids, kind = value["addressee_ids"], value["addressee_type"]
        if any(id_key(identity) not in supplied for identity in ids):
            raise ValueError("Addressee was not supplied as a candidate")
        if value["status"] != "predicted":
            # Preserve uncertain raw proposals for review. Their public listener
            # fields are canonicalized to unspecified/[] by apply(), so an
            # incomplete tentative individual/self/group is not a prediction.
            return
        if ((kind == "individual" and len(ids) != 1) or (kind == "group" and len(ids) < 2) or
                (kind in ("unspecified", "not_applicable") and ids)):
            raise ValueError("Addressee cardinality contradicts its type")
        source = next(r for r in rows if r["id"] == target["id"])
        if kind == "self" and (len(ids) != 1 or (value["status"] == "predicted" and
                id_key(ids[0]) != id_key(source.get("speaker_id")))):
            raise ValueError("Self addressee must equal the current speaker")
    def apply(task, values, pass_name):
        for row in rows:
            if row["id"] not in values:
                continue
            value = values[row["id"]]
            row["reasoning"][task] = copy.deepcopy(value)
            row["evidence_refs"] = sorted(set(row["evidence_refs"]) | set(value["evidence_refs"]))
            history.append({"pass": pass_name, "task": task, **value})
            if task == "speaker":
                row["proposed_speaker_cluster_id"] = value["speaker_cluster_id"]
                row["proposed_speaker_id"] = value["speaker_id"]
                row["speaker_cluster_id"] = value["speaker_cluster_id"] if value["status"] == "predicted" else None
                row["speaker_id"] = value["speaker_id"] if value["status"] == "predicted" else None
                row["speaker_status"] = value["status"]
            elif task == "identity":
                row["proposed_speaker_id"] = value["speaker_id"]
                row["proposed_speaker_name"] = value["speaker_name"]
                row["speaker_id"] = value["speaker_id"] if value["status"] == "predicted" else None
                row["speaker_status"] = value["status"]
                row["speaker_name"] = value["speaker_name"] if value["name_status"] != "unknown" else None
                row["speaker_name_status"] = value["name_status"]
                profile = chapter.profiles.get(id_key(value["speaker_id"]), {})
                if value["speaker_name"] is not None:
                    if (value["status"] == "predicted" and value["name_status"] != "unknown" and
                            profile.get("name") == value["speaker_name"] and profile.get("source") == "provided_bank"):
                        row["speaker_name_status"] = "confirmed"
                    else:
                        row["speaker_name_status"] = "needs_review"
                        row["review_flags"].append("name_proposal_requires_review")
            else:
                row["proposed_addressee_type"], row["proposed_addressee_ids"] = value["addressee_type"], value["addressee_ids"]
                row["addressee_type"] = value["addressee_type"] if value["status"] == "predicted" else "unspecified"
                row["addressee_ids"] = value["addressee_ids"] if value["status"] == "predicted" else []
                row["addressee_status"] = value["status"]
    indexes = list(range(len(rows)))
    apply("speaker", inference("speaker", SPEAKER_PROMPT, indexes, speaker_check), "initial")
    snapshot("3A")
    apply("identity", inference("identity", IDENTITY_PROMPT, indexes, identity_check), "initial")
    snapshot("3B")
    apply("addressee", inference("addressee", ADDRESSEE_PROMPT, indexes, addressee_check), "initial")
    snapshot("3C")
    changed = []
    if correct_order:
        rows, changed, audit = reorder_dialogue(rows, runner)
        output["utterances"], output["ordering_audit"] = rows, audit
    else:
        output["ordering_audit"] = []
        for index, row in enumerate(rows):
            row["corrected_order"] = row["reading_order"] = index
            row["order_status"] = "not_run"
    snapshot("3D")
    if changed:
        affected = sorted({j for i, row in enumerate(rows) if row["id"] in changed
                           for j in range(max(0, i - reasoner.config.overlap), min(len(rows), i + reasoner.config.overlap + 1))})
        before = {rows[i]["id"]: (rows[i].get("speaker_cluster_id"), rows[i].get("speaker_id")) for i in affected}
        apply("speaker", inference("speaker", SPEAKER_PROMPT, affected, speaker_check), "after_ordering")
        renamed = [i for i in affected if before[rows[i]["id"]] !=
                   (rows[i].get("speaker_cluster_id"), rows[i].get("speaker_id"))]
        if renamed:
            apply("identity", inference("identity", IDENTITY_PROMPT, renamed, identity_check), "after_ordering")
        apply("addressee", inference("addressee", ADDRESSEE_PROMPT, affected, addressee_check), "after_ordering")
        snapshot("3_local_rerun")
    # Record unresolved chapter-wide conflicts; do not force a global identity or edit scenes.
    cluster_ids = {}
    for row in rows:
        if row.get("speaker_cluster_id") and row.get("speaker_id") is not None:
            cluster_ids.setdefault(row["speaker_cluster_id"], set()).add(id_key(row["speaker_id"]))
    for row in rows:
        if len(cluster_ids.get(row.get("speaker_cluster_id"), set())) > 1:
            row["review_flags"].append("conflicting_identity_for_visual_cluster")
            row["speaker_status"] = "needs_review"
        if row.get("speaker_status") != "predicted" or row.get("speaker_id") is None:
            row["review_flags"].append("speaker_unresolved")
        if row.get("addressee_status") != "predicted":
            row["review_flags"].append("addressee_unresolved")
        if row.get("scene_status") == "needs_review":
            row["review_flags"].append("scene_boundary_unresolved")
        if row.get("order_status") in ("unknown", "needs_review"):
            row["review_flags"].append("order_unresolved")
        row["review_flags"] = sorted(set(row["review_flags"]))
    output["identity_proposals"] = [{"utterance_id": r["id"], "speaker_id": r.get("proposed_speaker_id"),
                                    "speaker_cluster_id": r.get("proposed_speaker_cluster_id"),
                                    "speaker_name": r.get("proposed_speaker_name"),
                                    "evidence_refs": r["reasoning"]["identity"]["evidence_refs"]}
                                   for r in rows if r.get("speaker_name_status") == "needs_review"]
    output["stage3_history"] = history
    output["local_rerun_count"] = 1 if changed else 0
    output["reasoning_warnings"] += runner.warnings
    validate_reconstructed(chapter, scenes, output)
    return output
