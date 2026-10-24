import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from tests import test_visual_semantics as fixtures

DEPLOYMENT = Path(__file__).resolve().parents[1] / "deployment/runpod"
sys.path.insert(0, str(DEPLOYMENT))
spec = importlib.util.spec_from_file_location("chapter_batch_tests", DEPLOYMENT / "batch.py")
batch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(batch)


class BatchTests(unittest.TestCase):
    def test_collector_verifies_copied_run_without_rewriting_remote_paths(self):
        spec = importlib.util.spec_from_file_location("chapter_collect_tests", DEPLOYMENT / "collect.py")
        collector = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(collector)
        fixture = fixtures.VisualSemanticsTests()
        fixture.setUp()
        try:
            runner, _, _, _ = fixture.fixture()
            extract = runner.extractor
            runner.extractor = lambda store: {**extract(store), "panel_source": "koharu"}
            self.assertTrue(runner.run())
            summary = collector.verify_local(runner.store.directory.parent, {"pages": 1},
                                              {"run_dir": "/remote/output/" + runner.store.directory.name})
            self.assertEqual(summary["pages"], 1)
            self.assertEqual(summary["translations_per_style"], 2)
        finally:
            fixture.doCleanups()

    def test_context_budget_change_forks_analysis_instead_of_reusing_fallback(self):
        directory = Path("/old-run")
        with patch.object(batch.manage, "read_json", return_value={"config": {"ollama": {"num_ctx": 8192}}}), \
             patch.object(batch.manage, "configuration", return_value={"ollama": {"num_ctx": 16384}}):
            self.assertEqual(batch.command_for({}, None, directory), ["reanalyze", "--run-dir", str(directory)])

    def test_verified_summary_requires_actual_completed_v5_outputs(self):
        fixture = fixtures.VisualSemanticsTests()
        fixture.setUp()
        try:
            runner, _, _, _ = fixture.fixture()
            extract = runner.extractor
            runner.extractor = lambda store: {**extract(store), "panel_source": "koharu"}
            self.assertTrue(runner.run())
            summary = batch.verify(runner.store.directory, {"pages": 1})
            self.assertEqual(summary["translations_per_style"], 2)
            self.assertEqual(summary["pages"], 1)
            with self.assertRaisesRegex(RuntimeError, "page count"):
                batch.verify(runner.store.directory, {"pages": 2})
            from manga_pipeline.storage.io import atomic_json
            atomic_json(runner.store.artifact_path("analyze").parent / "attempts/budget.json",
                        {"error": "Request exceeds context budget"})
            with self.assertRaisesRegex(RuntimeError, "context limit"):
                batch.verify(runner.store.directory, {"pages": 1})
        finally:
            fixture.doCleanups()

    def test_plan_rejects_path_escape_before_launching(self):
        import tempfile
        import json
        with tempfile.TemporaryDirectory() as temp:
            plan = Path(temp) / "plan.json"
            plan.write_text(json.dumps({"chapters": [{"story_key": "../escape", "chapter": "c",
                "input": "input", "pages": 1}]}), encoding="utf-8")
            with patch.object(batch.manage, "lock") as lock:
                with self.assertRaisesRegex(ValueError, "Unsafe"):
                    batch.main([str(plan), "--state", str(Path(temp) / "state.json")])
                lock.assert_not_called()
