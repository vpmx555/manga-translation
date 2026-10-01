"""Human-reviewed scene labels and leakage-checked embedding benchmarks."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import numpy as np

from dialogue_data import read_json, validate_normalized
from scene_clustering import MODEL_SPECS, adjacent_clusters, embed_dialogue


def reference_fingerprint(document: dict) -> str:
    """Ignore predicted scene fields: reference labels must bind to exact source text."""
    validate_normalized(document)
    fields = ("id", "text", "source_text_ids", "content_type", "speaker_id", "speaker_status",
              "speaker_cluster_id", "page", "source_order_in_scene", "addressee_ids", "addressee_type")
    source = {"document_id": document["document_id"],
              "utterances": [{key: row.get(key) for key in fields} for row in document["utterances"]]}
    return hashlib.sha256(json.dumps(source, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def review_template(document: dict) -> dict:
    """Create a BLANK reference template, never seed gold from clustering predictions."""
    fingerprint = reference_fingerprint(document)
    return {"schema_version": 1, "kind": "scene_reference", "document_id": document["document_id"],
            "reference_fingerprint": fingerprint, "status": "proposed", "reviewed_by": None,
            "annotation_source": "independent_dialogue_reading",
            "instructions": "Read the dialogue independently. Set boundary=true/false and a reason at each gap; "
                            "use status=ambiguous with boundary=null if dialogue alone is insufficient. "
                            "Only the user's explicit review can confirm these labels.",
            "boundaries": [{"before_utterance_id": row["id"], "boundary": None,
                            "status": "proposed", "reason": ""}
                           for row in document["utterances"][1:]]}


def validate_reference(document: dict, labels: dict, *, require_confirmed: bool = True) -> dict[int, bool | None]:
    fingerprint = reference_fingerprint(document)
    if labels.get("schema_version") != 1 or labels.get("kind") != "scene_reference":
        raise ValueError("Expected scene_reference schema v1")
    if labels.get("document_id") != document["document_id"] or labels.get("reference_fingerprint") != fingerprint:
        raise ValueError("Reference labels are stale or belong to a different dialogue")
    if labels.get("annotation_source") != "independent_dialogue_reading":
        raise ValueError("Gold labels must come from independent dialogue reading, not clustering")
    if require_confirmed and (labels.get("status") != "confirmed" or not labels.get("reviewed_by")):
        raise ValueError("Only explicitly user-confirmed references can enter benchmark")
    expected = {row["id"]: i for i, row in enumerate(document["utterances"]) if i > 0}
    entries = labels.get("boundaries")
    if not isinstance(entries, list) or len(entries) != len(expected):
        raise ValueError("Reference must include every inter-utterance gap")
    decisions = {}
    for row in entries:
        if not isinstance(row, dict) or row.get("status") not in {"proposed", "confirmed", "ambiguous"}:
            raise ValueError("Reference gap has an invalid review status")
        identifier = row.get("before_utterance_id")
        if identifier not in expected or expected[identifier] in decisions:
            raise ValueError("Reference has missing, duplicate or unknown utterance IDs")
        status, decision = row.get("status"), row.get("boundary")
        if status == "ambiguous":
            if decision is not None or not str(row.get("reason", "")).strip():
                raise ValueError("Ambiguous gaps require boundary=null and a reason")
        else:
            if require_confirmed and status != "confirmed":
                raise ValueError("Every known reference gap must be confirmed")
            if type(decision) is not bool:
                raise ValueError("Known reference gaps require true or false")
        decisions[expected[identifier]] = decision
    return decisions


def confirm_reference(document: dict, labels: dict, reviewer: str) -> dict:
    """Called only by the user's explicit `confirm-labels` command after review."""
    if not reviewer.strip():
        raise ValueError("Reviewer name must not be empty")
    validate_reference(document, labels, require_confirmed=False)
    result = copy.deepcopy(labels)
    result.update({"status": "confirmed", "reviewed_by": reviewer.strip()})
    for row in result["boundaries"]:
        if row["status"] != "ambiguous":
            row["status"] = "confirmed"
    validate_reference(document, result)
    return result


