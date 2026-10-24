"""Fingerprintable runtime limits, fair cross-process GPU leases and telemetry."""
import copy
import json
import os
import subprocess
import threading
import time
import urllib.request
import uuid
from contextlib import contextmanager
from pathlib import Path

from ..storage.io import atomic_json, read_json, fingerprint
from ..storage.locking import exclusive_lock
from .llm_budget import BudgetExceeded, OutputTruncated, input_estimate
from .inference_state import server_identity, server_replaced, require_ready, recovery_proven


# Starting bounds, not benchmark results. Override only in the frozen config.
PRESETS = {
    "l40s": {"story_workers": 1, "llm_slots": 8, "typeset_workers": 2, "num_thread": 8, "cpu_threads": 4},
    "a40": {"story_workers": 3, "llm_slots": 2, "typeset_workers": 2, "num_thread": 8, "cpu_threads": 4},
    "rtx4090": {"story_workers": 3, "llm_slots": 2, "typeset_workers": 2, "num_thread": 8, "cpu_threads": 4},
    "rtx5090": {"story_workers": 3, "llm_slots": 2, "typeset_workers": 3, "num_thread": 8, "cpu_threads": 4},
}
DEFAULT_CONFIG = {
    "enabled": False, "preset": "l40s", "story_workers": 1, "llm_slots": 64,
    "typeset_workers": None, "num_thread": None, "cpu_threads": None,
    "adaptive_batches": True, "analyze_max_batch": 8, "translation_max_batch": 8,
    "context_fraction": 0.85, "recovery_attempts": 3,
    "gate_timeout": 3600, "gpu_sample_seconds": 10,
    "required_gpu": None,
}


class ResourceExhausted(RuntimeError):
    """An explicit allocator/OOM failure; not a semantic unknown."""


def is_oom(error):
    text = str(error).lower()
    return any(term in text for term in ("out of memory", "cuda_error_out_of_memory",
        "cuda error: memory allocation", "failed to allocate", "unable to allocate", "not enough memory"))


def validate_config(config):
    if not isinstance(config, dict) or set(config) != set(DEFAULT_CONFIG):
        raise ValueError("Invalid runtime configuration fields")
    for key in ("enabled", "adaptive_batches"):
        if type(config[key]) is not bool:
            raise ValueError(f"runtime.{key} must be boolean")
    if config["preset"] not in PRESETS:
        raise ValueError("Unknown runtime GPU preset")
    if config["required_gpu"] is not None and config["required_gpu"] not in {"RTX 4090", "RTX 5090", "L40S"}:
        raise ValueError("runtime.required_gpu must match a supported GPU")
    limits = {"story_workers": 8, "llm_slots": 64, "typeset_workers": 16,
              "num_thread": 32, "cpu_threads": 32}
    for key, maximum in limits.items():
        value = config[key]
        if value is not None and (type(value) is not int or not 1 <= value <= maximum):
            raise ValueError(f"runtime.{key} must be null or 1..{maximum}")
    for key in ("analyze_max_batch", "translation_max_batch"):
        if type(config[key]) is not int or not 1 <= config[key] <= 8:
            raise ValueError(f"runtime.{key} must be 1..8")
    if type(config["context_fraction"]) not in (int, float) or not 0.5 <= config["context_fraction"] <= 0.95:
        raise ValueError("runtime.context_fraction must be 0.5..0.95")
    if type(config["recovery_attempts"]) is not int or not 0 <= config["recovery_attempts"] <= 3:
        raise ValueError("runtime.recovery_attempts must be 0..3")
    for key, minimum, maximum in (("gate_timeout", 30, 7200), ("gpu_sample_seconds", 5, 60)):
        if type(config[key]) is not int or not minimum <= config[key] <= maximum:
            raise ValueError(f"Invalid runtime.{key}")


