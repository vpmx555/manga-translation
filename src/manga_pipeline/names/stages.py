"""Essential dialogue analysis, name scanning and selective panel linking."""
import base64
import io
import re

from ..bank.names import NameBank, known_names
from ..extraction.document import panel_for_box
from ..progress import NullProgress
from ..policies import is_essential_policy
from ..storage.io import atomic_json, fingerprint, read_json

MENTION_TYPES = {"direct_address", "self_introduction", "third_person",
                 "narration_introduction", "narration_reference", "unknown"}
CONTENT_TYPES = {"dialogue", "narration", "thought", "unknown"}


def object_schema(fields):
    return {"type": "object", "properties": fields, "required": list(fields), "additionalProperties": False}


CLASSIFY_SCHEMA = object_schema({
    "content_type": {"type": "string", "enum": sorted(CONTENT_TYPES)},
    "mentions": {"type": "array", "items": object_schema({
        "candidate_id": {"type": "string"},
        "mention_type": {"type": "string", "enum": sorted(MENTION_TYPES)}})},
})
LINK_SCHEMA = object_schema({"is_narration": {"type": "boolean"}, "links": {
    "type": "array", "items": object_schema({
        "candidate_id": {"type": "string"}, "detection_index": {"type": ["integer", "null"]},
        "status": {"type": "string", "enum": ["linked", "unknown"]},
        "reason": {"type": "string"}})}})

CLASSIFY_PROMPT = (
    "Classify only the target utterance using its source-order context (up to five turns). "
    "content_type: dialogue, narration, thought, unknown. Coherence or one apparent voice does NOT prove narration. "
    "For EACH supplied candidate name choose direct_address, self_introduction, third_person, "
    "narration_introduction, narration_reference, or unknown. Names of people merely mentioned are not addressees. "
    "If the candidate is not actually a person name, use unknown. MAGI speaker IDs are fallible source hints. "
    "Do not infer speaker/addressee IDs, scenes, ordering or relationships. Return all supplied candidate IDs exactly once. "
    "Do not explain; return the exact JSON schema."
)
LINK_PROMPT = (
    "Verify whether the target text is narration and identifies a visible person in the supplied panel. "
    "Select the MAGI detection index for each supplied name, only from eligible_detections. "
    "Only detections with stable character IDs are eligible; pending detections cannot receive names. "
    "A MAGI speaker association on narration may be mistaken: prioritize checking that detection, do not blindly trust it. "
    "A named off-panel person or ambiguous correspondence returns unknown/null. Never invent an ID or merge characters. "
    "If not narration, every link must be unknown. Return every candidate ID once. Reasons must be short."
)


def snapshot_bank(store, label):
    path = store.directory / "snapshots" / (label + "_bank.json")
    if not path.exists():
        atomic_json(path, NameBank(store.manifest["bank_path"]).read())
    return read_json(path)


def scan(store, document, detector, *, progress=None):
    progress = progress if progress is not None else NullProgress()
    bank = snapshot_bank(store, "scan")
    known = known_names(bank)
    output, failures = [], []
    progress.reset(total=len(document["utterances"]), unit="utterance")
    for row in document["utterances"]:
        config = store.manifest["config"]
        request = {"row": row, "known": known, "model": config.get("ner", config["spacy_model"])}
        cached = store.target("scan", row["id"], request)
        if cached is not None:
            output.extend(cached)
            progress.advance(reused=True)
            continue
        try:
            spans = detector.detect(row["text"])
            for name in known:
                for match in re.finditer(r"(?<!\w)" + re.escape(name) + r"(?!\w)", row["text"], re.I):
                    spans.append({"name": match.group(), "start": match.start(), "end": match.end(), "source": "bank"})
            # Exact bank hits win over NER subspans of the same already-known name.
            bank_spans = [x for x in spans if x["source"] == "bank"]
            spans = [x for x in spans if x["source"] == "bank" or not any(
                b["start"] <= x["start"] and b["end"] >= x["end"] for b in bank_spans)]
            unique = {}
            for span in spans:
                if not 0 <= span["start"] < span["end"] <= len(row["text"]):
                    raise ValueError("NER returned invalid source offsets")
                name = row["text"][span["start"]:span["end"]].strip()
                key = name.casefold()
                identity = known.get(key, [])
                if len(identity) > 1:
                    known_id = None
                    state = "bank_conflict"
                else:
                    known_id = identity[0] if identity else None
                    state = "known" if identity else "new"
                candidate = {**span, "name": name, "key": key, "utterance_id": row["id"],
                             "known_id": known_id, "state": state,
                             "id": fingerprint([row["id"], span["start"], span["end"], key])[:20]}
                unique[(span["start"], span["end"], key)] = candidate
            result = sorted(unique.values(), key=lambda x: (x["start"], x["end"]))
            store.commit_target("scan", row["id"], request, result=result)
            output.extend(result)
            progress.advance()
        except Exception as exc:
            store.commit_target("scan", row["id"], request, error=str(exc))
            failures.append({"utterance_id": row["id"], "error": str(exc)})
            progress.advance(failed=True)
    return {"candidates": output, "failures": failures}


