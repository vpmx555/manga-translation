"""Whole-chapter scenes followed by frozen, independently scheduled batches."""
import copy
import threading

from ..progress import NullProgress
from ..runtime.resources import event
from ..runtime.llm_budget import OutputTruncated
from ..storage.io import atomic_json, fingerprint, read_json
from .policy import POLICY, require_config
from .anchors import route
from .requests import Calls
from . import scene, planning, scheduling, merge
from .batch_contract import PROMPT, validate
from .semantic_contract import target_records, unresolved


def analyze(store, raw, document, scanned, provider, progress=None):
    from .semantic import snapshot
    config = store.manifest["config"]
    require_config(config)
    frozen = snapshot(store, raw)
    if scanned.get("person_snapshot_hash") != fingerprint(frozen):
        raise ValueError("Person input snapshot changed after scan")
    source_hash = fingerprint(document)
    if scanned.get("name_normalization") is not None:
        from .name_normalization import apply
        document = apply(document, scanned["name_normalization"])
    initial = copy.deepcopy(frozen["state"])
    rows = route([r for r in document["utterances"] if r.get("is_essential_text") is True],
                 raw, initial, read_json(store.manifest["bank_path"]))
    calls = Calls(store, provider)
    if config["ner"]["backend"] == "llm-scene" or scanned.get('policy') in {'canonical-scan-v3', 'canonical-scan-v4'}:
        if scanned.get("scene_source_hash") != source_hash or "scenes" not in scanned:
            raise ValueError("Combined scene/name scan missing or stale; rerun scan")
        scenes = copy.deepcopy(scanned["scenes"])
        if [identity for entry in scenes for identity in entry["target_ids"]] != [r["id"] for r in rows]:
            raise ValueError("Scanned scenes do not cover essential targets in source order")
    else:
        scenes = scene.plan(rows, calls)
    by_id = {r["id"]: r for r in rows}
    for entry in scenes:
        entry["caption_max_distance"] = config["v6"]["caption_max_distance"]
        for identity in entry["target_ids"]:
            by_id[identity]["scene_id"] = entry["scene_id"]
    candidates = {m["id"]: m for m in scanned["candidates"]}
    jobs = planning.plan(rows, scenes, initial, candidates, calls, config["v6"]["analyze_batch_size"])
    plan = {"policy": POLICY, "scenes": scenes, "batches": jobs,
            "source_hash": source_hash, "snapshot_hash": fingerprint(frozen),
            "name_normalization": scanned.get("name_normalization")}
    atomic_json(store.artifact_path("analyze").parent / "plan.json", plan)
    progress = progress or NullProgress()
    progress.reset(total=len(rows), unit="utterance")
    lock = threading.Lock()

    def worker(job):
        try:
            response = calls.call(job["batch_id"], "analyze_dialogue_v6", PROMPT,
                                  job["request"], job["schema"], reserve=job["reserve"])
        except (OutputTruncated, ValueError) as exc:
            from ..runtime.llm_budget import BudgetExceeded
            if isinstance(exc, BudgetExceeded) and not isinstance(exc, OutputTruncated):
                raise
            response = {"rejected_response": str(exc)}
        try:
            records = target_records(response, [r["id"] for r in job["rows"]])
        except (ValueError, TypeError):
            records = {}
        accepted = []
        for row in job["rows"]:
            try:
                item = validate(records.get(row["id"]), row, job, initial, config["v6"]["caption_max_distance"])
            except (ValueError, TypeError, KeyError) as exc:
                item = unresolved(row["id"], [m for m in job["mentions"].values() if m["utterance_id"] == row["id"]], [str(exc)])
                item.update({k: copy.deepcopy(row[k]) for k in ("scene_id", "analysis_branch", "essential_index", "speaker_anchor", "original_speaker", "routing_reason")})
                item.update(batch_id=job["batch_id"], anchor_exception=job["anchor_exception"], repair_count=0)
            accepted.append(item)
            with lock:
                progress.advance()
        with lock:
            store.commit_target("analyze", POLICY+":"+job["batch_id"], job, result=accepted)
        event("analyze_batch", batch_id=job["batch_id"], valid=sum(r["semantic_resolution"] == "resolved" for r in accepted),
              unresolved=sum(r["semantic_resolution"] == "unresolved" for r in accepted))
        return accepted

    # All scenes and both branches share one queue; no completion feeds another batch.
    completed, failures = scheduling.run(jobs, worker, provider, min(8, config["runtime"].get("llm_slots") or 8))
    source_only = []
    essential_ids = {r["id"] for r in rows}
    for source in document["utterances"]:
        if source["id"] not in essential_ids:
            item = unresolved(source["id"], [m for m in candidates.values() if m["utterance_id"] == source["id"]], [])
            item.update(semantic_resolution="source_only", repair_count=0, speaker_kind="others",
                        analysis_branch="translation-only", routing_reason="not_essential",
                        evidence="Nonessential source-only translation", addressee_type="unknown")
            source_only.append(item)
    utterances, state, events = merge.merge(document["utterances"], [*completed, source_only], initial, candidates, raw)
    unresolved_ids = [r["utterance_id"] for r in utterances if r["semantic_resolution"] == "unresolved"]
    return {"policy": POLICY, "utterances": utterances, "calling_events": events, "person_state": state,
            "name_normalization": scanned.get("name_normalization"),
            "speaker_merge_policy": config['v6']['speaker_merge_policy'],
            "source_bank_snapshot": frozen["source_bank_snapshot"], "failures": failures,
            "scenes": scenes, "batches": jobs, "analysis_plan_hash": fingerprint(plan),
            "unresolved_targets": unresolved_ids,
            "semantic_validation_issues": [{"target_id": r["utterance_id"], "batch_id": r.get("batch_id"),
                "scene_id": r.get("scene_id"), "errors": r.get("semantic_errors", []), "resolution": "unresolved"}
                for r in utterances if r["semantic_resolution"] == "unresolved"],
            "semantic_coverage_complete": not failures and not unresolved_ids and len(utterances) == len(document["utterances"])}
