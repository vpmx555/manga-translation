"""Sequential Ollama inference with bounded requests and validated checkpoints."""
from __future__ import annotations

import hashlib
import json
import math
import socket
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from reasoning_schemas import VERSION, validate


class BudgetExceeded(ValueError):
    pass


class InvalidResponse(RuntimeError):
    pass


class ProviderError(RuntimeError):
    pass


class ResourceLimit(ProviderError):
    pass


@dataclass(frozen=True)
class ReasoningConfig:
    model: str = "gemma4:e4b-it-q4_K_M"
    host: str = "http://127.0.0.1:11434"
    num_ctx: int = 8192
    num_predict: int = 1024
    max_targets: int = 6
    overlap: int = 2
    max_images: int = 1
    max_identity_candidates: int = 24
    image_max_side: int = 768
    image_tokens: int = 2048
    reserve_tokens: int = 512
    text_bytes_per_token: float = 1.0
    timeout: float = 600
    retries: int = 1
    think: bool = False
    keep_alive: str = "5m"
    unload_after: bool = True

    def validate(self) -> None:
        for key in ("num_ctx", "num_predict", "max_targets", "max_images", "max_identity_candidates", "image_max_side",
                    "image_tokens", "reserve_tokens"):
            if type(getattr(self, key)) is not int or getattr(self, key) <= 0:
                raise ValueError(f"{key} must be a positive integer")
        import math
        if (type(self.overlap) is not int or type(self.retries) is not int or
                type(self.think) is not bool or type(self.unload_after) is not bool or
                type(self.timeout) not in (int, float) or not math.isfinite(self.timeout)):
            raise ValueError("Invalid configuration field types")
        if self.overlap < 1 or not 0 <= self.retries <= 3 or self.timeout <= 0:
            raise ValueError("Invalid overlap, retry count, or timeout")
        if self.num_predict + self.reserve_tokens >= self.num_ctx:
            raise ValueError("Context must leave room for input")
        if (type(self.text_bytes_per_token) not in (int, float) or not math.isfinite(self.text_bytes_per_token)
                or not 1 <= self.text_bytes_per_token <= 3):
            raise ValueError("text_bytes_per_token must be a finite estimate between 1 and 3")
        if (not isinstance(self.host, str) or not self.host.startswith(("http://", "https://")) or
                not isinstance(self.model, str) or not self.model):
            raise ValueError("Provide an HTTP Ollama host and model tag")


PROFILES = {
    "cpu": ReasoningConfig(),
    "gpu16": ReasoningConfig(model="qwen3.5:9b", num_ctx=16384, num_predict=2048,
                             max_targets=12, max_images=2, image_max_side=1024),
    "gpu48": ReasoningConfig(model="qwen3.5:27b", num_ctx=16384, num_predict=2048,
                             max_targets=16, max_images=3, image_max_side=1024),
}


def load_config(profile: str = "cpu", path: Path | None = None, **overrides) -> ReasoningConfig:
    if profile not in PROFILES:
        raise ValueError(f"Unknown profile: {profile}")
    values = {} if path is None else json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(values, dict) or set(values) - asdict(PROFILES[profile]).keys():
        raise ValueError("Config must contain only ReasoningConfig fields")
    values.update({key: value for key, value in overrides.items() if value is not None})
    config = replace(PROFILES[profile], **values)
    config.validate()
    return config


