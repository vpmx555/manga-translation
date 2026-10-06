"""Text-only dialogue batches with immutable participant-memory checkpoints."""
import copy
from ..bank.names import NameBank, known_names
from ..progress import NullProgress
from ..storage.io import atomic_json, fingerprint, read_json
from .stages import CONTENT_TYPES, MENTION_TYPES, object_schema, snapshot_bank
from . import dialogue_labels

# Keep the v1 prompt/schema intact for saved requests and resumed legacy batches.
# New runs select the v2 contract in dialogue_labels explicitly.
POLICY = "essential-dialogue-v1"
SPECIAL = {"UNKNOWN", "NARRATOR", "NOT_APPLICABLE"}
ADDRESS_TYPES = {"individual", "group", "self", "unspecified", "not_applicable"}
IDENTITY = {"type": ["integer", "string"]}
ITEM_SCHEMA = object_schema({
    "utterance_id": {"type": "string"},
    "content_type": {"type": "string", "enum": sorted(CONTENT_TYPES)},
    "speaker_id": IDENTITY,
    "addressee_ids": {"type": "array", "items": IDENTITY, "minItems": 1, "uniqueItems": True},
    "addressee_type": {"type": "string", "enum": sorted(ADDRESS_TYPES)},
    "mentions": {"type": "array", "items": object_schema({
        "candidate_id": {"type": "string"},
        "mention_type": {"type": "string", "enum": sorted(MENTION_TYPES)},
        "name_target_id": {"type": ["integer", "null"]}})},
    "evidence": {"type": "string", "maxLength": 120},
})
SCHEMA = object_schema({"utterances": {"type": "array", "items": ITEM_SCHEMA}})
PROMPT = (
    "Analyze EACH target in the supplied source order. Use only supplied text and stable ID candidates. "
    "If validation_errors are supplied, correct them for the targets. "
    "Each target's text context is restricted to its context_ids; the shared turns table also serves other targets. "
    "MAGI speaker hints may be corrected when text, names or conversational evidence contradict them. "
    "Return every target exactly once, and every target name candidate exactly once. "
    "Choose speaker and listeners only from that target's identity_candidates or UNKNOWN. "
    "Every target needs a speaker and a nonempty listener list; do not invent characters. "
    "Narration requires NARRATOR and [NOT_APPLICABLE], type not_applicable. "
    "Thought requires a character speaker or UNKNOWN and [NOT_APPLICABLE]. "
    "Coherent consecutive text alone does not prove narration. "
    "For dialogue infer individual/group/self/unspecified. Unknown listeners use [UNKNOWN]. "
    "An ordinary dialogue speaker is NOT its own listener. Same-speaker listening requires explicit self-address "
    "and addressee_type self. A pool containing only the speaker does not prove a listener: use UNKNOWN. "
    "A name used as direct_address identifies a listener; third_person mentions do not. "
    "Candidates may be NER mistakes: if not a person's proper name, use mention_type unknown and name_target_id null. "
    "Use already mapped names/aliases as evidence, and infer unnamed follow-up listeners from context/memory. "
    "For a direct_address name you may conclude name_target_id is one supplied stable listener ID, "
    "including for a new name; otherwise return null. Never reassign an already mapped name or invent an alias "
    "from a shared surname. Keep names as written. Do not infer scenes, change order or use images. "
    "evidence: at most twelve words supporting speaker/listener choices, never quote the entire utterance. Return JSON only."
)


def compact_payload(request):
    """Use short local references on the wire; retain source IDs in checkpoints."""
    payload = copy.deepcopy(request)
    turns = {turn["id"]: f"u{i}" for i, turn in enumerate(payload["turns"])}
    candidates = {candidate["id"]: f"n{i}" for i, candidate in enumerate(
        c for target in payload["targets"] for c in target["candidates"])}
    for turn in payload["turns"]:
        turn["id"] = turns[turn["id"]]
    for target in payload["targets"]:
        target["utterance_id"] = turns[target["utterance_id"]]
        target["context_ids"] = [turns[key] for key in target["context_ids"]]
        for candidate in target["candidates"]:
            candidate["id"] = candidates[candidate["id"]]
    for issue in payload.get("validation_errors", []):
        if isinstance(issue, dict) and issue.get("utterance_id") in turns:
            issue["utterance_id"] = turns[issue["utterance_id"]]
    reverse_turns = {v: k for k, v in turns.items()}
    reverse_candidates = {v: k for k, v in candidates.items()}

    def decode(value):
        result = copy.deepcopy(value)
        for item in result.get("utterances", []):
            item["utterance_id"] = reverse_turns.get(item.get("utterance_id"), "INVALID")
            for mention in item.get("mentions", []):
                mention["candidate_id"] = reverse_candidates.get(mention.get("candidate_id"), "INVALID")
        return result
    return payload, decode


