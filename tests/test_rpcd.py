#!/usr/bin/env python3
# Tests for the Yun Panel's rpcd plugin (yun.uc), run under a host build of
# ucode with stand-ins for the uci and ubus modules and for the commands it
# runs (tests/rpcd/). Set UCODE_BUILD to a ucode build directory; the tests
# are skipped without one.
#
#   git clone https://github.com/jow-/ucode && cd ucode && mkdir build && cd build
#   cmake .. -DUCI_SUPPORT=OFF -DUBUS_SUPPORT=OFF -DULOOP_SUPPORT=OFF \
#     -DRTNL_SUPPORT=OFF -DNL80211_SUPPORT=OFF && make

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, '..')
PANEL = os.path.join(ROOT, 'feed', 'yun-webpanel', 'files', 'usr', 'share')
UCODE = os.environ.get('UCODE_BUILD', '/home/user/ucode/build')

UCI = {
    'system': {'@system[0]': {'hostname': 'workbench-yun', 'zonename': 'Australia/Sydney'}},
    'arduino': {'@arduino[0]': {'wifi_state': 'client', 'secure_rest_api': 'true'}},
    'wireless': {'yun_sta': {'ssid': 'Workshop'}, 'yun_ap': {'ssid': 'Arduino Yun-90A2DAF054D2'}},
}

UBUS = {
    'network.wireless status': {'radio0': {'interfaces': [
        {'section': 'yun_sta', 'ifname': 'lo'},
    ]}},
    'network.interface.wwan status': {'ipv4-address': [{'address': '192.168.1.45', 'mask': 24}]},
    'network.interface.wan status': {
        'l3_device': 'lo',
        'ipv4-address': [{'address': '192.168.1.135', 'mask': 24}],
        'route': [{'target': '0.0.0.0', 'mask': 0, 'nexthop': '192.168.1.1'}],
    },
}