def score_boundaries(count: int, predicted: set[int], reference: dict[int, bool | None],
                     *, window: int | None = None) -> dict:
    if count < 2 or set(reference) != set(range(1, count)):
        raise ValueError("Evaluation requires at least two turns and every reference gap")
    if any(v is not None and type(v) is not bool for v in reference.values()):
        raise ValueError("Reference decisions must be booleans or ambiguous nulls")
    if any(type(x) is not int or not 1 <= x < count for x in predicted):
        raise ValueError("Predicted boundaries must be inter-utterance positions")
    known = {index for index, decision in reference.items() if decision is not None}
    if not known:
        raise ValueError("No confirmed unambiguous gaps to score")
    gold = {index for index in known if reference[index]}
    pred = predicted & known
    tp, fp, fn = len(gold & pred), len(pred - gold), len(gold - pred)
    precision = tp / (tp + fp) if tp + fp else (1.0 if not gold else 0.0)
    recall = tp / (tp + fn) if tp + fn else 1.0
    f1 = 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else 1.0
    # Approximate half the mean reference segment length. Explicitly report k.
    k = window if window is not None else max(1, round(count / (len(gold) + 1) / 2))
    if not 1 <= k < count:
        k = min(max(1, k), count - 1)
    mistakes = valid_windows = 0
    for start in range(count - k):
        gaps = set(range(start + 1, start + k + 1))
        if not gaps <= known:
            continue  # Do not treat uncertain reference gaps as false or true.
        valid_windows += 1
        mistakes += len(gaps & pred) != len(gaps & gold)
    return {"boundary_precision": precision, "boundary_recall": recall, "boundary_f1": f1,
            "window_diff": mistakes / valid_windows if valid_windows else None, "window_k": k,
            "valid_windows": valid_windows, "true_positives": tp,
            "over_split_errors": fp, "over_merge_errors": fn,
            "confirmed_gaps": len(known), "ambiguous_gaps": count - 1 - len(known),
            "reference_boundaries": len(gold), "predicted_boundaries_on_known_gaps": len(pred),
            "informative_positive_boundaries": bool(gold)}


def load_manifest(path: Path) -> dict[str, list[dict]]:
    manifest = read_json(path)
    datasets = {}
    seen_ids, seen_content = set(), set()
    for split in ("development", "test"):
        entries = manifest.get(split)
        if not isinstance(entries, list) or not entries:
            raise ValueError("Manifest needs nonempty development and test arrays")
        datasets[split] = []
        for entry in entries:
            dialogue_path = (path.parent / entry["dialogue"]).resolve()
            labels_path = (path.parent / entry["labels"]).resolve()
            document, labels = read_json(dialogue_path), read_json(labels_path)
            decisions = validate_reference(document, labels)
            if len(document["utterances"]) < 2 or all(v is None for v in decisions.values()):
                raise ValueError("Each evaluation document needs at least two turns and known reference gaps")
            # Different IDs/filenames must not hide duplicated evaluation text.
            content = hashlib.sha256(json.dumps([r["text"] for r in document["utterances"]],
                                                 ensure_ascii=False).encode()).hexdigest()
            if document["document_id"] in seen_ids or content in seen_content:
                raise ValueError("Duplicate chapter/content across benchmark documents: possible leakage")
            seen_ids.add(document["document_id"])
            seen_content.add(content)
            datasets[split].append({"document": document, "reference": decisions,
                                    "dialogue_path": str(dialogue_path), "labels_path": str(labels_path)})
    return datasets


def _aggregate(scores: list[dict]) -> dict:
    return {"documents": len(scores), "macro_boundary_f1": float(np.mean([s["boundary_f1"] for s in scores])),
            "macro_window_diff": (float(np.mean([s["window_diff"] for s in scores if s["window_diff"] is not None]))
                                  if any(s["window_diff"] is not None for s in scores) else None),
            "over_merge_errors": sum(s["over_merge_errors"] for s in scores),
            "over_split_errors": sum(s["over_split_errors"] for s in scores),
            "ambiguous_gaps": sum(s["ambiguous_gaps"] for s in scores),
            "positive_reference_boundaries": sum(s["reference_boundaries"] for s in scores)}


