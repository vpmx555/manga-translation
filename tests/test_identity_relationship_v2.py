"""Model-free behavioral regressions for identity v2 and single-pass graph stages."""
import copy
import math
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

from manga_pipeline.pipeline import Pipeline, load_config as current_config
from manga_pipeline.storage.io import atomic_json, fingerprint, read_json
from manga_pipeline.storage.runs import RunStore
from manga_pipeline.runtime.resources import ResourceExhausted
from manga_pipeline.runtime.inference_state import InferenceUncertain
from manga_pipeline.v6 import persons, semantic, identity_v2 as identity
from manga_pipeline.v6 import identity_profiles as profiles, relationship_v2 as relationships
from manga_pipeline.v6.identity_runtime import run_jobs, pack, EvidenceIntegrityError


def load_config(*args, **kwargs):
    """Legacy proof-validator fixtures; simplified validators have separate tests."""
    config = current_config(*args, **kwargs)
    config['v6'].update(scan_policy=None, identity_name_binding_policy='chapter-multimodal-name-binding-v1',
                        scan_output_tokens=4096)
    return config


class Store:
    steps = ('build_identity', 'relationship', 'export', 'translate')

    def __init__(self, root, bank=None, chapter='chapter-1'):
        self.directory = root
        self.manifest = {'config': load_config(), 'source': {'story': 'story', 'chapter': chapter},
                         'bank_path': str(bank or root.parent / 'banks/story/metadata.json')}
        # These fixtures exercise the original v2 identity/profile contract.
        self.manifest['config']['v6']['identity_name_binding_policy'] = None
        self.values = {}

    def read(self, step):
        return copy.deepcopy(self.values[step])

    def artifact_path(self, step):
        return self.directory / step / 'result.json'


def state():
    s = {'schema_version': 6, 'persons': {}, 'name_index': {}, 'cluster_to_person': {},
         'redirects': {}, 'calling_events': {}, 'operations': [], 'observations': {},
         'extraction_scope': 's', 'base_registry_hash': fingerprint(None)}
    for i, pid in enumerate(('a', 'b', 'c'), 1):
        s['persons'][pid] = {'person_id': pid, 'primary_name': None, 'name_forms': [],
                            'components': [], 'appearance_refs': []}
        s['cluster_to_person']['s:' + pid] = pid
        s['observations']['s:' + pid] = [{'source_bank_id': i, 'page_id': 'p1',
            'bbox': [i*20+j, 0, i*20+j+1, 1], 'appearance_key': pid} for j in range(8)]
    persons.add_name(s, 'a', 'Toru', source='confirmed-calling')
    persons.add_name(s, 'b', 'Koyuki', source='confirmed-calling')
    return s


def rows():
    return [{'id': 'u' + str(i), 'text': text, 'reading_order': i, 'content_type': 'dialogue',
        'speaker_person_id': 'a' if i % 2 == 0 else 'b', 'addressee_ids': ['b' if i % 2 == 0 else 'a'],
        'semantic_resolution': 'resolved', 'mentions': [], 'scene_id': 'scene:1'}
        for i, text in enumerate(('Hello Koyuki.', 'Hello Toru.', 'Will you help me?', 'Yes.'))]


