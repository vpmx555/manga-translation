import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

from tests.bank.test_assignment import FakeModel
from manga_pipeline.bank.assignment import DynamicCharacterAssigner
from manga_pipeline.bank.identity import CharacterBank
from manga_pipeline.bank.prototypes import closest_indices, consensus_mean


class ConsensusPrototypeTests(unittest.TestCase):
    def test_outlier_does_not_pull_mean_away_from_consistent_group(self):
        vectors = np.asarray([[1, 0]]*14 + [[1, .03], [1, -.03], [0, 1], [-1, 0]], dtype=np.float32)
        self.assertLess(np.linalg.norm(consensus_mean(vectors) - [1, 0]), 1e-5)
        np.testing.assert_allclose(consensus_mean(vectors[::-1]), consensus_mean(vectors), atol=1e-6)

    def test_pool_budget_keeps_compact_group_instead_of_distant_outlier(self):
        values = np.asarray([[1, 0], [1, .02], [1, -.02], [-1, 0]], dtype=np.float32)
        self.assertEqual(closest_indices(values, 3), [0, 1, 2])

    def test_sparse_and_opposing_samples_remain_finite_and_unit_length(self):
        for values in ([[3, 4]], [[1, 0], [0, 1]], [[1, 0], [-1, 0]]):
            mean = consensus_mean(values)
            self.assertTrue(np.isfinite(mean).all())
            self.assertAlmostEqual(float(np.linalg.norm(mean)), 1, places=6)
        np.testing.assert_allclose(consensus_mean([[1, 0], [0, 1]]), [2**-.5, 2**-.5])

    def test_bad_vectors_are_rejected(self):
        for values in ([], [[0, 0]], [[float('nan'), 1]]):
            with self.assertRaises(ValueError):
                consensus_mean(values)

    def test_cluster_with_bad_crop_still_matches_and_survives_reload(self):
        with tempfile.TemporaryDirectory() as temporary:
            with CharacterBank(Path(temporary), 'Story', model_fingerprint='test') as bank:
                bank.create_character(np.asarray([[1, 0], [1, .02], [1, -.02], [-1, 0]], dtype=np.float32), [])
                vectors = np.asarray([[1, 0]]*14 + [[1, .01], [1, -.01], [0, 1], [-1, 0]], dtype=np.float32)
                model = FakeModel([vectors])
                model._character_assignment_panels = [[[0, 0, 32, 32]]]
                assigner = DynamicCharacterAssigner(bank, ['chapter/p1'])
                assigner.assign_names_to_characters(model, [np.zeros((32, 32, 3), dtype=np.uint8)],
                    [[[1, 1, 10, 10]] * len(vectors)], {}, [[0] * len(vectors)])
                self.assertEqual(assigner.last_character_ids, [1] * len(vectors))
                self.assertEqual(len(bank.characters), 1)
                before = consensus_mean(bank.get_character_embeddings(1))
            with CharacterBank(Path(temporary), 'Story', model_fingerprint='test') as bank:
                np.testing.assert_allclose(consensus_mean(bank.get_character_embeddings(1)), before)

    def test_merge_selects_support_from_both_banks_instead_of_truncating_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            with CharacterBank(Path(temporary), 'Story', model_fingerprint='test') as bank:
                bank.config['max_embeddings_per_character'] = 3
                target = bank.create_character(np.asarray([[-1, 0], [0, -1]], dtype=np.float32), [])
                source = bank.create_character(np.asarray([[1, 0], [1, .02], [1, -.02]], dtype=np.float32), [])
                bank.merge_characters(source, target)
                self.assertTrue((bank.get_character_embeddings(target)[:, 0] > 0).all())

    def test_mean_uses_all_samples_up_to_sixteen_instead_of_sixty_percent(self):
        values = np.asarray([[1, 0], [1, .1], [1, .2], [1, .3], [1, .4]], dtype=np.float32)
        unit = values / np.linalg.norm(values, axis=1, keepdims=True)
        expected = unit.mean(axis=0)
        expected /= np.linalg.norm(expected)
        np.testing.assert_allclose(consensus_mean(values), expected, atol=1e-6)

    def test_selected_vectors_keep_their_exact_crop_paths_after_merge(self):
        with tempfile.TemporaryDirectory() as temporary:
            with CharacterBank(Path(temporary), 'Story', model_fingerprint='test') as bank:
                vectors = np.asarray([[1, i*.001] for i in range(20)], dtype=np.float32)
                first = bank.create_character(vectors[:10], [f'crops/{i}.png' for i in range(10)])
                second = bank.create_character(vectors[10:], [f'crops/{i}.png' for i in range(10,20)])
                bank.merge_characters(second, first)
                stored = bank.get_character_embeddings(first)
                paths = bank.characters[str(first)]['prototype_crop_paths']
                self.assertEqual(len(stored), 16)
                self.assertEqual(paths, bank.characters[str(first)]['crop_paths'])
                for vector, path in zip(stored, paths):
                    np.testing.assert_array_equal(vector, vectors[int(Path(path).stem)])

    def test_full_assignment_saves_sixteen_selected_vectors_with_matching_images(self):
        with tempfile.TemporaryDirectory() as temporary:
            image = np.zeros((8, 200, 3), dtype=np.uint8)
            boxes = []
            values = np.asarray([[1, i*.01] for i in range(20)], dtype=np.float32)
            normalized = values / np.linalg.norm(values, axis=1, keepdims=True)
            for i in range(20):
                image[:, i*10:i*10+8] = i
                boxes.append([i*10, 0, i*10+8, 8])
            with CharacterBank(Path(temporary), 'Story', model_fingerprint='test') as bank:
                model = FakeModel([values])
                model._character_assignment_panels = [[[0, 0, 200, 8]]]
                assigner = DynamicCharacterAssigner(bank, ['chapter/p1'])
                assigner.assign_names_to_characters(model, [image], [boxes], {}, [[0]*20])
            with CharacterBank(Path(temporary), 'Story', model_fingerprint='test') as bank:
                vectors = bank.get_character_embeddings(1)
                record = bank.characters['1']
                self.assertEqual(len(vectors), 16)
                self.assertEqual(record['prototype_crop_paths'], record['crop_paths'])
                self.assertEqual(len(set(record['prototype_source_keys'])), 16)
                for vector, crop in zip(vectors, record['prototype_crop_paths']):
                    with Image.open(bank.directory / crop) as saved:
                        index = saved.getpixel((0, 0))[0]
                    np.testing.assert_allclose(vector, normalized[index], atol=1e-6)


if __name__ == '__main__':
    unittest.main()
