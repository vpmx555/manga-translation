"""Immutable translation revisions, replayable responses and a mutable manual layer."""
import copy
from pathlib import Path

from ..progress import NullProgress
from ..storage.io import atomic_json, file_hash, fingerprint, read_json
from ..storage.locking import exclusive_lock
from . import contract
from .context import ContextQuery
from .review import write_reviews
from .source import source_order, targets, view

DEFAULT_CONFIG = {"policy": contract.POLICY, "batch_size": 3, "context_radius": 2,
                  "num_predict": 1536, "glossary_path": None, "overrides_path": None, "instructions": ""}


def validate_config(config):
    if not isinstance(config, dict) or set(config) != set(DEFAULT_CONFIG) or config["policy"] != contract.POLICY:
        raise ValueError("Unsupported translation configuration")
    for key, maximum in (("batch_size", 8), ("context_radius", 10), ("num_predict", 16384)):
        if type(config[key]) is not int or not (0 if key == "context_radius" else 1) <= config[key] <= maximum:
            raise ValueError("Invalid translation " + key)
    if not isinstance(config["instructions"], str):
        raise ValueError("Translation instructions must be a string")
    for key in ("glossary_path", "overrides_path"):
        if config[key] is not None and not isinstance(config[key], str):
            raise ValueError("Translation paths must be strings or null")


def glossary_for(path):
    if path is None or not Path(path).is_file():
        return []
    data = read_json(path)
    if not isinstance(data, dict) or set(data) - {"entries"} or not isinstance(data.get("entries"), list):
        raise ValueError("Glossary requires entries array")
    result, seen = [], set()
    for entry in data["entries"]:
        if (not isinstance(entry, dict) or type(entry.get("confirmed")) is not bool
                or any(not isinstance(entry.get(k), str) or not entry[k].strip() for k in ("source", "vi"))):
            raise ValueError("Invalid glossary entry")
        if not entry["confirmed"]:
            continue
        if entry["source"].casefold() in seen:
            raise ValueError("Duplicate confirmed glossary source term")
        seen.add(entry["source"].casefold())
        result.append({"source": entry["source"], "vi": entry["vi"]})
    return result


def overrides_for(path, ids):
    if path is None or not Path(path).is_file():
        return {style: {} for style in contract.STYLES}
    data = read_json(path)
    if not isinstance(data, dict) or set(data) - set(contract.STYLES):
        raise ValueError("Unknown manual translation style")
    for style, values in data.items():
        if (not isinstance(values, dict) or set(values) - ids
                or any(not isinstance(x, str) or not x.strip() for x in values.values())):
            raise ValueError("Manual translations require known IDs and nonempty strings")
    return {style: data.get(style, {}) for style in contract.STYLES}


def receipt(directory, style, identity, plan_hash):
    path = directory / "targets" / style / (fingerprint(identity) + ".json")
    if not path.exists():
        return None
    saved = read_json(path)
    if saved["plan_hash"] != plan_hash or saved["result_hash"] != fingerprint(saved["result"]):
        raise ValueError("Translation target changed/corrupt")
    return saved["result"] if saved["status"] == "completed" else None


def commit(directory, style, identity, plan_hash, result=None, error=None):
    atomic_json(directory / "targets" / style / (fingerprint(identity) + ".json"), {
        "plan_hash": plan_hash, "status": "completed" if error is None else "failed",
        "result": result, "result_hash": fingerprint(result), "error": error})


