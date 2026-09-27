#!/usr/bin/env python3
# Tests for the first-boot network setup (90-yun-network) and yun-wifi,
# on OpenWrt 25.12's default config files (tests/fixtures/openwrt-25.12) plus
# what config_generate and "wifi config" write for this board.
#
# Needs OpenWrt's uci tool on the host: set UCI_BIN (it's skipped without).

import os
import shutil
import subprocess
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.join(HERE, '..', 'feed', 'yun-base', 'files')
FIXTURES = os.path.join(HERE, 'fixtures', 'openwrt-25.12')
UCI = os.environ.get('UCI_BIN', '/home/user/hostroot/bin/uci')

# What config_generate writes from board.json (wan on eth0 only) ...
NETWORK = """
config interface 'loopback'
	option device 'lo'
	option proto 'static'
	option ipaddr '127.0.0.1'
	option netmask '255.0.0.0'

config globals 'globals'
	option ula_prefix 'fd12:3456:789a::/48'

config interface 'wan'
	option device 'eth0'
	option proto 'dhcp'

config interface 'wan6'
	option device 'eth0'
	option proto 'dhcpv6'
"""

# ... and "wifi config" for the AR9331's radio.
WIRELESS = """
config wifi-device 'radio0'
	option type 'mac80211'
	option path 'platform/ahb/18100000.wmac'
	option band '2g'
	option channel '1'
	option htmode 'HT20'
	option disabled '1'

config wifi-iface 'default_radio0'
	option device 'radio0'
	option network 'lan'
	option mode 'ap'
	option ssid 'OpenWrt'
	option encryption 'none'
"""

SYSTEM = "config system\n\toption hostname 'OpenWrt'\n\toption timezone 'UTC'\n"


