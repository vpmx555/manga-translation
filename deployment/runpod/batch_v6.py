"""Frozen sequential v6 batches; preserve failures and attempt remaining chapters."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

import manage
from v6_contract import verify_v6


def safe_key(value):
    return isinstance(value, str) and bool(value) and all(c in "abcdefghijklmnopqrstuvwxyz0123456789-_" for c in value)


def latest_run(entry):
    base = manage.RUNTIME / "data/outputs" / entry["story_key"] / entry["chapter"]
    paths = sorted(base.glob("*/manifest.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not paths:
        return None
    manifest = manage.read_json(paths[0])
    source = manifest.get("source", {})
    expected = manage.RUNTIME / "data/input" / entry["input"]
    if (manifest.get("pipeline_version") != 6 or source.get("story") != entry["story_key"]
            or source.get("chapter") != entry["chapter"]
            or Path(source.get("folder", "")).resolve() != expected.resolve()):
        raise RuntimeError("Chapter has an unrelated/legacy run; choose a new chapter key")
    return paths[0].parent


def validate_plan(plan, config_path):
    if plan.get("pipeline_version") != 6 or not safe_key(plan.get("batch")):
        raise ValueError("Invalid v6 batch plan")
    entries = plan.get("chapters", [])
    if not entries or len({(e["story_key"], e["chapter"]) for e in entries}) != len(entries):
        raise ValueError("Empty or duplicate chapter plan")
    if hashlib.sha256(config_path.read_bytes()).hexdigest() != plan["config_sha256"]:
        raise ValueError("Frozen v6 configuration changed")
    bundle = manage.read_json(manage.PROJECT / "__bundle__.json", {})
    if bundle.get("version") != plan["source_bundle_hash"]:
        raise ValueError("Deployed source differs from the prepared plan")
    for entry in entries:
        if any(not safe_key(entry.get(field)) for field in ("story_key", "chapter", "input")):
            raise ValueError("Unsafe chapter target")
        images = entry.get("images", {})
        if type(entry.get("pages")) is not int or entry["pages"] <= 0 or len(images) != entry["pages"]:
            raise ValueError("Invalid input page count/manifest")
        for name, image in images.items():
            if (Path(name).name != name or "/" in name or "\\" in name or name in {".", ".."}
                    or not isinstance(image.get("sha256"), str) or len(image["sha256"]) != 64
                    or type(image.get("bytes")) is not int or image["bytes"] < 1):
                raise ValueError("Invalid input image receipt")


def input_folder(entry):
    folder = manage.RUNTIME / "data/input" / entry["input"]
    actual = {p.name for p in folder.iterdir() if p.is_file() and p.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".bmp"}}
    if actual != set(entry["images"]):
        raise RuntimeError("Input filenames/page count differ from the frozen manifest")
    for name, receipt in entry["images"].items():
        path = folder / name
        if path.stat().st_size != receipt["bytes"] or manage.file_hash(path) != receipt["sha256"]:
            raise RuntimeError("Input checksum mismatch: " + name)
    return folder


def errors_for(directory):
    if not directory:
        return [{"error": "No run was created"}]
    manifest = manage.read_json(directory / "manifest.json", {})
    errors = []
    for step, item in manifest.get("steps", {}).items():
        if item.get("status") != "completed":
            errors.append({"stage": step, "status": item.get("status"), "error": item.get("error", "")})
        for result in directory.glob("*_" + step + "/result.json"):
            for error in manage.read_json(result, {}).get("failures", []):
                errors.append({"stage": step, **error})
    return errors


def write_error_review(path, state):
    lines = ["# Runpod v6 — execution errors", "",
             "Attempt all chapters first. Source, thresholds and prompts stay frozen.",
             "Fixes are deferred until the entire requested workload has been attempted.", ""]
    for record in state["chapters"]:
        if record.get("status") not in {"failed", "interrupted"}:
            continue
        lines.extend(["## " + record["story"] + " / " + record["chapter"], "",
                      "Run: " + str(record.get("run_dir")) + "; exit: " + str(record.get("exit_code")) + ".",
                      "Log: " + str(record.get("log")) + ".", ""])
        lines.append(json.dumps({"error": record.get("error"), "targets": record.get("errors", []),
                                 "missing": record.get("summary", {}).get("incomplete_steps", [])},
                                ensure_ascii=False, indent=2))
        lines.append("")
    temporary = path.with_suffix(".md.pending")
    temporary.write_text("\n".join(lines) + "\n", encoding="utf-8")
    temporary.replace(path)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plan", type=Path)
    parser.add_argument("--state", required=True, type=Path)
    args = parser.parse_args(argv)
    plan = manage.read_json(args.plan)
    config_path = args.plan.parent / "pipeline-v6.json"
    validate_plan(plan, config_path)
    if str(manage.execution_root()) != plan["execution_root"]:
        raise RuntimeError("Execution root differs from the prepared/preflight environment")
    state = manage.read_json(args.state, {"plan": plan, "chapters": [], "created_at": time.time()})
    if state["plan"] != plan:
        raise ValueError("Cannot change a saved v6 batch plan")
    sys.path.insert(0, str(manage.PROJECT / "src"))
    from manga_pipeline.storage.runs import RunStore
    config = manage.configuration(config_path)
    if not config.get("v6", {}).get("enabled") or config.get("device") != "cuda":
        raise ValueError("V6 batch requires the CUDA v6 preset")
    manage.current_python()
    manage.visual_python()
    if not manage.hayai_python().is_file():
        raise RuntimeError("Prepared Hayai CUDA environment is missing")
    doctor = manage.read_json(manage.RUNTIME / "config/last-doctor.json", {})
    if (doctor.get("signature") != manage.dependency_signature()
            or doctor.get("visual_signature") != manage.visual_signature()
            or doctor.get("hayai_python") != str(manage.hayai_python())
            or doctor.get("execution_root") != str(manage.execution_root()) or not doctor.get("inference")):
        raise RuntimeError("A current, authorized CUDA/inference preflight is required")
    env = manage.environment()
    env["MANGA_DEFER_REVIEWS"] = "1"
    with manage.lock("chapter-batch"):
        state.update(status="running", pid=manage.service_identity(os.getpid()),
                     execution_root=str(manage.execution_root()))
        for source in (manage.execution_root() / "envs").glob("*/*"):
            if source.is_file() and source.name in {"requirements.lock", "ready.json"}:
                target = args.state.parent / "environment" / source.parent.name / source.name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
        manage.atomic_json(args.state, state)
        try:
            for index, entry in enumerate(plan["chapters"]):
                if len(state["chapters"]) <= index:
                    state["chapters"].append({"story": entry["story"], "chapter": entry["chapter"]})
                record = state["chapters"][index]
                state["current"] = index
                log = args.state.parent / f"chapter-{index + 1:02d}.log"
                record.update(status="running", phase="ingress", writer_stopped=False,
                              started_at=time.time(), log=str(log))
                manage.atomic_json(args.state, state)
                directory = None
                try:
                    folder = input_folder(entry)
                    directory = latest_run(entry)
                    if directory:
                        saved = manage.read_json(directory / "manifest.json")["config"]
                        for key in ("ollama", "layout", "v6", "ocr", "inpaint", "translation"):
                            if any(saved.get(key, {}).get(k) != v for k, v in config.get(key, {}).items()):
                                raise RuntimeError("Resume config differs; prepare an explicitly authorized revision")
                        command = ["resume", "--run-dir", str(directory)]
                    else:
                        command = ["run", str(folder), "--story", entry["story"], "--chapter", entry["chapter"],
                                   "--config", str(config_path)]
                    record["phase"] = "inference"
                    manage.atomic_json(args.state, state)
                    with log.open("ab") as output:
                        result = subprocess.run([str(manage.current_python()), str(manage.PROJECT / "deployment/runpod/manage.py"),
                                                 *command, "--no-progress"], cwd=manage.PROJECT, env=env,
                                                stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT)
                    record["exit_code"] = result.returncode
                    directory = latest_run(entry)
                    record.update(writer_stopped=True, run_dir=str(directory) if directory else None, phase="review")
                    if directory:
                        with log.open("ab") as output:
                            reviewed = subprocess.run([str(manage.current_python()), str(manage.PROJECT / "run.py"),
                                                       "review", "--run-dir", str(directory), "--no-progress"],
                                                      cwd=manage.PROJECT, env=env, stdin=subprocess.DEVNULL,
                                                      stdout=output, stderr=subprocess.STDOUT)
                        record["review_exit_code"] = reviewed.returncode
                    if result.returncode or not directory or record.get("review_exit_code"):
                        raise RuntimeError("Chapter execution/review failed; preserve its checkpoints")
                    record["phase"] = "verify"
                    record.update(summary=verify_v6(RunStore(directory), entry), status="completed")
                except Exception as exc:
                    record.update(status="failed", error=str(exc), writer_stopped=True,
                                  run_dir=str(directory) if directory else None, errors=errors_for(directory))
                    if directory:
                        try:
                            record["summary"] = verify_v6(RunStore(directory), entry, allow_partial=True)
                        except Exception as integrity:
                            record["integrity_error"] = str(integrity)
                record.update(phase="finished", finished_at=time.time())
                manage.atomic_json(args.state, state)
                write_error_review(args.state.parent / "errors.md", state)
                manage.say(json.dumps(record, ensure_ascii=False))
            failed = any(r["status"] != "completed" for r in state["chapters"])
            state.update(status="completed_with_errors" if failed else "completed", finished_at=time.time())
            manage.atomic_json(args.state, state)
            write_error_review(args.state.parent / "errors.md", state)
            return 2 if failed else 0
        except BaseException as exc:
            state.update(status="interrupted", error=str(exc), updated_at=time.time())
            manage.atomic_json(args.state, state)
            write_error_review(args.state.parent / "errors.md", state)
            raise


if __name__ == "__main__":
    raise SystemExit(main())
