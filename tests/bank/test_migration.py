import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.helpers import atomic_json
from manga_pipeline.extraction.magi import MODEL_FINGERPRINT
from manga_pipeline.pipeline import prepare_bank
from manga_pipeline.storage.io import read_json


class MigrationTests(unittest.TestCase):
    def legacy(self, root):
        old = Path(root) / "old" / "story"
        metadata = {"schema_version": 1, "model_fingerprint": MODEL_FINGERPRINT,
                    "characters": {"7": {"id": 7, "display_name": "Zoro"}}, "pending": {}}
        atomic_json(old / "metadata.json", metadata)
        (old / "embeddings.npz").write_bytes(b"unchanged vectors")
        (old / "crops").mkdir()
        (old / "crops" / "7.png").write_bytes(b"unchanged crop")
        return old, metadata

    def test_copy_preserves_identity_vectors_crops_and_legacy(self):
        with tempfile.TemporaryDirectory() as root:
            old, metadata = self.legacy(root)
            before = (old / "metadata.json").read_bytes()
            path = prepare_bank(Path(root) / "banks", "story", old.parent)
            self.assertEqual(read_json(path), metadata)
            self.assertEqual(read_json(path.parent / "migration_backup.json"), metadata)
            for file in ("embeddings.npz", "crops/7.png"):
                self.assertEqual((path.parent / file).read_bytes(), (old / file).read_bytes())
            self.assertEqual((old / "metadata.json").read_bytes(), before)
            # A later prepare accepts the published bank without copying again.
            with patch("manga_pipeline.pipeline.shutil.copytree", side_effect=AssertionError("copied twice")):
                self.assertEqual(prepare_bank(Path(root) / "banks", "story", old.parent), path)

    def test_interrupted_copy_never_publishes_partial_bank_and_can_retry(self):
        with tempfile.TemporaryDirectory() as root:
            old, metadata = self.legacy(root)
            target = Path(root) / "banks" / "story"

            def fail_copy(source, destination, **kwargs):
                destination.mkdir()
                (destination / "partial").write_bytes(b"partial")
                raise KeyboardInterrupt()

            with patch("manga_pipeline.pipeline.shutil.copytree", side_effect=fail_copy):
                with self.assertRaises(KeyboardInterrupt):
                    prepare_bank(target.parent, "story", old.parent)
            self.assertFalse(target.exists())
            self.assertEqual(read_json(old / "metadata.json"), metadata)
            self.assertEqual(read_json(prepare_bank(target.parent, "story", old.parent)), metadata)

    def test_mismatched_legacy_model_is_rejected_without_publish(self):
        with tempfile.TemporaryDirectory() as root:
            old, metadata = self.legacy(root)
            metadata["model_fingerprint"] = "another-model"
            atomic_json(old / "metadata.json", metadata)
            with self.assertRaises(ValueError):
                prepare_bank(Path(root) / "banks", "story", old.parent)
            self.assertFalse((Path(root) / "banks" / "story").exists())
