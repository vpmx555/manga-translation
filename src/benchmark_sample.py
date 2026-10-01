"""Run real embedding comparisons on ONE reviewed sample, without held-out claims.

Unlike dialogue_pipeline benchmark, this diagnostic never tunes/selects a
production threshold or treats this sample as an independent test set.
"""
from __future__ import annotations

import argparse
import gc
import importlib.metadata
import json
import platform
import time
from datetime import datetime, timezone
from pathlib import Path

from dialogue_data import import_utterances, normalize_document, read_json, write_json
from scene_clustering import MODEL_SPECS, adjacent_clusters, embed_dialogue
from scene_evaluation import reference_fingerprint, score_boundaries, validate_reference


def parse_args():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="Original utterance JSON or normalized dialogue")
    parser.add_argument("--labels", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--models", nargs="+", choices=tuple(MODEL_SPECS), default=list(MODEL_SPECS))
    parser.add_argument("--thresholds", default="0.25,0.35,0.45,0.55")
    parser.add_argument("--cohesion-threshold", type=float, default=0.8)
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--cache-dir", type=Path, default=root / "data" / "scene_embeddings")
    parser.add_argument("--model-cache-dir", type=Path, default=root / "data" / "scene_models")
    parser.add_argument("--resume", action="store_true", help="Retry missing/failed backends in a matching report")
    parser.add_argument("--local-only", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    original = read_json(args.input)
    document = original if isinstance(original, dict) and original.get("kind") == "normalized_dialogue" else normalize_document(
        import_utterances(original, document_id=args.input.stem))
    reference = validate_reference(document, read_json(args.labels))
    thresholds = sorted(set(float(x) for x in args.thresholds.split(",")))
    if not thresholds or any(not 0 <= t <= 2 for t in thresholds) or not 0 <= args.cohesion_threshold <= 2:
        raise ValueError("Thresholds must be finite cosine distances in [0,2]")
    if args.threads < 1 or len(args.models) != len(set(args.models)):
        raise ValueError("Choose positive CPU threads and distinct models")
    if args.output.resolve() in {args.input.resolve(), args.labels.resolve()}:
        raise ValueError("Report cannot replace source dialogue or reference labels")
    fingerprint = reference_fingerprint(document)
    config = {"reference_fingerprint": fingerprint, "thresholds": thresholds,
              "cohesion_threshold": args.cohesion_threshold, "device": "cpu", "cpu_threads": args.threads}
    if args.output.exists():
        if not args.resume:
            raise FileExistsError("Report exists; choose a new path or use --resume")
        report = read_json(args.output)
        if report.get("config") != config or report.get("kind") != "scene_sample_benchmark":
            raise ValueError("Cannot resume a report with different source/configuration")
    else:
        report = {"schema_version": 1, "kind": "scene_sample_benchmark", "config": config,
                  "created_at": datetime.now(timezone.utc).isoformat(), "source": str(args.input.resolve()),
                  "labels": str(args.labels.resolve()), "utterances": len(document["utterances"]),
                  "known_gaps": sum(v is not None for v in reference.values()),
                  "ambiguous_before_turns": [i + 1 for i, v in reference.items() if v is None],
                  "reference_boundary_before_turns": [i + 1 for i, v in reference.items() if v],
                  "independent_test_set": False, "production_model_selected": False,
                  "threshold_selected": False, "models": {},
                  "limitations": ["Single reviewed sample; descriptive results only, not generalization estimates.",
                                  "All listed thresholds are reported; no tuning/test split or production selection.",
                                  "Unknown gaps are excluded from scoring.",
                                  "Encoding measured once; download/load time reported separately.",
                                  "Scores depend on clustering as well as embeddings; translation quality is not measured."]}
    import torch
    torch.set_num_threads(args.threads)
    torch.set_num_interop_threads(1)
    report["software"] = {"python": platform.python_version(), "torch": torch.__version__,
                          **{name: importlib.metadata.version(name) for name in ("transformers", "sentence-transformers", "numpy")}}
    for backend in args.models:
        if report["models"].get(backend, {}).get("status") == "ok":
            print(f"SKIP completed {backend}", flush=True)
            continue
        print(f"START {backend}: {MODEL_SPECS[backend]['model']}", flush=True)
        started = time.perf_counter()
        try:
            vectors, metadata = embed_dialogue(document, backend=backend, cache_dir=args.cache_dir,
                                               model_cache_dir=args.model_cache_dir,
                                               device="cpu", local_only=args.local_only, batch_size=8)
            evaluations = []
            for threshold in thresholds:
                clusters = adjacent_clusters(vectors, distance_threshold=threshold,
                                             cohesion_threshold=args.cohesion_threshold)
                cuts = {start for start, _ in clusters if start > 0}
                evaluations.append({"distance_threshold": threshold,
                                    "scene_ranges_1_based": [[a + 1, b] for a, b in clusters],
                                    "proposed_boundary_before_turns": [i + 1 for i in sorted(cuts)],
                                    "metrics": score_boundaries(len(vectors), cuts, reference)})
            report["models"][backend] = {"status": "ok", "embedding": metadata,
                                         "evaluations": evaluations,
                                         "total_seconds": time.perf_counter() - started}
            fixed = next((x for x in evaluations if x["distance_threshold"] == 0.45), evaluations[0])
            metrics = fixed["metrics"]
            print(f"DONE {backend}: threshold={fixed['distance_threshold']} "
                  f"F1={metrics['boundary_f1']:.4f} WindowDiff={metrics['window_diff']} "
                  f"encode_seconds={metadata['encode_seconds']} "
                  f"cuts={fixed['proposed_boundary_before_turns']}", flush=True)
        except Exception as exc:
            # Persist genuine backend errors and continue the other requested models.
            report["models"][backend] = {"status": "failed", "error_type": type(exc).__name__,
                                         "error": str(exc), "total_seconds": time.perf_counter() - started}
            print(f"FAILED {backend}: {type(exc).__name__}: {exc}", flush=True)
        report["status"] = "running"
        write_json(args.output, report, overwrite=args.output.exists())
        gc.collect()
    report["status"] = "complete" if all(report["models"].get(b, {}).get("status") == "ok" for b in args.models) else "incomplete"
    report["completed_at"] = datetime.now(timezone.utc).isoformat()
    write_json(args.output, report, overwrite=True)
    print(f"REPORT {report['status']}: {args.output}", flush=True)
    return 0 if report["status"] == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
