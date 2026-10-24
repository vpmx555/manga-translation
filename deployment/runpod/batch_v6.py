"""Bounded v6 story batches; preserve failures and chapter order within each story."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from threading import Lock, RLock
from contextlib import nullcontext

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
    if (plan.get("pipeline_version") != 6 or not safe_key(plan.get("batch"))
            or plan.get("analysis_contract") != "independent-batches-v6.1"):
        raise ValueError("Invalid v6 batch plan")
    entries = plan.get("chapters", [])
    if plan.get("until") not in {None, 'scan', "analyze", 'build_identity', 'relationship'} or any(e.get("until") != plan.get("until") for e in entries):
        raise ValueError("Invalid or inconsistent frozen stage limit")
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
    # Own the batch before touching its coordinator, receipts or service profile.
    with manage.lock("chapter-batch"):
        return _main(argv)


def _main(argv=None):
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
    from manga_pipeline.storage.checkpoints import publish, archive_files, files_under
    from manga_pipeline.runtime.resources import configure_batch, GPUMonitor, event
    from manga_pipeline.bank.identity import normalize_story_key
    if any(normalize_story_key(entry["story"]) != entry["story_key"] for entry in plan["chapters"]):
        raise ValueError("Plan story keys must match normalized bank ownership")
    config = manage.configuration(config_path)
    if config['runtime']['llm_slots'] > 1:
        from validate_vllm_concurrency import validate_receipt
        validate_receipt(config, manage.read_json(args.state.parent / 'concurrency-probe.json', {}))
    if not config.get("v6", {}).get("enabled") or config.get("device") != "cuda":
        raise ValueError("V6 batch requires the CUDA v6 preset")
    manage.validate_gpu_profile(config)
    manage.current_python()
    manage.visual_python()
    ocr_python = manage.ocr_python(config)
    if not ocr_python.is_file():
        raise RuntimeError(f"Prepared {config['ocr']['backend']} CUDA environment is missing")
    doctor = manage.read_json(manage.RUNTIME / "config/last-doctor.json", {})
    if (doctor.get("signature") != manage.dependency_signature()
            or doctor.get("visual_signature") != manage.visual_signature()
            or doctor.get("ocr_python") != str(ocr_python)
            or doctor.get("ocr_backend") != config['ocr']['backend']
            or doctor.get("execution_root") != str(manage.execution_root()) or not doctor.get("inference")):
        raise RuntimeError("A current, authorized CUDA/inference preflight is required")
    if config.get("llm", {}).get("require_full_gpu"):
        use_vllm = config["llm"].get("backend") == "vllm"
        if use_vllm:
            manage.activate_vllm(config)
            import vllm_runtime
            installed = {"digest": config["llm"]["model"] + "@" + config["vllm"]["revision"]}
            process_path = manage.RUNTIME / "config/vllm-process.json"
            if doctor.get("vllm_signature") != vllm_runtime.signature(config):
                raise RuntimeError("vLLM engine/model revision differs from the authorized preflight")
        if (not doctor.get("full_gpu_verified") or doctor.get("config_fingerprint") != manage.config_fingerprint(config)
                or doctor.get("model_digest") != installed.get("digest")
                or doctor.get("source_bundle") != manage.read_json(manage.PROJECT / "__bundle__.json", {}).get("version")
):
            raise RuntimeError("Full-GPU preflight does not match this model, context, server and frozen config")
    os.environ.update({k: v for k, v in manage.environment().items() if k.startswith("MANGA_LLM_")})
    resource_env = configure_batch(config, args.state.parent)
    os.environ.update(resource_env)
    env = manage.environment()
    env.update(resource_env)
    env["MANGA_DEFER_REVIEWS"] = "1"
    runtime_enabled = bool(config.get("runtime", {}).get("enabled"))
    story_workers = max(1, int(env.get("MANGA_STORY_WORKERS", "1"))) if runtime_enabled else 1
    if runtime_enabled:
        env["MANGA_PARALLEL_BATCH"] = "1"
        os.environ["MANGA_PARALLEL_BATCH"] = "1"
    state_lock = RLock()
    ingress_locks = {entry["input"]: Lock() for entry in plan["chapters"]}
    pipeline_guard = manage.parallel_service_guard(config) if runtime_enabled else nullcontext()
    with pipeline_guard, GPUMonitor(config) as monitor:
        state.update(status="running", pid=manage.service_identity(os.getpid()),
                     execution_root=str(manage.execution_root()))
        for source in (manage.execution_root() / "envs").glob("*/*"):
            if source.is_file() and source.name in {"requirements.lock", "ready.json"}:
                target = args.state.parent / "environment" / source.parent.name / source.name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
        manage.atomic_json(args.state, state)
        try:
            if len(state["chapters"]) > len(plan["chapters"]):
                raise ValueError("Saved batch has more chapter records than its plan")
            for index, entry in enumerate(plan["chapters"]):
                if len(state["chapters"]) <= index:
                    state["chapters"].append({"story": entry["story"], "chapter": entry["chapter"]})
                if (state["chapters"][index].get("story") != entry["story"]
                        or state["chapters"][index].get("chapter") != entry["chapter"]):
                    raise ValueError("Saved chapter record differs from its plan position")
            state.update(story_workers=story_workers, active_chapters=[])
            manage.atomic_json(args.state, state)

            def run_entry(index, entry):
                # Private record snapshots keep serialization independent of worker mutations.
                record = deepcopy(state["chapters"][index])

                def save_record(finished=False):
                    with state_lock:
                        state["chapters"][index] = deepcopy(record)
                        active = set(state["active_chapters"])
                        if finished:
                            active.discard(index)
                        else:
                            active.add(index)
                        state["active_chapters"] = sorted(active)
                        state["current"] = index
                        manage.atomic_json(args.state, state)

                log = args.state.parent / f"chapter-{index + 1:02d}.log"
                for key in ("error", "errors", "integrity_error", "publication_error", "review_exit_code",
                            "summary", "publication", "exit_code", "finished_at"):
                    record.pop(key, None)
                record.update(status="running", phase="ingress", writer_stopped=False,
                              started_at=time.time(), log=str(log))
                save_record()
                directory = None
                try:
                    if plan.get('native_sync'):
                        from local_v6 import ingest_chapter
                        ingress_jobs = args.state.parent / "ingress" / entry["story_key"]
                        ingress_jobs.mkdir(parents=True, exist_ok=True)
                        with ingress_locks[entry["input"]]:
                            ingest_chapter(plan, entry, ingress_jobs)
                    folder = input_folder(entry)
                    directory = latest_run(entry)
                    if plan.get('preanalyze_import'):
                        selected = plan['preanalyze_import']['runs'][entry['story_key'] + '/' + entry['chapter']]
                        imported = manage.RUNTIME / 'data/imports' / plan['batch'] / selected
                        origin = manage.read_json(directory / 'manifest.json').get('preanalyze_import', {}) if directory else {}
                        if origin.get('source') != str(imported):
                            with log.open('ab') as output:
                                subprocess.run([str(manage.current_python()), '-B',
                                    str(manage.PROJECT / 'deployment/runpod/preanalyze_import.py'),
                                    '--source', str(imported), '--config', str(config_path),
                                    '--through', plan['preanalyze_import']['reuse'][-1]],
                                    cwd=manage.PROJECT, env=env, check=True,
                                    stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT)
                            directory = latest_run(entry)
                    if directory:
                        saved = manage.read_json(directory / "manifest.json")["config"]
                        changed = any(any(saved.get(key, {}).get(k) != v for k, v in config.get(key, {}).items())
                                      for key in ("llm", "vllm", "layout", "v6", "ocr", "inpaint", "translation", "runtime"))
                        if changed:
                            if not plan.get("reanalyze_existing"):
                                raise RuntimeError("Resume config differs; prepare an explicitly authorized revision")
                            command = ["reanalyze", "--run-dir", str(directory), "--config", str(config_path)]
                        else:
                            command = ["resume", "--run-dir", str(directory), "--config", str(config_path)]
                    else:
                        command = ["run", str(folder), "--story", entry["story"], "--chapter", entry["chapter"],
                                   "--config", str(config_path)]
                    if entry.get("until"):
                        command.extend(["--until", entry["until"]])
                    record["phase"] = "inference"
                    save_record()
                    chapter_env = dict(env)
                    if runtime_enabled:
                        chapter_env["MANGA_STORY_KEY"] = entry["story_key"]
                    started = time.monotonic()
                    with log.open("ab") as output:
                        result = subprocess.run([str(manage.current_python()), str(manage.PROJECT / "deployment/runpod/manage.py"),
                                                 *command, "--no-progress"], cwd=manage.PROJECT, env=chapter_env,
                                                stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT)
                    event("chapter_inference", story=entry["story_key"], chapter=entry["chapter"],
                          wall_seconds=time.monotonic()-started, exit_code=result.returncode)
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
                    summary = verify_v6(RunStore(directory), entry)
                    record.update(summary=summary, status="completed_with_review" if summary.get("semantic_unresolved")
                                  or summary.get("typeset_needs_review") or summary.get('identity_needs_review') else "completed")
                except Exception as exc:
                    record.update(status="failed", error=str(exc), writer_stopped=True,
                                  run_dir=str(directory) if directory else None, errors=errors_for(directory))
                    if manage.read_json(manage.RUNTIME / "config/llm-inference-pause.json", {}).get("status") == "paused":
                        record["status"] = "paused"
                    if directory:
                        try:
                            record["summary"] = verify_v6(RunStore(directory), entry, allow_partial=True)
                        except Exception as integrity:
                            record["integrity_error"] = str(integrity)
                record.update(phase="finished", finished_at=time.time())
                if os.environ.get("MANGA_DURABLE_ROOT") and directory:
                    try:
                        record["publication"] = publish(manage.RUNTIME,
                            Path(os.environ["MANGA_DURABLE_ROOT"]) / plan["batch"],
                            "outputs", story=entry["story_key"], chapter=entry["chapter"])
                    except Exception as exc:
                        record.update(status="failed", publication_error=str(exc))
                save_record(finished=True)
                with state_lock:
                    write_error_review(args.state.parent / "errors.md", state)
                    manage.say(json.dumps(record, ensure_ascii=False))
                if record["status"] == "paused":
                    raise RuntimeError("Inference paused with uncertain server work; later chapters were not started")

            stories = {}
            for index, entry in enumerate(plan["chapters"]):
                stories.setdefault(entry["story_key"], []).append((index, entry))

            def run_story(entries):
                for index, entry in entries:
                    run_entry(index, entry)

            if story_workers == 1:
                # Complete a chapter before moving to another story; preserve each
                # story's chapter/bank order while avoiding story-level starvation.
                state["schedule"] = "chapter-round-robin-v1"
                for turn in range(max(map(len, stories.values()), default=0)):
                    for entries in stories.values():
                        if turn < len(entries):
                            run_entry(*entries[turn])
            else:
                # Legacy parallel profiles retain one writer per story.
                with ThreadPoolExecutor(max_workers=min(story_workers, len(stories)),
                                        thread_name_prefix="v6-story") as executor:
                    futures = [executor.submit(run_story, entries) for entries in stories.values()]
                    for future in as_completed(futures):
                        future.result()
            failed = any(r["status"] not in {"completed", "completed_with_review"} for r in state["chapters"])
            reviews = any(r["status"] == "completed_with_review" for r in state["chapters"])
            state.update(status="completed_with_errors" if failed else "completed_with_review" if reviews else "completed", finished_at=time.time())
            manage.atomic_json(args.state, state)
            write_error_review(args.state.parent / "errors.md", state)
            if config.get("llm", {}).get("backend") == "vllm":
                vllm_runtime.stop()
            monitor.__exit__(None, None, None)
            if runtime_enabled:
                manage.atomic_json(args.state.parent / "runtime/final-gpu-state.json",
                                   manage.read_json(Path(env["MANGA_GPU_COORDINATOR"]) / "gpu-state.json"))
            if os.environ.get("MANGA_DURABLE_ROOT"):
                durable = Path(os.environ["MANGA_DURABLE_ROOT"]) / plan["batch"]
                state["bank_publication"] = publish(manage.RUNTIME, durable, "banks")
                audit = durable / "published/audit"
                state["audit_publication"] = archive_files(files_under(args.state.parent),
                    manage.RUNTIME, audit, audit / "ready.json")
                manage.atomic_json(args.state, state)
            return 2 if failed else 0
        except BaseException as exc:
            paused = manage.read_json(manage.RUNTIME / "config/llm-inference-pause.json", {}).get("status") == "paused"
            state.update(status="paused" if paused else "interrupted", error=str(exc), updated_at=time.time())
            manage.atomic_json(args.state, state)
            write_error_review(args.state.parent / "errors.md", state)
            raise


if __name__ == "__main__":
    raise SystemExit(main())
