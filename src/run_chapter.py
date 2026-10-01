"""Run source manga through MAGI, normalization and Stage 3 with one step-check file."""
from __future__ import annotations

import argparse
import ast
import hashlib
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from dialogue_data import read_json
from reasoning_client import OllamaReasoner, atomic_json, load_config
from reasoning_pipeline import load_chapter, run_reasoning

ROOT = Path(__file__).resolve().parents[1]


def default_folder() -> Path:
    # Read the constant without importing/loading the MAGI/torch process.
    tree = ast.parse((ROOT / "src" / "main.py").read_text(encoding="utf-8-sig"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "DEFAULT_IMAGE_FOLDER" for t in node.targets):
            return Path(ast.literal_eval(node.value.args[0]))
    raise ValueError("DEFAULT_IMAGE_FOLDER was not found in src/main.py")


def source_manifest(folder: Path) -> list[dict]:
    def natural(path):
        import re
        return [int(p) if p.isdigit() else p.casefold() for p in re.split(r"(\d+)", path.name)]
    paths = sorted((p for p in folder.iterdir() if p.suffix.lower() in (".png", ".jpg", ".jpeg")), key=natural)
    if not paths:
        raise ValueError(f"No manga images found in {folder}")
    return [{"path": str(p.resolve()), "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in paths]


def run(args) -> Path:
    folder = (args.image_folder or default_folder()).resolve()
    output_dir = args.output_dir.resolve()
    if output_dir == ROOT or ROOT not in output_dir.parents or folder == output_dir or folder in output_dir.parents:
        raise ValueError("Use a separate output folder inside the project, outside the image source")
    inputs = source_manifest(folder)
    config = load_config(args.profile, args.config, model=args.model)
    check_path = output_dir / "check.json"
    if check_path.exists():
        if not args.resume:
            raise FileExistsError("Output run exists; use --resume to continue it")
        check = read_json(check_path)
        if check["source_images"] != inputs:
            raise ValueError("Source images changed; use a new output folder")
    else:
        if output_dir.exists() and any(output_dir.iterdir()):
            raise FileExistsError("Choose an empty output folder for a new run")
        check = {"schema_version": 1, "kind": "chapter_step_checks", "source_folder": str(folder),
                 "source_images": inputs, "steps": {}, "started_at": datetime.now(timezone.utc).isoformat()}
    final_path = output_dir / "stage3" / "structured_dialogue.json"
    if final_path.exists():
        raise FileExistsError("Final output already exists; choose a new output folder")
    output_dir.mkdir(parents=True, exist_ok=True)
    check.update(status="running", pid=os.getpid(), output_dir=str(output_dir))
    check.pop("error", None)
    started = time.perf_counter()
    def save():
        check["updated_at"] = datetime.now(timezone.utc).isoformat()
        atomic_json(check_path, check)
    def snapshot(stage, artifact):
        previous = check["steps"].get(stage)
        if previous and previous.get("output") != artifact:
            check.setdefault("step_revisions", {}).setdefault(stage, []).append(previous)
        check["steps"][stage] = {"status": "complete", "saved_at": datetime.now(timezone.utc).isoformat(),
                                  "output": artifact}
        check["last_completed_step"] = stage
        save()
    try:
        # Preflight model availability; prepare does not load weights. MAGI exits
        # before any VLM inference to avoid holding both models in RAM.
        OllamaReasoner(config).prepare()
        raw_path = output_dir / "stage1" / "transcript.raw.json"
        normalized_path = output_dir / "stage2" / "dialogue.normalized.json"
        bank_root = output_dir / "character_banks"
        if "stage1" not in check["steps"]:
            check["current_task"] = "stage1_magi"
            save()
            if not bank_root.exists() and args.bank_root.exists():
                shutil.copytree(args.bank_root, bank_root)
            stage1_dir = output_dir / "stage1"
            stage1_dir.mkdir(parents=True, exist_ok=True)
            command = [sys.executable, "-B", str(ROOT / "src" / "main.py"), str(folder),
                       "--output", str(stage1_dir / "transcript.txt"), "--raw-json", str(raw_path),
                       "--normalized-json", str(normalized_path), "--bank-root", str(bank_root),
                       "--visualization-dir", str(stage1_dir / "visualizations"), "--device", args.device]
            # A failed extraction may have left exports. They belong to this run,
            # and --resume explicitly authorizes retrying that incomplete stage.
            if args.resume:
                command.append("--overwrite-json")
            with (stage1_dir / "magi.log").open("a", encoding="utf-8") as log:
                subprocess.run(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, check=True)
            snapshot("stage1", read_json(raw_path))
            snapshot("stage2_normalization", read_json(normalized_path))
        else:
            if not raw_path.exists() or not normalized_path.exists():
                raise ValueError("Resume needs completed raw/normalized exports")
            if "stage2_normalization" not in check["steps"]:
                snapshot("stage2_normalization", read_json(normalized_path))
        raw_document = read_json(raw_path)
        bank_path = bank_root / raw_document["story_id"] / "metadata.json"
        if not bank_path.exists():
            raise ValueError(f"Extracted story bank is missing: {bank_path}")
        chapter = load_chapter(normalized_path, raw_path=raw_path, bank_path=bank_path)
        client = OllamaReasoner(config, output_dir / "internal" / "cache")
        original_infer = client.infer
        def report_infer(task, *values, **kwargs):
            check.update(current_task=task, completed_requests=len(client.events))
            save()
            value = original_infer(task, *values, **kwargs)
            check["completed_requests"] = len(client.events)
            save()
            return value
        client.infer = report_infer
        result = run_reasoning(chapter, client, step_callback=snapshot)
        atomic_json(final_path, result)
        check.update(status="complete", current_task="complete", final_output=str(final_path),
                     elapsed_seconds=time.perf_counter() - started)
        save()
        return final_path
    except Exception as exc:
        check.update(status="failed", error=str(exc), elapsed_seconds=time.perf_counter() - started)
        save()
        raise


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image_folder", nargs="?", type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--bank-root", type=Path, default=ROOT / "data" / "character_banks")
    parser.add_argument("--device", choices=("auto", "cpu", "cuda"), default="auto")
    parser.add_argument("--profile", choices=("cpu", "gpu16", "gpu48"), default="cpu")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--model")
    parser.add_argument("--resume", action="store_true")
    return parser


if __name__ == "__main__":
    try:
        print(run(build_parser().parse_args()))
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
