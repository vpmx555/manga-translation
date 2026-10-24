"""OCR-only diagnostic lane using the canonical local working-set/native transport."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import traceback
import zipfile

import manage
import native_transport as transport

ROOT = transport.PROJECT
KIND = "ocr-diagnostic"
MODEL = "zai-org/GLM-OCR"
REVISION = "2e85a62840ccac27daa451df36c736c4636b8628"
IMAGE = "runpod/pytorch:1.0.2-cu1281-torch280-ubuntu2404@sha256:0a360022e8de4375af99430f84e8b38951acc397252163a37ceac7204d01be35"


def info(path):
    return {"bytes": path.stat().st_size, "sha256": manage.file_hash(path)}


def unpack(path, destination):
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path) as archive:
        if archive.testzip():
            raise ValueError("Archive CRC mismatch")
        for member in archive.infolist():
            target = (destination / member.filename).resolve()
            target.relative_to(destination.resolve())
            if (member.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError("Archive symlinks are not allowed")
        archive.extractall(destination)


def prepare(args):
    from PIL import Image
    import numpy as np
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,100}", args.batch):
        raise ValueError("Invalid diagnostic batch ID")
    destination = transport.STATE / "v6" / args.batch
    if destination.exists():
        raise ValueError("Batch already exists; freeze a new batch")
    baseline = manage.read_json(args.baseline / "result.json")
    mobile = manage.read_json(args.mobile / "result.json")
    medium = manage.read_json(args.medium / "result.json")
    for report in (baseline, mobile, medium):
        if report["status"] != "completed" or report["completed"] != report["targets"]:
            raise ValueError("Comparison baseline is not complete")
    contract = manage.read_json(args.baseline / "request.json")
    source = Path(contract["source_run"])
    extraction = next(source.glob("[0-9][0-9]_extract/result.json"))
    if manage.file_hash(extraction) != contract["extract_hash"]:
        raise ValueError("Original extraction changed")
    old_mobile = {row["id"]: row for row in mobile["results"]}
    old_medium = {row["id"]: row for row in medium["results"]}
    rows = []
    for old in baseline["results"]:
        with Image.open(args.baseline / old["crop"]) as crop_image:
            actual_pixels = np.asarray(crop_image.convert("RGB"))
        if hashlib.sha256(actual_pixels.tobytes()).hexdigest() != old["crop_sha256"]:
            raise ValueError("Baseline crop pixels changed: " + old["id"])
        for index in (old_mobile, old_medium):
            if index[old["id"]]["crop_sha256"] != old["crop_sha256"] or index[old["id"]]["bbox"] != old["bbox"]:
                raise ValueError("Baselines use different crops")
        rows.append({**{key: old[key] for key in ("id", "page", "text_index", "bbox", "crop", "crop_sha256", "is_essential_text")},
                     "file_sha256": manage.file_hash(args.baseline / old["crop"]),
                     "hayai_text": old["new_text"],
                     "paddle_mobile_text": old_mobile[old["id"]]["paddle_text"],
                     "paddle_medium_text": old_medium[old["id"]]["paddle_text"]})
    resolver = manage.read_json(args.resolver)
    lock = []
    for package in resolver["install"]:
        digest = package["download_info"]["archive_info"]["hashes"]["sha256"]
        lock.append(f'{package["metadata"]["name"]}=={package["metadata"]["version"]} --hash=sha256:{digest}')
    if not any(line.startswith("transformers==5.20.2 ") for line in lock):
        raise ValueError("Missing frozen Transformers dependency")
    destination.mkdir(parents=True)
    (destination / "requirements.lock").write_text("\n".join(sorted(lock)) + "\n", encoding="utf-8")
    # Native Syncthing ignores *.lock. Transfer the frozen lock inside an archive,
    # like the canonical v6 dependency lane, rather than relying on a manual copy.
    with zipfile.ZipFile(destination / "locks.zip", "w", compression=zipfile.ZIP_STORED) as archive:
        archive.write(destination / "requirements.lock", "requirements.lock")
    config = {"backend": "glm-ocr", "engine": "transformers", "model": MODEL, "revision": REVISION,
              "prompt": "Text Recognition:", "batch_size": 8, "max_new_tokens": 2048,
              "dtype": "bfloat16", "attention": "sdpa", "do_sample": False}
    manage.atomic_json(destination / "ocr-config.json", config)
    with zipfile.ZipFile(destination / "payload.zip", "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("targets.json", json.dumps(rows, ensure_ascii=False))
        archive.writestr("document.json", (args.baseline / "ocr-result.json").read_bytes())
        for row in rows:
            archive.write(args.baseline / row["crop"], row["crop"])
    bundle = transport.bundle(destination / "source.zip")
    plan = {"kind": KIND, "batch": args.batch, "pipeline_version": 6,
            "local_working_set": True, "native_sync": True,
            "native_ingress": "/opt/manga-work/ingress/" + args.batch,
            "runtime": "/opt/manga-work/runtime", "execution_root": "/opt/manga-work/execution",
            "project": "/opt/manga-work/project", "durable_root": "/workspace/manga-runtime/local-v6",
            "boot_image": IMAGE, "min_cuda_version": "12.8", "base_torch": "2.8.0",
            "python": "3.12", "source_bundle_hash": bundle["version"],
            "archives": {name: info(destination / name) for name in
                         ("source.zip", "payload.zip", "locks.zip", "ocr-config.json")},
            "dependency_lock_sha256": manage.file_hash(destination / "requirements.lock"),
            "pages": baseline["pages"], "targets": len(rows), "config": config,
            "source_extract_sha256": contract["extract_hash"],
            "baseline_hashes": {str(path): manage.file_hash(path / "result.json")
                                for path in (args.baseline, args.mobile, args.medium)},
            "chapters": [{"story": "iris-zero", "chapter": "chapter-001", "pages": baseline["pages"]}]}
    manage.atomic_json(destination / "plan.json", plan)
    import native_sync
    native_sync.prepare(destination / "plan.json")
    print(json.dumps({"status": "prepared_offline", "plan": str(destination / "plan.json"),
                      "targets": len(rows), "payload_bytes": plan["archives"]["payload.zip"]["bytes"]}), flush=True)


def prepared(path):
    plan = manage.read_json(path)
    if plan.get("kind") != KIND:
        raise ValueError("Expected OCR diagnostic plan")
    for name, record in plan["archives"].items():
        if info(path.parent / name) != record:
            raise ValueError("Frozen diagnostic artifact changed: " + name)
    # Verify the frozen bundle manifest against the current source before launch.
    with zipfile.ZipFile(path.parent / "source.zip") as archive:
        bundle = json.loads(archive.read("__bundle__.json"))
    for name, expected in bundle["files"].items():
        if manage.file_hash(ROOT / name) != expected:
            raise ValueError("Source changed after prepare; freeze a new diagnostic batch")
    return plan


def launch(args):
    plan = prepared(args.plan.resolve())
    from native_prepare import connection_for
    connection = connection_for(args.pod_id)
    import native_sync
    native_sync.activate(args.plan.resolve(), connection)
    staging = plan["durable_root"] + "/" + plan["batch"] + "/ingress"
    transport.remote(connection, ["mkdir", "-p", staging])
    for name in ("ocr_diagnostic.py", "manage.py", "native_transport.py"):
        transport.upload(connection, Path(__file__).parent / name, staging + "/" + name)
    transport.upload(connection, args.plan, staging + "/plan.json")
    script = '''import json,os,pathlib,subprocess,sys,time
root=pathlib.Path(sys.argv[1]);plan=json.loads((root/'plan.json').read_text())
local=pathlib.Path(plan['runtime'])/'data'/'ocr-jobs'/plan['batch'];local.mkdir(parents=True,exist_ok=True)
pidfile=local/'launch.json'
if pidfile.exists():
 old=json.loads(pidfile.read_text())
 try:os.kill(old['pid'],0)
 except ProcessLookupError:pass
 else:raise RuntimeError('OCR diagnostic already active')
env=dict(os.environ,MANGA_RUNTIME=plan['runtime'],MANGA_EXECUTION_ROOT=plan['execution_root'])
with (local/'bootstrap.log').open('ab') as log:
 child=subprocess.Popen(['python3.12',str(root/'ocr_diagnostic.py'),'ocr-remote','--plan',str(root/'plan.json')],env=env,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
record={'pid':child.pid,'started_at':time.time(),'batch':plan['batch']};pidfile.write_text(json.dumps(record));print(json.dumps(record))'''
    result = transport.remote(connection, ["python3", "-c", script, staging], capture=True)
    manage.atomic_json(args.plan.parent / "bound-plan.json", {**plan, "pod_id": args.pod_id})
    with (args.plan.parent / "native-verifier.log").open("ab") as log:
        subprocess.Popen([sys.executable, str(Path(__file__).parent / "native_verify.py"),
                          "--plan", str(args.plan.resolve()), "--wait"], stdin=subprocess.DEVNULL,
                         stdout=log, stderr=subprocess.STDOUT,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "DETACHED_PROCESS", 0))
    print(result, flush=True)


def remote(args):
    plan = manage.read_json(args.plan)
    os.environ.update(MANGA_RUNTIME=plan["runtime"], MANGA_EXECUTION_ROOT=plan["execution_root"],
                      HF_HOME=plan["runtime"] + "/models/huggingface", TOKENIZERS_PARALLELISM="false")
    jobs = Path(plan["runtime"]) / "data/ocr-jobs" / plan["batch"]
    jobs.mkdir(parents=True, exist_ok=True)
    durable = Path(plan["durable_root"]) / plan["batch"]
    state_path = durable / "jobs/state.json"
    state = {"kind": KIND, "batch": plan["batch"], "status": "bootstrap", "writer_stopped": False,
             "last_progress_at": time.time()}
    manage.atomic_json(state_path, state)
    output = Path(plan["runtime"]) / "data/ocr" / plan["batch"]
    output.mkdir(parents=True, exist_ok=True)
    try:
        working = Path("/opt/manga-work")
        working.mkdir(exist_ok=True)
        filesystem = subprocess.check_output(["findmnt", "-T", str(working), "-no", "FSTYPE"], text=True).strip()
        if any(name in filesystem.lower() for name in ("fuse", "nfs", "cifs", "9p")):
            raise RuntimeError("OCR working set is not on a verified local filesystem")
        if shutil.disk_usage(working).free < 35 * 1024**3:
            raise RuntimeError("Insufficient local disk headroom")
        ingress = Path(plan["native_ingress"])
        deadline = time.monotonic() + 600
        for name, record in plan["archives"].items():
            path = ingress / name
            while not path.exists() or path.stat().st_size != record["bytes"]:
                if time.monotonic() > deadline:
                    raise TimeoutError("Native ingress did not complete: " + name)
                time.sleep(2)
            if info(path) != record:
                raise ValueError("Native ingress checksum mismatch: " + name)
        project = Path(plan["project"])
        unpack(ingress / "source.zip", project)
        with zipfile.ZipFile(ingress / "source.zip") as archive:
            manifest = json.loads(archive.read("__bundle__.json"))
        for name, expected in manifest["files"].items():
            if manage.file_hash(project / name) != expected:
                raise ValueError("Published source checksum mismatch: " + name)
        inputs = Path(plan["runtime"]) / "input" / plan["batch"]
        unpack(ingress / "payload.zip", inputs)
        execution = Path(plan["execution_root"]) / "glm-ocr"
        lock_file = ingress / "requirements.lock"
        if "locks.zip" in plan["archives"]:
            lock_dir = Path(plan["execution_root"]) / "glm-ocr-locks"
            unpack(ingress / "locks.zip", lock_dir)
            lock_file = lock_dir / "requirements.lock"
            if manage.file_hash(lock_file) != plan["dependency_lock_sha256"]:
                raise ValueError("Frozen dependency lock changed after archive ingress")
        subprocess.run(["python3.12", "-m", "venv", "--system-site-packages", str(execution)], check=True)
        python = execution / "bin/python"
        subprocess.run([str(python), "-m", "pip", "install", "--disable-pip-version-check", "--no-deps",
                        "--require-hashes", "-r", str(lock_file)], check=True)
        manage.atomic_json(output / "deployment.json", {"plan": plan, "filesystem": filesystem,
                                                        "lock_sha256": manage.file_hash(lock_file)})
        state.update(status="inference", last_progress_at=time.time())
        manage.atomic_json(state_path, state)
        subprocess.run([str(python), "-B", str(project / "scripts/diagnostics/run_glm_ocr.py"),
                        "--input", str(inputs), "--output", str(output),
                        "--config", str(ingress / "ocr-config.json")], check=True)
        result = manage.read_json(output / "result.json")
        state.update(status=result["status"], coverage={key: result[key] for key in
                     ("targets", "completed", "succeeded", "failed", "empty", "truncated")})
    except Exception as exc:
        state.update(status="failed", error=type(exc).__name__ + ": " + str(exc))
        (output / "bootstrap-failure.log").write_text(traceback.format_exc(), encoding="utf-8")
    # Retain both successful and failed artifacts; never require perfect coverage to rescue data.
    shutil.copyfile(jobs / "bootstrap.log", output / "bootstrap.log")
    inventory = {path.relative_to(output).as_posix(): info(path) for path in sorted(output.rglob("*"))
                 if path.is_file() and path.name != "inventory.json" and not path.name.endswith(".tmp")}
    manage.atomic_json(output / "inventory.json", inventory)
    publication = durable / "published/ocr"
    publication.mkdir(parents=True, exist_ok=True)
    pending = publication / "results.zip.pending"
    with zipfile.ZipFile(pending, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
        for name in [*inventory, "inventory.json"]:
            archive.write(output / name, name)
    archive = publication / "results.zip"
    os.replace(pending, archive)
    ready = {"archive": str(archive), "archive_bytes": archive.stat().st_size,
             "archive_sha256": manage.file_hash(archive), "files": len(inventory) + 1}
    manage.atomic_json(publication / "ready.json", ready)
    state.update(writer_stopped=True, publication=ready, last_progress_at=time.time())
    manage.atomic_json(state_path, state)


def verify(path):
    plan = manage.read_json(path)
    receipt = manage.read_json(path.parent / "native-sync.json")
    destination = Path(receipt["destination"])
    durable = destination / "durable" / plan["batch"]
    state = manage.read_json(durable / "jobs/state.json", {})
    if not state.get("writer_stopped") or not state.get("publication"):
        return None
    ready = state["publication"]
    archive = durable / "published/ocr/results.zip"
    if not archive.is_file() or archive.stat().st_size != ready["archive_bytes"] or manage.file_hash(archive) != ready["archive_sha256"]:
        return None
    with zipfile.ZipFile(archive) as zipped:
        if zipped.testzip():
            raise ValueError("Diagnostic archive CRC mismatch")
        inventory = json.loads(zipped.read("inventory.json"))
        if set(zipped.namelist()) != set(inventory) | {"inventory.json"}:
            raise ValueError("Diagnostic archive inventory differs")
        for name, record in inventory.items():
            content = zipped.read(name)
            if len(content) != record["bytes"] or hashlib.sha256(content).hexdigest() != record["sha256"]:
                raise ValueError("Diagnostic archive file checksum mismatch")
        if state["status"] in {"completed", "completed_with_errors"}:
            result = json.loads(zipped.read("result.json"))
            with zipfile.ZipFile(path.parent / "payload.zip") as payload:
                targets = json.loads(payload.read("targets.json"))
                original = json.loads(payload.read("document.json"))
            expected = {row["id"]: row for row in targets}
            actual = result["results"]
            if len(actual) != plan["targets"] or len({row["id"] for row in actual}) != len(actual):
                raise ValueError("Diagnostic target coverage or IDs differ")
            if result["completed"] != plan["targets"] or result["targets"] != plan["targets"]:
                raise ValueError("Diagnostic success status has incomplete coverage")
            for row in actual:
                reference = expected[row["id"]]
                if any(row[key] != reference[key] for key in ("page", "text_index", "bbox", "crop_sha256", "file_sha256")):
                    raise ValueError("Diagnostic crop/ID mapping differs")
            updated = json.loads(zipped.read("ocr-result.json"))
            texts = {row["id"]: row for row in actual}
            if any(row["text"] != texts[row["id"]].get("glm_text", "") for row in updated["texts"]):
                raise ValueError("GLM text is mapped to the wrong output target")
            for document in (original, updated):
                document.pop("ocr", None)
                document.pop("diagnostic", None)
                for row in document["texts"]:
                    row.pop("text", None)
            if original != updated:
                raise ValueError("Non-OCR document metadata changed")
            if not json.loads(zipped.read("environment.json"))["cuda_matmul_verified"]:
                raise ValueError("Missing real CUDA proof")
    unpack(archive, destination / "review")
    return {"status": "replica_archives_verified", "kind": KIND, "batch": plan["batch"],
            "verified_at": time.time(), "pipeline_status": state["status"], "coverage": state.get("coverage"),
            "writer_stopped": True, "native_transfer": True,
            "archives": [{"archive": str(archive), "sha256": ready["archive_sha256"], "bytes": ready["archive_bytes"]}],
            "chapter_statuses": [{"story": "iris-zero", "chapter": "chapter-001", "status": state["status"]}]}


def main():
    parser = argparse.ArgumentParser(__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prep = commands.add_parser("ocr-prepare")
    for name in ("baseline", "mobile", "medium", "resolver"):
        prep.add_argument("--" + name, type=Path, required=True)
    prep.add_argument("--batch", required=True)
    for name in ("ocr-launch", "ocr-remote", "ocr-watch"):
        sub = commands.add_parser(name)
        sub.add_argument("--plan", type=Path, required=True)
        if name == "ocr-launch":
            sub.add_argument("--pod-id", required=True)
    args = parser.parse_args()
    if args.command == "ocr-prepare":
        return prepare(args)
    if args.command == "ocr-launch":
        return launch(args)
    if args.command == "ocr-remote":
        return remote(args)
    from native_prepare import connection_for
    plan = manage.read_json(args.plan)
    bound = manage.read_json(args.plan.parent / "bound-plan.json")
    connection = connection_for(bound["pod_id"])
    script = "import json,pathlib,sys;p=pathlib.Path(sys.argv[1]);q=pathlib.Path(sys.argv[2]);r=json.loads(p.read_text()) if p.exists() else (json.loads(q.read_text()) if q.exists() else {});keys=['status','targets','completed','succeeded','failed','empty','truncated','elapsed_seconds','last_progress_at','error','writer_stopped'];print(json.dumps({k:r[k] for k in keys if k in r}))"
    print(transport.remote(connection, ["python3", "-c", script,
          plan["runtime"] + "/data/ocr/" + plan["batch"] + "/result.json",
          plan["durable_root"] + "/" + plan["batch"] + "/jobs/state.json"], capture=True))


if __name__ == "__main__":
    main()
