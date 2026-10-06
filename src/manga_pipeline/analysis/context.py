"""Essential text context and role-aware memory; immutable inputs on disk."""
import copy
from ..bank.names import known_names
from ..names.dialogue import stable_id
from .contract import POLICY


def bank_view(bank):
    fields = ("id", "display_name", "aliases", "disabled", "name_source", "last_auto_name", "name_priority")
    return {"characters": {key: {field: copy.deepcopy(value[field]) for field in fields if field in value}
                           for key, value in bank.get("characters", {}).items()}}


def memory(previous):
    return [{"utterance_id": row["utterance_id"], "content_type": row["content_type"],
             "speaker_id": row["speaker_id"], "addressee_type": row["addressee_type"],
             "addressee_ids": row["addressee_ids"], "evidence": row.get("evidence", ""),
             "names": [{"name": m["name"], "id": m["name_target_id"], "role": m["mention_type"]}
                       for m in row.get("mentions", []) if m.get("name_target_id") is not None
                       and m.get("binding_status") in {"linked", "protected", "known"}]}
            for row in list(previous.values())[-10:]]


def identities(rows, index, mentions, previous, bank, names):
    pool = {}

    def add(value, source):
        identity = stable_id(value, bank)
        if identity is None:
            return
        character = bank["characters"][str(identity)]
        candidate = pool.setdefault(identity, {"id": identity, "name": character.get("display_name"),
            "aliases": list(character.get("aliases", [])), "sources": []})
        if source not in candidate["sources"]:
            candidate["sources"].append(source)

    for offset in range(max(0, index - 2), min(len(rows), index + 3)):
        row = rows[offset]
        add(row.get("speaker_id"), "MAGI" if offset == index else "context:MAGI")
        for mention in mentions.get(row["id"], []):
            for identity in names.get(mention["key"], []):
                add(identity, "name/alias")
    for offset in range(max(0, index - 10), index):
        row = rows[offset]
        add(row.get("speaker_id"), "memory:MAGI")
        accepted = previous.get(row["id"], {})
        add(accepted.get("speaker_id"), "memory:speaker")
        for identity in accepted.get("addressee_ids", []):
            add(identity, "memory:listener")
        for mention in accepted.get("mentions", []):
            if mention.get("binding_status") in {"linked", "protected", "known"}:
                add(mention.get("name_target_id"), "memory:confirmed-name")
    return sorted(pool.values(), key=lambda candidate: candidate["id"])


def request_for(rows, start, mentions, previous, bank, speaker_policy=None):
    batch, names = rows[start:start + 3], known_names(bank)
    targets = []
    for index in range(start, start + len(batch)):
        row = rows[index]
        targets.append({"utterance_id": row["id"],
            "context_ids": [r["id"] for r in rows[max(0, index - 2):index + 3]],
            "candidates": [{"id": c["id"], "name": c["name"], "mapped_ids": names.get(c["key"], [])}
                           for c in mentions.get(row["id"], [])],
            "identity_candidates": identities(rows, index, mentions, previous, bank, names)})
    return {"policy": POLICY, **({"speaker_policy": speaker_policy} if speaker_policy is not None else {}),
            "targets": targets, "memory": memory(previous),
            "turns": [{"id": r["id"], "text": r["text"], "source_speaker_id": r.get("speaker_id")}
                      for r in rows[max(0, start - 2):start + len(batch) + 2]]}


def compact(request):
    wire = copy.deepcopy(request)
    turns = {r["id"]: f"u{i}" for i, r in enumerate(wire["turns"])}
    candidates = {c["id"]: f"n{i}" for i, c in enumerate(c for t in wire["targets"] for c in t["candidates"])}
    for row in wire["turns"]:
        row["id"] = turns[row["id"]]
    for target in wire["targets"]:
        target["utterance_id"] = turns[target["utterance_id"]]
        target["context_ids"] = [turns[key] for key in target["context_ids"]]
        for candidate in target["candidates"]:
            candidate["id"] = candidates[candidate["id"]]
    for i, row in enumerate(wire.get("memory", [])):
        row["utterance_id"] = turns.get(row["utterance_id"], f"h{i}")
    for item in wire.get("previous_results", []):
        item["utterance_id"] = turns[item["utterance_id"]]
        for mention in item.get("mentions", []) if isinstance(item.get("mentions"), list) else []:
            if isinstance(mention, dict):
                key = mention.get("candidate_id")
                mention["candidate_id"] = candidates.get(key, "invalid") if isinstance(key, str) else "invalid"
    for item in wire.get("validation_errors", []):
        item["utterance_id"] = turns[item["utterance_id"]]
        for error in item["issues"]:
            if error.get("candidate_id"):
                error["candidate_id"] = candidates.get(error["candidate_id"], "invalid")
    reverse_turns, reverse_names = {v: k for k, v in turns.items()}, {v: k for k, v in candidates.items()}

    def decode(value):
        result = copy.deepcopy(value)
        for row in result["utterances"]:
            if not isinstance(row, dict):
                continue
            key = row.get("utterance_id")
            row["utterance_id"] = reverse_turns.get(key, "invalid") if isinstance(key, str) else "invalid"
            for mention in row.get("mentions", []) if isinstance(row.get("mentions"), list) else []:
                if isinstance(mention, dict):
                    key = mention.get("candidate_id")
                    mention["candidate_id"] = reverse_names.get(key, "invalid") if isinstance(key, str) else "invalid"
        return result
    return wire, decode
