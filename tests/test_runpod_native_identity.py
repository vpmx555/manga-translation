"""Prevent a new batch from using an older batch's native daemon."""
from pathlib import Path
import socket
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'deployment/runpod'))
import native_sync


class NativeIdentityTests(unittest.TestCase):
    def test_other_batch_is_rejected_before_spawning(self):
        with patch.object(native_sync, 'api', return_value={'myID': 'old-device'}), \
                patch.object(native_sync.subprocess, 'Popen') as spawn:
            with self.assertRaisesRegex(RuntimeError, 'another batch/device'):
                native_sync.start_pc(Path('unused'), 'new-batch', 'new-device')
            spawn.assert_not_called()

    def test_same_device_can_be_reused(self):
        with patch.object(native_sync, 'api', return_value={'myID': 'same-device'}), \
                patch.object(native_sync.subprocess, 'Popen') as spawn:
            result = native_sync.start_pc(Path('unused'), 'batch', 'same-device')
            self.assertEqual(result['status'], 'already_running')
            spawn.assert_not_called()

    def test_existing_listener_is_preserved(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as occupied:
            occupied.bind(('127.0.0.1', 0))
            occupied.listen()
            port = occupied.getsockname()[1]
            if port == 65535:
                self.skipTest('OS selected last TCP port')
            self.assertNotEqual(native_sync.available_tcp_port(port), port)
            self.assertEqual(occupied.getsockname()[1], port)


if __name__ == '__main__':
    unittest.main()
