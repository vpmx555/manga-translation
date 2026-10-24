import tempfile
import unittest
from pathlib import Path

import numpy as np

from tests.bank.test_assignment import FakeModel
from manga_pipeline.bank.assignment import DynamicCharacterAssigner, _Observation
from manga_pipeline.bank.identity import CharacterBank


class PanelAssignmentTests(unittest.TestCase):
    def assign(self, reverse=False, alternative=False):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        with CharacterBank(Path(temporary.name), 'Story', model_fingerprint='test') as bank:
            bank.config.update(max_distance=.55, min_margin=0)
            bank.create_character(np.asarray([[1., 0.]], dtype=np.float32), [])
            if alternative:
                bank.create_character(np.asarray([[np.cos(.9), np.sin(.9)]], dtype=np.float32), [])
            angle = .4 if alternative else .55
            weak = [np.cos(angle), np.sin(angle)]
            strong = [np.cos(.1), np.sin(.1)]
            embeddings = [weak, strong, strong] if not reverse else [strong, strong, weak]
            labels = [0, 1, 1] if not reverse else [1, 1, 0]
            model = FakeModel([np.asarray(embeddings, dtype=np.float32)])
            model._character_assignment_panels = [[[0, 0, 32, 32]]]
            assigner = DynamicCharacterAssigner(bank, ['chapter/p1'])
            assigner.assign_names_to_characters(model, [np.zeros((32, 32, 3), dtype=np.uint8)],
                [[[1, 1, 10, 10], [11, 1, 20, 10], [21, 1, 30, 10]]], {}, [labels])
            ids = assigner.last_character_ids
            return ids if not reverse else [ids[2], ids[0], ids[1]]

    def test_strong_match_wins_even_when_weak_crop_arrives_first(self):
        self.assertEqual(self.assign(), [None, 1, 1])
        self.assertEqual(self.assign(reverse=True), [None, 1, 1])

    def test_loser_retries_next_candidate_with_frozen_page_scores(self):
        self.assertEqual(self.assign(alternative=True), [2, 1, 1])

    def test_cluster_reserves_all_panels_but_id_remains_reusable_elsewhere(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        with CharacterBank(Path(temporary.name), 'Story', model_fingerprint='test') as bank:
            bank.config.update(max_distance=.55, min_margin=0)
            assigner = DynamicCharacterAssigner(bank, ['p1'])
            def observation(label, panels):
                return _Observation(0, 'p1', label, [label], [label], np.asarray([[1., 0.]]),
                    np.asarray([1., 0.]), (), set(panels))
            assigner._candidate_scores = lambda obs, blocked: [(1, {0:.1, 1:.2, 2:.3}[obs.cluster_label])]
            plan = assigner._plan_page([observation(1, [2]), observation(2, [3]), observation(0, [1, 2])])
            self.assertEqual({obs.cluster_label: identity for obs, identity in plan}, {0:1, 1:None, 2:1})

    def test_frequent_bank_id_is_reserved_before_a_rarer_id(self):
        with tempfile.TemporaryDirectory() as temporary:
            with CharacterBank(Path(temporary), 'Story', model_fingerprint='test') as bank:
                bank.config.update(max_distance=.55, min_margin=0)
                bank.create_character(np.asarray([[1., 0.]]), [])
                bank.create_character(np.asarray([[0., 1.]]), [])
                bank.characters['1']['appearance_count'] = 20
                bank.characters['2']['appearance_count'] = 2
                assigner = DynamicCharacterAssigner(bank, ['p1'])
                observations = [_Observation(0, 'p1', i, [i], [i], np.asarray([[1., 0.]]),
                    np.asarray([1., 0.]), (), {0}) for i in range(2)]
                assigner._candidate_scores = lambda obs, blocked: [(2, .1)] if obs.cluster_label == 0 else [(1, .2)]
                plan = assigner._plan_page(observations)
                self.assertEqual([identity for obs, identity in plan], [1, 2])

    def test_replaying_same_observations_does_not_inflate_appearance_count(self):
        with tempfile.TemporaryDirectory() as temporary:
            with CharacterBank(Path(temporary), 'Story', model_fingerprint='test') as bank:
                model = FakeModel([np.asarray([[1., 0.], [1., .01]])])
                model._character_assignment_panels = [[[0, 0, 32, 32]]]
                for _ in range(2):
                    assigner = DynamicCharacterAssigner(bank, ['chapter/p1'])
                    assigner.assign_names_to_characters(model, [np.zeros((32, 32, 3), dtype=np.uint8)],
                        [[[1, 1, 10, 10], [11, 1, 20, 10]]], {}, [[0, 0]])
                    self.assertEqual(bank.characters['1']['appearance_count'], 2)
                self.assertEqual(len(bank.characters['1']['appearance_keys']), 2)


if __name__ == '__main__':
    unittest.main()