def response_schema(targets, *, analysis_policy="essential-v1"):
    schema = copy.deepcopy(SCHEMA)
    allowed = sorted({c["id"] for target in targets for c in target["identity_candidates"]})
    properties = schema["properties"]["utterances"]["items"]["properties"]
    properties["utterance_id"] = {"enum": [target["utterance_id"] for target in targets]}
    if analysis_policy == "essential-v2":
        properties["speaker_id"] = {"enum": [*allowed, *dialogue_labels.SPEAKER_SPECIAL]}
        properties["addressee_ids"]["items"] = {"enum": [*allowed, *dialogue_labels.LISTENER_SPECIAL]}
        properties["addressee_type"]["enum"] = sorted(dialogue_labels.ADDRESS_TYPES)
    else:
        properties["speaker_id"] = {"enum": [*allowed, "UNKNOWN", "NARRATOR"]}
        properties["addressee_ids"]["items"] = {"enum": [*allowed, "UNKNOWN", "NOT_APPLICABLE"]}
    properties["mentions"]["items"]["properties"]["name_target_id"] = {"enum": [*allowed, None]}
    candidate_ids = [c["id"] for target in targets for c in target["candidates"]]
    if candidate_ids:
        properties["mentions"]["items"]["properties"]["candidate_id"] = {"enum": candidate_ids}
    else:
        properties["mentions"]["maxItems"] = 0
    schema["properties"]["utterances"].update(minItems=len(targets), maxItems=len(targets))
    if analysis_policy == "essential-v2":
        schema["properties"]["utterances"]["items"] = dialogue_labels.response_items(
            schema["properties"]["utterances"]["items"], targets)
    return schema


def stable_id(value, bank):
    if type(value) is not int:
        return None
    character = bank.get("characters", {}).get(str(value))
    return value if character and not character.get("disabled") else None


def identity_candidates(rows, index, mentions, previous, bank, names):
    pool = {}

    def add(identity, source, seen=None):
        identity = stable_id(identity, bank)
        if identity is None:
            return
        character = bank["characters"][str(identity)]
        item = pool.setdefault(identity, {"id": identity, "name": character.get("display_name"),
                                          "aliases": list(character.get("aliases", [])), "sources": []})
        if source not in item["sources"]:
            item["sources"].append(source)
        if seen is not None:
            item["last_seen"] = max(seen, item.get("last_seen", -1))

    for offset in range(max(0, index - 2), min(len(rows), index + 3)):
        add(rows[offset].get("speaker_id"), "MAGI" if offset == index else "context")
        for mention in mentions.get(rows[offset]["id"], []):
            for identity in names.get(mention["key"], []):
                add(identity, "name/alias")
    for offset in range(max(0, index - 10), index):
        turn = rows[offset]
        add(turn.get("speaker_id"), "memory:MAGI", offset)
        for mention in mentions.get(turn["id"], []):
            for identity in names.get(mention["key"], []):
                add(identity, "memory:name/alias", offset)
        inferred = previous.get(turn["id"], {})
        add(inferred.get("speaker_id"), "memory:LLM", offset)
        for identity in inferred.get("addressee_ids", []):
            add(identity, "memory:LLM", offset)
        for mention in inferred.get("mentions", []):
            identity = stable_id(mention.get("name_target_id"), bank)
            if identity is not None:
                add(identity, "memory:LLM:name", offset)
                aliases = pool[identity].setdefault("inferred_aliases", [])
                if mention["name"] not in aliases:
                    aliases.append(mention["name"])
    return sorted(pool.values(), key=lambda x: x["id"])


