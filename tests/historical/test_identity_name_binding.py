"""Name bootstrap integration from unnamed bank to directed translation."""
# Historical contract test; requires the matching frozen source.
import copy
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

from manga_pipeline.pipeline import Pipeline, load_config as current_config
from manga_pipeline.storage.io import atomic_json, fingerprint, read_json
from manga_pipeline.storage.runs import RunStore
from manga_pipeline.runtime.inference_state import InferenceUncertain
from manga_pipeline.runtime.resources import ResourceExhausted
from manga_pipeline.v6 import identity_binding as binding, identity_v2, semantic, persons
from manga_pipeline.v6.identity_runtime import EvidenceIntegrityError
import test_identity_relationship_v2 as fixtures
from test_identity_relationship_v2 import Provider


def load_config(*args, **kwargs):
    """These fixtures exercise the retained v1 binding contract."""
    config = current_config(*args, **kwargs)
    config['v6'].update(scan_policy=None, identity_name_binding_policy=binding.POLICY, scan_output_tokens=4096)
    config['llm'].update(analyze_thinking=False, analyze_num_ctx=32768, analyze_timeout=600, analyze_num_predict=8192)
    return config


class BindingProvider(Provider):
    def __init__(self, transform=None):
        super().__init__()
        self.transform = transform

    def infer(self, task, prompt, data, schema, validator, images=None, **kwargs):
        if task == binding.TASK:
            self.calls.append((task, copy.deepcopy(data), len(images or [])))
            decisions = []
            for occurrence in data['occurrences']:
                owner = '1' if 'toru' in occurrence['name'] else '2'
                kind = ('narrator_introduction' if occurrence['content_type'] == 'narration' else
                        'self_introduction' if occurrence['mention_type'] == 'introduction' else 'direct_address')
                caller = None if kind == 'narrator_introduction' else '1'
                decisions.append({'occurrence_id': occurrence['id'], 'status': 'confirmed',
                    'owner_magi_id': owner, 'speaker_magi_id': caller, 'evidence_kind': kind,
                    'evidence_refs': [occurrence['utterance_id'], *[c['crop_id'] for c in data['crops']]],
                    'reason': 'Explicit introduction or directed name call, grounded in labelled images'})
            result = {'group_status': 'consistent', 'group_reason': '', 'decisions': decisions}
            return self.transform(result, data) if self.transform else result
        if task == 'translate_vi_group_v6_natural':
            self.calls.append((task, copy.deepcopy(data), len(images or [])))
            return {'translations': [{'id': r['id'], 'translation': 'Chào cậu.', 'needs_review': False,
                'review_reason': '', 'glossary_proposals': []} for r in data['targets']]}
        return super().infer(task, prompt, data, schema, validator, images, **kwargs)