def attempt(directory, provider, plan, model_request, style, start, cycle, index):
    path = directory / "attempts" / style / f"batch-{start:06d}-{cycle}-{index}.json"
    if path.exists():
        saved = read_json(path)
        if (saved["plan_hash"] != fingerprint(plan) or saved["request_hash"] != fingerprint(saved["request"])
                or saved["result_hash"] != fingerprint([saved["value"], saved["error"]])):
            raise ValueError("Translation attempt journal changed/corrupt")
        return saved["value"], saved["error"]
    wire = copy.deepcopy(model_request)
    wire.pop("prompt", None)
    wire.pop("source_hash", None)
    aliases = {row["id"]: f"u{i}" for i, row in enumerate(plan["turns"])}
    for row in wire["turns"] + wire["targets"]:
        row["id"] = aliases[row["id"]]
    for row in wire.get("previous_results", []):
        row["id"] = aliases[row["id"]]
    for row in wire.get("validation_errors", []):
        row["id"] = aliases[row["id"]]
    if "extension_context" in wire:
        wire["extension_context"] = {aliases[key]: value for key, value in wire["extension_context"].items()}
    expected = {row["id"]: row for row in wire["targets"]}
    def validator(value):
        contract.readable(value)
    def validate_cache(value):
        found = [x.get("id") for x in value["translations"] if isinstance(x, dict)]
        if len(found) != len(expected) or set(found) != set(expected):
            raise ValueError("Translation coverage changed")
        for row in value["translations"]:
            if contract.issues(row, expected[row["id"]], wire["glossary"]):
                raise ValueError("Invalid translation result")
    validator.validate_cache = validate_cache
    value, error = None, None
    try:
        infer = getattr(provider, "infer_once", provider.infer)
        value = infer("translate_vi_" + style, plan["prompt"], wire, contract.schema(list(expected)), validator)
        contract.readable(value)
        reverse = {v: k for k, v in aliases.items()}
        value = copy.deepcopy(value)
        for row in value["translations"]:
            if isinstance(row, dict):
                identity = row.get("id")
                row["id"] = reverse.get(identity, "invalid") if isinstance(identity, str) else "invalid"
    except Exception as exc:
        value, error = None, str(exc)[:300]
    atomic_json(path, {"plan_hash": fingerprint(plan), "request": model_request,
        "request_hash": fingerprint(model_request), "value": value, "error": error,
        "result_hash": fingerprint([value, error])})
    return value, error


def translate(document, source_path, directory, config, ollama_config, *, provider=None,
              glossary_path=None, overrides_path=None, context_provider=None, progress=None):
    """Never writes source_path or its run manifest/bank; all writes live in directory."""
    validate_config(config)
    progress = progress if progress is not None else NullProgress()
    rows = targets(document)
    inputs = {"source_hash": fingerprint(document), "source_adapter": "page-text-order-v1", "config": config, "ollama": ollama_config,
              "glossary": glossary_for(glossary_path), "prompts": {
                  s: contract.prompt(s, config["instructions"]) for s in contract.STYLES}}
    if context_provider is not None:
        views = tuple({**view(row), "source_position": {
            "page": source_order(row)[0], "text_index": source_order(row)[1]}} for row in rows)
        ids = {x for row in views for x in [row.get("speaker_id"), *row.get("addressee_ids", [])] if type(x) is int}
        query = ContextQuery(document.get("story_id", ""), document.get("chapter_id", ""),
                             inputs["source_hash"], views, tuple(sorted(ids)))
        context = dict(context_provider.get_context(query))
        if set(context) - {r["id"] for r in rows}:
            raise ValueError("Extension context must be keyed by known source target IDs")
        inputs["extension_context"] = context
        for style in contract.STYLES:
            inputs["prompts"][style] += (
                " Extension facts are keyed by target ID and scoped to that target's story time. "
                "Use only that target's facts for its translation; do not transfer later relationship "
                "or event facts from another target. Exported identities remain authoritative."
            )
    directory = Path(directory) / fingerprint(inputs)[:20]
    directory.mkdir(parents=True, exist_ok=True)
    with exclusive_lock(directory / ".translation.lock", timeout=1):
        return _translate(document, source_path, directory, inputs, rows, overrides_path, provider, progress)


