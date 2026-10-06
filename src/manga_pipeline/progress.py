"""Sequential terminal bars; progress never participates in checkpoint state."""
import sys
import threading


class NullProgress:
    enabled = False

    def start(self, step, index, steps, *, reused=False):
        pass

    def reset(self, total=None, unit="item", phase=""):
        pass

    def advance(self, *, reused=False, failed=False):
        pass

    def set_phase(self, phase):
        pass

    def worker(self, event):
        pass

    def finish(self, status="completed"):
        pass

    def close(self):
        pass


class TerminalProgress(NullProgress):
    enabled = True

    def __init__(self, stream=None):
        from tqdm import tqdm
        self._tqdm = tqdm
        self.stream = stream if stream is not None else sys.stderr
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread = None
        self._bar = None
        self._prefix = ""
        self._phase = None
        self._reused = self._failed = 0

    def start(self, step, index, steps, *, reused=False):
        with self._lock:
            self._prefix = f"[{index}/{steps}] {step}"
            self._phase = None
            self._reused, self._failed = int(reused), 0
            self._bar = self._tqdm(total=1 if reused else None, desc=self._prefix,
                                  unit="step", file=self.stream, ascii=True,
                                  dynamic_ncols=True, leave=True, mininterval=0.2)
            if reused:
                self._bar.set_postfix_str("reusing completed result")
            if (self._thread is None or not self._thread.is_alive()) and self.stream.isatty():
                self._stop.clear()
                self._thread = threading.Thread(target=self._heartbeat, daemon=True)
                self._thread.start()

    def _heartbeat(self):
        while not self._stop.wait(1):
            with self._lock:
                if self._bar is not None:
                    self._bar.refresh()

    def reset(self, total=None, unit="item", phase=""):
        with self._lock:
            if self._bar is None:
                return
            self._phase = (phase, total, unit)
            self._bar.unit = unit
            self._bar.total = total
            self._bar.set_description_str(self._prefix + (f" / {phase}" if phase else ""), refresh=False)
            self._bar.reset(total=total)

    def set_phase(self, phase):
        with self._lock:
            if self._bar is not None:
                self._bar.set_description_str(self._prefix + (f" / {phase}" if phase else ""))

    def advance(self, *, reused=False, failed=False):
        with self._lock:
            if self._bar is None:
                return
            self._reused += int(reused)
            self._failed += int(failed)
            self._bar.set_postfix(reused=self._reused, failed=self._failed, refresh=False)
            self._bar.update(1)

    def worker(self, event):
        with self._lock:
            if self._bar is None:
                return
            phase = (event["phase"], event["total"], event["unit"])
            if phase != self._phase:
                self.reset(total=event["total"], unit=event["unit"], phase=event["phase"])
            delta = event["done"] - self._bar.n
            if delta >= 0:
                self._bar.update(delta)

    def finish(self, status="completed"):
        with self._lock:
            if self._bar is None:
                return
            if status in {"completed", "reused"}:
                if self._bar.total is None:
                    self._bar.unit = "step"
                    self._bar.reset(total=1)
                self._bar.update(max(0, self._bar.total - self._bar.n))
            self._bar.set_postfix_str(f"{status}; reused={self._reused}; failed={self._failed}", refresh=False)
            self._bar.close()
            self._bar = None

    def close(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
        self.finish("interrupted")
