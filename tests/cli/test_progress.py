import copy
import io
import tempfile
import unittest
from pathlib import Path

from tests import test_v6_pipeline as fixtures
from manga_pipeline.extraction.progress import magi_progress
from manga_pipeline.pipeline import Pipeline
from manga_pipeline.progress import TerminalProgress
from manga_pipeline.storage.io import read_json
from manga_pipeline.storage.runs import RunStore, V6_STEPS


class ProgressTests(unittest.TestCase):
    def test_analyze_progress_and_completed_resume_reuse(self):
        fixture = fixtures.V6Tests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        store, _ = fixture.fixture()
        provider, stream = fixtures.Provider(), io.StringIO()
        runner = Pipeline(store, detector=fixtures.Detector(), provider=provider, progress=TerminalProgress(stream))
        self.assertTrue(runner.run(until="analyze"))
        for index, step in enumerate(V6_STEPS[:6], 1):
            self.assertIn(f"[{index}/6] {step}", stream.getvalue())
        before = len(provider.calls)
        resumed = io.StringIO()
        self.assertTrue(Pipeline(RunStore(store.directory), provider=provider, progress=TerminalProgress(resumed)).run(until="analyze"))
        self.assertEqual(len(provider.calls), before)
        self.assertEqual(resumed.getvalue().count("reused; reused=1; failed=0"), 6)

    def test_worker_bridge_reports_actual_batches_and_restores_tqdm(self):
        import tqdm
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "progress.json"
            original = tqdm.tqdm
            stream = io.StringIO()
            with magi_progress(path):
                list(tqdm.tqdm(range(3), file=stream))
                event = read_json(path)
                self.assertEqual((event["phase"], event["done"], event["total"]), ("MAGI detection", 3, 3))
                list(tqdm.tqdm(range(2), file=stream))
            self.assertIs(tqdm.tqdm, original)
            event = read_json(path)
            self.assertEqual((event["phase"], event["done"], event["total"]), ("MAGI OCR", 2, 2))
            progress = TerminalProgress(io.StringIO())
            progress.start("extract", 1, 1)
            progress.worker(event)
            progress.worker({"phase": "Saving bank", "done": 0, "total": None, "unit": "batch"})
            self.assertIsNone(progress._bar.total)
            progress.finish()
            progress.close()
