"""Checkpoint writes tolerate temporary Windows file locks."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from manga_pipeline.storage.io import atomic_json


class AtomicCheckpointTests(unittest.TestCase):
    def test_temporary_lock_retries_and_writes_complete_checkpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "check.json"
            atomic_json(destination, {"status": "old"})
            real_replace = os.replace
            calls = []

            def replace(source, target):
                calls.append(source)
                if len(calls) < 3:
                    self.assertEqual(json.loads(destination.read_text())["status"], "old")
                    raise PermissionError("temporary reader lock")
                return real_replace(source, target)

            with patch("os.replace", side_effect=replace), patch("manga_pipeline.storage.io.time.sleep"):
                atomic_json(destination, {"status": "new", "rows": [1, 2]})
            self.assertEqual(json.loads(destination.read_text()), {"status": "new", "rows": [1, 2]})
            self.assertEqual(len(calls), 3)
            self.assertEqual(list(Path(directory).glob("*.tmp")), [])

    def test_permanent_lock_preserves_old_checkpoint_and_reports_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "check.json"
            atomic_json(destination, {"status": "old"})
            with patch("os.replace", side_effect=PermissionError("permanent lock")) as replace, \
                    patch("manga_pipeline.storage.io.time.sleep"):
                with self.assertRaises(PermissionError):
                    atomic_json(destination, {"status": "new"})
            self.assertEqual(replace.call_count, 10)
            self.assertEqual(json.loads(destination.read_text()), {"status": "old"})
            self.assertEqual(list(Path(directory).glob("*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
