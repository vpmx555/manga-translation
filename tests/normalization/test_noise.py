import copy
import tempfile
import unittest
from pathlib import Path
from tests.helpers import raw_document
from manga_pipeline.normalization.dialogue import normalize_document
from manga_pipeline.normalization.noise import DEFAULT_RULES, rules_for
from manga_pipeline.pipeline import load_config
from manga_pipeline.storage.io import atomic_json, file_hash, read_json
from manga_pipeline.storage.runs import RunStore


class NoiseTests(unittest.TestCase):
    def test_whole_box_filter_before_both_essential_routes_and_keep_ambiguous(self):
        raw = raw_document()
        examples = [("Clench", True), ("Hahhh...", False), ("Whoosh", False),
                    ("Ahhh...! I should've just said that...", True), ("Clench your fists!", True),
                    ("So cute!", False), ("What", False), ("Why", False), ("...", False),
                    ("NNGGGGGGGGGGGGH...", True), ("Load more comments", False),
                    ("Read more at the library.", True), ("Read more at https://scans.example/chapter1", False)]
        raw["texts"] = [{**copy.deepcopy(raw["texts"][0]), "id": f"t{i}", "reading_order": i,
                         "text": text, "is_essential_text": essential} for i, (text, essential) in enumerate(examples)]
        output = normalize_document(raw, essential_only=True, analysis_policy="essential-v3", noise_rules=DEFAULT_RULES)
        self.assertEqual({x["text_original"] for x in output["excluded"]},
                         {"Clench", "Hahhh...", "Whoosh", "Load more comments", "Read more at https://scans.example/chapter1"})
        kept = [*output["utterances"], *output["translation_only"]]
        self.assertEqual(len(kept), 8)
        self.assertTrue(all(x.get("content_review_status") == "needs_review" for x in kept
                            if x["text"] in {"...", "NNGGGGGGGGGGGGH...", "Read more at the library."}))
        self.assertIn("bbox", output["excluded"][0])
        self.assertTrue(any(x["text"] == "Clench" for x in normalize_document(raw, essential_only=True, analysis_policy="essential-v3")["utterances"]))

    def test_story_exception_and_snapshot_are_stable(self):
        with tempfile.TemporaryDirectory() as root:
            bank = Path(root) / "bank/metadata.json"
            atomic_json(bank, {"characters": {}})
            custom = bank.parent / "normalization_rules.json"
            atomic_json(custom, {"keep": ["Clench"], "drop": {"sfx": ["custom sound"]}})
            rules = rules_for(custom)
            raw = raw_document(text="Clench")
            self.assertEqual(len(normalize_document(raw, essential_only=True, analysis_policy="essential-v3", noise_rules=rules)["utterances"]), 1)
            store = RunStore.create(Path(root) / "run", source={"images": []}, config=load_config(), bank_path=bank)
            snapshot = store.directory / "snapshots/noise_rules.json"
            before = file_hash(snapshot)
            atomic_json(custom, {"keep": [], "drop": {}})
            self.assertEqual(before, file_hash(snapshot))
            self.assertIn("Clench", read_json(snapshot)["keep"])


if __name__ == "__main__":
    unittest.main()
