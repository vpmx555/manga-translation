"""Read-only review rendering and authenticated reuse of empty identity checkpoints."""
# Historical contract test; requires the matching frozen source.
import copy
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'deployment/runpod'))
import identity_review
import preanalyze_import as importer
import test_identity_name_binding as fixtures
from manga_pipeline.pipeline import Pipeline
from test_identity_name_binding import load_config
from manga_pipeline.storage.io import atomic_json, file_hash, fingerprint, read_json
from manga_pipeline.storage.runs import RunStore
from manga_pipeline.review.v6 import V6Review
from manga_pipeline.v6 import identity_binding


class ReviewTests(unittest.TestCase):
    def setUp(self):
        fixtures.NameBindingTests.setUp(self)

    def real(self):
        run = RunStore.create(self.root / 'real-review',
            source={'story': 'story', 'chapter': 'chapter-1', 'images': []},
            config=load_config(), bank_path=self.store.manifest['bank_path'])
        for stage, value in [('layout', {}), ('extract', self.raw), ('bubble_geometry', {}),
                ('normalize', self.store.values['normalize']), ('scan', self.store.values['scan']),
                ('analyze', self.analyzed)]:
            run.finish(stage, value)
        return run

    def test_dedicated_html_markdown_and_machine_json_do_not_change_source(self):
        real = self.real()
        self.assertTrue(Pipeline(real, provider=fixtures.BindingProvider()).run(until='relationship'))
        before = file_hash(real.path)
        output = self.root / 'review-only'
        result = identity_review.generate_identity_reviews(V6Review(real.directory), output)
        self.assertEqual(file_hash(real.path), before)
        data = read_json(output / 'report.json')
        self.assertEqual(len(data['claims']), 3)
        self.assertEqual(len(data['nodes']), 2)
        self.assertEqual(len(data['edges']), 2)
        self.assertEqual({(e['from_id'], e['to_id']) for e in data['edges']}, {('a', 'b'), ('b', 'a')})
        self.assertTrue(all(n['label'].startswith('MAGI ') for n in data['nodes']))
        self.assertTrue(all(c['images'] for c in data['claims']))
        for name in ('index.html', 'build_identity.html', 'relationship.html', 'review.md', 'build_identity.md', 'relationship.md'):
            self.assertTrue((output / name).is_file())
        html = (output / 'relationship.html').read_text(encoding='utf-8')
        self.assertNotIn('__DATA__', html)
        self.assertNotIn('__VIEW__', html)
        self.assertIn('marker-end=', html)
        self.assertEqual(result['directed_edges'], 2)

    def test_pending_binding_preview_is_not_presented_as_a_committed_graph(self):
        real = self.real()
        real.begin('build_identity')
        identity_binding.build(real, fixtures.BindingProvider(), real.read('analyze'))
        output = self.root / 'preview'
        identity_review.generate_identity_reviews(V6Review(real.directory), output)
        data = read_json(output / 'report.json')
        self.assertTrue(data['binding_preview'])
        self.assertEqual(data['stages']['build_identity'], 'running')
        self.assertEqual(data['edges'], [])
        self.assertEqual(data['nodes'], [])
        self.assertEqual(len(data['claims']), 3)


class AnalyzeImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.runtime = self.root / 'runtime'
        self.banks = self.runtime / 'data/banks'
        atomic_json(self.banks / 'story/metadata.json', {})
        self.registry = self.banks / '_persons_v6/story/registry.json'
        self.before = {'schema_version': 6, 'persons': {}, 'name_index': {}, 'operations': []}
        self.after = {**self.before, 'operations': ['empty-source-op']}
        atomic_json(self.registry, self.after)
        atomic_json(self.registry.parent / 'journal/empty-source-op.json', {
            'before': self.before, 'before_hash': fingerprint(self.before), 'after_hash': fingerprint(self.after)})
        self.source, self.selected = self.root / 'source', self.root / 'selected'
        self.identity = {'operation_id': 'empty-source-op', 'person_state': self.after, 'profiles': {}, 'proposals': []}
        self.identity_file = self.source / '07_build_identity/result.json'
        atomic_json(self.identity_file, self.identity)
        self.manifest = {'pipeline_version': 6, 'source': {'story': 'story'}, 'steps': {
            'build_identity': {'status': 'completed', 'artifact_hash': file_hash(self.identity_file)}}}
        atomic_json(self.source / 'manifest.json', self.manifest)
        atomic_json(self.source / '06_analyze/result.json', {'person_state': {'base_registry_hash': fingerprint(self.before)}})

    def target(self):
        return SimpleNamespace(directory=self.root / 'target', manifest={
            'source': {'story': 'story'}, 'bank_path': str(self.banks / 'story/metadata.json')})

    def test_reuses_analysis_only_when_semantic_inference_contract_matches(self):
        previous, new = load_config(), load_config()
        previous['v6'].pop('identity_name_binding_policy')
        self.assertEqual(importer.analysis_contract(previous), importer.analysis_contract(new))
        new['llm']['temperature'] = .5
        self.assertNotEqual(importer.analysis_contract(previous), importer.analysis_contract(new))
        self.assertEqual(importer.selected_stages('analyze')[-1], 'analyze')
        self.assertEqual(len(importer.selected_stages('analyze')), 6)

    def test_verified_journal_restores_only_task_local_preidentity_registry(self):
        source_hash = file_hash(self.identity_file)
        importer.prepare_identity_base(self.source, self.banks, self.selected)
        with patch.object(importer.manage, 'RUNTIME', self.runtime):
            importer.restore_identity_base(self.selected, self.target())
            importer.restore_identity_base(self.selected, self.target())  # idempotent interrupted import
        self.assertEqual(read_json(self.registry), self.before)
        self.assertEqual(file_hash(self.identity_file), source_hash)

    def test_newer_registry_or_bad_before_hash_cannot_be_overwritten(self):
        importer.prepare_identity_base(self.source, self.banks, self.selected)
        atomic_json(self.registry, {**self.after, 'newer': True})
        with patch.object(importer.manage, 'RUNTIME', self.runtime):
            with self.assertRaisesRegex(ValueError, 'changed after verified restore'):
                importer.restore_identity_base(self.selected, self.target())
        self.assertTrue(read_json(self.registry)['newer'])

    def test_nonempty_named_identity_publication_is_not_rewound(self):
        self.identity['person_state'] = {'persons': {'p': {'primary_name': 'Toru'}}}
        atomic_json(self.identity_file, self.identity)
        self.manifest['steps']['build_identity']['artifact_hash'] = file_hash(self.identity_file)
        atomic_json(self.source / 'manifest.json', self.manifest)
        with self.assertRaisesRegex(ValueError, 'cannot rewind named identity'):
            importer.prepare_identity_base(self.source, self.banks, self.selected)


if __name__ == '__main__':
    unittest.main()
