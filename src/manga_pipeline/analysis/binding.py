"""Only scanned introductions open panels; identity decisions precede bank writes."""
import copy
from ..bank.names import NameBank, known_names
from ..names.dialogue import stable_id
from ..names.stages import object_schema, panel_candidates, panel_image
from ..storage.io import fingerprint
from .text import infer_once

PANEL_PROMPT = (
    "Match the supplied introduced person names to visible people in this panel. "
    "Their introduction role has already been established from text; do not reclassify narration/dialogue. "
    "Choose character_id only from eligible_detections with stable IDs. Pending people cannot receive names. "
    "Source speaker associations may be mistaken: check priority_character_ids as possible introduced subjects, "
    "not as automatically correct speakers or identities. The teller and introduced person can differ. "
    "For an off-panel person or ambiguous match choose null. Never invent an ID, merge characters or infer "
    "aliases from similar names. Return each candidate_id exactly once with a short reason. JSON only."
)


def unresolved(mention, reason):
    return {"candidate_id": mention["id"], "name": mention["name"], "status": "unresolved", "reason": reason}


def panel_decisions(store, raw, targets, bank, provider, start, progress):
    row = targets[0]["row"]
    page, stable, _ = panel_candidates(raw, row, bank)
    by_name = {}
    for target in targets:
        by_name.setdefault(target["mention"]["key"], []).append(target)
    ids = sorted({d["character_id"] for d in stable})
    names = [{"candidate_id": ts[0]["mention"]["id"], "name": ts[0]["mention"]["name"]}
             for ts in by_name.values()]
    request = {"names": names, "eligible_detections": stable,
        "texts": [{"text": t["row"]["text"], "source_speaker_id": t["row"].get("speaker_id")}
                  for t in targets],
        "priority_character_ids": sorted({t["row"].get("speaker_id") for t in targets
                                           if t["row"].get("speaker_id") in ids}),
        "page_id": row.get("page_id"), "panel_index": row.get("panel_index"),
        "source_hash": next((s["sha256"] for s in store.manifest["source"]["images"]
                             if page and s["path"] == page["image_path"]), None)}
    key = f"panel-decision:{start}:{row.get('page_id')}:{row.get('panel_index')}"
    cached = store.target("analyze", key, request)
    if cached is not None:
        return cached
    assignments, reason = {}, "no_stable_id_in_panel"
    if len(ids) == 1 and len(names) == 1:
        assignments[names[0]["candidate_id"]] = ids[0]
        reason = "one_stable_id_one_new_name"
    elif len(ids) == 1 and len(names) > 1:
        reason = "multiple_new_names_one_id"
    elif len(ids) >= 2:
        progress.set_phase(f"batch {start // 3 + 1}: panel VLM")
        schema = object_schema({"links": {"type": "array", "minItems": len(names), "maxItems": len(names),
            "items": object_schema({"candidate_id": {"enum": [n["candidate_id"] for n in names]},
                "character_id": {"enum": [*ids, None]}, "reason": {"type": "string", "maxLength": 120}})}})

        def validate(value):
            links = value.get("links")
            expected = {n["candidate_id"] for n in names}
            if (not isinstance(links, list) or len(links) != len(expected)
                    or {x.get("candidate_id") for x in links} != expected):
                raise ValueError("Panel name coverage changed")
            for result in links:
                identity = result.get("character_id")
                if identity is not None and (type(identity) is not int or identity not in ids):
                    raise ValueError("Panel selected a non-stable/out-of-panel ID")
                if not isinstance(result.get("reason"), str) or len(result["reason"]) > 120:
                    raise ValueError("Invalid panel reason")
        try:
            image = panel_image(page, row["panel_index"], stable, store.manifest["config"]["ollama"]["image_max_side"])
            value = infer_once(provider, "bind_introduction_names_v3", PANEL_PROMPT, request, schema, validate, [image])
            validate(value)
            assignments = {x["candidate_id"]: x["character_id"] for x in value["links"]
                           if x["character_id"] is not None}
            reason = "vlm_introduction_panel"
        except Exception as exc:
            reason = "panel_unresolved: " + str(exc)[:120]
    collisions = {identity for identity in assignments.values() if list(assignments.values()).count(identity) > 1}
    decisions = []
    for ts in by_name.values():
        identity = assignments.get(ts[0]["mention"]["id"])
        for target in ts:
            mention = target["mention"]
            if identity is None or identity in collisions:
                decisions.append(unresolved(mention, "conflicting_names" if identity in collisions else reason))
            else:
                decisions.append({"candidate_id": mention["id"], "name": mention["name"],
                    "status": "resolved", "character_id": identity, "source": "introduction", "reason": reason})
    # This receipt is durable before any bank write; VLM is not repeated on resume.
    store.commit_target("analyze", key, request, result=decisions)
    return decisions


