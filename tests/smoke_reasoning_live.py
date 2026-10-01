"""Opt-in real-model smoke test; never executed by unittest discovery."""
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from dialogue_data import import_utterances, normalize_document, read_json, write_json
from reasoning_batching import Chapter
from reasoning_client import OllamaReasoner, atomic_json, load_config
from reasoning_pipeline import run_reasoning


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="Original utterance array, not model output")
    parser.add_argument("--images", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--limit", type=int, default=2)
    parser.add_argument("--profile", default="cpu")
    parser.add_argument("--model")
    args = parser.parse_args()
    if args.output.exists() or args.output.resolve() == args.input.resolve() or args.limit < 1:
        parser.error("Use a new output filename and a positive limit")
    status_path = args.output.with_suffix(".status.json")
    if status_path.resolve() == args.input.resolve():
        parser.error("Status path must not replace input")
    started = time.perf_counter()
    status = {"state": "running", "note": "Smoke verifies transport/schema/provenance, not model accuracy"}
    atomic_json(status_path, status)
    try:
        source = read_json(args.input)
        records = source if isinstance(source, list) else source["utterances"]
        raw = import_utterances(records[:args.limit], document_id="live-smoke")
        document = normalize_document(raw)
        chapter = Chapter(document, raw, images_dir=args.images)
        config = load_config(args.profile, model=args.model, max_targets=2, num_predict=768)
        client = OllamaReasoner(config, args.output.parent / "live-cache")
        original_infer = client.infer
        def report_infer(task, *values, **kwargs):
            status.update(stage=task, requests=len(client.events), elapsed_seconds=time.perf_counter() - started)
            atomic_json(status_path, status)
            return original_infer(task, *values, **kwargs)
        client.infer = report_infer
        result = run_reasoning(chapter, client, checkpoint_dir=args.output.parent / "live-checkpoints")
        write_json(args.output, result)
        status.update(state="passed", output=str(args.output), utterances=len(result["utterances"]),
                      requests=result["reasoning_run"]["requests"], elapsed_seconds=time.perf_counter() - started)
        atomic_json(status_path, status)
        print(json.dumps(status))
        return 0
    except Exception as exc:
        status.update(state="failed", error=str(exc), elapsed_seconds=time.perf_counter() - started)
        atomic_json(status_path, status)
        print(json.dumps(status))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
