import copy
import tempfile
import unittest
from pathlib import Path
from tests.helpers import raw_document
from manga_pipeline.normalization.noise import DEFAULT_RULES, rules_for
from manga_pipeline.pipeline import load_config
from manga_pipeline.storage.io import atomic_json, file_hash, read_json
from manga_pipeline.storage.runs import RunStore


class NoiseTests(unittest.TestCase):
    def test_story_exception_and_snapshot_are_stable(self):
        with tempfile.TemporaryDirectory() as root:
            bank = Path(root) / "bank/metadata.json"
            atomic_json(bank, {"characters": {}})
            custom = bank.parent / "normalization_rules.json"
            atomic_json(custom, {"keep": ["Clench"], "drop": {"sfx": ["custom sound"]}})
            rules = rules_for(custom)
            raw = raw_document(text="Clench")
            self.assertIn("Clench", rules["keep"])
            store = RunStore.create(Path(root) / "run", source={"images": []}, config=load_config(), bank_path=bank)
            snapshot = store.directory / "snapshots/noise_rules.json"
            before = file_hash(snapshot)
            atomic_json(custom, {"keep": [], "drop": {}})
            self.assertEqual(before, file_hash(snapshot))
            self.assertIn("Clench", read_json(snapshot)["keep"])


if __name__ == "__main__":
    unittest.main()
