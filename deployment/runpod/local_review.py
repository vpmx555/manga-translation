"""Local v6 review generation and synchronization watcher; never runs inference."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT / "src"))
from manga_pipeline.review.v6 import V6Review
from manga_pipeline.storage.io import atomic_json, atomic_bytes, fingerprint, read_json
from manga_pipeline.storage.locking import exclusive_lock

DEFAULT_BATCH = PROJECT / "outputs/runpod-v6-nine-stories-idfix-20261013"
VERSION = "v7-local-review-identity-html-v1"


def _stat(path):
    try:
        stat = path.stat()
        return [stat.st_mtime_ns, stat.st_size]
    except FileNotFoundError:
        return None


def source_folders(batch, plan_path=None):
    result = {}
    plans = sorted((batch / "durable").glob("*/ingress/plan.json"))
    if plan_path:
        plans.append(Path(plan_path))
    for path in plans:
        plan = read_json(path)
        for entry in plan.get("chapters", []):
            if entry.get("source_folder"):
                result[(entry.get("story_key", entry.get("story")), entry.get("chapter"))] = entry["source_folder"]
    return result


def outputs_root(batch):
    return batch / "live/outputs" if (batch / "live/outputs").is_dir() else batch / "outputs"


def run_paths(batch):
    base = outputs_root(batch)
    if not base.is_dir():
        return []
    return sorted(base.glob("*/*/*/manifest.json"))


def stamp(directory, source_folder, bank):
    files = [directory / "manifest.json", *directory.glob("[0-9][0-9]_*/result.json"), bank / "metadata.json"]
    # Track readable previews arriving after JSON, without scanning cache/masks.
    files.extend(directory.glob("[0-9][0-9]_*/overlays/*.png"))
    files.extend(directory.glob("[0-9][0-9]_*/visualizations/*.png"))
    files.extend(directory.glob("[0-9][0-9]_typeset/pages/page-*/*/rendered.png"))
    files.extend(directory.glob("[0-9][0-9]_inpaint/pages/**/*.png"))
    files.extend(directory.glob('[0-9][0-9]_build_identity/name-binding/result.json'))
    files.extend(directory.glob('[0-9][0-9]_build_identity/name-binding/reference-crops/*.png'))
    files.extend(directory.glob('[0-9][0-9]_build_identity/reference-crops/*.png'))
    files.extend(directory.glob('[0-9][0-9]_relationship/reference-crops/*.png'))
    if bank.is_dir():
        files.extend(bank.glob("characters/*/*.png"))
    return fingerprint([VERSION, source_folder, [(str(f), _stat(f)) for f in sorted(set(files))]])


def generate(batch, *, plan_path=None):
    batch = Path(batch).resolve()
    output = batch / "local-reviews"
    output.mkdir(parents=True, exist_ok=True)
    receipt = output / "review-state.json"
    try:
        saved = read_json(receipt) if receipt.is_file() else {}
    except (OSError, ValueError):
        saved = {}
    sources = source_folders(batch, plan_path)
    previous = saved.get("runs", {})
    runs, pending, errors = {}, [], []
    changed = 0
    renderer_hash = hashlib.sha256((PROJECT / "src/manga_pipeline/review/v6.py").read_bytes()
                                  + (PROJECT / 'deployment/runpod/identity_review.py').read_bytes()
                                  + (PROJECT / 'deployment/runpod/identity_failure_review.py').read_bytes()).hexdigest()
    for path in run_paths(batch):
        directory = path.parent
        relative = directory.relative_to(outputs_root(batch))
        destination = output / relative
        key = relative.as_posix()
        try:
            manifest = read_json(path)
            if manifest.get("pipeline_version") != 6:
                continue
            story, chapter = relative.parts[:2]
            source = sources.get((story, chapter))
            bank_root = outputs_root(batch).parent / "banks"
            bank = bank_root / story
            signature = fingerprint([renderer_hash, stamp(directory, source, bank)])
            record = previous.get(key, {})
            complete_reviews = ((destination / "review.md").is_file()
                and len(list(destination.glob("[0-9][0-9]_*/review.md"))) == record.get('stage_count')
                and (destination / 'identity-relationship/review.md').is_file()
                and (destination / 'identity-relationship/build_identity.html').is_file()
                and (destination / 'identity-relationship/relationship.html').is_file())
            if record.get("signature") == signature and complete_reviews:
                runs[key] = record
                continue
            reviewer = V6Review(directory, source_folder=source, bank_root=bank_root)
            target = reviewer.write(destination)
            from identity_review import generate_identity_reviews
            identity_report = generate_identity_reviews(reviewer, destination / 'identity-relationship')
            # Recheck input snapshot after rendering to avoid publishing a mixed sync revision.
            if fingerprint([renderer_hash, stamp(directory, source, bank)]) != signature:
                pending.append({"run": key, "reason": "Replica changed while rendering; retry next poll"})
                continue
            runs[key] = {"signature": signature, "review": str(target), "updated_at": time.time(),
                         "pipeline_status": manifest.get("status"), "artifact_stages": len(reviewer.data),
                         "model": manifest.get("config", {}).get("llm", {}).get("model"),
                         'stage_count': len(reviewer.steps), 'identity_relationship': identity_report,
                         "analyze_policy": reviewer.data.get("analyze", {}).get("policy")}
            changed += 1
        except (FileNotFoundError, json.JSONDecodeError, ValueError) as exc:
            pending.append({"run": key, "reason": str(exc)})
            if key in previous:
                runs[key] = previous[key]
        except Exception as exc:
            errors.append({"run": key, "error": str(exc)})
            if key in previous:
                runs[key] = previous[key]
    index = ["# Review local — pipeline v7 và identity/relationship", "",
             "Tự tạo từ bản sao JSON đã đồng bộ. Không inference, không thay đổi manifest/bank hoặc dữ liệu trong live/durable.", "",
             "| Truyện / chapter / run | Trạng thái pipeline | Stage có dữ liệu | Review | Identity / relationship |",
             "| --- | --- | --- | --- | --- |"]
    for key, item in sorted(runs.items()):
        index.append(f"| {key} | {item['pipeline_status']} | {item['artifact_stages']}/{item.get('stage_count', '?')} | "
                     f"[Mở]({key}/review.md) | [HTML]({key}/identity-relationship/index.html) · "
                     f"[Markdown]({key}/identity-relationship/review.md) |")
    if pending or errors:
        index.extend(["", f"Đang chờ đồng bộ/ổn định: {len(pending)} run; lỗi renderer: {len(errors)}. Xem review-state.json."])
    atomic_bytes(output / "review.md", ("\n".join(index)+"\n").encode("utf-8"))
    report = {"version": VERSION, "checked_at": time.time(), "runs": runs, "pending": pending, "errors": errors,
              "generated": changed, "review": str(output / "review.md")}
    atomic_json(receipt, report)
    return report


def watch(batch, interval):
    batch.mkdir(parents=True, exist_ok=True)
    with exclusive_lock(batch / ".local-review-worker.lock", timeout=1):
        atomic_json(batch / "local-review-worker.json", {"pid": os.getpid(), "started_at": time.time(),
                    "batch": str(batch), "interval_seconds": interval, "status": "watching",
                    "launch_token": os.environ.get("MANGA_LOCAL_REVIEW_LAUNCH_TOKEN")})
        previous = None
        while not (batch / "local-review-stop").exists():
            try:
                report = generate(batch)
                state = {"runs": len(report["runs"]), "pending": len(report["pending"]), "errors": len(report["errors"])}
                if report["generated"] or state != previous:
                    print(json.dumps({**state, "generated": report["generated"], "time": time.time()}, ensure_ascii=False), flush=True)
                    previous = state
            except Exception as exc:
                print(json.dumps({"watch_error": str(exc), "time": time.time()}, ensure_ascii=False), flush=True)
            time.sleep(interval)
        atomic_json(batch / "local-review-worker.json", {"pid": os.getpid(), "status": "stopped", "time": time.time()})


def install_startup(batch):
    if os.name != "nt":
        return {"registered": False, "reason": "Windows logon registration only"}
    import winreg
    executable = PROJECT / "venv/Scripts/pythonw.exe"
    if not executable.is_file():
        raise RuntimeError("pythonw.exe is required for a hidden logon watcher")
    name = "MangaV6Review-" + hashlib.sha256(str(batch).encode()).hexdigest()[:16]
    command = subprocess.list2cmdline([str(executable), "-B", str(Path(__file__).resolve()), "--batch", str(batch), "--watch"])
    location = r"Software\Microsoft\Windows\CurrentVersion\Run"
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, location) as key:
        winreg.SetValueEx(key, name, 0, winreg.REG_SZ, command)
    return {"registered": True, "registry": "HKCU\\" + location, "name": name}


def start_watch(batch, *, install_logon=False):
    batch = Path(batch).resolve()
    batch.mkdir(parents=True, exist_ok=True)
    registration = install_startup(batch) if install_logon else None
    with exclusive_lock(batch / ".local-review-start.lock"):
        try:
            with exclusive_lock(batch / ".local-review-worker.lock", timeout=0.2):
                pass
        except TimeoutError:
            return {"status": "already_running", "startup": registration}
        if (batch / "local-review-stop").exists():
            (batch / "local-review-stop").unlink()
        log_path = batch / "local-review-watcher.log"
        token = secrets.token_hex(16)
        env = dict(os.environ, MANGA_LOCAL_REVIEW_LAUNCH_TOKEN=token)
        with log_path.open("ab") as log:
            child = subprocess.Popen([sys.executable, "-B", str(Path(__file__).resolve()), "--batch", str(batch), "--watch"],
                cwd=PROJECT, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                creationflags=(subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS) if os.name == "nt" else 0,
                start_new_session=os.name != "nt")
        for _ in range(300):
            if child.poll() is not None:
                raise RuntimeError(f"Local review watcher exited ({child.returncode}); see {log_path}")
            state_path = batch / "local-review-worker.json"
            if state_path.is_file():
                state = read_json(state_path)
                # Windows venv redirectors can have a different PID from the worker.
                if (state.get("launch_token") == token and state.get("batch") == str(batch)
                        and state.get("status") == "watching" and type(state.get("pid")) is int):
                    return {"status": "started", "pid": state["pid"], "launcher_pid": child.pid,
                            "startup": registration, "log": str(log_path)}
            time.sleep(0.1)
        raise RuntimeError("Local review watcher startup timed out; inspect worker/log before retrying")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", type=Path, default=DEFAULT_BATCH)
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--start", action="store_true")
    parser.add_argument("--install-startup", action="store_true")
    parser.add_argument("--interval", type=int, default=30)
    args = parser.parse_args()
    if args.interval < 10:
        parser.error("Polling interval must be at least 10 seconds")
    batch = args.batch.resolve()
    if args.start:
        print(json.dumps(start_watch(batch, install_logon=args.install_startup), ensure_ascii=False))
    elif args.watch:
        watch(batch, args.interval)
    else:
        report = generate(batch)
        print(json.dumps({"runs": len(report["runs"]), "generated": report["generated"], "pending": len(report["pending"]),
                          "errors": report["errors"], "review": report["review"]}, ensure_ascii=False))
        return 1 if report["errors"] else 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