def atomic_json(path: Path, value: dict) -> None:
    """Write a complete checkpoint before replacing the previous checkpoint."""
    import os
    import tempfile
    data = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(data + "\n")
        # Windows readers/antivirus can briefly hold the destination without
        # delete sharing. Keep the old checkpoint intact and retry the rename.
        for attempt in range(10):
            try:
                os.replace(temporary, path)
                break
            except PermissionError:
                if attempt == 9:
                    raise
                time.sleep(min(0.05 * (2 ** attempt), 0.5))
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class OllamaReasoner:
    def __init__(self, config: ReasoningConfig, cache_dir: Path | None = None):
        config.validate()
        self.config, self.cache_dir = config, cache_dir
        self.digest = None
        self.events: list[dict] = []
        self.memory_samples: list[dict] = []

    def _http(self, endpoint: str, payload: dict | None = None) -> dict:
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(self.config.host.rstrip("/") + endpoint, data=data,
                                         headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=self.config.timeout) as response:
                result = json.load(response)
        except urllib.error.HTTPError as exc:
            message = exc.read(4096).decode("utf-8", errors="replace")
            error = ResourceLimit if any(x in message.lower() for x in
                    ("out of memory", "not enough memory", "context length", "memory required")) else ProviderError
            raise error(f"Ollama HTTP {exc.code}: {message}") from exc
        except (urllib.error.URLError, socket.timeout, TimeoutError) as exc:
            raise ProviderError(f"Ollama request failed: {exc}") from exc
        if not isinstance(result, dict) or result.get("error"):
            raise ProviderError(f"Invalid Ollama response: {result}")
        return result

    def prepare(self) -> None:
        models = self._http("/api/tags").get("models", [])
        record = next((m for m in models if m.get("name") == self.config.model or
                       m.get("model") == self.config.model), None)
        if record is None:
            raise ProviderError(f"Model {self.config.model!r} is not installed. Install the exact tag first; no automatic download.")
        self.digest = record.get("digest")
        if not self.digest:
            raise ProviderError("Ollama did not expose the model digest")
        capabilities = self._http("/api/show", {"model": self.config.model}).get("capabilities", [])
        if "vision" not in capabilities or (self.config.think and "thinking" not in capabilities):
            raise ProviderError("Selected model lacks requested vision/thinking capabilities")

    def budget(self, instruction: str, data: dict, schema: dict, images: list[dict]) -> int:
        # UTF-8 bytes deliberately overestimate text tokens; images require a configurable reserve.
        size = len((instruction + json.dumps(data, ensure_ascii=False) + json.dumps(schema)).encode("utf-8"))
        return (math.ceil(size / self.config.text_bytes_per_token) + len(images) * self.config.image_tokens
                + self.config.num_predict + self.config.reserve_tokens)

    def infer(self, task: str, instruction: str, data: dict, schema: dict,
              images: list[dict], validator=None) -> dict:
        if not self.digest:
            raise ProviderError("Call prepare() before inference")
        if len(images) > self.config.max_images or self.budget(instruction, data, schema, images) > self.config.num_ctx:
            raise BudgetExceeded(f"{task}: input, image reserve and output exceed context budget")
        request = {"model": self.config.model, "stream": False, "think": self.config.think,
                   "keep_alive": self.config.keep_alive, "format": schema,
                   "options": {"temperature": 0, "num_ctx": self.config.num_ctx,
                               "num_predict": self.config.num_predict},
                   "messages": [{"role": "system", "content": instruction},
                                {"role": "user", "content": json.dumps(data, ensure_ascii=False)}]}
        if images:
            request["messages"][1]["images"] = [x["data"] for x in images]
        key = hashlib.sha256(json.dumps({"version": VERSION, "digest": self.digest,
                             "task": task, "request": request, "image_refs": [x["ref"] for x in images]},
                             sort_keys=True).encode()).hexdigest()
        cache = self.cache_dir / (key + ".json") if self.cache_dir is not None else None
        if cache is not None and cache.exists():
            try:
                entry = json.loads(cache.read_text(encoding="utf-8"))
                if entry.get("key") != key:
                    raise ValueError("Cache key mismatch")
                validate(entry["result"], schema)
                if validator:
                    validator(entry["result"])
                self.events.append({"task": task, "key": key, "cache_hit": True, "usage": entry.get("usage", {})})
                return entry["result"]
            except (ValueError, KeyError, TypeError):
                pass  # A corrupt or semantically invalid checkpoint is never trusted.
        for attempt in range(self.config.retries + 1):
            started = time.perf_counter()
            try:
                response = self._http("/api/chat", request)
            except ProviderError as exc:
                self.events.append({"task": task, "key": key, "cache_hit": False,
                                    "elapsed_seconds": time.perf_counter() - started, "error": str(exc)})
                raise
            usage = {k: response.get(k) for k in ("prompt_eval_count", "eval_count", "total_duration",
                                                   "load_duration", "prompt_eval_duration", "eval_duration")}
            event = {"task": task, "key": key, "cache_hit": False, "attempt": attempt,
                     "elapsed_seconds": time.perf_counter() - started, "usage": usage}
            self.events.append(event)
            if (response.get("prompt_eval_count") or 0) + self.config.num_predict + self.config.reserve_tokens > self.config.num_ctx:
                event["error"] = "Actual prompt usage exceeded reserved context"
                raise ResourceLimit(event["error"])
            try:
                if response.get("done") is not True or response.get("done_reason") == "length":
                    raise ValueError("Output was truncated")
                result = json.loads(response["message"]["content"])
                validate(result, schema)
                if validator:
                    validator(result)
            except (ValueError, KeyError, TypeError) as exc:
                event["error"] = str(exc)
                if attempt == self.config.retries:
                    raise InvalidResponse(f"{task}: {exc}") from exc
                correction = " Validator correction: " + str(exc)[:240] + ". Fix this constraint in every row; return the exact schema."
                corrected_instruction = request["messages"][0]["content"] + correction
                if self.budget(corrected_instruction, data, schema, images) > self.config.num_ctx:
                    raise BudgetExceeded("Repair instructions no longer fit the context budget") from exc
                request["messages"][0]["content"] = corrected_instruction
                event["repair_requested"] = True
                continue
            if cache is not None:
                atomic_json(cache, {"key": key, "result": result, "usage": usage})
            try:
                for model in self._http("/api/ps").get("models", []):
                    if model.get("name") == self.config.model or model.get("model") == self.config.model:
                        self.memory_samples.append({"task": task, "size_bytes": model.get("size"),
                                                    "vram_bytes": model.get("size_vram"),
                                                    "context_length": model.get("context_length")})
            except ProviderError:
                pass  # Optional sampled server memory, not a peak-memory guarantee.
            return result
        raise AssertionError("Unreachable")

    def close(self) -> None:
        if self.digest and self.config.unload_after:
            self._http("/api/generate", {"model": self.config.model, "stream": False, "keep_alive": 0})
