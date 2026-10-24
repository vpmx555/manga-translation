"""Append-only, story-scoped relationships with frozen per-chapter evidence."""
import base64
import copy
import io
import hashlib
import itertools
from collections import defaultdict
from pathlib import Path

from ..storage.io import atomic_bytes, atomic_json, fingerprint, read_json, file_hash
from ..storage.locking import exclusive_lock
from ..runtime.inference_state import InferenceUncertain
from ..runtime.llm_budget import BudgetExceeded, OutputTruncated
from ..runtime.resources import ResourceExhausted
from . import persons, scheduling
from .requests import Calls
from .semantic_contract import object_schema

POLICY = "identity-relationships-append-only-v1"
PROMPT = """Infer the relationship and Vietnamese forms of address for the supplied
two identities using their dialogue and labelled representative crops. Member MAGI
IDs are already confirmed same identity; never change that. Dialogue is evidence
for social/family roles; images alone cannot prove friendship, family or status.
When dialogue is insufficient, use broad apparent age groups and relative age
from the crops to choose self-reference/address terms. Record visual_age_estimate,
not an exact age or a factual family relationship. For visual_age_estimate cite
at least one labelled crop from EACH identity. Keep unknown if both dialogue
and images are insufficient. Choose ONE chapter-wide directed address pair in
each direction; scene-specific changes are out of scope. Cite supplied utterance
IDs and/or crop IDs for each decision. Prior graph is read-only background.
No invented identities, ages, genders or evidence references. JSON only."""


def edge_key(a, b):
    return fingerprint(sorted([a, b]))


def graph_path(store):
    return persons.registry_path(store).with_name("relationships.json")


def empty_graph():
    return {"policy": POLICY, "identities": {}, "relations": {}}


def canonical(state, pid):
    return persons.canonical(state, pid) if pid in state["persons"] or pid in state.get("redirects", {}) else pid


def candidates(state, minimum, prior):
    """Count unique current-chapter detections, not bank preview/history totals."""
    observations, members = defaultdict(set), defaultdict(set)
    for ref, items in state.get("observations", {}).items():
        pid = canonical(state, state["cluster_to_person"][ref])
        for item in items:
            bank_id = item.get("source_bank_id")
            if bank_id is not None:
                members[pid].add(str(bank_id))
                if ref.startswith(state["extraction_scope"]+":"):
                    observations[pid].add((item["page_id"], tuple(item["bbox"])))
    old = {canonical(state, pid) for pid in prior["identities"]}
    return {pid: {"person_id": pid, "magi_ids": sorted(ids), "chapter_appearances": len(observations[pid])}
            for pid, ids in sorted(members.items()) if len(observations[pid]) >= minimum or pid in old}