def benchmark(path: Path, *, backends: list[str], thresholds: list[float], cohesion_threshold: float = 0.8,
              cache_dir: Path | None = None, device: str = "cpu", local_only: bool = False,
              revisions: dict[str, str] | None = None, encoder=embed_dialogue) -> dict:
    """Tune per-model thresholds ONLY on development; evaluate test once afterward."""
    datasets = load_manifest(path)  # Validate all references before loading any model.
    if not backends or len(set(backends)) != len(backends) or any(b not in MODEL_SPECS for b in backends):
        raise ValueError("Choose distinct supported embedding backends")
    if not thresholds or any(not np.isfinite(t) or not 0 <= t <= 2 for t in thresholds):
        raise ValueError("Threshold grid must contain finite cosine distances in [0,2]")
    if not np.isfinite(cohesion_threshold) or not 0 <= cohesion_threshold <= 2:
        raise ValueError("Invalid cohesion threshold")
    report = {"schema_version": 1, "kind": "scene_benchmark", "manifest": str(path.resolve()),
              "thresholds": thresholds, "cohesion_threshold": cohesion_threshold,
              "selection_rule": "development macro boundary F1, then lower WindowDiff, then fewer over-merges",
              "test_used_for_threshold_selection": False, "production_model_selected": False,
              "limitations": ["Manga quality is not established by provider MTEB scores.",
                              "Timing includes model loading/cache reads; not a pure inference throughput comparison.",
                              "Only reviewed known gaps are scored; ambiguous windows are excluded.",
                              "Translation quality has not been evaluated by this benchmark."], "models": {}}
    for backend in backends:
        try:
            encoded = {}
            for split, entries in datasets.items():
                encoded[split] = []
                for entry in entries:
                    vectors, metadata = encoder(entry["document"], backend=backend, cache_dir=cache_dir,
                                                device=device, local_only=local_only,
                                                revision=(revisions or {}).get(backend))
                    encoded[split].append((entry, vectors, metadata))

            def evaluate(split, threshold):
                results = []
                for entry, vectors, metadata in encoded[split]:
                    clusters = adjacent_clusters(vectors, distance_threshold=threshold,
                                                 cohesion_threshold=cohesion_threshold)
                    cuts = {start for start, _ in clusters if start > 0}
                    results.append({"document_id": entry["document"]["document_id"],
                                    "metrics": score_boundaries(len(vectors), cuts, entry["reference"]),
                                    "embedding": metadata})
                return {"summary": _aggregate([r["metrics"] for r in results]), "details": results}

            tuning = [{"threshold": t, **evaluate("development", t)} for t in sorted(set(thresholds))]
            def rank(item):
                summary = item["summary"]
                wd = summary["macro_window_diff"]
                return (summary["macro_boundary_f1"], -(wd if wd is not None else 1),
                        -summary["over_merge_errors"])
            chosen = max(tuning, key=rank)
            report["models"][backend] = {"status": "ok", "chosen_threshold": chosen["threshold"],
                                         "development": tuning, "test": evaluate("test", chosen["threshold"])}
        except (RuntimeError, OSError, ValueError, ImportError) as exc:
            report["models"][backend] = {"status": "failed", "error": str(exc)}
    report["status"] = "complete" if all(m["status"] == "ok" for m in report["models"].values()) else "incomplete"
    return report


def review_markdown(document: dict) -> str:
    """Human-readable source-only review; excludes proposed clustering labels."""
    validate_normalized(document)
    lines = ["# Scene reference review", "", "Read in order. Decide whether a NEW scene begins before each turn after the first.",
             "Use the matching ID in the labels JSON. Do not use clustering output as gold.", ""]
    for row in document["utterances"]:
        lines.extend([f"## {row['reading_order'] + 1}. {row['id']}", "",
                      f"Page: {row.get('page')} | Speaker: {row.get('speaker_id')} | Type: {row.get('content_type')}", "",
                      str(row["text"]), ""])
    return "\n".join(lines)
