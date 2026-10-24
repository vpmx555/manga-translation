import copy
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))

from manga_pipeline.bank.config import DEFAULT_CONFIG, validate_config
from manga_pipeline.bank.identity import CharacterBank
from manga_pipeline.extraction.config import DETECTION_DEFAULTS, threshold_for_manifest
from manga_pipeline.pipeline import load_config


class PipelineBankDefaultsTests(unittest.TestCase):
    def test_all_shipped_pipeline_configs_use_the_final_character_policy(self):
        for name in ('pipeline.json', 'pipeline-v6-runpod.json', 'pipeline-visual.json', 'pipeline-visual-runpod.json'):
            with self.subTest(config=name):
                config = load_config(ROOT / 'configs' / name)
                self.assertEqual(config['character_detection_threshold'], .3)
                self.assertEqual(config['character_character_matching_threshold'], .81)
                self.assertEqual(config['character_bank'], DEFAULT_CONFIG)

    def test_code_defaults_match_main_pipeline(self):
        config = load_config()
        self.assertEqual(config['character_bank'], DEFAULT_CONFIG)
        self.assertEqual(DETECTION_DEFAULTS['character_character_matching_threshold'], .81)

    def test_frozen_policy_overrides_old_bank_thresholds_during_extraction(self):
        with tempfile.TemporaryDirectory() as temporary:
            with CharacterBank(Path(temporary), 'Story', model_fingerprint='test') as bank:
                bank.config.update(max_distance=.65, min_margin=.02, pending_max_page_gap=2)
            with CharacterBank(Path(temporary), 'Story', model_fingerprint='test') as bank:
                bank.apply_config(DEFAULT_CONFIG)
            with CharacterBank(Path(temporary), 'Story', model_fingerprint='test') as bank:
                for key, value in DEFAULT_CONFIG.items():
                    self.assertEqual(bank.config[key], value)

    def test_invalid_bank_threshold_and_policy_are_rejected(self):
        for key, value in (('min_margin', float('nan')), ('max_distance', -1),
                           ('assignment_strategy', 'sequential'), ('max_embeddings_per_character', 8)):
            config = copy.deepcopy(DEFAULT_CONFIG)
            config[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_config(config)

    def test_old_implicit_manifest_thresholds_are_preserved(self):
        self.assertEqual(threshold_for_manifest({}, 'character_character_matching_threshold'), .75)
        self.assertEqual(threshold_for_manifest({'character_character_matching_threshold': .55},
                                              'character_character_matching_threshold'), .55)


if __name__ == '__main__':
    unittest.main()
