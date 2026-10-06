import copy
import io
import tempfile
import unittest
from pathlib import Path

from tests.helpers import FakeDetector, FakeProvider, make_store, raw_document
from manga_pipeline.extraction.progress import magi_progress
from manga_pipeline.pipeline import Pipeline
from manga_pipeline.progress import TerminalProgress
from manga_pipeline.storage.io import read_json
from manga_pipeline.storage.runs import RunStore, STEPS


class ProgressTests(unittest.TestCase):
    def test_full_run_and_completed_resume_show_six_bars_without_reprocessing(self):
        with tempfile.TemporaryDirectory() as root:
            store = make_store(root)
            detector, provider, stream = FakeDetector(), FakeProvider(), io.StringIO()
            self.assertTrue(Pipeline(store, detector=detector, provider=provider,
                                     extractor=lambda _: raw_document(),
                                     progress=TerminalProgress(stream)).run())
            output = stream.getvalue()
            positions = [output.index(f"[{index}/6] {step}") for index, step in enumerate(STEPS, 1)]
            self.assertEqual(positions, sorted(positions))
            self.assertIn("1/1", output)
            model_calls, detector_calls = len(provider.calls), detector.calls
            resumed = io.StringIO()
            self.assertTrue(Pipeline(RunStore(store.directory), detector=detector, provider=provider,
                                     extractor=lambda _: self.fail("Repeated extraction"),
                                     progress=TerminalProgress(resumed)).run())
            self.assertEqual(len(provider.calls), model_calls)
            self.assertEqual(detector.calls, detector_calls)
            for index, step in enumerate(STEPS, 1):
                self.assertIn(f"[{index}/6] {step}", resumed.getvalue())
            self.assertEqual(resumed.getvalue().count("reused; reused=1; failed=0"), 6)

    def test_partial_targets_report_failure_then_resume_reports_reused_target(self):
        with tempfile.TemporaryDirectory() as root:
            store, raw = make_store(root), raw_document()
            second = copy.deepcopy(raw["texts"][0])
            second.update(id="text2", reading_order=1)
            raw["texts"].append(second)
            provider = FakeProvider()
            infer = provider.infer

            def fail_second(task, prompt, data, schema, validator, images=None):
                if task == "classify_mentions" and data["target"]["id"] == "text2:u":
                    raise RuntimeError("temporary failure")
                return infer(task, prompt, data, schema, validator, images)

            provider.infer = fail_second
            stream = io.StringIO()
            self.assertFalse(Pipeline(store, detector=FakeDetector(), provider=provider,
                                      extractor=lambda _: raw, progress=TerminalProgress(stream)).run())
            self.assertIn("partial; reused=0; failed=1", stream.getvalue())
            self.assertNotIn("[5/6]", stream.getvalue())
            self.assertEqual(RunStore(store.directory).manifest["steps"]["classify"]["status"], "partial")
            provider.infer = infer
            retry = io.StringIO()
            self.assertTrue(Pipeline(RunStore(store.directory), provider=provider,
                                     progress=TerminalProgress(retry)).run())
            self.assertIn("completed; reused=1; failed=0", retry.getvalue())
            self.assertEqual(len(provider.calls), 2)

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
