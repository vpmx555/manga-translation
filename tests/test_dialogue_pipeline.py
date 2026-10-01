from __future__ import annotations

import argparse
import copy
import io
import json
import sys
import tempfile
import types
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dialogue_data import (build_raw_document, context_for_utterance, import_utterances,
                           normalize_document, read_json, write_json)
from dialogue_pipeline import build_parser, run
from scene_clustering import adjacent_clusters, cluster_document, embed_dialogue, unit_vectors
from scene_evaluation import (benchmark, confirm_reference, reference_fingerprint, review_template,
                             score_boundaries, validate_reference)


def example_raw():
    prediction = {
        "texts": [[60, 5, 90, 15], [60, 18, 90, 25], [5, 5, 40, 15], [5, 18, 40, 25], [5, 26, 40, 30]],
        "ocr": ["I will", "protect you!", "Three years later...", "Sort by", "BAM"],
        "panels": [[50, 0, 100, 50], [0, 0, 49, 50]],
        "characters": [[55, 30, 75, 50], [10, 30, 30, 50]],
        "tails": [[61, 20, 65, 30]], "text_character_associations": [[0, 0], [1, 0]],
        "text_tail_associations": [[0, 0], [1, 0]], "character_cluster_labels": [0, 1],
        "character_names": ["Other", "14"], "is_essential_text": [True, True, False, False, False],
    }
    raw = build_raw_document([np.zeros((60, 100, 3), dtype=np.uint8)], [Path("page1.png")], [prediction],
                             story_id="story", chapter_id="ch1", model_fingerprint="test", character_ids=[[7, None]])
    return raw