def validate_response(value, targets, *, analysis_policy="essential-v1"):
    expected = {x["utterance_id"]: x for x in targets}
    values = value.get("utterances")
    if not isinstance(values, list) or len(values) != len(expected) or {x.get("utterance_id") for x in values} != set(expected):
        raise ValueError("Dialogue response changed target coverage")
    for item in values:
        target = expected[item["utterance_id"]]
        allowed = {x["id"] for x in target["identity_candidates"]}
        kind = item.get("content_type")
        listeners = item.get("addressee_ids")
        speaker = item.get("speaker_id")
        address_types = dialogue_labels.ADDRESS_TYPES if analysis_policy == "essential-v2" else ADDRESS_TYPES
        if kind not in CONTENT_TYPES or item.get("addressee_type") not in address_types:
            raise ValueError("Invalid dialogue type")
        if not isinstance(listeners, list) or not listeners or len(listeners) != len(set(listeners)):
            raise ValueError("Each utterance requires distinct listener IDs")
        def eligible(identity):
            return type(identity) is int and identity in allowed
        if analysis_policy == "essential-v2":
            dialogue_labels.validate_identities(item, allowed)
        elif kind == "narration":
            if speaker != "NARRATOR" or listeners != ["NOT_APPLICABLE"] or item["addressee_type"] != "not_applicable":
                raise ValueError("Invalid narrator IDs")
        else:
            if speaker != "UNKNOWN" and not eligible(speaker):
                raise ValueError("Speaker outside supplied stable pool")
            if kind == "thought":
                if listeners != ["NOT_APPLICABLE"] or item["addressee_type"] != "not_applicable":
                    raise ValueError("Thought listeners must be not applicable")
            elif (any(x != "UNKNOWN" and not eligible(x) for x in listeners)
                  or ("UNKNOWN" in listeners and listeners != ["UNKNOWN"])
                  or item["addressee_type"] == "not_applicable"):
                raise ValueError(f"Listener invalid for {kind}: {listeners}; use {sorted(allowed)} or [UNKNOWN], not NOT_APPLICABLE")
        if analysis_policy != "essential-v2" and item["addressee_type"] == "group" and len(listeners) < 2:
            raise ValueError("Group requires multiple listeners")
        if item["addressee_type"] == "individual" and len(listeners) != 1:
            raise ValueError("Individual requires one listener")
        if analysis_policy != "essential-v2" and item["addressee_type"] == "self" and (not eligible(speaker) or listeners != [speaker]):
            raise ValueError("Self requires the known speaker ID")
        if kind == "dialogue" and eligible(speaker) and speaker in listeners and item["addressee_type"] != "self":
            raise ValueError("Same speaker/listener requires explicit self-address type")
        candidates = {c["id"]: c for c in target["candidates"]}
        mentions = item.get("mentions")
        if not isinstance(mentions, list) or len(mentions) != len(candidates) or {m.get("candidate_id") for m in mentions} != set(candidates):
            raise ValueError("Dialogue response changed name coverage")
        for mention in mentions:
            if mention.get("mention_type") not in MENTION_TYPES:
                raise ValueError("Invalid mention type")
            identity = mention.get("name_target_id")
            if identity is not None:
                candidate = candidates[mention["candidate_id"]]
                mapped = candidate.get("mapped_ids", [])
                if (kind != "dialogue" or mention["mention_type"] != "direct_address"
                        or not eligible(identity) or identity not in listeners
                        or (mapped and mapped != [identity])):
                    raise ValueError("Name conclusion must be an unambiguous stable direct listener")
        if not isinstance(item.get("evidence"), str) or len(item["evidence"]) > 120:
            raise ValueError("Evidence must be a short phrase")


def validate_coverage(value, targets):
    expected = {target["utterance_id"] for target in targets}
    values = value.get("utterances")
    if not isinstance(values, list) or len(values) != len(expected) or {x.get("utterance_id") for x in values} != expected:
        raise ValueError("Dialogue response changed target coverage")


class BatchValidator:
    """Commit good targets from live batches; cache only fully valid responses."""
    def __init__(self, decode, targets, analysis_policy):
        self.decode, self.targets, self.analysis_policy = decode, targets, analysis_policy

    def __call__(self, value):
        validate_coverage(self.decode(value), self.targets)

    def validate_cache(self, value):
        validate_response(self.decode(value), self.targets, analysis_policy=self.analysis_policy)


def classified_result(value, target, mentions):
    key = target["utterance_id"]
    roles = {m["candidate_id"]: m for m in value["mentions"]}
    return {**value, "classification_source": "model", "identity_candidates": target["identity_candidates"],
            "speaker_candidates": target["identity_candidates"], "listener_candidates": target["identity_candidates"],
            "mentions": [{**c, **{k: v for k, v in roles[c["id"]].items() if k != "candidate_id"},
                          "skipped": False} for c in mentions.get(key, [])]}


