"""Frozen multimodal evidence and single-attempt, resumable stage requests."""
import base64
import copy
from pathlib import Path

from ..storage.io import atomic_bytes, atomic_json, file_hash, fingerprint, read_json
from ..runtime.inference_state import InferenceUncertain
from ..runtime.llm_budget import BudgetExceeded
from ..runtime.resources import ResourceExhausted
from . import scheduling
from .requests import Calls
from .relationships import crop_selection


class EvidenceIntegrityError(RuntimeError):
    """A corrupt frozen artifact is not an inference failure eligible for fallback."""


def freeze(path, source, factory):
    source_hash = fingerprint(source)
    if path.exists():
        value = read_json(path)
        if value.get('source_hash') != source_hash:
            raise ValueError('Frozen identity/relationship input changed: ' + str(path))
        if value.get('content_hash') != fingerprint(value['data']):
            raise ValueError('Corrupt frozen identity/relationship plan')
        return value['data']
    value = factory()
    atomic_json(path, {'source_hash': source_hash, 'content_hash': fingerprint(value), 'data': value})
    return value


def retain_crops(store, stage, main_id, count):
    if main_id is None:
        return [], [{'error': 'Identity has no active main MAGI member'}]
    bank = read_json(store.manifest['bank_path'])
    rec = bank.get('characters', {}).get(str(main_id), {})
    disabled = set(rec.get('disabled_crop_paths', []))
    crops, issues = crop_selection(store.manifest['bank_path'], [str(main_id)], 16,
                                   store.manifest['config']['v6']['context_image_max_side'])
    output, seen = [], set()
    for crop in crops:
        # The model sees the normalized PNG, so duplicate pixels encoded with
        # different source metadata should not consume two of its ten images.
        visible_hash = fingerprint(crop['image'])
        if crop['path'] in disabled or visible_hash in seen:
            continue
        seen.add(visible_hash)
        binary = base64.b64decode(crop['image'])
        relative = store.artifact_path(stage).parent.relative_to(store.directory) / 'reference-crops' / (crop['sha256'] + '.png')
        path = store.directory / relative
        if path.exists() and path.read_bytes() != binary:
            raise ValueError('Frozen reference image changed')
        atomic_bytes(path, binary)
        output.append({k: v for k, v in crop.items() if k != 'image'} | {
            'file': relative.as_posix(), 'retained_sha256': file_hash(path)})
        if len(output) >= count:
            break
    return output, issues


def images(store, crops):
    output = []
    for crop in crops:
        path = (store.directory / crop['file']).resolve()
        try:
            if not path.is_relative_to(store.directory.resolve()) or file_hash(path) != crop['retained_sha256']:
                raise EvidenceIntegrityError('Frozen image checksum mismatch')
            output.append(base64.b64encode(path.read_bytes()).decode('ascii'))
        except OSError as exc:
            raise EvidenceIntegrityError('Missing frozen reference image') from exc
    return output


def spread(rows, count):
    """Sample whole evidence segments across the complete chapter deterministically."""
    if count >= len(rows):
        return rows
    if count <= 0:
        return []
    if count == 1:
        return rows[:1]
    indices = {round(i * (len(rows)-1) / (count-1)) for i in range(count)}
    return [rows[i] for i in sorted(indices)]


def pack(calls, task, prompt, request, schema, reserve, image_data):
    request = copy.deepcopy(request)
    if task == 'character_profile_v2' and request.get('previous'):
        request['previous'] = {k: request['previous'][k] for k in ('person_id', 'version', 'attributes')}
    if 'profiles' in request:
        request['profiles'] = {pid: {k: profile[k] for k in ('person_id', 'version', 'attributes')}
                               for pid, profile in request['profiles'].items()}
    if task == 'relationship_vi_v2' and request.get('previous'):
        request['previous'] = {k: request['previous'][k] for k in ('version', 'decision')}
    segments = request.pop('evidence_segments', [])
    ordered = sorted(enumerate(segments), key=lambda x: (-x[1].get('priority', 0), x[0]))
    count = len(segments)
    while True:
        # Priority strata preserve named/direct evidence while covering scenes.
        selected = []
        for priority in sorted({s.get('priority', 0) for s in segments}, reverse=True):
            group = [i for i, s in ordered if s.get('priority', 0) == priority]
            remaining = count-len(selected)
            if remaining <= 0:
                break
            selected.extend(spread(group, min(remaining, len(group))))
        selected = sorted(selected)
        wire = {**request, 'evidence_segments': [segments[i] for i in selected]}
        report = calls.budget(task, prompt, wire, schema, reserve, image_data)
        if report['estimate'] <= report['limit']:
            return wire, {'budget': report, 'selected_segments': selected,
                          'omitted_segments': [i for i in range(len(segments)) if i not in selected]}
        if not count:
            raise BudgetExceeded('Mandatory identity/profile inputs exceed context; no POST sent')
        count = max(0, min(count-1, int(count * .75)))


