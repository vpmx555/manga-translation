"""Per-target name ownership on the wire and independent semantic validation."""
import copy

from . import persons

ROLES = {"dialogue", "narration", "sfx", "action_state", "ui", "watermark", "unknown"}
STYLES = {"normal", "whisper", "shout", "thought", "caption", "unknown"}
COMPONENTS = {"given_name", "middle_name", "surname", "nickname", "full_name"}


def object_schema(properties):
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


def contract(target_ids, person_ids, cluster_refs, mentions_by_target):
    person = {"type": ["string", "null"], "enum": [*person_ids, None]}
    cluster = {"type": ["string", "null"], "enum": [*cluster_refs, None]}
    component = object_schema({"kind": {"type": "string", "enum": sorted(COMPONENTS)},
                               "value": {"type": "string"}})
    mention = object_schema({"confirmed_person_name": {"type": "boolean"},
        "mention_type": {"type": "string", "enum": ["direct_address", "introduction", "reference", "unknown"]},
        "target_person_id": copy.deepcopy(person), "recipient_cluster_ref": copy.deepcopy(cluster),
        "visual_support": {"type": "string", "enum": ["compatible", "different_appearance", "unknown"]},
        "components": {"type": "array", "items": component, "maxItems": 4},
        "evidence": {"type": "string", "maxLength": 160}})
    rows = {}
    for identity in target_ids:
        ids = mentions_by_target[identity]
        # No candidate_id is generated. Its immutable key belongs to this target.
        rows[identity] = object_schema({
            "content_type": {"type": "string", "enum": sorted(ROLES)},
            "speech_style": {"type": "string", "enum": sorted(STYLES)},
            "speaker_person_id": copy.deepcopy(person), "speaker_cluster_ref": copy.deepcopy(cluster),
            "addressee_person_ids": {"type": "array", "items": {"type": "string", **({"enum": person_ids} if person_ids else {})},
                                     "maxItems": len(person_ids)},
            "mentions": object_schema({mid: copy.deepcopy(mention) for mid in ids}),
            "evidence": {"type": "string", "maxLength": 160}})
    distinct = object_schema({"person_ids": {"type": "array", "items": {"type": "string", **({"enum": person_ids} if person_ids else {})},
                                             "minItems": 2, "maxItems": 2},
                              "evidence": {"type": "string", "maxLength": 160}})
    return object_schema({"utterances": object_schema(rows),
                          "distinct_person_pairs": {"type": "array", "items": distinct,
                                                    "maxItems": 4 if len(person_ids) >= 2 else 0}})


def target_records(value, target_ids):
    if not isinstance(value, dict) or not isinstance(value.get("utterances"), (dict, list)):
        raise ValueError("Analyze response has no target collection")
    payload = value["utterances"]
    if isinstance(payload, dict):
        return {identity: copy.deepcopy(payload[identity]) for identity in target_ids if identity in payload}
    indexed, duplicate = {}, set()
    for row in payload:
        if not isinstance(row, dict):
            continue
        identity = row.get("utterance_id")
        if identity in indexed:
            duplicate.add(identity)
        indexed[identity] = copy.deepcopy(row)
    return {identity: row for identity, row in indexed.items() if identity in target_ids and identity not in duplicate}


