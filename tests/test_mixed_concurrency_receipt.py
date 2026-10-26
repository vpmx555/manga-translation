"""Offline qualification contract tests; fixtures are not operational receipts."""
import copy
from contextlib import nullcontext
import io
import hashlib
import json
import sys
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'deployment/runpod'))
import manage
import vllm_runtime
import validate_vllm_concurrency as gate
from manga_pipeline.pipeline import load_config


class MixedTests(unittest.TestCase):
    def test_server_path_exposes_locked_jit_tools_without_losing_system_path(self):
        import os
        with patch.object(vllm_runtime.manage, 'environment', return_value={'PATH': '/system/bin'}), \
                patch.object(vllm_runtime, 'python', return_value=Path('/locked/vllm/bin/python')):
            env = vllm_runtime.server_environment()
        self.assertEqual(env['PATH'].split(os.pathsep)[0], str(Path('/locked/vllm/bin')))
        self.assertTrue(env['PATH'].endswith('/system/bin'))
        self.assertEqual(env['HF_HUB_OFFLINE'], '1')

    def fixture(self, slots=32, thinking=False):
        cfg = load_config()
        if not thinking:
            cfg['llm'].update(analyze_thinking=False, analyze_num_ctx=32768,
                              analyze_timeout=600, analyze_num_predict=8192)
        cfg['runtime']['llm_slots'] = slots
        cfg['vllm']['max_num_seqs'] = slots
        def phase(slots, short):
            c = copy.deepcopy(cfg)
            c['runtime']['llm_slots'] = slots
            count = 1024 if short else 16000
            return {'status': 'passed', 'slots': slots, 'probe_policy': gate.phase_policy(c, short=short),
                'sample_count': gate.sample_count(c, short=short), 'signature': vllm_runtime.signature(c),
                'config_fingerprint': manage.config_fingerprint(c), 'engine_profile_fingerprint': gate.engine_profile(c),
                'cpu_offload_gb': 0, 'input_tokens_each': count, 'peak_running_requests': slots,
                'enable_thinking': thinking and not short,
                'temperature': c['llm']['analyze_temperature'] if thinking and not short else c['llm']['temperature'],
                'presence_penalty': c['llm']['analyze_presence_penalty'] if thinking and not short else c['llm']['presence_penalty'],
                'num_ctx': c['llm']['num_ctx'] if short else c['llm']['analyze_num_ctx'],
                'output_reserve': 8192 if short else c['llm']['analyze_num_predict'],
                'server_identity': {'fixture': 'not-real'},
                'responses': [{'usage': {'prompt_tokens': count, 'completion_tokens': 4096}} for _ in range(slots)]}
        result = {'status': 'passed', 'slots': slots, 'probe_policy': gate.mixed_policy(cfg),
            'signature': vllm_runtime.signature(cfg), 'engine_profile_fingerprint': gate.engine_profile(cfg),
            'cpu_offload_gb': 0, 'long_context': phase(8, False), 'short_context': phase(slots, True)}
        return cfg, result

    def test_stage_thinking_receipt_requires_new_context_and_output_proof(self):
        cfg, result = self.fixture(64, thinking=True)
        gate.validate_receipt(cfg, result)
        for phase, field, value in [('long_context', 'enable_thinking', False),
                                    ('long_context', 'num_ctx', 32768),
                                    ('long_context', 'output_reserve', 8192),
                                    ('short_context', 'enable_thinking', True),
                                    ('long_context', 'temperature', 0),
                                    ('long_context', 'presence_penalty', 0)]:
            wrong = copy.deepcopy(result)
            wrong[phase][field] = value
            with self.assertRaises(RuntimeError):
                gate.validate_receipt(cfg, wrong)

    def test_both_real_usage_profiles_are_required(self):
        cfg, result = self.fixture()
        gate.validate_receipt(cfg, result)
        for key in ('long_context','short_context'):
            for change in ({'responses': []}, {'input_tokens_each': 1}, {'peak_running_requests': 1}, {'status': 'failed'}):
                wrong = copy.deepcopy(result)
                wrong[key].update(change)
                with self.assertRaises(RuntimeError):
                    gate.validate_receipt(cfg, wrong)

    def test_same_server_and_profile_are_required(self):
        cfg, result = self.fixture()
        wrong = copy.deepcopy(result)
        wrong['short_context']['server_identity'] = {'fixture': 'different'}
        with self.assertRaises(RuntimeError):
            gate.validate_receipt(cfg, wrong)
        with self.assertRaises(RuntimeError):
            gate.validate_receipt(cfg, result['short_context'])

    def test_64_ceiling_requires_64_observed_requests_and_real_decode(self):
        cfg, result = self.fixture(64)
        gate.validate_receipt(cfg, result)
        _, _, schema = gate.probe_contract(cfg, short=True)
        self.assertEqual(schema['properties']['samples']['minItems'], 512)
        self.assertEqual(result['long_context']['sample_count'], 512)
        for change in ({'peak_running_requests': 32}, {'slots': 32}, {'sample_count': 2048}):
            wrong = copy.deepcopy(result)
            wrong['short_context'].update(change)
            with self.assertRaises(RuntimeError):
                gate.validate_receipt(cfg, wrong)
        result['short_context']['responses'][0]['usage']['completion_tokens'] = 1023
        with self.assertRaises(RuntimeError):
            gate.validate_receipt(cfg, result)

    def test_tokenizer_count_never_changes_requested_or_validated_array_length(self):
        cfg, _ = self.fixture(thinking=True)
        input_tokens = cfg['llm']['analyze_num_ctx'] // 2
        truncated = [False]
        cfg['runtime']['llm_slots'] = 1
        record = {'max_num_seqs': cfg['vllm']['max_num_seqs'], 'cpu_offload_gb': 0,
                  'max_model_len': max(cfg['llm']['num_ctx'], cfg['llm']['analyze_num_ctx']),
                  'identity': {'fixture': 'not-real'}}
        seen = []

        def http(request, timeout):
            route = request.full_url.rsplit('/', 1)[-1]
            if route == 'metrics':
                return io.BytesIO(b'vllm:num_requests_running 1\n')
            if route == 'tokenize':
                return io.BytesIO(json.dumps({'count': input_tokens}).encode())
            wire = json.loads(request.data)
            seen.append(wire)
            # Keep the in-memory fixture alive for one metrics sample.
            time.sleep(.03)
            response = {'choices': [{'message': {'content': json.dumps({'ok': True,
                'samples': [0] * gate.SAMPLE_COUNT})}, 'finish_reason': 'stop'}],
                'usage': {'prompt_tokens': input_tokens, 'completion_tokens': 1545}}
            if truncated[0]:
                response['choices'][0].update(message={'content': None}, finish_reason='length')
            return io.BytesIO(json.dumps(response).encode())

        with tempfile.TemporaryDirectory() as directory:
            receipt = Path(directory) / 'fixture.json'
            with patch.object(gate, 'urlopen', side_effect=http), \
                    patch.object(gate.manage, 'read_json', return_value=record), \
                    patch.object(gate.manage, 'lock', return_value=nullcontext()), \
                    patch.object(vllm_runtime, 'owns', return_value=True), \
                    patch.object(gate.subprocess, 'check_output', return_value='12345'):
                gate.verify(cfg, receipt)
                truncated[0] = True
                with self.assertRaisesRegex(RuntimeError, 'finish_reason=length'):
                    gate.verify(cfg, Path(directory) / 'truncated.json')
            result = json.loads(receipt.read_text())
            self.assertEqual(result['status'], 'passed')
            self.assertEqual(result['sample_count'], gate.SAMPLE_COUNT)
            self.assertEqual(result['input_tokens_each'], input_tokens)
            self.assertTrue(receipt.with_name('fixture-response-00.json').is_file())
        self.assertIn(f'exactly {gate.SAMPLE_COUNT} zero integers', seen[0]['messages'][0]['content'])
        self.assertIn(f'exactly {gate.SAMPLE_COUNT} zero integers', seen[0]['messages'][1]['content'])
        self.assertIn('Do not count, enumerate, simulate, or verify', seen[0]['messages'][0]['content'])
        self.assertTrue(seen[0]['chat_template_kwargs']['enable_thinking'])
        self.assertEqual(seen[0]['temperature'], 1.0)

    def test_verified_mixed_proof_reuse_allows_only_the_same_engine_profile(self):
        from preanalyze_import import prepare_qualification
        cfg, proof = self.fixture(64)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            previous, requested, backup = root/'previous', root/'requested', root/'backup'
            previous.mkdir(); requested.mkdir(); backup.mkdir()
            def write(path, value):
                path.write_text(json.dumps(value), encoding='utf-8')
            def archive(relative, name, value):
                path = backup/relative/'fixture.zip'
                path.parent.mkdir(parents=True)
                data = json.dumps(value).encode()
                item = {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
                with zipfile.ZipFile(path, 'w') as zipped:
                    zipped.writestr(name, data)
                    zipped.writestr('__transfer_inventory__.json', json.dumps({'format': 1, 'files': {name: item}}))
                return {'archive': str(path), 'sha256': manage.file_hash(path)}
            # Worker executable paths differ from the control config, without
            # changing the independently qualified LLM/GPU profile.
            effective = copy.deepcopy(cfg)
            effective['ocr']['python'] = '/fixture/old-ocr/bin/python'
            archives = [archive('published/audit', 'concurrency-probe.json', proof),
                        archive('published/outputs/iris-zero/chapter-001', 'run/manifest.json', {'config': effective})]
            write(backup/'verification.json', {'status': 'replica_archives_verified', 'batch': 'fixture-old', 'archives': archives})
            source = previous/'bound-plan.json'
            write(source, {'batch': 'fixture-old', 'pod_id': 'fixture-not-a-real-pod'})
            write(previous/'native-sync.json', {'destination': str(backup)})
            write(requested/'pipeline-v6.json', cfg)
            plan = {'ingress': {}}
            prepare_qualification(source, requested, plan)
            gate.validate_receipt(cfg, json.loads((requested/'qualification.json').read_text()))
            self.assertEqual(plan['qualification']['pod_id'], 'fixture-not-a-real-pod')
            changed = copy.deepcopy(cfg)
            changed['vllm']['max_num_seqs'] = 32
            write(requested/'pipeline-v6.json', changed)
            with self.assertRaisesRegex(ValueError, 'profile changed'):
                prepare_qualification(source, requested, {'ingress': {}})


if __name__ == '__main__':
    unittest.main()