def run_jobs(store, provider, stage, jobs, prompt, schema_for, validate, reserve, maximum, *, normalize=None):
    directory = store.artifact_path(stage).parent
    calls = Calls(store, provider, stage, allow_uncertain=True)
    cfg = store.manifest['config']
    generation = fingerprint([cfg['llm'], cfg.get('vllm', {}), prompt])
    attempted, outcomes, fatal = set(), {}, []
    import threading
    lock = threading.Lock()

    def execute(job):
        key = job['batch_id']
        path = directory / 'decisions' / (fingerprint(key) + '.json')
        job_hash = fingerprint([job, generation, reserve])
        with lock:
            attempted.add(key)
        # Completed decisions still depend on the exact retained images, even
        # before the enclosing stage has a commit receipt.
        image_data = images(store, job.get('crops', []))
        if path.exists():
            try:
                receipt = read_json(path)
                if receipt.get('job_hash') != job_hash or receipt.get('record_hash') != fingerprint(receipt['record']):
                    raise EvidenceIntegrityError('Frozen decision receipt changed')
                if receipt['record']['status'] == 'completed':
                    validate(receipt['record']['result'], job['request'])
            except (ValueError, KeyError, TypeError, OSError) as exc:
                raise EvidenceIntegrityError('Invalid frozen decision receipt') from exc
            return receipt['record']
        record = {'batch_id': key, 'status': 'failed', 'post_attempts_max': 1}
        try:
            schema = schema_for(job['request'])
            wire, packing = pack(calls, job['task'], prompt,
                {**job['request'], 'generation_identity': generation}, schema, reserve, image_data)
            # Validate only against evidence actually sent, never omitted evidence.
            record['packing'] = packing
            atomic_json(directory / 'packed' / (fingerprint(key) + '.json'), {
                'request': wire, 'packing': packing, 'crops': job.get('crops', [])})
            value = calls.call(key, job['task'], prompt, wire, schema, reserve=reserve,
                               images=image_data or None, oom_retry=False)
            if normalize is not None:
                value = normalize(value, wire)
            validate(value, wire)
            record.update(status='completed', result=value)
        except EvidenceIntegrityError:
            raise
        except Exception as exc:
            record.update(status='uncertain' if isinstance(exc, (InferenceUncertain, OSError)) else 'failed',
                          error=str(exc), error_type=type(exc).__name__)
            if isinstance(exc, ResourceExhausted):
                lower = getattr(provider, 'reduce_admission', None)
                if callable(lower):
                    lower()
                    record['admission_reduced'] = True
            # Paused/uncertain providers must not drain all never-sent jobs.
            if isinstance(exc, (InferenceUncertain, OSError)):
                persist(path, job_hash, record)
                with lock:
                    outcomes[key] = record
                raise InferenceUncertain(str(exc)) from exc
        persist(path, job_hash, record)
        with lock:
            outcomes[key] = record
        return record

    def worker(job):
        try:
            return execute(job)
        except EvidenceIntegrityError as exc:
            with lock:
                fatal.append(str(exc))
            raise

    results, failures = scheduling.run(jobs, worker, provider,
        maximum=min(maximum, 8, cfg['runtime']['llm_slots']), stage=stage,
        retry_resources=False, stop_on_error=lambda exc: isinstance(exc, (EvidenceIntegrityError, InferenceUncertain, OSError)))
    if fatal:
        raise EvidenceIntegrityError('; '.join(fatal))
    # A worker may have persisted an uncertain response and raised to stop admission.
    for job in jobs:
        if job['batch_id'] not in attempted:
            results.append({'batch_id': job['batch_id'], 'status': 'never_sent',
                            'error': 'Service unavailable or queue paused; no automatic retry'})
        elif not any(r['batch_id'] == job['batch_id'] for r in results):
            if job['batch_id'] in outcomes:
                results.append(outcomes[job['batch_id']])
            else:
                failure = next((f for f in failures if f['batch_id'] == job['batch_id']), {})
                results.append({'batch_id': job['batch_id'], 'status': 'failed',
                                'error': failure.get('error', 'Worker failed before recording a decision')})
    atomic_json(directory / 'request-summary.json', {'results': results, 'queue_failures': failures,
        'maximum_concurrency': min(maximum, 8, cfg['runtime']['llm_slots']), 'automatic_retries': 0})
    return results


def persist(path, job_hash, record):
    atomic_json(path, {'job_hash': job_hash, 'record_hash': fingerprint(record), 'record': record})


def evidence_refs(request):
    refs = {c['crop_id'] for c in request.get('crops', [])}
    refs.update(p['ref'] for p in request.get('profile_refs', []))
    for segment in request.get('evidence_segments', []):
        refs.update(r['id'] for r in segment['rows'])
    return refs


def checked_evidence(item, refs, *, required=True):
    values = item.get('evidence_refs')
    if not isinstance(values, list) or (required and not values) or any(r not in refs for r in values):
        raise ValueError('Unknown or missing evidence reference')
