"""Run only relationship in a fresh revision, preserving committed upstream work.

Run from a newly frozen source bundle on the existing authorized Pod. This does
not provision, stop or terminate a Pod, and does not run qualification requests.
"""
import argparse
import copy
import os
from pathlib import Path
import shutil
import sys
import time
import uuid

import manage


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-run', type=Path, required=True)
    parser.add_argument('--batch', required=True)
    parser.add_argument('--durable-root', type=Path, required=True)
    args = parser.parse_args()
    if not args.batch or any(c not in 'abcdefghijklmnopqrstuvwxyz0123456789-_' for c in args.batch):
        raise ValueError('Invalid batch key')
    sys.path.insert(0, str(manage.PROJECT / 'src'))
    from manga_pipeline.pipeline import Pipeline
    from manga_pipeline.storage.runs import RunStore
    from manga_pipeline.storage.io import atomic_json, file_hash, read_json
    from manga_pipeline.storage.locking import exclusive_lock
    from manga_pipeline.storage.checkpoints import publish, archive_files, files_under
    from manga_pipeline.runtime.resources import configure_batch

    durable = args.durable_root / args.batch
    jobs = durable / 'jobs'
    jobs.mkdir(parents=True, exist_ok=True)
    state = {'status': 'preparing', 'source_run': str(args.source_run.resolve()),
             'source_bundle': read_json(manage.PROJECT / '__bundle__.json')['version'],
             'started_at': time.time(), 'only_stage': 'relationship',
             'startup_tests': False, 'automatic_retries': 0}
    atomic_json(jobs / 'state.json', state)
    with manage.lock('pipeline'), exclusive_lock(args.source_run / '.run.lock', timeout=1):
        source = RunStore(args.source_run)
        stages = source.steps[:source.steps.index('relationship')]
        with source.verification_pass():
            for stage in stages:
                if not source.completed(stage):
                    raise ValueError('Committed upstream stage required: ' + stage)
        config = copy.deepcopy(source.manifest['config'])
        if config['runtime']['required_gpu'] != 'L40S':
            raise ValueError('Relationship replay requires the authorized L40S configuration')
        run_id = time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()) + '-' + uuid.uuid4().hex[:8]
        target = RunStore.create(source.directory.parent / run_id,
            source=copy.deepcopy(source.manifest['source']), config=config,
            bank_path=source.manifest['bank_path'])
        for path in (source.directory / 'snapshots').iterdir():
            if path.is_file() and path.name not in {'config.json', 'source.json'}:
                shutil.copyfile(path, target.directory / 'snapshots' / path.name)
        target.manifest['relationship_replay'] = {
            'source_run': str(source.directory),
            'source_manifest_sha256': file_hash(source.path),
            'source_bundle': state['source_bundle'],
            'upstream_artifacts': {s: source.manifest['steps'][s]['artifact_hash'] for s in stages}}
        for stage in stages:
            shutil.copytree(source.artifact_path(stage).parent, target.artifact_path(stage).parent,
                            ignore=shutil.ignore_patterns('*.lock'))
            target.finish(stage, source.read(stage))
        state.update(status='inference', run_dir=str(target.directory))
        atomic_json(jobs / 'state.json', state)
        os.environ.update(manage.environment())
        os.environ['MANGA_BATCH_ID'] = args.batch
        os.environ.update(configure_batch(config, jobs))
        manage.activate_vllm(config)
        try:
            completed = Pipeline(target).run('relationship', only_step=True)
            result = RunStore(target.directory).read('relationship')
            state.update(status='completed' if completed and not result.get('inference_failures')
                         else 'completed_with_errors',
                         inference_failures=result.get('inference_failures', []),
                         pairs=len(result.get('pairs', {})),
                         directed_edges=len(result.get('address_table', [])))
        except BaseException as exc:
            state.update(status='failed', error=str(exc))
            raise
        finally:
            state.update(finished_at=time.time(), writer_stopped=True)
            atomic_json(jobs / 'state.json', state)
            state['output_publication'] = publish(manage.RUNTIME, durable, 'outputs',
                story=source.manifest['source']['story'], chapter=source.manifest['source']['chapter'])
            state['bank_publication'] = publish(manage.RUNTIME, durable, 'banks')
            atomic_json(jobs / 'state.json', state)
            audit = durable / 'published/audit'
            state['audit_publication'] = archive_files(files_under(jobs), manage.RUNTIME,
                                                       audit, audit / 'ready.json')
            atomic_json(jobs / 'state.json', state)
    print(state, flush=True)
    return 0 if state['status'] == 'completed' else 2


if __name__ == '__main__':
    raise SystemExit(main())
