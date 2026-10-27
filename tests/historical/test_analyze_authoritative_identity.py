"""Model-free vote registration, immutable projection and per-stage serving tests."""
# Historical contract test; requires the matching frozen source.
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from manga_pipeline.pipeline import load_config, Pipeline
from manga_pipeline.storage.io import atomic_json, read_json
from manga_pipeline.storage.runs import RunStore
from manga_pipeline.v6 import persons, semantic
from manga_pipeline.v6.identity_from_analyze import register, POLICY
from manga_pipeline.v6.identity_projection import document_view
from manga_pipeline.runtime.stage_policy import request_policy, server_context
from manga_pipeline.providers.vllm import VLLM
import test_identity_name_binding as fixtures


def data(assignments):
    mids = ('2', '5', '12', '15')
    state = {'schema_version': 6, 'extraction_scope': 's', 'persons': {}, 'observations': {},
             'cluster_to_person': {}, 'redirects': {}, 'name_index': {}, 'cannot_links': [], 'calling_events': {}}
    for mid in mids:
        pid, ref = 'p'+mid, 's:c'+mid
        state['persons'][pid] = {'primary_name': None, 'name_forms': [], 'components': [], 'appearance_refs': []}
        state['cluster_to_person'][ref] = pid
        state['observations'][ref] = [{'source_bank_id': int(mid), 'page_id': 'page1', 'bbox': [int(mid), 0, int(mid)+1, 1]}]
    groups = [{'group_id': g, 'canonical_name': n, 'aliases': [n]} for g, n in
              [('t', 'Toru Mizushima'), ('k', 'Koyuki Sasamori'), ('h', 'Hijiri'), ('a', 'Asahi')]]
    rows, candidates = [], []
    for i, (mid, gid, kind) in enumerate(assignments):
        uid, cid = 'u'+str(i), 'n'+str(i)
        candidates.append({'id': cid, 'group_id': gid, 'utterance_id': uid, 'original_start': 0, 'original_end': 4})
        rows.append({'utterance_id': uid, 'content_type': 'dialogue', 'speech_style': 'normal',
                     'speaker_person_id': 'p2', 'speaker_cluster_ref': 's:c2', 'speaker_kind': 'person',
                     'addressee_person_ids': ['p12'], 'evidence': 'fixture',
                     'mentions': [{'candidate_id': cid, 'confirmed_person_name': True,
                         'target_person_id': 'p'+mid, 'recipient_cluster_ref': 's:c'+mid,
                         'mention_type': kind}]})
    raw = {'pages': [{'id': 'page'+str(i), 'character_ids': [int(m) for m in mids],
                     'characters': [[int(m), 0, int(m)+1, 1] for m in mids]} for i in range(8)]}
    metadata = {'characters': {m: {'appearance_count': int(m)} for m in mids}}
    return {'policy': 'independent-batches-v6.1', 'person_state': state, 'utterances': rows}, {
        'candidates': candidates, 'name_normalization': {'groups': groups}}, raw, metadata


