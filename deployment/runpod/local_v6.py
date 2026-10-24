"""Canonical v6 Runpod controller: local working-set + native Syncthing.

Prepare offline; launch only on an explicitly authorized Pod. See
docs/plans/runpod-v6-agent-operational-plan.md. Only the native working-set controller is supported.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import zipfile

import manage
import native_transport as sync
import native_prepare as v6

WORK = Path('/opt/manga-work')
DURABLE = Path('/workspace/manga-runtime/local-v6')


def unpack(archive, destination):
    destination = Path(destination)
    with zipfile.ZipFile(archive) as zipped:
        for item in zipped.infolist():
            path = Path(item.filename)
            if path.is_absolute() or '..' in path.parts or '\\' in item.filename or ':' in item.filename:
                raise ValueError('Unsafe ingress archive')
            (destination / path).resolve().relative_to(destination.resolve())
        if zipped.testzip():
            raise RuntimeError('Ingress archive CRC failed')
        zipped.extractall(destination)


def ingest_chapter(plan, entry, jobs):
    """Wait for a native transfer, validate in isolation, then publish atomically."""
    from batch_v6 import input_folder
    root = manage.RUNTIME / 'data/input'
    target = root / entry['input']
    if target.exists():
        input_folder(entry)  # An empty/existing directory is never a readiness signal.
        return target
    receipt = entry['archive']
    archive = Path(plan['input_ingress']) / receipt['name']
    last_bytes, progressed = -1, time.monotonic()
    deadline = time.monotonic() + 4 * 3600
    while not archive.is_file():
        partials = list(archive.parent.glob('.syncthing.*.tmp'))
        size = sum(p.stat().st_size for p in partials if p.exists())
        if size != last_bytes:
            last_bytes, progressed = size, time.monotonic()
        manage.atomic_json(jobs / 'ingress-progress.json',
                           {'chapter': entry['input'], 'partial_bytes': size,
                            'waiting_seconds': round(time.monotonic() - progressed), 'updated_at': time.time()})
        if time.monotonic() - progressed > 600 or time.monotonic() > deadline:
            raise RuntimeError('Native input replication stalled; no committed archive: ' + receipt['name'])
        time.sleep(15)
    if archive.stat().st_size != receipt['bytes'] or manage.file_hash(archive) != receipt['sha256']:
        raise RuntimeError('Native input archive checksum mismatch')
    root.mkdir(parents=True, exist_ok=True)
    pending = root / (entry['input'] + '.pending')
    if pending.exists():
        # Only this exact unpublished, task-owned staging directory may be replaced.
        pending.resolve().relative_to(root.resolve())
        shutil.rmtree(pending)
    pending.mkdir()
    unpack(archive, pending)
    payload = pending / entry['input']
    if {p.name for p in pending.iterdir()} != {entry['input']}:
        raise RuntimeError('Unexpected input archive root')
    names = {p.name for p in payload.iterdir() if p.name != '.input-manifest.json'}
    if names != set(entry['images']):
        raise RuntimeError('Frozen input image set differs from archive')
    for name, image in entry['images'].items():
        p = payload / name
        if p.stat().st_size != image['bytes'] or manage.file_hash(p) != image['sha256']:
            raise RuntimeError('Frozen image checksum mismatch: ' + name)
    payload.rename(target)
    pending.rmdir()
    return input_folder(entry)


def prepare_restore(backup, directory, plan):
    """Freeze verified terminal publications as native ingress for a new batch."""
    backup = backup.resolve()
    verification = manage.read_json(backup / 'verification.json')
    if not verification or verification.get('status') != 'replica_archives_verified':
        raise ValueError('Restore requires a verified native backup')
    if verification.get('pipeline_status') not in {'completed', 'completed_with_errors'}:
        raise ValueError('Restore requires a terminal backup')
    published = backup / 'durable' / verification['batch'] / 'published'
    entries = {(e['story_key'], e['chapter']) for e in plan['chapters']}
    restored, found = [], set()
    for record in verification['archives']:
        receipt = Path(record['receipt']).resolve()
        relative = receipt.relative_to(published.resolve())
        kind = relative.parts[0]
        if kind == 'banks':
            destination = 'data/banks'
        elif kind == 'outputs' and len(relative.parts) == 4:
            story, chapter = relative.parts[1:3]
            if (story, chapter) not in entries:
                continue
            destination = f'data/outputs/{story}/{chapter}'
            found.add((story, chapter))
        else:
            continue
        archive = Path(record['archive']).resolve()
        archive.relative_to(backup)
        if archive.stat().st_size != record['bytes'] or manage.file_hash(archive) != record['sha256']:
            raise ValueError('Backup archive checksum changed: ' + str(archive))
        with zipfile.ZipFile(archive) as zipped:
            inventory = json.loads(zipped.read('__transfer_inventory__.json'))
            names = [i.filename for i in zipped.infolist() if not i.is_dir()]
            if (inventory.get('format') != 1 or len(names) != len(set(names))
                    or set(names) != {*inventory['files'], '__transfer_inventory__.json'}
                    or zipped.testzip() is not None):
                raise ValueError('Backup CRC/inventory mismatch')
            for name in inventory['files']:
                key = Path(name)
                if key.is_absolute() or '..' in key.parts or '\\' in name or ':' in name:
                    raise ValueError('Unsafe backup member')
        name = f'restore-{len(restored) + 1:03d}.zip'
        shutil.copyfile(archive, directory / name)
        plan['ingress'][name] = {'bytes': record['bytes'], 'sha256': record['sha256']}
        restored.append({'archive': name, 'destination': destination})
    if found != entries or not any(r['destination'] == 'data/banks' for r in restored):
        raise ValueError('Backup lacks requested chapter output or bank publication')
    plan['restore'] = {'batch': verification['batch'], 'archives': restored,
                       'verification_sha256': manage.file_hash(backup / 'verification.json')}


def prepare(args):
    args.execution_root = (WORK / 'execution').as_posix()
    v6.prepare(args)
    directory = sync.STATE / 'v6' / args.batch
    plan = manage.read_json(directory / 'plan.json')
    plan.update(runtime=(WORK / 'runtime').as_posix(), project=(WORK / 'project').as_posix(),
                durable_root=DURABLE.as_posix(), local_working_set=True)
    inputs = directory / 'inputs'
    inputs.mkdir()
    for index, entry in enumerate(plan['chapters'], 1):
        archive_name = f'{index:03d}-{entry["input"]}.zip'
        with zipfile.ZipFile(inputs / archive_name, 'w', compression=zipfile.ZIP_STORED) as zipped:
            folder = Path(entry['source_folder'])
            for name, receipt in entry['images'].items():
                image = folder / name
                if manage.file_hash(image) != receipt['sha256']:
                    raise RuntimeError('Input changed during preparation')
                zipped.write(image, entry['input'] + '/' + name)
            zipped.writestr(entry['input'] + '/.input-manifest.json',
                            json.dumps({n: r['sha256'] for n, r in entry['images'].items()}))
        entry['archive'] = {'name': archive_name, 'bytes': (inputs / archive_name).stat().st_size,
                            'sha256': manage.file_hash(inputs / archive_name)}
    locks = args.locks.resolve()
    with zipfile.ZipFile(directory / 'locks.zip', 'w') as zipped:
        expected = [manage.dependency_signature(), 'visual-' + manage.visual_signature(),
                    manage.ocr_python(json.loads((directory / 'pipeline-v6.json').read_text(encoding='utf-8'))).parents[2].name]
        if json.loads((directory / 'pipeline-v6.json').read_text(encoding='utf-8'))['llm'].get('backend') == 'vllm':
            expected.append('vllm')
        for name in expected:
            source = locks / name / 'requirements.lock'
            if not source.is_file():
                raise RuntimeError('Missing frozen dependency lock: ' + str(source))
            zipped.write(source, name + '/requirements.lock')
    plan['ingress'] = {name: {'sha256': manage.file_hash(directory / name),
                            'bytes': (directory / name).stat().st_size}
                       for name in ['source.zip', 'locks.zip', 'pipeline-v6.json']}
    plan['native_sync'] = True
    plan['native_ingress'] = (WORK / 'ingress' / args.batch).as_posix()
    plan['input_ingress'] = (WORK / 'ingress' / args.batch / 'inputs').as_posix()
    frozen_config = json.loads((directory / 'pipeline-v6.json').read_text(encoding='utf-8'))
    plan['verify_concurrency'] = bool(getattr(args, 'verify_concurrency', False)
                                      or frozen_config['runtime']['llm_slots'] > 1)
    if getattr(args, 'restore_from', None):
        if not args.reanalyze_existing:
            raise ValueError('Restored publications require explicit --reanalyze-existing')
        if getattr(args, 'import_preanalyze', False):
            from preanalyze_import import prepare_import
            prepare_import(args.restore_from, directory, plan, through=getattr(args, 'import_through', 'scan'))
        else:
            prepare_restore(args.restore_from, directory, plan)
    if getattr(args, 'reuse_runtime', False):
        if not getattr(args, 'import_preanalyze', False) or not args.restore_from:
            raise ValueError('Runtime reuse requires explicit verified pre-analysis import')
        plan['reuse_verified_runtime'] = True
    if getattr(args, 'qualification_from', None):
        from preanalyze_import import prepare_qualification
        prepare_qualification(args.qualification_from, directory, plan)
    manage.atomic_json(directory / 'plan.json', plan)
    print(json.dumps({'plan': str(directory / 'plan.json'),
                      'pages': sum(e['pages'] for e in plan['chapters']), 'status': 'prepared'}))


def remote_bootstrap(args):
    plan_path = args.plan.resolve()
    plan = manage.read_json(plan_path)
    incoming = Path(plan.get('native_ingress', str(plan_path.parent)))
    if plan.get('runtime') != str(WORK / 'runtime') or plan.get('project') != str(WORK / 'project'):
        raise ValueError('Unexpected canonical local paths')
    WORK.mkdir(parents=True, exist_ok=True)
    filesystem = subprocess.check_output(['findmnt', '-n', '-o', 'FSTYPE', '-T', str(WORK)], text=True).strip()
    if any(word in filesystem.lower() for word in ['fuse', 'nfs', 'cifs', '9p']):
        raise RuntimeError('Working set is on a remote filesystem: ' + filesystem)
    if shutil.disk_usage(WORK).free < 35 * 1024**3:
        raise RuntimeError('Local disk has less than 35 GiB free before bootstrap')
    for name, receipt in plan['ingress'].items():
        path = incoming / name
        deadline, progressed, last_size = time.monotonic() + 4 * 3600, time.monotonic(), -1
        while not path.exists():
            size = sum(p.stat().st_size for p in incoming.rglob('.syncthing.*.tmp') if p.exists())
            if size != last_size:
                last_size, progressed = size, time.monotonic()
            if time.monotonic() - progressed > 600 or time.monotonic() > deadline:
                raise RuntimeError('Native bootstrap delivery stalled: ' + name)
            time.sleep(10)
        if path.stat().st_size != receipt['bytes'] or manage.file_hash(path) != receipt['sha256']:
            raise RuntimeError('Ingress checksum mismatch: ' + name)
    project, runtime, execution = Path(plan['project']), Path(plan['runtime']), Path(plan['execution_root'])
    if project.exists():
        bundle = manage.read_json(project / '__bundle__.json', {})
        if bundle.get('version') != plan['source_bundle_hash']:
            raise RuntimeError('Local source belongs to another deployment')
    else:
        pending = project.with_name('project.pending')
        pending.mkdir()
        unpack(incoming / 'source.zip', pending)
        pending.rename(project)
    os.environ.update(MANGA_RUNTIME=str(runtime), MANGA_EXECUTION_ROOT=str(execution),
                      MANGA_DURABLE_ROOT=plan['durable_root'], MANGA_BATCH_ID=plan['batch'])
    # Import the frozen source, rather than the controller staged on the volume.
    os.execv(sys.executable, [sys.executable, str(project / 'deployment/runpod/local_v6.py'),
                            'remote-run', '--plan', str(plan_path)])


def qualify_concurrency(config, directory):
    """Only called by an explicitly launched remote job; never by prepare."""
    import vllm_runtime
    vllm_runtime.start(config)
    try:
        manage.call([manage.current_python(), '-B',
                     manage.PROJECT / 'deployment/runpod/validate_vllm_concurrency.py',
                     '--config', directory / 'pipeline-v6.json',
                     '--receipt', directory / 'concurrency-probe.json'])
    finally:
        vllm_runtime.stop()


def remote_run(args):
    plan = manage.read_json(args.plan)
    incoming = Path(plan.get('native_ingress', str(args.plan.parent)))
    directory = DURABLE / plan['batch'] / 'jobs'
    directory.mkdir(parents=True, exist_ok=True)
    state_path = directory / 'state.json'
    with manage.lock('local-bootstrap', timeout=1):
        status = {'status': 'bootstrap', 'pid': manage.service_identity(os.getpid()),
                  'started_at': time.time(), 'plan': plan}
        manage.atomic_json(directory / 'bootstrap.json', status)
        execution = manage.execution_root()
        execution.mkdir(parents=True, exist_ok=True)
        unpack(incoming / 'locks.zip', execution / 'envs')
        manage.atomic_json(directory / 'plan.json', plan)
        shutil.copyfile(incoming / 'pipeline-v6.json', directory / 'pipeline-v6.json')
        sys.path.insert(0, str(manage.PROJECT / 'src'))
        if plan.get('restore'):
            from manga_pipeline.storage.checkpoints import restore_archive
            restored = directory / 'restore.json'
            if restored.exists():
                raise RuntimeError('Backup already restored; inspect driver identity before relaunch')
            for receipt in plan['restore']['archives']:
                destination = Path(plan['runtime']) / receipt['destination']
                archive = incoming / receipt['archive']
                expected = plan['ingress'][receipt['archive']]
                if archive.stat().st_size != expected['bytes'] or manage.file_hash(archive) != expected['sha256']:
                    raise RuntimeError('Restore archive changed after ingress')
                if destination.exists():
                    if not plan.get('reuse_verified_runtime') or receipt['destination'] != 'data/banks':
                        raise RuntimeError('Refusing to overwrite restored runtime data: ' + str(destination))
                    from preanalyze_import import verify_existing_archive
                    verify_existing_archive(archive, destination)
                else:
                    restore_archive(archive, destination)
            manage.atomic_json(restored, {**plan['restore'], 'restored_at': time.time()})
            print('Verified backup restored before environment setup', flush=True)
        from manga_pipeline.runtime.resources import configure_batch
        config = manage.configuration(directory / 'pipeline-v6.json')
        os.environ.update(configure_batch(config, directory))
        # The normal setup consumes the supplied locks; no dependency re-resolution.
        manage.setup(config=config)
        if config.get('llm', {}).get('backend') == 'vllm':
            manage.activate_vllm(config)
            import vllm_runtime
            vllm_runtime.setup(config, resolve=False)
        if config.get('runtime', {}).get('enabled'):
            with manage.parallel_service_guard(config):
                pass
        manage.prefetch(config=config)
        config = manage.configuration(directory / 'pipeline-v6.json')
        if plan.get('until') not in {'scan', 'analyze', 'build_identity', 'relationship'} and config.get('inpaint', {}).get('enabled'):
            worker = manage.visual_python()
            manage.call([worker, '-B', manage.PROJECT / 'scripts/setup_visual_models.py',
                         '--only', 'inpaint', '--model-root', manage.RUNTIME / 'models/visual'])
            # Some visual workers resolve assets relative to the frozen project.
            target = (manage.RUNTIME / 'models/visual').resolve()
            alias = manage.PROJECT / 'models/visual'
            if alias.exists() or alias.is_symlink():
                if alias.resolve() != target:
                    raise RuntimeError('Visual model alias points outside the current runtime')
            else:
                alias.parent.mkdir(parents=True, exist_ok=True)
                alias.symlink_to(target, target_is_directory=True)
        manage.doctor(inference=True, config=config)
        # Real CUDA parity is required before any chapter inference.
        parity = ('import os,sys,unittest;os.environ["MANGA_TEST_CUDA"]="1";sys.path.insert(0,' + repr(str(manage.PROJECT / 'src')) + ');'
                  'suite=unittest.defaultTestLoader.discover(' + repr(str(manage.PROJECT / 'tests')) +
                  ',pattern="test_v6_geometry_backend.py");'
                  'r=unittest.TextTestRunner(verbosity=2).run(suite);'
                  'sys.exit(0 if r.wasSuccessful() and not r.skipped else 1)')
        manage.call([manage.current_python(), '-c', parity])
        if plan.get('qualification'):
            from validate_vllm_concurrency import validate_receipt
            proof = manage.read_json(incoming / plan['qualification']['file'])
            validate_receipt(config, proof)
            manage.atomic_json(directory / 'concurrency-probe.json', proof)
            manage.atomic_json(directory / 'qualification-reused.json', plan['qualification'])
            print('Reused verified concurrency qualification on the same pod/profile', flush=True)
        elif plan.get('verify_concurrency'):
            qualify_concurrency(config, directory)
        status.update(status='inference', updated_at=time.time())
        manage.atomic_json(directory / 'bootstrap.json', status)
        import batch_v6
        result = batch_v6.main([str(directory / 'plan.json'), '--state', str(state_path)])
        status.update(status='finished', exit_code=result, finished_at=time.time())
        manage.atomic_json(directory / 'bootstrap.json', status)
        return result


def launch(args):
    plan = v6.prepared(args.plan.resolve())
    if not plan.get('local_working_set'):
        raise ValueError('Expected a prepared local working-set plan')
    connection = v6.connection_for(args.pod_id)
    if plan.get('qualification') and plan['qualification']['pod_id'] != args.pod_id:
        raise ValueError('Qualification reuse is restricted to the original pod')
    staging = (DURABLE / plan['batch'] / 'ingress').as_posix()
    sync.remote(connection, ['mkdir', '-p', staging])
    # Native file replication handles all large input/output transfers.
    import native_sync
    native_sync.activate(args.plan.resolve(), connection)
    for name in ['plan.json']:
        sync.upload(connection, args.plan.parent / name, staging + '/' + name)
    sync.upload(connection, Path(__file__), staging + '/local_v6.py')
    # These small imports are needed only before the frozen local bundle is installed.
    for name in ['manage.py', 'native_transport.py', 'native_prepare.py', 'batch_v6.py', 'v6_contract.py']:
        sync.upload(connection, Path(__file__).parent / name, staging + '/' + name)
    script = '''import json,pathlib,subprocess,sys,time
root=pathlib.Path(sys.argv[1]);sys.path.insert(0,str(root));import manage
directory=root.parent/'jobs';directory.mkdir(parents=True,exist_ok=True)
with manage.lock('local-launch'):
 previous=manage.read_json(directory/'launch.json',{});identity=previous.get('identity')
 if identity and manage.service_identity(identity['pid'])==identity: raise RuntimeError('Driver already active')
 with (directory/'driver.log').open('ab') as log:
  child=subprocess.Popen([sys.executable,str(root/'local_v6.py'),'remote-bootstrap','--plan',str(root/'plan.json')],stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
 record={'identity':manage.service_identity(child.pid),'started_at':time.time()}
 manage.atomic_json(directory/'launch.json',record);print(json.dumps(record))'''
    print(sync.remote(connection, ['python3', '-c', script, staging], capture=True))
    manage.atomic_json(args.plan.parent / 'bound-plan.json', {**plan, 'pod_id': args.pod_id})
    with (args.plan.parent / 'native-verifier.log').open('ab') as log:
        verifier = subprocess.Popen([sys.executable, str(Path(__file__).parent / 'native_verify.py'),
                                     '--plan', str(args.plan.resolve()), '--wait'],
                                    stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)
                                    | getattr(subprocess, 'DETACHED_PROCESS', 0))
    manage.atomic_json(args.plan.parent / 'native-verifier.json', {'pid': verifier.pid, 'started_at': time.time()})


def watch(args):
    plan = manage.read_json(args.plan)
    connection = v6.connection_for(plan['pod_id'])
    script = '''import json,pathlib,sys
root=pathlib.Path(sys.argv[1]);out={}
for name in ['bootstrap.json','state.json','launch.json']:
 p=root/name
 if p.exists():out[name]=json.loads(p.read_text())
log=root/'driver.log'
if log.exists():
 with log.open('rb') as f:f.seek(max(0,log.stat().st_size-2500));out['log_tail']=f.read().decode('utf-8','replace')
print(json.dumps(out))'''
    print(sync.remote(connection, ['python3', '-c', script, (DURABLE / plan['batch'] / 'jobs').as_posix()], capture=True))


def collect(args):
    plan = manage.read_json(args.plan)
    connection = v6.connection_for(plan['pod_id'])
    destination = args.destination.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(sync.PROJECT / 'src'))
    from manga_pipeline.storage.checkpoints import restore_archive
    receipts = manage.read_json(destination / 'collection.json', {})
    while True:
        script = '''import json,pathlib,sys
root=pathlib.Path(sys.argv[1]);state=root/'jobs/state.json'
print(json.dumps({'state':json.loads(state.read_text()) if state.exists() else {},'receipts':[json.loads(p.read_text())|{'receipt_path':str(p)} for p in (root/'published').rglob('ready.json')]}))'''
        remote = json.loads(sync.remote(connection, ['python3', '-c', script,
                                                      (DURABLE / plan['batch']).as_posix()], capture=True))
        for receipt in remote['receipts']:
            key = receipt['receipt_path']
            if key in receipts and receipts[key]['archive_sha256'] == receipt['archive_sha256']:
                continue
            relative = Path(key).relative_to(DURABLE / plan['batch'] / 'published').parent
            archive = destination / 'archives' / (receipt['archive_sha256'] + '.zip')
            archive.parent.mkdir(parents=True, exist_ok=True)
            target = connection.get('user', 'root') + '@' + connection['host'] + ':' + receipt['archive']
            sync.execute([sync.ssh_executable('scp'), *sync.ssh_options(connection, scp=True),
                          target, sync.ssh_path(archive)])
            if archive.stat().st_size != receipt['archive_bytes'] or manage.file_hash(archive) != receipt['archive_sha256']:
                raise RuntimeError('Downloaded archive checksum mismatch')
            restore_archive(archive, destination / relative)
            receipts[key] = receipt
            manage.atomic_json(destination / 'collection.json', receipts)
            print('COLLECTED ' + str(relative), flush=True)
        from local_review import generate
        generate(destination, plan_path=args.plan)
        terminal = remote['state'].get('status') in {'completed', 'completed_with_review', 'completed_with_errors', 'interrupted'}
        if terminal and 'bank_publication' in remote['state'] and 'audit_publication' in remote['state']:
            manage.atomic_json(destination / 'final-state.json', remote['state'])
            return 0
        time.sleep(args.poll)


def main():
    if len(sys.argv) > 1 and sys.argv[1] in {"ocr-prepare", "ocr-launch", "ocr-watch", "ocr-remote"}:
        from ocr_diagnostic import main as diagnostic_main
        return diagnostic_main()
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    prep = commands.add_parser('prepare')
    prep.add_argument('folder', type=Path)
    prep.add_argument('--batch', required=True)
    prep.add_argument('--gpu-preset', choices=['a40', 'rtx4090', 'rtx5090', 'l40s'],
                      help='Opt in to bounded parallel stories and adaptive batches; prepare only')
    prep.add_argument('--llm-slots', type=int, choices=range(1,65))
    prep.add_argument('--story-workers', type=int, choices=range(1,9))
    prep.add_argument('--reanalyze-existing', action='store_true')
    prep.add_argument('--restore-from', type=Path,
                      help='Verified terminal native backup to restore before reanalysis')
    prep.add_argument('--import-preanalyze', action='store_true',
                      help='Import verified v6 stages through scan into a fresh contract; never copy analyze/cache')
    prep.add_argument('--import-through', choices=['normalize', 'scan', 'analyze', 'inpaint'], default='scan',
                      help='Last committed stage to import; normalize re-runs the complete scan')
    prep.add_argument('--reuse-runtime', action='store_true',
                      help='Reuse existing banks only after matching every file to the frozen restore inventory')
    prep.add_argument('--qualification-from', type=Path,
                      help='Verified bound plan on the same pod and identical config; reuse real concurrency evidence')
    prep.add_argument('--verify-concurrency', action='store_true',
                      help='Require two real long-context requests before chapter inference')
    prep.add_argument('--caption-max-distance', type=int, choices=range(1,65))
    prep.add_argument('--until', choices=['scan', 'analyze', 'build_identity', 'relationship'],
                      help='Stop at the selected semantic checkpoint; no downstream rendering/translation')
    prep.add_argument('--story', required=True)
    prep.add_argument('--chapter', default='chapter-001')
    prep.add_argument('--additional-chapter', nargs=2, action='append', default=[])
    prep.add_argument('--additional-story', nargs=3, action='append', default=[],
                      metavar=('STORY', 'CHAPTER', 'FOLDER'))
    prep.add_argument('--locks', type=Path, required=True)
    for name in ['launch', 'watch', 'collect', 'remote-bootstrap', 'remote-run']:
        command = commands.add_parser(name)
        command.add_argument('--plan', type=Path, required=True)
        if name == 'launch': command.add_argument('--pod-id', required=True)
        if name == 'collect':
            command.add_argument('--destination', type=Path, required=True)
            command.add_argument('--poll', type=int, default=30)
    args = parser.parse_args()
    return globals()[args.command.replace('-', '_')](args)


if __name__ == '__main__':
    raise SystemExit(main())
