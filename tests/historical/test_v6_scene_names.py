"""Combined scene/name contract checks; no models or network."""
# Historical contract test; requires the matching frozen source.
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from manga_pipeline.v6 import scene
from manga_pipeline.v6.independent import analyze
from manga_pipeline.pipeline import load_config
from manga_pipeline.v6 import semantic
from test_v6_independent_batches import fixture, Provider


class Calls:
    def __init__(self, response):
        self.response, self.count = response, 0

    def call(self, *args, **kwargs):
        self.count += 1
        return self.response


class JointScanTests(unittest.TestCase):
    def test_one_call_exact_offsets_repeated_names_and_nonessential(self):
        rows = [{"id": "a", "text": "Hi Ayame-san. Ayame-san!"}]
        extra = {"id": "b", "text": "SASA-MORI"}
        calls = Calls({"scenes": [{"start_id": "u0", "summary": "They talk."}],
                       "names": [{"utterance_id": "u0", "name": "Ayame-san"},
                                 {"utterance_id": "u1", "name": "SASA-MORI"}]})
        scenes, names = scene.plan(rows, calls, rows + [extra])
        self.assertEqual(calls.count, 1)
        self.assertEqual(scenes[0]["target_ids"], ["a"])
        self.assertEqual(len(names["a"]), 2)
        for row in rows + [extra]:
            for mention in names[row["id"]]:
                self.assertEqual(row["text"][mention["start"]:mention["end"]], mention["name"])
                self.assertEqual(mention["source"], "llm_scene")

    def test_rejects_invented_corrected_or_partial_names(self):
        rows = [{"id": "a", "text": "MAD01 AYAME"}]
        for entry in ({"utterance_id": "a", "name": "Madoi"},
                      {"utterance_id": "a", "name": "AYA"},
                      {"utterance_id": "wrong", "name": "AYAME"}, None):
            with self.assertRaises(ValueError):
                scene.validate_names({"names": [entry]}, rows)

    def test_empty_chapter_and_no_essential_dialogue(self):
        self.assertEqual(scene.plan([], None, []), ([], {}))
        calls = Calls({"scenes": [], "names": []})
        self.assertEqual(scene.plan([], calls, [{"id": "a", "text": "Bang"}]), ([], {"a": []}))
        self.assertEqual(calls.count, 1)

    def test_defaults(self):
        self.assertEqual(load_config()["ner"]["backend"], "llm-scene")

    def test_scan_then_analyze_reuses_scene_and_resume_receipt(self):
        class JointProvider(Provider):
            def infer_reserved(self, task, prompt, data, schema, validator, *, reserve):
                value = super().infer_reserved(task, prompt, data, schema, validator, reserve=reserve)
                if task == "analyze_scene_v6":
                    value["names"] = []
                    value["name_normalization"] = {"patches": [], "groups": [], "suspicions": []}
                return value

        with tempfile.TemporaryDirectory() as directory:
            store, raw, document, _, _ = fixture(Path(directory), 2)
            store.manifest["config"]["ner"]["backend"] = "llm-scene"
            store.target = lambda *args: None
            provider = JointProvider()
            scanned = semantic.scan(store, document, raw, None, provider=provider)
            with patch.object(scene, "plan", side_effect=AssertionError("Duplicate scene call")):
                output = analyze(store, raw, document, scanned, provider)
            self.assertEqual(output["scenes"][0]["target_ids"], ["u0", "u1"])
            semantic.scan(store, document, raw, None, provider=provider)
            self.assertEqual(sum(task == "analyze_scene_v6" for task, *_ in provider.calls), 1)
            document["utterances"][0]["text"] = "Changed"
            with self.assertRaisesRegex(ValueError, "stale|source changed"):
                analyze(store, raw, document, scanned, provider)


if __name__ == "__main__":
    unittest.main()
