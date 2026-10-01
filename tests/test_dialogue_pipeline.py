from __future__ import annotations

import copy
import io
import sys
import tempfile
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

    def test_context_keeps_nearby_turns(self):
        document = example_document()
        context = context_for_utterance(document, 1, radius=1)
        self.assertEqual(len(context), 3)
        self.assertEqual([row["text"] for row in context],
                         [row["text"] for row in document["utterances"][:3]])


class CliTests(unittest.TestCase):
    def test_import_normalize_cli_without_magi_or_model_downloads(self):
        with tempfile.TemporaryDirectory() as temporary, redirect_stdout(io.StringIO()):
            root = Path(temporary)
            write_json(root / "input.json", [{"text": "Hello!", "speaker_id": 1, "page": 1},
                                               {"text": "Hi!", "speaker_id": 2, "page": 1}])
            commands = [
                ["import-utterances", str(root / "input.json"), "--output", str(root / "raw.json")],
                ["normalize", str(root / "raw.json"), "--output", str(root / "normalized.json")],
            ]
            for command in commands:
                self.assertEqual(run(build_parser().parse_args(command)), 0)
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