def classify(store, document, scanned, provider, *, progress=None):
    if is_essential_policy(document.get("analysis_policy")):
        from .dialogue import classify_dialogue
        return classify_dialogue(store, document, scanned, provider, progress=progress)
    progress = progress if progress is not None else NullProgress()
    output, failures = [], []
    rows = document["utterances"]
    by_row = {}
    for candidate in scanned["candidates"]:
        by_row.setdefault(candidate["utterance_id"], []).append(candidate)
    progress.reset(total=sum(bool(by_row.get(row["id"])) for row in rows), unit="utterance")
    live_names = NameBank(store.manifest["bank_path"]).known()
    for index, row in enumerate(rows):
        candidates = by_row.get(row["id"], [])
        if not candidates:
            continue
        # This immutable request commits all mentions in one utterance, avoiding
        # repeated calls when the same turn contains several new names.
        request = {"target": row, "candidates": candidates,
                   "context": [{k: r.get(k) for k in ("id", "text", "speaker_id", "page", "content_type")}
                               for r in rows[max(0, index - 2):index + 3]]}
        cached = store.target("classify", row["id"], request)
        if cached is not None:
            output.append(cached)
            progress.advance(reused=True)
            continue
        try:
            unknown, skipped = [], []
            for c in candidates:
                mapped = live_names.get(c["key"], [])
                if c["state"] != "new" or mapped:
                    skipped.append({**c, "mention_type": "unknown", "skipped": True,
                                    "known_id": mapped[0] if len(mapped) == 1 else c["known_id"]})
                else:
                    unknown.append(c)
            result = {"utterance_id": row["id"], "content_type": row["content_type"], "mentions": skipped,
                      "classification_source": "normalization"}
            if unknown:
                ids = {c["id"] for c in unknown}
                def check(value):
                    if value.get("content_type") not in CONTENT_TYPES or not isinstance(value.get("mentions"), list):
                        raise ValueError("Invalid mention classification")
                    values = value["mentions"]
                    if len(values) != len(ids) or {x.get("candidate_id") for x in values} != ids:
                        raise ValueError("Classification changed candidate coverage")
                    if any(x.get("mention_type") not in MENTION_TYPES for x in values):
                        raise ValueError("Unknown mention type")
                # Keep geometry, tail evidence and image references out of text inference.
                text_fields = ("id", "text", "speaker_id")
                payload = {"target": {k: row.get(k) for k in text_fields},
                           "context": [{k: turn.get(k) for k in text_fields} for turn in request["context"]],
                           "candidates": [{k: candidate[k] for k in ("id", "name", "start", "end")}
                                          for candidate in unknown]}
                value = provider.infer("classify_mentions", CLASSIFY_PROMPT,
                                       payload, CLASSIFY_SCHEMA, check)
                types = {x["candidate_id"]: x["mention_type"] for x in value["mentions"]}
                result.update(content_type=value["content_type"], classification_source="model")
                result["mentions"].extend({**c, "mention_type": types[c["id"]], "skipped": False} for c in unknown)
            store.commit_target("classify", row["id"], request, result=result)
            output.append(result)
            progress.advance()
        except Exception as exc:
            store.commit_target("classify", row["id"], request, error=str(exc))
            failures.append({"utterance_id": row["id"], "error": str(exc)})
            progress.advance(failed=True)
    return {"utterances": output, "failures": failures}


