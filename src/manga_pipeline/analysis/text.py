"""One text request plus at most one targeted repair, journaled before commits."""
import copy
from ..storage.io import atomic_json, fingerprint, read_json
from . import contract, pairs, speech
from .context import compact

FIELDS = ("content_type", "speaker_id", "addressee_type", "addressee_ids", "mentions", "evidence")


def infer_once(provider, *args):
    return getattr(provider, "infer_once", provider.infer)(*args)


def received(value, target, fields=FIELDS):
    matches = [row for row in (value or {}).get("utterances", [])
               if isinstance(row, dict) and row.get("utterance_id") == target["utterance_id"]]
    if len(matches) != 1:
        return {}, [{"code": "target_coverage_changed", "fields": list(fields)}]
    return matches[0], []


def attempt(store, provider, root_request, model_request, start, index):
    path = store.artifact_path("analyze").parent / "attempts" / f"batch-{start:06d}-{index}.json"
    if path.exists():
        saved = read_json(path)
        if (saved["request_hash"] != fingerprint(saved["request"])
                or saved["result_hash"] != fingerprint([saved["value"], saved["error"]])):
            raise ValueError("Corrupt analyze attempt journal")
        for field in ("policy", "turns", "memory"):
            if saved["request"][field] != root_request[field]:
                raise ValueError("Analyze attempt input changed")
        if saved["request"].get("speaker_policy") != root_request.get("speaker_policy"):
            raise ValueError("Analyze attempt speaker policy changed")
        if saved["request"].get("speech_style_policy") != root_request.get("speech_style_policy"):
            raise ValueError("Analyze attempt speech style policy changed")
        expected = {t["utterance_id"]: t for t in root_request["targets"]}
        if any(t != expected.get(t["utterance_id"]) for t in saved["request"]["targets"]):
            raise ValueError("Analyze attempt targets changed")
        return saved["value"], saved["error"]
    wire, decode = compact(model_request)
    speaker_policy = root_request.get("speaker_policy")
    speech_policy = root_request.get("speech_style_policy")
    value, error = None, None
    try:
        value = decode(infer_once(provider, "analyze_dialogue_v3", contract.prompt(speaker_policy, speech_policy), wire,
                                  contract.schema(wire["targets"], speaker_policy, speech_policy), contract.readable))
    except Exception as exc:
        error = str(exc)
    atomic_json(path, {"request": model_request, "request_hash": fingerprint(model_request),
        "value": value, "error": error, "result_hash": fingerprint([value, error])})
    return value, error


def enrich(item, target, mentions, *, source, warnings, affected=()):
    roles = {m["candidate_id"]: m for m in item["mentions"]}
    candidates = target["identity_candidates"]
    return {**item, "classification_source": source, "warnings": warnings,
        "identity_candidates": candidates, "speaker_candidates": candidates, "listener_candidates": candidates,
        "mentions": [{**m, **{k: v for k, v in roles[m["id"]].items() if k != "candidate_id"},
                      "skipped": False, "unresolved": m["id"] in affected}
                     for m in mentions.get(target["utterance_id"], [])]}


def classify_batch(store, request, mentions, provider, start, progress):
    speaker_policy = request.get("speaker_policy")
    speech_policy = request.get("speech_style_policy")
    fields = FIELDS + (speech.FIELDS if speech_policy else ())
    accepted = {t["utterance_id"]: store.target("analyze", "text:" + t["utterance_id"], request)
                for t in request["targets"]}
    pending = [t for t in request["targets"] if accepted[t["utterance_id"]] is None]
    rejected, errors, warnings, speech_sources = {}, {}, {}, {}
    for index in range(2):
        if not pending:
            break
        progress.set_phase(f"batch {start // 3 + 1}: " + ("text" if index == 0 else "repair pairs"))
        model_request = {**copy.deepcopy(request), "targets": pending}
        if index:
            model_request["previous_results"] = [rejected[t["utterance_id"]] for t in pending
                                                 if rejected[t["utterance_id"]]]
            model_request["validation_errors"] = [{"utterance_id": t["utterance_id"],
                "issues": errors[t["utterance_id"]],
                "editable": sorted({f for e in errors[t["utterance_id"]] for f in e["fields"]})}
                for t in pending]
        value, model_error = attempt(store, provider, request, model_request, start, index)
        next_pending = []
        for target in pending:
            key = target["utterance_id"]
            raw, coverage = received(value, target, fields)
            editable = {f for e in errors.get(key, []) for f in e["fields"]}
            if index and rejected.get(key) and (raw or speaker_policy is not None):
                if speaker_policy is not None:
                    coverage = [{**e, "fields": sorted(editable)} for e in coverage]
                raw = {"utterance_id": key, **{f: copy.deepcopy(raw.get(f)) if f in editable and raw
                       else copy.deepcopy(rejected[key].get(f)) for f in fields}}
            item, normalized = pairs.normalize(raw, target)
            if speech_policy:
                speech_sources.setdefault(key, "model")
                if index and set(editable) & set(speech.FIELDS) and not speech.check(item) and not coverage and not model_error:
                    speech_sources[key] = "repaired"
            warnings.setdefault(key, []).extend(normalized)
            issues = coverage + pairs.check(item, target, speaker_policy)
            if speech_policy:
                issues += speech.check(item)
            if model_error:
                failed_fields = sorted(editable) if index and rejected.get(key) and speaker_policy is not None else list(fields)
                issues.append({"code": "model_request_failed", "fields": failed_fields, "detail": model_error[:160]})
            if issues:
                rejected[key], errors[key] = item, issues
                next_pending.append(target)
            else:
                result = enrich(item, target, mentions, source="repaired" if index else "model", warnings=warnings[key])
                if speech_policy:
                    result = speech.conclude(result, speech_sources[key])
                store.commit_target("analyze", "text:" + key, request, result=result)
                accepted[key] = result
        pending = next_pending
    for target in pending:
        key = target["utterance_id"]
        item, affected = pairs.fallback(rejected[key], target, errors[key], speaker_policy)
        warning = {"code": "unresolved_after_repair", "issues": [e["code"] for e in errors[key]]}
        result = enrich(item, target, mentions, source="fallback", warnings=warnings[key] + [warning], affected=affected)
        if speech_policy:
            result = speech.conclude(result, speech_sources[key], errors[key])
        store.commit_target("analyze", "text:" + key, request, result=result)
        accepted[key] = result
    return [accepted[t["utterance_id"]] for t in request["targets"]]