def validate_target(row, identity, mentions, person_ids, visible, state):
    if not isinstance(row, dict):
        raise ValueError("Missing target " + identity)
    required = {"content_type", "speech_style", "speaker_person_id", "speaker_cluster_ref",
                "addressee_person_ids", "mentions", "evidence"}
    if not required.issubset(row):
        raise ValueError("Missing semantic fields: " + str(sorted(required - set(row))))
    if row.get("utterance_id", identity) != identity:
        raise ValueError("Target identity changed: " + identity)
    item = copy.deepcopy(row)
    item["utterance_id"] = identity
    if item["content_type"] not in ROLES or item["speech_style"] not in STYLES:
        raise ValueError("Invalid content role/speech style")
    if not isinstance(item["evidence"], str) or not item["evidence"].strip():
        raise ValueError("Semantic evidence is empty")
    for field, known in (("speaker_person_id", person_ids), ("speaker_cluster_ref", visible)):
        if item[field] is not None and item[field] not in known:
            raise ValueError("Unknown semantic reference: " + field)
    addresses = item["addressee_person_ids"]
    if not isinstance(addresses, list) or any(p not in person_ids for p in addresses) or len(addresses) != len(set(addresses)):
        raise ValueError("Unknown/duplicate addressee")
    expected = {mid for mid, candidate in mentions.items() if candidate["utterance_id"] == identity}
    payload = item["mentions"]
    if isinstance(payload, dict):
        actual = list(payload)
        normalized = [{**value, "candidate_id": mid} if isinstance(value, dict) else value
                      for mid, value in payload.items()]
        if any(isinstance(value, dict) and value.get("candidate_id", mid) != mid for mid, value in payload.items()):
            raise ValueError("Mention key/ID disagree")
    elif isinstance(payload, list):
        actual = [m.get("candidate_id") if isinstance(m, dict) else None for m in payload]
        normalized = payload
    else:
        raise ValueError("Invalid mention collection")
    if len(actual) != len(set(actual)) or set(actual) != expected:
        raise ValueError("Name candidate coverage changed for " + identity + ": missing=" +
                         str(sorted(expected - set(actual))) + "; extra=" + str(sorted(set(actual) - expected, key=str)) +
                         "; duplicate=" + str(len(actual) != len(set(actual))))
    item["mentions"] = normalized
    for mention in normalized:
        if not isinstance(mention, dict) or type(mention.get("confirmed_person_name")) is not bool:
            raise ValueError("Invalid name decision")
        if mention.get("mention_type") not in {"direct_address", "introduction", "reference", "unknown"}:
            raise ValueError("Invalid mention type")
        if mention.get("visual_support") not in {"compatible", "different_appearance", "unknown"}:
            raise ValueError("Invalid visual support")
        if not isinstance(mention.get("evidence"), str) or not isinstance(mention.get("components"), list):
            raise ValueError("Invalid mention evidence/components")
        pid = mention.get("target_person_id")
        recipient = mention.get("recipient_cluster_ref")
        if pid is not None and pid not in person_ids:
            raise ValueError("Unknown name target")
        if recipient is not None and recipient not in visible:
            raise ValueError("Unknown calling recipient")
        if item["content_type"] in {"sfx", "action_state", "ui", "watermark"} and mention["confirmed_person_name"]:
            raise ValueError("An effect/action-state cannot supply a confirmed person name")
        if not mention["confirmed_person_name"]:
            if mention["components"] or pid is not None or recipient is not None:
                raise ValueError("Unconfirmed candidate must have empty components and null references")
            continue
        candidate = mentions[mention["candidate_id"]]
        allowed = persons.name_form(candidate["name"])["base_name"]
        if pid is not None:
            allowed += " " + " ".join(f["base_name"] for f in state["persons"][pid]["name_forms"])
        import re
        for component in mention["components"]:
            if not isinstance(component, dict) or component.get("kind") not in COMPONENTS or not isinstance(component.get("value"), str):
                raise ValueError("Invalid name component")
            normalized_name = persons.name_form(component["value"])["base_name"]
            if not re.search(r"(?<!\w)" + re.escape(normalized_name) + r"(?!\w)", allowed):
                raise ValueError("Unsupported name component")
    return item


def systemic_failure(exc):
    from ..runtime.resources import BudgetExceeded, ResourceExhausted
    from ..runtime.inference_state import InferenceUncertain
    current = exc
    while current is not None:
        if isinstance(current, (OSError, BudgetExceeded, ResourceExhausted, InferenceUncertain)):
            return True
        text = str(current).lower()
        if any(term in text for term in ("context budget", "context reserve", "model is not installed",
                                        "timed out", "connection refused", "http error", "ollama http", "ollama:",
                                        "gpu coordinator", "vllm:", "cuda", "out of memory")):
            return True
        current = current.__cause__
    return False


def unresolved(identity, candidates, errors):
    return {"utterance_id": identity, "content_type": "unknown", "speech_style": "unknown",
            "speaker_person_id": None, "speaker_cluster_ref": None, "addressee_person_ids": [],
            "mentions": [], "evidence": "Semantic response rejected; source-only unresolved fallback",
            "semantic_resolution": "unresolved", "semantic_needs_review": True,
            "semantic_errors": errors, "source_name_candidates": copy.deepcopy(candidates),
            "repair_count": 1, "semantic_response_used": False}
