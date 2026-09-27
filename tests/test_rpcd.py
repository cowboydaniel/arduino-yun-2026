#!/usr/bin/env python3
# Tests for the Yun Panel's rpcd plugin (yun.uc), run under a host build of
# ucode with stand-ins for the uci and ubus modules and for the commands it
# runs (tests/rpcd/). Set UCODE_BUILD to a ucode build directory; the tests
# are skipped without one.
#
#   git clone https://github.com/jow-/ucode && cd ucode && mkdir build && cd build
#   cmake .. -DUCI_SUPPORT=OFF -DUBUS_SUPPORT=OFF -DULOOP_SUPPORT=OFF \
#     -DRTNL_SUPPORT=OFF -DNL80211_SUPPORT=OFF && make

import base64
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
        self.assertEqual(by['Office']['encryption'], 'wpa2')      # 802.1X: Enterprise
        self.assertEqual(len(r), 4)             # hidden network skipped

    def test_wifi_client_validates_and_passes_args_safely(self):
        self.assertIn('error', self.call('wifi_client', {'ssid': '', 'encryption': 'psk2', 'key': 'x' * 8}))
        self.assertIn('error', self.call('wifi_client', {'ssid': 'a', 'encryption': 'wep', 'key': 'x' * 8}))
        self.assertIn('error', self.call('wifi_client', {'ssid': 'a', 'encryption': 'psk2', 'key': 'short'}))
        ssid = 'a"b; $(touch /tmp/pwned) `id`'
        self.assertEqual(self.call('wifi_client', {'ssid': ssid, 'encryption': 'psk2', 'key': "pa ss'word"}),
                         {'ok': True})
        self.assertEqual(self.calls(), [f"yun-wifi client {ssid} psk2 pa ss'word"])
        self.assertFalse(os.path.exists('/tmp/pwned'))

    def test_wifi_client_enterprise(self):
        base = {'ssid': 'eduroam', 'encryption': 'wpa3-mixed', 'eap': 'peap', 'phase2': 'MSCHAPV2',
                'identity': 'dan@example.edu', 'password': "p@ss 'w$(id)"}
        self.assertIn('error', self.call('wifi_client', dict(base, eap='tls')))
        self.assertIn('error', self.call('wifi_client', dict(base, phase2='PAP')))      # not with PEAP
        self.assertIn('error', self.call('wifi_client', dict(base, identity='')))
        self.assertIn('error', self.call('wifi_client', dict(base, password='')))
        self.assertIn('error', self.call('wifi_client', dict(base, domain='bad domain;')))
        self.assertEqual(self.call('wifi_client', dict(base, anonymous_identity='anonymous@example.edu',
                                                       domain='radius.example.edu')), {'ok': True})
        self.assertEqual(self.calls(), ["yun-wifi client-eap eduroam wpa3-mixed peap MSCHAPV2 dan@example.edu "
                                        "p@ss 'w$(id) anonymous@example.edu radius.example.edu"])

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


    # --- terminal ---

    def shell(self, command, cwd='/tmp', timeout=10):
        import base64
        job = self.call('shell_start', {'command': command, 'cwd': cwd})
        self.assertIn('id', job, job)
        out, offset = b'', 0
        deadline = time.monotonic() + timeout
        while True:
            r = self.call('shell_poll', {'id': job['id'], 'offset': offset})
            self.assertNotIn('error', r, r)
            out += base64.b64decode(r['output'])
            offset = r['offset']
            if r['done']:
                return out.decode('utf-8', 'replace'), r
            self.assertLess(time.monotonic(), deadline, 'command never finished')
            time.sleep(0.2)

    def test_shell_output_exit_code_and_cwd(self):
        out, r = self.shell('echo hello; echo oops >&2; cd /usr; exit 3')
        self.assertEqual(out, 'hello\noops\n')
        self.assertEqual(r['rc'], 3)
        self.assertEqual(r['cwd'], '/usr')

    def test_shell_keeps_the_directory_and_quoting(self):
        out, r = self.shell("pwd; printf '%s|' \"a b\" '$HOME' \"it's\"", cwd='/usr')
        self.assertEqual(out, "/usr\na b|$HOME|it's|")
        out, r = self.shell('pwd', cwd='/no/such/dir')
        self.assertEqual(out.strip(), '/root' if os.path.isdir('/root') else out.strip())

    def test_shell_binary_and_utf8(self):
        out, r = self.shell("printf 'caf\\303\\251 \\377 end'")
        self.assertTrue(out.startswith('café'), out)
        self.assertTrue(out.endswith('end'))

    def test_shell_output_is_capped(self):
        out, r = self.shell('yes', timeout=30)
        self.assertEqual(len(out.encode()), 1048576)
        self.assertTrue(r['truncated'])

    def test_shell_stop(self):
        job = self.call('shell_start', {'command': 'echo started; sleep 60; echo never', 'cwd': '/tmp'})
        time.sleep(0.5)
        self.assertEqual(self.call('shell_stop', {'id': job['id']}), {'ok': True})
        deadline = time.monotonic() + 10
        while True:
            r = self.call('shell_poll', {'id': job['id'], 'offset': 0})
            if r.get('done'):
                break
            self.assertLess(time.monotonic(), deadline)
            time.sleep(0.3)
        import base64
        self.assertEqual(base64.b64decode(r['output']), b'started\n')
        self.assertIsNone(r['rc'])          # stopped, not exited

    def test_shell_rejects_bad_input(self):
        self.assertIn('error', self.call('shell_start', {'command': '   '}))
        self.assertIn('error', self.call('shell_poll', {'id': '../../etc/passwd', 'offset': 0}))
        self.assertIn('error', self.call('shell_stop', {'id': 'zz'}))

    def make_usb(self):
        root = os.path.join(self.tmp.name, 'usb')
        drivers = os.path.join(self.tmp.name, 'drivers')

        def dev(name, vid, pid, product, interfaces, dev_class='00', speed='12'):
            d = os.path.join(root, name)
            os.makedirs(d)
            for attr, value in (('idVendor', vid), ('idProduct', pid), ('bDeviceClass', dev_class),
                                ('speed', speed), ('product', product)):
                if value is not None:
                    with open(os.path.join(d, attr), 'w') as f:
                        f.write(value + '\n')
            for i, (cls, sub, proto, driver) in enumerate(interfaces):
                idir = os.path.join(d, f'{name}:1.{i}')
                os.makedirs(idir)
                for attr, value in (('bInterfaceClass', cls), ('bInterfaceSubClass', sub),
                                    ('bInterfaceProtocol', proto)):
                    with open(os.path.join(idir, attr), 'w') as f:
                        f.write(value + '\n')
                if driver:
                    os.makedirs(os.path.join(drivers, driver), exist_ok=True)
                    os.symlink(os.path.join(drivers, driver), os.path.join(idir, 'driver'))

        dev('1-1', '05e3', '0608', 'USB2.0 Hub', [('09', '00', '00', 'hub')], dev_class='09', speed='480')
        dev('1-1.4', '058f', '6366', 'Flash Reader', [('08', '06', '50', 'usb-storage')], speed='480')
        dev('1-1.1', '2341', '0043', 'Arduino Uno', [('02', '02', '01', 'cdc_acm'), ('0a', '00', '00', 'cdc_acm')])
        dev('1-1.2', '1a86', '7523', 'USB Serial', [('ff', '01', '02', None)])
        dev('1-1.3', '046d', '0825', None, [('0e', '01', '00', None), ('0e', '02', '00', None), ('01', '01', '00', None)],
            dev_class='ef', speed='480')
        dev('1-1.3.1', '0e8d', '7612', '802.11ac WLAN', [('ff', 'ff', 'ff', None)], speed='480')
        os.makedirs(os.path.join(root, '1-1.1:1.0'), exist_ok=True)     # interfaces are listed at the top too
        os.makedirs(os.path.join(root, 'usb1'))
        self.env['YUN_USB_SYSFS'] = root

    def test_usb_devices(self):
        self.make_usb()
        devices = {d['path']: d for d in self.call('usb_devices')['devices']}
        self.assertEqual(sorted(devices), ['1-1.1', '1-1.2', '1-1.3', '1-1.3.1', '1-1.4'])   # no hubs
        self.assertTrue(devices['1-1.4']['builtin'])
        self.assertFalse(devices['1-1.4']['needs_driver'])
        uno = devices['1-1.1']
        self.assertEqual((uno['name'], uno['id'], uno['drivers'], uno['needs_driver'], uno['packages']),
                         ('Arduino Uno', '2341:0043', ['cdc_acm'], False, []))
        self.assertEqual(uno['types'], ['Communications'])
        self.assertEqual(devices['1-1.2']['packages'], ['kmod-usb-serial-ch341'])
        cam = devices['1-1.3']
        self.assertEqual((cam['name'], cam['needs_driver'], cam['packages']),
                         ('USB device 046d:0825', True, ['kmod-video-uvc']))
        self.assertEqual(cam['types'], ['Video', 'Audio'])
        self.assertEqual(devices['1-1.3.1']['packages'], ['kmod-mt76x2u'])
        self.assertEqual(devices['1-1.3.1']['speed'], 480)

    def test_usb_install_only_known_packages(self):
        for bad in ('', 'luci', 'kmod-usb-serial-ch341; reboot', 'kmod-video-uvc kmod-evil'):
            self.assertIn('error', self.call('usb_install', {'package': bad}))
        bin_dir = os.path.join(self.tmp.name, 'apkbin')
        os.makedirs(bin_dir)
        with open(os.path.join(bin_dir, 'apk'), 'w') as f:
            f.write('#!/bin/sh\necho "apk $*"\n')
        os.chmod(os.path.join(bin_dir, 'apk'), 0o755)
        self.env['PATH'] = bin_dir + os.pathsep + self.env['PATH']
        job = self.call('usb_install', {'package': 'kmod-usb-serial-ch341'})
        self.assertIn('id', job, job)
        deadline = time.time() + 10
        out, offset = b'', 0
        while time.time() < deadline:
            r = self.call('shell_poll', {'id': job['id'], 'offset': offset})
            out += base64.b64decode(r['output'])
            offset = r['offset']
            if r['done']:
                break
            time.sleep(0.2)
        self.assertEqual(out.decode(), 'apk add kmod-usb-serial-ch341\n')
        self.assertEqual(r['rc'], 0)

    def test_vpn(self):
        self.assertEqual(self.call('vpn_status'), {'installed': False, 'configured': False})
        r = self.call('vpn_import', {'config': '[Interface]\nPrivateKey = x\n'})
        self.assertEqual(r, {'error': 'the file must have exactly one [Peer] (the server); it has 0'})
        conf = '[Interface]\nPrivateKey = x\n[Peer]\nEndpoint = a:1\n'
        self.assertEqual(self.call('vpn_import', {'config': conf}), {'ok': True})
        with open(self.env['CALL_LOG'] + '.conf') as f:
            self.assertEqual(f.read(), conf)
        self.assertFalse(os.path.exists('/tmp/yun-vpn-import.conf'))    # the keys don't stay in /tmp
        self.assertIn('error', self.call('vpn_import', {'config': ''}))
        self.assertEqual(self.call('vpn_set', {'enabled': False}), {'ok': True})
        self.assertEqual(self.call('vpn_remove'), {'ok': True})
        with open(self.env['CALL_LOG']) as f:
            self.assertEqual(f.read().splitlines(), ['yun-vpn import /tmp/yun-vpn-import.conf'] * 2 +
                             ['yun-vpn off', 'yun-vpn remove'])

        with open(self.env['UCI_FIXTURE']) as f:
            uci = json.load(f)
        uci['network'] = {'yunvpn': {'proto': 'wireguard', 'addresses': ['10.8.0.5/32']},
                          'yunvpn_server': {'endpoint_host': 'vpn.example.com', 'endpoint_port': '51820',
                                            'allowed_ips': ['10.8.0.0/24']}}
        with open(self.env['UCI_FIXTURE'], 'w') as f:
            json.dump(uci, f)
        st = self.call('vpn_status')
        self.assertEqual((st['configured'], st['enabled'], st['server'], st['addresses'], st['up']),
                         (True, True, 'vpn.example.com:51820', ['10.8.0.5/32'], False))

    def test_update_progress(self):
        # The paths are fixed; save whatever a real update left there.
        os.makedirs('/tmp/yun-update', exist_ok=True)
        saved = {}
        for f in ('/tmp/yun-update/stage', '/tmp/yun-update/total',
                  '/tmp/yun-update/sysupgrade.bin', '/tmp/yun-update.log'):
            if os.path.exists(f):
                with open(f, 'rb') as h:
                    saved[f] = h.read()
        def restore():
            for f in ('/tmp/yun-update/stage', '/tmp/yun-update/total',
                      '/tmp/yun-update/sysupgrade.bin', '/tmp/yun-update.log'):
                if f in saved:
                    with open(f, 'wb') as h:
                        h.write(saved[f])
                elif os.path.exists(f):
                    os.unlink(f)
        self.addCleanup(restore)
        for f, data in (('/tmp/yun-update/stage', 'downloading\n'), ('/tmp/yun-update/total', '4000\n'),
                        ('/tmp/yun-update/sysupgrade.bin', 'x' * 1000),
                        ('/tmp/yun-update.log', 'yun-update: stopping services\nyun-update: downloading Yun 2026.4\n')):
            with open(f, 'w') as h:
                h.write(data)
        self.assertEqual(self.call('update_progress'), {
            'stage': 'downloading', 'downloaded': 1000, 'total': 4000, 'message': 'downloading Yun 2026.4'})


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