class NameBindingTests(unittest.TestCase):
    def setUp(self):
        # Reuse real crop/vector fixtures without inheriting/rerunning their tests.
        fixtures.IdentityGraphTests.setUp(self)
        self.store.manifest['config'] = load_config()
        self.s['persons']['a'].update(primary_name=None, name_forms=[], components=[])
        self.s['persons']['b'].update(primary_name=None, name_forms=[], components=[])
        self.s['name_index'] = {}
        self.image = self.root / 'page.png'
        image = Image.new('RGB', (180, 100), 'white')
        draw = ImageDraw.Draw(image)
        for i in range(3):
            draw.rectangle((20+i*40, 20, 45+i*40, 90), fill=(30+i*50, 70, 120))
        image.save(self.image)
        self.raw = {'pages': [{'id': 'p1', 'page': 1, 'image_path': str(self.image),
            'width': 180, 'height': 100, 'panels': [[0, 0, 180, 100]],
            'characters': [[20, 20, 45, 90], [60, 20, 85, 90], [100, 20, 125, 90]],
            'character_ids': [1, 2, 3], 'character_cluster_labels': [0, 1, 2]}]}
        texts = ['I am Toru.', 'This is Koyuki.', 'Koyuki, help me.', 'All right.']
        self.rows, self.candidates, analyzed = [], [], []
        for i, text in enumerate(texts):
            uid = 'u'+str(i)
            self.rows.append({'id': uid, 'text': text, 'reading_order': i, 'page_id': 'p1', 'page': 1,
                'panel_index': 0, 'source_boxes': [], 'source_text_ids': [], 'content_type': 'dialogue',
                'speaker_person_id': None, 'addressee_ids': []})
            mentions = []
            if i < 3:
                name = 'Toru' if i == 0 else 'Koyuki'
                start = text.index(name)
                candidate = {'id': 'n'+str(i), 'utterance_id': uid, 'name': name.casefold(),
                    'raw_surface': name, 'start': start, 'end': start+len(name), 'candidate_person_ids': []}
                self.candidates.append(candidate)
                mentions.append({'candidate_id': candidate['id'], 'confirmed_person_name': False,
                    'mention_type': ['introduction', 'reference', 'direct_address'][i],
                    'target_person_id': None, 'recipient_cluster_ref': None, 'components': [],
                    'visual_support': 'unknown', 'evidence': 'No known owner yet'})
            analyzed.append({'utterance_id': uid, 'content_type': 'narration' if i == 1 else 'dialogue',
                'speech_style': 'caption' if i == 1 else 'normal', 'speaker_person_id': 'b' if i == 3 else None,
                'speaker_cluster_ref': 's:b' if i == 3 else None,
                'speaker_kind': 'narrator' if i == 1 else 'person' if i == 3 else 'others',
                'addressee_person_ids': ['a'] if i == 3 else [],
                'addressee_type': 'audience' if i == 1 else 'individual' if i == 3 else 'unknown',
                'mentions': mentions, 'evidence': 'fixture', 'semantic_resolution': 'resolved'})
        self.analyzed = {'policy': 'independent-batches-v6.1', 'person_state': self.s,
            'calling_events': [], 'utterances': analyzed,
            'source_bank_snapshot': persons.bank_snapshot(self.store.manifest['bank_path'])}
        self.store.values.update(analyze=self.analyzed, extract=self.raw, scan={'candidates': self.candidates},
                                 normalize={'utterances': self.rows, 'excluded': []})

    def bind(self, provider=None):
        return binding.build(self.store, provider or BindingProvider(), self.analyzed)

    def test_unnamed_registry_to_profiles_graph_effective_export_and_translation(self):
        self.assertFalse(any(p['primary_name'] for p in self.s['persons'].values()))
        real = RunStore.create(self.root / 'full-run', source={'story': 'story', 'chapter': 'chapter-1', 'images': []},
                               config=load_config(), bank_path=self.store.manifest['bank_path'])
        for stage, value in [('layout', {}), ('extract', self.raw), ('bubble_geometry', {}),
                ('normalize', self.store.values['normalize']), ('scan', self.store.values['scan']),
                ('analyze', self.analyzed)]:
            real.finish(stage, value)
        provider = BindingProvider()
        before = persons.bank_snapshot(self.store.manifest['bank_path'])
        self.assertTrue(Pipeline(real, provider=provider).run(until='translate'))
        real = RunStore(real.directory)
        linked, graph, exported = [real.read(s) for s in ('build_identity', 'relationship', 'export')]
        self.assertEqual(linked['name_binding']['summary']['confirmed_occurrences'], 3)
        self.assertEqual(linked['person_state']['persons']['a']['primary_name'], 'toru')
        self.assertEqual(linked['person_state']['persons']['b']['primary_name'], 'koyuki')
        self.assertEqual(persons.canonical(linked['person_state'], 'c'), 'a')
        self.assertEqual(set(linked['profiles']), {'a', 'b'})
        self.assertEqual(len(graph['pairs']), 1)
        direct = next(r for r in exported['utterances'] if r['id'] == 'u2')
        self.assertEqual(direct['speaker_person_id'], 'a')
        self.assertEqual(direct['addressee_ids'], ['b'])
        self.assertIsNone(direct['analyze_original']['speaker_person_id'])
        self.assertEqual(direct['analyze_original']['addressee_person_ids'], [])
        narrator = next(r for r in exported['utterances'] if r['id'] == 'u1')
        self.assertEqual(narrator['speaker_kind'], 'narrator')
        self.assertEqual(narrator['communication_evidence'], [])
        self.assertEqual(exported['binding_snapshot_hash'], linked['binding_snapshot_hash'])
        self.assertEqual(real.read('analyze'), self.analyzed)
        self.assertFalse(real.read('translate')['unresolved_translation_targets'])
        self.assertEqual(before, persons.bank_snapshot(self.store.manifest['bank_path']))
        calls = len(provider.calls)
        self.assertTrue(Pipeline(RunStore(real.directory), provider=provider).run(until='translate'))
        self.assertEqual(calls, len(provider.calls))

    def test_missing_mention_type_and_people_without_bank_are_skipped(self):
        self.analyzed['utterances'][0]['mentions'][0]['mention_type'] = 'unknown'
        for page in self.raw['pages']:
            page['character_ids'] = [None, None, None]
        for obs in self.s['observations'].values():
            for o in obs:
                o['source_bank_id'] = None
        provider = BindingProvider()
        state, result = self.bind(provider)
        self.assertFalse(provider.calls)
        self.assertEqual(result['summary']['named_identities'], 0)
        self.assertEqual(result['review_status'], 'completed_with_review')
        self.assertIn('missing_or_unknown_mention_type', {s['reason'] for s in result['skipped']})
        self.assertIn('no_candidate_with_magi_bank_id', {s['reason'] for s in result['skipped']})
        self.assertTrue(state['name_groups_v1']['groups'])

    def test_suspect_alias_group_is_held_without_splitting(self):
        for c in self.candidates:
            c['alias_groups'] = [{'group_id': 'bad', 'canonical_name': 'Toru',
                                 'aliases': ['Toru', 'Koyuki'], 'status': 'suspect'}]
        provider = BindingProvider()
        _, result = self.bind(provider)
        self.assertFalse(provider.calls)
        self.assertEqual(len(result['groups']), 1)
        self.assertEqual(result['summary']['suspect_groups'], 1)
        self.assertFalse(result['accepted'])

    def test_model_suspect_in_one_child_blocks_all_successful_claims_in_group(self):
        def suspect(result, data):
            if data['canonical_name'] == 'koyuki':
                result.update(group_status='suspect', group_reason='Two different characters share this form')
            return result
        _, result = self.bind(BindingProvider(suspect))
        self.assertEqual([c['owner_magi_id'] for c in result['accepted']], ['1'])
        self.assertEqual(result['summary']['suspect_groups'], 1)

    def test_off_panel_uses_only_resolved_individual_listener(self):
        # Keep only the direct name call; the page shows the caller alone.
        self.candidates[:] = [self.candidates[2]]
        self.raw['pages'][0]['characters'] = self.raw['pages'][0]['characters'][:1]
        self.raw['pages'][0]['character_ids'] = [1]
        row = self.analyzed['utterances'][2]
        row.update(speaker_person_id='a', speaker_kind='person', addressee_person_ids=['b'], addressee_type='individual')
        _, result = self.bind()
        self.assertEqual(result['accepted'][0]['owner_magi_id'], '2')
        self.assertEqual(result['accepted'][0]['occurrence']['off_panel_magi_id'], '2')

    def test_third_person_reference_cannot_use_listener_fallback(self):
        self.candidates[:] = [self.candidates[2]]
        self.raw['pages'][0]['characters'] = self.raw['pages'][0]['characters'][:1]
        self.raw['pages'][0]['character_ids'] = [1]
        row = self.analyzed['utterances'][2]
        row.update(speaker_person_id='a', speaker_kind='person', addressee_person_ids=['b'], addressee_type='individual')
        row['mentions'][0]['mention_type'] = 'reference'
        _, result = self.bind()
        self.assertFalse(result['accepted'])
        self.assertTrue(result['inference_failures'])

    def test_group_listener_cannot_create_off_panel_owner(self):
        self.candidates[:] = [self.candidates[2]]
        self.raw['pages'][0]['characters'] = self.raw['pages'][0]['characters'][:1]
        self.raw['pages'][0]['character_ids'] = [1]
        self.analyzed['utterances'][2].update(speaker_person_id='a', speaker_kind='person',
            addressee_person_ids=['b', 'c'], addressee_type='group')
        _, result = self.bind()
        self.assertFalse(result['accepted'])

    def test_resolved_analyze_facts_are_not_overwritten(self):
        self.analyzed['utterances'][2].update(speaker_person_id='b', speaker_kind='person',
            addressee_person_ids=['a'], addressee_type='individual')
        state, result = self.bind()
        linked = {'policy': identity_v2.POLICY, 'person_state': state, 'proposals': [],
                  'name_binding': result, 'binding_snapshot_hash': result['snapshot_hash']}
        doc = semantic.export(self.store.values['normalize'], self.analyzed, linked)
        row = next(r for r in doc['utterances'] if r['id'] == 'u2')
        self.assertEqual(row['speaker_person_id'], 'b')
        self.assertEqual(row['addressee_ids'], ['a'])
        self.assertFalse(row['communication_evidence'])
        self.assertTrue(any('resolved_speaker_conflict' in r.get('reason', []) for r in result['needs_review']))

    def test_empty_known_names_and_false_confirmation_do_not_prevent_requests(self):
        state, result = self.bind()
        self.assertEqual(result['summary']['named_identities'], 2)
        self.assertTrue(state['persons']['a']['name_forms'])
        self.assertTrue(all(not m['confirmed_person_name'] for r in self.analyzed['utterances'] for m in r['mentions']))

    def test_error_and_uncertain_request_are_not_replayed(self):
        def fail(result, data):
            raise ResourceExhausted('CUDA out of memory')
        provider = BindingProvider(fail)
        _, first = self.bind(provider)
        count = len(provider.calls)
        _, second = self.bind(provider)
        self.assertEqual(count, len(provider.calls))
        self.assertFalse(second['accepted'])
        self.assertEqual(first, second)

    def test_uncertain_child_is_blocked_but_never_sent_work_can_resume(self):
        self.store.manifest['config']['v6']['identity_binding_concurrency'] = 1
        def disconnect(result, data):
            raise OSError('Connection lost after POST')
        broken = BindingProvider(disconnect)
        _, first = self.bind(broken)
        self.assertEqual(first['summary']['request_statuses'], {'uncertain': 1, 'never_sent': 1})
        healthy = BindingProvider()
        _, second = self.bind(healthy)
        self.assertEqual(len([c for c in healthy.calls if c[0] == binding.TASK]), 1)
        self.assertEqual(second['summary']['request_statuses'].get('uncertain'), 1)
        self.assertTrue(second['accepted'])

    def test_preflight_splits_by_occurrence_and_keeps_partial_success(self):
        self.store.manifest['config']['v6']['identity_binding_output_tokens'] = 768
        def fail_direct_child(result, data):
            if any(o['utterance_id'] == 'u2' for o in data['occurrences']):
                raise ResourceExhausted('OOM of one planned child')
            return result
        provider = BindingProvider(fail_direct_child)
        _, result = self.bind(provider)
        self.assertEqual(result['summary']['requests'], 3)
        calls = [c for c in provider.calls if c[0] == binding.TASK]
        occurrences = [o['id'] for c in calls for o in c[1]['occurrences']]
        self.assertCountEqual(occurrences, ['n0', 'n1', 'n2'])
        self.assertEqual(len(set(occurrences)), 3)
        self.assertEqual(result['summary']['named_identities'], 2)
        self.assertEqual(result['summary']['confirmed_occurrences'], 2)
        summary_path = self.store.artifact_path('build_identity').parent / 'name-binding/request-summary.json'
        summary = read_json(summary_path)
        self.assertEqual(summary['maximum_concurrency'], 8)
        self.assertEqual(summary['automatic_retries'], 0)

    def test_one_oversized_occurrence_never_sends_post(self):
        class NoRoom(BindingProvider):
            def request_budget(self, task, prompt, data, schema, images=None, *, reserve=None):
                return {'estimate': 100000, 'limit': 1000, 'output_reserve': reserve}
        provider = NoRoom()
        _, result = self.bind(provider)
        self.assertFalse(provider.calls)
        self.assertEqual(len(result['budget_failures']), 3)
        self.assertTrue(all(not f['post_sent'] for f in result['budget_failures']))
        self.assertEqual(result['review_status'], 'completed_with_review')

    def test_group_suspect_in_one_successful_split_child_blocks_other_children(self):
        self.store.manifest['config']['v6']['identity_binding_output_tokens'] = 768
        def suspect(result, data):
            if any(o['utterance_id'] == 'u2' for o in data['occurrences']):
                result.update(group_status='suspect', group_reason='Conflicting identity evidence')
            return result
        _, result = self.bind(BindingProvider(suspect))
        self.assertEqual(result['summary']['requests'], 3)
        self.assertEqual([c['owner_magi_id'] for c in result['accepted']], ['1'])

    def test_multiple_name_groups_can_add_aliases_to_one_magi(self):
        self.rows[2]['text'] = 'Toru, help me.'
        self.candidates[2].update(name='toru', raw_surface='Toru', end=4)
        self.rows[0]['text'] = 'I am Mizushima.'
        self.candidates[0].update(name='mizushima', raw_surface='Mizushima', end=14)
        def same_owner(result, data):
            for d in result['decisions']:
                if data['canonical_name'] == 'mizushima':
                    d['owner_magi_id'] = '1'
            return result
        state, result = self.bind(BindingProvider(same_owner))
        forms = {f['base_name'] for f in state['persons']['a']['name_forms']}
        self.assertEqual(forms, {'toru', 'mizushima'})
        self.assertEqual(len(result['groups']), 3)

    def test_known_name_ownership_cannot_be_reassigned(self):
        persons.add_name(self.s, 'a', 'Koyuki', source='prior-confirmed')
        state, result = self.bind()
        self.assertEqual(state['name_index']['koyuki'], ['a'])
        self.assertFalse(any(c['owner_magi_id'] == '2' for c in result['accepted']))
        self.assertTrue(any(r.get('reason') == 'known_name_owner_conflict' for r in result['needs_review']))

    def test_duplicate_occurrence_response_fails_without_retry(self):
        def duplicate(result, data):
            if len(result['decisions']) > 1:
                result['decisions'][1] = copy.deepcopy(result['decisions'][0])
            return result
        _, result = self.bind(BindingProvider(duplicate))
        self.assertEqual(result['summary']['request_statuses'], {'completed': 1, 'failed': 1})
        self.assertEqual([c['owner_magi_id'] for c in result['accepted']], ['1'])

    def test_retained_panel_tampering_before_stage_commit_is_fatal(self):
        _, result = self.bind()
        crop = next(c for c in result['reference_crops'] if c['kind'] == 'panel')
        (self.store.directory / crop['file']).write_bytes(b'changed')
        with self.assertRaises(EvidenceIntegrityError):
            self.bind()

    def test_plan_tampering_and_occurrence_coverage_are_rejected(self):
        self.bind()
        path = self.store.artifact_path('build_identity').parent / 'name-binding/plan.json'
        plan = read_json(path)
        plan['data']['jobs'][0]['request']['occurrences'] = []
        atomic_json(path, plan)
        with self.assertRaisesRegex(ValueError, 'Corrupt frozen'):
            self.bind()

    def test_old_frozen_config_does_not_enable_bootstrap(self):
        cfg = copy.deepcopy(load_config())
        for k in ('identity_name_binding_policy', 'identity_binding_concurrency', 'identity_binding_output_tokens'):
            cfg['v6'].pop(k)
        self.assertFalse(binding.enabled(cfg))
        from manga_pipeline.v6.policy import require_config
        require_config(cfg)

    def test_zero_binding_commits_review_and_continues_to_relationship(self):
        for row in self.analyzed['utterances']:
            for mention in row['mentions']:
                mention['mention_type'] = 'unknown'
        real = RunStore.create(self.root / 'review-run',
            source={'story': 'story', 'chapter': 'chapter-1', 'images': []},
            config=load_config(), bank_path=self.store.manifest['bank_path'])
        for stage, value in [('layout', {}), ('extract', self.raw), ('bubble_geometry', {}),
                ('normalize', self.store.values['normalize']), ('scan', self.store.values['scan']),
                ('analyze', self.analyzed)]:
            real.finish(stage, value)
        provider = BindingProvider()
        self.assertTrue(Pipeline(real, provider=provider).run(until='relationship'))
        real = RunStore(real.directory)
        self.assertTrue(real.completed('relationship'))
        self.assertEqual(real.manifest['steps']['build_identity']['review_status'], 'completed_with_review')
        self.assertEqual(real.read('build_identity')['name_binding']['summary']['requests'], 0)
        self.assertFalse(provider.calls)

    def test_effective_overlay_tampering_is_rejected(self):
        state, result = self.bind()
        result['effective_updates']['u2']['fields']['addressee_person_ids'] = ['a']
        linked = {'policy': identity_v2.POLICY, 'person_state': state, 'proposals': [],
                  'name_binding': result, 'binding_snapshot_hash': result['snapshot_hash']}
        with self.assertRaisesRegex(EvidenceIntegrityError, 'snapshot changed'):
            semantic.export(self.store.values['normalize'], self.analyzed, linked)

    def test_group_history_keeps_known_binding_without_new_confirmation(self):
        state, result = self.bind()
        path = self.store.artifact_path('build_identity').parent / 'name-binding/plan.json'
        evidence = read_json(path)['data']['evidence']
        after, accepted, _, _, _ = binding.reconcile(state, evidence, [], [], self.metadata)
        self.assertFalse(accepted)
        records = [history[-1] for history in after['name_groups_v1']['groups'].values()]
        self.assertTrue(all(r['status'] == 'bound' and r['known_person_ids'] for r in records))

    def test_import_contract_and_parents_distinguish_bootstrap_from_old_v2(self):
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'deployment/runpod'))
        from preanalyze_import import import_run, import_parents
        old = load_config()
        old['v6'].pop('identity_name_binding_policy')
        source = self.root / 'old-v2-import'
        atomic_json(source / 'manifest.json', {'pipeline_version': 6, 'config': old})
        with self.assertRaisesRegex(ValueError, 'Name-binding contract changed'):
            import_run(source, load_config(), through='inpaint')
        self.assertNotIn('scan', import_parents(old)['build_identity'])
        self.assertIn('scan', import_parents(load_config())['build_identity'])


if __name__ == '__main__':
    unittest.main()