class Provider:
    def __init__(self, failure=None):
        self.calls = []
        self.failure = failure

    def request_budget(self, task, prompt, data, schema, images=None, *, reserve=None):
        return {'estimate': 1000, 'limit': 27852, 'output_reserve': reserve, 'input_tokens': 100,
                'num_ctx': 32768}

    def metrics(self):
        return {'kv': .1, 'preemptions': 0, 'generated_tokens': 0}

    def infer(self, task, prompt, data, schema, validator, images=None, **kwargs):
        self.calls.append((task, copy.deepcopy(data), len(images or [])))
        if self.failure:
            raise self.failure
        if task == 'character_profile_v2':
            attrs = {key: {'value': 'unknown', 'basis': 'unknown', 'status': 'unknown', 'evidence_refs': []}
                     for key in profiles.FIELDS}
            if data['crops']:
                attrs['apparent_age_group'] = {'value': 'adolescent', 'basis': 'visual',
                    'status': 'provisional', 'evidence_refs': [data['crops'][0]['crop_id']]}
            return {'person_id': data['identity']['person_id'], 'attributes': attrs}
        a, b = data['identities']
        ref = data['evidence_segments'][0]['trigger']
        return {'relations': [{'category': 'school_work', 'relationship': 'school peers',
            'role_from': 'student', 'role_to': 'student',
            'from_id': a, 'to_id': b, 'symmetric': True, 'status': 'confirmed',
            'evidence_refs': [ref], 'reason': 'Explicit school context'}], 'address_edges': [
                {'from_id': a, 'to_id': b, 'self': 'tớ', 'address': 'cậu', 'status': 'confirmed',
                 'basis': 'dialogue', 'evidence_refs': [ref], 'reason': 'Direct dialogue'},
                {'from_id': b, 'to_id': a, 'self': 'tôi', 'address': 'bạn', 'status': 'provisional',
                 'basis': 'context', 'evidence_refs': [ref], 'reason': 'Independent reverse choice'}]}

    def infer_reserved(self, task, prompt, data, schema, validator, *, reserve):
        return self.infer(task, prompt, data, schema, validator, reserve=reserve)


class IdentityGraphTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.store = Store(self.root / 'run')
        self.s = state()
        self.metadata = {'characters': {str(i): {'appearance_count': count, 'prototype_crop_paths': [],
                         'disabled_prototype_indices': [], 'disabled_crop_paths': []}
                         for i, count in ((1, 100), (2, 50), (3, 5))}}
        bank = Path(self.store.manifest['bank_path'])
        arrays = {}
        for mid, vector in ((1, [1., 0.]), (2, [0., 1.]), (3, [.999, .01])):
            names = []
            for n in range(12):
                name = f'crops/{mid}-{n}.png'
                p = bank.parent / name
                p.parent.mkdir(parents=True, exist_ok=True)
                Image.new('RGB', (12, 12), (mid*30, n*15, 80)).save(p)
                names.append(name)
            self.metadata['characters'][str(mid)]['prototype_crop_paths'] = names
            arrays['character_' + str(mid)] = np.array([vector] * 12, dtype=np.float32)
        atomic_json(bank, self.metadata)
        (bank.parent / 'embeddings').mkdir()
        np.savez(bank.parent / 'embeddings/vectors.npz', **arrays)

    def analyzed(self):
        rs = rows()
        return {'policy': 'independent-batches-v6.1', 'person_state': self.s,
            'calling_events': [], 'source_bank_snapshot': persons.bank_snapshot(self.store.manifest['bank_path']),
            'utterances': [{**r, 'utterance_id': r['id'], 'speaker_cluster_ref': 's:' + r['speaker_person_id'],
                           'speech_style': 'normal', 'evidence': 'fixture',
                           'addressee_person_ids': r['addressee_ids']} for r in rs]}

    def test_complete_stages_freeze_profiles_then_independent_address_edges(self):
        self.store.values.update(analyze=self.analyzed(), normalize={'utterances': rows(), 'excluded': []})
        p = Provider()
        before = persons.bank_snapshot(self.store.manifest['bank_path'])
        linked = identity.build(self.store, p)
        self.store.values['build_identity'] = linked
        self.assertEqual(persons.canonical(linked['person_state'], 'c'), 'a')
        self.assertEqual(set(linked['profiles']), {'a', 'b'})
        self.assertEqual([x[2] for x in p.calls], [10, 10])
        graph = relationships.build(self.store, p)
        self.assertEqual([t[0] for t in p.calls].count('relationship_vi_v2'), 1)
        self.assertEqual([(e['from_id'], e['to_id']) for e in graph['address_table']], [('a', 'b'), ('b', 'a')])
        self.assertNotEqual(graph['address_table'][0]['self'], graph['address_table'][1]['self'])
        self.assertEqual(before, persons.bank_snapshot(self.store.manifest['bank_path']))
        count = len(p.calls)
        self.assertEqual(linked, identity.build(self.store, p))
        self.assertEqual(graph, relationships.build(self.store, p))
        self.assertEqual(count, len(p.calls))

    def test_visual_threshold_is_strict_and_cannot_merge_two_confirmed_people(self):
        retained, _ = identity.vectors(self.store.manifest['bank_path'], self.metadata)
        linked, proposals, _, _ = identity.visual_links(self.s, self.metadata, retained, .38)
        self.assertEqual([p['magi_id'] for p in proposals], ['3'])
        self.assertEqual(linked['cluster_to_person']['s:b'], 'b')
        distance = proposals[0]['min_crop']
        unchanged, none, _, _ = identity.visual_links(self.s, self.metadata, retained, distance)
        self.assertEqual(none, [])
        self.assertEqual(unchanged['cluster_to_person']['s:c'], 'c')

    def test_no_chaining_via_secondary_member_and_history_selects_main(self):
        s = self.s
        s['cluster_to_person']['s:b'] = 'a'
        # Main=1 from lifetime count, although member 2 has more current detections.
        s['observations']['s:b'] = [{**s['observations']['s:b'][0], 'bbox': [i, 10, i+1, 11]}
                                   for i in range(32)]
        retained, _ = identity.vectors(self.store.manifest['bank_path'], self.metadata)
        retained['3']['values'][:] = [0., 1.]
        result, proposals, _, anchors = identity.visual_links(s, self.metadata, retained, .38)
        self.assertEqual(anchors['a'], '1')
        self.assertEqual(proposals, [])
        self.assertEqual(result['cluster_to_person']['s:c'], 'c')

    def test_nearest_qualifying_identity_wins_without_margin(self):
        retained, _ = identity.vectors(self.store.manifest['bank_path'], self.metadata)
        retained['2']['values'][:] = [.99995, .01]
        retained['3']['values'][:] = [.9998, .02]
        _, proposals, _, _ = identity.visual_links(self.s, self.metadata, retained, .38)
        self.assertEqual(proposals[0]['person_id'], 'b')
        self.assertEqual(len(proposals[0]['candidates']), 2)

    def test_name_call_cannot_reassign_existing_named_identity(self):
        proposal = {'cluster_ref': 's:b', 'cluster_refs': ['s:b'], 'person_id': 'a', 'same_person_status': 'supported'}
        result, accepted, reviews = identity.guarded_name_links(self.s, [proposal])
        self.assertEqual(accepted, [])
        self.assertEqual(result['cluster_to_person']['s:b'], 'b')
        self.assertEqual(reviews[0]['reason'], 'confirmed_identity_is_immutable')

    def test_profile_update_trigger_ignores_scene_and_crop_changes(self):
        doc = {'utterances': rows()}
        _, h1 = profiles.profile_evidence(profiles.source_rows(doc), 'a', self.s)
        doc['utterances'][0]['scene_id'] = 'scene:changed'
        _, h2 = profiles.profile_evidence(profiles.source_rows(doc), 'a', self.s)
        self.assertEqual(h1, h2)
        doc['utterances'][0]['text'] += ' New dialogue.'
        _, h3 = profiles.profile_evidence(profiles.source_rows(doc), 'a', self.s)
        self.assertNotEqual(h1, h3)

    def test_mentions_without_direct_communication_do_not_create_pair(self):
        rs = rows()
        for r in rs:
            r['addressee_ids'] = []
            r['mentions'] = [{'target_person_id': 'b', 'confirmed_person_name': True}]
        self.assertEqual(relationships.direct_segments(rs, 'a', 'b'), [])

    def test_confirmed_address_is_retained_on_conflict_but_provisional_can_change(self):
        req = self.pair_request()
        old = Provider().infer('relationship_vi_v2', '', req, {}, lambda x: None)
        fresh = copy.deepcopy(old)
        fresh['address_edges'][0]['address'] = 'bạn'
        fresh['address_edges'][1]['self'] = 'tớ'
        result, issues = relationships.reconcile({'decision': old}, fresh)
        self.assertEqual(result['address_edges'][0]['address'], 'cậu')
        self.assertEqual(result['address_edges'][1]['self'], 'tớ')
        self.assertTrue(issues)

    def test_confirmed_profile_role_is_retained_when_new_claim_conflicts(self):
        attribute = {'value': 'teacher', 'status': 'confirmed', 'basis': 'dialogue', 'evidence_refs': ['old:u']}
        previous = {'person_id': 'a', 'attributes': {'roles': attribute}}
        fresh = {'person_id': 'a', 'attributes': {'roles': {**attribute, 'value': 'student', 'evidence_refs': ['new:u']}}}
        result, conflicts = profiles.reconcile_profile(previous, fresh)
        self.assertEqual(result['attributes']['roles']['value'], 'teacher')
        self.assertEqual(conflicts[0]['proposed']['value'], 'student')

    def pair_request(self):
        return {'identities': {'a': {'primary_name': 'Toru', 'aliases': []},
                               'b': {'primary_name': 'Koyuki', 'aliases': []}},
                'evidence_segments': relationships.direct_segments(rows(), 'a', 'b'), 'crops': [], 'profile_refs': []}

    def test_wrong_target_name_and_japanese_pronouns_are_rejected(self):
        req = self.pair_request()
        value = Provider().infer('relationship_vi_v2', '', req, {}, lambda x: None)
        relationships.validate(value, req)
        value['address_edges'][0]['address'] = 'Toru-san'
        with self.assertRaisesRegex(ValueError, 'wrong identity'):
            relationships.validate(value, req)
        value['address_edges'][0]['address'] = 'Koyuki-san'
        value['address_edges'][0]['self'] = 'Watashi'
        with self.assertRaises(ValueError):
            relationships.validate(value, req)

    def test_oom_and_invalid_response_are_not_retried_even_on_resume(self):
        job = {'batch_id': 'relationship:fixture', 'task': 'relationship_vi_v2',
               'request': self.pair_request(), 'crops': []}
        for failure in (ResourceExhausted('OOM'), ValueError('bad schema')):
            with self.subTest(failure=str(failure)):
                store = Store(self.root / type(failure).__name__)
                p = Provider(failure)
                args = (store, p, 'relationship', [job], relationships.PROMPT, relationships.schema,
                        relationships.validate, 4096, 8)
                first = run_jobs(*args)
                second = run_jobs(*args)
                self.assertEqual(first, second)
                self.assertEqual(first[0]['status'], 'failed')
                self.assertEqual(len(p.calls), 1)

    def test_uncertain_post_is_not_resent_on_resume(self):
        job = {'batch_id': 'relationship:uncertain', 'task': 'relationship_vi_v2',
               'request': self.pair_request(), 'crops': []}
        p = Provider(InferenceUncertain('completion unknown'))
        args = (self.store, p, 'relationship', [job], relationships.PROMPT,
                relationships.schema, relationships.validate, 4096, 8)
        self.assertEqual(run_jobs(*args)[0]['status'], 'uncertain')
        self.assertEqual(run_jobs(*args)[0]['status'], 'uncertain')
        self.assertEqual(len(p.calls), 1)

    def test_budget_packing_keeps_segments_whole_and_never_drops_images(self):
        class Budget:
            def budget(self, task, prompt, request, schema, reserve, images):
                self.asserted_images = len(images)
                return {'estimate': 100 + len(request['evidence_segments'])*100, 'limit': 250}
        calls = Budget()
        req = self.pair_request()
        wire, report = pack(calls, 'relationship_vi_v2', '', req, {}, 4096, ['image']*6)
        self.assertEqual(calls.asserted_images, 6)
        self.assertEqual(len(wire['evidence_segments']), 1)
        self.assertIn(wire['evidence_segments'][0], req['evidence_segments'])
        self.assertTrue(report['omitted_segments'])

    def test_mandatory_budget_overflow_does_not_send_or_retry(self):
        class TooSmall(Provider):
            def request_budget(self, *args, **kwargs):
                return {'estimate': 1000, 'limit': 100, 'output_reserve': 4096, 'input_tokens': 900}
        job = {'batch_id': 'relationship:oversize', 'task': 'relationship_vi_v2',
               'request': self.pair_request(), 'crops': []}
        p = TooSmall()
        args = (self.store, p, 'relationship', [job], relationships.PROMPT,
                relationships.schema, relationships.validate, 4096, 8)
        first = run_jobs(*args)
        self.assertEqual(first[0]['error_type'], 'BudgetExceeded')
        self.assertEqual(run_jobs(*args), first)
        self.assertEqual(p.calls, [])

    def test_corrupt_decision_cannot_be_reused_as_a_successful_result(self):
        job = {'batch_id': 'relationship:corrupt', 'task': 'relationship_vi_v2',
               'request': self.pair_request(), 'crops': []}
        p = Provider()
        args = (self.store, p, 'relationship', [job], relationships.PROMPT,
                relationships.schema, relationships.validate, 4096, 8)
        run_jobs(*args)
        path = next((self.store.artifact_path('relationship').parent / 'decisions').glob('*.json'))
        record = read_json(path)
        record['record']['result']['address_edges'][0]['self'] = 'tao'
        atomic_json(path, record)
        with self.assertRaises(EvidenceIntegrityError):
            run_jobs(*args)
        self.assertEqual(len(p.calls), 1)

    def test_vllm_image_limit_is_pinned_and_covered_by_engine_signature(self):
        import sys
        from unittest.mock import patch
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'deployment/runpod'))
        import vllm_runtime
        import json
        cfg = load_config()
        command = vllm_runtime.command(cfg)
        limit = json.loads(command[command.index('--limit-mm-per-prompt')+1])
        self.assertEqual(limit, {'image': 10, 'video': 0})
        legacy = copy.deepcopy(cfg)
        del legacy['vllm']['max_images_per_prompt']
        self.assertNotEqual(vllm_runtime.signature(cfg), vllm_runtime.signature(legacy))
        self.assertNotIn('--limit-mm-per-prompt', vllm_runtime.command(legacy))

    def test_post_analysis_import_rejects_legacy_identity_stage_contract(self):
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'deployment/runpod'))
        from preanalyze_import import import_run
        old_config = load_config()
        del old_config['v6']['identity_relationship_policy']
        source = self.root / 'old-import'
        atomic_json(source / 'manifest.json', {'pipeline_version': 6, 'config': old_config})
        with self.assertRaisesRegex(ValueError, 'contract changed'):
            import_run(source, load_config(), through='inpaint')

    def test_new_and_legacy_run_layouts_remain_distinct(self):
        from manga_pipeline.storage.runs import flow_steps
        cfg = load_config()
        self.assertIn('build_identity', flow_steps(cfg))
        self.assertNotIn('identity', flow_steps(cfg))
        del cfg['v6']['identity_relationship_policy']
        self.assertIn('identity', flow_steps(cfg))
        self.assertNotIn('relationship', flow_steps(cfg))

    def test_real_checkpoint_flow_commits_fallback_and_exports_after_oom(self):
        cfg = load_config()
        real = RunStore.create(self.root / 'production-run',
            source={'story': 'story', 'chapter': 'chapter-1', 'images': []},
            config=cfg, bank_path=self.store.manifest['bank_path'])
        for stage, value in [('layout', {}), ('extract', {}), ('bubble_geometry', {}),
                             ('normalize', {'utterances': rows(), 'excluded': []}), ('scan', {}),
                             ('analyze', self.analyzed())]:
            real.finish(stage, value)
        provider = Provider(ResourceExhausted('OOM without retry'))
        self.assertTrue(Pipeline(real, provider=provider).run(until='export'))
        real = RunStore(real.directory)
        for stage in ('build_identity', 'relationship', 'export'):
            self.assertTrue(real.completed(stage))
            self.assertTrue((real.artifact_path(stage).parent / 'review.md').is_file())
        self.assertEqual(len(real.read('build_identity')['inference_failures']), 2)
        graph = real.read('relationship')
        self.assertEqual(len(graph['inference_failures']), 1)
        self.assertTrue(all(e['status'] == 'unknown' for e in graph['address_table']))
        self.assertEqual(real.read('export')['relationship_snapshot_hash'], graph['snapshot_hash'])
        attempts = len(provider.calls)
        self.assertTrue(Pipeline(real, provider=provider).run(until='export'))
        self.assertEqual(attempts, len(provider.calls))

    def test_real_translation_reads_directed_graph_without_reverse_review_leak(self):
        class TranslationProvider(Provider):
            def infer(self, task, prompt, data, schema, validator, images=None, **kwargs):
                if task == 'translate_vi_group_v6_natural':
                    self.calls.append((task, copy.deepcopy(data), len(images or [])))
                    return {'translations': [{'id': r['id'], 'translation': 'Chào cậu.',
                        'needs_review': False, 'review_reason': '', 'glossary_proposals': []}
                        for r in data['targets']]}
                result = super().infer(task, prompt, data, schema, validator, images, **kwargs)
                if task == 'relationship_vi_v2':
                    result['address_edges'][1].update(basis='visual_age_estimate',
                        evidence_refs=[r['ref'] for r in data['profile_refs']])
                return result

        real = RunStore.create(self.root / 'translation-run',
            source={'story': 'story', 'chapter': 'chapter-1', 'images': []},
            config=load_config(), bank_path=self.store.manifest['bank_path'])
        for stage, value in [('layout', {}), ('extract', {}), ('bubble_geometry', {}),
                             ('normalize', {'utterances': rows(), 'excluded': []}), ('scan', {}),
                             ('analyze', self.analyzed())]:
            real.finish(stage, value)
        provider = TranslationProvider()
        self.assertTrue(Pipeline(real, provider=provider).run(until='translate'))
        real = RunStore(real.directory)
        translated = real.read('translate')
        self.assertEqual(translated['failures'], [])
        self.assertEqual(translated['unresolved_translation_targets'], [])
        for row in translated['utterances']:
            item = row['translations']['natural']
            self.assertEqual(item['translation'], 'Chào cậu.')
            self.assertEqual(item['needs_review'], row['speaker_person_id'] == 'b')
            if row['speaker_person_id'] == 'b':
                self.assertIn('visual age estimate', item['review_reason'])
        calls = [c for c in provider.calls if c[0] == 'translate_vi_group_v6_natural']
        self.assertTrue(calls)
        self.assertEqual(calls[0][1]['relationship_snapshot_hash'],
                         real.read('relationship')['snapshot_hash'])
        self.assertTrue(Pipeline(RunStore(real.directory), provider=provider).run(until='translate'))
        self.assertEqual(len(calls), len([c for c in provider.calls if c[0] == 'translate_vi_group_v6_natural']))

    def test_profiles_reuse_without_new_dialogue_despite_changed_main_member(self):
        self.store.values.update(analyze=self.analyzed(), normalize={'utterances': rows(), 'excluded': []})
        p = Provider()
        linked = identity.build(self.store, p)
        self.store.values['build_identity'] = linked
        relationships.build(self.store, p)
        old_count = len(p.calls)
        next_store = Store(self.root / 'next-run', Path(self.store.manifest['bank_path']), 'chapter-2')
        doc = semantic.export(self.store.values['normalize'], self.store.values['analyze'], linked)
        doc['utterances'] = []
        doc['person_registry_snapshot']['extraction_scope'] = 'next-chapter'
        metadata = copy.deepcopy(self.metadata)
        metadata['characters']['3']['appearance_count'] = 1000
        atomic_json(Path(next_store.manifest['bank_path']), metadata)
        result = profiles.build_profiles(next_store, doc, p)
        self.assertEqual(result['eligible_identities']['a']['main_magi_id'], '3')
        self.assertEqual(result['profiles'], linked['profiles'])
        self.assertEqual(old_count, len(p.calls))

    def test_committed_reference_crop_tampering_invalidates_stage(self):
        real = RunStore.create(self.root / 'protected-run',
            source={'story': 'story', 'chapter': 'chapter-1', 'images': []}, config=load_config(),
            bank_path=self.store.manifest['bank_path'])
        for stage in ('layout', 'extract', 'bubble_geometry', 'normalize', 'scan', 'analyze'):
            real.finish(stage, {})
        image = real.directory / 'reference.png'
        image.write_bytes(b'original')
        from manga_pipeline.storage.io import file_hash
        real.finish('build_identity', {'reference_crops': [{'file': 'reference.png', 'retained_sha256': file_hash(image)}]})
        self.assertTrue(real.completed('build_identity'))
        image.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'crop changed'):
            RunStore(real.directory).completed('build_identity')


if __name__ == '__main__':
    unittest.main()
