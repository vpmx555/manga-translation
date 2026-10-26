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
RUNTIME = Path(os.environ.get("MANGA_RUNTIME", "/opt/manga-work/runtime"))
PYTHON_VERSION = "3.12.11"
UV_VERSION = "0.12.23"
UV_SHA256 = "9167d72b3319674b6303c4cbe071854bba13ebdf3d76b1a7cbdc175471fb66d6"
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
def lock(name, timeout=1, *, shared=False):
    if sys.platform != "linux":
        raise RuntimeError("manage.py runs on the Linux Pod; use local_v6.py on your PC")
    import fcntl
    path = RUNTIME / "locks" / (name + ".lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(handle, (fcntl.LOCK_SH if shared else fcntl.LOCK_EX) | fcntl.LOCK_NB)
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
        "UV_CACHE_DIR": str(execution_root() / "cache" / "uv"),
        "CUPY_CACHE_DIR": str(execution_root() / "cache" / "cupy"),
        "UV_PYTHON_INSTALL_DIR": str(execution_root() / "python"),
        "UV_PYTHON_PREFERENCE": "only-managed",
        "UV_HTTP_TIMEOUT": "600",
        "UV_HTTP_RETRIES": "3",
        "MANGA_LLM_PAUSE_FILE": str(RUNTIME / "config/llm-inference-pause.json"),
        "MPLBACKEND": "Agg",
        "PYTHONUNBUFFERED": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "TOKENIZERS_PARALLELISM": "false",
        "OMP_NUM_THREADS": env.get("OMP_NUM_THREADS", "8"),
        "MKL_NUM_THREADS": env.get("MKL_NUM_THREADS", "8"),
        "OPENBLAS_NUM_THREADS": env.get("OPENBLAS_NUM_THREADS", "8"),
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


def execution_root():
    """Optionally keep Python/package I/O off a slow mounted data volume."""
    return Path(os.environ.get("MANGA_EXECUTION_ROOT", "/opt/manga-work/execution"))


def current_python():
    signature = dependency_signature()
    marker = read_json(execution_root() / "envs" / signature / "ready.json")
    python = execution_root() / "envs" / signature / "venv" / "bin" / "python"
    if not marker or marker.get("signature") != signature or not python.exists():
        raise RuntimeError("Environment is not ready for these requirements; run: manga setup")
    return python


def visual_signature():
    requirements = PROJECT / "deployment/runpod/requirements-visual.in"
    content = (PROJECT / "requirements-visual-layout.txt").read_text(encoding="utf-8").replace("\r\n", "\n")
    return hashlib.sha256((dependency_signature(requirements) + content).encode()).hexdigest()[:20]


def visual_python(*, require_ready=True):
    signature = visual_signature()
    root = execution_root() / "envs" / ("visual-" + signature)
    python = root / "venv/bin/python"
    marker = read_json(root / "ready.json") if require_ready else None
    if require_ready and (not marker or marker.get("signature") != signature or not python.exists()):
        raise RuntimeError("Koharu environment is not ready; run: manga setup")
    return python


def hayai_python():
    content = ((PROJECT / "requirements-hayai.txt").read_bytes()
               + (PROJECT / "deployment/runpod/requirements-hayai.in").read_bytes())
    signature = hashlib.sha256(content).hexdigest()[:20]
    return execution_root() / "envs" / ("hayai-" + signature) / "venv/bin/python"


def glm_python():
    content = ((PROJECT / "requirements-glm-ocr.txt").read_bytes()
               + (PROJECT / "deployment/runpod/requirements-glm-ocr.in").read_bytes())
    return execution_root() / "envs" / ("glm-" + hashlib.sha256(content).hexdigest()[:20]) / "venv/bin/python"


def ocr_python(config):
    return glm_python() if config['ocr']['backend'] == 'glm-ocr' else hayai_python()


def setup_glm(uv, *, verify_cuda=True):
    python = glm_python()
    root = python.parents[2]
    signature = root.name
    ready = root / "ready.json"
    if read_json(ready, {}).get("signature") == signature and python.exists():
        if verify_cuda:
            cuda_check(python)
        return
    locked = root / "requirements.lock"
    if not locked.exists():
        raise RuntimeError("GLM-OCR Linux lock must be prepared before renting a GPU")
    if not python.exists():
        call([uv, "venv", "--allow-existing", "--python", PYTHON_VERSION, root / "venv"])
    call([uv, "pip", "sync", "--python", python, "--require-hashes", "--extra-index-url",
          "https://download.pytorch.org/whl/cu128", "--index-strategy", "unsafe-best-match", locked])
    call([uv, "pip", "check", "--python", python])
    call([python, "-c", "import torchvision; from transformers import AutoProcessor,GlmOcrForConditionalGeneration"])
    if verify_cuda:
        cuda_check(python)
    atomic_json(ready, {"signature": signature, "created_at": time.time()})


def setup_hayai(uv, *, verify_cuda=True):
    python = hayai_python()
    root = python.parents[2]
    ready = root / "ready.json"
    if ready.exists() and python.exists():
        if verify_cuda:
            cuda_check(python)
        return
    root.mkdir(parents=True, exist_ok=True)
    if not python.exists():
        call([uv, "venv", "--python", PYTHON_VERSION, root / "venv"])
    locked = root / "requirements.lock"
    if not locked.exists():
        pending = root / "requirements.lock.pending"
        call([uv, "pip", "compile", PROJECT / "deployment/runpod/requirements-hayai.in", "--python", python,
              "--index-strategy", "unsafe-best-match",
              "--generate-hashes", "--output-file", pending])
        pending.replace(locked)
    call([uv, "pip", "sync", "--python", python, "--require-hashes",
          "--extra-index-url", "https://download.pytorch.org/whl/cu128",
          "--index-strategy", "unsafe-best-match", locked])
    call([uv, "pip", "check", "--python", python])
    call([python, "-c", "from transformers import AutoProcessor, Siglip2VisionModel, PreTrainedTokenizerFast"])
    if verify_cuda:
        cuda_check(python)
    atomic_json(ready, {"created_at": time.time()})


def setup_visual(uv, *, verify_cuda=True):
    signature = visual_signature()
    python = visual_python(require_ready=False)
    root = python.parents[2]
    ready = root / "ready.json"
    if read_json(ready, {}).get("signature") == signature and python.exists():
        say("Koharu dependencies unchanged; reusing isolated worker")
        if verify_cuda:
            cuda_check(python)
        return
    root.mkdir(parents=True, exist_ok=True)
    resuming = python.exists()
    if not resuming:
        call([uv, "venv", "--python", PYTHON_VERSION, root / "venv"])
    locked = root / "requirements.lock"
    if not locked.exists():
        pending = root / "requirements.lock.pending"
        call([uv, "pip", "compile", PROJECT / "deployment/runpod/requirements-visual.in",
              "--python", python, "--index-strategy", "unsafe-best-match",
              "--generate-hashes", "--output-file", pending])
        pending.replace(locked)
    args = [uv, "pip", "sync", "--python", python, "--require-hashes",
            "--extra-index-url", "https://download.pytorch.org/whl/cu128",
            "--index-strategy", "unsafe-best-match", locked]
    if resuming:
        args.append("--reinstall")
    call(args)
    call([uv, "pip", "check", "--python", python])
    call([python, "-c", "from rfdetr import RFDETRSeg2XLarge; from importlib.metadata import version; "
          "assert version('rfdetr')=='1.7.0'; assert version('transformers').split('.')[0]=='5'"])
    gpu = cuda_check(python) if verify_cuda else {"verified": False, "reason": "CPU image preparation only"}
    atomic_json(ready, {"signature": signature, "gpu": gpu, "created_at": time.time()})


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
    root = execution_root() / "tools" / "uv" / UV_VERSION
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
    for key in ["HF_HOME", "MPLBACKEND"]:
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


def setup(*, verify_cuda=True, config=None):
    if sys.platform != "linux" or platform.machine() != "x86_64":
        raise RuntimeError("Setup requires a Linux x86_64 GPU Pod")
    with lock("launch"), lock("pipeline"), lock("setup"):
        for name in ["logs", "data/input", "cache/downloads", "config"]:
            (RUNTIME / name).mkdir(parents=True, exist_ok=True)
        attach_data()
        uv = ensure_uv()
        signature = dependency_signature()
        root = execution_root() / "envs" / signature
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
            gpu = cuda_check(python) if verify_cuda else {"verified": False, "reason": "CPU image preparation only"}
            atomic_json(ready, {"signature": signature, "gpu": gpu, "created_at": time.time()})
            say("Environment installed" + (" and CUDA verified" if verify_cuda else "; CUDA verification deferred to the GPU Pod"))
        else:
            say("Dependencies unchanged; reusing the existing environment")
            if verify_cuda:
                say(json.dumps(cuda_check(python)))
        setup_visual(uv, verify_cuda=verify_cuda)
        config = config or configuration()
        if config['ocr']['backend'] == 'glm-ocr':
            setup_glm(uv, verify_cuda=verify_cuda)
        else:
            setup_hayai(uv, verify_cuda=verify_cuda)
        write_shell()
        atomic_json(RUNTIME / "config/runtime.json", {
            "python": PYTHON_VERSION, "uv": UV_VERSION,
            "signature": signature,
            "visual_signature": visual_signature(), "visual_python": str(visual_python()),
        })
        say("Setup ready. Code edits do not change the dependency signature.")


def service_identity(pid):
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
        start_ticks = stat.rsplit(")", 1)[1].split()[19]
        executable = Path(f"/proc/{pid}/exe").resolve()
        return {"pid": pid, "start_ticks": start_ticks, "executable": str(executable)}
    except (OSError, IndexError):
        return None


def start(*, require_profile=False):
    config = configuration()
    activate_vllm(config)
    import vllm_runtime
    return vllm_runtime.start(config)


def stop():
    import vllm_runtime
    return vllm_runtime.stop()


@contextlib.contextmanager
def parallel_service_guard(config):
    """Change server slot profile only at an idle, exclusively locked boundary."""
    activate_vllm(config)
    from manga_pipeline.runtime.inference_state import require_ready
    with lock("pipeline"):
        require_ready(RUNTIME / "config/llm-inference-pause.json")
    with lock("pipeline", shared=True):
        yield


def configuration(path=None):
    sys.path.insert(0, str(PROJECT / "src"))
    from manga_pipeline.pipeline import load_config
    result = load_config(Path(path) if path else PROJECT / "configs/pipeline-v6-runpod.json")
    # Interpreter/assets belong to the persistent managed runtime, not the image.
    result.setdefault("layout", {}).update(enabled=True, python=str(visual_python(require_ready=False)),
                                          model_root=str(RUNTIME / "models/visual"))
    result.setdefault("inpaint", {}).setdefault("enabled", False)
    result["inpaint"]["model_root"] = str(RUNTIME / "models/visual")
    sys.path.insert(0, str(PROJECT / "src"))
    from manga_pipeline.runtime.llm_budget import validate_runpod_profile
    validate_runpod_profile(result)
    return result


def activate_vllm(config):
    """Configure the selected local controller; never starts or downloads here."""
    sys.path.insert(0, str(PROJECT / "src"))
    os.environ.update(MANGA_LLM_BACKEND="vllm", MANGA_VLLM_CONFIG=json.dumps(config),
        MANGA_LLM_MANAGER=str(PROJECT / "deployment/runpod/vllm_runtime.py"),
        MANGA_LLM_SERVICE_RECORD=str(RUNTIME / "config/vllm-service.json"),
        MANGA_LLM_SERVER_IDENTITY_FILE=str(RUNTIME / "config/vllm-process.json"),
        MANGA_LLM_PAUSE_FILE=str(RUNTIME / "config/llm-inference-pause.json"))


def config_fingerprint(config):
    return hashlib.sha256(json.dumps(config, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def validate_gpu_profile(config):
    expected = config.get("runtime", {}).get("required_gpu")
    if expected:
        names = call(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"], capture=True).strip().splitlines()
        if len(names) != 1 or expected.lower() not in names[0].lower():
            raise RuntimeError(f"Frozen profile requires exactly one {expected}; no alternative GPU is authorized")


def ensure_model(config):
    activate_vllm(config)
    import vllm_runtime
    vllm_runtime.require_prepared(config)
    return config["llm"]["model"]


def prefetch(config=None):
    with lock("pipeline"):
        config = configuration() if config is None else config
        if config['ocr']['backend'] == 'glm-ocr':
            call([glm_python(), "-c", "from huggingface_hub import snapshot_download; import json,sys; "
                  "from transformers import AutoProcessor; c=json.loads(sys.argv[1]); "
                  "p=snapshot_download(c['model'], revision=c['revision'], "
                  "allow_patterns=['*.json','*.safetensors','*.model','*.txt','*.jinja']); "
                  "AutoProcessor.from_pretrained(p,local_files_only=True); print('GLM-OCR cached; processor verified')",
                  json.dumps(config['ocr'])])
        else:
            call([hayai_python(), "-c",
              "from huggingface_hub import snapshot_download; "
              "snapshot_download('JustANormalTinkerer/hayai-ocr-v2.5-nova',revision='2803d0dc005fa4396ab1e7c5a472d2c450a247f7'); "
                  "snapshot_download('google/siglip2-base-patch16-naflex',revision='b53b807d3a2d5e2b3911292f2d69e5341cdc064c',allow_patterns=['*.json','preprocessor_config.json'])"])
        worker = visual_python()
        call([worker, "-B", PROJECT / "scripts/setup_visual_models.py", "--only", "layout",
              "--model-root", RUNTIME / "models/visual"])
        activate_vllm(config)
        import vllm_runtime
        vllm_runtime.prefetch(config)
        ensure_model(config)
        script = ("from huggingface_hub import snapshot_download; "
                  f"snapshot_download('ragavsachdeva/magiv2',revision='{MAGI_REVISION}'); "
                  "from manga_pipeline.providers.gliner_ner import GlinerNames; "
                  "import json,sys; config=json.loads(sys.argv[1]); "
                  "ner=GlinerNames(config['ner']) if config['ner']['backend']=='gliner' else None; "
                  "ner.load() if ner else None; ner.close() if ner else None; print('Model caches ready')")
        env = environment()
        env["PYTHONPATH"] = str(PROJECT / "src")
        subprocess.run([str(current_python()), "-c", script, json.dumps(config)],
                       cwd=PROJECT, env=env, check=True)


def doctor(*, inference=False, config=None):
    with lock("pipeline"):
        return _doctor(inference=inference, config=config)


def visual_preflight(config):
    worker = visual_python()
    script = ("import json,sys; from manga_pipeline.visual.config import LAYOUT_CONFIG,preflight; "
              "preflight({**LAYOUT_CONFIG,**json.loads(sys.argv[1])}); print('Koharu ready')")
    env = environment()
    env["PYTHONPATH"] = str(PROJECT / "src")
    subprocess.run([str(worker), "-B", "-c", script, json.dumps(config["layout"])],
                   cwd=PROJECT, env=env, check=True)


def _doctor(*, inference=False, config=None):
    sys.path.insert(0, str(PROJECT / "src"))
    config = configuration() if config is None else config
    use_vllm = config.get("llm", {}).get("backend") == "vllm"
    if use_vllm:
        activate_vllm(config)
        from manga_pipeline.runtime.inference_state import require_ready
        require_ready(RUNTIME / "config/llm-inference-pause.json")
    validate_gpu_profile(config)
    python = current_python()
    visual_preflight(config)
    visual_gpu = cuda_check(visual_python())
    hayai_gpu = cuda_check(ocr_python(config))
    gpu = cuda_check(python)
    geometry_code = ("import sys,json; sys.path.insert(0,sys.argv[1]); "
                     "from manga_pipeline.v6.geometry_backend import preflight; "
                     "print(json.dumps(preflight('cuda')))")
    geometry_gpu = json.loads(call([python, "-c", geometry_code, PROJECT / "src"], capture=True))
    call([ensure_uv(), "pip", "check", "--python", python])
    version = {"version": config["vllm"]["version"], "backend": "vllm"}
    report = {"gpu": gpu, "geometry_gpu": geometry_gpu, "llm": version, "signature": dependency_signature(),
              "config_fingerprint": config_fingerprint(config),
              "source_bundle": read_json(PROJECT / "__bundle__.json", {}).get("version"),
              "server_identity": read_json(RUNTIME / "config/vllm-process.json"),
              "visual_gpu": visual_gpu, "visual_signature": visual_signature(),
              "ocr_gpu": hayai_gpu, "ocr_python": str(ocr_python(config)), "ocr_backend": config['ocr']['backend'],
              "execution_root": str(execution_root()),
              "runtime": str(RUNTIME), "disk_free_gb": round(shutil.disk_usage(RUNTIME).free / 2**30, 2)}
    if inference and use_vllm:
        import vllm_runtime
        from manga_pipeline.runtime.resources import configure_batch
        from manga_pipeline.runtime.llm_budget import preflight_case
        from manga_pipeline.providers.vllm import VLLM
        # A preflight lease belongs to one frozen batch. Sharing this audit root
        # across batches makes an interrupted old probe block every later run.
        preflight_root = RUNTIME / "config/vllm-preflight"
        batch_id = os.environ.get("MANGA_BATCH_ID")
        if batch_id:
            preflight_root = preflight_root / batch_id
        os.environ.update(configure_batch(config, preflight_root))
        provider = VLLM(config, RUNTIME / "cache/vllm-preflight" / uuid.uuid4().hex)
        probe = preflight_case(config)
        def check(value):
            if set(value.get("utterances", {})) != {r["id"] for r in probe["data"]["targets"]}:
                raise ValueError("Preflight target coverage changed")
        result = provider.infer_once("analyze_dialogue_v6", probe["prompt"], probe["data"], probe["schema"], check)
        record = read_json(RUNTIME / "config/vllm-service.json", {})
        report.update(inference=True, model_digest=config["llm"]["model"] + "@" + config["vllm"]["revision"],
            server_identity=record.get("identity"), model_revision=config["vllm"]["revision"],
            vllm_signature=vllm_runtime.signature(config), full_gpu_verified=record.get("cpu_offload_gb") == 0,
            probe_budget=provider.request_budget("analyze_dialogue_v6", probe["prompt"], probe["data"], probe["schema"]))
        # Follow the same stage ownership as execution; release VRAM for visual work.
        vllm_runtime.stop()
    say(json.dumps(report, ensure_ascii=False, indent=2))
    atomic_json(RUNTIME / "config/last-doctor.json", report)
    return report


def run_pipeline(arguments):
    if not arguments:
        raise RuntimeError("Usage: manga run IMAGE_FOLDER --story STORY --chapter CHAPTER")
    parallel = os.environ.get("MANGA_PARALLEL_BATCH") == "1"
    story = os.environ.get("MANGA_STORY_KEY", "")
    if parallel and (not story or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789-_" for c in story)
                     or not os.environ.get("MANGA_GPU_COORDINATOR")):
        raise RuntimeError("Parallel workers require a validated story key and GPU coordinator")
    from contextlib import nullcontext
    with lock("pipeline", shared=parallel), (lock("story-" + story) if parallel else nullcontext()):
        python = current_python()
        job_id = os.environ.get("MANGA_JOB_ID")
        if job_id:
            if not job_id.isalnum():
                raise RuntimeError("Invalid job identifier")
            atomic_json(RUNTIME / "jobs" / job_id / "job.json", {
                "id": job_id, "status": "running", "identity": service_identity(os.getpid()),
                "command": list(arguments), "started_at": time.time(),
            })
        config_path = None
        args = list(arguments)
        if "--config" in args:
            pos = args.index("--config")
            if pos + 1 >= len(args):
                raise RuntimeError("--config requires a path")
            config_path = args[pos + 1]
            del args[pos:pos + 2]
        config = configuration(config_path)
        if config.get("ocr", {}).get("backend") in {"hayai", "glm-ocr"}:
            if not ocr_python(config).exists():
                raise RuntimeError("OCR environment is missing; run manga setup before inference")
            config["ocr"]["python"] = str(ocr_python(config))
        if args[0] in {"run", "extract"}:
            visual_preflight(config)
        use_vllm = config.get("llm", {}).get("backend") == "vllm"
        activate_vllm(config)
        model = ensure_model(config)
        if args[0] in {"run", "extract", "reanalyze", "translate"}:
            runtime_config = RUNTIME / "config" / (f"pipeline-{story}.json" if parallel else "pipeline.json")
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
            host = manifest.get("config", {}).get("llm", {}).get("host")
            expected_host = "http://127.0.0.1:8000"
            if args[0] != "translate" and host and host != expected_host:
                raise RuntimeError("Run uses a different Ollama host; it was not silently rewritten")
        started = time.monotonic()
        metadata = {"pid": os.getpid(), "command": args, "started_at": time.time()}
        atomic_json(RUNTIME / "config" / (f"pipeline-process-{story}.json" if parallel else "pipeline-process.json"), metadata)
        try:
            result = subprocess.run([str(python), str(PROJECT / "run.py"), *args],
                                    cwd=PROJECT, env=environment())
            return result.returncode
        finally:
            atomic_json(RUNTIME / "config" / (f"last-run-{story}.json" if parallel else "last-run.json"), {
                **metadata, "elapsed_seconds": round(time.monotonic() - started, 2),
            })


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
    spec = importlib.util.spec_from_file_location("manga_sync_status", PROJECT / "deployment/runpod/native_transport.py")
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
                shutil.move(str(PROJECT), str(backup))
            try:
                shutil.move(str(staging), str(PROJECT))
            except BaseException:
                if existed:
                    shutil.move(str(backup), str(PROJECT))
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
        selected_config = None
        if args.command in {"doctor", "prefetch"}:
            flags = list(args.arguments)
            if "--config" in flags:
                index = flags.index("--config")
                if index + 1 >= len(flags) or flags[index + 1].startswith("--"):
                    raise ValueError("--config requires a path")
                selected_config = configuration(flags[index + 1])
                del flags[index:index + 2]
            if any(flag != "--inference" or args.command != "doctor" for flag in flags):
                raise ValueError("doctor/prefetch accepts --config PATH; only doctor accepts --inference")
        if args.command == "setup":
            if args.arguments not in ([], ["--prepare-only"]):
                raise ValueError("setup accepts only --prepare-only (CPU image preparation; not CUDA verification)")
            setup(verify_cuda="--prepare-only" not in args.arguments)
        elif args.command == "start":
            start()
        elif args.command == "stop":
            stop()
        elif args.command == "doctor":
            doctor(inference="--inference" in args.arguments, config=selected_config)
        elif args.command == "prefetch":
            prefetch(config=selected_config)
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
                            "vllm_service": read_json(RUNTIME / "config/vllm-service.json")}, indent=2))
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