def classify_dialogue(store, document, scanned, provider, *, progress=None):
    progress = progress if progress is not None else NullProgress()
    analysis_policy = document["analysis_policy"]
    policy = dialogue_labels.POLICY if analysis_policy == "essential-v2" else POLICY
    prompt = dialogue_labels.PROMPT if analysis_policy == "essential-v2" else PROMPT
    rows, output, failures = document["utterances"], [], []
    bank = snapshot_bank(store, "dialogue")
    names = known_names(bank)
    mentions = {}
    for candidate in scanned["candidates"]:
        mentions.setdefault(candidate["utterance_id"], []).append(candidate)
    previous = {}
    progress.reset(total=len(rows), unit="utterance")
    # Three targets share a deduplicated text context. Memory is updated between
    # batches. Frozen plans preserve failed-batch inputs even if earlier retries succeed.
    for start in range(0, len(rows), 3):
        batch = rows[start:start + 3]
        path = store.directory / "04_classify" / "plans" / f"batch-{start:06d}.json"
        if path.exists():
            saved = read_json(path)
            request = saved["request"]
            if saved["hash"] != fingerprint(request) or request["policy"] != policy:
                raise ValueError("Dialogue batch plan changed/corrupt")
        else:
            targets = []
            for index in range(start, start + len(batch)):
                row = rows[index]
                candidates = [{"id": c["id"], "name": c["name"], "mapped_ids": names.get(c["key"], [])}
                              for c in mentions.get(row["id"], [])]
                targets.append({"utterance_id": row["id"], "candidates": candidates,
                                "context_ids": [x["id"] for x in rows[max(0, index - 2):index + 3]],
                                "identity_candidates": identity_candidates(rows, index, mentions, previous, bank, names)})
            request = {"policy": policy, "targets": targets,
                       "turns": [{"id": x["id"], "text": x["text"], "source_speaker_id": x.get("speaker_id")}
                                 for x in rows[max(0, start - 2):start + len(batch) + 2]]}
            atomic_json(path, {"request": request, "hash": fingerprint(request)})
        cached = {row["id"]: store.target("classify", row["id"], request) for row in batch}
        missing = [x for x in request["targets"] if cached[x["utterance_id"]] is None]
        fresh = {}
        if missing:
            pending, issues = list(missing), {}
            for attempt in range(store.manifest["config"]["ollama"]["retries"] + 1):
                try:
                    model_request = {**request, "targets": pending}
                    if issues:
                        model_request["validation_errors"] = (
                            [{"utterance_id": key, "error": error} for key, error in issues.items()]
                            if analysis_policy == "essential-v2" else list(issues.values()))
                    payload, decode = compact_payload(model_request)
                    validator = (BatchValidator(decode, pending, analysis_policy) if analysis_policy == "essential-v2"
                                 else lambda response: validate_coverage(decode(response), pending))
                    value = provider.infer("classify_dialogue", prompt, payload,
                                           response_schema(payload["targets"], analysis_policy=analysis_policy),
                                           validator)
                    value = decode(value)
                    validate_coverage(value, pending)
                    by_id = {x["utterance_id"]: x for x in value["utterances"]}
                    retry, issues = [], {}
                    for target in pending:
                        key = target["utterance_id"]
                        try:
                            validate_response({"utterances": [by_id[key]]}, [target], analysis_policy=analysis_policy)
                            result = classified_result(by_id[key], target, mentions)
                            store.commit_target("classify", key, request, result=result)
                            fresh[key] = result
                        except (ValueError, KeyError, TypeError) as exc:
                            retry.append(target)
                            issues[key] = str(exc)
                    pending = retry
                    if not pending:
                        break
                except Exception as exc:
                    issues = {target["utterance_id"]: str(exc) for target in pending}
                    break
            for target in pending:
                key = target["utterance_id"]
                error = issues[key]
                store.commit_target("classify", key, request, error=error)
                failures.append({"utterance_id": key, "error": error})
        for target in request["targets"]:
            key = target["utterance_id"]
            result = cached[key]
            if result is None and key in fresh:
                result = fresh[key]
            if result is not None:
                output.append(result)
                previous[key] = result
            progress.advance(reused=cached[key] is not None, failed=result is None)
    return {"policy": policy, "utterances": output, "failures": failures}


def link_direct_names(store, classified, *, progress=None):
    progress = progress if progress is not None else NullProgress()
    bank = NameBank(store.manifest["bank_path"])
    output, failures = [], []
    for row in classified["utterances"]:
        for mention in row["mentions"]:
            identity = mention.get("name_target_id")
            if identity is None:
                continue
            key = "direct:" + mention["id"]
            request = {"utterance_id": row["utterance_id"], "name": mention["name"],
                       "identity": identity, "evidence": row["evidence"]}
            result = store.target("link", key, request)
            reused = result is not None
            try:
                if result is None:
                    # A later/concurrent bank change must not silently remap a name.
                    mapped = bank.known().get(mention["key"], [])
                    if mapped and set(mapped) != {identity}:
                        result = {"status": "unknown", "reason": "name_bank_conflict", "candidate_id": mention["id"]}
                    elif mapped:
                        result = {"status": "skipped", "character_id": identity, "name": mention["name"],
                                  "reason": "name_already_mapped", "candidate_id": mention["id"]}
                    else:
                        outcome = bank.apply(fingerprint([str(store.directory), key]), identity, mention["name"],
                                             request, preserve_name=True)
                        result = {**outcome, "candidate_id": mention["id"], "reason": "llm_direct_address"}
                    store.commit_target("link", key, request, result=result)
                output.append(result)
                progress.advance(reused=reused)
            except Exception as exc:
                store.commit_target("link", key, request, error=str(exc))
                failures.append({"utterance_id": row["utterance_id"], "error": str(exc)})
                progress.advance(failed=True)
    return output, failures