@unittest.skipUnless(os.path.exists(os.path.join(UCODE, 'ucode')), 'no ucode build (set UCODE_BUILD)')
class RpcdPluginTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        t = self.tmp.name
        self.env = dict(os.environ,
                        LD_LIBRARY_PATH=UCODE,
                        PATH=os.path.join(HERE, 'rpcd', 'bin') + os.pathsep + os.environ['PATH'],
                        UCI_FIXTURE=os.path.join(t, 'uci.json'),
                        UCI_LOG=os.path.join(t, 'uci-log.json'),
                        UBUS_FIXTURE=os.path.join(t, 'ubus.json'),
                        CALL_LOG=os.path.join(t, 'calls.log'))
        with open(self.env['UCI_FIXTURE'], 'w') as f:
            json.dump(UCI, f)
        with open(self.env['UBUS_FIXTURE'], 'w') as f:
            json.dump(UBUS, f)

    def call(self, method, args=None):
        script = ('let m = require("yun").yun; '
                  f'let r = m[{json.dumps(method)}].call({{ args: {json.dumps(args or {})} }}); '
                  'print(sprintf("%J", r));')
        p = subprocess.run(
            [os.path.join(UCODE, 'ucode'),
             '-L', os.path.join(UCODE, '*.so'),
             '-L', os.path.join(HERE, 'rpcd', 'modules', '*.uc'),
             '-L', os.path.join(PANEL, 'ucode', '*.uc'),
             '-L', os.path.join(PANEL, 'rpcd', 'ucode', '*.uc'),
             '-e', script],
            env=self.env, capture_output=True, text=True, timeout=30)
        self.assertEqual(p.returncode, 0, p.stderr)
        return json.loads(p.stdout)

    def calls(self):
        time.sleep(2.5)     # spawn_later() runs commands after a delay
        try:
            with open(self.env['CALL_LOG']) as f:
                return f.read().splitlines()
        except FileNotFoundError:
            return []

    def test_status(self):
        s = self.call('status')
        self.assertEqual(s['hostname'], 'workbench-yun')
        self.assertEqual(s['zonename'], 'Australia/Sydney')
        self.assertTrue(s['rest_secure'])
        self.assertEqual(len(s['load']), 3)
        self.assertGreater(s['memory']['total'], 0)
        w = s['wifi']
        self.assertEqual(w['mode'], 'client')
        self.assertEqual(w['ssid'], 'Workshop')
        self.assertTrue(w['connected'])
        self.assertEqual(w['signal'], -61)
        self.assertEqual(w['quality'], 70)
        self.assertEqual(w['channel'], 6)
        self.assertEqual(w['encryption'], 'psk2')
        self.assertEqual(w['ipv4'], '192.168.1.45')
        self.assertEqual(s['ethernet']['ipv4'], '192.168.1.135')
        self.assertEqual(s['ethernet']['gateway'], '192.168.1.1')
        self.assertEqual(s['storage'][0]['name'], 'Internal flash')
        self.assertEqual(s['storage'][0]['total'], 5504 * 1024)
        self.assertTrue(s['storage'][1]['name'].startswith('SD card'))

    def test_scan(self):
        r = self.call('wifi_scan')['results']
        by = {n['ssid']: n for n in r}
        self.assertEqual(by['Workshop']['encryption'], 'psk2')
        self.assertEqual(by['Workshop']['quality'], 70)
        self.assertEqual(by['Cafe <b>Guest</b>']['encryption'], 'none')
        self.assertEqual(by['Newer']['encryption'], 'sae-mixed')
        self.assertNotIn('Office', by)          # 802.1X can't be joined from the panel
        self.assertEqual(len(r), 3)             # hidden network skipped too

    def test_wifi_client_validates_and_passes_args_safely(self):
        self.assertIn('error', self.call('wifi_client', {'ssid': '', 'encryption': 'psk2', 'key': 'x' * 8}))
        self.assertIn('error', self.call('wifi_client', {'ssid': 'a', 'encryption': 'wep', 'key': 'x' * 8}))
        self.assertIn('error', self.call('wifi_client', {'ssid': 'a', 'encryption': 'psk2', 'key': 'short'}))
        ssid = 'a"b; $(touch /tmp/pwned) `id`'
        self.assertEqual(self.call('wifi_client', {'ssid': ssid, 'encryption': 'psk2', 'key': "pa ss'word"}),
                         {'ok': True})
        self.assertEqual(self.calls(), [f"yun-wifi client {ssid} psk2 pa ss'word"])
        self.assertFalse(os.path.exists('/tmp/pwned'))

    def test_sketch_flash(self):
        self.assertIn('error', self.call('sketch_flash'))
        with open('/tmp/sketch.hex', 'w') as f:
            f.write(':00000001FF\n')
        r = self.call('sketch_flash')
        self.assertEqual(r['code'], 0)
        self.assertIn('verified', r['output'])
        with open(self.env['CALL_LOG']) as f:
            log = f.read().splitlines()
        self.assertEqual(log, ['merge-sketch-with-bootloader.lua /tmp/sketch.hex', 'kill-bridge ',
                               'run-avrdude /tmp/sketch.hex -q -q'])
        self.assertFalse(os.path.exists('/tmp/sketch.hex'))

    def test_sketch_flash_failure(self):
        with open('/tmp/sketch.hex', 'w') as f:
            f.write(':00000001FF\n')
        self.env['AVRDUDE_EXIT'] = '1'
        self.assertEqual(self.call('sketch_flash')['code'], 1)

    def test_settings(self):
        self.assertIn('error', self.call('settings_set', {'hostname': 'bad name!'}))
        self.assertIn('error', self.call('settings_set', {'zonename': 'Mars/Olympus'}))
        self.assertEqual(self.call('settings_set', {'hostname': 'yun2', 'zonename': 'Europe/London',
                                                    'rest_secure': False}), {'ok': True})
        with open(self.env['UCI_LOG']) as f:
            log = json.load(f)
        self.assertIn(['system', '@system[0]', 'hostname', 'yun2'], log)
        self.assertIn(['system', '@system[0]', 'timezone', 'GMT0BST,M3.5.0/1,M10.5.0'], log)
        self.assertIn(['arduino', '@arduino[0]', 'secure_rest_api', 'false'], log)

    def test_password(self):
        self.assertIn('error', self.call('password_set', {'password': 'short'}))
        self.assertIn('error', self.call('password_set', {'password': 'two\nlines!!'}))
        self.assertEqual(self.call('password_set', {'password': 'correct horse'}), {'ok': True})
        with open(self.env['CALL_LOG']) as f:
            self.assertEqual(f.read().splitlines(), ['passwd root correct horse correct horse'])

    def test_bridge_calls_without_bridge(self):
        # Nothing listens on 5700 in this test.
        s = socket.socket()
        try:
            s.bind(('127.0.0.1', 5700))
        except OSError:
            self.skipTest('port 5700 in use')
        finally:
            s.close()
        self.assertEqual(self.call('bridge_data'), {'running': False, 'values': {}})
        self.assertIn('error', self.call('mailbox_send', {'message': 'hi'}))


@unittest.skipUnless(os.path.exists(os.path.join(UCODE, 'ucode')), 'no ucode build (set UCODE_BUILD)')
class RpcdWithBridgeTest(unittest.TestCase):
    def test_datastore_through_real_bridge(self):
        bridge = subprocess.Popen(
            [sys.executable, os.path.join(ROOT, 'feed', 'yun-bridge', 'files', 'usr', 'lib', 'yun-bridge', 'bridge.py'),
             '--mailbox-port', '5700', '--console-port', '16571'],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(bridge.wait)
        self.addCleanup(bridge.kill)
        deadline = time.monotonic() + 10
        while True:
            try:
                socket.create_connection(('127.0.0.1', 5700), timeout=1).close()
                break
            except OSError:
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.1)
        t = RpcdPluginTest('test_status')
        t.setUp()
        self.addCleanup(t.tmp.cleanup)
        self.assertEqual(t.call('bridge_put', {'key': 'led', 'value': 'on'}), {'ok': True})
        self.assertEqual(t.call('bridge_data'), {'running': True, 'values': {'led': 'on'}})
        self.assertEqual(t.call('bridge_delete', {'key': 'led'}), {'ok': True})
        self.assertEqual(t.call('bridge_data')['values'], {})
        self.assertEqual(t.call('mailbox_send', {'message': 'hi'}), {'ok': True})


if __name__ == '__main__':
    unittest.main()
