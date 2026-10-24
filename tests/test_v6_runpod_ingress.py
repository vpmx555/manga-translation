"""Small input integrity fixtures; no pipeline/model execution."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'deployment/runpod'))
import local_v6


class IngressTests(unittest.TestCase):
    def fixture(self, root):
        content = b'image-fixture'
        entry = {'input': 'story-chapter-v6', 'images': {'1.png': {
            'bytes': len(content), 'sha256': hashlib.sha256(content).hexdigest()}}}
        incoming = root / 'incoming'
        incoming.mkdir()
        archive = incoming / '001-input.zip'
        with zipfile.ZipFile(archive, 'w') as zipped:
            zipped.writestr(entry['input'] + '/1.png', content)
            zipped.writestr(entry['input'] + '/.input-manifest.json', '{}')
        entry['archive'] = {'name': archive.name, 'bytes': archive.stat().st_size,
                            'sha256': local_v6.manage.file_hash(archive)}
        return {'input_ingress': str(incoming)}, entry, archive

    def test_verified_atomic_publish_and_repeat_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan, entry, _ = self.fixture(root)
            with patch.object(local_v6.manage, 'RUNTIME', root / 'runtime'):
                result = local_v6.ingest_chapter(plan, entry, root / 'jobs')
                self.assertEqual((result / '1.png').read_bytes(), b'image-fixture')
                self.assertFalse(result.with_name(result.name + '.pending').exists())
                self.assertEqual(result, local_v6.ingest_chapter(plan, entry, root / 'jobs'))
                (result / '1.png').write_bytes(b'corrupt')
                with self.assertRaisesRegex(RuntimeError, 'checksum'):
                    local_v6.ingest_chapter(plan, entry, root / 'jobs')

    def test_existing_empty_input_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan, entry, _ = self.fixture(root)
            with patch.object(local_v6.manage, 'RUNTIME', root / 'runtime'):
                (root / 'runtime/data/input' / entry['input']).mkdir(parents=True)
                with self.assertRaisesRegex(RuntimeError, 'page count'):
                    local_v6.ingest_chapter(plan, entry, root / 'jobs')

    def test_corrupt_archive_cannot_publish_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan, entry, archive = self.fixture(root)
            archive.write_bytes(b'corrupt')
            with patch.object(local_v6.manage, 'RUNTIME', root / 'runtime'):
                with self.assertRaisesRegex(RuntimeError, 'checksum'):
                    local_v6.ingest_chapter(plan, entry, root / 'jobs')
                self.assertFalse((root / 'runtime/data/input' / entry['input']).exists())


if __name__ == '__main__':
    unittest.main()
