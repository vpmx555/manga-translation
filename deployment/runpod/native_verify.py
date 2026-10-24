"""Verify native replicas without transporting data or controlling Syncthing."""
import argparse
import json
import os
from pathlib import Path
import sys
import time
import zipfile

import manage


def local_path(path):
    """Allow direct access to long native-replica paths on Windows."""
    path = Path(path).resolve()
    value = str(path)
    if os.name == 'nt' and not value.startswith('\\\\?\\'):
        return Path('\\\\?\\' + value)
    return path


def verify(plan_path):
    plan = manage.read_json(plan_path)
    if plan.get('kind') == 'ocr-diagnostic':
        from ocr_diagnostic import verify as verify_diagnostic
        return verify_diagnostic(plan_path)
    receipt = manage.read_json(plan_path.parent / 'native-sync.json')
    destination = Path(receipt['destination'])
    durable = destination / 'durable' / plan['batch']
    state = manage.read_json(durable / 'jobs/state.json', {})
    if state.get('status') not in {'completed', 'completed_with_review', 'completed_with_errors', 'interrupted'}:
        return None
    if 'bank_publication' not in state or 'audit_publication' not in state:
        return None
    receipts = list((durable / 'published').rglob('ready.json'))
    verified = []
    for path in receipts:
        ready = manage.read_json(path)
        remote = Path(ready['archive'])
        remote.relative_to(Path(plan['durable_root']) / plan['batch'])
        archive = local_path(destination / 'durable' /
                             remote.relative_to(Path(plan['durable_root'])))
        if not archive.is_file() or archive.stat().st_size != ready['archive_bytes']:
            return None
        if manage.file_hash(archive) != ready['archive_sha256']:
            return None  # Native replication may still be replacing this file.
        with zipfile.ZipFile(archive) as zipped:
            if zipped.testzip():
                raise RuntimeError('Replica archive CRC mismatch: ' + str(archive))
        verified.append({'receipt': str(path), 'archive': str(archive),
                         'sha256': ready['archive_sha256'], 'bytes': ready['archive_bytes']})
    expected = [r.get('publication') for r in state.get('chapters', []) if r.get('publication')]
    expected.extend([state['bank_publication'], state['audit_publication']])
    hashes = {r['sha256'] for r in verified}
    if any(r['archive_sha256'] not in hashes for r in expected):
        return None
    if len(state.get('chapters', [])) != len(plan['chapters']):
        return None
    return {'status': 'replica_archives_verified', 'batch': plan['batch'], 'verified_at': time.time(),
            'pipeline_status': state['status'], 'chapter_statuses': [
                {'story': r['story'], 'chapter': r['chapter'], 'status': r['status'],
                 'summary': r.get('summary'), 'error': r.get('error')} for r in state['chapters']],
            'archives': verified, 'native_transfer': True, 'agent_collector_required': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--wait', action='store_true')
    args = parser.parse_args()
    if manage.read_json(args.plan).get('kind') == 'ocr-diagnostic':
        while True:
            report = verify(args.plan)
            if report:
                receipt = manage.read_json(args.plan.parent / 'native-sync.json')
                manage.atomic_json(Path(receipt['destination']) / 'verification.json', report)
                print(json.dumps({'status': report['status'], 'coverage': report.get('coverage')}), flush=True)
                return
            if not args.wait:
                print('{"status":"replication_or_diagnostic_pending"}')
                return
            time.sleep(30)
    receipt = manage.read_json(args.plan.parent / 'native-sync.json')
    from local_review import start_watch
    start_watch(Path(receipt['destination']), install_logon=True)
    while True:
        report = verify(args.plan)
        if report:
            receipt = manage.read_json(args.plan.parent / 'native-sync.json')
            from local_review import generate
            reviews = generate(Path(receipt['destination']), plan_path=args.plan)
            report['local_reviews'] = {'index': reviews['review'], 'runs': len(reviews['runs']),
                                      'pending': len(reviews['pending']), 'errors': reviews['errors']}
            manage.atomic_json(Path(receipt['destination']) / 'verification.json', report)
            print(json.dumps({'status': report['status'], 'chapters': len(report['chapter_statuses'])}), flush=True)
            return
        if not args.wait:
            print('{"status":"replication_or_pipeline_pending"}')
            return
        time.sleep(30)


if __name__ == '__main__':
    main()
