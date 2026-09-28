#!/usr/bin/env python3
# Tests for the bridge's small logger (bridgelog.py) and its argument
# parsing, which replaced the logging and argparse modules to save RAM.

import io
import os
import socket
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
BRIDGE_DIR = os.path.join(HERE, '..', 'feed', 'yun-bridge', 'files', 'usr', 'lib', 'yun-bridge')
sys.path.insert(0, BRIDGE_DIR)

import bridge  # noqa: E402
import bridgelog  # noqa: E402


class BridgeLogTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, 'log')
        self.syslog = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        self.syslog.bind(self.path)
        self.syslog.settimeout(1)
        self.addCleanup(self.syslog.close)
        self.out = io.StringIO()
        self.addCleanup(bridgelog.setup, False, sys.stderr, None)
        self.log = bridgelog.getLogger('bridge')

    def test_info_goes_to_stderr_and_syslog(self):
        bridgelog.setup(False, self.out, self.path)
        self.log.info('connect to %s:%d failed: %s', 'example.com', 80, 'refused')
        self.assertRegex(self.out.getvalue(),
                         r'^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d INFO connect to example.com:80 failed: refused\n$')
        self.assertEqual(self.syslog.recv(1000), b'<14>bridge: connect to example.com:80 failed: refused')

    def test_debug_only_when_asked(self):
        bridgelog.setup(False, self.out, None)
        self.log.debug('dropping packet with bad CRC')
        self.assertEqual(self.out.getvalue(), '')
        bridgelog.setup(True, self.out, None)
        self.log.debug('dropping packet with bad CRC')
        self.assertIn('DEBUG dropping packet with bad CRC', self.out.getvalue())

    def test_exception_has_the_traceback(self):
        bridgelog.setup(False, self.out, self.path)
        try:
            {}['x']
        except KeyError:
            self.log.exception('command %r failed', b'R')
        self.assertIn("ERROR command b'R' failed\nTraceback", self.out.getvalue())
        self.assertIn('KeyError', self.out.getvalue())
        self.assertTrue(self.syslog.recv(4000).startswith(b"<11>bridge: command b'R' failed\nTraceback"))

    def test_bad_format_args_dont_crash(self):
        bridgelog.setup(False, self.out, None)
        self.log.warning('%d things', 'not a number')
        self.assertIn("WARNING %d things ('not a number',)", self.out.getvalue())

    def test_no_syslog_socket(self):
        bridgelog.setup(False, self.out, os.path.join(self.tmp.name, 'missing'))
        self.log.warning('still works')
        self.assertIn('still works', self.out.getvalue())


class ArgsTest(unittest.TestCase):
    def test_defaults(self):
        self.assertEqual(bridge.parse_args([]), (False, 5700, 6571))

    def test_options(self):
        self.assertEqual(bridge.parse_args(['--debug', '--mailbox-port', '1234', '--console-port=99']),
                         (True, 1234, 99))

    def test_bad_arguments(self):
        for args in (['--bogus'], ['--mailbox-port'], ['--console-port', 'x']):
            p = subprocess.run([sys.executable, os.path.join(BRIDGE_DIR, 'bridge.py'), *args],
                               capture_output=True, text=True, timeout=10)
            self.assertEqual(p.returncode, 1, args)
            self.assertIn('usage: bridge.py', p.stderr)

    def test_heavy_modules_stay_unloaded(self):
        code = ('import sys; sys.path.insert(0, %r); import bridge; bridge.build_processor(0, 0); '
                'print(sorted(m for m in ("logging", "argparse", "subprocess", "pickle") if m in sys.modules))'
                % BRIDGE_DIR)
        p = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, timeout=10)
        self.assertEqual(p.stdout.strip(), '[]', p.stderr)


if __name__ == '__main__':
    unittest.main()
