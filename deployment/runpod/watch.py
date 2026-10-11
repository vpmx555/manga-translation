"""Read-only, bounded monitoring of a managed chapter batch over SSH."""
import argparse
from pathlib import Path
import sys

import sync


REMOTE = r'''
from pathlib import Path
import json, sys, time
directory = Path('/workspace/manga-runtime/jobs') / sys.argv[1]
def snapshot(counters=False):
    path = directory / 'state.json'
    if not path.exists():
        launch = directory / 'launch.json'
        if launch.exists():
            sys.path.insert(0, '/workspace/manga-translate-v2/deployment/runpod')
            import manage
            record = json.loads(launch.read_text())
            identity = record.get('identity')
            alive = identity and manage.service_identity(identity['pid']) == identity
            log = directory / 'driver.log'
            return {'status': 'starting' if alive else 'failed',
                    'log_tail': log.read_text(errors='replace')[-3500:] if log.exists() else ''}
        return {'status': 'preflight'}
    state = json.loads(path.read_text())
    identity = state.get('pid')
    driver_alive = None
    if identity:
        sys.path.insert(0, '/workspace/manga-translate-v2/deployment/runpod')
        import manage
        driver_alive = manage.service_identity(identity['pid']) == identity
    current = state.get('current', 0)
    records = state.get('chapters', [])
    result = {'status': state['status'], 'current': current + 1,
              'driver_alive': driver_alive,
              'completed': sum(r.get('status') == 'completed' for r in records),
              'failed': sum(r.get('status') == 'failed' for r in records),
              'total': len(state['plan']['chapters'])}
    if state['status'] == 'running' and driver_alive is False:
        result.update(status='interrupted', stale_state=True)
    if records:
        result['chapter'] = records[current]['story'] + ' / ' + records[current]['chapter']
        result['phase'] = records[current].get('phase', 'inference')
        result['summaries'] = [r.get('summary') for r in records if r.get('summary')]
        entry = state['plan']['chapters'][current]
        base = Path('/workspace/manga-runtime/data/outputs') / entry['story_key'] / entry['chapter']
        manifests = sorted(base.glob('*/manifest.json'))
        if manifests:
            manifest = json.loads(manifests[-1].read_text())
            result['run'] = manifests[-1].parent.name
            result['steps'] = {key: value['status'] for key, value in manifest['steps'].items()}
            if counters:
                run = manifests[-1].parent
                active = [key for key, value in manifest['steps'].items() if value['status'] == 'running']
                if active:
                    step = active[-1]
                    if step == 'translate' and manifest.get('pipeline_version') != 6:
                        revisions = sorted((run / 'translations/vi').glob('*/targets'))
                        if revisions:
                            result['target_receipts'] = {style: len(list((revisions[-1] / style).glob('*.json')))
                                                        for style in ('natural', 'localized')}
                    else:
                        folders = list(run.glob('*_' + step))
                        if folders:
                            result['target_receipts'] = len(list((folders[0] / 'targets').glob('*.json')))
    if state['status'] in ('failed', 'interrupted', 'completed_with_errors'):
        result['error'] = state.get('error')
        log = directory / ('chapter-%02d.log' % (current + 1))
        result['log_tail'] = log.read_text(errors='replace')[-3500:] if log.exists() else ''
    return result
start = time.monotonic()
initial = snapshot()
while initial['status'] == 'running' and time.monotonic() - start < int(sys.argv[2]):
    time.sleep(3)
    now = snapshot()
    if now != initial:
        initial = now
        break
print(json.dumps(snapshot(counters=True), ensure_ascii=False))
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('batch')
    parser.add_argument('--wait', type=int, default=0)
    args = parser.parse_args()
    if not args.batch or any(c not in 'abcdefghijklmnopqrstuvwxyz0123456789-_' for c in args.batch):
        parser.error('Unsafe batch identifier')
    if not 0 <= args.wait <= 45:
        parser.error('Wait must be between 0 and 45 seconds')
    print(sync.remote(sync.load_connection(), ['python3', '-c', REMOTE, args.batch, str(args.wait)], capture=True).strip())


if __name__ == '__main__':
    main()
