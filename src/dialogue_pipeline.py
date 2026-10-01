"""Standalone dialogue normalization, scene reasoning and Stage 3.

Run `python src/dialogue_pipeline.py --help`. MAGI is NOT imported here.
See docs/pipeline_usage.md for the full workflow.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dialogue_data import import_utterances, normalize_document, read_json, write_json


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
    reason = commands.add_parser("reason", help="Narrative scenes and Stage 3 via one sequential local VLM")
    reason.add_argument("input", type=Path, help="Normalized JSON, not a legacy transcript")
    reason.add_argument("--output", required=True, type=Path)
    reason.add_argument("--raw", type=Path, help="Matching raw MAGI JSON; normally located automatically")
    reason.add_argument("--bank", type=Path, help="Read-only character-bank metadata.json")
    reason.add_argument("--images", type=Path, help="page_N.png directory for imported utterances")
    reason.add_argument("--profile", choices=("cpu", "gpu16", "gpu48"), default="cpu")
    reason.add_argument("--config", type=Path, help="JSON overrides for the selected hardware profile")
    reason.add_argument("--model", help="Exact installed Ollama tag; models are never auto-downloaded")
    reason.add_argument("--host")
    reason.add_argument("--num-ctx", type=int)
    reason.add_argument("--num-predict", type=int)
    reason.add_argument("--max-targets", type=int)
    reason.add_argument("--max-images", type=int)
    reason.add_argument("--timeout", type=float)
    reason.add_argument("--think", action=argparse.BooleanOptionalAction, default=None)
    reason.add_argument("--unload-after", action=argparse.BooleanOptionalAction, default=None)
    reason.add_argument("--work-dir", type=Path, default=Path(__file__).resolve().parents[1] / "runs" / "reasoning")
    reason.add_argument("--text-only", action="store_true", help="Explicit ablation; do not silently omit images")
    reason.add_argument("--scenes-only", action="store_true")
    reason.add_argument("--no-order-correction", action="store_true", help="Ablation without 3D/local rerun")
    reason.add_argument("--scene-source", choices=("predicted", "none", "gold"), default="predicted")
    reason.add_argument("--reference", type=Path, help="Confirmed reference, only allowed for the gold-scene oracle")
    reason.add_argument("--dry-run", action="store_true", help="Write a plan without contacting Ollama")
    review = commands.add_parser("review-reasoning", help="Create a blank independent pilot annotation template")
    review.add_argument("input", type=Path)
    review.add_argument("--output", required=True, type=Path)
    evaluate = commands.add_parser("evaluate-reasoning", help="Score output against independently reviewed labels")
    evaluate.add_argument("input", type=Path, help="Exact normalized source JSON")
    evaluate.add_argument("--prediction", required=True, type=Path)
    evaluate.add_argument("--reference", required=True, type=Path)
    evaluate.add_argument("--output", required=True, type=Path)
    bench = commands.add_parser("benchmark-reasoning", help="Run explicit model/ablation jobs sequentially across a disjoint pilot")
    bench.add_argument("input", type=Path, help="Manifest with development/test chapters and model configurations")
    bench.add_argument("--output", required=True, type=Path)
    bench.add_argument("--work-dir", type=Path, default=Path(__file__).resolve().parents[1] / "runs" / "reasoning")
    return parser


def run(args: argparse.Namespace) -> int:
    inputs = [args.input]
    inputs += [getattr(args, key) for key in ("content_overrides", "merge_groups", "raw", "bank", "config", "prediction", "reference")
               if getattr(args, key, None) is not None]
    ensure_new_outputs([args.output], inputs)
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
    elif args.command == "reason":
        from reasoning_client import OllamaReasoner, load_config
        from reasoning_pipeline import load_chapter, pipeline_plan, run_reasoning
        if (args.scene_source == "gold") != (args.reference is not None):
            raise ValueError("--reference is required only for --scene-source gold; do not feed evaluation labels into predicted runs")
        config = load_config(args.profile, args.config, **{key: getattr(args, key) for key in
                     ("model", "host", "num_ctx", "num_predict", "max_targets", "max_images", "timeout", "think", "unload_after")})
        chapter = load_chapter(args.input, raw_path=args.raw, bank_path=args.bank,
                               images_dir=args.images, text_only=args.text_only)
        if args.dry_run:
            document = pipeline_plan(chapter, config, scenes_only=args.scenes_only, scene_source=args.scene_source,
                                     correct_order=not args.no_order_correction)
        else:
            client = OllamaReasoner(config, args.work_dir / "cache")
            document = run_reasoning(chapter, client, scenes_only=args.scenes_only,
                                     correct_order=not args.no_order_correction, scene_source=args.scene_source,
                                     reference=read_json(args.reference) if args.reference else None,
                                     checkpoint_dir=args.work_dir / "checkpoints")
        write_json(args.output, document)
        print(f"{document['kind']}: {args.output}")
    elif args.command == "review-reasoning":
        from reasoning_evaluation import review_template
        write_json(args.output, review_template(source))
        print(f"Blank reference: {args.output}. No labels have been confirmed.")
    elif args.command == "evaluate-reasoning":
        from reasoning_evaluation import evaluate
        report = evaluate(source, read_json(args.prediction), read_json(args.reference))
        write_json(args.output, report)
        print(f"Pilot evaluation: {args.output}")
    elif args.command == "benchmark-reasoning":
        from reasoning_benchmark import benchmark
        report = benchmark(args.input, args.work_dir)
        write_json(args.output, report)
        print(f"Sequential benchmark {report['status']}: {args.output}")
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
