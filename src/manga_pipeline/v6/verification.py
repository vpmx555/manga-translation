"""Verify independent batch isolation and provenance from retained artifacts."""
from collections import Counter

from ..storage.io import fingerprint
from .policy import POLICY
from .scene import validate as validate_scenes
from .batch_contract import validate
from .planning import prepare
from .anchors import route


def verify(artifacts, config, frozen, bank, plan):
    analyzed = artifacts["analyze"]
    normalized = artifacts["normalize"]["utterances"]
    if artifacts['scan'].get('scene_wire') is not None:
        from .scene_wire import mapping
        from .name_normalization import registry_context
        if artifacts['scan']['scene_wire'] != mapping(normalized, registry_context(frozen['state'])):
            raise ValueError('Scene wire source/registry mapping changed')
    raw = artifacts["extract"]
    if analyzed.get("policy") != POLICY or plan.get("policy") != POLICY:
        raise ValueError("Legacy analyze policy is incompatible")
    if fingerprint(plan) != analyzed.get("analysis_plan_hash"):
        raise ValueError("Analysis plan hash mismatch")
    if fingerprint(plan["batches"]) != fingerprint(analyzed["batches"]):
        raise ValueError("Analyzed batch plan changed")
    if plan["snapshot_hash"] != fingerprint(frozen) or plan["source_hash"] != fingerprint(artifacts["normalize"]):
        raise ValueError("Frozen analysis input changed")
    results = analyzed["utterances"]
    if [r["utterance_id"] for r in results] != [r["id"] for r in normalized]:
        raise ValueError("Semantic merge coverage/order mismatch")
    if artifacts["scan"].get("name_normalization") is not None:
        from .name_normalization import apply
        normalized = apply(artifacts["normalize"], artifacts["scan"]["name_normalization"])["utterances"]
    essential = [r for r in normalized if r.get("is_essential_text") is True]
    scenes = analyzed["scenes"]
    if essential:
        if artifacts['scan'].get('policy') in {'canonical-scan-v3', 'canonical-scan-v4'}:
            expected_ids = [r['id'] for r in essential]
            valid = (all(isinstance(s.get('summary'), str) and s.get('target_ids')
                         and s.get('scene_id') == f'scene:{i}' for i, s in enumerate(scenes, 1))
                     and [uid for s in scenes for uid in s['target_ids']] == expected_ids)
        else:
            expected = validate_scenes({"scenes": [{"start_id": s["target_ids"][0], "summary": s["summary"]} for s in scenes]}, essential)
            valid = [{k: s[k] for k in ("scene_id", "summary", "target_ids")} for s in scenes] == expected
        if not valid:
            raise ValueError("Scene coverage is not contiguous")
    elif scenes:
        raise ValueError("Empty essential input has nonempty scenes")
    state = frozen["state"]
    routed = {r["id"]: r for r in route(essential, raw, state, bank)}
    scene_by_target = {identity: s for s in scenes for identity in s["target_ids"]}
    candidates = {m["id"]: m for m in artifacts["scan"]["candidates"]}
    records = {r["utterance_id"]: r for r in results}
    coverage = Counter()
    branch_cursors, preceding = {}, {}
    for number, job in enumerate(analyzed["batches"], 1):
        if job.get("batch_id") != f"batch:{number}":
            raise ValueError("Batch IDs must be unique and follow plan order")
        ids = [r["id"] for r in job["rows"]]
        if not ids or any(i not in routed for i in ids):
            raise ValueError("Batch contains an invalid target")
        entry = scene_by_target[ids[0]]
        rows = [{**routed[i], "scene_id": scene_by_target[i]["scene_id"]} for i in ids]
        if (any(r["scene_id"] != entry["scene_id"] or r["analysis_branch"] != rows[0]["analysis_branch"] for r in rows)
                or rows != job["rows"]):
            raise ValueError("Batch crossed scene/branch or changed source anchors")
        branch = rows[0]["analysis_branch"]
        key = (entry["scene_id"], branch)
        branch_rows = [routed[i] for i in entry["target_ids"] if routed[i]["analysis_branch"] == branch]
        start = branch_cursors.get(key, 0)
        end = start + len(rows)
        if ids != [r["id"] for r in branch_rows[start:end]]:
            raise ValueError("Batch target order/contiguity changed")
        expected_exception = None
        if branch == "dialogue" and not rows[0]["speaker_anchor"]["locked"]:
            expected_exception = "scene_start" if start == 0 else "hard_token_limit"
            if start and not preceding[key]["hard_split"]:
                raise ValueError("Unanchored start was not forced by a hard split")
        if job["anchor_exception"] != expected_exception:
            raise ValueError("Invalid anchor exception provenance")
        soft_end = min(len(branch_rows), start + config["v6"]["analyze_batch_size"])
        if branch == "dialogue":
            while soft_end < len(branch_rows) and not branch_rows[soft_end]["speaker_anchor"]["locked"]:
                soft_end += 1
        hard_split = end < soft_end
        overflow = job.get("overflow_budget")
        if (end > soft_end or job.get("hard_split") is not hard_split
                or (hard_split and (not overflow or overflow["estimate"] <= overflow["limit"]))
                or (not hard_split and overflow is not None)
                or job["budget"]["estimate"] > job["budget"]["limit"]):
            raise ValueError("Batch cut or token budget provenance changed")
        branch_cursors[key], preceding[key] = end, job
        reconstructed = prepare(rows, entry, state, candidates, job["anchor_exception"])
        if config.get('llm', {}).get('analyze_thinking'):
            reconstructed['reserve'] = config['llm']['analyze_num_predict']
        if any(reconstructed[k] != job[k] for k in reconstructed):
            raise ValueError("Batch pool, names, schema or source snapshot changed")
        for row in rows:
            coverage[row["id"]] += 1
            item = records[row["id"]]
            if (item.get("batch_id") != job["batch_id"] or item.get("scene_id") != entry["scene_id"]
                    or item.get("analysis_branch") != branch or item.get("speaker_anchor") != row["speaker_anchor"]
                    or item.get("anchor_exception") != expected_exception):
                raise ValueError("Result changed its batch/source provenance")
            if item.get("semantic_resolution") == "resolved":
                validate(item, row, job, state, config["v6"]["caption_max_distance"])
                import copy
                from .merge import resolve_speaker
                inferred = item.get('inferred_speaker')
                if not isinstance(inferred, dict):
                    raise ValueError('Missing pre-merge speaker inference')
                raw_item = {**item, **inferred}
                validate(raw_item, row, job, state, config['v6']['caption_max_distance'])
                expected = resolve_speaker(copy.deepcopy(raw_item), state)
                for field in ('speaker_person_id', 'speaker_cluster_ref', 'speaker_kind', 'speaker_resolution'):
                    if item.get(field) != expected.get(field):
                        raise ValueError('Speaker inference-first merge provenance changed')
            elif (item.get("semantic_resolution") != "unresolved" or item.get("semantic_response_used") is not False
                    or item.get("speaker_person_id") is not None or item.get("speaker_cluster_ref") is not None
                    or item.get("mentions") or not item.get("semantic_errors")):
                raise ValueError("Unresolved target consumed untrusted semantics")
    if coverage != Counter(list(routed)):
        raise ValueError("Batch coverage lost or duplicated targets")
    for source in normalized:
        if source["id"] not in routed:
            item = records[source["id"]]
            if item.get("semantic_resolution") != "source_only" or item.get("semantic_response_used") is not False:
                raise ValueError("Nonessential source was sent to semantic inference")
