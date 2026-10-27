"""Bounded real concurrent-prefill gate before a parallel scene batch."""
import argparse
import copy
from concurrent.futures import ThreadPoolExecutor
import json
import math
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
from urllib.request import Request, urlopen

import manage
sys.path.insert(0, str(manage.PROJECT / 'src'))
from manga_pipeline.runtime.stage_policy import request_policy

PROBE_POLICY = 'long-prefill-bounded-decode-v3'
SHORT_POLICY = 'short-prefill-bounded-decode-v3'
MIXED_POLICY = 'stage-bounded-long8-short-ceiling-v3'
SAMPLE_COUNT = 512


def sample_count(config, *, short=False):
    # Keep aggregate KV bounded; every request must still prove >=1024 actual
    # decode tokens and the full simultaneous-running ceiling below.
    return SAMPLE_COUNT


def phase_policy(config, *, short=False):
    base = SHORT_POLICY if short else PROBE_POLICY
    return base + '-story-graph-v7'


def mixed_policy(config):
    return MIXED_POLICY + '-story-graph-v7'


def engine_profile(config):
    return manage.config_fingerprint({k: config.get(k) for k in ('llm', 'vllm', 'runtime', 'device')})


def probe_contract(config, *, short=False):
    """Keep decoding alive while other long prompts enter chunked prefill."""
    context = config['llm']['num_ctx'] if short else config['llm'].get('analyze_num_ctx', config['llm']['num_ctx'])
    reserve = 8192 if short else config['llm']['analyze_num_predict']
    ceiling = math.floor(context * config['runtime']['context_fraction'])
    target = min(context // 2, ceiling - reserve - 640)
    if short:
        target = 1024
    if (not short and target < 16000) or reserve < 8192:
        raise ValueError('Probe requires long input plus the real analysis output reserve')
    samples = sample_count(config, short=short)
    schema = {'type': 'object', 'properties': {
        'ok': {'type': 'boolean', 'enum': [True]},
        'samples': {'type': 'array', 'minItems': samples, 'maxItems': samples,
                    'items': {'type': 'integer', 'enum': [0]}}},
        'required': ['ok', 'samples'], 'additionalProperties': False}
    return target, reserve, schema


def validate_receipt(config, receipt, *, short=False):
    import vllm_runtime
    slots = config['runtime']['llm_slots']
    if slots > 8 and not short:
        long_config = copy.deepcopy(config)
        long_config['runtime']['llm_slots'] = 8
        if (receipt.get('status') != 'passed' or receipt.get('probe_policy') != mixed_policy(config)
                or receipt.get('slots') != slots or receipt.get('signature') != vllm_runtime.signature(config)
                or receipt.get('engine_profile_fingerprint') != engine_profile(config)
                or receipt.get('cpu_offload_gb') != 0):
            raise RuntimeError('Missing mixed long-context/admission-ceiling qualification')
        validate_receipt(long_config, receipt.get('long_context', {}))
        validate_receipt(config, receipt.get('short_context', {}), short=True)
        if (not receipt['long_context'].get('server_identity')
                or receipt['long_context'].get('server_identity') != receipt['short_context'].get('server_identity')):
            raise RuntimeError('Qualification phases used different servers')
        return
    responses = receipt.get('responses', [])
    expected_context = config['llm']['num_ctx'] if short else config['llm']['analyze_num_ctx']
    sampler = request_policy(config, 'qualification_short' if short else 'analyze_qualification')
    if (receipt.get('enable_thinking') is not False or receipt.get('num_ctx') != expected_context
            or receipt.get('output_reserve') != (8192 if short else config['llm']['analyze_num_predict'])
            or receipt.get('temperature') != sampler['temperature']
            or receipt.get('presence_penalty') != sampler['presence_penalty']):
        raise RuntimeError('Qualification does not prove the no-thinking/context/output profile')
    if (receipt.get('status') != 'passed' or receipt.get('slots') != slots
            or receipt.get('probe_policy') != phase_policy(config, short=short)
            or receipt.get('sample_count') != sample_count(config, short=short)
            or receipt.get('signature') != vllm_runtime.signature(config)
            or (receipt.get('config_fingerprint') != manage.config_fingerprint(config)
                and receipt.get('engine_profile_fingerprint') != engine_profile(config))
            or receipt.get('cpu_offload_gb') != 0
            or receipt.get('input_tokens_each', 0) < (1024 if short else 16000)
            or receipt.get('peak_running_requests', 0) < slots or len(responses) != slots
            or any(r.get('usage', {}).get('prompt_tokens', 0) < receipt.get('input_tokens_each', 1)
                   or r.get('usage', {}).get('completion_tokens', 0) < 1024 for r in responses)):
        raise RuntimeError('Missing or incompatible real concurrency qualification receipt')


def verify(config, receipt_path, *, short=False):
    slots = config['runtime']['llm_slots']
    if config['llm'].get('backend') != 'vllm' or not 1 <= slots <= 64:
        raise ValueError('This bounded probe requires 1..64 configured vLLM slots')
    if slots > 8 and not short:
        long_config = copy.deepcopy(config)
        long_config['runtime']['llm_slots'] = 8
        long_path = receipt_path.with_name(receipt_path.stem+'-long8.json')
        short_path = receipt_path.with_name(receipt_path.stem+'-short-ceiling.json')
        verify(long_config, long_path)
        verify(config, short_path, short=True)
        import vllm_runtime
        result = {'status': 'passed', 'probe_policy': mixed_policy(config), 'slots': slots,
                  'signature': vllm_runtime.signature(config), 'cpu_offload_gb': 0,
                  'engine_profile_fingerprint': engine_profile(config),
                  'config_fingerprint': manage.config_fingerprint(config),
                  'long_context': manage.read_json(long_path), 'short_context': manage.read_json(short_path)}
        validate_receipt(config, result)
        manage.atomic_json(receipt_path, result)
        return
    record = manage.read_json(manage.RUNTIME / 'config/vllm-service.json', {})
    if (record.get('max_num_seqs') != config['vllm']['max_num_seqs'] or record.get('cpu_offload_gb') != 0
            or record.get('max_model_len') != max(config['llm']['num_ctx'], config['llm'].get('analyze_num_ctx', config['llm']['num_ctx']))):
        raise RuntimeError('Concurrent server profile is not verified')
    import vllm_runtime
    if not vllm_runtime.owns(record):
        raise RuntimeError('Concurrent probe server identity does not match')
    host = config['llm']['host'].rstrip('/')
    model = config['llm']['model']

    def http(route, payload=None, raw=False):
        request = Request(host + route,
                          data=json.dumps(payload).encode() if payload is not None else None,
                          headers={'Content-Type': 'application/json'})
        with urlopen(request, timeout=(config['llm'].get('analyze_timeout', 600) if not short else 600)
                     if payload is not None else 10) as response:
            body = response.read()
            return body.decode() if raw else json.loads(body)

    samples = sample_count(config, short=short)

    def messages(repeats):
        instruction = f'Return only JSON with ok=true and samples containing exactly {samples} zero integers. Write every array item; do not abbreviate.'
        if not short:
            instruction += (' This is a mechanical serving probe, not an arithmetic task. '
                            'The schema-constrained decoder enforces the array size automatically. '
                            'Keep reasoning brief. Do not count, enumerate, simulate, or verify '
                            'array items in your reasoning. Generate the array only in the final JSON.')
        return [{'role': 'system', 'content': instruction},
                {'role': 'user', 'content': 'Ignore this reference filler: '
                 + 'background ' * repeats + '\n' + instruction}]

    # Exercise configured simultaneous contexts while leaving the real output reserve.
    context = config['llm']['num_ctx'] if short else config['llm'].get('analyze_num_ctx', config['llm']['num_ctx'])
    target, reserve, schema = probe_contract(config, short=short)
    low, high, selected = 0, context * 2, None
    for _ in range(20):
        repeats = (low + high) // 2
        value = http('/tokenize', {'model': model, 'messages': messages(repeats),
                     'add_generation_prompt': True, 'chat_template_kwargs': {
                         'enable_thinking': False}})
        count = value['count']
        if not isinstance(count, int):
            raise RuntimeError('Invalid tokenizer response')
        if target <= count <= target + 128:
            selected = (messages(repeats), count)
            break
        if count < target:
            low = repeats + 1
        else:
            high = repeats - 1
    if selected is None:
        raise RuntimeError('Could not construct a bounded long-context probe')
    prompt, input_tokens = selected
    sampler = request_policy(config, 'qualification_short' if short else 'analyze_qualification')
    payload = {'model': model, 'messages': prompt, 'stream': False, 'max_tokens': reserve,
               'temperature': sampler['temperature'], 'presence_penalty': sampler['presence_penalty'],
               'top_k': config['llm'].get('top_k', 20), 'top_p': config['llm'].get('top_p', .95),
               'chat_template_kwargs': {'enable_thinking': False},
               'response_format': {'type': 'json_schema', 'json_schema': {
                   'name': 'concurrency_probe', 'strict': True, 'schema': schema}}}
    barrier = threading.Barrier(slots)

    def request(index):
        barrier.wait(timeout=10)
        started = time.monotonic()
        response = http('/v1/chat/completions', payload)
        manage.atomic_json(receipt_path.with_name(f'{receipt_path.stem}-response-{index:02d}.json'), response)
        choice = response['choices'][0]
        if choice.get('finish_reason') != 'stop':
            raise RuntimeError('Qualification output incomplete: finish_reason=' + str(choice.get('finish_reason')))
        if json.loads(choice['message']['content']) != {'ok': True, 'samples': [0] * samples}:
            raise RuntimeError('Concurrent response failed JSON/finish validation')
        usage = response.get('usage', {})
        if usage.get('prompt_tokens', 0) < target or usage.get('completion_tokens', 0) < 1024:
            raise RuntimeError('Concurrent response lacks real long-input token evidence')
        return {'seconds': time.monotonic() - started, 'usage': usage}

    result = {'status': 'running', 'started_at': time.time(), 'server_identity': record['identity'],
              'probe_policy': phase_policy(config, short=short), 'sample_count': samples,
              'engine_profile_fingerprint': engine_profile(config),
              'signature': vllm_runtime.signature(config), 'config_fingerprint': manage.config_fingerprint(config),
              'num_ctx': context, 'slots': slots, 'input_tokens_each': input_tokens,
              'enable_thinking': False, 'output_reserve': reserve,
              'temperature': sampler['temperature'], 'presence_penalty': sampler['presence_penalty'],
              'cpu_offload_gb': 0, 'peak_running_requests': 0, 'peak_gpu_memory_mib': 0}
    manage.atomic_json(receipt_path, result)
    try:
        with manage.lock('pipeline'), ThreadPoolExecutor(max_workers=slots) as pool:
            pending = [pool.submit(request, i) for i in range(slots)]
            gpu_at = 0
            while not all(f.done() for f in pending):
                metrics = http('/metrics', raw=True)
                running = sum(float(line.rsplit(' ', 1)[1]) for line in metrics.splitlines()
                              if re.match(r'^vllm:num_requests_running(?:\{| )', line))
                result['peak_running_requests'] = max(result['peak_running_requests'], running)
                if time.monotonic() - gpu_at >= 2:
                    memory = subprocess.check_output(['nvidia-smi', '--query-gpu=memory.used',
                              '--format=csv,noheader,nounits'], text=True).strip().splitlines()
                    result['peak_gpu_memory_mib'] = max(result['peak_gpu_memory_mib'], max(map(int, memory)))
                    gpu_at = time.monotonic()
                time.sleep(.1)
            result['responses'] = [f.result() for f in pending]
        if result['peak_running_requests'] < slots:
            raise RuntimeError('Configured simultaneous running requests were not observed')
        result.update(status='passed', finished_at=time.time())
    except Exception as exc:
        result.update(status='failed', error=str(exc), finished_at=time.time())
        manage.atomic_json(receipt_path, result)
        raise
    manage.atomic_json(receipt_path, result)
    print(json.dumps(result), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--receipt', type=Path, required=True)
    args = parser.parse_args()
    config = manage.configuration(args.config)
    manage.activate_vllm(config)
    verify(config, args.receipt)
