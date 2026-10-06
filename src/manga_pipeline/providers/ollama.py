"""Schema-checked local text/VLM inference with thinking disabled."""
import json
import copy
import time
import urllib.error
import urllib.request
from pathlib import Path

from ..storage.io import atomic_json, fingerprint, read_json


class Ollama:
    def __init__(self, config, cache_dir):
        self.config = config
        self.cache_dir = Path(cache_dir)
        self.digest = None
        self.used = False

    def http(self, route, body=None):
        request = urllib.request.Request(self.config["host"].rstrip("/") + route,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=self.config["timeout"]) as response:
            return json.load(response)

    def infer_once(self, task, prompt, data, schema, validator, images=None):
        """Caller owns the v3 repair budget; never multiply retries internally."""
        return self.infer(task, prompt, data, schema, validator, images, retries=0)

    def infer(self, task, prompt, data, schema, validator, images=None, *, retries=None):
        images = images or []
        if self.digest is None:
            models = self.http("/api/tags").get("models", [])
            model = next((m for m in models if m.get("name") == self.config["model"]), None)
            if model is None:
                raise RuntimeError(f"Ollama model is not installed: {self.config['model']}")
            self.digest = model["digest"]
        reserve = 2048 if images else 0
        estimate = len(json.dumps(data).encode()) / 2 + len(prompt) / 2 + reserve + self.config["num_predict"] + 512
        if estimate > self.config["num_ctx"]:
            raise ValueError("Request exceeds context budget; shorten source text/config or use a larger context")
        request = {"model": self.config["model"], "stream": False, "think": False,
                   "keep_alive": "5m", "format": schema,
                   "options": {"num_ctx": self.config["num_ctx"], "num_predict": self.config["num_predict"],
                               "temperature": 0},
                   "messages": [{"role": "system", "content": prompt},
                                {"role": "user", "content": json.dumps(data, ensure_ascii=False),
                                 **({"images": images} if images else {})}]}
        key = fingerprint({"digest": self.digest, "task": task, "request": request})
        path = self.cache_dir / (key + ".json")
        cache_validator = getattr(validator, "validate_cache", validator)
        if path.exists():
            try:
                cached = read_json(path)
                if cached.get("key") == key and cached.get("result_hash") == fingerprint(cached["result"]):
                    validator(cached["result"])
                    cache_validator(cached["result"])
                    return cached["result"]
            except (ValueError, KeyError, TypeError):
                # Model cache is disposable; rejected output must allow fresh inference.
                pass
        error = None
        retry_count = self.config["retries"] if retries is None else retries
        for attempt in range(retry_count + 1):
            try:
                self.used = True
                attempt_request = request
                if task == "classify_dialogue" and attempt and isinstance(error, (ValueError, KeyError, TypeError)):
                    attempt_request = copy.deepcopy(request)
                    attempt_request["messages"][0]["content"] += (
                        " Previous response was rejected: " + str(error)[:200] +
                        ". Correct this error while preserving all target/name coverage. Return JSON only.")
                response = self.http("/api/chat", attempt_request)
                if response.get("done") is not True or response.get("done_reason") == "length":
                    raise ValueError("Incomplete model response")
                if (response.get("prompt_eval_count") or 0) + self.config["num_predict"] + 512 > self.config["num_ctx"]:
                    raise ValueError("Actual prompt exceeded context reserve")
                result = json.loads(response["message"]["content"])
                validator(result)
                try:
                    cache_validator(result)
                except (ValueError, KeyError, TypeError):
                    # A mixed dialogue batch still returns so good targets can commit.
                    # Failed targets are repaired by the caller, never cached as success.
                    pass
                else:
                    atomic_json(path, {"key": key, "result": result, "result_hash": fingerprint(result),
                                       "usage": {k: response.get(k) for k in
                                                 ("prompt_eval_count", "eval_count", "total_duration")}})
                return result
            except (ValueError, KeyError, TypeError, OSError) as exc:
                error = exc
                if attempt < retry_count:
                    time.sleep(0.2)
        raise RuntimeError(f"{task} failed after retry: {error}") from error

    def close(self):
        if self.used:
            try:
                self.http("/api/generate", {"model": self.config["model"], "keep_alive": 0})
            except (OSError, ValueError):
                pass