def crop_selection(bank_path, magi_ids, count, max_side):
    """Rank paired bank crop vectors by distance to their MAGI prototype."""
    import numpy as np
    from PIL import Image
    from ..bank.prototypes import consensus_mean, normalized
    path = Path(bank_path)
    root = path.parent if path.is_file() else path
    metadata = read_json(path if path.is_file() else root / "bank.json")
    vectors_path = root / "embeddings/vectors.npz"
    if not vectors_path.exists():
        vectors_path = root / "embeddings.npz"
    arrays = {}
    if vectors_path.exists():
        with np.load(vectors_path, allow_pickle=False) as data:
            arrays = {key: data[key] for key in data.files}
    output, issues = [], []
    for mid in sorted(set(magi_ids)):
        record = metadata.get("characters", {}).get(mid, {})
        values = arrays.get("character_"+mid)
        paths = record.get("prototype_crop_paths", [])
        if values is None or len(paths) != len(values):
            issues.append({"magi_id": mid, "error": "Missing paired crop embeddings"})
            continue
        disabled = set(record.get("disabled_prototype_indices", []))
        indices = [i for i, p in enumerate(paths) if p and i not in disabled]
        if not indices:
            issues.append({"magi_id": mid, "error": "No active paired crops"})
            continue
        try:
            vectors = normalized(values[indices])
            center = consensus_mean(values[[i for i in range(len(values)) if i not in disabled]])
            distances = np.linalg.norm(vectors-center, axis=1)
            ordered = sorted(range(len(indices)), key=lambda i: (float(distances[i]), indices[i]))
            kept = 0
            for index in ordered:
                relative = paths[indices[index]]
                crop = (root / relative).resolve()
                if not crop.is_relative_to(root.resolve()):
                    raise ValueError("Crop path leaves bank")
                if not crop.is_file():
                    issues.append({"magi_id": mid, "error": "Missing crop", "path": relative})
                    continue
                with Image.open(crop) as image:
                    image = image.convert("RGB")
                    image.thumbnail((max_side, max_side))
                    buffer = io.BytesIO()
                    image.save(buffer, format="PNG")
                digest = file_hash(crop)
                output.append({"crop_id": f"magi:{mid}:{indices[index]}:{digest[:12]}", "magi_id": mid,
                    "path": relative, "sha256": digest, "distance": float(distances[index]),
                    "image": base64.b64encode(buffer.getvalue()).decode("ascii")})
                kept += 1
                if kept >= count:
                    break
        except (ValueError, OSError) as exc:
            issues.append({"magi_id": mid, "error": str(exc)})
    return output, issues


def evidence_rows(rows, a, b):
    """Only actual directed interactions/name references make a candidate edge."""
    selected = set()
    for index, row in enumerate(rows):
        if row.get("semantic_resolution") != "resolved":
            continue
        speaker = row.get("speaker_person_id")
        others = set(row.get("addressee_ids", []))
        others.update(m.get("target_person_id") for m in row.get("mentions", []) if m.get("confirmed_person_name"))
        if speaker == a and b in others or speaker == b and a in others:
            selected.update(range(max(0, index-2), min(len(rows), index+3)))
    fields = ("id", "text", "speaker_person_id", "addressee_ids", "mentions", "semantic_resolution")
    return [{k: rows[i].get(k) for k in fields} for i in sorted(selected)]


def wire_schema(a, b):
    text = {"type": "string", "minLength": 1, "maxLength": 256}
    direction = object_schema({"self": text, "address": text})
    return object_schema({"a": {"type": "string", "enum": [a]}, "b": {"type": "string", "enum": [b]},
        "relationship": text, "role_a": text, "role_b": text,
        "a_to_b": direction, "b_to_a": direction,
        "basis": {"type": "string", "enum": ["dialogue", "visual_age_estimate", "unknown"]},
        "evidence_refs": {"type": "array", "items": {"type": "string"}}, "reason": text})


def validate(value, request):
    if not isinstance(value, dict) or value.get("a") != request["a"] or value.get("b") != request["b"]:
        raise ValueError("Relationship identity changed")
    for key in ("relationship", "role_a", "role_b", "reason"):
        if not isinstance(value.get(key), str) or not value[key].strip() or len(value[key]) > 256:
            raise ValueError("Invalid relationship text")
    for key in ("a_to_b", "b_to_a"):
        if not isinstance(value.get(key), dict) or set(value[key]) != {"self", "address"}:
            raise ValueError("Invalid directed address")
        if any(not isinstance(v, str) or not v.strip() or len(v) > 256 for v in value[key].values()):
            raise ValueError("Invalid address terms")
    refs = value.get("evidence_refs")
    utterances = {r["id"] for r in request["targets"]}
    crops = {r["crop_id"] for r in request["crops"]}
    if not isinstance(refs, list) or any(not isinstance(r, str) or r not in utterances | crops for r in refs):
        raise ValueError("Unknown relationship evidence")
    basis = value.get("basis")
    if basis not in {"dialogue", "visual_age_estimate", "unknown"}:
        raise ValueError("Invalid relationship basis")
    if basis == "dialogue" and not utterances.intersection(refs):
        raise ValueError("Social relationship needs dialogue evidence")
    if basis == "visual_age_estimate":
        supported_people = {c.get("person_id") for c in request["crops"] if c["crop_id"] in refs}
        if not {request["a"], request["b"]}.issubset(supported_people):
            raise ValueError("Age estimate needs crop evidence for both identities")
        # Images cannot establish a factual social/family relationship.
        value = {**value, "relationship": "unknown", "role_a": "unknown", "role_b": "unknown"}
    if basis == "unknown":
        value = {**value, "relationship": "unknown", "role_a": "unknown", "role_b": "unknown",
                 "a_to_b": {"self": "unknown", "address": "unknown"},
                 "b_to_a": {"self": "unknown", "address": "unknown"}}
    return copy.deepcopy(value)


