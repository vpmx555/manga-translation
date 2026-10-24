"""Review generation uses saved artifacts, with no provider or detector access."""
import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from tests import test_v6_pipeline as fixtures
from manga_pipeline.cli.main import main
from manga_pipeline.pipeline import Pipeline
from manga_pipeline.storage.io import file_hash
from manga_pipeline.v6.persons import bank_snapshot
from manga_pipeline.review.v6 import V6Review


class ReviewTests(unittest.TestCase):
    def test_review_backfill_preserves_artifacts_and_shows_new_plan(self):
        fixture = fixtures.V6Tests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        store, _ = fixture.fixture()
        runner = Pipeline(store, provider=fixtures.Provider(), detector=fixtures.Detector())
        self.assertTrue(runner.run(until="analyze"))
        store = runner.store
        before = {step: file_hash(store.artifact_path(step)) for step in store.steps if store.completed(step)}
        bank_before = bank_snapshot(store.manifest["bank_path"])
        with patch("manga_pipeline.providers.factory.create_provider", side_effect=AssertionError("Review accessed model")), \
                patch("manga_pipeline.providers.names.create_detector", side_effect=AssertionError("Review accessed detector")), \
                redirect_stdout(io.StringIO()):
            self.assertEqual(main(["review", "--run-dir", str(store.directory), "--no-progress"]), 0)
            V6Review(store.directory).write(store.directory / "detailed-review")
        self.assertEqual(before, {step: file_hash(store.artifact_path(step)) for step in before})
        self.assertEqual(bank_before, bank_snapshot(store.manifest["bank_path"]))
        stage = (store.artifact_path("analyze").parent / "review.md").read_text(encoding="utf-8")
        self.assertIn("Scene `scene:1`", stage)
        self.assertIn("Batch `batch:1`", stage)
        self.assertIn("cluster pool", stage)
        detailed = V6Review(store.directory).analyze_trace(store.read("analyze")["utterances"], store.directory)
        for label in ("English summary", "Cluster pool", "Anchor exception", "Resolution"):
            self.assertIn(label, detailed)
