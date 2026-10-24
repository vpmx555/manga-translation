"""Integrity boundaries of the OCR-only native publication lane, without models/network."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile
from unittest.mock import patch
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "deployment/runpod"))
import ocr_diagnostic as diagnostic


class DiagnosticIntegrityTests(unittest.TestCase):
    def test_dependency_delivery_survives_native_lock_ignore_rule(self):
        from PIL import Image
        import numpy as np
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            baseline = root / "baseline"
            baseline.mkdir()
            (baseline / "crops").mkdir()
            image = Image.new("RGB", (8, 8), "white")
            image.save(baseline / "crops/one.png")
            pixels = hashlib.sha256(np.asarray(image).tobytes()).hexdigest()
            row = {"id": "p1:t1", "page": 1, "text_index": 0, "bbox": [0, 0, 8, 8],
                   "crop": "crops/one.png", "crop_sha256": pixels,
                   "is_essential_text": True, "new_text": "TEXT", "paddle_text": "TEXT"}
            report = {"status": "completed", "completed": 1, "targets": 1, "pages": 1, "results": [row]}
            diagnostic.manage.atomic_json(baseline / "result.json", report)
            diagnostic.manage.atomic_json(baseline / "ocr-result.json", {"texts": []})
            source = root / "source/02_extract"
            source.mkdir(parents=True)
            (source / "result.json").write_text("{}")
            diagnostic.manage.atomic_json(baseline / "request.json", {
                "source_run": str(source.parent), "extract_hash": diagnostic.manage.file_hash(source / "result.json")})
            resolver = root / "resolver.json"
            diagnostic.manage.atomic_json(resolver, {"install": [{"metadata": {"name": "transformers", "version": "5.20.2"},
                "download_info": {"archive_info": {"hashes": {"sha256": "a" * 64}}}}]})

            def bundle(path):
                with zipfile.ZipFile(path, "w") as archive:
                    archive.writestr("__bundle__.json", "{}")
                return {"version": "frozen"}

            args = SimpleNamespace(batch="lock-regression", baseline=baseline, mobile=baseline,
                                   medium=baseline, resolver=resolver)
            with patch.object(diagnostic.transport, "STATE", root / "state"), \
                 patch.object(diagnostic.transport, "bundle", side_effect=bundle), \
                 patch("native_sync.prepare"):
                diagnostic.prepare(args)
            prepared = root / "state/v6/lock-regression"
            plan = diagnostic.manage.read_json(prepared / "plan.json")
            self.assertFalse(any(name.endswith(".lock") for name in plan["archives"]))
            with zipfile.ZipFile(prepared / "locks.zip") as archive:
                content = archive.read("requirements.lock")
            self.assertEqual(hashlib.sha256(content).hexdigest(), plan["dependency_lock_sha256"])

    def test_rejects_zip_path_escape(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "input.zip"
            with zipfile.ZipFile(archive, "w") as zipped:
                zipped.writestr("../outside.txt", "bad")
            with self.assertRaises(ValueError):
                diagnostic.unpack(archive, root / "safe")
            self.assertFalse((root / "outside.txt").exists())

    def test_never_verifies_active_writer(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            diagnostic.manage.atomic_json(root / "plan.json", {"batch": "test"})
            diagnostic.manage.atomic_json(root / "native-sync.json", {"destination": str(root / "replica")})
            diagnostic.manage.atomic_json(root / "replica/durable/test/jobs/state.json",
                                         {"status": "completed", "writer_stopped": False})
            self.assertIsNone(diagnostic.verify(root / "plan.json"))

    def test_verifies_failed_archive_and_rejects_bad_inventory(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            destination = root / "replica"
            diagnostic.manage.atomic_json(root / "plan.json", {"batch": "test"})
            diagnostic.manage.atomic_json(root / "native-sync.json", {"destination": str(destination)})
            archive = destination / "durable/test/published/ocr/results.zip"
            archive.parent.mkdir(parents=True)
            content = b"real failure evidence"
            inventory = {"failure.log": {"bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()}}

            def publish(inventory):
                with zipfile.ZipFile(archive, "w") as zipped:
                    zipped.writestr("failure.log", content)
                    zipped.writestr("inventory.json", json.dumps(inventory))
                diagnostic.manage.atomic_json(destination / "durable/test/jobs/state.json", {
                    "status": "failed", "writer_stopped": True,
                    "publication": {"archive_bytes": archive.stat().st_size,
                                    "archive_sha256": diagnostic.manage.file_hash(archive)}})

            publish(inventory)
            result = diagnostic.verify(root / "plan.json")
            self.assertEqual(result["pipeline_status"], "failed")
            self.assertEqual((destination / "review/failure.log").read_bytes(), content)
            inventory["failure.log"]["sha256"] = "0" * 64
            publish(inventory)
            with self.assertRaisesRegex(ValueError, "checksum"):
                diagnostic.verify(root / "plan.json")


if __name__ == "__main__":
    unittest.main()
