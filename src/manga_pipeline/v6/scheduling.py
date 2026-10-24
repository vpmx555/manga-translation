"""Continuous shared queue with bounded admission driven by real engine metrics."""
import math
import time
import threading
from collections import deque
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED

from ..runtime.resources import ResourceExhausted, event


class Admission:
    def __init__(self, maximum=8, clock=time.monotonic):
        if type(maximum) is not int or not 1 <= maximum <= 64:
            raise ValueError("Admission maximum must be 1..64")
        self.maximum = maximum
        self.limit = min(2, maximum)
        self.clock = clock
        self.cooldown = 0
        self.last_adjust = clock()
        self.previous = None
        self.missing = self.good = self.completed = self.tokens = 0
        self.stable = self.limit
        self.baseline = None
        self.regressions = 0
        self.engine_completions = False
        self.lock = threading.RLock()

    def pressure(self):
        with self.lock:
            self.limit = max(1, self.limit // 2)
            self.stable, self.baseline = self.limit, None
            self.cooldown = self.clock() + 30

    def sample(self, metrics, backlog):
        with self.lock:
            return self._sample(metrics, backlog)

    def _sample(self, metrics, backlog):
        now = self.clock()
        if not metrics or any(type(metrics.get(k)) not in {int, float}
                or not math.isfinite(metrics[k]) or metrics[k] < 0
                for k in ("kv", "preemptions", "generated_tokens")):
            self.missing += 1
            self.good = 0
            if self.missing >= 2:
                self.limit, self.stable, self.baseline = 1, 1, None
                self.regressions = self.completed = self.tokens = 0
                self.previous = None
                self.last_adjust = now
            return
        self.missing = 0
        self.good += 1
        previous = self.previous
        if "completed_requests" in metrics:
            if not self.engine_completions:
                self.completed = 0
            self.engine_completions = True
            count = metrics["completed_requests"]
            before = previous.get("completed_requests") if previous else None
            if type(count) in {int, float} and type(before) in {int, float}:
                self.completed += max(0, count-before)
            elif count is None:
                self.completed = 0
        self.previous = dict(metrics)
        preempted = previous and metrics["preemptions"] > previous["preemptions"]
        if metrics["kv"] >= .90 or preempted:
            self.pressure()
            self.last_adjust = now
            self.completed = self.tokens = 0
            return
        if previous:
            self.tokens += max(0, metrics["generated_tokens"] - previous["generated_tokens"])
        elapsed = now - self.last_adjust
        if elapsed < 10 or (self.completed < 2 and self.tokens < 256):
            return
        rate = self.tokens / max(elapsed, .001)
        if self.baseline is not None:
            self.regressions = self.regressions + 1 if rate < .9 * self.baseline else 0
            if self.regressions >= 2:
                self.limit = self.stable
                self.cooldown = now + 30
                self.baseline = None
            elif not self.regressions:
                self.baseline = None
        if (self.baseline is None and now >= self.cooldown and self.good >= 2
                and backlog and metrics["kv"] < .80 and self.limit < self.maximum):
            self.stable, self.baseline = self.limit, rate
            self.limit += 1
            self.regressions = 0
        self.completed = self.tokens = 0
        self.last_adjust = now


def run(jobs, worker, provider, maximum=8, clock=time.monotonic, stop_on_error=None, stage="analyze", retry_resources=True):
    admission = Admission(maximum, clock)
    provider.reduce_admission = admission.pressure
    results, failures, pending = {}, [], {}
    retries, resource_attempts = deque(), {}
    cursor, stopped, last_poll = 0, False, clock() - 5
    with ThreadPoolExecutor(max_workers=maximum, thread_name_prefix="v6-batch") as pool:
        while pending or ((cursor < len(jobs) or retries) and not stopped):
            if clock() - last_poll >= 5:
                try:
                    metrics = provider.metrics()
                except Exception:
                    metrics = None
                admission.sample(metrics, cursor < len(jobs) or bool(retries))
                event(stage+"_admission", admission=admission.limit, metrics=metrics)
                last_poll = clock()
            # Inspect completions before admitting more: an uncertain request pauses the queue.
            done = {future for future in pending if future.done()}
            for future in done:
                index = pending.pop(future)
                try:
                    results[index] = future.result()
                    if not admission.engine_completions:
                        admission.completed += 1
                except Exception as exc:
                    if isinstance(exc, ResourceExhausted) and not getattr(exc, "admission_reduced", False):
                        admission.pressure()
                    if retry_resources and isinstance(exc, ResourceExhausted) and resource_attempts.get(index, 0) < 1:
                        resource_attempts[index] = resource_attempts.get(index, 0) + 1
                        retries.append(index)
                        # Calls has already reduced admission and persisted OOM.
                        # Retrying is admission work, never an in-worker POST.
                        continue
                    halt = stop_on_error(exc) if stop_on_error else True
                    failures.append({"batch_id": jobs[index]["batch_id"], "error": str(exc), "systemic": halt})
                    stopped |= halt
            if not stopped:
                while (cursor < len(jobs) or retries) and len(pending) < admission.limit:
                    if retries:
                        index = retries.popleft()
                    else:
                        index, cursor = cursor, cursor + 1
                    pending[pool.submit(worker, jobs[index])] = index
            if pending:
                wait(pending, timeout=.1, return_when=FIRST_COMPLETED)
    return [results[i] for i in sorted(results)], failures
