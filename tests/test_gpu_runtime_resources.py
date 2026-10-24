"""GPU admission regressions without models, services, network or GPU hardware."""
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from manga_pipeline.runtime import resources


class GPULeaseTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.threads = []
        self.releases = []
        self.errors = []
        real_lock = resources.exclusive_lock
        patches = [
            patch.dict(os.environ, {"MANGA_GPU_COORDINATOR": str(self.root),
                                    "MANGA_GPU_GATE_TIMEOUT": "3"}),
            patch.object(resources, "_identity", side_effect=lambda: {"pid": 1, "identity": "test"}),
            patch.object(resources, "_alive", side_effect=lambda record: not record.get("dead", False)),
            patch.object(resources, "_unload_llm"),
            patch.object(resources, "event"),
            patch.object(resources, "exclusive_lock", side_effect=lambda path: real_lock(path, timeout=1)),
        ]
        for mock in patches:
            mock.start()
            self.addCleanup(mock.stop)
        resources.atomic_json(self.root / "gpu-state.json", {"active": [], "waiting": [], "max_slots": 2})
        # Cleanup workers before restoring mocks/environment and removing their state directory.
        self.addCleanup(self._stop_workers)

    def _state(self):
        return resources.read_json(self.root / "gpu-state.json")

    def _wait(self, predicate, message):
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.01)
        self.fail(message)

    def _worker(self, kind):
        entered, release = threading.Event(), threading.Event()
        self.releases.append(release)

        def run():
            try:
                with resources.gpu_lease(kind):
                    entered.set()
                    if not release.wait(4):
                        raise TimeoutError("Test lease was not released")
            except BaseException as exc:
                self.errors.append(exc)

        thread = threading.Thread(target=run, daemon=True)
        self.threads.append(thread)
        thread.start()
        return entered, release, thread

    def _stop_workers(self):
        for release in self.releases:
            release.set()
        for thread in self.threads:
            thread.join(timeout=4)

    def _finish(self):
        self._stop_workers()
        self.assertTrue(all(not thread.is_alive() for thread in self.threads))
        self.assertEqual(self.errors, [])
        self.assertEqual(self._state()["active"], [])
        self.assertEqual(self._state()["waiting"], [])

    def test_llm_admission_is_bounded_and_reuses_a_released_slot(self):
        first, release_first, first_thread = self._worker("llm")
        second, _, _ = self._worker("llm")
        self.assertTrue(first.wait(2))
        self.assertTrue(second.wait(2))
        third, _, _ = self._worker("llm")
        self._wait(lambda: len(self._state()["waiting"]) == 1, "Third LLM never queued")
        self.assertFalse(third.is_set())
        self.assertEqual(len(self._state()["active"]), 2)
        release_first.set()
        first_thread.join(timeout=2)
        self.assertTrue(third.wait(2))
        self.assertLessEqual(len(self._state()["active"]), 2)
        self._finish()

    def test_visual_is_exclusive_and_fifo_blocks_later_llm(self):
        llm, release_llm, llm_thread = self._worker("llm")
        self.assertTrue(llm.wait(2))
        visual, release_visual, visual_thread = self._worker("visual")
        self._wait(lambda: [r["kind"] for r in self._state()["waiting"]] == ["visual"],
                   "Visual never queued")
        later_llm, _, _ = self._worker("llm")
        self._wait(lambda: [r["kind"] for r in self._state()["waiting"]] == ["visual", "llm"],
                   "Later LLM never queued behind visual")
        self.assertFalse(visual.is_set())
        self.assertFalse(later_llm.is_set())
        release_llm.set()
        llm_thread.join(timeout=2)
        self.assertTrue(visual.wait(2))
        self.assertEqual([r["kind"] for r in self._state()["active"]], ["visual"])
        self.assertFalse(later_llm.is_set())
        release_visual.set()
        visual_thread.join(timeout=2)
        self.assertTrue(later_llm.wait(2))
        self._finish()

    def test_exception_releases_lease(self):
        with self.assertRaisesRegex(ValueError, "caller failure"):
            with resources.gpu_lease("llm"):
                raise ValueError("caller failure")
        self.assertEqual(self._state()["active"], [])
        self.assertEqual(self._state()["waiting"], [])
        with resources.gpu_lease("visual"):
            self.assertEqual(len(self._state()["active"]), 1)

    def test_slot_backoff_never_reaches_zero(self):
        for _ in range(4):
            resources.reduce_slots("simulated OOM")
        self.assertEqual(self._state()["max_slots"], 1)

    def test_dead_owner_quarantines_coordinator(self):
        resources.atomic_json(self.root / "gpu-state.json", {
            "active": [{"pid": 999, "token": "dead-owner", "kind": "llm", "dead": True}],
            "waiting": [], "max_slots": 2,
        })
        with self.assertRaisesRegex(RuntimeError, "systemic failure"):
            with resources.gpu_lease("visual"):
                self.fail("Dead owner must prevent admission")
        self.assertIn("owner exited", self._state()["fault"])
        self.assertEqual(self._state()["waiting"], [])


if __name__ == "__main__":
    unittest.main()
