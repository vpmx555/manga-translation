"""Actual native file replication on loopback; no cloud/models."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'deployment/runpod'))
import native_sync


@unittest.skipUnless(os.environ.get('MANGA_TEST_NATIVE_SYNC') == '1' and os.name == 'nt'
                    and (native_sync.ROOT / 'syncthing.exe').exists(), 'Explicit native daemon integration opt-in required')
class NativePairTests(unittest.TestCase):
    def test_native_transfer_survives_configurator_exit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            a, b = root / 'a', root / 'b'
            aid, bid = native_sync.generate(a), native_sync.generate(b)
            source, copied = root / 'source', root / 'copied'
            native_sync.configure(a, aid, bid, [('fixture', source, 'sendonly')],
                                  gui_port=17984, peer_address='tcp://127.0.0.1:22902')
            native_sync.configure(b, bid, aid, [('fixture', copied, 'receiveonly')],
                                  gui_port=17985, peer_address='tcp://127.0.0.1:22901')
            for home, port in [(a, 22901), (b, 22902)]:
                tree = ET.parse(home / 'config.xml')
                options = tree.getroot().find('options')
                for node in list(options.findall('listenAddress')):
                    options.remove(node)
                ET.SubElement(options, 'listenAddress').text = f'tcp://127.0.0.1:{port}'
                for name in ['globalAnnounceEnabled', 'localAnnounceEnabled', 'relaysEnabled', 'natEnabled']:
                    node = options.find(name)
                    if node is None:
                        node = ET.SubElement(options, name)
                    node.text = 'false'
                tree.write(home / 'config.xml', encoding='utf-8', xml_declaration=True)
            processes = []
            try:
                for home in [a, b]:
                    processes.append(subprocess.Popen([str(native_sync.ROOT / 'syncthing.exe'), 'serve',
                        '--home', str(home), '--no-browser', '--no-upgrade', '--no-console', '--no-restart'],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        creationflags=subprocess.CREATE_NO_WINDOW))
                (source / 'canary.json').write_bytes(b'{"native":true}')
                deadline = time.monotonic() + 35
                while not (copied / 'canary.json').exists() and time.monotonic() < deadline:
                    time.sleep(0.5)
                self.assertEqual((copied / 'canary.json').read_bytes(), b'{"native":true}')
                self.assertTrue(native_sync.api(a, 'system/connections')['connections'][bid]['connected'])
            finally:
                for home in [a, b]:
                    try:
                        native_sync.api(home, 'system/shutdown', method='POST')
                    except OSError:
                        pass
                time.sleep(1)
                for process in processes:
                    if process.poll() is None:
                        process.terminate()
                for process in processes:
                    process.wait(timeout=10)


if __name__ == '__main__':
    unittest.main()