def resolved(config):
    result = copy.deepcopy(DEFAULT_CONFIG)
    result.update(config.get("runtime", {}))
    validate_config(result)
    for key, value in PRESETS[result["preset"]].items():
        if result[key] is None:
            result[key] = value
    # Leave CPU headroom for driver, server, sync and checkpoint publication.
    available = max(1, (os.cpu_count() or 2) - 2)
    result["typeset_workers"] = min(result["typeset_workers"], max(1, available // result["story_workers"]))
    result["cpu_threads"] = min(result["cpu_threads"], max(1, available // result["story_workers"]))
    result["num_thread"] = min(result["num_thread"], max(1, available // result["llm_slots"]))
    return result


def _identity():
    identity = {"pid": os.getpid()}
    if os.name != "nt":
        try:
            identity["start"] = Path(f"/proc/{os.getpid()}/stat").read_text().rsplit(")", 1)[1].split()[19]
        except OSError:
            pass
    return identity


def _alive(record):
    try:
        if os.name == "nt":
            # This coordinator is used on Linux Runpod; refuse unsupported sharing.
            return record["pid"] == os.getpid()
        stat = Path(f"/proc/{record['pid']}/stat").read_text().rsplit(")", 1)[1].split()
        return stat[0] != "Z" and ("start" not in record or record["start"] == stat[19])
    except (OSError, KeyError, IndexError):
        return False


def configure_batch(config, state_parent):
    limits = resolved(config)
    if not limits["enabled"]:
        return {}
    if os.name == "nt":
        raise RuntimeError("GPU batch coordination requires Linux; prepare config locally, do not run the batch on Windows")
    if os.environ.get("MANGA_LLM_PAUSE_FILE"):
        require_ready(os.environ["MANGA_LLM_PAUSE_FILE"])
    audit = Path(state_parent).resolve() / "runtime"
    audit.mkdir(parents=True, exist_ok=True)
    # High-frequency admission stays on execution storage; telemetry is retained in jobs.
    root = Path(os.environ.get("MANGA_EXECUTION_ROOT", str(state_parent))) / "coordination" / fingerprint(str(audit))[:16]
    root.mkdir(parents=True, exist_ok=True)
    state_path = root / "gpu-state.json"
    with exclusive_lock(root / "gpu-state.lock"):
        previous = read_json(state_path) if state_path.exists() else {}
        live = [r for k in ("active", "waiting") for r in previous.get(k, []) if _alive(r)]
        if live:
            raise RuntimeError("Previous GPU workers are still alive; do not start a duplicate batch")
        pause_file = os.environ.get("MANGA_LLM_PAUSE_FILE")
        recovered = server_replaced(previous.get("server_identity")) or (
            pause_file and recovery_proven(previous.get("server_identity"), pause_file))
        if (previous.get("fault") or previous.get("active")) and not recovered:
            raise RuntimeError("GPU coordinator paused after uncertain inference; explicit recovery is required")
        recover_server = False
        effective = min(limits["llm_slots"], previous.get("max_slots", limits["llm_slots"]))
        atomic_json(state_path, {"active": [], "waiting": [], "max_slots": effective,
                                 "server_identity": server_identity()})
    atomic_json(audit / "effective-config.json", {"requested": config.get("runtime", {}),
        "resolved": limits, "effective_llm_slots": effective, "recorded_at": time.time(),
        "coordinator": str(root),
        "preset_status": "initial bounded configuration, not benchmarked"})
    return {"MANGA_GPU_COORDINATOR": str(root), "MANGA_RUNTIME_TELEMETRY": str(audit), "MANGA_PARALLEL_BATCH": "1",
        "MANGA_GPU_RECOVER_SERVER": "1" if recover_server else "0",
        "MANGA_STORY_WORKERS": str(limits["story_workers"]),
        "MANGA_TYPESET_WORKERS": str(limits["typeset_workers"]),
        "MANGA_LLM_THREADS": str(limits["num_thread"]),
        "MANGA_GPU_GATE_TIMEOUT": str(limits["gate_timeout"]),
        "MANGA_GPU_RECOVERY_ATTEMPTS": str(limits["recovery_attempts"]),
        "MANGA_LLM_MODEL": config["llm"]["model"], "MANGA_LLM_HOST": config["llm"]["host"],
        "OMP_NUM_THREADS": str(limits["cpu_threads"]), "MKL_NUM_THREADS": str(limits["cpu_threads"]),
        "OPENBLAS_NUM_THREADS": str(limits["cpu_threads"])}


def event(name, **fields):
    root = os.environ.get("MANGA_RUNTIME_TELEMETRY")
    if not root:
        return
    value = {"event": name, "time": time.time(), "pid": os.getpid(),
             "thread": threading.get_ident(), **fields}
    # Each process owns its telemetry stream; thread writes are serialized.
    with _event_lock:
        path = Path(root) / f"events-{os.getpid()}.jsonl"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(value, ensure_ascii=False) + "\n")


_event_lock = threading.Lock()


def quarantine(reason):
    """A lost HTTP client may leave server inference alive; prevent visual admission."""
    root = os.environ.get("MANGA_GPU_COORDINATOR")
    if not root:
        return
    root = Path(root)
    with exclusive_lock(root / "gpu-state.lock"):
        state = read_json(root / "gpu-state.json")
        state["fault"] = str(reason)[:500]
        state["server_identity"] = server_identity()
        atomic_json(root / "gpu-state.json", state)
    event("gpu_quarantined", reason=str(reason)[:500])


def reduce_slots(reason):
    root = os.environ.get("MANGA_GPU_COORDINATOR")
    if not root:
        return
    root = Path(root)
    with exclusive_lock(root / "gpu-state.lock"):
        state = read_json(root / "gpu-state.json")
        before = state["max_slots"]
        state["max_slots"] = max(1, before - 1)
        atomic_json(root / "gpu-state.json", state)
    event("resource_backoff", previous_slots=before, effective_slots=state["max_slots"], reason=str(reason)[:300])


def _unload_llm():
    if os.environ.get("MANGA_LLM_BACKEND") == "vllm":
        import sys
        subprocess.run([sys.executable, os.environ["MANGA_LLM_MANAGER"], "stop"],
                       check=True, stdin=subprocess.DEVNULL, timeout=45)
        return
    raise RuntimeError("Only managed vLLM can release the GPU")


@contextmanager
def gpu_lease(kind):
    """FIFO: one visual owner, or bounded consecutive LLM owners, never both."""
    root = os.environ.get("MANGA_GPU_COORDINATOR")
    if not root:
        yield
        return
    if kind not in {"visual", "llm"}:
        raise ValueError("Unknown GPU lease kind")
    root = Path(root)
    token = uuid.uuid4().hex
    waiter = {**_identity(), "token": token, "kind": kind}
    begin = time.monotonic()
    acquired = False
    cpu_begin = None
    try:
        while not acquired:
            with exclusive_lock(root / "gpu-state.lock"):
                state = read_json(root / "gpu-state.json")
                before = copy.deepcopy(state)
                if any(not _alive(r) for r in state["active"]):
                    state["fault"] = "GPU lease owner exited; server/visual worker state is uncertain"
                    atomic_json(root / "gpu-state.json", state)
                if state.get("fault"):
                    raise RuntimeError("GPU coordinator stopped after a systemic failure: " + state["fault"])
                state["waiting"] = [r for r in state["waiting"] if _alive(r)]
                if not any(r["token"] == token for r in state["waiting"]):
                    state["waiting"].append(waiter)
                position = next(i for i, r in enumerate(state["waiting"]) if r["token"] == token)
                if kind == "visual":
                    acquired = not state["active"] and position == 0
                else:
                    acquired = (all(r["kind"] == "llm" for r in state["active"])
                        and len(state["active"]) < state["max_slots"]
                        and all(r["kind"] == "llm" for r in state["waiting"][:position]))
                if acquired:
                    state["waiting"] = [r for r in state["waiting"] if r["token"] != token]
                    state["active"].append(waiter)
                if state != before:
                    atomic_json(root / "gpu-state.json", state)
            if not acquired:
                if time.monotonic() - begin > int(os.environ.get("MANGA_GPU_GATE_TIMEOUT", "3600")):
                    raise TimeoutError("GPU queue wait exceeded configured limit")
                time.sleep(0.1)
        admitted = time.monotonic()
        cpu_begin = time.thread_time()
        event("gpu_admitted", kind=kind, token=token, queue_seconds=admitted - begin)
        if kind == "visual":
            _unload_llm()
        yield
    finally:
        with exclusive_lock(root / "gpu-state.lock"):
            state = read_json(root / "gpu-state.json")
            for key in ("active", "waiting"):
                state[key] = [r for r in state[key] if r["token"] != token]
            atomic_json(root / "gpu-state.json", state)
        if acquired:
            event("gpu_released", kind=kind, token=token, wall_seconds=time.monotonic() - admitted,
                  cpu_thread_seconds=time.thread_time() - cpu_begin)


class GPUMonitor:
    """One batch-level sampler; missing nvidia-smi is recorded, not hidden."""
    def __init__(self, config):
        self.config = resolved(config)
        self.stop = threading.Event()
        self.thread = None

    def __enter__(self):
        if self.config["enabled"]:
            self.thread = threading.Thread(target=self._sample, daemon=True, name="gpu-telemetry")
            self.thread.start()
        return self

    def _sample(self):
        while not self.stop.is_set():
            try:
                result = subprocess.run(["nvidia-smi", "--query-gpu=index,name,utilization.gpu,memory.used,memory.total,power.draw",
                    "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=3)
                event("gpu_sample", rows=result.stdout.strip().splitlines(), exit_code=result.returncode,
                      error=result.stderr.strip()[:300], load_average=list(os.getloadavg()))
            except (OSError, subprocess.TimeoutExpired) as exc:
                event("gpu_sample_error", error=str(exc)[:300])
            self.stop.wait(self.config["gpu_sample_seconds"])

    def __exit__(self, *args):
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=4)


def adaptive_limits(store, stage, configured_size):
    limits = resolved(store.manifest["config"])
    enabled = limits["enabled"] and limits["adaptive_batches"]
    return limits, min(configured_size, limits[f"{stage}_max_batch"]) if enabled else configured_size