@unittest.skipUnless(os.access(UCI, os.X_OK), 'no host uci (set UCI_BIN)')
class NetworkSetupTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        t = self.tmp.name
        self.conf = os.path.join(t, 'etc', 'config')
        os.makedirs(self.conf)
        for name in ('firewall', 'dhcp', 'umdns'):
            shutil.copy(os.path.join(FIXTURES, name), self.conf)
        shutil.copy(os.path.join(BASE, 'etc', 'config', 'arduino'), self.conf)
        for name, text in (('network', NETWORK), ('wireless', WIRELESS), ('system', SYSTEM)):
            with open(os.path.join(self.conf, name), 'w') as f:
                f.write(text)

        self.bin = os.path.join(t, 'bin')
        os.makedirs(self.bin)
        self.calls = os.path.join(t, 'calls')

        def script(name, body):
            path = os.path.join(self.bin, name)
            with open(path, 'w') as f:
                f.write('#!/bin/sh\n' + body)
            os.chmod(path, 0o755)

        script('uci', f'case " $* " in *" -c "*) exec {UCI} "$@";; esac\nexec {UCI} -c "{self.conf}" "$@"\n')
        for name in ('wifi', 'yun-led', 'logger'):
            script(name, f'echo "{name} $*" >> "{self.calls}"\n')
        self.env = dict(os.environ, PATH=os.pathsep.join(
            [self.bin, os.path.join(BASE, 'usr', 'bin'), os.environ['PATH']]))

    def run_script(self, *args):
        p = subprocess.run(['sh', *args], capture_output=True, text=True, env=self.env)
        self.assertEqual(p.returncode, 0, p.stderr)
        return p.stdout

    def uci(self, key):
        p = subprocess.run([UCI, '-q', '-c', self.conf, 'get', key], capture_output=True, text=True)
        return p.stdout.strip() if p.returncode == 0 else None

    def first_boot(self):
        self.run_script(os.path.join(BASE, 'etc', 'uci-defaults', '90-yun-network'))

    def test_first_boot(self):
        self.first_boot()
        self.assertEqual(self.uci('system.@system[0].hostname'), 'Arduino')
        self.assertEqual(self.uci('network.lan.proto'), 'static')
        self.assertEqual(self.uci('network.lan.ipaddr'), '192.168.240.1')
        self.assertEqual(self.uci('network.wwan.proto'), 'dhcp')
        self.assertEqual(self.uci('network.wan.device'), 'eth0')
        self.assertEqual(self.uci('dhcp.lan.interface'), 'lan')
        self.assertIsNone(self.uci('wireless.default_radio0'))
        self.assertEqual(self.uci('wireless.radio0.disabled'), '0')
        self.assertEqual(self.uci('wireless.yun_ap.mode'), 'ap')
        self.assertEqual(self.uci('wireless.yun_ap.network'), 'lan')
        self.assertEqual(self.uci('wireless.yun_ap.encryption'), 'none')
        self.assertTrue(self.uci('wireless.yun_ap.ssid').startswith('Arduino Yun-'))
        self.assertEqual(self.uci('wireless.yun_ap.disabled'), '0')
        self.assertEqual(self.uci('wireless.yun_sta.network'), 'wwan')
        self.assertEqual(self.uci('wireless.yun_sta.disabled'), '1')
        self.assertEqual(self.uci('firewall.@zone[1].name'), 'wan')
        self.assertEqual(self.uci('firewall.@zone[1].network'), 'wan wan6 wwan')
        self.assertEqual(self.uci('firewall.@zone[1].input'), 'ACCEPT')
        self.assertEqual(self.uci('firewall.@zone[0].network'), 'lan')
        self.assertEqual(self.uci('umdns.@umdns[0].network'), 'lan wan wwan')

    def test_first_boot_twice_is_harmless(self):
        self.first_boot()
        self.first_boot()
        self.assertEqual(self.uci('firewall.@zone[1].network'), 'wan wan6 wwan')
        self.assertEqual(self.uci('umdns.@umdns[0].network'), 'lan wan wwan')

    def test_wifi_client_and_back(self):
        self.first_boot()
        yun_wifi = os.path.join(BASE, 'usr', 'bin', 'yun-wifi')
        self.run_script(yun_wifi, 'client', "Dan's \"Net\" $x", 'sae-mixed', 'pass word!')
        self.assertEqual(self.uci('wireless.yun_sta.ssid'), 'Dan\'s "Net" $x')
        self.assertEqual(self.uci('wireless.yun_sta.key'), 'pass word!')
        self.assertEqual(self.uci('wireless.yun_sta.encryption'), 'sae-mixed')
        self.assertEqual(self.uci('wireless.yun_sta.disabled'), '0')
        self.assertEqual(self.uci('wireless.yun_ap.disabled'), '1')
        self.assertEqual(self.run_script(yun_wifi, 'status').strip(), 'client')
        with open(self.calls) as f:
            self.assertIn('wifi reload', f.read())

        # No network at boot: fall back to the access point, keep the settings ...
        self.run_script(yun_wifi, 'fallback')
        self.assertEqual(self.uci('wireless.yun_ap.disabled'), '0')
        self.assertEqual(self.uci('wireless.yun_sta.disabled'), '1')
        self.assertEqual(self.uci('wireless.yun_sta.ssid'), 'Dan\'s "Net" $x')
        self.assertEqual(self.run_script(yun_wifi, 'status').strip(), 'fallback')
        # ... and try them again on the next boot.
        self.run_script(yun_wifi, 'retry')
        self.assertEqual(self.uci('wireless.yun_sta.disabled'), '0')
        self.assertEqual(self.uci('wireless.yun_ap.disabled'), '1')
        self.assertEqual(self.run_script(yun_wifi, 'status').strip(), 'client')

        # WLAN RST: back to setup mode, and a retry doesn't undo that.
        self.run_script(yun_wifi, 'ap')
        self.run_script(yun_wifi, 'retry')
        self.assertEqual(self.uci('wireless.yun_ap.disabled'), '0')
        self.assertEqual(self.run_script(yun_wifi, 'status').strip(), 'ap')

    def test_open_network(self):
        self.first_boot()
        yun_wifi = os.path.join(BASE, 'usr', 'bin', 'yun-wifi')
        self.run_script(yun_wifi, 'client', 'Cafe', 'none')
        self.assertEqual(self.uci('wireless.yun_sta.encryption'), 'none')
        self.assertIsNone(self.uci('wireless.yun_sta.key'))

    def test_client_needs_a_key(self):
        self.first_boot()
        p = subprocess.run(['sh', os.path.join(BASE, 'usr', 'bin', 'yun-wifi'), 'client', 'Home', 'psk2'],
                           capture_output=True, text=True, env=self.env)
        self.assertEqual(p.returncode, 1)
        self.assertEqual(self.uci('wireless.yun_sta.disabled'), '1')


if __name__ == '__main__':
    unittest.main()
