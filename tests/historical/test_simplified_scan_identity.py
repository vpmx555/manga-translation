"""Model-free behavior checks for canonical OCR and structural validators."""
# Historical contract test; requires the matching frozen source.
import copy
import tempfile
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from manga_pipeline.pipeline import Pipeline, load_config
from manga_pipeline.storage.io import atomic_json, read_json
from manga_pipeline.storage.runs import RunStore
from manga_pipeline.v6 import scan_v3 as scan, identity_binding_simple as binding, persons
from manga_pipeline.v6 import structural_validators as structural
from manga_pipeline.v6.name_normalization import apply
from manga_pipeline.v6.scene_wire import mapping
import test_identity_name_binding as fixtures
import test_v6_independent_batches as batches


class ScanTests(unittest.TestCase):
    def parse(self, texts, names, groups=None, scenes=None):
        self.document = {'utterances': [{'id': 'row:'+str(i), 'text': text, 'is_essential_text': True}
                                        for i, text in enumerate(texts)]}
        rows = self.document['utterances']
        return scan.parse({'names': names, 'groups': groups or [], 'scenes': scenes or []},
                          self.document, rows, mapping(rows, []), {})

    def name(self, old, new, uid='u0', group='g'):
        return {'text_id': uid, 'old_name': old, 'new_name': new, 'group_id': group}

    def test_case_spacing_hyphen_unicode_and_all_chapter_occurrences(self):
        result = self.parse(['SASA- MORI, sasa mori!', 'Sasa—mori-chan'],
            [self.name('sasa-mori', 'Sasamori')], [{'group_id': 'g', 'canonical_name': 'Sasamori Koyuki'}])
        current = apply(self.document, result['name_normalization'])
        self.assertEqual([r['text'] for r in current['utterances']], ['Sasamori, Sasamori!', 'Sasamori-chan'])
        self.assertEqual(sum(p['count'] for p in result['name_normalization']['patches']), 3)
        self.assertEqual(len(result['candidates']), 2)
        self.assertEqual(scan.find_all('STRASSE Straße', 'straße'), [(0, 7), (8, 14)])
        self.assertEqual(scan.find_all('Ke\u0301i', 'Kéi'), [(0, 4)])

    def test_cascades_once_in_model_order_and_remaps_candidate(self):
        result = self.parse(['A B'], [self.name('A', 'B'), self.name('B', 'C')])
        self.assertEqual(result['name_normalization']['canonical_rows'][0]['text'], 'C C')
        self.assertEqual([(c['start'], c['end'], c['name']) for c in result['candidates']], [(0, 1, 'C'), (2, 3, 'C')])
        self.assertEqual([p['rule_index'] for p in result['name_normalization']['patches']], [0, 1])

    def test_correction_not_substring_and_bad_items_are_review_only(self):
        result = self.parse(['Sasa- mori'], [None, self.name('Sasa mori', 'Sasamori'),
            self.name('Mizushlma', 'Mizushima', uid='invalid'), self.name('absent', 'Toru'),
            self.name('bad', 'bad', uid=[]), self.name('bad', 'bad', group=[])], scenes=[{'start_id': []}])
        self.assertEqual(result['name_normalization']['canonical_rows'][0]['text'], 'Sasamori')
        self.assertEqual(result['candidates'][0]['name'], 'Sasamori')
        self.assertTrue(all(not c['alias_groups'] for c in result['candidates']))
        self.assertIn('no_match', {w['reason'] for w in result['needs_review']})
        self.assertEqual(result['scenes'][0]['target_ids'], ['row:0'])

    def test_canonical_apply_idempotent_and_integrity_guarded(self):
        result = self.parse(['Sasa-mori'], [self.name('Sasa-mori', 'Sasamori')])
        norm = result['name_normalization']
        current = apply(self.document, norm)
        self.assertEqual(current, apply(current, norm))
        self.assertEqual(current['utterances'][0]['ocr_raw_text'], 'Sasa-mori')
        corrupt = copy.deepcopy(norm)
        corrupt['canonical_rows'][0]['text'] = 'changed'
        with self.assertRaises(ValueError):
            apply(self.document, corrupt)

    def test_duplicate_group_and_assignment_use_last_object(self):
        result = self.parse(['Toru'], [self.name('Toru', 'Toru', group='old'),
            self.name('Toru', 'Toru', group='new')], [
                {'group_id': 'old', 'canonical_name': 'Old'},
                {'group_id': 'new', 'canonical_name': 'Wrong'},
                {'group_id': 'new', 'canonical_name': 'Mizushima Toru'}])
        self.assertEqual(len(result['candidates']), 1)
        self.assertEqual(result['candidates'][0]['alias_groups'][0]['canonical_name'], 'Mizushima Toru')


