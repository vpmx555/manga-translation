"""Deployment checks for isolation, transaction validation and process ownership."""
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

PROJECT = Path(__file__).resolve().parents[1]


def module(name, filename):
    spec = importlib.util.spec_from_file_location(name, PROJECT / "deployment/runpod" / filename)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


manager = module("runpod_manager_tests", "manage.py")
sync = module("runpod_sync_tests", "sync.py")


class RuntimeTests(unittest.TestCase):
    def make_valid_update(self, root):
        source = root / "source"
        for name in ["run.py", "src/manga_pipeline/cli/main.py", "deployment/runpod/manage.py"]:
            path = source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("version = 'new'\n", encoding="utf-8")
        archive = root / "update.zip"
        sync.bundle(archive, source)
        return archive

    @unittest.skipUnless(sys.platform == "linux", "Real flock and data symlink integration runs on the Pod")
    def test_atomic_update_preserves_data_and_keeps_previous_code(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = root / "project"
            project.mkdir()
            (project / "old.py").write_text("old = True\n", encoding="utf-8")
            runtime = root / "runtime"
            bank = runtime / "data/banks/story/metadata.json"
            bank.parent.mkdir(parents=True)
            bank.write_text('{"keep":true}', encoding="utf-8")
            archive = self.make_valid_update(root)
            with patch.object(manager, "PROJECT", project), patch.object(manager, "RUNTIME", runtime):
                manager.install_update(archive)
            self.assertEqual(bank.read_text(encoding="utf-8"), '{"keep":true}')
            self.assertEqual((project / "banks").resolve(), runtime / "data/banks")
            self.assertEqual(len(list((runtime / "code-backups").glob("*/old.py"))), 1)
            self.assertFalse((project / "old.py").exists())
            self.assertTrue((project / "run.py").exists())

    @unittest.skipUnless(sys.platform == "linux", "Real flock integration runs on the Pod")
    def test_running_pipeline_blocks_code_replacement(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = root / "project"
            project.mkdir()
            marker = project / "old.py"
            marker.write_text("old = True\n", encoding="utf-8")
            archive = self.make_valid_update(root)
            with patch.object(manager, "PROJECT", project), patch.object(manager, "RUNTIME", root / "runtime"):
                with manager.lock("pipeline"):
                    with self.assertRaisesRegex(RuntimeError, "pipeline is busy"):
                        manager.install_update(archive)
            self.assertTrue(marker.exists())

    def test_identical_code_sync_skips_upload_and_preserves_backups(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "src").mkdir()
            (root / "src/app.py").write_text("value = 1\n", encoding="utf-8")
            expected = sync.bundle(root / "expected.zip", root)["version"]
            with patch.object(sync, "PROJECT", root), \
                 patch.object(sync, "remote", side_effect=["1", expected]), \
                 patch.object(sync, "upload") as upload:
                sync.sync({"host": "unused"})
            upload.assert_not_called()

    def test_duplicate_input_does_not_upload_or_replace_images(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            image = root / "page.png"
            image.write_bytes(b"image fixture")
            hashes = {"page.png": hashlib.sha256(image.read_bytes()).hexdigest()}
            with patch.object(sync, "remote", return_value=json.dumps({"exists": True, "hashes": hashes})), \
                 patch.object(sync, "upload") as upload:
                destination = sync.push_input({}, root, "chapter-001")
            self.assertTrue(destination.endswith("/chapter-001"))
            upload.assert_not_called()

    def test_changed_input_cannot_overwrite_an_existing_checkpoint_source(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "page.png").write_bytes(b"new content")
            with patch.object(sync, "remote", return_value=json.dumps({"exists": True, "hashes": {"page.png": "old"}})), \
                 patch.object(sync, "upload") as upload:
                with self.assertRaisesRegex(RuntimeError, "different images"):
                    sync.push_input({}, root, "chapter-001")
            upload.assert_not_called()

    def test_resume_options_are_forwarded_in_their_original_order(self):
        with patch.object(sync, "load_connection", return_value={"host": "example"}), \
             patch.object(sync, "sync"), \
             patch.object(sync, "remote", return_value=0) as remote:
            result = sync.main(["resume", "--run-dir", "/saved run", "--until", "analyze"])
        self.assertEqual(result, 0)
        self.assertEqual(remote.call_args.args[1], ["python3", sync.REMOTE_MANAGE, "launch", "resume", "--run-dir", "/saved run", "--until", "analyze"])

    def test_dependency_signature_ignores_code_and_normalizes_line_endings(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            requirements = root / "requirements.in"
            requirements.write_bytes(b"torch==2.8.0+cu128\r\n")
            first = manager.dependency_signature(requirements)
            requirements.write_bytes(b"torch==2.8.0+cu128\n")
            (root / "code.py").write_text("changed = True\n", encoding="utf-8")
            self.assertEqual(first, manager.dependency_signature(requirements))
            requirements.write_text("torch==2.9.0\n", encoding="utf-8")
            self.assertNotEqual(first, manager.dependency_signature(requirements))

    def test_bundle_excludes_credentials_caches_and_generated_artifacts(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name in ["src/app.py", "src/__pycache__/leak.py", "configs/pipeline.json",
                         "configs/.env", ".runpod/connection.json", "outputs/page.json"]:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("{}\n" if path.suffix == ".json" else "value = 1\n", encoding="utf-8")
            archive = root / "bundle.zip"
            result = sync.bundle(archive, root)
            self.assertEqual(set(result["files"]), {"src/app.py", "configs/pipeline.json"})
            with zipfile.ZipFile(archive) as bundle:
                for name, digest in result["files"].items():
                    self.assertEqual(hashlib.sha256(bundle.read(name)).hexdigest(), digest)

    def test_bundle_refuses_invalid_python_before_upload(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "src").mkdir()
            (root / "src/broken.py").write_text("def broken(:\n", encoding="utf-8")
            with self.assertRaises(SyntaxError):
                sync.bundle(root / "bundle.zip", root)

    def test_new_requirements_cannot_silently_use_previous_environment(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(manager, "RUNTIME", Path(temp)):
                with self.assertRaisesRegex(RuntimeError, "not ready"):
                    manager.current_python()

    def test_pid_reuse_or_unmanaged_binary_cannot_be_stopped(self):
        record = {"pid": 100, "start_ticks": "1", "executable": str(manager.ollama_binary())}
        with patch.object(manager, "service_identity", return_value={**record, "start_ticks": "2"}):
            self.assertFalse(manager.owns_service(record))
        with patch.object(manager, "service_identity", return_value={**record, "executable": "/usr/bin/other"}):
            self.assertFalse(manager.owns_service(record))
        self.assertFalse(manager.owns_service(None))

    def test_bad_update_does_not_touch_previous_code_or_escape_staging(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = root / "project"
            project.mkdir()
            marker = project / "keep.txt"
            marker.write_text("old code", encoding="utf-8")
            runtime = root / "runtime"
            archive = root / "update.zip"
            data = b"attempt = True\n"
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr("../escape.py", data)
                bundle.writestr("__bundle__.json", json.dumps({"files": {"../escape.py": hashlib.sha256(data).hexdigest()}}))
            with patch.object(manager, "PROJECT", project), patch.object(manager, "RUNTIME", runtime):
                with self.assertRaisesRegex(RuntimeError, "Unsafe"):
                    manager.install_update(archive)
            self.assertEqual(marker.read_text(encoding="utf-8"), "old code")
            self.assertFalse((runtime / "code-staging/escape.py").exists())

    def test_connection_rejects_option_injection(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            key = root / "key"
            key.write_text("unused", encoding="utf-8")
            config = root / "connection.json"
            config.write_text(json.dumps({"host": "-oProxyCommand=bad", "port": 22, "key": str(key)}), encoding="utf-8")
            with patch.object(sync, "CONNECTION", config):
                with self.assertRaisesRegex(RuntimeError, "Invalid SSH host"):
                    sync.load_connection()


if __name__ == "__main__":
    unittest.main()