def bind_batch(store, raw, rows, text, bank, provider, start, progress):
    progress.set_phase(f"batch {start // 3 + 1}: bind names")
    known = known_names(bank)
    row_by_id = {row["id"]: row for row in rows}
    decisions, panels = [], {}
    for item in text:
        row = row_by_id[item["utterance_id"]]
        for mention in item["mentions"]:
            role, mapped = mention["mention_type"], known.get(mention["key"], [])
            if mention.get("unresolved"):
                decisions.append(unresolved(mention, "unresolved_text_pair"))
            elif role == "unknown":
                decisions.append(unresolved(mention, "not_a_confirmed_person_mention"))
            elif len(mapped) > 1:
                decisions.append(unresolved(mention, "name_bank_conflict"))
            elif len(mapped) == 1:
                decisions.append({"candidate_id": mention["id"], "name": mention["name"],
                    "status": "resolved" if role == "introduction" else "known", "character_id": mapped[0],
                    "source": role, "reason": "name_already_mapped"})
            elif role == "direct_address":
                identity = stable_id(mention.get("name_target_id"), bank)
                if identity is not None and identity in item["addressee_ids"]:
                    decisions.append({"candidate_id": mention["id"], "name": mention["name"],
                        "status": "resolved", "character_id": identity, "source": role, "reason": "llm_direct_address"})
                else:
                    decisions.append(unresolved(mention, "direct_listener_unresolved"))
            elif role == "introduction":
                key = (row.get("page_id"), row.get("panel_index"))
                panels.setdefault(key, []).append({"row": row, "mention": mention})
            else:
                decisions.append(unresolved(mention, "reference_without_known_id"))
    for targets in panels.values():
        decisions.extend(panel_decisions(store, raw, targets, bank, provider, start, progress))
    name_bank, outcomes = NameBank(store.manifest["bank_path"]), []
    for decision in decisions:
        key = "name:" + decision["candidate_id"]
        cached = store.target("analyze", key, decision)
        if cached is not None:
            outcomes.append(cached)
            continue
        result = decision
        if decision["status"] == "resolved":
            progress.set_phase(f"batch {start // 3 + 1}: save bank")
            try:
                result = {**decision, **name_bank.bind(fingerprint([str(store.directory), key]),
                    decision["character_id"], decision["name"], decision, source=decision["source"])}
            except ValueError as exc:
                result = {"candidate_id": decision["candidate_id"], "name": decision["name"],
                          "status": "unresolved", "reason": "bank_conflict: " + str(exc)[:120]}
        store.commit_target("analyze", key, decision, result=result)
        outcomes.append(result)
    by_id = {result["candidate_id"]: result for result in outcomes}
    enriched = copy.deepcopy(text)
    for row in enriched:
        for mention in row["mentions"]:
            outcome = by_id[mention["id"]]
            mention["model_name_target_id"] = mention.get("name_target_id")
            mention["binding_status"] = outcome["status"]
            mention["binding_source"] = outcome.get("source")
            mention["name_target_id"] = outcome.get("character_id") if outcome["status"] in {"known", "linked", "protected"} else None
            if outcome.get("name_conflict"):
                row["warnings"].append({"code": "equal_priority_introduction_names", "candidate_id": mention["id"]})
    return enriched, outcomes
