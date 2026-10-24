"""Explicit, read-only import of verified v6 pre-analysis artifacts into a new run."""
import argparse
import copy
import json
from pathlib import Path, PurePosixPath
import shutil
import sys
import tempfile
import zipfile

import manage

STAGES = ('layout', 'extract', 'bubble_geometry', 'normalize', 'scan',
          'analyze', 'build_identity', 'relationship', 'export', 'inpaint')
LEGACY_STAGES = ('layout', 'extract', 'bubble_geometry', 'normalize', 'scan',
                 'analyze', 'identity', 'export', 'inpaint')
PARENTS = {'layout': (), 'extract': ('layout',),
           'bubble_geometry': ('layout', 'extract'), 'normalize': ('bubble_geometry',),
           'scan': ('normalize',), 'analyze': ('scan', 'normalize', 'extract'),
           'identity': ('analyze', 'normalize', 'extract'),
           'build_identity': ('analyze', 'normalize', 'extract'),
           'relationship': ('build_identity', 'analyze', 'normalize'),
           'export': ('build_identity', 'relationship', 'analyze', 'normalize'),
           'inpaint': ('export', 'bubble_geometry')}


def import_parents(config):
    parents = dict(PARENTS)
    if config.get('v6', {}).get('identity_relationship_policy') != 'identity-relationship-v2':
        parents['export'] = ('identity', 'analyze', 'normalize')
    if config.get('v6', {}).get('identity_name_binding_policy'):
        parents['build_identity'] = ('analyze', 'normalize', 'extract', 'scan')
    return parents


def binding_contract(config):
    cfg = config.get('v6', {})
    return (tuple(cfg.get(k) for k in ('identity_name_binding_policy',
            'identity_binding_concurrency', 'identity_binding_output_tokens', 'identity_min_chapter_crops'))
            if cfg.get('identity_name_binding_policy') else None)


def scan_contract(config):
    cfg = config.get('v6', {})
    return (cfg.get('scan_policy'), cfg.get('scan_output_tokens')) if cfg.get('scan_policy') else None


def analysis_contract(config):
    """Only post-analyze identity/profile/translation settings may change."""
    return {**{k: config.get(k) for k in ('analysis_contract', 'dialogue_analysis', 'speaker_policy', 'llm', 'vllm')},
            'runtime': {k: config.get('runtime', {}).get(k) for k in ('context_fraction', 'analyze_max_batch', 'adaptive_batches')},
            'v6': {k: config.get('v6', {}).get(k) for k in
                   ('analyze_engine', 'speaker_merge_policy', 'analyze_batch_size', 'caption_max_distance', 'context_image_max_side')}}


def prepare_identity_base(source, banks, selected):
    """Freeze an authenticated rollback of an empty, task-local identity pass.

    Named/profile/graph publications require a more general history migration;
    refuse that case instead of replaying old analysis over newer knowledge.
    """
    from manga_pipeline.storage.io import fingerprint
    manifest = manage.read_json(source / 'manifest.json')
    analyzed = manage.read_json(stage_directory(source, 'analyze') / 'result.json')
    registry = banks / '_persons_v6' / manifest['source']['story'] / 'registry.json'
    current = manage.read_json(registry) if registry.exists() else None
    expected = analyzed['person_state']['base_registry_hash']
    before, operation = current, None
    identity_step = next((s for s in ('build_identity', 'identity') if manifest.get('steps', {}).get(s, {}).get('status') == 'completed'), None)
    if identity_step:
        file = stage_directory(source, identity_step) / 'result.json'
        if manage.file_hash(file) != manifest['steps'][identity_step]['artifact_hash']:
            raise ValueError('Source identity publication changed')
        identity = manage.read_json(file)
        if (identity.get('profiles') or identity.get('proposals') or
                any(p.get('primary_name') for p in identity.get('person_state', {}).get('persons', {}).values())):
            raise ValueError('Analyze-only reuse cannot rewind named identity/profile publications; import through scan')
    if manifest.get('steps', {}).get('relationship', {}).get('status') == 'completed':
        file = stage_directory(source, 'relationship') / 'result.json'
        if manage.file_hash(file) != manifest['steps']['relationship']['artifact_hash']:
            raise ValueError('Source relationship publication changed')
        graph = manage.read_json(file)
        if graph.get('identities') or graph.get('pairs'):
            raise ValueError('Analyze-only reuse cannot rewind a nonempty graph; import through scan')
    if fingerprint(current) != expected:
        if not identity_step:
            raise ValueError('No authenticated identity journal for reused analyze')
        operation = identity['operation_id']
        journal = manage.read_json(registry.parent / 'journal' / (operation + '.json'))
        if (fingerprint(current) != journal.get('after_hash')
                or fingerprint(identity['person_state']) != fingerprint(current)
                or journal.get('before_hash') != expected or fingerprint(journal.get('before')) != expected):
            raise ValueError('Identity journal does not match the exact source analyze/base registry')
        before = journal['before']
    manage.atomic_json(selected / 'snapshots/import-identity-base.json', {
        'policy': 'verified-empty-identity-fork-v1', 'expected_current_hash': fingerprint(current),
        'before_hash': expected, 'before': before, 'source_operation': operation})


