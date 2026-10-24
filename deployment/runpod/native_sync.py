"""Native Syncthing replication: data survives agent/controller disconnection.

Only configuration and the initial launch are controlled here. The native daemon
owns every byte transfer; no Python collector is required while the queue runs.
Credentials are stored outside source bundles and replicated data directories.
"""
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import time
import urllib.request
import xml.etree.ElementTree as ET

import manage
import native_transport as sync
from prepare_native_sync import ROOT, ASSETS


def api(home, endpoint, *, method='GET'):
    tree = ET.parse(home / 'config.xml')
    gui = tree.getroot().find('gui')
    address, key = gui.findtext('address'), gui.findtext('apikey')
    request = urllib.request.Request('http://' + address + '/rest/' + endpoint,
                                     headers={'X-API-Key': key}, method=method)
    with urllib.request.urlopen(request, timeout=10) as response:
        body = response.read()
        return json.loads(body) if body else None


def generate(home):
    home.mkdir(parents=True, exist_ok=True)
    if not (home / 'config.xml').exists():
        subprocess.run([str(ROOT / 'syncthing.exe'), 'generate', '--home', str(home),
                        '--no-port-probing'], check=True, capture_output=True,
                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    return ET.parse(home / 'config.xml').getroot().find('device').get('id')


def configure(home, own, peer, folders, *, gui_port, peer_address='dynamic', listen_port=None):
    tree = ET.parse(home / 'config.xml')
    root = tree.getroot()
    for item in list(root.findall('folder')) + list(root.findall('device')):
        root.remove(item)
    for ident, name, address in [(own, 'local', 'dynamic'), (peer, 'paired', peer_address)]:
        device = ET.SubElement(root, 'device', {'id': ident, 'name': name, 'compression': 'always',
                                               'introducer': 'false', 'autoAcceptFolders': 'false'})
        ET.SubElement(device, 'address').text = address
        if address != 'dynamic':
            ET.SubElement(device, 'address').text = 'dynamic'
    for ident, path, kind in folders:
        wire_path = str(path)  # Preserve Linux paths while configuring on Windows.
        path = Path(path)
        if os.name == 'nt' and ':' in str(path):
            path.mkdir(parents=True, exist_ok=True)
        folder = ET.SubElement(root, 'folder', {'id': ident, 'label': ident, 'path': wire_path,
                    'type': kind, 'rescanIntervalS': '30', 'fsWatcherEnabled': 'true',
                    'fsWatcherDelayS': '1', 'ignorePerms': 'true', 'autoNormalize': 'true'})
        for device in [own, peer]:
            ET.SubElement(folder, 'device', {'id': device})
        ET.SubElement(folder, 'order').text = 'alphabetic'
        ET.SubElement(folder, 'ignoreDelete').text = 'true' if kind == 'receiveonly' else 'false'
        ET.SubElement(folder, 'markerName').text = '.stfolder'
        # Output replacement is versioned locally; remote deletions never erase backup.
        if kind == 'receiveonly':
            versioning = ET.SubElement(folder, 'versioning', {'type': 'simple'})
            ET.SubElement(versioning, 'param', {'key': 'keep', 'val': '3'})
    gui = root.find('gui')
    gui.set('enabled', 'true')
    gui.set('tls', 'false')
    gui.find('address').text = f'127.0.0.1:{gui_port}'
    gui.find('apikey').text = secrets.token_hex(32)
    options = root.find('options')
    if listen_port is not None:
        for node in list(options.findall('listenAddress')):
            options.remove(node)
        ET.SubElement(options, 'listenAddress').text = f'tcp://0.0.0.0:{listen_port}'
    for field, value in [('urAccepted', '-1'), ('startBrowser', 'false'), ('autoUpgradeIntervalH', '0')]:
        node = options.find(field)
        if node is None:
            node = ET.SubElement(options, field)
        node.text = value
    tree.write(home / 'config.xml', encoding='utf-8', xml_declaration=True)


def available_tcp_port(preferred):
    """Choose a batch port without changing daemons owned by other batches."""
    for port in range(preferred, min(preferred + 100, 65536)):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            try:
                probe.bind(('127.0.0.1', port))
            except OSError:
                continue
            return port
    raise RuntimeError('No free native sync port near ' + str(preferred))


def prepare(plan_path):
    """Prepare native identity/config before a GPU is rented; no Runpod access."""
    plan = manage.read_json(plan_path)
    batch = plan['batch']
    private = Path(os.environ['LOCALAPPDATA']) / 'manga-runpod-sync' / batch
    destination = sync.PROJECT / 'outputs' / ('runpod-' + batch)
    previous = manage.read_json(plan_path.parent / 'native-sync.json')
    if previous:
        if (Path(previous['private_home']) != private or Path(previous['destination']) != destination
                or generate(private / 'pc') != previous['pc_device']
                or generate(private / 'pod') != previous['pod_device']):
            raise RuntimeError('Existing native pairing does not match this batch')
        if plan.get('kind') != 'ocr-diagnostic':
            from local_review import start_watch
            previous['local_review'] = start_watch(destination, install_logon=True)
        manage.atomic_json(plan_path.parent / 'native-sync.json', previous)
        return previous
    pc, pod = private / 'pc', private / 'pod'
    own, peer = generate(pc), generate(pod)
    inputs = plan_path.parent
    folders_pc = [(batch + '-input', inputs, 'sendonly'),
                  (batch + '-live', destination / 'live', 'receiveonly'),
                  (batch + '-durable', destination / 'durable', 'receiveonly')]
    folders_pod = [(batch + '-input', plan['native_ingress'], 'receiveonly'),
                   (batch + '-live', plan['runtime'] + '/data', 'sendonly'),
                   (batch + '-durable', plan['durable_root'], 'sendonly')]
    gui_port, listen_port = available_tcp_port(17884), available_tcp_port(22000)
    configure(pc, own, peer, folders_pc, gui_port=gui_port, listen_port=listen_port)
    configure(pod, peer, own, folders_pod, gui_port=17885)
    for _, path, _ in folders_pc:
        (Path(path) / '.stignore').write_text('**/*.tmp\n**/*.pending\n**/*.lock\ninput\n', encoding='utf-8')
    receipt = {'private_home': str(private), 'destination': str(destination),
               'pc_device': own, 'pod_device': peer, 'gui_port': gui_port,
               'listen_port': listen_port, 'folders': [f[0] for f in folders_pc]}
    manage.atomic_json(plan_path.parent / 'native-sync.json', receipt)
    if plan.get('kind') != 'ocr-diagnostic':
        from local_review import start_watch
        receipt['local_review'] = start_watch(destination, install_logon=True)
    manage.atomic_json(plan_path.parent / 'native-sync.json', receipt)
    return receipt


def start_pc(home, batch, expected_device):
    binary = ROOT / 'syncthing.exe'
    args = ['serve', '--home', str(home), '--no-browser', '--no-upgrade', '--no-console',
            '--log-file', str(home / 'syncthing.log')]
    try:
        status = api(home, 'system/status')
    except (OSError, ValueError):
        pass
    else:
        if status.get('myID') != expected_device:
            raise RuntimeError('Native GUI port belongs to another batch/device; refusing to reuse it')
        return {'status': 'already_running', 'device': expected_device}
    child = subprocess.Popen([str(binary), *args], stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             creationflags=subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS)
    # Register native executable directly: no agent/Python dependency at next login.
    quote = lambda s: "'" + str(s).replace("'", "''") + "'"
    arguments = subprocess.list2cmdline(args)
    script = ('$a=New-ScheduledTaskAction -Execute ' + quote(binary) + ' -Argument ' + quote(arguments) + ';'
              '$t=New-ScheduledTaskTrigger -AtLogOn;'
              '$s=New-ScheduledTaskSettingsSet -Hidden -RestartCount 99 -RestartInterval (New-TimeSpan -Minutes 1) '
              '-ExecutionTimeLimit ([TimeSpan]::Zero);'
              'Register-ScheduledTask -TaskName ' + quote('MangaSync-' + batch) +
              ' -Action $a -Trigger $t -Settings $s -Force | Out-Null')
    task = subprocess.run(['powershell.exe', '-NoProfile', '-WindowStyle', 'Hidden', '-Command', script],
                          capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
    return {'status': 'started', 'pid': child.pid, 'logon_task_registered': task.returncode == 0}


def activate(plan_path, connection):
    """Pair/test native service, then leave it running when this process exits."""
    plan = manage.read_json(plan_path)
    receipt_path = plan_path.parent / 'native-sync.json'
    receipt = manage.read_json(receipt_path)
    if not receipt:
        raise RuntimeError('Run native-sync prepare before provisioning')
    private = Path(receipt['private_home'])
    if connection.get('syncthing_address'):
        tree = ET.parse(private / 'pc/config.xml')
        device = next(d for d in tree.getroot().findall('device') if d.get('id') == receipt['pod_device'])
        for address in list(device.findall('address')):
            device.remove(address)
        ET.SubElement(device, 'address').text = connection['syncthing_address']
        ET.SubElement(device, 'address').text = 'dynamic'
        tree.write(private / 'pc/config.xml', encoding='utf-8', xml_declaration=True)
        # prepare() starts the PC daemon before the pod address is known.  It
        # must reload the newly pinned address or a Secure Cloud peer behind
        # NAT may never connect, leaving the canary pending indefinitely.
        try:
            current = api(private / 'pc', 'system/status')
            if current.get('myID') != receipt['pc_device']:
                raise RuntimeError('Native PC device identity changed')
            api(private / 'pc', 'system/shutdown', method='POST')
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                try:
                    api(private / 'pc', 'system/status')
                except OSError:
                    break
                time.sleep(.25)
        except OSError:
            pass
    staging = '/workspace/manga-native-sync/' + plan['batch']
    sync.remote(connection, ['mkdir', '-p', staging])
    for name in ['config.xml', 'cert.pem', 'key.pem']:
        sync.upload(connection, private / 'pod' / name, staging + '/' + name)
    sync.upload(connection, plan_path, staging + '/plan.json')
    asset = 'syncthing-linux-amd64-v2.1.6.tar.gz'
    script = r'''import hashlib,json,os,pathlib,subprocess,sys,tarfile,time,urllib.request
home=pathlib.Path(sys.argv[1]);plan=json.loads((home/'plan.json').read_text());expected=sys.argv[2]
os.chmod(home,0o700);os.chmod(home/'key.pem',0o600)
binary=home/'syncthing'
if not binary.exists() or binary.read_bytes()[:4] != b'\x7fELF':
 request=urllib.request.Request('https://github.com/syncthing/syncthing/releases/download/v2.1.6/syncthing-linux-amd64-v2.1.6.tar.gz',headers={'User-Agent':'manga-native-sync'})
 with urllib.request.urlopen(request,timeout=120) as r:data=r.read()
 if hashlib.sha256(data).hexdigest()!=expected:raise RuntimeError('Native binary digest mismatch')
 archive=home/'native.tar.gz';archive.write_bytes(data)
 with tarfile.open(archive) as t:
  m=next(m for m in t.getmembers() if len(pathlib.PurePosixPath(m.name).parts)==2 and m.name.endswith('/syncthing') and m.isfile())
  with t.extractfile(m) as f:binary.write_bytes(f.read())
 if binary.read_bytes()[:4]!=b'\x7fELF':raise RuntimeError('Native Linux binary is not ELF')
 binary.chmod(0o755);archive.unlink()
for name in [plan['native_ingress'],plan['runtime']+'/data',plan['durable_root']]:
 path=pathlib.Path(name);path.mkdir(parents=True,exist_ok=True)
 (path/'.stignore').write_text('**/*.tmp\n**/*.pending\n**/*.lock\ninput\n')
 (path/'.stfolder').mkdir(exist_ok=True)
with (home/'daemon.log').open('ab') as log:
 p=subprocess.Popen([str(binary),'serve','--home',str(home),'--no-browser','--no-upgrade'],stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
path=pathlib.Path(plan['durable_root'])/plan['batch']/'native-canary.json'
path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps({'batch':plan['batch'],'native':True,'at':time.time()}))
print(json.dumps({'native_pid':p.pid,'canary':str(path)}))'''
    remote = json.loads(sync.remote(connection, ['python3', '-c', script, staging,
                                                  ASSETS[asset]], capture=True))
    local = start_pc(private / 'pc', plan['batch'], receipt['pc_device'])
    deadline = time.monotonic() + 240
    canary = Path(receipt['destination']) / 'durable' / plan['batch'] / 'native-canary.json'
    while time.monotonic() < deadline:
        if canary.is_file() and manage.read_json(canary, {}).get('batch') == plan['batch']:
            receipt.update(activated_at=time.time(), remote=remote, local=local,
                           canary_verified=True)
            manage.atomic_json(receipt_path, receipt)
            return receipt
        time.sleep(5)
    raise RuntimeError('Native backup handshake/canary not verified; inference was not launched')


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['prepare', 'status'])
    parser.add_argument('--plan', type=Path, required=True)
    args = parser.parse_args()
    if args.command == 'prepare':
        result = prepare(args.plan.resolve())
        print(json.dumps({'destination': result['destination'], 'status': 'native_prepared'}))
    else:
        receipt = manage.read_json(args.plan.parent / 'native-sync.json')
        home = Path(receipt['private_home']) / 'pc'
        if api(home, 'system/status').get('myID') != receipt['pc_device']:
            raise RuntimeError('Native GUI port belongs to another batch/device')
        status = api(home, 'system/connections')
        peer = receipt['pod_device']
        print(json.dumps({'connected': status.get('connections', {}).get(peer, {}).get('connected'),
            'folders': {folder: api(home, 'db/status?folder=' + folder)
                        for folder in receipt['folders']}}))


if __name__ == '__main__':
    main()