def expanded(graph, state):
    """Project shared relations to MAGI nodes without duplicating stored truth."""
    members = defaultdict(set)
    for pid, item in graph["identities"].items():
        members[canonical(state, pid)].update(item["magi_ids"])
    nodes = [{"magi_id": mid, "person_id": pid} for pid, ids in sorted(members.items()) for mid in sorted(ids)]
    edges = []
    for pid, ids in members.items():
        for a, b in itertools.combinations(sorted(ids), 2):
            edges.append({"a_magi": a, "b_magi": b, "relationship": "same_identity", "address": "none"})
    for relation in graph["relations"].values():
        a, b = canonical(state, relation["a"]), canonical(state, relation["b"])
        if a == b:
            continue
        for ma in sorted(members[a]):
            for mb in sorted(members[b]):
                edges.append({**relation, "a": a, "b": b, "a_magi": ma, "b_magi": mb})
    return {"nodes": nodes, "edges": edges}


def table(graph, state):
    result = []
    for relation in graph["relations"].values():
        a, b = canonical(state, relation["a"]), canonical(state, relation["b"])
        if a != b:
            result.append({**relation, "a": a, "b": b})
    return result


def build(store, document, provider):
    state = document["person_registry_snapshot"]
    cfg = store.manifest["config"]["v6"]
    path = graph_path(store)
    snapshot = store.directory / "snapshots/relationships-v1.json"
    source_hash = fingerprint([document, cfg["relationship_min_appearances"], cfg["relationship_crops_per_magi"]])
    result_path = store.artifact_path("translate").parent / "relationships.json"
    if result_path.exists():
        saved = read_json(result_path)
        if saved["source_hash"] != source_hash:
            raise ValueError("Relationship result source changed; create a new run")
        if not any(f.get("systemic") for f in saved["failures"]):
            return saved
    if snapshot.exists():
        frozen = read_json(snapshot)
        if frozen["source_hash"] != source_hash:
            raise ValueError("Relationship snapshot changed; create a new run")
    else:
        prior = read_json(path) if path.exists() else empty_graph()
        if prior.get("policy") != POLICY:
            raise ValueError("Unsupported relationship graph")
        eligible = candidates(state, cfg["relationship_min_appearances"], prior)
        rows = sorted([*document["utterances"], *document.get("effects", [])], key=lambda r: r["reading_order"])
        known = {edge_key(canonical(state, r["a"]), canonical(state, r["b"])) for r in prior["relations"].values()}
        jobs, crop_issues, crop_cache = [], [], {}
        for a, b in itertools.combinations(sorted(eligible), 2):
            key = edge_key(a, b)
            dialogue = evidence_rows(rows, a, b)
            if key in known or not dialogue:
                continue
            selected = []
            for pid in (a, b):
                if pid not in crop_cache:
                    crops, issues = crop_selection(store.manifest["bank_path"], eligible[pid]["magi_ids"],
                        cfg["relationship_crops_per_magi"], cfg["context_image_max_side"])
                    crop_cache[pid] = [{**c, "person_id": pid} for c in crops]
                    crop_issues.extend(issues)
                selected.extend(crop_cache[pid])
            request = {"policy": POLICY, "a": a, "b": b, "targets": dialogue,
                "identities": {pid: eligible[pid] for pid in (a, b)},
                "crops": [{k: v for k, v in c.items() if k != "image"} for c in selected],
                "prior_graph": [r for r in table(prior, state) if a in (r["a"], r["b"]) or b in (r["a"], r["b"])]}
            jobs.append({"batch_id": "relationship:"+key, "request": request, "images": [c["image"] for c in selected]})
        frozen = {"source_hash": source_hash, "prior": prior, "eligible": eligible, "jobs": jobs, "crop_issues": crop_issues}
        atomic_json(snapshot, frozen)
    references = {}
    for job in frozen["jobs"]:
        for crop, encoded in zip(job["request"]["crops"], job["images"]):
            name = fingerprint(crop["crop_id"])[:24]+".png"
            image_path = store.artifact_path("translate").parent / "relationship-crops" / name
            raw = base64.b64decode(encoded, validate=True)
            digest = hashlib.sha256(raw).hexdigest()
            if image_path.exists() and file_hash(image_path) != digest:
                raise ValueError("Retained relationship crop changed")
            if not image_path.exists():
                atomic_bytes(image_path, raw)
            references[crop["crop_id"]] = {**crop,
                "file": image_path.relative_to(store.directory).as_posix(), "retained_sha256": digest}
    calls = Calls(store, provider, "translate")
    failures = []

    def worker(job):
        request = job["request"]
        for attempt in range(2):
            try:
                value = calls.call(job["batch_id"], "relationship_vi_v1", PROMPT, request,
                    wire_schema(request["a"], request["b"]), reserve=2048 if attempt == 0 else 4096, attempt=attempt,
                    images=job["images"] or None)
                relation = validate(value, request)
                return [{**relation, "source_run": store.directory.name}]
            except (ValueError, TypeError, KeyError, OutputTruncated) as exc:
                if isinstance(exc, BudgetExceeded) and not isinstance(exc, OutputTruncated):
                    raise
                if attempt:
                    raise ValueError("Rejected relationship response: "+str(exc)) from exc
        raise AssertionError("unreachable")

    completed, failures = scheduling.run(frozen["jobs"], worker, provider,
        maximum=min(cfg["relationship_concurrency"], store.manifest["config"]["runtime"]["llm_slots"]),
        stop_on_error=lambda exc: isinstance(exc, (InferenceUncertain, OSError)), stage="relationship")
    # Append under a story lock; existing relations (including unknown) never change.
    path.parent.mkdir(parents=True, exist_ok=True)
    with exclusive_lock(path.with_suffix(".lock")):
        graph = read_json(path) if path.exists() else empty_graph()
        if graph.get("policy") != POLICY:
            raise ValueError("Unsupported relationship graph")
        for pid, item in frozen["eligible"].items():
            node = graph["identities"].setdefault(pid, {**item, "source_run": store.directory.name})
            node["magi_ids"] = sorted(set(node["magi_ids"]) | set(item["magi_ids"]))
        existing = {edge_key(canonical(state, r["a"]), canonical(state, r["b"])) for r in graph["relations"].values()}
        for batch in completed:
            for relation in batch:
                key = edge_key(relation["a"], relation["b"])
                if key not in existing:
                    graph["relations"][key] = relation
                    existing.add(key)
        atomic_json(path, graph)
    result = {"policy": POLICY, "graph": graph, "address_table": table(graph, state),
              "view": expanded(graph, state), "failures": failures, "crop_issues": frozen["crop_issues"],
              "reference_crops": list(references.values()),
              "graph_path": str(path), "source_hash": source_hash}
    atomic_json(result_path, result)
    if any(f["systemic"] for f in failures):
        raise InferenceUncertain("Relationship queue paused; partial results retained")
    return result
