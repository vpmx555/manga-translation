import tempfile
import unittest
from unittest.mock import patch

from tests.helpers import make_store, raw_document
from manga_pipeline.review.renderer import runtime
from manga_pipeline.storage.io import file_hash
from manga_pipeline.storage.runs import RunStore


class TimingTests(unittest.TestCase):
    def test_partial_retry_accumulates_without_resume_pause(self):
        with tempfile.TemporaryDirectory() as root:
            store = make_store(root)
            with patch("manga_pipeline.storage.runs.monotonic", side_effect=[10, 14]):
                store.begin("layout")
                store.finish("layout", raw_document(), failures=1)
            resumed = RunStore(store.directory)
            with patch("manga_pipeline.storage.runs.monotonic", side_effect=[1000, 1007]):
                resumed.begin("layout")
                resumed.finish("layout", raw_document())
            self.assertEqual(resumed.manifest["steps"]["layout"]["runtime_seconds"], 11)
            self.assertEqual(RunStore(store.directory).manifest["steps"]["layout"]["runtime_seconds"], 11)
            self.assertEqual(runtime(11), "11.000s")

    def test_failure_and_interrupt_time_survive_retry(self):
        with tempfile.TemporaryDirectory() as root:
            store = make_store(root)
            with patch("manga_pipeline.storage.runs.monotonic", side_effect=[0, 3, 100, 105, 200, 207]):
                store.begin("layout")
                store.failed("layout", RuntimeError("worker error"))
                store.begin("layout")
                store.interrupted("layout")
                self.assertEqual(store.manifest["steps"]["layout"]["runtime_seconds"], 8)
                store.begin("layout")
                store.finish("layout", raw_document())
            self.assertEqual(store.manifest["steps"]["layout"]["runtime_seconds"], 15)
            receipt = store.artifact_path("layout").with_name("commit.json")
            before = file_hash(receipt)
            self.assertTrue(RunStore(store.directory).completed("layout"))
            self.assertEqual(file_hash(receipt), before)

    def test_legacy_missing_timing_stays_unknown_without_rewriting_source(self):
        with tempfile.TemporaryDirectory() as root:
            store = make_store(root)
            store.finish("layout", raw_document())
            before = file_hash(store.path)
            self.assertIsNone(RunStore(store.directory).manifest["steps"]["layout"].get("runtime_seconds"))
            self.assertEqual(file_hash(store.path), before)


if __name__ == "__main__":
    unittest.main()