class StructuralTests(unittest.TestCase):
    def test_new_defaults_and_historical_unversioned_file(self):
        config = load_config()
        self.assertEqual(config['v6']['scan_policy'], scan.POLICY)
        self.assertEqual(config['v6']['identity_name_binding_policy'], 'analyze-authoritative-v3')
        self.assertEqual(config['v6']['identity_min_chapter_crops'], 8)
        with tempfile.TemporaryDirectory() as directory:
            historical = copy.deepcopy(config)
            for key in ('scan_policy', 'identity_name_binding_policy'):
                historical['v6'].pop(key)
            path = Path(directory) / 'old.json'
            atomic_json(path, historical)
            restored = load_config(path)
            self.assertIsNone(restored['v6']['scan_policy'])
            self.assertEqual(restored['v6']['identity_name_binding_policy'], 'chapter-multimodal-name-binding-v1')

    def request(self):
        return {'candidates': {'1': {'chapter_crop_count': 8, 'existing_named_identity': False},
                               '2': {'chapter_crop_count': 7, 'existing_named_identity': False}},
                'minimum_chapter_crops': 8, 'occurrences': [{'id': 'n', 'context': [{'id': 'u'}]}], 'crops': []}

    def test_name_assignment_needs_no_citation_or_mention_type(self):
        result = binding.normalize({'characters': [
            {'magi_id': '1', 'primary_name': 'Mizushima Toru', 'aliases': [None, 'Toru']},
            {'magi_id': '2', 'primary_name': 'Koyuki'}, {'magi_id': 'bad', 'primary_name': 'Bad'}]}, self.request())
        self.assertEqual([d['owner_magi_id'] for d in result['decisions']], ['1'])
        self.assertEqual(result['decisions'][0]['evidence_refs'], [])
        self.assertEqual(result['decisions'][0]['supplied_evidence_refs'], ['u'])
        self.assertEqual(result['decisions'][0]['identity_aliases'], ['Toru'])
        self.assertEqual({r['reason'] for r in result['reviews']},
                         {'below_crop_threshold', 'unknown_magi_id', 'invalid_alias_fields'})

    def test_existing_identity_can_have_less_than_eight_crops(self):
        request = self.request()
        request['candidates']['2']['existing_named_identity'] = True
        result = binding.normalize({'characters': [{'magi_id': '2', 'primary_name': 'Koyuki'}]}, request)
        self.assertEqual(len(result['decisions']), 1)

    def test_new_scan_cannot_reuse_old_scan_checkpoint(self):
        with patch.object(sys, 'path', [str(Path(__file__).resolve().parents[1] / 'deployment/runpod'), *sys.path]):
            from preanalyze_import import scan_contract, binding_contract
        config = load_config()
        previous = copy.deepcopy(config)
        previous['v6']['scan_policy'] = None
        self.assertNotEqual(scan_contract(previous), scan_contract(config))
        previous['v6']['scan_policy'] = scan.POLICY
        previous['v6']['identity_min_chapter_crops'] = 7
        self.assertNotEqual(binding_contract(previous), binding_contract(config))

    def test_last_full_character_object_wins(self):
        result = binding.normalize({'characters': [
            {'magi_id': '1', 'primary_name': 'Wrong', 'aliases': ['old']},
            {'magi_id': '1', 'primary_name': 'Toru', 'aliases': ['new']}]}, self.request())
        self.assertEqual(len(result['decisions']), 1)
        self.assertEqual(result['decisions'][0]['identity_name'], 'Toru')
        self.assertEqual(result['decisions'][0]['identity_aliases'], ['new'])

    def test_count_uses_real_chapter_detections_not_gallery_or_history(self):
        page = {'id': 'p1', 'character_ids': [1, 1, 2, 1],
                'characters': [[0, 0, 1, 1], [2, 0, 3, 1], [0, 0, 1, 1], [0, 0, 1, 1]]}
        raw = {'pages': [page, {**page, 'id': 'p2'}], 'appearance_count': 1000}
        self.assertEqual(binding.chapter_counts(raw), {'1': 4, '2': 2})

    def test_profile_partial_field_error_does_not_erase_other_values(self):
        request = {'identity': {'person_id': 'p'}, 'evidence_segments': [{'rows': [{'id': 'u'}]}]}
        result = structural.profile({'person_id': 'p', 'attributes': {
            'appearance': {'value': 'Tóc đen', 'status': 'confirmed', 'basis': 'visual'},
            'personality': None}}, request)
        self.assertEqual(result['attributes']['appearance']['value'], 'Tóc đen')
        self.assertEqual(result['attributes']['appearance']['supplied_evidence_refs'], ['u'])
        self.assertEqual(result['attributes']['personality']['value'], 'unknown')
        self.assertTrue(result['reviews'])
        with self.assertRaises(ValueError):
            structural.profile({'person_id': 'other', 'attributes': {}}, request)

    def test_directed_edges_independent_free_text_and_missing_reverse_unknown(self):
        request = {'identities': {'a': {}, 'b': {}}}
        result = structural.relationship({'relations': [], 'address_edges': [
            {'from_id': 'a', 'to_id': 'b', 'self': 'tớ', 'address': 'Sasamori-chan',
             'status': 'confirmed', 'basis': 'context', 'reason': 'Model reasoning'},
            {'from_id': 'missing', 'to_id': 'a'}]}, request)
        self.assertEqual(result['address_edges'][0]['address'], 'Sasamori-chan')
        self.assertEqual(result['address_edges'][1]['self'], 'unknown')
        self.assertEqual(result['address_edges'][1]['from_id'], 'b')
        structural.validate_relationship(result, request)