def _translate(document, source_path, directory, inputs, rows, overrides_path, provider, progress):
    path = directory / "manifest.json"
    if path.exists():
        manifest = read_json(path)
        if manifest["inputs_hash"] != fingerprint(inputs) or manifest["inputs"] != inputs:
            raise ValueError("Translation revision changed/corrupt")
    else:
        manifest = {"policy": contract.POLICY, "source_path": str(Path(source_path).resolve()),
                    "inputs": inputs, "inputs_hash": fingerprint(inputs), "cycles": {}}
        atomic_json(path, manifest)
        atomic_json(directory / "snapshots/source.json", document)
    snapshot = read_json(directory / "snapshots/source.json")
    if fingerprint(snapshot) != inputs["source_hash"]:
        raise ValueError("Translation source snapshot changed/corrupt")
    manual = overrides_for(overrides_path, {r["id"] for r in rows})
    atomic_json(directory / "snapshots" / ("manual-" + fingerprint(manual) + ".json"), manual)
    owns_provider = provider is None
    if owns_provider:
        from ..providers.ollama import Ollama
        model = {**inputs["ollama"], "num_predict": inputs["config"]["num_predict"]}
        provider = Ollama(model, directory / "cache")
    output = {"policy": contract.POLICY, "source_hash": inputs["source_hash"], "directory": str(directory),
              "manual_hash": fingerprint(manual), "styles": {}, "failures": []}
    progress.reset(total=len(rows) * len(contract.STYLES), unit="translation")
    try:
        size = inputs["config"]["batch_size"]
        for style in contract.STYLES:
            results = {}
            for start in range(0, len(rows), size):
                batch = rows[start:start + size]
                radius = inputs["config"]["context_radius"]
                turns = [view(r) for r in rows[max(0, start-radius):start+size+radius]]
                plan = {"policy": contract.POLICY, "style": style, "source_hash": inputs["source_hash"],
                    "prompt": inputs["prompts"][style], "targets": [view(r) for r in batch], "turns": turns,
                    "glossary": [e for e in inputs["glossary"] if any(e["source"].casefold() in t["text"].casefold() for t in turns)]}
                if "extension_context" in inputs:
                    plan["extension_context"] = {r["id"]: inputs["extension_context"].get(r["id"], {}) for r in batch}
                plan_path = directory / "plans" / style / f"batch-{start:06d}.json"
                if plan_path.exists():
                    if read_json(plan_path) != plan:
                        raise ValueError("Translation plan changed/corrupt")
                else:
                    atomic_json(plan_path, plan)
                plan_hash = fingerprint(plan)
                accepted = {r["id"]: receipt(directory, style, r["id"], plan_hash) for r in batch}
                for r in batch:
                    if r["id"] in manual[style]:
                        accepted[r["id"]] = {"translation": manual[style][r["id"]], "needs_review": False,
                            "review_reason": "", "glossary_proposals": [], "status": "manual"}
                pending = [t for t in plan["targets"] if accepted[t["id"]] is None]
                cycle_key = f"{style}:{start}"
                cycle = manifest["cycles"].get(cycle_key, 0)
                rejected, errors = {}, {}
                attempted = bool(pending)
                for index in range(2):
                    if not pending:
                        break
                    progress.set_phase(f"{style}: batch {start // size + 1}" + (" repair" if index else ""))
                    request = {**copy.deepcopy(plan), "targets": pending}
                    if index:
                        request["previous_results"] = [rejected[t["id"]] for t in pending if rejected[t["id"]]]
                        request["validation_errors"] = [{"id": t["id"], "issues": errors[t["id"]]} for t in pending]
                    value, model_error = attempt(directory, provider, plan, request, style, start, cycle, index)
                    next_pending = []
                    for target in pending:
                        identity = target["id"]
                        matches = [r for r in (value or {}).get("translations", []) if isinstance(r, dict) and r.get("id") == identity]
                        item = matches[0] if len(matches) == 1 else {}
                        problems = [model_error] if model_error else (["target_coverage_changed"] if len(matches) != 1 else contract.issues(item, target, plan["glossary"]))
                        if problems:
                            rejected[identity], errors[identity] = item, problems
                            next_pending.append(target)
                        else:
                            result = {k: copy.deepcopy(item[k]) for k in ("translation", "needs_review", "review_reason", "glossary_proposals")}
                            if target.get("content_review_status") == "needs_review":
                                result["needs_review"] = True
                                result["review_reason"] = "Normalize flagged possible noise. " + result["review_reason"]
                            result["status"] = "review" if result["needs_review"] else ("repaired" if index else "translated")
                            commit(directory, style, identity, plan_hash, result=result)
                            accepted[identity] = result
                    pending = next_pending
                for target in pending:
                    identity = target["id"]
                    error = "; ".join(errors[identity])
                    commit(directory, style, identity, plan_hash, error=error)
                    accepted[identity] = {"translation": "", "status": "failed", "error": error,
                                          "needs_review": True, "review_reason": "Technical failure; resume to retry"}
                    output["failures"].append({"style": style, "id": identity, "error": error})
                if attempted:
                    manifest["cycles"][cycle_key] = cycle + 1
                    atomic_json(path, manifest)
                for row in batch:
                    results[row["id"]] = {**row, **accepted[row["id"]]}
                    progress.advance(reused=not attempted or row["id"] in manual[style])
            output["styles"][style] = [results[r["id"]] for r in rows]
            atomic_json(directory / f"{style}.json", {"source_hash": inputs["source_hash"], "style": style,
                                                       "translations": output["styles"][style]})
    finally:
        if owns_provider:
            provider.close()
    atomic_json(directory / "result.json", output)
    write_reviews(directory, output)
    atomic_json(directory.parent / "latest.json", {"directory": str(directory), "result_hash": file_hash(directory / "result.json")})
    return output
