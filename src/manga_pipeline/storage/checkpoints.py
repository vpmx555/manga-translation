"""Local working-set checkpoints and immutable transfer archives for Runpod."""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import uuid
import zipfile

from .io import atomic_json, file_hash


def archive_files(paths, local_root, durable, receipt):
    """Stream hashes while archiving locally; publish only a verified copy."""
    local_root, durable, receipt = Path(local_root), Path(durable), Path(receipt)
    temporary = local_root / 'archive-tmp' / (uuid.uuid4().hex + '.zip')
    temporary.parent.mkdir(parents=True, exist_ok=True)
    inventory = {}
    with zipfile.ZipFile(temporary,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=1) as zipped:
        for path,name in sorted(paths,key=lambda p:p[1]):
            path = Path(path)
            if path.is_symlink() or name in inventory or name == '__transfer_inventory__.json':
                raise ValueError('Unsafe/duplicate checkpoint entry')
            key = PurePosixPath(name)
            if key.is_absolute() or '..' in key.parts or '\\' in name or ':' in name:
                raise ValueError('Unsafe checkpoint name')
            before = path.stat()
            digest = hashlib.sha256()
            size = 0
            with path.open('rb') as incoming,zipped.open(name,'w',force_zip64=True) as outgoing:
                for block in iter(lambda:incoming.read(1024*1024),b''):
                    outgoing.write(block);digest.update(block);size += len(block)
            after = path.stat()
            if (before.st_size,before.st_mtime_ns) != (after.st_size,after.st_mtime_ns):
                raise RuntimeError('A checkpoint writer is still active')
            inventory[name] = {'bytes':size,'sha256':digest.hexdigest()}
        zipped.writestr('__transfer_inventory__.json',json.dumps({'format':1,'files':inventory},sort_keys=True))
    digest = file_hash(temporary)
    durable.mkdir(parents=True,exist_ok=True)
    target = durable / (digest + '.zip')
    if not target.exists():
        pending = target.with_suffix('.'+uuid.uuid4().hex+'.pending')
        shutil.copyfile(temporary,pending)
        if file_hash(pending) != digest:
            raise RuntimeError('Durable archive checksum mismatch')
        pending.replace(target)
    elif file_hash(target) != digest:
        raise RuntimeError('Durable archive changed')
    data = {'format':1,'archive':str(target),'archive_sha256':digest,
            'archive_bytes':temporary.stat().st_size,'files':len(inventory),
            'bytes':sum(v['bytes'] for v in inventory.values())}
    atomic_json(receipt,data)
    temporary.unlink()
    return data


def files_under(root, prefix=''):
    root = Path(root)
    return [(p,prefix+p.relative_to(root).as_posix()) for p in root.rglob('*')
            if p.is_file() and not p.is_symlink() and not p.name.endswith('.lock')]


def stage_checkpoint(store, step):
    durable = os.environ.get('MANGA_DURABLE_ROOT')
    batch = os.environ.get('MANGA_BATCH_ID')
    if not durable or not batch:
        return
    runtime = Path(os.environ['MANGA_RUNTIME']).resolve()
    directory = store.directory.resolve()
    directory.relative_to(runtime/'data/outputs')
    selected = {store.artifact_path(step).parent, directory/'snapshots'}
    paths = [(p,p.relative_to(runtime).as_posix()) for root in selected for p,_ in files_under(root)]
    for name in ('manifest.json','journal.jsonl'):
        path = directory/name
        if path.is_file():
            paths.append((path,path.relative_to(runtime).as_posix()))
    paths += [(p,p.relative_to(runtime).as_posix()) for p,_ in files_under(runtime/'data/banks')]
    folder = Path(durable)/'checkpoints'/batch/store.manifest['source']['chapter']/directory.name
    receipt = folder/(store.directories[step]+'.json')
    archive_files(paths,runtime,folder,receipt)
    print('DURABLE_STAGE '+step,flush=True)


def publish(runtime, durable, kind, *, story=None, chapter=None):
    runtime,durable = Path(runtime),Path(durable)
    source = runtime/'data'/kind
    folder = durable/'published'/kind
    if story:
        source = source/story/chapter
        folder = folder/story/chapter
    if not source.is_dir():
        raise RuntimeError('Local publication directory is missing: '+kind)
    return archive_files(files_under(source),runtime,folder,folder/'ready.json')


def restore_archive(archive, destination):
    """Restore exact bytes at the original canonical runtime, without rebasing."""
    destination = Path(destination).resolve()
    with zipfile.ZipFile(archive) as zipped:
        inventory = json.loads(zipped.read('__transfer_inventory__.json'))
        expected = inventory['files']
        names = [i.filename for i in zipped.infolist() if not i.is_dir()]
        if inventory.get('format') != 1 or len(names) != len(set(names)) or set(names) != {*expected,'__transfer_inventory__.json'}:
            raise ValueError('Checkpoint inventory mismatch')
        for name,receipt in expected.items():
            key = PurePosixPath(name)
            if key.is_absolute() or '..' in key.parts or '\\' in name or ':' in name:
                raise ValueError('Unsafe checkpoint restore path')
            target = destination.joinpath(*key.parts)
            target.resolve().relative_to(destination)
            target.parent.mkdir(parents=True,exist_ok=True)
            pending = target.with_suffix(target.suffix+'.restoring')
            digest,size = hashlib.sha256(),0
            with zipped.open(name) as incoming,pending.open('wb') as outgoing:
                for block in iter(lambda:incoming.read(1024*1024),b''):
                    outgoing.write(block);digest.update(block);size += len(block)
            if size != receipt['bytes'] or digest.hexdigest() != receipt['sha256']:
                raise RuntimeError('Restored checkpoint checksum mismatch')
            pending.replace(target)
