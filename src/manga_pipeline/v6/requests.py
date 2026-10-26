"""Durable request receipts. No retry can erase an uncertain POST."""
import inspect
import json
import threading
import time

from ..runtime.inference_state import InferenceUncertain
from ..runtime.llm_budget import BudgetExceeded, OutputTruncated, measured_budget
from ..runtime.resources import ResourceExhausted, event
from ..storage.io import atomic_json, fingerprint, read_json


class Calls:
    def __init__(self, store, provider, stage="analyze", *, allow_uncertain=False):
        self.store, self.provider = store, provider
        self.config = store.manifest["config"]
        self.lock = threading.RLock()
        self.paused = threading.Event()
        self.directory = store.artifact_path(stage).parent / "requests"
        for path in self.directory.glob("*.json"):
            receipt = read_json(path)
            if (not isinstance(receipt, dict) or receipt.get("request_id") != path.stem
                    or receipt.get("status") not in {"response", "truncated", "started", "uncertain", "failed", "oom", "budget_exceeded"}):
                raise RuntimeError("Malformed request receipt; queue cannot resume: " + path.name)
            if receipt.get("status") in {"started", "uncertain"}:
                if not allow_uncertain:
                    raise InferenceUncertain("Analyze queue paused by interrupted receipt: " + path.name)
                # New single-pass stages keep this request blocked in call(),
                # but may resume other never-sent jobs after service recovery.

    def budget(self, task, prompt, request, schema, reserve, images=None):
        started = time.monotonic()
        method = getattr(self.provider, "request_budget", None)
        if callable(method) and "reserve" in inspect.signature(method).parameters:
            report = method(task, prompt, request, schema, reserve=reserve,
                            **({"images": images} if images else {}))
        else:
            report = measured_budget(self.provider, self.config, task, prompt, request, schema,
                                     **({"images": images} if images else {}))
            report = {**report, "estimate": report["estimate"] - report["output_reserve"] + reserve,
                      "output_reserve": reserve}
        event("analyze_tokenize", seconds=time.monotonic()-started, **report)
        return report

    def call(self, key, task, prompt, request, schema, *, reserve, attempt=0, oom_retry=True, images=None):
        if self.paused.is_set():
            raise InferenceUncertain("Analyze queue paused after uncertain request completion")
        from ..runtime.stage_policy import request_policy
        identity = fingerprint([key, task, prompt, request, schema, reserve, attempt,
                                request_policy(self.config, task)])
        if images:
            identity = fingerprint([identity, images])
        path = self.directory / (identity + ".json")
        with self.lock:
            receipt = read_json(path) if path.exists() else {}
            if receipt.get("status") == "response":
                if receipt.get("response_hash") != fingerprint(receipt["response"]):
                    raise RuntimeError("Corrupt completed response receipt: " + identity)
                return receipt["response"]
            if receipt.get("status") == "truncated":
                raise OutputTruncated(receipt["error"])
            if receipt.get("status") in {"started", "uncertain"}:
                raise InferenceUncertain("Interrupted request cannot be duplicated: " + identity)
            if receipt.get("status") == "failed":
                raise RuntimeError(receipt["error"])
        report = self.budget(task, prompt, request, schema, reserve, images)
        components = {'prompt_characters': len(prompt),
                      'payload_characters': len(json.dumps(request, ensure_ascii=False)),
                      'schema_characters': len(json.dumps(schema, ensure_ascii=False))}
        if report["estimate"] > report["limit"]:
            atomic_json(path, {'request_id': identity, 'status': 'budget_exceeded',
                               'budget': report, 'components': components, 'post_sent': False})
            raise BudgetExceeded(f"Protected request exceeds context: {report['estimate']} > {report['limit']}")
        while True:
            used = receipt.get("oom_retries", 0)
            if receipt.get("status") == "oom":
                if not oom_retry or used >= 1:
                    raise ResourceExhausted(receipt["error"])
                # Reduce shared admission before a retry, then prove the service healthy.
                healthy = getattr(self.provider, "healthy", None)
                if not receipt.get("backoff_applied") or not callable(healthy):
                    raise ResourceExhausted(receipt["error"])
                if not healthy():
                    raise ResourceExhausted("Service unhealthy after OOM")
                used += 1
            receipt = {"request_id": identity, "status": "started", "attempt": attempt,
                       "oom_retries": used, "budget": report, 'components': components, "created_at": time.time()}
            # Persist retry consumption BEFORE the POST, including scene attempt 1.
            atomic_json(path, receipt)
            started = time.monotonic()
            try:
                infer = getattr(self.provider, "infer_reserved", None)
                if images:
                    response = self.provider.infer(task, prompt, request, schema, lambda value: None,
                                                   images=images, retries=0, reserve=reserve)
                elif callable(infer):
                    response = infer(task, prompt, request, schema, lambda value: None, reserve=reserve)
                else:
                    response = self.provider.infer_once(task, prompt, request, schema, lambda value: None)
            except ResourceExhausted as exc:
                lower = getattr(self.provider, "reduce_admission", None)
                if oom_retry and callable(lower):
                    lower()
                    exc.admission_reduced = True
                receipt.update(status="oom", error=str(exc), backoff_applied=oom_retry and callable(lower))
                atomic_json(path, receipt)
                # Return the terminated attempt to the scheduler. It must drain
                # existing requests before it admits this job at the lower limit.
                raise
            except Exception as exc:
                if isinstance(exc, (ValueError, TypeError, KeyError)) and not isinstance(exc, BudgetExceeded):
                    response = {"rejected_response": str(exc)}
                    atomic_json(path, {**receipt, "status": "response", "response": response,
                                       "response_hash": fingerprint(response)})
                    return response
                status = "truncated" if isinstance(exc, OutputTruncated) else (
                    "uncertain" if isinstance(exc, (OSError, InferenceUncertain)) else "failed")
                if status == "uncertain":
                    self.paused.set()
                atomic_json(path, {**receipt, "status": status, "error": str(exc)})
                raise
            atomic_json(path, {**receipt, "status": "response", "response": response,
                               "response_hash": fingerprint(response),
                               "request_wall_seconds": time.monotonic()-started})
            event("analyze_inference", batch_id=key, request_wall_seconds=time.monotonic()-started,
                  targets=len(request.get("targets", [])), reserve=reserve)
            return response
