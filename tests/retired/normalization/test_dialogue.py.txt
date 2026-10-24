import unittest
from tests.helpers import raw_document
from manga_pipeline.normalization.dialogue import normalize_document, validate_normalized


class NormalizationTests(unittest.TestCase):
    def test_source_preserved_without_scene_fields(self):
        raw = raw_document([1], "  This\n is Zoro. ")
        doc = normalize_document(raw)
        validate_normalized(doc)
        row = doc["utterances"][0]
        self.assertEqual(row["text"], "This is Zoro.")
        self.assertEqual(row["text_original"], raw["texts"][0]["text"])
        self.assertEqual(row["source_boxes"][0]["bbox"], raw["texts"][0]["bbox"])
        self.assertNotIn("scene_id", row)