class RegistrationTests(unittest.TestCase):
    def test_review_includes_below_gate_and_competing_candidates_without_model_requests(self):
        import sys
        import tempfile
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'deployment/runpod'))
        from identity_failure_review import authoritative_review
        selection = {'magi_id': '5', 'eligible': False, 'candidates': [{'group_id': 'k'}, {'group_id': 't'}]}
        with tempfile.TemporaryDirectory() as directory:
            payload = authoritative_review(Path(directory), {'selections': [selection],
                'needs_review': [{'reason': 'competing_groups'}]})
            self.assertEqual(payload['request_count'], 0)
            self.assertEqual(payload['results'], [selection])
            self.assertEqual(payload['reviews'][0]['reason'], 'competing_groups')
            for suffix in ('html', 'json', 'md'):
                self.assertTrue((Path(directory) / ('errors.' + suffix)).is_file())

    def run_registration(self, assignments):
        analyzed, scanned, raw, meta = data(assignments)
        prior = copy.deepcopy(analyzed['person_state'])
        original = copy.deepcopy(analyzed)
        state, result = register(analyzed, scanned, raw, meta, prior, 8)
        self.assertEqual(analyzed, original)
        return state, result, analyzed

    def test_reference_owner_does_not_become_speaker_or_addressee(self):
        state, result, _ = self.run_registration([('5', 'k', 'reference')])
        self.assertEqual(state['persons']['p5']['primary_name'], 'Koyuki Sasamori')
        self.assertIsNone(state['persons']['p2']['primary_name'])
        self.assertIsNone(state['persons']['p12']['primary_name'])
        self.assertEqual(len(result['selections']), 1)

    def test_four_different_winners_do_not_collapse_into_one_identity(self):
        state, result, _ = self.run_registration([('2', 't', 'reference'), ('5', 'k', 'reference'),
                                                  ('12', 'h', 'reference'), ('15', 'a', 'reference')])
        self.assertEqual(len({persons.canonical(state, 'p'+m) for m in ('2', '5', '12', '15')}), 4)
        self.assertFalse(result['proposals'])

    def test_majority_creates_name_and_retains_competing_group(self):
        state, result, _ = self.run_registration([('5', 'k', 'reference'), ('5', 'k', 'reference'), ('5', 'a', 'introduction')])
        self.assertEqual(state['persons']['p5']['primary_name'], 'Koyuki Sasamori')
        self.assertTrue(result['selections'][0]['needs_review'])
        self.assertEqual(result['selections'][0]['support'], 2)

    def test_tie_uses_type_priority_then_chapter_order_and_still_creates_name(self):
        for rows, expected in [([('5', 'a', 'reference'), ('5', 'k', 'introduction')], 'Koyuki Sasamori'),
                               ([('5', 'a', 'direct_address'), ('5', 'k', 'reference')], 'Asahi'),
                               ([('5', 'a', 'reference'), ('5', 'k', 'reference')], 'Asahi')]:
            with self.subTest(rows=rows):
                state, _, _ = self.run_registration(rows)
                self.assertEqual(state['persons']['p5']['primary_name'], expected)

    def test_duplicate_mentions_are_one_vote(self):
        a, s, raw, meta = data([('5', 'k', 'reference')])
        a['utterances'][0]['mentions'] *= 2
        state, result = register(a, s, raw, meta, copy.deepcopy(a['person_state']), 8)
        self.assertEqual(result['selections'][0]['support'], 1)

    def test_only_same_winning_group_links_new_magi(self):
        state, result, _ = self.run_registration([('5', 'k', 'reference'), ('12', 'k', 'reference'), ('2', 't', 'reference')])
        self.assertEqual(persons.canonical(state, 'p5'), persons.canonical(state, 'p12'))
        self.assertNotEqual(persons.canonical(state, 'p2'), persons.canonical(state, 'p5'))

    def test_existing_identities_are_not_merged(self):
        a, s, raw, meta = data([('5', 'k', 'reference'), ('12', 'k', 'reference')])
        prior = copy.deepcopy(a['person_state'])
        persons.add_name(prior, 'p5', 'Koyuki Sasamori', source='old')
        persons.add_name(prior, 'p12', 'Hijiri', source='old')
        a['person_state'] = copy.deepcopy(prior)
        state, _ = register(a, s, raw, meta, prior, 8)
        self.assertNotEqual(persons.canonical(state, 'p5'), persons.canonical(state, 'p12'))

    def test_no_new_name_below_crop_gate(self):
        a, s, raw, meta = data([('5', 'k', 'reference')])
        raw['pages'] = raw['pages'][:7]
        state, result = register(a, s, raw, meta, copy.deepcopy(a['person_state']), 8)
        self.assertIsNone(state['persons']['p5']['primary_name'])
        self.assertFalse(result['selections'][0]['eligible'])

    def test_export_retains_analyze_fields_and_exposes_separate_identity_view(self):
        state, result, analyzed = self.run_registration([('5', 'k', 'reference'), ('12', 'k', 'reference')])
        document = {'utterances': [{'id': r['utterance_id'], 'text': 'Sasamori-chan', 'reading_order': i}
                                   for i, r in enumerate(analyzed['utterances'])], 'excluded': []}
        linked = {'policy': 'identity-relationship-v2', 'person_state': state, 'proposals': result['proposals'],
                  'name_binding': {'policy': POLICY}, 'binding_snapshot_hash': 'fixture'}
        exported = semantic.export(document, analyzed, linked)
        for source, row in zip(analyzed['utterances'], exported['utterances']):
            for field in ('speaker_person_id', 'addressee_person_ids', 'mentions', 'content_type', 'speech_style'):
                self.assertEqual(row[field], source[field])
        before = copy.deepcopy(exported)
        view = document_view(exported)
        self.assertEqual(view['utterances'][0]['addressee_ids'], [persons.canonical(state, 'p12')])
        self.assertEqual(exported, before)