def example_document(identifier="chapter", texts=None):
    texts = texts or ["We are building a rocket.", "The rocket engine is ready.", "Let's buy dinner.", "I want soup."]
    return normalize_document(import_utterances(
        [{"text": text, "page": i // 2 + 1, "order_in_scene": i + 1, "speaker_id": i % 2}
         for i, text in enumerate(texts)], document_id=identifier))


def confirmed_reference(document, cuts):
    labels = review_template(document)
    for index, row in enumerate(labels["boundaries"], start=1):
        row.update({"boundary": index in cuts, "reason": "Independent manual review"})
    return confirm_reference(document, labels, "test reviewer")


class ExtractionNormalizationTests(unittest.TestCase):
    def test_raw_preserves_all_boxes_and_identity_is_not_parsed_from_display_label(self):
        raw = example_raw()
        self.assertEqual(len(raw["texts"]), 5)
        self.assertEqual(raw["texts"][0]["speaker_id"], 7)
        self.assertEqual(raw["texts"][0]["speaker_display_label"], "Other")
        self.assertEqual(raw["texts"][0]["speaker_status"], "resolved")
        self.assertEqual(raw["texts"][2]["speaker_status"], "unlinked")
        self.assertIsNone(raw["texts"][0]["speaker_affinity"])
        self.assertEqual(raw["texts"][0]["panel_index"], 0)
        self.assertEqual(raw["texts"][2]["panel_index"], 1)

    def test_pending_identity_is_distinct_from_unlinked_and_clusters_are_namespaced(self):
        raw = example_raw()
        predictions = [{"texts": [[0, 0, 1, 1]], "ocr": ["Hi"], "characters": [[0, 0, 1, 1]],
                        "character_names": ["Other"], "character_cluster_labels": [0],
                        "text_character_associations": [[0, 0]], "is_essential_text": [True]}] * 2
        result = build_raw_document([np.zeros((2, 2, 3))] * 2, [Path("p1"), Path("p2")], predictions,
                                    story_id="s", chapter_id="c", model_fingerprint="m", character_ids=[[None], [None]])
        self.assertEqual(result["texts"][0]["speaker_status"], "pending_identity")
        self.assertNotEqual(result["texts"][0]["speaker_cluster_id"], result["texts"][1]["speaker_cluster_id"])

    def test_bad_association_is_rejected(self):
        prediction = {"texts": [[0, 0, 1, 1]], "ocr": ["Hi"], "characters": [],
                      "character_cluster_labels": [], "text_character_associations": [[0, -1]],
                      "is_essential_text": [True]}
        with self.assertRaises(ValueError):
            build_raw_document([np.zeros((2, 2, 3))], [Path("p")], [prediction],
                               story_id="s", chapter_id="c", model_fingerprint="m")

    def test_narration_survives_nonessential_flag_and_noise_has_audit_trail(self):
        raw = example_raw()
        original = copy.deepcopy(raw)
        document = normalize_document(raw)
        self.assertEqual(raw, original)
        self.assertEqual([r["text"] for r in document["utterances"]],
                         ["I will", "protect you!", "Three years later...", "BAM"])
        self.assertEqual(document["utterances"][2]["content_type"], "narration")
        self.assertEqual(document["excluded"][0]["content_type"], "ui")
        self.assertEqual(document["merge_candidates"][0]["confirmed"], False)
        self.assertTrue(all(r["scene_id"] is None for r in document["utterances"]))

    def test_confirmed_sfx_filter_and_thought_keep(self):
        raw = example_raw()
        overrides = {raw["texts"][-1]["id"]: {"content_type": "sfx", "confirmed": True},
                     raw["texts"][0]["id"]: {"content_type": "thought", "confirmed": True}}
        document = normalize_document(raw, overrides=overrides)
        self.assertNotIn("BAM", [r["text"] for r in document["utterances"]])
        self.assertEqual(document["utterances"][0]["content_type"], "thought")
        overrides[raw["texts"][0]["id"]]["confirmed"] = False
        with self.assertRaises(ValueError):
            normalize_document(raw, overrides=overrides)

    def test_confirmed_merge_preserves_each_original_box(self):
        raw = example_raw()
        group = {"source_text_ids": [r["id"] for r in raw["texts"][:2]],
                 "confirmed": True, "reason": "Verified fragments of the same utterance"}
        document = normalize_document(raw, merge_groups=[group])
        first = document["utterances"][0]
        self.assertEqual(first["text"], "I will protect you!")
        self.assertEqual([b["bbox"] for b in first["source_boxes"]], [r["bbox"] for r in raw["texts"][:2]])
        self.assertIsNone(first["bbox"])
        self.assertEqual(first["text_original"], ["I will", "protect you!"])

    def test_merge_across_panels_and_unknown_speaker_is_rejected(self):
        raw = example_raw()
        group = {"source_text_ids": [r["id"] for r in raw["texts"][:2]], "confirmed": True, "reason": "test"}
        raw["texts"][1]["panel_index"] = 1
        with self.assertRaises(ValueError):
            normalize_document(raw, merge_groups=[group])
        raw = example_raw()
        for row in raw["texts"][:2]:
            row.update({"speaker_id": None, "speaker_cluster_id": None, "character_detection_index": None})
        with self.assertRaises(ValueError):
            normalize_document(raw, merge_groups=[group])

    def test_external_array_order_is_preserved_when_scene_local_order_resets(self):
        rows = [{"text": "Later", "page": 2, "order_in_scene": 1, "speaker_id": 1, "addressee_ids": [2]},
                {"text": "Earlier", "page": 1, "order_in_scene": 4, "speaker_id": 2, "addressee_type": "group"}]
        document = normalize_document(import_utterances(rows, document_id="input"))
        self.assertEqual([r["text"] for r in document["utterances"]], ["Later", "Earlier"])
        self.assertEqual(document["utterances"][0]["addressee_ids"], [2])
        self.assertEqual(document["utterances"][0]["source_order_in_scene"], 1)
        self.assertEqual(document["utterances"][0]["source_record"], rows[0])
        self.assertIsNone(document["utterances"][0]["source_boxes"][0]["bbox"])

    def test_context_keeps_nearby_turns_across_proposed_scene_cut(self):
        document = cluster_document(example_document(), [[1, 0], [1, 0], [0, 1], [0, 1]])
        context = context_for_utterance(document, 1, radius=1)
        self.assertEqual(len(context), 3)
        self.assertNotEqual(context[1]["scene_id"], context[2]["scene_id"])


class SceneClusteringTests(unittest.TestCase):
    def test_repeated_topic_does_not_merge_nonadjacent_scenes(self):
        vectors = [[1, 0], [1, 0], [0, 1], [0, 1], [1, 0], [1, 0]]
        self.assertEqual(adjacent_clusters(vectors), [(0, 2), (2, 4), (4, 6)])

    def test_scene_can_cross_page_without_reordering_sources(self):
        document = example_document()
        result = cluster_document(document, [[1, 0]] * 4)
        self.assertEqual(len({r["scene_id"] for r in result["utterances"]}), 1)
        self.assertEqual([r["id"] for r in result["utterances"]], [r["id"] for r in document["utterances"]])
        self.assertTrue(all(r["scene_status"] == "proposed" for r in result["utterances"]))
        self.assertIsNone(document["utterances"][0]["scene_id"])

    def test_cohesion_guard_prevents_gradual_chaining(self):
        angles = np.deg2rad([0, 25, 50, 75, 100])
        vectors = np.column_stack([np.cos(angles), np.sin(angles)])
        merged = adjacent_clusters(vectors, distance_threshold=1.5, cohesion_threshold=2)
        guarded = adjacent_clusters(vectors, distance_threshold=1.5, cohesion_threshold=0.5)
        self.assertEqual(merged, [(0, 5)])
        self.assertGreater(len(guarded), 1)

    def test_nonfinite_zero_or_misaligned_vectors_are_rejected(self):
        for vectors in ([[0, 0]], [[np.nan, 1]], [[np.inf, 1]]):
            with self.assertRaises(ValueError):
                unit_vectors(vectors)
        with self.assertRaises(ValueError):
            cluster_document(example_document(), [[1, 0]])

    def test_single_turn_and_short_boundary_evidence(self):
        single = example_document(texts=["Hi"])
        result = cluster_document(single, [[1, 0]])
        self.assertEqual(result["scene_boundaries"], [])
        pair = example_document(texts=["Yes", "Go"])
        result = cluster_document(pair, [[1, 0], [0, 1]])
        self.assertEqual(result["scene_boundaries"][0]["review_status"], "needs_review")


class ReferenceBenchmarkTests(unittest.TestCase):
    def test_reference_template_is_independent_of_cluster_output(self):
        document = cluster_document(example_document(), [[1, 0], [1, 0], [0, 1], [0, 1]])
        labels = review_template(document)
        self.assertTrue(all(r["boundary"] is None for r in labels["boundaries"]))
        self.assertEqual(reference_fingerprint(document), reference_fingerprint(example_document()))
        with self.assertRaises(ValueError):
            validate_reference(document, labels)

    def test_blank_reference_cannot_be_confirmed_and_ambiguous_remains_unscored(self):
        document = example_document()
        labels = review_template(document)
        with self.assertRaises(ValueError):
            confirm_reference(document, labels, "reviewer")
        for row in labels["boundaries"]:
            row.update({"boundary": False, "reason": "Reviewed continuity"})
        labels["boundaries"][1].update({"boundary": None, "status": "ambiguous", "reason": "Insufficient dialogue"})
        result = confirm_reference(document, labels, "reviewer")
        self.assertEqual(validate_reference(document, result)[2], None)

    def test_backend_failure_is_reported_without_selecting_production_model(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = {"development": [], "test": []}
            for split in manifest:
                document = example_document(split, [f"{split} {i}" for i in range(4)])
                write_json(root / f"{split}.json", document)
                write_json(root / f"{split}.labels.json", confirmed_reference(document, {2}))
                manifest[split].append({"dialogue": f"{split}.json", "labels": f"{split}.labels.json"})
            write_json(root / "manifest.json", manifest)
            def encoder(*args, **kwargs):
                raise RuntimeError("Model weights unavailable")
            report = benchmark(root / "manifest.json", backends=["mpnet"], thresholds=[0.1], encoder=encoder)
            self.assertEqual(report["status"], "incomplete")
            self.assertFalse(report["production_model_selected"])
            self.assertIn("weights unavailable", report["models"]["mpnet"]["error"])

    def test_stale_text_and_duplicate_reference_gaps_are_rejected(self):
        document = example_document()
        labels = confirmed_reference(document, {2})
        changed = copy.deepcopy(document)
        changed["utterances"][0]["text"] += " Changed."
        with self.assertRaises(ValueError):
            validate_reference(changed, labels)
        labels["boundaries"][1] = copy.deepcopy(labels["boundaries"][0])
        with self.assertRaises(ValueError):
            validate_reference(document, labels)

    def test_reference_is_portable_but_speaker_changes_invalidate_it(self):
        document = example_document()
        labels = confirmed_reference(document, {2})
        copied = copy.deepcopy(document)
        copied["raw_json_path"] = "a/different/location.json"
        copied["raw_fingerprint"] = "different file location metadata"
        validate_reference(copied, labels)
        copied["utterances"][0]["speaker_id"] = "changed-speaker"
        with self.assertRaises(ValueError):
            validate_reference(copied, labels)

    def test_ambiguous_gap_is_not_a_false_boundary_and_masks_windowdiff(self):
        metrics = score_boundaries(4, {2, 3}, {1: False, 2: None, 3: True}, window=1)
        self.assertEqual(metrics["boundary_f1"], 1)
        self.assertEqual(metrics["window_diff"], 0)
        self.assertEqual(metrics["valid_windows"], 2)
        self.assertEqual(metrics["ambiguous_gaps"], 1)

    def test_overmerge_oversplit_metrics(self):
        metrics = score_boundaries(4, {1}, {1: False, 2: True, 3: False}, window=1)
        self.assertEqual(metrics["over_merge_errors"], 1)
        self.assertEqual(metrics["over_split_errors"], 1)
        self.assertAlmostEqual(metrics["window_diff"], 2 / 3)

    def test_benchmark_tunes_only_development_and_rejects_unreviewed_labels_before_encoding(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            dev = example_document("dev")
            test = example_document("test", ["Different words", "Another topic", "We continue", "This is over"])
            manifest = {"development": [{"dialogue": "dev.json", "labels": "dev.labels.json"}],
                        "test": [{"dialogue": "test.json", "labels": "test.labels.json"}]}
            write_json(root / "dev.json", dev)
            write_json(root / "test.json", test)
            write_json(root / "dev.labels.json", confirmed_reference(dev, {2}))
            write_json(root / "test.labels.json", confirmed_reference(test, {1}))
            write_json(root / "manifest.json", manifest)
            calls = []
            def encoder(document, **kwargs):
                calls.append(document["document_id"])
                return np.asarray([[1, 0], [1, 0], [0, 1], [0, 1]]), {"model": "synthetic"}
            report = benchmark(root / "manifest.json", backends=["mpnet"], thresholds=[0.1, 1.5],
                               cohesion_threshold=2, encoder=encoder)
            self.assertEqual(report["models"]["mpnet"]["chosen_threshold"], 0.1)
            self.assertFalse(report["test_used_for_threshold_selection"])
            self.assertEqual(calls, ["dev", "test"])
            unreviewed = review_template(dev)
            write_json(root / "dev.labels.json", unreviewed, overwrite=True)
            calls.clear()
            with self.assertRaises(ValueError):
                benchmark(root / "manifest.json", backends=["mpnet"], thresholds=[0.1], encoder=encoder)
            self.assertEqual(calls, [])

    def test_duplicate_content_across_splits_is_rejected_even_with_different_ids(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = {"development": [], "test": []}
            for split in manifest:
                document = example_document(split)
                write_json(root / f"{split}.json", document)
                write_json(root / f"{split}.labels.json", confirmed_reference(document, {2}))
                manifest[split].append({"dialogue": f"{split}.json", "labels": f"{split}.labels.json"})
            write_json(root / "manifest.json", manifest)
            with self.assertRaises(ValueError):
                benchmark(root / "manifest.json", backends=["mpnet"], thresholds=[0.1])


class EmbeddingAdapterTests(unittest.TestCase):
    def fake_backend(self):
        calls = []
        class FakeSentenceTransformer:
            max_seq_length = 384
            tokenizer = staticmethod(lambda text, **kwargs: {"input_ids": list(range(len(text.split())))})
            def __init__(self, model, **kwargs):
                self.revision = kwargs.get("revision") or "resolved-commit"
            def __getitem__(self, index):
                return types.SimpleNamespace(auto_model=types.SimpleNamespace(config=types.SimpleNamespace(_commit_hash=self.revision)))
            def encode(self, texts, **kwargs):
                calls.append(kwargs["prompt"])
                return [[1, 0] for _ in texts]
        return types.SimpleNamespace(SentenceTransformer=FakeSentenceTransformer), calls

    def test_prompt_revision_and_text_isolate_embedding_caches(self):
        module, calls = self.fake_backend()
        document = example_document()
        with tempfile.TemporaryDirectory() as temporary, patch.dict(sys.modules, {"sentence_transformers": module}):
            cache = Path(temporary)
            _, first = embed_dialogue(document, backend="embeddinggemma", cache_dir=cache)
            _, second = embed_dialogue(document, backend="embeddinggemma", cache_dir=cache)
            _, changed = embed_dialogue(document, backend="embeddinggemma", cache_dir=cache, revision="new-commit")
            _, mpnet = embed_dialogue(document, backend="mpnet", cache_dir=cache)
            self.assertTrue(second["cache_hit"])
            self.assertNotEqual(first["cache_key"], changed["cache_key"])
            self.assertNotEqual(first["cache_key"], mpnet["cache_key"])
            self.assertEqual(calls, ["task: clustering | query: ", "task: clustering | query: ", ""])

    def test_model_input_truncation_is_rejected_before_encoding(self):
        module, calls = self.fake_backend()
        document = example_document(texts=["word " * 400])
        with patch.dict(sys.modules, {"sentence_transformers": module}), self.assertRaises(ValueError):
            embed_dialogue(document, backend="mpnet")
        self.assertEqual(calls, [])


class CliTests(unittest.TestCase):
    def test_import_normalize_review_cli_without_magi_or_model_downloads(self):
        with tempfile.TemporaryDirectory() as temporary, redirect_stdout(io.StringIO()):
            root = Path(temporary)
            write_json(root / "input.json", [{"text": "Hello!", "speaker_id": 1, "page": 1},
                                               {"text": "Hi!", "speaker_id": 2, "page": 1}])
            commands = [
                ["import-utterances", str(root / "input.json"), "--output", str(root / "raw.json")],
                ["normalize", str(root / "raw.json"), "--output", str(root / "normalized.json")],
                ["review", str(root / "normalized.json"), "--labels", str(root / "labels.json")],
            ]
            for command in commands:
                self.assertEqual(run(build_parser().parse_args(command)), 0)
            self.assertTrue((root / "labels.md").is_file())
            self.assertEqual(read_json(root / "labels.json")["status"], "proposed")
            with self.assertRaises(FileExistsError):
                run(build_parser().parse_args(commands[0]))
            with self.assertRaises(ValueError):
                run(build_parser().parse_args(["normalize", str(root / "raw.json"), "--output", str(root / "raw.json")]))

    def test_main_exports_json_and_keeps_legacy_transcript(self):
        import main as entrypoint
        prediction = {"texts": [[0, 0, 10, 10]], "ocr": ["Three years later..."],
                      "characters": [], "character_names": [], "character_cluster_labels": [],
                      "is_essential_text": [False], "text_character_associations": []}
        class FakeMagi:
            def eval(self): return self
            def to(self, device): return self
            def predict_detections_and_associations(self, *args, **kwargs): return [copy.deepcopy(prediction)]
            def do_chapter_wide_prediction(self, *args, **kwargs): return [copy.deepcopy(prediction)]
        with tempfile.TemporaryDirectory() as temporary, redirect_stdout(io.StringIO()):
            root = Path(temporary)
            (root / "images").mkdir()
            args = entrypoint.build_parser().parse_args([str(root / "images"), "--bank-root", str(root / "banks"),
                                                        "--output", str(root / "transcript.txt"), "--no-visualizations"])
            with patch.object(entrypoint, "discover_pages", return_value=[root / "images" / "p1.png"]), \
                 patch.object(entrypoint, "read_image", return_value=np.zeros((20, 20, 3), dtype=np.uint8)), \
                 patch.object(entrypoint.AutoModel, "from_pretrained", return_value=FakeMagi()):
                entrypoint.run(args)
            raw = read_json(root / "transcript.raw.json")
            normalized = read_json(root / "transcript.normalized.json")
            self.assertEqual(len(raw["texts"]), 1)
            self.assertEqual(normalized["utterances"][0]["content_type"], "narration")
            self.assertNotIn("Three years later", (root / "transcript.txt").read_text())


if __name__ == "__main__":
    unittest.main()