def panel_candidates(raw, row, bank):
    page = next((p for p in raw["pages"] if p["id"] == row.get("page_id")), None)
    panel_index = row.get("panel_index")
    if page is None or not isinstance(panel_index, int) or not 0 <= panel_index < len(page["panels"]):
        return None, [], []
    stable, pending = [], []
    for index, (box, character_id) in enumerate(zip(page["characters"], page["character_ids"])):
        if panel_for_box(box, page["panels"]) != panel_index:
            continue
        character = bank.get("characters", {}).get(str(character_id))
        detection = {"detection_index": index, "character_id": character_id, "bbox": box}
        if character_id is not None and character and not character.get("disabled"):
            stable.append(detection)
        else:
            pending.append({**detection, "character_id": None})
    return page, stable, pending


def panel_image(page, panel_index, stable, max_side):
    from PIL import Image, ImageDraw
    box = page["panels"][panel_index]
    with Image.open(page["image_path"]) as source:
        image = source.convert("RGB").crop(tuple(box))
    draw = ImageDraw.Draw(image)
    for detection in stable:
        x1, y1, x2, y2 = detection["bbox"]
        local = (x1 - box[0], y1 - box[1], x2 - box[0], y2 - box[1])
        draw.rectangle(local, outline="red", width=2)
        text = f"det {detection['detection_index']} / ID {detection['character_id']}"
        x, y = max(0, int(local[0])), max(0, int(local[1]))
        draw.rectangle((x, y, min(image.width, x + len(text) * 7), y + 14), fill="white")
        draw.text((x, y), text, fill="red")
    image.thumbnail((max_side, max_side))
    output = io.BytesIO()
    image.save(output, format="PNG")
    return base64.b64encode(output.getvalue()).decode()


