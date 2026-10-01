"""Standalone normalization, scene proposals, independent review and benchmark.

Run `python src/dialogue_pipeline.py --help`. MAGI is NOT imported here.
See docs/pipeline_usage.md for the full workflow.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dialogue_data import import_utterances, normalize_document, read_json, write_json
from scene_clustering import MODEL_SPECS, cluster_document, embed_dialogue
from scene_evaluation import benchmark, confirm_reference, review_markdown, review_template


def ensure_new_outputs(outputs: list[Path], inputs: list[Path]) -> None:
    resolved = [p.resolve() for p in outputs]
    if len(set(resolved)) != len(resolved) or set(resolved).intersection(p.resolve() for p in inputs):
        raise ValueError("Output paths must be distinct and must not replace inputs")
    if any(p.exists() for p in outputs):
        raise FileExistsError("Output already exists; use a new filename")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    convert = commands.add_parser("import-utterances", help="Adapt existing JSON records without inventing geometry")
    convert.add_argument("input", type=Path)
    convert.add_argument("--output", required=True, type=Path)
    convert.add_argument("--document-id", help="Stable unique chapter/document ID; default: input filename stem")
    normal = commands.add_parser("normalize", help="Normalize raw JSON; retain narration/thought and source boxes")
    normal.add_argument("input", type=Path)
    normal.add_argument("--output", required=True, type=Path)
    normal.add_argument("--content-overrides", type=Path)
    normal.add_argument("--merge-groups", type=Path)
    scene = commands.add_parser("scenes", help="Propose contiguous scenes with an explicitly chosen embedding")
    scene.add_argument("input", type=Path)
    scene.add_argument("--output", required=True, type=Path)
    scene.add_argument("--model", choices=tuple(MODEL_SPECS), required=True)
    scene.add_argument("--revision", help="Model commit/tag; resolved commit is stored in metadata")
    scene.add_argument("--distance-threshold", type=float, default=0.45)
    scene.add_argument("--cohesion-threshold", type=float, default=0.8)
    scene.add_argument("--boundary-window", type=int, default=2)
    scene.add_argument("--ambiguity-margin", type=float, default=0.08)
    review = commands.add_parser("review", help="Create BLANK independent gold template, not clustering labels")
    review.add_argument("input", type=Path)
    review.add_argument("--labels", required=True, type=Path)
    review.add_argument("--review-text", type=Path, help="Readable dialogue; default: labels filename with .md suffix")
    confirm = commands.add_parser("confirm-labels", help="USER command: confirm labels after manually reviewing them")
    confirm.add_argument("input", type=Path, help="Normalized dialogue")
    confirm.add_argument("--labels", required=True, type=Path)
    confirm.add_argument("--reviewer", required=True)
    confirm.add_argument("--output", required=True, type=Path)
    bench = commands.add_parser("benchmark", help="Tune development only; score separate test chapters")
    bench.add_argument("input", type=Path, help="Manifest with development/test dialogue+confirmed-label paths")
    bench.add_argument("--output", required=True, type=Path)
    bench.add_argument("--models", nargs="+", choices=tuple(MODEL_SPECS), default=list(MODEL_SPECS))
    bench.add_argument("--thresholds", default="0.25,0.35,0.45,0.55")
    bench.add_argument("--cohesion-threshold", type=float, default=0.8)
    bench.add_argument("--revisions", type=Path, help="Optional JSON backend→model commit mapping")
    for command in (scene, bench):
        command.add_argument("--device", default="cpu", choices=("cpu", "cuda"))
        command.add_argument("--cache-dir", type=Path, default=Path(__file__).resolve().parents[1] / "data" / "scene_embeddings")
        command.add_argument("--local-only", action="store_true", help="Refuse model downloads")
    return parser


def run(args: argparse.Namespace) -> int:
    inputs = [args.input]
    inputs += [getattr(args, key) for key in ("labels", "content_overrides", "merge_groups", "revisions")
               if getattr(args, key, None) is not None and not (args.command == "review" and key == "labels")]
    if args.command == "review":
        text_path = args.review_text or args.labels.with_suffix(".md")
        outputs = [args.labels, text_path]
    else:
        outputs = [args.output]
    ensure_new_outputs(outputs, inputs)
    source = read_json(args.input)
    if args.command == "import-utterances":
        document = import_utterances(source, document_id=args.document_id or args.input.stem)
        document["source_json_path"] = str(args.input.resolve())
        write_json(args.output, document)
        print(f"Imported {len(document['texts'])} records. Geometry is unavailable: {args.output}")
    elif args.command == "normalize":
        overrides = read_json(args.content_overrides) if args.content_overrides else None
        groups = read_json(args.merge_groups) if args.merge_groups else None
        document = normalize_document(source, overrides=overrides, merge_groups=groups)
        document["raw_json_path"] = str(args.input.resolve())
        write_json(args.output, document)
        print(f"Kept {len(document['utterances'])}; excluded {len(document['excluded'])}; "
              f"merge candidates {len(document['merge_candidates'])}: {args.output}")
    elif args.command == "scenes":
        # Validate thresholds before spending time loading a model.
        if not 0 <= args.distance_threshold <= 2 or not 0 <= args.cohesion_threshold <= 2:
            raise ValueError("Cosine distance thresholds must be in [0,2]")
        if args.boundary_window < 1 or not 0 <= args.ambiguity_margin <= 2:
            raise ValueError("Invalid boundary window/ambiguity margin")
        vectors, metadata = embed_dialogue(source, backend=args.model, cache_dir=args.cache_dir,
                                           device=args.device, local_only=args.local_only, revision=args.revision)
        document = cluster_document(source, vectors, distance_threshold=args.distance_threshold,
                                    cohesion_threshold=args.cohesion_threshold, boundary_window=args.boundary_window,
                                    ambiguity_margin=args.ambiguity_margin, embedding_metadata=metadata)
        write_json(args.output, document)
        count = len({r["scene_id"] for r in document["utterances"]})
        print(f"Proposed {count} scenes (uncalibrated, not gold): {args.output}")
    elif args.command == "review":
        labels = review_template(source)
        markdown = review_markdown(source)
        write_json(args.labels, labels)
        text_path.parent.mkdir(parents=True, exist_ok=True)
        with text_path.open("x", encoding="utf-8") as handle:
            handle.write(markdown)
        print(f"Independent review template: {args.labels}; dialogue: {text_path}. No labels confirmed.")
    elif args.command == "confirm-labels":
        labels = confirm_reference(source, read_json(args.labels), args.reviewer)
        write_json(args.output, labels)
        print(f"Reference labels confirmed by {args.reviewer}: {args.output}")
    elif args.command == "benchmark":
        report = benchmark(args.input, backends=args.models,
                           thresholds=[float(x.strip()) for x in args.thresholds.split(",")],
                           cohesion_threshold=args.cohesion_threshold, cache_dir=args.cache_dir,
                           device=args.device, local_only=args.local_only,
                           revisions=read_json(args.revisions) if args.revisions else None)
        write_json(args.output, report)
        for name, row in report["models"].items():
            if row["status"] == "ok":
                print(f"{name}: threshold {row['chosen_threshold']}; test {row['test']['summary']}")
            else:
                print(f"{name}: FAILED: {row['error']}", file=sys.stderr)
        print(f"Benchmark {report['status']}: {args.output}. No production model selected automatically.")
        return 0 if report["status"] == "complete" else 1
    return 0


def main() -> int:
    args = build_parser().parse_args()
    try:
        return run(args)
    except (OSError, ValueError, RuntimeError, KeyError, TypeError) as exc:
        print(f"Pipeline failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
