"""Frozen person snapshot and semantic export shared by graph stages."""
import copy
import re
from pathlib import Path
from ..progress import NullProgress
from ..storage.io import atomic_json, fingerprint, read_json
from . import persons
from .semantic_contract import object_schema

def snapshot(store, raw):
    path = store.directory / "snapshots/persons-v6.json"
    if path.exists():
        return read_json(path)
    source_bank = Path(store.manifest["bank_path"])
    registry = persons.registry_path(store)
    before = read_json(registry) if registry.exists() else None
    state = persons.bootstrap(raw, read_json(source_bank), before,
                              run_scope=fingerprint([store.directory.name, store.manifest["source"]]))
    state["base_registry_hash"] = fingerprint(before)
    result = {"state": state, "source_bank_snapshot": persons.bank_snapshot(source_bank)}
    atomic_json(path, result)
    return result



def export(document, analyzed, linked):
    """Project identity separately; preserve effective speaker/name semantics."""
    result = copy.deepcopy(document)
    if analyzed.get("name_normalization") is not None:
        from .name_normalization import apply
        result = apply(document, analyzed["name_normalization"])
        result["name_normalization"] = copy.deepcopy(analyzed["name_normalization"])
    classified = {r["utterance_id"]: r for r in analyzed["utterances"]}
    state = linked["person_state"]
    retained, effects = [], []
    for row in result["utterances"]:
        item = copy.deepcopy(classified[row["id"]])
        pid = item.get("speaker_person_id")
        identity_pid = persons.canonical(state, pid)
        person = state["persons"].get(identity_pid, {})
        kind = item.get("speaker_kind", "person" if pid else "others")
        row.update(item)
        row.update(
            speech_style_evidence=item.get("speech_style_evidence", item["evidence"]),
            speech_style_source=analyzed["policy"],
            speech_style_status="needs_review" if item["speech_style"] == "unknown"
                or item.get("semantic_resolution") == "unresolved" or item.get("semantic_needs_review") else "provisional",
            speaker_person_id=pid, speaker_id=pid if pid else kind,
            speaker_display_label=person.get("primary_name") or identity_pid or kind,
            speaker_name=person.get("primary_name"),
            addressee_ids=copy.deepcopy(item["addressee_person_ids"]),
            identity_projection_policy="analyze-authoritative-v3", speaker_identity_id=identity_pid,
            addressee_identity_ids=[persons.canonical(state, p) for p in item["addressee_person_ids"]],
            mention_identity_ids={m["candidate_id"]: persons.canonical(state, m.get("target_person_id"))
                                  for m in item.get("mentions", []) if m.get("candidate_id")},
            voice_type="narrator" if kind == "narrator" else "character" if pid else "unknown")
        if row["content_type"] in {"sfx", "action_state"}:
            effects.append(row)
        elif row["content_type"] in {"ui", "watermark"}:
            result["excluded"].append(row)
        else:
            retained.append(row)
    result.update(kind="structured_dialogue", utterances=retained, effects=effects,
        person_registry_snapshot=state, name_links=linked["proposals"], pipeline_version=6, failures=[],
        unresolved_targets=analyzed.get("unresolved_targets", []),
        semantic_validation_issues=analyzed.get("semantic_validation_issues", []),
        semantic_coverage_complete=analyzed.get("semantic_coverage_complete", True),
        binding_snapshot_hash=linked["binding_snapshot_hash"])
    for field in ("scenes", "batches", "analysis_plan_hash", "story_graph"):
        if field in analyzed:
            result[field] = copy.deepcopy(analyzed[field])
    return result
