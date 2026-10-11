"""Sequential, resumable v5 chapter batches on the managed Runpod runtime."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

import manage


def run_directory(entry):
    base = manage.RUNTIME / "data/outputs" / entry["story_key"] / entry["chapter"]
    runs = sorted(base.glob("*/manifest.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not runs:
        return None
    manifest = manage.read_json(runs[0])
    expected = manage.RUNTIME / "data/input" / entry["input"]
    if manifest.get("pipeline_version") != 5 or Path(manifest["source"]["folder"]).resolve() != expected.resolve():
        raise RuntimeError("Batch target contains an unrelated run")
    return runs[0].parent


def verify(directory, entry):
    """Proof of completed workload, not merely a live process."""
    sys.path.insert(0, str(manage.PROJECT / "src"))
    from manga_pipeline.storage.runs import RunStore
    store = RunStore(directory)
    for path in (store.artifact_path("analyze").parent / "attempts").glob("*.json"):
        error = str(manage.read_json(path, {}).get("error") or "")
        if "context budget" in error or "context reserve" in error:
            raise RuntimeError("Analysis hit a context limit; fallback is not a completed workload")
    with store.verification_pass():
        if not all(store.completed(step) for step in store.steps):
            raise RuntimeError("Chapter has incomplete checkpoints")
        raw, exported, translated = store.read("extract"), store.read("export"), store.read("translate")
    config = store.manifest["config"]
    if (store.manifest.get("speech_style_policy") != "semantic-speech-v1"
            or config["character_character_matching_threshold"] != 0.75
            or config["character_detection_threshold"] != 0.35
            or config["inpaint"]["enabled"]):
        raise RuntimeError("Chapter did not use the confirmed v5 defaults")
    if raw.get("panel_source") != "koharu" or len(raw["pages"]) != entry["pages"]:
        raise RuntimeError("Koharu provenance/page count mismatch")
    fields = ("speech_style", "speech_style_evidence", "speech_style_source", "speech_style_status", "source_boxes")
    if any(any(k not in row for k in fields) for row in exported["utterances"]):
        raise RuntimeError("Export lost semantic/provenance fields")
    revision = manage.read_json(Path(translated["directory"]) / "result.json")
    count = len(exported["utterances"]) + len(exported.get("translation_only", []))
    if revision.get("failures") or any(len(revision["styles"].get(s, [])) != count for s in ("natural", "localized")):
        raise RuntimeError("Translation coverage incomplete")
    return {"pages": len(raw["pages"]), "utterances": len(exported["utterances"]),
            "translation_only": len(exported.get("translation_only", [])), "translations_per_style": count,
            "unknown_styles": sum(r["speech_style"] == "unknown" for r in exported["utterances"])}


def command_for(entry, folder, directory):
    if directory:
        config = manage.read_json(directory / "manifest.json")["config"]
        if config["ollama"]["num_ctx"] != manage.configuration()["ollama"]["num_ctx"]:
            # Never mutate or reuse committed fallback analysis under a new model budget.
            return ["reanalyze", "--run-dir", str(directory)]
        return ["resume", "--run-dir", str(directory)]
    return ["run", str(folder), "--story", entry["story"], "--chapter", entry["chapter"]]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plan", type=Path)
    parser.add_argument("--state", required=True, type=Path)
    args = parser.parse_args(argv)
    plan = manage.read_json(args.plan)
    entries = plan["chapters"]
    if not entries or len({(e["story_key"], e["chapter"]) for e in entries}) != len(entries):
        raise ValueError("Empty or duplicate chapter plan")
    for entry in entries:
        for field in ("story_key", "chapter", "input"):
            value = entry[field]
            if not isinstance(value, str) or not value or value in {".", ".."} or any(c in value for c in "/\\\r\n"):
                raise ValueError("Unsafe batch target")
        if type(entry["pages"]) is not int or entry["pages"] <= 0:
            raise ValueError("Invalid page count")
    state = manage.read_json(args.state, {"plan": plan, "chapters": [], "created_at": time.time()})
    if state["plan"] != plan:
        raise ValueError("Cannot change a saved batch plan")
    with manage.lock("chapter-batch"):
        state.update(status="running", pid=manage.service_identity(os.getpid()),
                     execution_root=str(manage.execution_root()))
        # Preserve only environment provenance, not multi-GB packages or secrets.
        for source in (manage.execution_root() / "envs").glob("*/*"):
            if source.is_file() and source.name in {"requirements.lock", "ready.json"}:
                target = args.state.parent / "environment" / source.parent.name / source.name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
        manage.atomic_json(args.state, state)
        try:
            for index, entry in enumerate(entries):
                folder = manage.RUNTIME / "data/input" / entry["input"]
                images = [p for p in folder.iterdir() if p.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".bmp"}]
                if len(images) != entry["pages"]:
                    raise RuntimeError("Input page count mismatch")
                directory = run_directory(entry)
                if len(state["chapters"]) <= index:
                    state["chapters"].append({"story": entry["story"], "chapter": entry["chapter"]})
                record = state["chapters"][index]
                log = args.state.parent / f"chapter-{index + 1:02d}.log"
                record.update(status="running", started_at=time.time(), log=str(log))
                state["current"] = index
                manage.atomic_json(args.state, state)
                command = command_for(entry, folder, directory)
                env = manage.environment()
                # Export each stage's Markdown on completion; agents do not
                # need to read or approve these human-facing artifacts.
                env["MANGA_DEFER_REVIEWS"] = "0"
                with log.open("ab") as output:
                    result = subprocess.run([sys.executable, str(manage.PROJECT / "deployment/runpod/manage.py"),
                                             *command, "--no-progress"], cwd=manage.PROJECT,
                                            env=env, stdin=subprocess.DEVNULL,
                                            stdout=output, stderr=subprocess.STDOUT)
                directory = run_directory(entry)
                record.update(exit_code=result.returncode, run_dir=str(directory), finished_at=time.time())
                if result.returncode:
                    record["status"] = "failed"
                    raise RuntimeError(f"Chapter {index + 1} failed; inspect {log}")
                # One complete review per chapter, without re-running inference.
                record["phase"] = "review"
                manage.atomic_json(args.state, state)
                with log.open("ab") as output:
                    reviewed = subprocess.run([str(manage.current_python()), str(manage.PROJECT / "run.py"),
                        "review", "--run-dir", str(directory), "--no-progress"], cwd=manage.PROJECT,
                        env=manage.environment(), stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT)
                if reviewed.returncode:
                    record.update(status="failed", review_exit_code=reviewed.returncode)
                    raise RuntimeError(f"Chapter {index + 1} review failed; inspect {log}")
                record["phase"] = "verify"
                record.update(status="completed", summary=verify(directory, entry))
                record.update(phase="completed", finished_at=time.time())
                manage.atomic_json(args.state, state)
                manage.say(json.dumps(record, ensure_ascii=False))
            state.update(status="completed", finished_at=time.time())
            manage.atomic_json(args.state, state)
            return 0
        except BaseException as exc:
            state.update(status="interrupted" if isinstance(exc, KeyboardInterrupt) else "failed",
                         error=str(exc), updated_at=time.time())
            manage.atomic_json(args.state, state)
            raise


if __name__ == "__main__":
    raise SystemExit(main())
