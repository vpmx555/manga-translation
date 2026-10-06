import tempfile
import unittest
from unittest.mock import patch

from tests.helpers import make_store, raw_document
from manga_pipeline.review.renderer import Review
from manga_pipeline.storage.io import file_hash
from manga_pipeline.storage.runs import RunStore


class TimingTests(unittest.TestCase):
    def test_partial_retry_accumulates_without_resume_pause(self):
        with tempfile.TemporaryDirectory() as root:
            store = make_store(root)
            with patch("manga_pipeline.storage.runs.monotonic", side_effect=[10, 14]):
                store.begin("extract")
                store.finish("extract", raw_document(), failures=1)
            resumed = RunStore(store.directory)
            with patch("manga_pipeline.storage.runs.monotonic", side_effect=[1000, 1007]):
                resumed.begin("extract")
                resumed.finish("extract", raw_document())
            self.assertEqual(resumed.manifest["steps"]["extract"]["runtime_seconds"], 11)
            self.assertEqual(RunStore(store.directory).manifest["steps"]["extract"]["runtime_seconds"], 11)
            review = Review(resumed).render_index()
            self.assertIn("Tổng run-time đã ghi nhận: 11.000s", review)
            self.assertIn("1/6 bước có timing", review)
            self.assertIn("| Run-time |", review)

    def test_failure_and_interrupt_time_survive_retry(self):
        with tempfile.TemporaryDirectory() as root:
            store = make_store(root)
            with patch("manga_pipeline.storage.runs.monotonic", side_effect=[0, 3, 100, 105, 200, 207]):
                store.begin("extract")
                store.failed("extract", RuntimeError("worker error"))
                store.begin("extract")
                store.interrupted("extract")
                self.assertEqual(store.manifest["steps"]["extract"]["runtime_seconds"], 8)
                store.begin("extract")
                store.finish("extract", raw_document())
            self.assertEqual(store.manifest["steps"]["extract"]["runtime_seconds"], 15)
            receipt = store.artifact_path("extract").with_name("commit.json")
            before = file_hash(receipt)
            self.assertTrue(RunStore(store.directory).completed("extract"))
            self.assertEqual(file_hash(receipt), before)

    def test_legacy_missing_timing_stays_unknown_without_rewriting_source(self):
        with tempfile.TemporaryDirectory() as root:
            store = make_store(root)
            store.finish("extract", raw_document())
            before = file_hash(store.path)
            text = Review(RunStore(store.directory)).render_index()
            self.assertIn("Tổng run-time đã ghi nhận: —", text)
            self.assertIn("0/6 bước có timing", text)
            self.assertEqual(file_hash(store.path), before)


if __name__ == "__main__":
    unittest.main()
