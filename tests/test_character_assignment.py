from __future__ import annotations

import inspect
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import torch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from character_assignment import DynamicCharacterAssigner  # noqa: E402
from character_bank import CharacterBank, normalize_story_key  # noqa: E402


class FakeModel:
    def __init__(self, embeddings_by_page):
        self.embeddings_by_page = embeddings_by_page
        self._character_assignment_panels = []

    def predict_crop_embeddings(self, images, bboxes):
        return [torch.tensor(value, dtype=torch.float32) for value in self.embeddings_by_page]

    def assign_names_to_characters(self, *args, **kwargs):
        raise AssertionError("The override was not installed")


class CharacterAssignmentTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def _bank(self):
        return CharacterBank(self.root, "My Story", model_fingerprint="test-model")

    @staticmethod
    def _images(count):
        return [np.full((32, 32, 3), index * 20, dtype=np.uint8) for index in range(count)]

    def test_story_key_is_unicode_normalized_and_case_insensitive(self):
        self.assertEqual(normalize_story_key("  One PIECE  "), "one-piece")
        self.assertEqual(normalize_story_key("Đại  Chiến"), "đại-chiến")

    def test_instance_binding_has_upstream_compatible_signature(self):
        with self._bank() as bank:
            model = FakeModel([])
            DynamicCharacterAssigner(bank, []).bind(model)
            self.assertIs(model.assign_names_to_characters.__self__, model)
            self.assertEqual(
                list(inspect.signature(model.assign_names_to_characters).parameters),
                [
                    "images",
                    "character_bboxes",
                    "character_bank",
                    "character_clusters",
                    "eta",
                ],
            )

    def test_multi_detection_cluster_is_numbered_immediately(self):
        embeddings = [np.asarray([[1.0, 0.0], [0.99, 0.01]], dtype=np.float32)]
        with self._bank() as bank:
            model = FakeModel(embeddings)
            model._character_assignment_panels = [[[0, 0, 32, 32]]]
            assigner = DynamicCharacterAssigner(bank, ["chapter-1/page-1.png"])
            labels = assigner.assign_names_to_characters(
                model,
                self._images(1),
                [[[1, 1, 10, 10], [12, 1, 20, 10]]],
                {"images": [], "names": []},
                [[0, 0]],
            )
            self.assertEqual(labels, ["1", "1"])
            self.assertIn("1", bank.characters)

    def test_singleton_reappearance_on_another_page_promotes_and_rewrites(self):
        embeddings = [
            np.asarray([[1.0, 0.0]], dtype=np.float32),
            np.asarray([[0.999, 0.001]], dtype=np.float32),
        ]
        with self._bank() as bank:
            model = FakeModel(embeddings)
            model._character_assignment_panels = [
                [[0, 0, 32, 32]],
                [[0, 0, 32, 32]],
            ]
            assigner = DynamicCharacterAssigner(
                bank, ["chapter-1/page-1.png", "chapter-1/page-2.png"]
            )
            labels = assigner.assign_names_to_characters(
                model,
                self._images(2),
                [[[1, 1, 10, 10]], [[2, 2, 11, 11]]],
                {"images": [], "names": []},
                [[0], [0]],
            )
            self.assertEqual(labels, ["1", "1"])
            self.assertEqual(bank.pending, {})

    def test_singleton_does_not_confirm_on_same_page_key(self):
        with self._bank() as bank:
            bank.create_pending(
                "candidate",
                np.asarray([[1.0, 0.0]], dtype=np.float32),
                "chapter-1/page-1.png",
                [],
            )
            model = FakeModel([np.asarray([[1.0, 0.0]], dtype=np.float32)])
            model._character_assignment_panels = [[[0, 0, 32, 32]]]
            labels = DynamicCharacterAssigner(
                bank, ["chapter-1/page-1.png"]
            ).assign_names_to_characters(
                model,
                self._images(1),
                [[[1, 1, 10, 10]]],
                {"images": [], "names": []},
                [[0]],
            )
            self.assertEqual(labels, ["Other"])

    def test_singleton_is_promoted_across_separate_runs(self):
        with self._bank() as bank:
            first_model = FakeModel([np.asarray([[1.0, 0.0]], dtype=np.float32)])
            first_model._character_assignment_panels = [[[0, 0, 32, 32]]]
            first_labels = DynamicCharacterAssigner(
                bank, ["chapter-1/page-1.png"]
            ).assign_names_to_characters(
                first_model,
                self._images(1),
                [[[1, 1, 10, 10]]],
                {"images": [], "names": []},
                [[0]],
            )
            self.assertEqual(first_labels, ["Other"])

        with self._bank() as bank:
            second_model = FakeModel(
                [np.asarray([[0.999, 0.001]], dtype=np.float32)]
            )
            second_model._character_assignment_panels = [[[0, 0, 32, 32]]]
            second_labels = DynamicCharacterAssigner(
                bank, ["chapter-2/page-1.png"]
            ).assign_names_to_characters(
                second_model,
                self._images(1),
                [[[2, 2, 11, 11]]],
                {"images": [], "names": []},
                [[0]],
            )
            self.assertEqual(second_labels, ["1"])
            self.assertEqual(bank.pending, {})

    def test_same_character_can_match_in_different_panels(self):
        with self._bank() as bank:
            bank.create_character(np.asarray([[1.0, 0.0]], dtype=np.float32), [])
            model = FakeModel(
                [np.asarray([[1.0, 0.0], [1.0, 0.0]], dtype=np.float32)]
            )
            model._character_assignment_panels = [
                [[0, 0, 15, 32], [16, 0, 32, 32]]
            ]
            labels = DynamicCharacterAssigner(
                bank, ["chapter-1/page-1.png"]
            ).assign_names_to_characters(
                model,
                self._images(1),
                [[[1, 1, 10, 10], [20, 1, 30, 10]]],
                {"images": [], "names": []},
                [[0, 1]],
            )
            self.assertEqual(labels, ["1", "1"])

    def test_bank_persists_and_supports_safe_edits(self):
        with self._bank() as bank:
            character_id = bank.create_character(
                np.asarray([[1.0, 0.0], [0.0, 1.0]], dtype=np.float32),
                ["crops/a.png"],
            )
            bank.rename_character(character_id, "Luffy")
            bank.disable_prototype(character_id, 1)
            bank.disable_crop(character_id, 0)

        with self._bank() as bank:
            self.assertEqual(bank.display_label(character_id), "Luffy")
            self.assertEqual(len(bank.get_character_embeddings(character_id)), 1)
            self.assertEqual(
                bank.characters[str(character_id)]["disabled_crop_paths"],
                ["crops/a.png"],
            )


if __name__ == "__main__":
    unittest.main()