class ScanStageIntegrationTests(unittest.TestCase):
    def test_canonical_text_reaches_analyze_and_verification(self):
        from manga_pipeline.v6 import semantic
        from manga_pipeline.v6.independent import analyze
        from manga_pipeline.v6.verification import verify
        class Provider(batches.Provider):
            def infer_reserved(inner, task, prompt, data, schema, validator, *, reserve):
                if task == 'analyze_scene_v6':
                    inner.calls.append((task, copy.deepcopy(data), reserve))
                    return {'scenes': [], 'groups': [{'group_id': 'g', 'canonical_name': 'Sasamori Koyuki'}],
                            'names': [{'text_id': 'u0', 'old_name': 'Sasa-mori', 'new_name': 'Sasamori', 'group_id': 'g'}]}
                result = super().infer_reserved(task, prompt, data, schema, validator, reserve=reserve)
                for mention in data['mentions']:
                    result['utterances'][mention['utterance_id']]['mentions'][mention['id']] = {
                        'confirmed_person_name': False, 'mention_type': 'narrator_introduction',
                        'target_person_id': None, 'recipient_cluster_ref': None, 'visual_support': 'unknown',
                        'components': [], 'evidence': 'Name, owner uncertain'}
                return result
        with tempfile.TemporaryDirectory() as directory:
            store, raw, document, _, frozen = batches.fixture(Path(directory), 2)
            store.manifest['config']['v6'] = load_config()['v6']
            document['utterances'][0]['text'] = 'Hello Sasa-mori.'
            provider = Provider()
            scanned = semantic.scan(store, document, raw, None, provider=provider)
            output = analyze(store, raw, document, scanned, provider)
            plan = read_json(store.artifact_path('analyze').parent / 'plan.json')
            verify({'normalize': document, 'analyze': output, 'extract': raw, 'scan': scanned},
                   store.manifest['config'], frozen, read_json(store.manifest['bank_path']), plan)
            sent = [c[1] for c in provider.calls if c[0] != 'analyze_scene_v6']
            self.assertEqual(sent[0]['targets'][0]['text'], 'Hello Sasamori.')
            self.assertNotIn('ocr_raw_text', sent[0]['targets'][0])
            self.assertNotIn('raw_surface', sent[0]['mentions'][0])
            row = output['utterances'][0]
            self.assertEqual(row['mentions'][0]['mention_type'], 'unknown')
            self.assertTrue(row['semantic_needs_review'])
            self.assertEqual(scanned['automatic_retries'], 0)

    def test_truncated_scan_is_not_retried_on_reentry(self):
        from manga_pipeline.runtime.llm_budget import OutputTruncated
        from manga_pipeline.v6 import semantic
        with tempfile.TemporaryDirectory() as directory:
            store, raw, document, _, _ = batches.fixture(Path(directory), 2)
            store.manifest['config']['v6'] = load_config()['v6']
            provider = batches.Provider()
            provider.failures = [OutputTruncated('length')]
            for _ in range(2):
                with self.assertRaises((ValueError, OutputTruncated)):
                    semantic.scan(store, document, raw, None, provider=provider)
            self.assertEqual(len(provider.calls), 1)


