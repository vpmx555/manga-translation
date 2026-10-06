"""Persistent, isolated Runpod runtime. Code updates do not reinstall dependencies."""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import signal
import subprocess
import sys
import tarfile
import time
from urllib.error import URLError
from urllib.request import Request, urlopen
import uuid
import zipfile

PROJECT = Path(__file__).resolve().parents[2]
RUNTIME = Path(os.environ.get("MANGA_RUNTIME", "/workspace/manga-runtime"))
PYTHON_VERSION = "3.12.11"
UV_VERSION = "0.12.23"
UV_SHA256 = "9167d72b3319674b6303c4cbe071854bba13ebdf3d76b1a7cbdc175471fb66d6"
OLLAMA_VERSION = "0.35.1"
OLLAMA_SHA256 = "9fcd79ac4575b2bd31b992eee18b1000c8ad126b451627c8f8cd091714cfbb10"
HOST = "http://127.0.0.1:11534"
MAGI_REVISION = "fbc890fec52977142e8ee00bfe26e9458b65517c"


def say(message):
    print(message, flush=True)


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def read_json(path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


@contextlib.contextmanager
def lock(name, timeout=1):
    if sys.platform != "linux":
        raise RuntimeError("manage.py runs on the Linux Pod; use sync.py on your PC")
    import fcntl
    path = RUNTIME / "locks" / (name + ".lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise RuntimeError(f"{name} is busy; another command is still running")
                time.sleep(0.2)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def environment():
    env = dict(os.environ)
    env.update({
        "HF_HOME": str(RUNTIME / "cache" / "huggingface"),
        "UV_CACHE_DIR": str(RUNTIME / "cache" / "uv"),
        "UV_PYTHON_INSTALL_DIR": str(RUNTIME / "python"),
        "UV_PYTHON_PREFERENCE": "only-managed",
        "UV_HTTP_TIMEOUT": "600",
        "UV_HTTP_RETRIES": "3",
        "OLLAMA_HOST": HOST.removeprefix("http://"),
        "OLLAMA_MODELS": str(RUNTIME / "models" / "ollama"),
        "OLLAMA_NUM_PARALLEL": "1",
        "OLLAMA_MAX_LOADED_MODELS": "1",
        "MPLBACKEND": "Agg",
        "PYTHONUNBUFFERED": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "TOKENIZERS_PARALLELISM": "false",
        "OMP_NUM_THREADS": "8",
        "MKL_NUM_THREADS": "8",
        "OPENBLAS_NUM_THREADS": "8",
    })
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    return env


def call(args, *, capture=False, timeout=None):
    result = subprocess.run([str(a) for a in args], cwd=PROJECT, env=environment(),
                            text=True, capture_output=capture, timeout=timeout)
    if result.returncode:
        if capture:
            say((result.stderr or result.stdout)[-3000:])
        raise RuntimeError(f"Command failed ({result.returncode}): {args[0]}")
    return result.stdout if capture else result.returncode


def dependency_signature(requirements=None):
    requirements = requirements or PROJECT / "deployment" / "runpod" / "requirements.in"
    text = requirements.read_text(encoding="utf-8").replace("\r\n", "\n")
    values = [text, PYTHON_VERSION, UV_VERSION, "linux-x86_64-cu128"]
    return hashlib.sha256("\n".join(values).encode()).hexdigest()[:20]


def current_python():
    signature = dependency_signature()
    marker = read_json(RUNTIME / "envs" / signature / "ready.json")
    python = RUNTIME / "envs" / signature / "venv" / "bin" / "python"
    if not marker or marker.get("signature") != signature or not python.exists():
        raise RuntimeError("Environment is not ready for these requirements; run: manga setup")
    return python


def download(url, path, digest):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and file_hash(path) == digest:
        return
    partial = path.with_suffix(path.suffix + ".part")
    for attempt in range(3):
        try:
            say(f"Downloading {path.name} (attempt {attempt + 1}/3)")
            request = Request(url, headers={"User-Agent": "manga-pipeline-runpod"})
            checksum = hashlib.sha256()
            with urlopen(request, timeout=600) as response, partial.open("wb") as output:
                while chunk := response.read(4 * 1024 * 1024):
                    checksum.update(chunk)
                    output.write(chunk)
            if checksum.hexdigest() != digest:
                raise RuntimeError(f"Checksum mismatch: {path.name}")
            partial.replace(path)
            return
        except (OSError, RuntimeError) as exc:
            if attempt == 2:
                raise
            say(str(exc))
            time.sleep(2 ** attempt)


def file_hash(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(4 * 1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def ensure_uv():
    root = RUNTIME / "tools" / "uv" / UV_VERSION
    binary = root / "uv-x86_64-unknown-linux-gnu" / "uv"
    if not binary.exists():
        archive = RUNTIME / "cache" / "downloads" / f"uv-{UV_VERSION}.tar.gz"
        download(f"https://github.com/astral-sh/uv/releases/download/{UV_VERSION}/uv-x86_64-unknown-linux-gnu.tar.gz",
                 archive, UV_SHA256)
        root.mkdir(parents=True, exist_ok=True)
        with tarfile.open(archive) as source:
            source.extractall(root, filter="data")
        binary.chmod(0o755)
    return binary


def ollama_binary():
    return RUNTIME / "tools" / "ollama" / OLLAMA_VERSION / "bin" / "ollama"


def attach_data(project=PROJECT):
    for relative, target in [
        ("banks", RUNTIME / "data" / "banks"),
        ("outputs", RUNTIME / "data" / "outputs"),
        ("models/ner", RUNTIME / "cache" / "ner"),
    ]:
        target.mkdir(parents=True, exist_ok=True)
        link = project / relative
        link.parent.mkdir(parents=True, exist_ok=True)
        if link.is_symlink():
            if link.resolve() != target.resolve():
                raise RuntimeError(f"Unexpected data symlink: {link}")
        elif link.exists():
            if any(link.iterdir()):
                raise RuntimeError(f"Existing data at {link}; move it to {target} before setup")
            link.rmdir()
            link.symlink_to(target, target_is_directory=True)
        else:
            link.symlink_to(target, target_is_directory=True)


def write_shell():
    import shlex
    (RUNTIME / "bin").mkdir(parents=True, exist_ok=True)
    command = RUNTIME / "bin" / "manga"
    command.write_text("#!/bin/sh\nexec /usr/bin/python3 " +
                       shlex.quote(str(PROJECT / "deployment/runpod/manage.py")) + ' "$@"\n', encoding="utf-8")
    command.chmod(0o755)
    lines = [f"export PATH={shlex.quote(str(RUNTIME / 'bin'))}:\"$PATH\""]
    for key in ["HF_HOME", "OLLAMA_HOST", "OLLAMA_MODELS", "MPLBACKEND"]:
        lines.append(f"export {key}={shlex.quote(environment()[key])}")
    shell = RUNTIME / "env.sh"
    shell.write_text("\n".join(lines) + "\n", encoding="utf-8")
    bashrc = Path.home() / ".bashrc"
    existing = bashrc.read_text(encoding="utf-8") if bashrc.exists() else ""
    include = f"[ ! -f {shlex.quote(str(shell))} ] || . {shlex.quote(str(shell))}"
    if include not in existing:
        with bashrc.open("a", encoding="utf-8") as handle:
            handle.write("\n# Persistent manga runtime\n" + include + "\n")


def cuda_check(python):
    script = ("import torch,json; assert torch.cuda.is_available(), 'CUDA is unavailable'; "
              "a=torch.randn(32,32,device='cuda'); b=a@a; torch.cuda.synchronize(); "
              "print(json.dumps({'torch':torch.__version__,'cuda':torch.version.cuda,"
              "'gpu':torch.cuda.get_device_name(0),'capability':torch.cuda.get_device_capability(0),"
              "'result_finite':bool(torch.isfinite(b).all())}))")
    return json.loads(call([python, "-c", script], capture=True, timeout=120))


def setup():
    if sys.platform != "linux" or platform.machine() != "x86_64":
        raise RuntimeError("Setup requires a Linux x86_64 GPU Pod")
    with lock("launch"), lock("pipeline"), lock("setup"):
        for name in ["logs", "models/ollama", "data/input", "cache/downloads", "config"]:
            (RUNTIME / name).mkdir(parents=True, exist_ok=True)
        attach_data()
        uv = ensure_uv()
        signature = dependency_signature()
        root = RUNTIME / "envs" / signature
        python = root / "venv" / "bin" / "python"
        ready = root / "ready.json"
        if not ready.exists() or not python.exists():
            root.mkdir(parents=True, exist_ok=True)
            call([uv, "python", "install", PYTHON_VERSION])
            resuming = python.exists()
            if not python.exists():
                call([uv, "venv", "--python", PYTHON_VERSION, root / "venv"])
            locked = root / "requirements.lock"
            if not locked.exists():
                pending = root / "requirements.lock.pending"
                call([uv, "pip", "compile", PROJECT / "deployment/runpod/requirements.in",
                      "--python", python, "--index-strategy", "unsafe-best-match",
                      "--generate-hashes", "--output-file", pending])
                pending.replace(locked)
            sync_args = [uv, "pip", "sync", "--python", python, "--require-hashes",
                         "--extra-index-url", "https://download.pytorch.org/whl/cu128",
                         "--index-strategy", "unsafe-best-match", locked]
            if resuming:
                sync_args.append("--reinstall")
            call(sync_args)
            call([uv, "pip", "check", "--python", python])
            gpu = cuda_check(python)
            atomic_json(ready, {"signature": signature, "gpu": gpu, "created_at": time.time()})
            say("Environment installed and CUDA verified")
        else:
            say("Dependencies unchanged; reusing the existing environment")
            say(json.dumps(cuda_check(python)))
        binary = ollama_binary()
        ollama_ready = binary.parent.parent / "ready.json"
        if not binary.exists() or not ollama_ready.exists():
            archive = RUNTIME / "cache/downloads" / f"ollama-{OLLAMA_VERSION}.tar.zst"
            download(f"https://github.com/ollama/ollama/releases/download/v{OLLAMA_VERSION}/ollama-linux-amd64.tar.zst",
                     archive, OLLAMA_SHA256)
            destination = binary.parent.parent
            destination.mkdir(parents=True, exist_ok=True)
            script = ("import pathlib,tarfile,zstandard,sys; "
                      "src=pathlib.Path(sys.argv[1]); dst=pathlib.Path(sys.argv[2]); "
                      "f=src.open('rb'); stream=zstandard.ZstdDecompressor().stream_reader(f); "
                      "archive=tarfile.open(fileobj=stream,mode='r|'); "
                      "archive.extractall(dst,filter='data'); archive.close(); stream.close(); f.close()")
            call([python, "-c", script, archive, destination])
            binary.chmod(0o755)
            atomic_json(ollama_ready, {"version": OLLAMA_VERSION, "archive_sha256": OLLAMA_SHA256})
        write_shell()
        atomic_json(RUNTIME / "config/runtime.json", {
            "python": PYTHON_VERSION, "ollama": OLLAMA_VERSION, "uv": UV_VERSION,
            "host": HOST, "signature": signature,
        })
        say("Setup ready. Code edits do not change the dependency signature.")


def http(route, payload=None, timeout=5):
    body = json.dumps(payload).encode() if payload is not None else None
    request = Request(HOST + route, data=body, headers={"Content-Type": "application/json"})
    with urlopen(request, timeout=timeout) as response:
        return json.load(response)


def service_identity(pid):
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
        start_ticks = stat.rsplit(")", 1)[1].split()[19]
        executable = Path(f"/proc/{pid}/exe").resolve()
        return {"pid": pid, "start_ticks": start_ticks, "executable": str(executable)}
    except (OSError, IndexError):
        return None


def owns_service(record):
    if not isinstance(record, dict) or not isinstance(record.get("pid"), int):
        return False
    identity = service_identity(record["pid"])
    return bool(identity and identity == record and identity["executable"] == str(ollama_binary().resolve()))


def start():
    current_python()
    binary = ollama_binary()
    if not binary.exists():
        raise RuntimeError("Ollama binary is missing; run: manga setup")
    with lock("service", timeout=35):
        pidfile = RUNTIME / "config/ollama-process.json"
        record = read_json(pidfile)
        try:
            version = http("/api/version")
        except (OSError, ValueError):
            version = None
        if version:
            if not owns_service(record):
                raise RuntimeError("Port 11534 is occupied by an unmanaged service; it was not changed")
            if version.get("version") != OLLAMA_VERSION:
                raise RuntimeError("Ollama version differs from the pinned runtime")
            say("Ollama is already running; reusing it")
            return version
        if owns_service(record):
            raise RuntimeError("Managed Ollama is alive but unhealthy; inspect logs or run manga stop")
        log = RUNTIME / "logs/ollama.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        if log.exists() and log.stat().st_size > 20 * 1024 * 1024:
            log.replace(log.with_suffix(".previous.log"))
        env = environment()
        env["LD_LIBRARY_PATH"] = str(binary.parent.parent / "lib/ollama") + ":" + env.get("LD_LIBRARY_PATH", "")
        with log.open("ab") as output:
            process = subprocess.Popen([str(binary), "serve"], stdin=subprocess.DEVNULL,
                                       stdout=output, stderr=subprocess.STDOUT, env=env,
                                       cwd=RUNTIME, start_new_session=True)
        identity = service_identity(process.pid)
        if not identity:
            raise RuntimeError("Ollama exited during startup; inspect logs/ollama.log")
        atomic_json(pidfile, identity)
        for _ in range(60):
            if process.poll() is not None:
                raise RuntimeError("Ollama exited; inspect logs/ollama.log")
            try:
                version = http("/api/version", timeout=2)
                if version.get("version") != OLLAMA_VERSION:
                    raise RuntimeError("Unexpected Ollama version")
                say("Ollama ready on " + HOST)
                return version
            except (OSError, ValueError):
                time.sleep(1)
        raise RuntimeError("Ollama startup timed out; inspect logs/ollama.log")


def stop():
    with lock("pipeline"), lock("service"):
        record = read_json(RUNTIME / "config/ollama-process.json")
        if owns_service(record):
            os.kill(record["pid"], signal.SIGTERM)
            for _ in range(100):
                if not owns_service(record):
                    break
                time.sleep(0.1)
            say("Stopped the managed Ollama server; the Pod itself is still billable")
        else:
            say("No managed Ollama server is running")


def configuration(path=None):
    path = Path(path) if path else PROJECT / "configs/pipeline.json"
    result = read_json(path)
    if not isinstance(result, dict):
        raise RuntimeError("Missing or invalid pipeline config: " + str(path))
    result.setdefault("ollama", {})["host"] = HOST
    return result


def ensure_model(config):
    model = config["ollama"]["model"]
    tags = http("/api/tags").get("models", [])
    if not any(item.get("name") == model or item.get("model") == model for item in tags):
        say("Pulling " + model)
        call([ollama_binary(), "pull", model])
    return model


def prefetch():
    with lock("pipeline"):
        start()
        config = configuration()
        ensure_model(config)
        script = ("from huggingface_hub import snapshot_download; "
                  f"snapshot_download('ragavsachdeva/magiv2',revision='{MAGI_REVISION}'); "
                  "from manga_pipeline.providers.gliner_ner import GlinerNames; "
                  "import json,sys; config=json.loads(sys.argv[1]); "
                  "ner=GlinerNames(config['ner']); ner.load(); ner.close(); print('Model caches ready')")
        env = environment()
        env["PYTHONPATH"] = str(PROJECT / "src")
        subprocess.run([str(current_python()), "-c", script, json.dumps(config)],
                       cwd=PROJECT, env=env, check=True)


def doctor(*, inference=False):
    with lock("pipeline"):
        return _doctor(inference=inference)


def _doctor(*, inference=False):
    python = current_python()
    gpu = cuda_check(python)
    call([ensure_uv(), "pip", "check", "--python", python])
    version = start()
    config = configuration()
    report = {"gpu": gpu, "ollama": version, "signature": dependency_signature(),
              "runtime": str(RUNTIME), "disk_free_gb": round(shutil.disk_usage(RUNTIME).free / 2**30, 2)}
    if inference:
        model = ensure_model(config)
        reply = http("/api/chat", {
            "model": model, "stream": False, "think": False,
            "messages": [{"role": "user", "content": "Reply with the word OK."}],
            "options": {"num_ctx": 8192, "num_predict": 32},
        }, timeout=600)
        if not reply.get("message", {}).get("content", "").strip():
            raise RuntimeError("Model returned empty output")
        loaded = http("/api/ps").get("models", [])
        if not any(item.get("size_vram", 0) > 0 for item in loaded):
            raise RuntimeError("Ollama inference did not use GPU VRAM")
        report["inference"] = reply["message"]["content"]
        report["loaded_models"] = loaded
        http("/api/generate", {"model": model, "keep_alive": 0}, timeout=120)
    say(json.dumps(report, ensure_ascii=False, indent=2))
    atomic_json(RUNTIME / "config/last-doctor.json", report)
    return report


def run_pipeline(arguments):
    if not arguments:
        raise RuntimeError("Usage: manga run IMAGE_FOLDER --story STORY --chapter CHAPTER")
    with lock("pipeline"):
        python = current_python()
        job_id = os.environ.get("MANGA_JOB_ID")
        if job_id:
            if not job_id.isalnum():
                raise RuntimeError("Invalid job identifier")
            atomic_json(RUNTIME / "jobs" / job_id / "job.json", {
                "id": job_id, "status": "running", "identity": service_identity(os.getpid()),
                "command": list(arguments), "started_at": time.time(),
            })
        start()
        config_path = None
        args = list(arguments)
        if "--config" in args:
            pos = args.index("--config")
            if pos + 1 >= len(args):
                raise RuntimeError("--config requires a path")
            config_path = args[pos + 1]
            del args[pos:pos + 2]
        config = configuration(config_path)
        model = ensure_model(config)
        # MAGI owns the GPU first; release an earlier Ollama model before extraction.
        for loaded in http("/api/ps").get("models", []):
            http("/api/generate", {"model": loaded["name"], "keep_alive": 0}, timeout=120)
        if args[0] in {"run", "extract", "reanalyze", "translate"}:
            runtime_config = RUNTIME / "config/pipeline.json"
            atomic_json(runtime_config, config)
            args.extend(["--config", str(runtime_config)])
        if args[0] in {"run", "extract"}:
            if "--device" not in args:
                args.extend(["--device", "cuda"])
            if "--bank-root" not in args:
                args.extend(["--bank-root", str(RUNTIME / "data/banks")])
        if args[0] in {"run", "extract", "reanalyze"} and "--output-root" not in args:
            args.extend(["--output-root", str(RUNTIME / "data/outputs")])
        # A copied Windows manifest cannot safely be resumed on Linux.
        if "--run-dir" in args:
            directory = Path(args[args.index("--run-dir") + 1])
            manifest = read_json(directory / "manifest.json", {})
            if ":\\" in manifest.get("bank_path", ""):
                raise RuntimeError("This run has Windows paths; create a new run on the Pod")
            host = manifest.get("config", {}).get("ollama", {}).get("host")
            if args[0] != "translate" and host and host != HOST:
                raise RuntimeError("Run uses a different Ollama host; it was not silently rewritten")
        started = time.monotonic()
        metadata = {"pid": os.getpid(), "command": args, "started_at": time.time()}
        atomic_json(RUNTIME / "config/pipeline-process.json", metadata)
        try:
            result = subprocess.run([str(python), str(PROJECT / "run.py"), *args],
                                    cwd=PROJECT, env=environment())
            return result.returncode
        finally:
            atomic_json(RUNTIME / "config/last-run.json", {
                **metadata, "elapsed_seconds": round(time.monotonic() - started, 2),
            })
            try:
                http("/api/generate", {"model": model, "keep_alive": 0}, timeout=120)
            except OSError:
                pass


def launch(arguments):
    if not arguments or arguments[0] not in {"run", "extract", "resume", "reanalyze", "translate", "review"}:
        raise RuntimeError("launch requires a pipeline command")
    with lock("launch"):
        current_python()
        with lock("pipeline"):
            pass
        job_id = uuid.uuid4().hex
        directory = RUNTIME / "jobs" / job_id
        directory.mkdir(parents=True)
        env = environment()
        env["MANGA_JOB_ID"] = job_id
        with (directory / "output.log").open("ab") as output:
            process = subprocess.Popen([sys.executable, str(PROJECT / "deployment/runpod/manage.py"), *arguments],
                                       cwd=PROJECT, env=env, stdin=subprocess.DEVNULL,
                                       stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
        for _ in range(150):
            record = read_json(directory / "job.json")
            if record and record.get("identity"):
                if record.get("status") == "failed":
                    say((directory / "output.log").read_text(encoding="utf-8", errors="replace")[-3000:])
                    raise RuntimeError("Job failed during startup")
                say(json.dumps({"job": job_id, "log": str(directory / "output.log"),
                                "status": record.get("status"),
                                "message": "Job continues after SSH disconnects"}))
                return
            if process.poll() is not None:
                say((directory / "output.log").read_text(encoding="utf-8", errors="replace")[-3000:])
                raise RuntimeError("Job could not start")
            time.sleep(0.2)
        raise RuntimeError(f"Job {job_id} startup did not confirm; inspect its log before retrying")


def jobs():
    directory = RUNTIME / "jobs"
    records = []
    if directory.exists():
        for path in sorted(directory.glob("*/job.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:20]:
            record = read_json(path)
            if record:
                if record.get("status") == "running" and service_identity(record["identity"]["pid"]) != record["identity"]:
                    record["status"] = "interrupted"
                records.append(record)
    say(json.dumps(records, ensure_ascii=False, indent=2))


def logs(arguments):
    if len(arguments) != 1 or not arguments[0].isalnum():
        raise RuntimeError("Usage: manga logs JOB_ID")
    path = RUNTIME / "jobs" / arguments[0] / "output.log"
    with path.open("rb") as stream:
        stream.seek(max(0, path.stat().st_size - 16000))
        say(stream.read().decode("utf-8", errors="replace"))


def code_status():
    import importlib.util
    spec = importlib.util.spec_from_file_location("manga_sync_status", PROJECT / "deployment/runpod/sync.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    hashes = {p.relative_to(PROJECT).as_posix(): file_hash(p) for p in module.code_files(PROJECT)}
    version = hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()
    say(version)


def install_update(archive):
    """Validate the complete bundle, then atomically switch code under two locks."""
    staging = RUNTIME / "code-staging" / uuid.uuid4().hex
    staging.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(archive) as bundle:
            manifest = json.loads(bundle.read("__bundle__.json"))
            expected = manifest["files"]
            actual = set(bundle.namelist()) - {"__bundle__.json"}
            if actual != set(expected):
                raise RuntimeError("Bundle file list differs from its manifest")
            for name, digest in expected.items():
                target = staging / name
                if Path(name).is_absolute() or ".." in Path(name).parts or "\\" in name:
                    raise RuntimeError("Unsafe archive path")
                content = bundle.read(name)
                if hashlib.sha256(content).hexdigest() != digest:
                    raise RuntimeError("Bundle checksum mismatch: " + name)
                if name.endswith(".py"):
                    compile(content, name, "exec")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
        for name in ["run.py", "src/manga_pipeline/cli/main.py", "deployment/runpod/manage.py"]:
            if not (staging / name).is_file():
                raise RuntimeError("Missing required bundle file: " + name)
        with lock("launch"), lock("pipeline"), lock("setup"):
            attach_data(staging)
            backup = RUNTIME / "code-backups" / uuid.uuid4().hex
            backup.parent.mkdir(parents=True, exist_ok=True)
            existed = PROJECT.exists()
            if existed:
                for name in ["banks", "outputs", "models/ner"]:
                    old = PROJECT / name
                    if old.exists() and not old.is_symlink() and any(old.iterdir()):
                        raise RuntimeError("Unmanaged data must be backed up before updating: " + str(old))
                PROJECT.rename(backup)
            try:
                staging.rename(PROJECT)
            except BaseException:
                if existed:
                    backup.rename(PROJECT)
                raise
            atomic_json(RUNTIME / "config/code-version.json", manifest)
            say("Code updated atomically; data, caches and environments were preserved")
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["setup", "start", "stop", "doctor", "prefetch", "status", "install-update", "code-status", "launch", "jobs", "logs", "run", "extract", "resume", "reanalyze", "translate", "review"])
    parser.add_argument("arguments", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    try:
        if args.command == "setup":
            setup()
        elif args.command == "start":
            start()
        elif args.command == "stop":
            stop()
        elif args.command == "doctor":
            doctor(inference="--inference" in args.arguments)
        elif args.command == "prefetch":
            prefetch()
        elif args.command == "launch":
            launch(args.arguments)
        elif args.command == "jobs":
            jobs()
        elif args.command == "logs":
            logs(args.arguments)
        elif args.command == "code-status":
            code_status()
        elif args.command == "status":
            say(json.dumps({"runtime": read_json(RUNTIME / "config/runtime.json"),
                            "last_run": read_json(RUNTIME / "config/last-run.json"),
                            "ollama_process_owned": owns_service(read_json(RUNTIME / "config/ollama-process.json"))}, indent=2))
        elif args.command == "install-update":
            if len(args.arguments) != 1:
                raise RuntimeError("install-update requires one bundle path")
            install_update(Path(args.arguments[0]))
        else:
            return run_pipeline([args.command, *args.arguments])
        return 0
    except KeyboardInterrupt:
        say("Interrupted; checkpoints can be resumed")
        return 130
    except Exception as exc:
        say("ERROR: " + str(exc))
        return 1


if __name__ == "__main__":
    result = main()
    job_id = os.environ.get("MANGA_JOB_ID")
    if job_id and job_id.isalnum():
        record = read_json(RUNTIME / "jobs" / job_id / "job.json", {})
        record.update({"id": job_id, "status": "completed" if result == 0 else "partial" if result == 2 else "failed",
                       "exit_code": result, "finished_at": time.time()})
        atomic_json(RUNTIME / "jobs" / job_id / "job.json", record)
    raise SystemExit(result)
