"""Model-free behavioral coverage for the chapter graph contract."""
import copy
import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace

from PIL import Image

from manga_pipeline.cli.main import build_parser
from manga_pipeline.pipeline import load_config
from manga_pipeline.storage.io import atomic_json, file_hash, read_json
from manga_pipeline.storage.runs import RunStore
from manga_pipeline.v6.event_analysis import analyze
from manga_pipeline.v6.event_visuals import magi_tail_evidence, page_regions, render
from manga_pipeline.v6.graph import build as graph_build, hierarchy
from manga_pipeline.v6.normalize import normalize, panel_mapping
from manga_pipeline.v6.reanalysis import apply_corrections, build as reanalyze_build
from manga_pipeline.v6.story_contract import validate_row
from manga_pipeline.v6.verification import verify


class Provider:
    def __init__(self, fail_event=None, fail_review=False):
        self.fail_event, self.fail_review = fail_event, fail_review
        self.requests, self.lock = [], threading.Lock()

    def request_budget(self, task, prompt, request, schema, reserve, images=None):
        return {'estimate': 100+reserve, 'input_tokens': 100, 'limit': 60000,
                'num_ctx': 65536, 'output_reserve': reserve, 'method': 'fixture'}

    def infer(self, task, prompt, data, schema, validator, images=None, **kwargs):
        with self.lock:
            self.requests.append((task, copy.deepcopy(data)))
        if task == 'graph_chapter_v7':
            result = {'scenes': [{'start_id': 'u0', 'title': 'Lời chào', 'summary': 'Một cuộc trao đổi.',
                'confidence': 8, 'events': [{'start_id': 'u0', 'title': 'Gọi Toru', 'summary': 'Gọi tên.', 'confidence': 8},
                                          {'start_id': 'u4', 'title': 'Trao đổi', 'summary': 'Trả lời.', 'confidence': 8}]}],
                'groups': [{'group_id': 'g1', 'canonical_name': 'Toru', 'reason': 'Tên được gọi.'}],
                'names': [{'text_id': 'u0', 'old_name': 'TORU', 'new_name': 'Toru', 'group_id': 'g1'}], 'links': []}
        elif task == 'analyze_event_v7':
            if data['event_id'] == self.fail_event:
                raise ValueError('Malformed fixture response')
            people = data['visual_people']['people']
            pid = next(p for p, info in people.items() if info['magi_ids'])
            cluster = next((r for r, v in data['visual_people']['clusters'].items() if v['person_id'] == pid), None)
            rows = []
            for target in data['targets']:
                mentions = [{'candidate_id': m['id'], 'confirmed_person_name': True, 'mention_type': 'reference',
                    'target_person_id': pid, 'recipient_cluster_ref': cluster, 'visual_support': 'unknown',
                    'components': [{'kind': 'given_name', 'value': 'Toru'}],
                    'confidence': {'mention_type': 8, 'target': 8}, 'evidence': 'Tên trong lời nói.'}
                    for m in data['mentions'] if m['utterance_id'] == target['id']]
                rows.append({'utterance_id': target['id'], 'content_type': 'thought', 'speech_style': 'thought',
                    'speaker_kind': 'person', 'speaker_person_id': pid, 'speaker_cluster_ref': cluster,
                    'addressee_type': 'self', 'addressee_person_ids': [pid], 'mentions': mentions,
                    'confidence': {k: 8 for k in ('speaker', 'addressee', 'content_type', 'speech_style')},
                    'evidence': 'Lời độc thoại.', 'evidence_refs': [target['id']]})
            desc = 'Đã đọc ' + data['event_id']
            result = {'utterances': rows, 'event_title': 'Lời giải thích', 'event_description': desc,
                'event_confidence': 8, 'scene_title': 'Cuộc gặp', 'scene_description': desc, 'scene_confidence': 8,
                'scene_memory': {'description': desc, 'facts': [{'description': desc,
                    'source_ids': [data['targets'][0]['id']], 'confidence': 8}], 'characters': [], 'unresolved': []}}
        else:
            if self.fail_review:
                raise ValueError('Broken review JSON')
            result = {'corrections': [], 'event_updates': []}
        validator(result)
        return result

    def infer_reserved(self, task, prompt, data, schema, validator, reserve):
        return self.infer(task, prompt, data, schema, validator, reserve=reserve)


class StoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def fixture(self):
        cfg = load_config()
        cfg['runtime']['enabled'] = False
        image = self.root / 'page.png'
        Image.new('RGB', (400, 400), 'white').save(image)
        bank = self.root / 'banks/story/metadata.json'
        atomic_json(bank, {'characters': {'2': {'id': 2, 'appearance_count': 8}}})
        source = {'story': 'story', 'chapter': 'chapter', 'folder': str(self.root),
                  'images': [{'path': str(image), 'sha256': file_hash(image)}]}
        store = RunStore.create(self.root / 'run', source=source, config=cfg, bank_path=bank)
        page = {'id': 'p1', 'page': 1, 'image_path': str(image), 'width': 400, 'height': 400,
                'panels': [[0, 0, 200, 400], [200, 0, 400, 400]], 'tails': [],
                'characters': [[10, 100, 100, 300]], 'character_cluster_labels': [0], 'character_ids': [2],
                'text_character_associations': [], 'text_tail_associations': []}
        raw = {'schema_version': 1, 'kind': 'magi_raw', 'reading_direction': 'rtl', 'document_id': 'doc',
               'story_id': 'story', 'chapter_id': 'chapter', 'pages': [page], 'texts': []}
        for i in range(8):
            raw['texts'].append({'id': 'p1:t'+str(i), 'page_id': 'p1', 'page': 1, 'text_index': i,
                'reading_order': i, 'text': 'TORU...' if i == 0 else 'Câu '+str(i),
                'bbox': [20, 10+i*30, 90, 30+i*30], 'panel_index': 0, 'speaker_cluster_id': 'p1:c0',
                'speaker_id': 2, 'character_detection_index': 0, 'is_essential_text': False})
        store.finish('layout', {'pages': [{'page': 1, 'source': source['images'][0], 'panels': [], 'bubbles': [], 'effects_candidates': [], 'files': {}}]})
        store.finish('extract', raw)
        store.finish('bubble_geometry', raw)
        store.finish('normalize', normalize(raw))
        return store

    def analysis_fixture(self, provider=None):
        provider = provider or Provider()
        store = self.fixture()
        store.finish('graph', graph_build(store, provider))
        result = analyze(store, provider)
        store.finish('analyze', result)
        return store, provider, result

    def test_panel_first_box_threshold_not_panel_area(self):
        page = {'panels': [[0, 0, 5, 100], [5, 0, 100, 100]]}
        sources = [{'bbox': [0, 0, 100, 100], 'panel_index': 0}]
        self.assertEqual(panel_mapping(sources, page)[0], 1)
        page['panels'][0][2] = 10
        self.assertEqual(panel_mapping(sources, page)[0], 0)

    def test_event_splits_before_freeze_at_eight(self):
        rows = [{'id': 't'+str(i)} for i in range(17)]
        ledger = {'rows': {'u'+str(i): r['id'] for i, r in enumerate(rows)}}
        value = {'scenes': [{'start_id': 'u0', 'title': 'Một tên scene rất dài', 'summary': 'Scene',
                  'confidence': 8, 'events': [{'start_id': 'u0', 'title': 'Lời giải thích', 'summary': 'Event', 'confidence': 8}]}]}
        scenes, _ = hierarchy(value, rows, ledger)
        self.assertEqual([len(e['target_ids']) for e in scenes[0]['events']], [8, 8, 1])
        self.assertLessEqual(len(scenes[0]['title'].split()), 4)
        self.assertEqual(scenes[0]['events'][1]['continuation_of'], 'scene:1:event:1')

    def test_silent_middle_page_and_caption_outside_panel(self):
        pages = [{'id': 'p'+str(i), 'page': i, 'width': 200, 'height': 200,
                  'panels': [[0, 0, 100, 100], [100, 0, 200, 100]], 'bubble_groups': []} for i in range(1, 4)]
        pages[0]['bubble_groups'] = [{'panel_indices': [1], 'bbox': [90, 0, 200, 150]}]
        rows = [{'id': 'a', 'page_id': 'p1', 'panel_index': 1, 'panel_indices': [1], 'bbox': [100, 120, 180, 170]},
                {'id': 'b', 'page_id': 'p3', 'panel_index': 0, 'panel_indices': [0], 'bbox': [0, 0, 50, 50]}]
        packets = page_regions(rows, rows, pages)
        self.assertEqual(len(packets), 3)
        self.assertEqual(packets[1]['panel_indices'], [0, 1])
        self.assertEqual(packets[0]['bbox'], [90, 0, 200, 170])

    def test_scene_memory_and_single_attempt_cached_resume(self):
        store, provider, result = self.analysis_fixture()
        calls = [d for task, d in provider.requests if task == 'analyze_event_v7']
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[1]['previous_event']['event_id'], calls[0]['event_id'])
        self.assertEqual(calls[1]['scene_memory']['description'], 'Đã đọc '+calls[0]['event_id'])
        self.assertEqual(result['scenes'][0]['hypotheses'], [])
        before = len(provider.requests)
        resumed = analyze(store, provider)
        self.assertEqual(len(provider.requests), before)
        self.assertEqual(resumed['utterances'], result['utterances'])
        artifacts = {stage: store.read(stage) for stage in ('normalize', 'extract', 'graph', 'analyze')}
        verify(artifacts, store.manifest['config'], read_json(store.directory/'snapshots/persons-v6.json'),
               read_json(store.manifest['bank_path']), read_json(store.artifact_path('analyze').parent/'plan.json'))

    def test_failed_event_continues_with_last_valid_memory(self):
        store, provider, result = self.analysis_fixture(Provider(fail_event='scene:1:event:1'))
        calls = [d for task, d in provider.requests if task == 'analyze_event_v7']
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[1]['incomplete_events'], ['scene:1:event:1'])
        self.assertIsNone(calls[1]['previous_event'])
        self.assertEqual(calls[1]['scene_memory']['description'], '')
        self.assertEqual(len(result['unresolved_targets']), 4)
        self.assertEqual(result['failures'], [])

    def test_legacy_character_hypotheses_never_enter_analyze(self):
        store, provider = self.fixture(), Provider()
        graph = graph_build(store, provider)
        graph['scenes'][0]['hypotheses'] = [{'label': 'Wrong person', 'confidence': 10,
                                           'possible_person_ids': ['wrong']}]
        store.finish('graph', graph)
        result = analyze(store, provider)
        for task, packet in provider.requests:
            if task == 'analyze_event_v7':
                self.assertNotIn('hypotheses', packet['chapter_context']['scenes'][0])
        self.assertEqual(result['scenes'][0]['hypotheses'], [])
        self.assertEqual(store.read('graph')['scenes'][0]['hypotheses'], graph['scenes'][0]['hypotheses'])

    def test_tail_links_come_only_from_magi_including_grouped_sources(self):
        pages = [{'id': 'p', 'tails': [[10, 20, 30, 40], [50, 60, 70, 80]],
                  'text_tail_associations': [[0, 1], [1, 1]]}]
        rows = [{'id': 'group', 'page_id': 'p', 'source_boxes': [
                    {'id': 't0', 'text_index': 0}, {'id': 't1', 'text_index': 1}]},
                {'id': 'none', 'page_id': 'p', 'source_boxes': [
                    {'id': 't2', 'text_index': 2, 'tail_index': 0}]}]
        actual = magi_tail_evidence(rows, pages)
        self.assertTrue(actual['group']['has_tail'])
        self.assertEqual(actual['group']['tails'], [{'tail_index': 1, 'label': 'TAIL 1',
                         'bbox': [50, 60, 70, 80], 'source_text_ids': ['t0', 't1']}])
        # Neither a nearby detected tail nor an unlinked tail_index invents a link.
        self.assertFalse(actual['none']['has_tail'])
        self.assertEqual(actual['none']['tails'], [])

    def test_tail_bboxes_are_drawn_before_analyze_and_invalidate_image_cache(self):
        store = self.fixture()
        geometry = copy.deepcopy(store.read('bubble_geometry'))
        page = geometry['pages'][0]
        page['tails'] = [[100, 180, 120, 200]]
        page['text_tail_associations'] = [[0, 0]]
        rows = store.read('normalize')['utterances']
        event = {'event_id': 'tail-test'}
        before = render(store, event, rows[:1], rows, store.read('bubble_geometry'))
        after = render(store, event, rows[:1], rows, geometry)
        self.assertNotEqual(before[0]['file'], after[0]['file'])
        self.assertEqual(after[0]['tails'][0]['bbox'], [100, 180, 120, 200])
        box = after[0]['tails'][0]['render_bbox']
        with Image.open(store.directory/after[0]['file']) as image:
            red, green, blue = image.getpixel((round(box[0])+1, round((box[1]+box[3])/2)))
        self.assertGreater(red, green+50)
        self.assertGreater(blue, green+50)

    def test_tail_outside_panel_is_retained_without_linking_another_bubble(self):
        page = {'id': 'p', 'width': 300, 'height': 200, 'panels': [[0, 0, 100, 100]],
                'tails': [[90, 90, 130, 140]], 'text_tail_associations': [[0, 0]]}
        row = {'id': 'u', 'page_id': 'p', 'panel_index': 0, 'bbox': [10, 10, 40, 40],
               'source_boxes': [{'id': 't', 'text_index': 0, 'bbox': [10, 10, 40, 40]}]}
        packet = page_regions([row], [row], [page])[0]
        self.assertEqual(packet['bbox'], [0, 0, 130, 140])
        self.assertEqual(packet['tails'][0]['tail_index'], 0)

    def test_analyze_receives_magi_tail_metadata_without_requesting_detection(self):
        store, provider = self.fixture(), Provider()
        raw = copy.deepcopy(store.read('extract'))
        raw['pages'][0]['tails'] = [[100, 180, 120, 200]]
        raw['pages'][0]['text_tail_associations'] = [[0, 0]]
        raw['texts'][0]['tail_index'] = 0
        for stage in ('extract', 'bubble_geometry'):
            store.finish(stage, raw)
        store.finish('normalize', normalize(raw))
        store.finish('graph', graph_build(store, provider))
        result = analyze(store, provider)
        calls = [p for task, p in provider.requests if task == 'analyze_event_v7']
        self.assertTrue(calls[0]['targets'][0]['bubble_tail']['has_tail'])
        self.assertFalse(calls[0]['targets'][1]['bubble_tail']['has_tail'])
        self.assertEqual(result['utterances'][0]['bubble_tail'], calls[0]['targets'][0]['bubble_tail'])
        self.assertEqual(calls[0]['images'][0]['tails'][0]['bbox'], [100, 180, 120, 200])
        from manga_pipeline.v6.event_analysis import schema
        state = result['person_state']
        wire = schema(state, {}, ['u'])
        self.assertNotIn('bubble_tail', wire['properties']['utterances']['items']['properties'])

    def test_reanalyze_failure_is_durable_analyze_fallback(self):
        store, provider, analyzed = self.analysis_fixture(Provider(fail_review=True))
        result = reanalyze_build(store, provider)
        self.assertTrue(result['fallback_to_analyze'])
        self.assertEqual(result['analysis'], analyzed)
        store.finish('reanalyze', result)
        self.assertEqual(store.analysis(), analyzed)
        reanalyze_build(store, provider)
        self.assertEqual(sum(task == 'reanalyze_chapter_v7' for task, _ in provider.requests), 2)

    def test_new_images_checked_on_checkpoint_read(self):
        store, _, result = self.analysis_fixture()
        path = store.directory/result['reference_images'][0]['file']
        path.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'event image changed'):
            RunStore(store.directory).read('analyze')

    def test_lower_confidence_corrections_apply_without_replaying_later_events(self):
        before = {'utterance_id': 'u', 'content_type': 'thought', 'speech_style': 'thought', 'speaker_kind': 'person',
            'speaker_person_id': 'a', 'speaker_cluster_ref': 'ca', 'addressee_type': 'self', 'addressee_person_ids': ['a'],
            'mentions': [], 'confidence': {k: 9 for k in ('speaker', 'addressee', 'content_type', 'speech_style')},
            'evidence': 'Old', 'evidence_refs': ['u'], 'event_id': 'e1', 'scene_id': 's1'}
        state = {'persons': {'a': {}, 'b': {}}, 'cluster_to_person': {'ca': 'a', 'cb': 'b'}}
        scene = {'scene_id': 's1', 'events': [{'event_id': 'e1', 'target_ids': ['u'], 'description': 'old',
                 'description_confidence': 9}, {'event_id': 'e2', 'target_ids': [], 'memory_input': {'fact': 'old'}}]}
        analyzed = {'utterances': [before], 'scenes': [scene]}
        after = copy.deepcopy(before)
        after.update(speaker_person_id='b', speaker_cluster_ref='cb')
        after['inferred_speaker'] = {k: before[k] for k in ('speaker_person_id', 'speaker_cluster_ref', 'speaker_kind')}
        after['confidence']['speaker'] = 8
        response = {'corrections': [{'replacement': after, 'reason': 'Correction'}], 'event_updates': []}
        source = {'id': 'u', 'speaker_anchor': {'person_id': 'b'}}
        updated, accepted, rejected = apply_corrections(analyzed, response, {'u': source}, state, {})
        self.assertFalse(rejected)
        self.assertEqual(updated['utterances'][0]['speaker_person_id'], 'b')
        self.assertEqual(updated['utterances'][0]['confidence']['speaker'], 8)
        from manga_pipeline.v6.merge import resolve_speaker
        self.assertEqual(resolve_speaker(updated['utterances'][0], state)['speaker_person_id'], 'b')
        self.assertEqual(updated['scenes'][0]['events'][1], analyzed['scenes'][0]['events'][1])

    def test_reanalyze_preserves_magi_tails_and_accepts_lower_event_score(self):
        store, _, analyzed = self.analysis_fixture()
        before = analyzed['utterances'][0]
        row = copy.deepcopy(before)
        row['confidence']['speaker'] = 6
        row['bubble_tail'] = {'has_tail': True, 'tails': [{'bbox': [0, 0, 1, 1]}]}
        event = analyzed['scenes'][0]['events'][0]
        response = {'corrections': [{'replacement': row, 'reason': 'Reassess evidence honestly.'}],
                    'event_updates': [{'event_id': event['event_id'], 'title': 'Reviewed', 'description': 'Updated.',
                                      'confidence': 6, 'requires': [row['utterance_id']], 'reason': 'Corrected evidence.'}]}
        state = analyzed['person_state']
        candidates = {c['id']: c for c in store.read('graph')['candidates']}
        source = {'id': row['utterance_id']}
        result, accepted, rejected = apply_corrections(analyzed, response, {source['id']: source}, state, candidates)
        self.assertFalse(rejected)
        self.assertEqual(len(accepted), 2)
        self.assertEqual(result['utterances'][0]['bubble_tail'], before['bubble_tail'])
        self.assertEqual(result['utterances'][0]['confidence']['speaker'], 6)
        self.assertEqual(result['scenes'][0]['events'][0]['description_confidence'], 6)

    def test_reference_only_people_cannot_be_speakers(self):
        source = {'id': 'u'}
        row = {'utterance_id': 'u', 'content_type': 'thought', 'speech_style': 'thought', 'speaker_kind': 'person',
            'speaker_person_id': 'named', 'speaker_cluster_ref': None, 'addressee_type': 'self',
            'addressee_person_ids': [], 'mentions': [], 'confidence': {k: 7 for k in
                ('speaker', 'addressee', 'content_type', 'speech_style')}, 'evidence': 'x', 'evidence_refs': []}
        with self.assertRaisesRegex(ValueError, 'visual person'):
            validate_row(row, source, {'persons': {'named': {'story_reference_only': True}}, 'cluster_to_person': {}}, {})

    def test_configs_cli_and_no_thinking_all_stages(self):
        from manga_pipeline.runtime.stage_policy import request_policy
        config = load_config()
        for task in ('graph_chapter_v7', 'analyze_event_v7', 'reanalyze_chapter_v7', 'character_profile_v2', 'translate_vi_natural'):
            policy = request_policy(config, task)
            self.assertFalse(policy['enable_thinking'])
            self.assertEqual(policy['num_ctx'], 65536)
        self.assertEqual(request_policy(config, 'graph_chapter_v7')['timeout'], 1200)
        self.assertEqual(request_policy(config, 'translate_vi_natural')['timeout'], 600)
        config['llm']['graph_timeout'] = 1500
        self.assertEqual(request_policy(config, 'graph_chapter_v7')['timeout'], 1500)
        self.assertEqual(request_policy(config, 'analyze_event_v7')['timeout'], 1200)
        for command in ('graph', 'analyze', 'reanalyze', 'revise'):
            self.assertEqual(build_parser().parse_args([command, '--run-dir', 'example']).command, command)

    def test_scenes_run_concurrently_without_sharing_live_memory(self):
        class ParallelProvider(Provider):
            def __init__(self):
                super().__init__()
                self.barrier = threading.Barrier(2)

            def infer(self, task, prompt, data, schema, validator, images=None, **kwargs):
                if task == 'analyze_event_v7' and data['event_id'].endswith(':event:1'):
                    self.barrier.wait(timeout=2)
                value = super().infer(task, prompt, data, schema, validator, images, **kwargs)
                if task == 'graph_chapter_v7':
                    original = value['scenes'][0]
                    value['scenes'] = []
                    for start in (0, 4):
                        entry = copy.deepcopy(original)
                        entry['start_id'] = 'u'+str(start)
                        entry['events'][0]['start_id'] = 'u'+str(start)
                        entry['events'][1]['start_id'] = 'u'+str(start+2)
                        value['scenes'].append(entry)
                return value
        store, provider, result = self.analysis_fixture(ParallelProvider())
        self.assertEqual(result['event_failures'], [])
        calls = [p for task, p in provider.requests if task == 'analyze_event_v7']
        for packet in calls:
            if packet['previous_event']:
                self.assertTrue(packet['previous_event']['event_id'].startswith(packet['scene_id']+':'))
            else:
                self.assertEqual(packet['scene_memory']['description'], '')

    def test_reanalyze_effective_snapshot_and_offline_html(self):
        store, provider, analyzed = self.analysis_fixture()
        old_infer = provider.infer
        def infer(task, prompt, data, schema, validator, images=None, **kwargs):
            if task != 'reanalyze_chapter_v7':
                return old_infer(task, prompt, data, schema, validator, images, **kwargs)
            self.assertNotIn('images', data)
            index = 0 if data['scope']['batch_id'] == 'half:1' else 4
            row = copy.deepcopy(analyzed['utterances'][index])
            row['speech_style'] = 'normal'
            row['confidence']['speech_style'] = 9
            value = {'corrections': [{'replacement': row, 'reason': 'Style correction'}], 'event_updates': []}
            validator(value)
            return value
        provider.infer = infer
        reviewed = reanalyze_build(store, provider)
        self.assertFalse(reviewed['fallback_to_analyze'])
        store.finish('reanalyze', reviewed)
        self.assertEqual(store.analysis()['utterances'][0]['speech_style'], 'normal')
        self.assertEqual(store.read('analyze')['utterances'][0]['speech_style'], 'thought')
        from manga_pipeline.v6.stages import write_review
        for stage in ('graph', 'analyze', 'reanalyze'):
            write_review(store, stage)
            page = store.artifact_path(stage).parent/'review.html'
            self.assertTrue(page.is_file())
            html = page.read_text(encoding='utf-8')
            embedded = html.split('<script type="application/json" id="data">', 1)[1].split('</script>', 1)[0]
            review_data = json.loads(embedded)
            self.assertEqual(review_data['stage'], stage)
            if stage != 'graph':
                self.assertFalse(review_data['scenes'][0]['events'][0]['rows'][0]['semantic']['bubble_tail']['has_tail'])

    def test_pipeline_dispatch_reaches_export_with_effective_analysis(self):
        from manga_pipeline.pipeline import Pipeline
        store = self.fixture()
        provider = Provider(fail_review=True)
        self.assertTrue(Pipeline(store, provider=provider).run('export'))
        current = RunStore(store.directory)
        exported = current.read('export')
        self.assertEqual(exported['semantic_source'], 'analyze')
        self.assertEqual(exported['reanalyze_status'], 'failed')
        self.assertEqual(len(exported['utterances']), 8)
        self.assertIn('story_graph', exported)
        self.assertEqual([s for s in current.steps if current.completed(s)], list(current.steps[:current.steps.index('export')+1]))

    def test_review_halves_run_independently_and_merge_lower_score_changes(self):
        store, provider, analyzed = self.analysis_fixture()
        original = copy.deepcopy(analyzed)
        barrier = threading.Barrier(2)
        old_infer = provider.infer
        def infer(task, prompt, data, schema, validator, images=None, **kwargs):
            if task != 'reanalyze_chapter_v7':
                return old_infer(task, prompt, data, schema, validator, images, **kwargs)
            with provider.lock:
                provider.requests.append((task, copy.deepcopy(data)))
            barrier.wait(timeout=3)
            index = 0 if data['scope']['batch_id'] == 'half:1' else 4
            row = copy.deepcopy(analyzed['utterances'][index])
            row['speech_style'] = 'normal'
            row['confidence']['speech_style'] = 6
            return {'corrections': [{'replacement': row, 'reason': 'Independent evidence review.'}], 'event_updates': []}
        provider.infer = infer
        reviewed = reanalyze_build(store, provider)
        self.assertFalse(reviewed['fallback_to_analyze'])
        self.assertEqual(reviewed['rejected'], [])
        self.assertEqual(len(reviewed['accepted']), 2)
        self.assertEqual(analyzed, original)
        for index in (0, 4):
            self.assertEqual(reviewed['analysis']['utterances'][index]['speech_style'], 'normal')
            self.assertEqual(reviewed['analysis']['utterances'][index]['confidence']['speech_style'], 6)
        before = len(provider.requests)
        resumed = reanalyze_build(store, provider)
        self.assertEqual(len(provider.requests), before)
        self.assertEqual(resumed['analysis'], reviewed['analysis'])

    def test_failed_review_half_keeps_other_half_corrections(self):
        store, provider, analyzed = self.analysis_fixture()
        old_infer = provider.infer
        def infer(task, prompt, data, schema, validator, images=None, **kwargs):
            if task != 'reanalyze_chapter_v7':
                return old_infer(task, prompt, data, schema, validator, images, **kwargs)
            if data['scope']['batch_id'] == 'half:2':
                raise ValueError('Second half fixture failed')
            row = copy.deepcopy(analyzed['utterances'][0])
            row['speech_style'] = 'normal'
            row['confidence']['speech_style'] = 6
            return {'corrections': [{'replacement': row, 'reason': 'Correct owned half.'}], 'event_updates': []}
        provider.infer = infer
        reviewed = reanalyze_build(store, provider)
        self.assertFalse(reviewed['fallback_to_analyze'])
        self.assertEqual(reviewed['status'], 'completed_with_review')
        self.assertEqual(reviewed['batch_failures'][0]['batch_id'], 'half:2')
        self.assertEqual(reviewed['analysis']['utterances'][0]['speech_style'], 'normal')
        self.assertEqual(reviewed['analysis']['utterances'][4]['speech_style'], analyzed['utterances'][4]['speech_style'])

    def test_review_scope_blocks_other_half_and_preserves_owned_evidence(self):
        from manga_pipeline.v6.reanalysis_input import packet, partition
        store, _, analyzed = self.analysis_fixture()
        scopes = partition(analyzed)
        self.assertEqual([len(s['target_ids']) for s in scopes], [4, 4])
        self.assertTrue(set(scopes[0]['event_ids']).isdisjoint(scopes[1]['event_ids']))
        candidates = {c['id']: c for c in store.read('graph')['candidates']}
        request = packet(analyzed, store.read('normalize'), candidates, analyzed['person_state'], scopes[0])
        self.assertEqual([r['utterance_id'] for r in request['decisions']], scopes[0]['target_ids'])
        self.assertEqual(request['decisions'][0]['evidence'], analyzed['utterances'][0]['evidence'])
        self.assertNotIn('bbox', request['decisions'][0]['bubble_tail'])
        row = copy.deepcopy(analyzed['utterances'][4])
        event = analyzed['scenes'][0]['events'][1]
        response = {'corrections': [{'replacement': row, 'reason': 'Out of scope.'}], 'event_updates': [
            {'event_id': event['event_id'], 'title': 'Wrong half', 'description': 'Out of scope.',
             'confidence': 9, 'requires': [], 'reason': 'Out of scope.'}]}
        effective, accepted, rejected = apply_corrections(analyzed, response, {}, analyzed['person_state'], candidates, scopes[0])
        self.assertEqual(accepted, [])
        self.assertEqual(len(rejected), 2)
        self.assertEqual(effective, analyzed)


if __name__ == '__main__':
    unittest.main()
