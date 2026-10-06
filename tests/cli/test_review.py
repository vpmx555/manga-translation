import io
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from tests.helpers import FakeDetector, FakeProvider, make_store, raw_document
from manga_pipeline.cli.main import main
from manga_pipeline.pipeline import Pipeline
from manga_pipeline.storage.runs import DIRECTORIES, STEPS, RunStore


class ReviewTests(unittest.TestCase):
    def test_backfill_reviews_keeps_completed_results_and_bank_without_model_calls(self):
        with tempfile.TemporaryDirectory() as root:
            store = make_store(root)
            self.assertTrue(Pipeline(store, detector=FakeDetector(), provider=FakeProvider(),
                                     extractor=lambda _: raw_document()).run())
            store = RunStore(store.directory)
            files = [store.path, store.directory / "snapshots" / "config.json",
                     *[store.artifact_path(step) for step in STEPS]]
            from pathlib import Path
            files.append(Path(store.manifest["bank_path"]))
            before = {file: file.read_bytes() for file in files}
            for step in STEPS:
                (store.directory / DIRECTORIES[step] / "review.md").unlink()
            (store.directory / "review.md").unlink()
            with patch("manga_pipeline.cli.main.Pipeline.run", side_effect=AssertionError("Model pipeline called")):
                with redirect_stdout(io.StringIO()):
                    self.assertEqual(main(["review", "--run-dir", str(store.directory)]), 0)
            for file in files:
                self.assertEqual(file.read_bytes(), before[file])
            for step in STEPS:
                self.assertTrue((store.directory / DIRECTORIES[step] / "review.md").is_file())
            scan = (store.directory / "03_scan" / "review.md").read_text(encoding="utf-8")
            self.assertIn("Roronoa Zoro", scan)
            self.assertIn("P01/T01", scan)
            self.assertNotIn("request_hash", scan)
            self.assertNotIn("bbox", scan)
            index = (store.directory / "review.md").read_text(encoding="utf-8")
            self.assertEqual(index.count("]("), 6)
            self.assertIn("| Run-time |", index)
            self.assertIn("Tổng run-time đã ghi nhận:", index)
            self.assertIn("6/6 bước có timing", index)
