"""Canonical sentinel pairs and minimal consistency checks; no text heuristics."""
import copy
from .contract import ADDRESS, CONTENT, LISTENERS, ROLES, speakers


def allowed_ids(target):
    return {c["id"] for c in target["identity_candidates"]}


def stable(value, allowed):
    return type(value) is int and value in allowed


def normalize(item, target):
    item = copy.deepcopy(item) if isinstance(item, dict) else {}
    warnings = []
    scope = item.get("addressee_type")
    if not isinstance(scope, str):
        return item, warnings
    canonical = {"audience": ["public_audience"], "unknown": ["unknown"]}
    if scope == "self":
        speaker = item.get("speaker_id")
        canonical["self"] = [speaker] if stable(speaker, allowed_ids(target)) else ["self"]
    if scope in canonical and item.get("addressee_ids") != canonical[scope]:
        warnings.append({"code": "normalized_addressee_ids", "before": item.get("addressee_ids"),
                         "after": canonical[scope]})
        item["addressee_ids"] = canonical[scope]
    return item, warnings


def check(item, target, speaker_policy=None):
    issues, allowed = [], allowed_ids(target)
    speaker_labels = speakers(speaker_policy)

    def issue(code, *fields, candidate_id=None):
        issues.append({"code": code, "fields": list(fields),
                       **({"candidate_id": candidate_id} if candidate_id else {})})

    if not isinstance(item.get("content_type"), str) or item["content_type"] not in CONTENT:
        issue("invalid_content_type", "content_type")
    speaker, scope = item.get("speaker_id"), item.get("addressee_type")
    if not stable(speaker, allowed) and speaker not in speaker_labels:
        issue("invalid_speaker_id", "speaker_id")
    if speaker_policy is not None and speaker == "narrator" and item.get("content_type") != "narration":
        issue("narrator_requires_narration", "speaker_id")
    if not isinstance(scope, str) or scope not in ADDRESS:
        issue("invalid_addressee_type", "addressee_type", "addressee_ids")
    listeners = item.get("addressee_ids")
    if not isinstance(listeners, list) or not listeners:
        issue("empty_or_invalid_addressee_ids", "addressee_ids")
        listeners = []
    else:
        if any(not stable(x, allowed) and x not in LISTENERS for x in listeners):
            issue("invalid_listener_id", "addressee_ids")
        if len({str(x) for x in listeners}) != len(listeners):
            issue("duplicate_listener_id", "addressee_ids")
        if scope == "single":
            if len(listeners) != 1 or not (stable(listeners[0], allowed) or listeners[0] == "unknown"):
                issue("single_requires_one_recipient", "addressee_ids")
            elif stable(speaker, allowed) and listeners == [speaker]:
                issue("single_recipient_is_speaker", "speaker_id", "addressee_ids")
        if scope == "group" and any(not stable(x, allowed) and x != "unknown" for x in listeners):
            issue("group_requires_stable_or_unknown", "addressee_ids")
    if item.get("content_type") == "thought" and scope != "self":
        issue("thought_self_mismatch", "content_type", "addressee_type", "addressee_ids")
    if not isinstance(item.get("evidence"), str) or len(item["evidence"]) > 120:
        issue("invalid_evidence", "evidence")
    expected = {c["id"]: c for c in target["candidates"]}
    mentions = item.get("mentions")
    if (not isinstance(mentions, list) or any(not isinstance(m, dict) for m in mentions)
            or len(mentions) != len(expected)
            or any(not isinstance(m.get("candidate_id"), str) for m in mentions)
            or {m.get("candidate_id") for m in mentions} != set(expected)):
        issue("name_coverage_changed", "mentions")
        return issues
    for mention in mentions:
        key, role, identity = mention["candidate_id"], mention.get("mention_type"), mention.get("name_target_id")
        if not isinstance(role, str) or role not in ROLES:
            issue("invalid_mention_type", "mentions", candidate_id=key)
        if "name_target_id" not in mention or (identity is not None and not stable(identity, allowed)):
            issue("invalid_name_target_id", "mentions", candidate_id=key)
        mapped = expected[key].get("mapped_ids", [])
        if identity is not None and mapped and mapped != [identity]:
            issue("mapped_name_id_conflict", "mentions", "addressee_ids" if role == "direct_address" else "mentions",
                  candidate_id=key)
        if role == "direct_address":
            if scope == "single" and len(listeners) == 1 and stable(listeners[0], allowed) and identity != listeners[0]:
                issue("direct_name_single_mismatch", "mentions", candidate_id=key)
            if identity is not None and identity not in listeners:
                issue("direct_name_recipient_mismatch", "mentions", "addressee_ids", candidate_id=key)
            if len(mapped) == 1 and scope == "single" and listeners != mapped:
                issue("known_direct_name_recipient_mismatch", "mentions", "addressee_ids", candidate_id=key)
    return issues


def fallback(item, target, issues, speaker_policy=None):
    """Retain independent facts; unresolved pairs never seed new name links."""
    item = copy.deepcopy(item) if isinstance(item, dict) else {}
    allowed = allowed_ids(target)
    speaker_labels = speakers(speaker_policy)
    dirty = {field for issue in issues for field in issue["fields"]}
    item["utterance_id"] = target["utterance_id"]
    if not isinstance(item.get("content_type"), str) or item["content_type"] not in CONTENT:
        item["content_type"] = "unknown"
    if (not stable(item.get("speaker_id"), allowed) and item.get("speaker_id") not in speaker_labels
            or speaker_policy is not None and item.get("speaker_id") == "narrator"
            and item["content_type"] != "narration"):
        item["speaker_id"] = "others"
    if not isinstance(item.get("addressee_type"), str) or item["addressee_type"] not in ADDRESS:
        item["addressee_type"] = "unknown"
    if "speaker_id" in dirty and "addressee_ids" in dirty:
        item["speaker_id"] = "others"
    scope = item["addressee_type"]
    if scope in {"single", "group"} and "addressee_ids" in dirty:
        item["addressee_ids"] = ["unknown"]
    if item["content_type"] == "thought" and scope != "self":
        item.update(content_type="unknown", addressee_type="unknown")
    item, _ = normalize(item, target)
    if not isinstance(item.get("evidence"), str):
        item["evidence"] = ""
    item["evidence"] = item["evidence"][:120]
    values = item.get("mentions", [])
    old = {m.get("candidate_id"): m for m in values if isinstance(m, dict) and isinstance(m.get("candidate_id"), str)} if isinstance(values, list) else {}
    affected = {x["candidate_id"] for x in issues if x.get("candidate_id")}
    if any("mentions" in x["fields"] and not x.get("candidate_id") for x in issues):
        affected.update(c["id"] for c in target["candidates"])
    mentions = []
    for candidate in target["candidates"]:
        key = candidate["id"]
        previous = old.get(key, {})
        role = previous.get("mention_type")
        identity = previous.get("name_target_id")
        mentions.append({"candidate_id": key, "mention_type": role if isinstance(role, str) and role in ROLES else "unknown",
                         "name_target_id": identity if stable(identity, allowed) and key not in affected else None})
    item["mentions"] = mentions
    return item, affected
