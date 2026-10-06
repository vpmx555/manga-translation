import tempfile
import unittest
from pathlib import Path

from tests.helpers import make_store
from manga_pipeline.pipeline import load_config
from manga_pipeline.providers.gliner_ner import GlinerNames
from manga_pipeline.providers.names import create_detector
from manga_pipeline.providers.spacy_ner import SpacyNames
from manga_pipeline.storage.io import atomic_json


class GlinerTests(unittest.TestCase):
    def test_model_is_released_after_scan(self):
        detector = GlinerNames(load_config()["ner"])
        detector.model = object()
        detector.close()
        self.assertIsNone(detector.model)

    def test_new_default_and_legacy_backend_are_lazy(self):
        detector = create_detector(load_config())
        self.assertIsInstance(detector, GlinerNames)
        self.assertIsNone(detector.model)
        with tempfile.TemporaryDirectory() as root:
            legacy = make_store(root)
            self.assertIsInstance(create_detector(legacy.manifest["config"]), SpacyNames)

    def test_source_offsets_and_ocr_spelling_are_preserved(self):
        detector = GlinerNames(load_config()["ner"])
        calls = []

        class Model:
            def predict_entities(self, text, labels, threshold):
                calls.append((text, labels, threshold))
                return [{"text": "Madoi Ayame", "start": 0, "end": 11, "label": "person", "score": 0.9}]

        detector.model = Model()
        actual = detector.detect("MAD01 AYAME (16)")
        self.assertEqual(actual, [{"name": "MAD01 AYAME", "start": 0, "end": 11, "source": "gliner", "confidence": 0.9}])
        self.assertEqual(calls[0][1:], (["person name"], 0.85))

    def test_pronouns_and_honorifics_alone_are_removed_but_names_keep_suffix(self):
        detector = GlinerNames(load_config()["ner"])
        text = "I called arase- San, and Ai."

        class Model:
            def predict_entities(self, *args, **kwargs):
                return [{"start": 0, "end": 1, "score": 0.9}, {"start": 9, "end": 14, "score": 0.9},
                        {"start": 16, "end": 19, "score": 0.9}, {"start": 25, "end": 27, "score": 0.9}]

        detector.model = Model()
        names = detector.detect(text)
        self.assertEqual([x["name"] for x in names], ["arase- San", "Ai"])
        self.assertTrue(all(text[x["start"]:x["end"]] == x["name"] for x in names))

    def test_invalid_offsets_are_not_committed_as_names(self):
        detector = GlinerNames(load_config()["ner"])

        class Model:
            def predict_entities(self, *args, **kwargs):
                return [{"start": -1, "end": 50}]

        detector.model = Model()
        with self.assertRaises(ValueError):
            detector.detect("Arase Mahoru")

    def test_nested_threshold_override_keeps_pinned_model_and_validates(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "config.json"
            atomic_json(path, {"ner": {"threshold": 0.25}})
            config = load_config(path)
            self.assertEqual(config["ner"]["threshold"], 0.25)
            self.assertEqual(config["ner"]["model"], "gliner-community/gliner_small-v2.5")
            self.assertEqual(len(config["ner"]["revision"]), 40)
            atomic_json(path, {"ner": {"threshold": True}})
            with self.assertRaises(ValueError):
                load_config(path)


if __name__ == "__main__":
    unittest.main()
