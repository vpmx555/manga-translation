"""Managed loopback vLLM service; no provisioning, implicit downloads or CPU offload."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import manage


def settings():
    raw = os.environ.get("MANGA_VLLM_CONFIG")
    if not raw:
        raise RuntimeError("Missing frozen MANGA_VLLM_CONFIG")
    config = json.loads(raw)
    if config["llm"].get("backend") != "vllm":
        raise RuntimeError("vLLM controller cannot serve another backend")
    sys.path.insert(0, str(manage.PROJECT / "src"))
    from manga_pipeline.runtime.llm_budget import validate_runpod_profile
    validate_runpod_profile(config)
    return config


def python():
    return manage.execution_root() / "envs/vllm/bin/python"


def signature(config):
    return hashlib.sha256(json.dumps({"model": config["llm"]["model"],
        "num_ctx": config["llm"]["num_ctx"], "engine": config["vllm"],
        "llm_slots": config["vllm"]["max_num_seqs"]}, sort_keys=True).encode()).hexdigest()


def model_path(config):
    key = config["llm"]["model"].replace("/", "--")
    return manage.RUNTIME / "models/vllm" / key / config["vllm"]["revision"]


def setup(config, *, resolve=True):
    target = python().parent.parent
    source = manage.PROJECT / "deployment/runpod/requirements-vllm.in"
    expected = {"requirements_sha256": manage.file_hash(source), "python": manage.PYTHON_VERSION,
                "vllm": config["vllm"]["version"]}
    ready = manage.read_json(target / "ready.json", {})
    lock = target / "requirements.lock"
    if (python().is_file() and all(ready.get(k) == v for k, v in expected.items())
            and lock.is_file() and ready.get("lock_sha256") == manage.file_hash(lock)):
        return
    if not resolve and not lock.is_file():
        raise RuntimeError("Missing CPU-prepared vLLM lock; no dependency resolution on the GPU host")
    uv = manage.ensure_uv()
    if not python().is_file():
        # Native ingress seeds requirements.lock before the environment exists.
        # Keep that frozen lock intact while initializing the venv in place.
        manage.call([uv, "venv", "--allow-existing", target, "--python", manage.current_python()])
    if not lock.is_file():
        pending = lock.with_suffix(".lock.pending")
        manage.call([uv, "pip", "compile", source, "--python", python(), "--generate-hashes", "--output-file", pending])
        pending.replace(lock)
    manage.call([uv, "pip", "sync", "--python", python(), "--require-hashes", lock])
    actual = manage.call([python(), "-c", "from importlib.metadata import version; print(version('vllm'))"], capture=True).strip()
    if actual != config["vllm"]["version"]:
        raise RuntimeError("Installed vLLM version differs from the pinned version")
    manage.atomic_json(target / "ready.json", {**expected, "lock_sha256": manage.file_hash(lock)})


def prefetch(config):
    require_environment(config)
    path = model_path(config)
    manage.call([python(), "-c", "import sys; from huggingface_hub import snapshot_download; "
        "snapshot_download(sys.argv[1],revision=sys.argv[2],local_dir=sys.argv[3])",
        config["llm"]["model"], config["vllm"]["revision"], path])
    cfg = manage.read_json(path / "config.json")
    quant = cfg.get("quantization_config", {})
    groups = quant.get("config_groups", {}).values()
    if quant.get("quant_method") != "compressed-tensors" or not any(g.get("weights", {}).get("num_bits") == 4 for g in groups):
        raise RuntimeError("Downloaded checkpoint is not the pinned 4-bit compressed-tensors model")
    index = manage.read_json(path / "model.safetensors.index.json")
    shards = set(index.get("weight_map", {}).values())
    if not shards or any(not (path / shard).is_file() for shard in shards):
        raise RuntimeError("Downloaded model shard set is incomplete")
    manage.atomic_json(path / "ready.json", {"signature": signature(config), "revision": config["vllm"]["revision"]})


def http(route, payload=None, *, timeout=5):
    config = settings()
    request = Request(config["llm"]["host"].rstrip("/") + route,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Content-Type": "application/json"})
    with urlopen(request, timeout=timeout) as response:
        raw = response.read()
        return json.loads(raw) if raw else {}


def command(config):
    cfg, engine = config["llm"], config["vllm"]
    if cfg["host"] != "http://127.0.0.1:8000":
        raise RuntimeError("Managed vLLM is restricted to the loopback endpoint")
    args = [str(python().parent / "vllm"), "serve", str(model_path(config)),
        "--served-model-name", cfg["model"], "--host", "127.0.0.1", "--port", "8000",
        "--max-model-len", str(cfg["num_ctx"]), "--max-num-seqs", str(config["vllm"]["max_num_seqs"]),
        "--max-num-batched-tokens", str(engine["max_num_batched_tokens"]),
        "--gpu-memory-utilization", str(engine["gpu_memory_utilization"]),
        "--dtype", engine["dtype"], "--quantization", engine["quantization"],
        "--cpu-offload-gb", "0", "--enforce-eager", "--enable-chunked-prefill",
        "--no-enable-prefix-caching", "--generation-config", "vllm"]
    if 'max_images_per_prompt' in engine:
        args.extend(['--limit-mm-per-prompt', json.dumps({'image': engine['max_images_per_prompt'], 'video': 0})])
    return args


def owns(record):
    identity = manage.read_json(manage.RUNTIME / "config/vllm-process.json", {})
    return bool(record and identity and record.get("identity") == identity
                and manage.service_identity(identity.get("pid")) == identity)


def require_environment(config):
    ready = manage.read_json(python().parent.parent / "ready.json", {})
    lock = python().parent.parent / "requirements.lock"
    if (not python().is_file() or ready.get("vllm") != config["vllm"]["version"]
            or ready.get("requirements_sha256") != manage.file_hash(manage.PROJECT / "deployment/runpod/requirements-vllm.in")
            or not lock.is_file() or ready.get("lock_sha256") != manage.file_hash(lock)):
        raise RuntimeError("vLLM environment missing/stale; prepare it on CPU before GPU execution")


def require_prepared(config):
    require_environment(config)
    if manage.read_json(model_path(config) / "ready.json", {}).get("signature") != signature(config):
        raise RuntimeError("Pinned vLLM checkpoint/config is not prefetched; no download during inference")


def start(config=None):
    started = time.monotonic()
    config = settings() if config is None else config
    require_prepared(config)
    from manga_pipeline.runtime.inference_state import require_ready
    require_ready(manage.RUNTIME / "config/llm-inference-pause.json")
    with manage.lock("vllm-service", timeout=config["vllm"]["startup_timeout"] + 10):
        path = manage.RUNTIME / "config/vllm-service.json"
        record = manage.read_json(path, {})
        if owns(record):
            if record.get("signature") != signature(config):
                raise RuntimeError("vLLM is serving another frozen configuration; stop it explicitly")
            try:
                http("/health")
            except (OSError, ValueError):
                raise RuntimeError("Managed vLLM is alive but unhealthy; inspect logs, no automatic restart")
            return record
        from manga_pipeline.runtime.inference_state import process_group_alive, pause
        if record.get("identity") and process_group_alive(record["identity"].get("pid")):
            pause(manage.RUNTIME / "config/llm-inference-pause.json", request_key="orphan-vllm-workers",
                  reason="Managed server exited while its process group remains alive")
            raise RuntimeError("vLLM orphan workers are still alive; no new service was started")
        try:
            http("/v1/models")
        except (OSError, ValueError):
            pass
        else:
            raise RuntimeError("Port 8000 belongs to an unmanaged server; it was not changed")
        manage.validate_gpu_profile(config)
        env = manage.environment()
        env.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", VLLM_NO_USAGE_STATS="1")
        log = manage.RUNTIME / "logs/vllm.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("ab") as output:
            process = subprocess.Popen(command(config), cwd=manage.RUNTIME, env=env,
                stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
        identity = manage.service_identity(process.pid)
        if not identity:
            raise RuntimeError("vLLM exited during startup; inspect logs/vllm.log")
        record = {"identity": identity, "signature": signature(config), "command": command(config),
                  "model": config["llm"]["model"], "revision": config["vllm"]["revision"],
                  "version": config["vllm"]["version"], "cpu_offload_gb": 0, "max_model_len": config["llm"]["num_ctx"],
                  "max_num_seqs": config["vllm"]["max_num_seqs"]}
        manage.atomic_json(manage.RUNTIME / "config/vllm-process.json", identity)
        manage.atomic_json(path, record)
        deadline = time.monotonic() + config["vllm"]["startup_timeout"]
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError("vLLM startup failed; inspect logs/vllm.log. No GPU/context fallback")
            try:
                http("/health")
                from manga_pipeline.runtime.resources import event
                event("llm_service_start", backend="vllm", wall_seconds=time.monotonic()-started,
                      model=config["llm"]["model"], revision=config["vllm"]["revision"])
                return record
            except (OSError, ValueError):
                time.sleep(1)
        # Startup has not admitted any generation requests; reclaim only this process group.
        os.killpg(process.pid, signal.SIGTERM)
        raise RuntimeError("vLLM startup timed out; its task-owned process group was stopped")


def stop():
    started = time.monotonic()
    from manga_pipeline.runtime.inference_state import require_ready
    require_ready(manage.RUNTIME / "config/llm-inference-pause.json")
    with manage.lock("vllm-service", timeout=40):
        record = manage.read_json(manage.RUNTIME / "config/vllm-service.json", {})
        if not owns(record):
            from manga_pipeline.runtime.inference_state import process_group_alive, pause
            if record.get("identity") and process_group_alive(record["identity"].get("pid")):
                pause(manage.RUNTIME / "config/llm-inference-pause.json", request_key="orphan-vllm-workers",
                      reason="Visual admission blocked by orphan vLLM workers")
                raise RuntimeError("vLLM orphan process group still owns GPU memory; visual work is blocked")
            return
        identity = record["identity"]
        if os.getpgid(identity["pid"]) != identity["pid"]:
            raise RuntimeError("Managed vLLM process group identity changed")
        os.killpg(identity["pid"], signal.SIGTERM)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            from manga_pipeline.runtime.inference_state import process_group_alive
            if not process_group_alive(identity["pid"]):
                from manga_pipeline.runtime.resources import event
                event("llm_service_stop", backend="vllm", wall_seconds=time.monotonic()-started)
                return
            time.sleep(0.1)
        raise RuntimeError("vLLM has not stopped; visual GPU work is blocked")


def recover(config):
    """Explicit operator action after external shutdown; never kills uncertain work."""
    from manga_pipeline.runtime.inference_state import identity_alive, process_group_alive
    path = manage.RUNTIME / "config/llm-inference-pause.json"
    with manage.lock("pipeline"):
        record = manage.read_json(path, {})
        identity = record.get("server_identity")
        if record.get("status") != "paused" or not identity:
            raise RuntimeError("No identifiable paused request to recover")
        if identity_alive(identity) or process_group_alive(identity.get("pid")):
            raise RuntimeError("Previous server/process group is still alive; inspect and stop it explicitly first")
        manage.atomic_json(path, {**record, "status": "resolved_by_proven_operator_shutdown", "resolved_at": time.time()})
        return start(config)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["setup", "prefetch", "start", "stop", "recover"])
    parser.add_argument("--config", type=Path)
    args = parser.parse_args()
    if args.config:
        manage.activate_vllm(manage.configuration(args.config))
    config = settings()
    if args.command == "stop":
        stop()
    else:
        {"setup": setup, "prefetch": prefetch, "start": start, "recover": recover}[args.command](config)
