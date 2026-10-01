"""Pilot isolation and honest failure reporting, without live model calls."""
from __future__ import annotations

import copy
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dialogue_data import write_json
from reasoning_benchmark import benchmark
from reasoning_evaluation import review_template
from reasoning_pipeline import run_reasoning
from test_reasoning_pipeline import ScriptedProvider, fixture


class BenchmarkContracts(unittest.TestCase):
    def setup_manifest(self, root, *, variants=None):
        _, document, _ = fixture()
        reference = review_template(document)
        reference.update(confirmed=True, reviewer="independent fixture reviewer")
        for index, boundary in enumerate(reference["boundaries"]):
            boundary["boundary"] = index == 2
        write_json(root / "source.json", document)
        write_json(root / "reference.json", reference)
        manifest = {"models": [{"name": "fixture", "profile": "cpu"}], "text_only": True,
                    "test": [{"normalized": "source.json", "reference": "reference.json"}],
                    "variants": variants or ["predicted", "no_scene", "no_order_fix", "gold_scene"]}
        write_json(root / "manifest.json", manifest)
        return manifest, document, reference

    def test_sequential_variants_only_expose_gold_boundaries_to_oracle(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.setup_manifest(root)
            calls = []
            def run_job(chapter, client, **kwargs):
                calls.append((kwargs["scene_source"], kwargs["correct_order"], kwargs["reference"] is not None))
                return run_reasoning(chapter, client, **kwargs)
            report = benchmark(root / "manifest.json", root / "runs", run_fn=run_job,
                               client_factory=lambda config, cache: ScriptedProvider(cache))
            self.assertEqual(report["status"], "complete")
            self.assertEqual(calls, [("predicted", True, False), ("none", True, False),
                                     ("predicted", False, False), ("gold", True, True)])
            self.assertEqual(len(report["cases"]), 4)
            self.assertTrue(all(Path(case["output"]).is_file() for case in report["cases"]))
            self.assertNotIn("winner", report)

    def test_duplicate_development_test_chapter_rejected_before_inference(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest, _, _ = self.setup_manifest(root)
            manifest["development"] = copy.deepcopy(manifest["test"])
            write_json(root / "manifest.json", manifest, overwrite=True)
            def forbidden(*args, **kwargs):
                self.fail("Duplicate pilot data must fail before model initialization")
            with self.assertRaisesRegex(ValueError, "Duplicate chapter/content"):
                benchmark(root / "manifest.json", root / "runs", client_factory=forbidden)

    def test_incomplete_oracle_reference_rejected_before_inference(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _, _, reference = self.setup_manifest(root)
            reference["boundaries"][0]["boundary"] = None
            write_json(root / "reference.json", reference, overwrite=True)
            with self.assertRaisesRegex(ValueError, "fully reviewed boundary"):
                benchmark(root / "manifest.json", root / "runs")

    def test_failed_case_is_reported_without_metrics_or_winner(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.setup_manifest(root, variants=["predicted"])
            def fail(*args, **kwargs):
                raise RuntimeError("Fixture out of memory")
            report = benchmark(root / "manifest.json", root / "runs", run_fn=fail,
                               client_factory=lambda config, cache: ScriptedProvider(cache))
            self.assertEqual(report["status"], "incomplete")
            self.assertEqual(report["cases"][0]["status"], "failed")
            self.assertNotIn("metrics", report["cases"][0])
            self.assertNotIn("winner", report)


if __name__ == "__main__":
    unittest.main()
