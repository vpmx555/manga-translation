"""Extract one run from a verified native archive for offline review."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import zipfile


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--batch', type=Path, required=True)
    p.add_argument('--run-id', required=True)
    p.add_argument('--output', type=Path)
    a = p.parse_args()
    if not re.fullmatch(r'\d{8}T\d{6}Z-[0-9a-f]{8}', a.run_id):
        raise ValueError('Invalid run ID')
    batch = a.batch.resolve()
    report = json.loads((batch/'verification.json').read_text(encoding='utf-8-sig'))
    if report.get('status') != 'replica_archives_verified':
        raise ValueError('Native backup has not been verified')
    output = (a.output or batch/'local-verified').resolve()
    prefix = a.run_id+'/'
    matches = []
    for receipt in report['archives']:
        if '/published/outputs/' not in receipt['receipt'].replace('\\', '/'):
            continue
        with zipfile.ZipFile(receipt['archive']) as archive:
            if prefix+'manifest.json' in archive.namelist():
                matches.append(receipt)
    if len(matches) != 1:
        raise ValueError('Expected exactly one authenticated archive containing the run')
    receipt = matches[0]
    if digest(receipt['archive']) != receipt['sha256']:
        raise ValueError('Verified archive changed')
    count = 0
    with zipfile.ZipFile(receipt['archive']) as archive:
        inventory = json.loads(archive.read('__transfer_inventory__.json'))
        entries = [x.filename for x in archive.infolist() if not x.is_dir()]
        if inventory.get('format') != 1 or len(entries) != len(set(entries)) or set(entries) != {*inventory['files'], '__transfer_inventory__.json'}:
            raise ValueError('Checkpoint inventory mismatch')
        for name, expected in inventory['files'].items():
            if not name.startswith(prefix):
                continue
            key = PurePosixPath(name)
            if key.is_absolute() or '..' in key.parts or '\\' in name or ':' in name:
                raise ValueError('Unsafe archive path')
            target = output.joinpath(*key.parts)
            target.resolve().relative_to(output)
            if target.exists():
                if target.stat().st_size != expected['bytes'] or digest(target) != expected['sha256']:
                    raise ValueError('Refusing to overwrite a changed local review file: '+str(target))
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                pending = target.with_suffix(target.suffix+'.restoring')
                h, size = hashlib.sha256(), 0
                with archive.open(name) as source, pending.open('wb') as dest:
                    for block in iter(lambda: source.read(1024*1024), b''):
                        dest.write(block)
                        h.update(block)
                        size += len(block)
                if size != expected['bytes'] or h.hexdigest() != expected['sha256']:
                    pending.unlink()
                    raise ValueError('Extracted checkpoint checksum mismatch')
                pending.replace(target)
            count += 1
    print(json.dumps({'run': str(output/a.run_id), 'files': count,
        'archive_sha256': receipt['sha256'], 'model_requests': 0}))


if __name__ == '__main__':
    main()