class NewBindingIntegrationTests(unittest.TestCase):
    def setUp(self):
        fixtures.NameBindingTests.setUp(self)
        self.store.manifest['config'] = load_config()
        self.store.manifest['config']['v6']['identity_name_binding_policy'] = binding.POLICY
        # Eight distinct real detections per main MAGI in this chapter.
        base = self.raw['pages'][0]
        self.raw['pages'] = [{**copy.deepcopy(base), 'id': 'p'+str(i+1), 'page': i+1} for i in range(8)]
        self.store.values['extract'] = self.raw
        wire = mapping(self.rows, [])
        value = {'groups': [{'group_id': 't', 'canonical_name': 'Mizushima Toru'},
                             {'group_id': 'k', 'canonical_name': 'Sasamori Koyuki'}],
                 'names': [{'text_id': 'u0', 'old_name': 'Toru', 'new_name': 'Toru', 'group_id': 't'},
                           {'text_id': 'u1', 'old_name': 'Koyuki', 'new_name': 'Koyuki', 'group_id': 'k'}], 'scenes': []}
        self.scanned = scan.parse(value, self.store.values['normalize'], [], wire, self.s)
        self.store.values['scan'] = self.scanned

    def provider(self, malformed=False):
        class Provider(fixtures.BindingProvider):
            def infer(inner, task, prompt, data, schema, validator, images=None, **kwargs):
                if task == binding.TASK:
                    inner.calls.append((task, copy.deepcopy(data), len(images or [])))
                    if malformed:
                        return {'characters': 'invalid'}
                    mid = '1' if 'toru' in data['canonical_name'].casefold() else '2'
                    return {'characters': [{'magi_id': mid, 'primary_name': data['canonical_name'],
                                            'aliases': [], 'reason': 'Name assigned from supplied chapter'}]}
                return super().infer(task, prompt, data, schema, validator, images, **kwargs)
        return Provider()

    def test_new_identity_gate_and_replay_preserve_bank(self):
        provider = self.provider()
        before = persons.bank_snapshot(self.store.manifest['bank_path'])
        state, result = binding.build(self.store, provider, self.analyzed)
        self.assertEqual(result['summary']['new_named_identities'], 2)
        self.assertEqual(state['persons']['a']['primary_name'], 'Mizushima Toru')
        self.assertEqual(state['persons']['b']['primary_name'], 'Sasamori Koyuki')
        self.assertEqual(before, persons.bank_snapshot(self.store.manifest['bank_path']))
        calls = len(provider.calls)
        again, replay = binding.build(self.store, provider, self.analyzed)
        self.assertEqual(state, again)
        self.assertEqual(result['snapshot_hash'], replay['snapshot_hash'])
        self.assertEqual(len(provider.calls), calls)

    def test_seven_crops_cannot_create_name_even_if_analyze_proposed_it(self):
        self.raw['pages'] = self.raw['pages'][:7]
        atomic_json(self.store.directory / 'snapshots/persons-v6.json', {'state': self.s})
        self.analyzed = copy.deepcopy(self.analyzed)
        persons.add_name(self.analyzed['person_state'], 'a', 'Toru', source='analyze')
        state, result = binding.build(self.store, self.provider(), self.analyzed)
        self.assertEqual(result['summary']['new_named_identities'], 0)
        self.assertFalse(any(p['primary_name'] for p in state['persons'].values()))
        self.assertTrue(any(i.get('reason') == 'below_crop_threshold' for i in result['needs_review']))

    def test_ungrouped_names_make_no_binding_request(self):
        for candidate in self.scanned['candidates']:
            candidate['alias_groups'] = []
        provider = self.provider()
        state, result = binding.build(self.store, provider, self.analyzed)
        self.assertEqual(result['summary']['requests'], 0)
        self.assertFalse(provider.calls)
        self.assertFalse(any(p['primary_name'] for p in state['persons'].values()))

    def test_no_crop_citation_or_available_gallery_is_required(self):
        for character in self.metadata['characters'].values():
            character['prototype_crop_paths'] = []
        atomic_json(self.store.manifest['bank_path'], self.metadata)
        for page in self.raw['pages']:
            page['image_path'] = str(self.root / 'missing.png')
        state, result = binding.build(self.store, self.provider(), self.analyzed)
        self.assertEqual(result['summary']['new_named_identities'], 2)
        self.assertTrue(result['crop_issues'])

    def test_budget_overflow_splits_before_post_and_indivisible_failure_is_reviewed(self):
        provider = self.provider()
        def budget(task, prompt, request, schema, images=None, *, reserve=None):
            return {'estimate': 1000, 'limit': 1, 'output_reserve': reserve, 'input_tokens': 100}
        provider.request_budget = budget
        state, result = binding.build(self.store, provider, self.analyzed)
        self.assertFalse(provider.calls)
        self.assertEqual(len(result['budget_failures']), 2)
        self.assertTrue(all(not f['post_sent'] for f in result['budget_failures']))

    def test_malformed_response_single_attempt_and_other_stages_continue(self):
        provider = self.provider(malformed=True)
        _, result = binding.build(self.store, provider, self.analyzed)
        self.assertEqual(len(provider.calls), 2)
        self.assertEqual(result['summary']['request_statuses'], {'failed': 2})
        binding.build(self.store, provider, self.analyzed)
        self.assertEqual(len(provider.calls), 2)

    def test_real_stage_dispatch_profiles_graph_and_local_review(self):
        # Direct dialogue already resolved by analyze: identity adds names only.
        for i, row in enumerate(self.analyzed['utterances']):
            row.update(speaker_person_id='a' if i % 2 == 0 else 'b',
                       addressee_person_ids=['b' if i % 2 == 0 else 'a'],
                       addressee_type='individual', content_type='dialogue', speaker_kind='person')
        real = RunStore.create(self.root / 'new-full-run',
            source={'story': 'story', 'chapter': 'chapter-1', 'images': []},
            config=self.store.manifest['config'], bank_path=self.store.manifest['bank_path'])
        for stage, value in [('layout', {}), ('extract', self.raw), ('bubble_geometry', {}),
                ('normalize', self.store.values['normalize']), ('scan', self.scanned), ('analyze', self.analyzed)]:
            real.finish(stage, value)
        self.assertTrue(Pipeline(real, provider=self.provider()).run(until='export'))
        real = RunStore(real.directory)
        linked, graph = real.read('build_identity'), real.read('relationship')
        self.assertEqual(set(linked['profiles']), {'a', 'b'})
        self.assertEqual(len(graph['pairs']), 1)
        self.assertEqual(linked['name_binding']['policy'], binding.POLICY)
        self.assertTrue(real.artifact_path('build_identity').with_name('review.md').exists())
        from manga_pipeline.review.v6 import V6Review
        with patch.object(sys, 'path', [str(Path(__file__).resolve().parents[1] / 'deployment/runpod'), *sys.path]):
            import identity_review
            output = self.root / 'local-review'
            identity_review.generate_identity_reviews(V6Review(real.directory), output)
        payload = read_json(output / 'report.json')
        self.assertTrue(all(c['images'] for c in payload['claims']))
        self.assertTrue(all(c['supplied_evidence_refs'] and not c['model_evidence_refs'] for c in payload['claims']))
        self.assertTrue((output / 'errors.html').exists())

    def test_local_review_shows_below_threshold_items_without_legacy_proof_checks(self):
        self.raw['pages'] = self.raw['pages'][:7]
        real = RunStore.create(self.root / 'below-threshold',
            source={'story': 'story', 'chapter': 'chapter-1', 'images': []},
            config=self.store.manifest['config'], bank_path=self.store.manifest['bank_path'])
        for stage, value in [('layout', {}), ('extract', self.raw), ('bubble_geometry', {}),
                ('normalize', self.store.values['normalize']), ('scan', self.scanned), ('analyze', self.analyzed)]:
            real.finish(stage, value)
        self.assertTrue(Pipeline(real, provider=self.provider()).run(until='relationship'))
        from manga_pipeline.review.v6 import V6Review
        with patch.object(sys, 'path', [str(Path(__file__).resolve().parents[1] / 'deployment/runpod'), *sys.path]):
            import identity_review
            output = self.root / 'below-threshold-review'
            identity_review.generate_identity_reviews(V6Review(real.directory), output)
        errors = read_json(output / 'errors.json')
        self.assertEqual(errors['request_count'], 2)
        self.assertEqual(errors['decision_count'], 2)
        self.assertTrue(all(r['reviews'][0]['reason'] == 'below_crop_threshold' for r in errors['results']))
        self.assertIn('không phải ảnh model', (output / 'errors.html').read_text(encoding='utf8'))


if __name__ == '__main__':
    unittest.main()
