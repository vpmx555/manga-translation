"""Offline review preserves source artifacts and produces reproducible output."""
import json
import unittest

import test_chapter_story_graph as fixtures
from manga_pipeline.review.story_graph import build_review
from manga_pipeline.storage.io import file_hash
from manga_pipeline.v6.graph import build


class GraphReviewTests(unittest.TestCase):
    def setUp(self):
        fixture = fixtures.StoryTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.fixture = fixture

    def test_rebuild_with_images_is_identical_and_does_not_call_provider(self):
        store = self.fixture.fixture()
        provider = fixtures.Provider()
        store.finish('graph', build(store, provider))
        artifact = store.artifact_path('graph')
        original = file_hash(artifact)
        calls = len(provider.requests)
        output = self.fixture.root / 'review'
        first = build_review(artifact, output, source_folder=self.fixture.root)
        hashes = {p.relative_to(output).as_posix(): file_hash(p)
                  for p in output.rglob('*') if p.is_file()}
        second = build_review(artifact, output, source_folder=self.fixture.root)
        self.assertEqual(first, second)
        self.assertEqual(hashes, {p.relative_to(output).as_posix(): file_hash(p)
                                 for p in output.rglob('*') if p.is_file()})
        self.assertEqual(file_hash(artifact), original)
        self.assertEqual(len(provider.requests), calls)
        self.assertEqual(first['model_requests'], 0)
        self.assertEqual(first['stats']['utterances'], 8)
        self.assertEqual(first['warnings'], [])
        self.assertTrue(list((output / 'evidence/images').glob('*.jpg')))

    def test_embedded_data_cannot_close_script_tag(self):
        from manga_pipeline.storage.io import atomic_json
        artifact = self.fixture.root / 'result.json'
        title = '</script><script>alert(1)</script>'
        atomic_json(artifact, {'story_graph': {'chapter': {'title': title}, 'scenes': []}})
        output = self.fixture.root / 'review'
        build_review(artifact, output)
        html = (output / 'index.html').read_text(encoding='utf-8')
        self.assertNotIn(title, html)
        data = json.loads((output / 'review-data.json').read_text(encoding='utf-8'))
        self.assertEqual(data['chapter']['title'], title)

    def test_analyze_review_preserves_semantics_and_copies_verified_evidence(self):
        store, provider, analyzed = self.fixture.analysis_fixture()
        artifact = store.artifact_path('analyze')
        original = file_hash(artifact)
        calls = len(provider.requests)
        output = self.fixture.root / 'analyze-review'
        report = build_review(artifact, output)
        data = json.loads((output/'review-data.json').read_text(encoding='utf-8'))
        uid = analyzed['utterances'][0]['utterance_id']
        self.assertEqual(data['stage'], 'analyze')
        self.assertEqual(data['utterances'][uid]['semantic']['speaker_person_id'], analyzed['utterances'][0]['speaker_person_id'])
        packet = next(iter(data['pictures'].values()))[0]
        self.assertTrue((output/packet['file']).is_file())
        self.assertEqual(file_hash(output/packet['file']), packet['retained_sha256'])
        self.assertEqual(file_hash(artifact), original)
        self.assertEqual(len(provider.requests), calls)
        self.assertEqual(report['model_requests'], 0)

    def test_review_rejects_modified_retained_evidence(self):
        store, _, analyzed = self.fixture.analysis_fixture()
        packet = analyzed['scenes'][0]['events'][0]['image_packets'][0]
        (store.directory/packet['file']).write_bytes(b'changed after analyze')
        with self.assertRaisesRegex(ValueError, 'Retained event image checksum mismatch'):
            build_review(store.artifact_path('analyze'), self.fixture.root/'invalid-review')


if __name__ == '__main__':
    unittest.main()
