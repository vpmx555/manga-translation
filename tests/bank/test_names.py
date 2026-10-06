import tempfile
import unittest
from pathlib import Path
from tests.helpers import make_store
from manga_pipeline.bank.names import NameBank
from manga_pipeline.storage.io import atomic_json, read_json


class NameBankTests(unittest.TestCase):
    def test_metadata_update_does_not_change_embeddings_or_crops(self):
        with tempfile.TemporaryDirectory() as root:
            store = make_store(root)
            bank = NameBank(store.manifest["bank_path"])
            vector = bank.path.parent / "embeddings" / "vectors.npz"
            crop = bank.path.parent / "crops" / "a.png"
            for path in (vector, crop):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"unchanged")
            bank.apply("op", 1, "Zoro", {})
            bank.apply("op", 1, "Zoro", {})
            self.assertEqual(vector.read_bytes(), b"unchanged")
            self.assertEqual(crop.read_bytes(), b"unchanged")
            self.assertEqual(len(bank.read()["characters"]["1"]["name_history"]), 1)

    def test_manual_edit_is_protected(self):
        with tempfile.TemporaryDirectory() as root:
            store = make_store(root)
            bank = NameBank(store.manifest["bank_path"])
            bank.apply("first", 1, "Wrong", {})
            value = bank.read()
            value["characters"]["1"]["display_name"] = "Corrected manually"
            atomic_json(bank.path, value)
            result = bank.apply("second", 1, "Other prediction", {})
            self.assertEqual(result["status"], "protected")
            self.assertEqual(bank.read()["characters"]["1"]["display_name"], "Corrected manually")

    def test_pending_cannot_be_named(self):
        with tempfile.TemporaryDirectory() as root:
            store = make_store(root, {})
            bank = NameBank(store.manifest["bank_path"])
            with self.assertRaises(ValueError):
                bank.apply("op", "pending", "Zoro", {})
