import tempfile
import unittest
from tests.helpers import make_store
from manga_pipeline.storage.runs import RunStore
from manga_pipeline.storage.io import atomic_json, read_json


class ResumeTests(unittest.TestCase):
    def test_receipt_recovers_manifest_after_interruption(self):
        with tempfile.TemporaryDirectory() as root:
            store = make_store(root)
            store.finish("layout", {"kind": "test"})
            manifest = read_json(store.path)
            manifest["steps"]["layout"] = {"status": "running"}
            atomic_json(store.path, manifest)
            recovered = RunStore(store.directory)
            self.assertTrue(recovered.completed("layout"))
            self.assertEqual(recovered.read("layout"), {"kind": "test"})

    def test_completed_artifact_corruption_is_not_silently_recomputed(self):
        with tempfile.TemporaryDirectory() as root:
            store = make_store(root)
            store.finish("layout", {"kind": "test"})
            atomic_json(store.artifact_path("layout"), {"kind": "changed"})
            with self.assertRaises(ValueError):
                RunStore(store.directory).read("layout")

    def test_config_change_and_legacy_run_are_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            store = make_store(root)
            manifest = read_json(store.path)
            manifest["config"]["device"] = "cuda"
            atomic_json(store.path, manifest)
            with self.assertRaises(ValueError):
                RunStore(store.directory)
            manifest["pipeline_version"] = 1
            atomic_json(store.path, manifest)
            with self.assertRaises(ValueError):
                RunStore(store.directory)
