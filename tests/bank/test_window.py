import tempfile
import unittest
from pathlib import Path

import numpy as np
from tests.helpers import make_store
from tests.bank.test_assignment import FakeModel
from manga_pipeline.bank.assignment import DynamicCharacterAssigner
from manga_pipeline.bank.identity import CharacterBank


class PendingWindowTests(unittest.TestCase):
    def assign(self, bank, visible_pages, total):
        embeddings = [np.asarray([[1.0, 0.0]], dtype=np.float32) if i in visible_pages
                      else np.empty((0, 2), dtype=np.float32) for i in range(total)]
        boxes = [[[1, 1, 10, 10]] if i in visible_pages else [] for i in range(total)]
        clusters = [[0] if i in visible_pages else [] for i in range(total)]
        images = [np.zeros((32, 32, 3), dtype=np.uint8) for _ in range(total)]
        model = FakeModel(embeddings)
        model._character_assignment_panels = [[[0, 0, 32, 32]] for _ in images]
        assigner = DynamicCharacterAssigner(bank, [f"chapter/page-{i + 1}.png" for i in range(total)])
        labels = assigner.assign_names_to_characters(model, images, boxes, {"images": [], "names": []}, clusters)
        return labels, assigner

    def test_page_one_can_promote_on_page_three(self):
        with tempfile.TemporaryDirectory() as root, CharacterBank(Path(root), "Story", model_fingerprint="test") as bank:
            labels, assigner = self.assign(bank, {0, 2}, 3)
            self.assertEqual(labels, ["1", "1"])
            self.assertEqual(assigner.last_character_ids, [1, 1])

    def test_page_one_cannot_match_page_four_while_pending(self):
        with tempfile.TemporaryDirectory() as root, CharacterBank(Path(root), "Story", model_fingerprint="test") as bank:
            labels, assigner = self.assign(bank, {0, 3}, 4)
            self.assertEqual(labels, ["Other", "Other"])
            self.assertEqual(bank.characters, {})
            self.assertEqual(len(bank.pending), 2)
            self.assertEqual(sum(p.get("state") == "closed" for p in bank.pending.values()), 1)

    def test_stable_id_can_match_after_pending_window(self):
        with tempfile.TemporaryDirectory() as root, CharacterBank(Path(root), "Story", model_fingerprint="test") as bank:
            bank.create_character(np.asarray([[1.0, 0.0]], dtype=np.float32), [])
            labels, _ = self.assign(bank, {0, 3}, 4)
            self.assertEqual(labels, ["1", "1"])

    def test_pending_closes_even_if_later_pages_have_no_detections(self):
        with tempfile.TemporaryDirectory() as root, CharacterBank(Path(root), "Story", model_fingerprint="test") as bank:
            self.assign(bank, {0}, 4)
            self.assertEqual(next(iter(bank.pending.values()))["state"], "closed")