def link(store, raw, document, classified, provider, *, progress=None):
    progress = progress if progress is not None else NullProgress()
    bank = snapshot_bank(store, "link")
    name_bank = NameBank(store.manifest["bank_path"])
    rows = {r["id"]: r for r in document["utterances"]}
    groups, ignored = {}, []
    dialogue_links, dialogue_failures = [], []
    for item in classified["utterances"]:
        row = rows[item["utterance_id"]]
        for mention in item["mentions"]:
            if mention.get("name_target_id") is not None:
                continue
            if mention["skipped"] or item["content_type"] != "narration" or mention["mention_type"] not in {
                    "narration_introduction", "narration_reference"}:
                ignored.append({"candidate_id": mention["id"], "status": "skipped", "reason": "known_or_not_narration"})
                continue
            key = f"{row.get('page_id')}:{row.get('panel_index')}"
            groups.setdefault(key, []).append({"mention": mention, "row": row})
    direct_count = sum(m.get("name_target_id") is not None for item in classified["utterances"] for m in item["mentions"])
    progress.reset(total=len(groups) + direct_count, unit="target")
    if is_essential_policy(document.get("analysis_policy")):
        from .dialogue import link_direct_names
        dialogue_links, dialogue_failures = link_direct_names(store, classified, progress=progress)
    output, failures = list(ignored) + dialogue_links, dialogue_failures
    for key, targets in groups.items():
        row = targets[0]["row"]
        page, stable, pending = panel_candidates(raw, row, bank)
        request = {"targets": targets, "eligible_detections": stable, "pending_detections": pending,
                   "panel": page["panels"][row["panel_index"]] if page else None,
                   "source_hash": next((x["sha256"] for x in store.manifest["source"]["images"]
                                        if page and x["path"] == page["image_path"]), None)}
        cached = store.target("link", key, request)
        if cached is not None:
            output.extend(cached)
            progress.advance(reused=True)
            continue
        try:
            plan_path = store.directory / "05_link" / "plans" / (fingerprint(key) + ".json")
            if plan_path.exists():
                plan = read_json(plan_path)
                if plan["request_hash"] != fingerprint(request) or plan["plan_hash"] != fingerprint(plan["value"]):
                    raise ValueError("Link plan changed/corrupt")
                value = plan["value"]
                results, assignments, reason = value["results"], value["assignments"], value["reason"]
                unknown = {}
                for target in targets:
                    name = target["mention"]["key"]
                    if name in value["unknown_keys"]:
                        unknown.setdefault(name, []).append(target)
            else:
                live_known = name_bank.known()
                results, unknown = [], {}
                for target in targets:
                    m = target["mention"]
                    if m["key"] in live_known:
                        results.append({"candidate_id": m["id"], "status": "skipped", "reason": "name_already_mapped"})
                    else:
                        unknown.setdefault(m["key"], []).append(target)
                ids = {x["character_id"] for x in stable}
                assignments = {}
                if not ids:
                    reason = "no_stable_id_in_panel"
                elif len(ids) == 1 and len(unknown) > 1:
                    reason = "multiple_new_names_one_id"
                elif len(ids) == 1 and len(unknown) == 1:
                    assignments[next(iter(unknown))] = next(iter(ids))
                    reason = "one_stable_id_one_new_name"
                elif len(ids) >= 2 and unknown:
                    names = [{"candidate_id": ts[0]["mention"]["id"], "name": ts[0]["mention"]["name"]}
                             for ts in unknown.values()]
                    target_ids = {x["candidate_id"] for x in names}
                    eligible = {x["detection_index"]: x["character_id"] for x in stable}
                    def check(value):
                        if type(value.get("is_narration")) is not bool or not isinstance(value.get("links"), list):
                            raise ValueError("Invalid narration linking response")
                        values = value["links"]
                        if len(values) != len(target_ids) or {x.get("candidate_id") for x in values} != target_ids:
                            raise ValueError("Link response changed candidate coverage")
                        for item in values:
                            index = item.get("detection_index")
                            if item.get("status") not in {"linked", "unknown"}:
                                raise ValueError("Invalid link status")
                            if item["status"] == "linked" and (not value["is_narration"] or type(index) is not int or index not in eligible):
                                raise ValueError("Link is not a supplied stable detection on narration")
                            if item["status"] == "unknown" and index is not None:
                                raise ValueError("Unknown must have null detection")
                    payload = {**request, "names": names,
                               "priority_character_ids": sorted({t["row"].get("speaker_id") for t in targets
                                                                 if t["row"].get("speaker_id") in ids})}
                    image = panel_image(page, row["panel_index"], stable, store.manifest["config"]["ollama"]["image_max_side"])
                    value = provider.infer("link_narration_names", LINK_PROMPT, payload, LINK_SCHEMA, check, [image])
                    key_by_id = {ts[0]["mention"]["id"]: name for name, ts in unknown.items()}
                    if value["is_narration"]:
                        for item in value["links"]:
                            if item["status"] == "linked":
                                assignments[key_by_id[item["candidate_id"]]] = eligible[item["detection_index"]]
                    reason = "vlm_narration_check"
                else:
                    reason = "no_new_name"
                value = {"results": results, "assignments": assignments, "reason": reason, "unknown_keys": list(unknown)}
                atomic_json(plan_path, {"request_hash": fingerprint(request), "plan_hash": fingerprint(value), "value": value})
            # Different new names selecting the same ID are not implicitly aliases.
            collisions = {identity for identity in assignments.values()
                          if list(assignments.values()).count(identity) > 1}
            for name, ts in unknown.items():
                identity = assignments.get(name)
                if identity is None or identity in collisions:
                    for t in ts:
                        results.append({"candidate_id": t["mention"]["id"], "status": "unknown",
                                        "reason": "conflicting_names" if identity in collisions else reason})
                    continue
                operation = fingerprint({"run": str(store.directory), "panel": key, "name": name})
                outcome = name_bank.apply(operation, identity, ts[0]["mention"]["name"],
                                          {"panel": key, "reason": reason,
                                           "utterance_ids": [t["row"]["id"] for t in ts]},
                                          preserve_name=is_essential_policy(document.get("analysis_policy")))
                for t in ts:
                    results.append({**outcome, "candidate_id": t["mention"]["id"], "reason": reason})
            store.commit_target("link", key, request, result=results)
            output.extend(results)
            progress.advance()
        except Exception as exc:
            store.commit_target("link", key, request, error=str(exc))
            failures.append({"panel": key, "error": str(exc)})
            progress.advance(failed=True)
    return {"links": output, "failures": failures}
