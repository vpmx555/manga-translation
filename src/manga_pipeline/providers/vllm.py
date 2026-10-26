"""Managed vLLM JSON inference with exact chat token budgets and no engine fallback."""
import json
import copy
import math
import os
from pathlib import Path
import subprocess
import sys
import time
import threading
import re
import urllib.error
import urllib.request

from ..storage.io import atomic_json, fingerprint, read_json
from ..runtime.resources import BudgetExceeded, ResourceExhausted, event, gpu_lease, is_oom, quarantine
from ..runtime.llm_budget import OutputTruncated, final_prompt, output_tokens
from ..runtime.stage_policy import request_policy, server_context
from ..runtime.inference_state import InferenceUncertain, pause, require_ready, server_identity, server_replaced


class VLLM:
    def __init__(self, config, cache_dir):
        self.pipeline_config = copy.deepcopy(config)
        self.config = self.pipeline_config["llm"]
        self.engine = self.pipeline_config["vllm"]
        self.cache_dir = Path(cache_dir)
        self.digest = self.config["model"] + "@" + self.engine["revision"]
        self._token_counts = {}
        self._token_lock = threading.RLock()
        self._http_options = threading.local()
        self.reduce_admission = lambda: None
        self.pause_path = Path(os.environ.get("MANGA_LLM_PAUSE_FILE", str(self.cache_dir / "inference-pause.json")))

    def _http(self, route, payload=None):
        body = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None
        request = urllib.request.Request(self.config["host"].rstrip("/") + route,
            data=body, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=getattr(self._http_options, 'timeout', self.config['timeout'])) as response:
                return json.load(response)
        except json.JSONDecodeError as exc:
            raise RuntimeError("vLLM: server returned malformed JSON") from exc
        except urllib.error.HTTPError as exc:
            detail = exc.read(4096).decode("utf-8", errors="replace")
            if is_oom(detail):
                raise ResourceExhausted(detail) from exc
            if exc.code == 400 and any(t in detail.lower() for t in ("maximum context", "max_model_len", "too many tokens")):
                raise BudgetExceeded("vLLM context budget rejected: " + detail) from exc
            raise RuntimeError(f"vLLM: HTTP {exc.code}: {detail}") from exc

    def _ready(self):
        require_ready(self.pause_path)
        controller = os.environ.get("MANGA_LLM_MANAGER")
        if (os.environ.get("MANGA_LLM_BACKEND") != "vllm" or not controller
                or not os.environ.get("MANGA_GPU_COORDINATOR")):
            raise RuntimeError("vLLM: the full-GPU profile requires the managed coordinator and service controller")
        try:
            subprocess.run([sys.executable, controller, "start"], check=True,
                           stdin=subprocess.DEVNULL, timeout=self.engine["startup_timeout"] + 30)
        except (subprocess.SubprocessError, OSError) as exc:
            raise RuntimeError("vLLM: managed service could not become ready; inspect its log") from exc
        record_path = os.environ.get("MANGA_LLM_SERVICE_RECORD")
        record = read_json(Path(record_path)) if record_path else {}
        if (not isinstance(record, dict) or record.get("model") != self.config["model"] or record.get("revision") != self.engine["revision"]
                or record.get("version") != self.engine["version"] or record.get("cpu_offload_gb") != 0
                 or record.get("max_model_len") != server_context(self.pipeline_config)
                or record.get("max_num_seqs") != self.engine["max_num_seqs"]):
            raise RuntimeError("vLLM: service revision/profile differs from the provider's frozen configuration")
        rows = self._http("/v1/models").get("data", [])
        if not any(r.get("id") == self.config["model"] and r.get("max_model_len") == server_context(self.pipeline_config) for r in rows):
            raise RuntimeError("vLLM: served model/context differs from the frozen configuration")

    def _messages(self, task, prompt, data, schema, images=None):
        system = final_prompt(task, prompt) + "\nJSON schema:\n" + json.dumps(schema, ensure_ascii=False, sort_keys=True)
        content = json.dumps(data, ensure_ascii=False, sort_keys=True)
        if images:
            content = [{"type": "text", "text": content}] + [
                {"type": "image_url", "image_url": {"url": image if image.startswith("data:") else "data:image/png;base64," + image}}
                for image in images]
        return [{"role": "system", "content": system}, {"role": "user", "content": content}]

    def request_budget(self, task, prompt, data, schema, images=None, *, reserve=None):
        messages = self._messages(task, prompt, data, schema, images)
        policy = request_policy(self.pipeline_config, task)
        key = fingerprint([self.digest, self.engine, messages, policy])
        with self._token_lock:
            count = self._token_counts.get(key)
            if count is None:
                with gpu_lease("llm"):
                    self._ready()
                    value = self._http("/tokenize", {"model": self.config["model"],
                        "messages": messages, "add_generation_prompt": True,
                        "chat_template_kwargs": {"enable_thinking": policy['enable_thinking']}})
                count = value.get("count")
                if type(count) is not int or count <= 0:
                    raise RuntimeError("vLLM: tokenizer did not return a valid chat token count")
                self._token_counts[key] = count
        reserve = output_tokens(self.pipeline_config, task) if reserve is None else reserve
        fraction = self.pipeline_config.get("runtime", {}).get("context_fraction", 0.85)
        return {"estimate": count + reserve + 512, "input_tokens": count,
                "limit": math.floor(policy['num_ctx'] * fraction),
                "num_ctx": policy['num_ctx'], "output_reserve": reserve,
                "method": "vllm-tokenize-chat-with-schema-v1"}

    def infer_once(self, task, prompt, data, schema, validator, images=None):
        return self.infer(task, prompt, data, schema, validator, images, retries=0)

    def infer_reserved(self, task, prompt, data, schema, validator, *, reserve):
        return self.infer(task, prompt, data, schema, validator, retries=0, reserve=reserve)

    def healthy(self):
        require_ready(self.pause_path)
        rows = self._http("/v1/models").get("data", [])
        return any(r.get("id") == self.config["model"] and r.get("max_model_len") == server_context(self.pipeline_config) for r in rows)

    def metrics(self):
        request = urllib.request.Request(self.config["host"].rstrip("/") + "/metrics")
        with urllib.request.urlopen(request, timeout=3) as response:
            lines = response.read(2_000_000).decode().splitlines()
        def value(names, aggregate=sum):
            for name in names:
                values = [float(line.rsplit(" ", 1)[1]) for line in lines
                          if re.match(r"^vllm:" + name + r"(?:\{| )", line)]
                if values:
                    return aggregate(values)
            return None
        return {"kv": value(["kv_cache_usage_perc", "gpu_cache_usage_perc"], max),
                "preemptions": value(["num_preemptions_total"]),
                "generated_tokens": value(["generation_tokens_total"]),
                "completed_requests": value(["request_success_total"])}

    def infer(self, task, prompt, data, schema, validator, images=None, *, retries=None, reserve=None):
        require_ready(self.pause_path)
        policy = request_policy(self.pipeline_config, task)
        payload = {"model": self.config["model"], "stream": False,
            "messages": self._messages(task, prompt, data, schema, images),
            "max_tokens": output_tokens(self.pipeline_config, task) if reserve is None else reserve,
            "temperature": policy['temperature'], "top_k": self.config.get("top_k", 20),
            "top_p": self.config.get("top_p", 0.95), "presence_penalty": policy['presence_penalty'],
            "chat_template_kwargs": {"enable_thinking": policy['enable_thinking']},
            "response_format": {"type": "json_schema", "json_schema": {"name": "manga_result", "strict": True, "schema": schema}}}
        key = fingerprint({"backend": "vllm", "version": self.engine["version"], "digest": self.digest,
                           "request": payload, "stage_policy": policy})
        cache_path, ledger_path = self.cache_dir / (key + ".json"), self.cache_dir / "timeouts" / (key + ".json")
        cache_validator = getattr(validator, "validate_cache", validator)
        if cache_path.exists():
            try:
                cached = read_json(cache_path)
                if not isinstance(cached, dict):
                    raise ValueError("Malformed disposable model cache")
                if cached.get("key") == key and cached.get("result_hash") == fingerprint(cached["result"]):
                    validator(cached["result"])
                    cache_validator(cached["result"])
                    event("llm_cache_hit", task=task, key=key, backend="vllm")
                    return cached["result"]
            except (KeyError, ValueError, TypeError, OSError):
                pass
        ledger = read_json(ledger_path) if ledger_path.exists() else {}
        if ledger_path.exists() and (not isinstance(ledger, dict) or ledger.get("request_key") != key
                or ledger.get("status") not in {"started", "uncertain", "completed", "terminated_error"}):
            raise InferenceUncertain("vLLM: malformed generation receipt; duplicate blocked")
        if ledger.get("status") == "completed" and ("response" not in ledger
                or ledger.get("response_hash") != fingerprint(ledger["response"])):
            raise InferenceUncertain("vLLM: completed response receipt is corrupt; duplicate blocked")
        if ledger.get("oom_failures", 0) >= 2:
            raise ResourceExhausted("vLLM: persisted OOM retry budget exhausted")
        if ledger.get("status") in {"started", "uncertain", "retry_started"}:
            raise InferenceUncertain("vLLM: request completion is uncertain; duplicate blocked")
        budget = self.request_budget(task, prompt, data, schema, images, reserve=reserve)
        if budget["estimate"] > budget["limit"]:
            raise BudgetExceeded(f"vLLM context budget: {budget['estimate']} > {budget['limit']}")
        event("llm_budget", task=task, backend="vllm", targets=len(data.get("targets", [])), **budget)
        started = time.monotonic()
        admitted = started
        response = ledger.get("response") if ledger.get("status") == "completed" else None
        replayed = response is not None
        if response is None:
            with gpu_lease("llm"):
                admitted = time.monotonic()
                self._ready()
                if ledger.get("status") in {"started", "uncertain", "retry_started"}:
                    raise InferenceUncertain("vLLM: interrupted POST cannot be duplicated")
                ledger = {**ledger, "request_key": key, "status": "started", "server_identity": server_identity(), "started_at": time.time()}
                atomic_json(ledger_path, ledger)
                post_started = time.monotonic()
                try:
                    self._http_options.timeout = policy['timeout']
                    try:
                        response = self._http("/v1/chat/completions", payload)
                    finally:
                        del self._http_options.timeout
                except (ResourceExhausted, BudgetExceeded, RuntimeError) as exc:
                    atomic_json(ledger_path, {**ledger, "status": "terminated_error", "error": str(exc),
                        "oom_failures": ledger.get("oom_failures", 0) + int(isinstance(exc, ResourceExhausted)),
                        "completed_at": time.time()})
                    raise
                except OSError as exc:
                    atomic_json(ledger_path, {**ledger, "status": "uncertain", "timeouts": ledger.get("timeouts", 0) + 1,
                        "server_identity": server_identity(), "error": str(exc)[:500]})
                    pause(self.pause_path, request_key=key, reason=exc)
                    quarantine(exc)
                    raise InferenceUncertain("vLLM: request completion is uncertain; queue paused") from exc
                inference_seconds = time.monotonic()-post_started
                atomic_json(ledger_path, {**ledger, "status": "completed", "response": response,
                                         "response_hash": fingerprint(response), "completed_at": time.time(),
                                         "inference_seconds": inference_seconds})
        usage = response.get("usage", {})
        event("llm_response_replay" if replayed else "llm_request", task=task, backend="vllm",
              queue_seconds=admitted-started, wall_seconds=time.monotonic()-started,
              inference_seconds=0 if replayed else inference_seconds, usage=usage)
        choices = response.get("choices", [])
        if not choices or choices[0].get("finish_reason") != "stop":
            raise OutputTruncated("vLLM output incomplete; split targets without accepting partial JSON")
        if usage.get("prompt_tokens", budget["input_tokens"]) + payload["max_tokens"] + 512 > policy['num_ctx']:
            raise BudgetExceeded("vLLM actual prompt exceeded context reserve")
        try:
            result = json.loads(choices[0]["message"]["content"])
            validator(result)
            try:
                cache_validator(result)
            except (ValueError, TypeError, KeyError) as exc:
                atomic_json(self.cache_dir / "rejected" / (key + ".json"), {"key": key, "task": task,
                    "result": result, "error": str(exc), "cache_accepted": False, "usage": usage})
            else:
                atomic_json(cache_path, {"key": key, "result": result, "result_hash": fingerprint(result), "usage": usage})
            return result
        except (ValueError, TypeError, KeyError) as exc:
            atomic_json(self.cache_dir / "rejected" / (key + "-invalid.json"),
                        {"key": key, "task": task, "response": response, "error": str(exc)})
            raise

    def close(self):
        # Stage ownership releases the service before visual GPU work. Never stop
        # a server here when request completion may still be uncertain.
        pass