class StagePolicyTests(unittest.TestCase):
    def test_analyze_only_thinks_and_uses_64k(self):
        cfg = load_config()
        self.assertEqual(server_context(cfg), 65536)
        self.assertEqual(request_policy(cfg, 'analyze_dialogue_v6'),
                         {'enable_thinking': True, 'num_ctx': 65536, 'timeout': 1200,
                          'temperature': 1.0, 'presence_penalty': 1.5})
        for task in ('analyze_scene_v6', 'character_profile_v2', 'relationship_vi_v2', 'translate_vi_group_v6_natural'):
            self.assertEqual(request_policy(cfg, task), {'enable_thinking': False, 'num_ctx': 32768,
                'timeout': 600, 'temperature': 0, 'presence_penalty': 0})

    def test_tokenize_post_cache_and_timeout_use_same_stage_policy(self):
        cfg = load_config()
        with tempfile.TemporaryDirectory() as directory:
            provider = VLLM(cfg, Path(directory))
            calls = []
            def http(route, payload=None):
                calls.append((route, payload, getattr(provider._http_options, 'timeout', None)))
                if route == '/tokenize':
                    return {'count': 100}
                return {'usage': {'prompt_tokens': 100}, 'choices': [{'finish_reason': 'stop',
                    'message': {'reasoning_content': 'private model reasoning', 'content': '{"ok": true}'}}]}
            with patch.object(provider, '_ready'), patch.object(provider, '_http', side_effect=http):
                for task in ('analyze_dialogue_v6', 'character_profile_v2'):
                    provider.infer(task, 'prompt', {}, {'type': 'object'}, lambda value: None)
            posts = [c for c in calls if c[0] == '/v1/chat/completions']
            self.assertEqual([c[1]['chat_template_kwargs']['enable_thinking'] for c in posts], [True, False])
            self.assertEqual([c[2] for c in posts], [1200, 600])
            self.assertEqual(posts[0][1]['max_tokens'], 12288)
            self.assertEqual([c[1]['temperature'] for c in posts], [1.0, 0])
            self.assertEqual([c[1]['presence_penalty'] for c in posts], [1.5, 0])
            tokenizes = [c for c in calls if c[0] == '/tokenize']
            self.assertEqual([c[1]['chat_template_kwargs']['enable_thinking'] for c in tokenizes], [True, False])


class PipelineTests(unittest.TestCase):
    def setUp(self):
        fixtures.NameBindingTests.setUp(self)

    def test_identity_to_translation_has_no_binding_post_or_panel_and_preserves_source(self):
        # One row names Toru, another names Koyuki; analyze has already decided owners.
        cfg = load_config()
        self.raw['pages'] = [{**copy.deepcopy(self.raw['pages'][0]), 'id': 'p'+str(i+1)} for i in range(8)]
        groups = [{'group_id': 't', 'canonical_name': 'Toru', 'aliases': ['Toru']},
                  {'group_id': 'k', 'canonical_name': 'Koyuki', 'aliases': ['Koyuki']}]
        for group in groups:
            group.update(status='proposed', members=[], evidence='fixture')
        for i, candidate in enumerate(self.candidates):
            candidate['group_id'] = 't' if i == 0 else 'k'
        for i, row in enumerate(self.analyzed['utterances']):
            row.update(speaker_person_id='a' if i % 2 == 0 else 'b',
                       addressee_person_ids=['b' if i % 2 == 0 else 'a'],
                       addressee_type='individual', content_type='dialogue', speaker_kind='person')
            for mention in row['mentions']:
                mention.update(confirmed_person_name=True, target_person_id='a' if i == 0 else 'b')
        scanned = {'candidates': self.candidates, 'name_normalization': None}
        # This fixture uses direct group annotations without changing OCR.
        for c in scanned['candidates']:
            c['alias_groups'] = [next(g for g in groups if g['group_id'] == c['group_id'])]
        scanned['name_normalization'] = {'groups': groups}
        real = RunStore.create(self.root / 'authoritative', source={'story': 'story', 'chapter': 'chapter-1', 'images': []},
                               config=cfg, bank_path=self.store.manifest['bank_path'])
        atomic_json(real.directory / 'snapshots/persons-v6.json', {'state': self.s})
        for step, value in [('layout', {}), ('extract', self.raw), ('bubble_geometry', {}),
                            ('normalize', self.store.values['normalize']), ('scan', scanned), ('analyze', self.analyzed)]:
            real.finish(step, value)
        provider = fixtures.BindingProvider()
        # Export's normalizer is omitted here because fixture text needs no OCR edits.
        # register reads group definitions directly, while semantic export reads analyze.normalization (absent).
        self.assertTrue(Pipeline(real, provider=provider).run(until='translate'))
        real = RunStore(real.directory)
        identity = real.read('build_identity')
        self.assertEqual(identity['name_binding']['summary']['requests'], 0)
        self.assertEqual(set(identity['profiles']), {'a', 'b'})
        self.assertTrue(all(task not in {'identity_name_binding_v1', 'identity_name_binding_simple_v2'} for task, *_ in provider.calls))
        self.assertTrue(all(c.get('kind') != 'panel' for c in identity['reference_crops']))
        self.assertEqual(real.read('analyze'), self.analyzed)
        exported = real.read('export')
        for source, row in zip(self.analyzed['utterances'], exported['utterances']):
            self.assertEqual(row['mentions'], source['mentions'])
            self.assertEqual(row['speaker_person_id'], source['speaker_person_id'])
        self.assertEqual(len(real.read('relationship')['pairs']), 1)


if __name__ == '__main__':
    unittest.main()