def restore_identity_base(source, target):
    from manga_pipeline.v6.persons import registry_path
    from manga_pipeline.storage.io import fingerprint
    proof = manage.read_json(source / 'snapshots/import-identity-base.json')
    path = registry_path(target)
    path.resolve().relative_to((manage.RUNTIME / 'data/banks/_persons_v6').resolve())
    current = manage.read_json(path) if path.exists() else None
    if proof.get('policy') != 'verified-empty-identity-fork-v1' or fingerprint(proof.get('before')) != proof.get('before_hash'):
        raise ValueError('Invalid pre-identity restore proof')
    if fingerprint(current) not in {proof['expected_current_hash'], proof['before_hash']}:
        raise ValueError('Task-local person registry changed after verified restore')
    if proof['before'] is None:
        path.unlink(missing_ok=True)
    else:
        manage.atomic_json(path, proof['before'])
    manage.atomic_json(target.directory / 'snapshots/import-identity-base.json', proof)


def stage_directory(source, stage):
    matches = list(source.glob('[0-9][0-9]_' + stage))
    if len(matches) != 1:
        raise ValueError('Missing or ambiguous imported stage directory: ' + stage)
    return matches[0]


def prepare_qualification(source_plan, directory, plan):
    """Freeze real evidence from a verified audit; never invent a passed receipt."""
    import hashlib
    from validate_vllm_concurrency import engine_profile, validate_receipt
    prior = manage.read_json(source_plan)
    if not prior.get('pod_id'):
        raise ValueError('Qualification reuse requires a bound source pod')
    native = manage.read_json(source_plan.parent / 'native-sync.json')
    verified = manage.read_json(Path(native['destination']) / 'verification.json', {})
    if verified.get('status') != 'replica_archives_verified' or verified.get('batch') != prior['batch']:
        raise ValueError('Prior qualification backup is not verified')
    record = next(a for a in verified['archives'] if '/published/audit/' in Path(a['archive']).as_posix())
    archive = Path(record['archive'])
    if manage.file_hash(archive) != record['sha256']:
        raise ValueError('Qualification audit archive changed')
    with zipfile.ZipFile(archive) as zipped:
        inventory = json.loads(zipped.read('__transfer_inventory__.json'))
        data = zipped.read('concurrency-probe.json')
        receipt = inventory['files']['concurrency-probe.json']
        if len(data) != receipt['bytes'] or hashlib.sha256(data).hexdigest() != receipt['sha256']:
            raise ValueError('Qualification audit member changed')
    proof = json.loads(data)
    if proof.get('status') != 'passed' or proof.get('cpu_offload_gb') != 0:
        raise ValueError('Prior concurrency evidence is incompatible')
    # Read the effective source config from its verified immutable output archive.
    source_config = None
    for output in verified['archives']:
        if '/published/outputs/' not in Path(output['archive']).as_posix():
            continue
        if manage.file_hash(Path(output['archive'])) != output['sha256']:
            raise ValueError('Qualification source output archive changed')
        with zipfile.ZipFile(output['archive']) as zipped:
            inventory = json.loads(zipped.read('__transfer_inventory__.json'))
            for name in inventory['files']:
                if not name.endswith('/manifest.json'):
                    continue
                wire = zipped.read(name)
                if hashlib.sha256(wire).hexdigest() != inventory['files'][name]['sha256']:
                    raise ValueError('Qualification source manifest changed')
                config = json.loads(wire).get('config')
                if config and (manage.config_fingerprint(config) == proof.get('config_fingerprint')
                        or engine_profile(config) == proof.get('engine_profile_fingerprint')):
                    source_config = config
    if source_config is None:
        raise ValueError('No verified effective config matches the original qualification')
    validate_receipt(source_config, proof)
    sys.path.insert(0, str(manage.PROJECT / 'src'))
    from manga_pipeline.pipeline import load_config
    requested = load_config(directory / 'pipeline-v6.json')
    if engine_profile(source_config) != engine_profile(requested):
        raise ValueError('Qualification engine/GPU/context profile changed')
    proof['engine_profile_fingerprint'] = engine_profile(source_config)
    proof['source_qualification_sha256'] = hashlib.sha256(data).hexdigest()
    data = (json.dumps(proof, ensure_ascii=False, indent=2) + '\n').encode()
    (directory / 'qualification.json').write_bytes(data)
    plan['ingress']['qualification.json'] = {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
    plan['qualification'] = {'file': 'qualification.json', 'pod_id': prior['pod_id'],
                             'source_batch': prior['batch'], 'source_audit_sha256': record['sha256']}


def verify_existing_archive(archive, destination):
    """A revision may reuse exact bank bytes, but never overwrite live state."""
    destination = Path(destination).resolve()
    with zipfile.ZipFile(archive) as zipped:
        inventory = json.loads(zipped.read('__transfer_inventory__.json'))
        names = [i.filename for i in zipped.infolist() if not i.is_dir()]
        if (inventory.get('format') != 1 or len(names) != len(set(names))
                or set(names) != {*inventory['files'], '__transfer_inventory__.json'}
                or zipped.testzip() is not None):
            raise ValueError('Invalid bank reuse inventory')
        for name, receipt in inventory['files'].items():
            key = PurePosixPath(name)
            if key.is_absolute() or '..' in key.parts or '\\' in name or ':' in name:
                raise ValueError('Unsafe bank reuse path')
            path = destination.joinpath(*key.parts)
            path.resolve().relative_to(destination)
            if (path.is_symlink() or not path.is_file() or path.stat().st_size != receipt['bytes']
                    or manage.file_hash(path) != receipt['sha256']):
                raise ValueError('Existing bank differs from frozen import: ' + name)


def selected_stages(through, config=None):
    if through not in {'normalize', 'scan', 'analyze', 'inpaint'}:
        raise ValueError('Import boundary must be normalize, scan, analyze or inpaint')
    stages = LEGACY_STAGES if config is not None and config.get('v6', {}).get('identity_relationship_policy') != 'identity-relationship-v2' else STAGES
    return stages[:stages.index(through) + 1]


def prepare_import(backup, directory, plan, *, through='scan'):
    """Package only committed source stages; preserve the original backup."""
    sys.path.insert(0, str(manage.PROJECT / 'src'))
    from manga_pipeline.storage.checkpoints import restore_archive, archive_files, files_under
    from manga_pipeline.storage.io import fingerprint
    from native_verify import local_path
    backup = backup.resolve()
    target_config = manage.read_json(directory / 'pipeline-v6.json')
    stages = selected_stages(through, target_config)
    verification = backup / 'migration-verification.json'
    if not verification.exists():
        verification = backup / 'verification.json'
    proof = manage.read_json(verification, {})
    if proof.get('status') not in {'migration_backup_verified', 'replica_archives_verified'}:
        raise ValueError('Explicit import requires a verified backup')
    state = manage.read_json(backup / 'durable' / proof['batch'] / 'jobs/state.json')
    entries = {(e['story_key'], e['chapter']) for e in plan['chapters']}
    chapters = {(c['story'], c['chapter']): c for c in state.get('chapters', [])}
    if not entries <= chapters.keys() or any(not chapters[k].get('writer_stopped') for k in entries):
        raise ValueError('Import source writers have not stopped')
    restored, runs = [], {}
    with tempfile.TemporaryDirectory(prefix='v6-preanalyze-import-') as temporary:
        root = Path(temporary)
        for record in sorted(proof['archives'], key=lambda r: '/published/banks/' not in Path(r['archive']).as_posix()):
            archive = local_path(Path(record['archive']))
            archive.relative_to(local_path(backup))
            if manage.file_hash(archive) != record['sha256']:
                raise ValueError('Import archive checksum changed')
            # restore_archive verifies every inventory entry and CRC while reading.
            if '/published/banks/' in archive.as_posix():
                restore_archive(archive, root / 'banks')
                name = 'restore-import-banks.zip'
                shutil.copyfile(archive, directory / name)
                restored.append({'archive': name, 'destination': 'data/banks'})
            elif '/published/outputs/' in archive.as_posix():
                match = next((k for k in entries if chapters[k].get('publication', {}).get('archive_sha256') == record['sha256']), None)
                if match is None:
                    continue
                story, chapter = match
                unpacked = root / story / chapter
                restore_archive(archive, unpacked)
                run_id = PurePosixPath(chapters[match]['run_dir']).name
                source = unpacked / run_id
                manifest = manage.read_json(source / 'manifest.json')
                if manifest.get('pipeline_version') != 6:
                    raise ValueError('Only explicit v6 pre-analysis import is supported')
                if through in {'scan', 'analyze', 'inpaint'} and scan_contract(manifest['config']) != scan_contract(target_config):
                    raise ValueError('Scan contract changed; import through normalize and rerun scan')
                if through == 'inpaint' and selected_stages(through, manifest['config']) != stages:
                    raise ValueError('Identity/relationship stage contract changed; import through scan or normalize instead')
                if through == 'inpaint' and binding_contract(manifest['config']) != binding_contract(target_config):
                    raise ValueError('Name-binding contract changed; import through scan or normalize instead')
                if through == 'analyze':
                    from manga_pipeline.pipeline import load_config
                    requested = load_config(directory / 'pipeline-v6.json')
                    if analysis_contract(manifest['config']) != analysis_contract(requested):
                        raise ValueError('Analyze inference contract changed; import through scan')
                parents = import_parents(manifest['config'])
                selected = root / 'selected' / story / chapter / run_id
                selected.mkdir(parents=True)
                shutil.copyfile(source / 'manifest.json', selected / 'manifest.json')
                shutil.copytree(source / 'snapshots', selected / 'snapshots')
                hashes = {}
                for index, stage in enumerate(stages, 1):
                    receipt = manifest['steps'].get(stage, {})
                    if receipt.get('status') != 'completed' or receipt.get('failures') != 0:
                        raise ValueError('Source stage is not committed: ' + stage)
                    name = stage_directory(source, stage).name
                    artifact = source / name / 'result.json'
                    if (manage.file_hash(artifact) != receipt.get('artifact_hash')
                            or receipt.get('input_hash') != fingerprint({p: hashes[p] for p in parents[stage]})
                            or manage.read_json(artifact).get('failures')):
                        raise ValueError('Source stage integrity failed: ' + stage)
                    hashes[stage] = receipt['artifact_hash']
                    shutil.copytree(source / name, selected / name,
                                    ignore=shutil.ignore_patterns('targets', '*.lock'))
                if through == 'analyze':
                    prepare_identity_base(source, root / 'banks', selected)
                if through == 'inpaint':
                    # Resume only durable, deterministic translation work. Never
                    # carry request receipts: `started` is intentionally uncertain.
                    translation_name = f'{len(stages)+1:02d}_translate'
                    old_translate = source / translation_name
                    resume_translate = selected / translation_name
                    targets = old_translate / 'targets'
                    if targets.is_dir():
                        for item in targets.glob('*.json'):
                            value = manage.read_json(item)
                            if (value.get('status') == 'completed'
                                    and value.get('result_hash') == fingerprint(value.get('result'))
                                    and isinstance(value.get('request_hash'), str)):
                                (resume_translate / 'targets').mkdir(parents=True, exist_ok=True)
                                shutil.copyfile(item, resume_translate / 'targets' / item.name)
                    for item in ('relationships.json',):
                        path = old_translate / item
                        if path.is_file():
                            resume_translate.mkdir(parents=True, exist_ok=True)
                            shutil.copyfile(path, resume_translate / item)
                    if (old_translate / 'relationship-crops').is_dir():
                        shutil.copytree(old_translate / 'relationship-crops', resume_translate / 'relationship-crops')
                    relationship_snapshot = source / 'snapshots/relationships-v1.json'
                    if relationship_snapshot.is_file():
                        shutil.copyfile(relationship_snapshot, selected / 'snapshots/relationships-v1.json')
                name = f'restore-import-{len(runs) + 1:03d}'
                receipt = archive_files(files_under(root / 'selected'), root,
                                        directory / name, directory / (name + '.json'))
                archive_name = name + '/' + Path(receipt['archive']).name
                restored.append({'archive': archive_name,
                                 'destination': 'data/imports/' + plan['batch']})
                runs[story + '/' + chapter] = story + '/' + chapter + '/' + run_id
                shutil.rmtree(root / 'selected')
        if set(runs) != {s + '/' + c for s, c in entries} or not (root / 'banks').is_dir():
            raise ValueError('Import backup lacks selected output or bank')
    for record in restored:
        file = directory / record['archive']
        plan['ingress'][record['archive']] = {'bytes': file.stat().st_size, 'sha256': manage.file_hash(file)}
    plan['restore'] = {'batch': proof['batch'], 'archives': restored,
                       'verification_sha256': manage.file_hash(verification)}
    plan['preanalyze_import'] = {'runs': runs, 'reuse': list(stages),
                                'analyze_cache': False, 'source_backup': str(backup)}


def import_run(source, config, *, through='scan'):
    """Never instantiate an old RunStore or relax its resume guard."""
    sys.path.insert(0, str(manage.PROJECT / 'src'))
    from manga_pipeline.pipeline import prepare_bank
    from manga_pipeline.storage.runs import RunStore
    from manga_pipeline.storage.io import read_json, fingerprint, file_hash
    from manga_pipeline.visual.stages import verify_files
    from manga_pipeline.extraction.document import validate_raw
    from manga_pipeline.extraction.config import DETECTION_DEFAULTS, threshold_for_manifest
    from manga_pipeline.v6.semantic import snapshot
    from datetime import datetime, timezone
    import uuid

    stages = selected_stages(through, config)
    old = read_json(source / 'manifest.json')
    if old.get('pipeline_version') != 6:
        raise ValueError('Import requires a v6 source')
    previous = old['config']
    if through in {'scan', 'analyze', 'inpaint'} and scan_contract(previous) != scan_contract(config):
        raise ValueError('Scan contract changed; import through normalize and rerun scan')
    if through == 'analyze' and analysis_contract(previous) != analysis_contract(config):
        raise ValueError('Analyze inference contract changed; import through scan')
    if through == 'inpaint' and selected_stages(through, previous) != stages:
        raise ValueError('Identity/relationship contract changed; import through scan or normalize')
    if through == 'inpaint' and binding_contract(previous) != binding_contract(config):
        raise ValueError('Name-binding contract changed; import through scan or normalize')
    parents = import_parents(previous)
    for name, value in (('config', previous), ('source', old['source'])):
        if fingerprint(read_json(source / 'snapshots' / (name + '.json'))) != fingerprint(value):
            raise ValueError('Import snapshot changed: ' + name)
    upstream = [('layout', {'python', 'model_root', 'device'}),
                ('ocr', {'python'}), ('noise_filter', set()), ('character_bank', set())]
    if through in {'scan', 'analyze', 'inpaint'}:
        upstream.append(('ner', {'device'}))
    if through == 'inpaint' and previous != config:
        raise ValueError('Post-analysis import requires the exact effective configuration')
    for key, ignored in upstream:
        if {k: v for k, v in previous[key].items() if k not in ignored} != {
                k: v for k, v in config[key].items() if k not in ignored}:
            raise ValueError('Import configuration differs: ' + key)
    for key in DETECTION_DEFAULTS:
        if threshold_for_manifest(previous, key) != threshold_for_manifest(config, key):
            raise ValueError('Import extraction threshold differs: ' + key)
    geometry_keys = ('mask_merge_iou', 'mask_containment', 'repair_radius', 'neck_width_ratio',
                     'min_body_area', 'max_reconstructed_fraction', 'hint_acceptance',
                     'body_margin_px', 'body_margin_ratio')
    if any(previous['v6'][k] != config['v6'][k] for k in geometry_keys):
        raise ValueError('Import geometry configuration differs')
    info = old['source']
    for image in info['images']:
        if file_hash(image['path']) != image['sha256']:
            raise ValueError('Import source image changed')
    outputs, hashes = {}, {}
    for index, stage in enumerate(stages, 1):
        path = stage_directory(source, stage) / 'result.json'
        receipt = old['steps'][stage]
        if (receipt.get('status') != 'completed' or receipt.get('failures') != 0
                or file_hash(path) != receipt['artifact_hash']
                or receipt['input_hash'] != fingerprint({p: hashes[p] for p in parents[stage]})):
            raise ValueError('Import stage integrity failed: ' + stage)
        result = read_json(path)
        if result.get('failures'):
            raise ValueError('Import stage contains failures: ' + stage)
        if stage in {'layout', 'bubble_geometry', 'inpaint'}:
            for page in result['pages']:
                verify_files(source, page)
        outputs[stage], hashes[stage] = result, receipt['artifact_hash']
    validate_raw(outputs['extract'])
    old_snapshot = read_json(source / 'snapshots/persons-v6.json') if through in {'scan', 'analyze', 'inpaint'} else None
    if through in {'scan', 'analyze', 'inpaint'} and outputs['scan']['person_snapshot_hash'] != fingerprint(old_snapshot):
        raise ValueError('Source scan snapshot changed')
    if ((through in {'scan', 'analyze', 'inpaint'} and outputs['scan'].get('policy') != 'name-index-v6.1')
            or outputs['normalize'].get('analysis_policy') != 'essential-v3'):
        raise ValueError('Unsupported pre-analysis artifact policy')
    bank = prepare_bank(manage.RUNTIME / 'data/banks', info['story'], visual=True)
    run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:8]
    destination = manage.RUNTIME / 'data/outputs' / info['story'] / info['chapter'] / run_id
    target = RunStore.create(destination, source=copy.deepcopy(info), config=config, bank_path=bank)
    if target.manifest.get('noise_rules_hash') != old.get('noise_rules_hash'):
        raise ValueError('Import normalization rules differ')
    target.manifest['preanalyze_import'] = {'source': str(source), 'source_manifest_sha256': file_hash(source / 'manifest.json'),
                                          'stage_hashes': hashes, 'analyze_cache_reused': False,
                                          'analyze_artifact_reused': through == 'analyze'}
    for stage in STAGES[:4]:
        shutil.copytree(source / target.directories[stage], target.artifact_path(stage).parent,
                        ignore=shutil.ignore_patterns('commit.json', 'targets', '*.lock'))
        target.finish(stage, outputs[stage])
    if through in {'scan', 'analyze', 'inpaint'}:
        scanned = copy.deepcopy(outputs['scan'])
        if through in {'analyze', 'inpaint'}:
            # Analyze and every downstream artifact are bound to the original
            # frozen person snapshot.  The restored bank may legitimately be
            # newer because identity/export update it after analyze; replacing
            # the snapshot with that bank state invalidates the reused plan.
            shutil.copyfile(source / 'snapshots/persons-v6.json',
                            target.directory / 'snapshots/persons-v6.json')
        else:
            fresh = snapshot(target, outputs['extract'])
            if fresh['state']['name_index'] != old_snapshot['state']['name_index']:
                raise ValueError('Source scan name index differs; cannot reuse scan')
            scanned['person_snapshot_hash'] = fingerprint(fresh)
        target.finish('scan', scanned)
    if through == 'analyze':
        restore_identity_base(source, target)
        shutil.copytree(source / target.directories['analyze'], target.artifact_path('analyze').parent,
                        ignore=shutil.ignore_patterns('commit.json', '*.lock'))
        target.finish('analyze', outputs['analyze'])
    if through == 'inpaint':
        for stage in stages[5:]:
            shutil.copytree(source / target.directories[stage], target.artifact_path(stage).parent,
                            ignore=shutil.ignore_patterns('commit.json', 'targets', '*.lock'))
            target.finish(stage, outputs[stage])
        old_translate = source / target.directories['translate']
        if old_translate.is_dir():
            destination = target.artifact_path('translate').parent
            for name in ('targets', 'relationship-crops'):
                if (old_translate / name).is_dir():
                    shutil.copytree(old_translate / name, destination / name)
            if (old_translate / 'relationships.json').is_file():
                shutil.copyfile(old_translate / 'relationships.json', destination / 'relationships.json')
        relationship_snapshot = source / 'snapshots/relationships-v1.json'
        if relationship_snapshot.is_file():
            shutil.copyfile(relationship_snapshot, target.directory / 'snapshots/relationships-v1.json')
        v2_snapshot = source / 'snapshots/relationships-v2.json'
        if v2_snapshot.is_file():
            shutil.copyfile(v2_snapshot, target.directory / 'snapshots/relationships-v2.json')
    with target.verification_pass():
        for stage in stages:
            target.read(stage)
    if through not in {'analyze', 'inpaint'} and ((target.directory / 'cache').exists() or (target.directory / '06_analyze').exists()):
        raise RuntimeError('Fresh import unexpectedly contains analyze data')
    print(json.dumps({'status': 'preanalyze_imported', 'run': str(target.directory),
                      'reused': list(stages), 'analyze_cache_reused': False,
                      'analyze_artifact_reused': through == 'analyze'}), flush=True)
    return target


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--through', choices=['normalize', 'scan', 'analyze', 'inpaint'], default='scan')
    args = parser.parse_args()
    import_run(args.source.resolve(), manage.configuration(args.config), through=args.through)
