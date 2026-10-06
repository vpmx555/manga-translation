"""Bridge the pinned MAGI model's two tqdm loops to its parent process."""
from contextlib import contextmanager

from ..storage.io import atomic_json


def emit(path, phase, done=0, total=None, unit="batch"):
    if path:
        try:
            atomic_json(path, {"phase": phase, "done": done, "total": total, "unit": unit})
        except OSError:
            # A display failure must not invalidate extraction.
            pass


@contextmanager
def magi_progress(path):
    if not path:
        yield
        return
    import tqdm as module
    original = module.tqdm
    phases = iter(("MAGI detection", "MAGI OCR"))

    class WorkerTqdm(original):
        def __init__(self, *args, **kwargs):
            self._phase = next(phases, "MAGI")
            kwargs.setdefault("mininterval", 0.2)
            super().__init__(*args, **kwargs)

        def display(self, msg=None, pos=None):
            emit(path, self._phase, self.n, self.total)

    # MAGI imports tqdm locally inside detection and OCR; this affects only
    # the isolated worker and is restored after prediction, including errors.
    module.tqdm = WorkerTqdm
    try:
        yield
    finally:
        module.tqdm = original
