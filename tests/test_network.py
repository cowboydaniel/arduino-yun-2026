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

    def test_sd_cards_are_mounted(self):
        storage = os.path.join(BASE, 'etc', 'uci-defaults', '91-yun-storage')
        # As fstools' own first-boot default writes it, with no card in.
        with open(os.path.join(self.conf, 'fstab'), 'w') as f:
            f.write("config 'global'\n\toption\tanon_swap\t'0'\n\toption\tanon_mount\t'0'\n"
                    "\toption\tauto_swap\t'1'\n\toption\tauto_mount\t'1'\n")
        self.run_script(storage)
        self.assertEqual(self.uci('fstab.@global[0].anon_mount'), '1')
        self.assertEqual(self.uci('fstab.@global[0].auto_mount'), '1')
        os.remove(os.path.join(self.conf, 'fstab'))
        with open(os.path.join(self.conf, 'fstab'), 'w') as f:
            pass
        self.run_script(storage)
        self.assertEqual(self.uci('fstab.@global[0].anon_mount'), '1')

    def test_ethernet_is_the_preferred_route(self):
        self.first_boot()
        self.assertEqual(self.uci('network.wan.metric'), '10')
        self.assertEqual(self.uci('network.wan6.metric'), '10')
        self.assertEqual(self.uci('network.wwan.metric'), '20')

    def test_route_metrics_on_update_keep_hand_set_ones(self):
        # A board from 2026.3 or older: no metrics yet, one set by hand.
        self.first_boot()
        for key in ('wan.metric', 'wan6.metric'):
            subprocess.run([UCI, '-c', self.conf, 'delete', f'network.{key}'], check=True)
        subprocess.run([UCI, '-c', self.conf, 'set', 'network.wwan.metric=5'], check=True)
        subprocess.run([UCI, '-c', self.conf, 'commit', 'network'], check=True)
        self.first_boot()         # sysupgrade runs the uci-defaults again
        self.assertEqual(self.uci('network.wan.metric'), '10')
        self.assertEqual(self.uci('network.wan6.metric'), '10')
        self.assertEqual(self.uci('network.wwan.metric'), '5')

    def test_first_boot_twice_is_harmless(self):
        self.first_boot()
        self.first_boot()
        self.assertEqual(self.uci('firewall.@zone[1].network'), 'wan wan6 wwan')
        self.assertEqual(self.uci('umdns.@umdns[0].network'), 'lan wan wwan')

    def test_radio_not_ready_still_opens_ethernet(self):
        # "wifi config" found no radio: Ethernet must still be reachable, and
        # the script must ask to run again next boot.
        with open(os.path.join(self.conf, 'wireless'), 'w') as f:
            f.write('')
        script = os.path.join(BASE, 'etc', 'uci-defaults', '90-yun-network')
        p = subprocess.run(['sh', script], capture_output=True, text=True, env=self.env)
        self.assertEqual(p.returncode, 1)
        self.assertEqual(self.uci('firewall.@zone[1].input'), 'ACCEPT')
        self.assertEqual(self.uci('firewall.@zone[1].network'), 'wan wan6 wwan')
        self.assertEqual(self.uci('network.wan.proto'), 'dhcp')
        self.assertEqual(self.uci('network.wwan.proto'), 'dhcp')
        self.assertEqual(self.uci('system.@system[0].hostname'), 'Arduino')
        self.assertIsNone(self.uci('wireless.yun_sta'))

        # In between, the stock settings import names the board.
        subprocess.run([UCI, '-c', self.conf, 'set', 'system.@system[0].hostname=workbench'], check=True)
        subprocess.run([UCI, '-c', self.conf, 'commit', 'system'], check=True)

        # Next boot the radio is there: the Wi-Fi part runs, and the name stays.
        with open(os.path.join(self.conf, 'wireless'), 'w') as f:
            f.write(WIRELESS)
        self.first_boot()
        self.assertEqual(self.uci('wireless.yun_sta.network'), 'wwan')
        self.assertEqual(self.uci('wireless.yun_ap.disabled'), '0')
        self.assertEqual(self.uci('system.@system[0].hostname'), 'workbench')
        self.assertEqual(self.uci('firewall.@zone[1].network'), 'wan wan6 wwan')

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

    def test_enterprise_network(self):
        self.first_boot()
        yun_wifi = os.path.join(BASE, 'usr', 'bin', 'yun-wifi')
        self.run_script(yun_wifi, 'client-eap', 'eduroam', 'wpa3-mixed', 'peap', 'MSCHAPV2',
                        'dan@example.edu', "p@ss 'word", 'anonymous@example.edu', 'radius.example.edu')
        for key, value in (('ssid', 'eduroam'), ('encryption', 'wpa3-mixed'), ('eap_type', 'peap'),
                           ('auth', 'MSCHAPV2'), ('identity', 'dan@example.edu'), ('password', "p@ss 'word"),
                           ('anonymous_identity', 'anonymous@example.edu'), ('ca_cert_usesystem', '1'),
                           ('domain_suffix_match', 'radius.example.edu'), ('ieee80211w', '1'),
                           ('disabled', '0')):
            self.assertEqual(self.uci(f'wireless.yun_sta.{key}'), value, key)
        self.assertIsNone(self.uci('wireless.yun_sta.key'))
        self.assertEqual(self.uci('wireless.yun_ap.disabled'), '1')
        self.assertEqual(self.run_script(yun_wifi, 'status').strip(), 'client')

        # Back to a home network: no Enterprise settings left behind.
        self.run_script(yun_wifi, 'client', 'Home', 'psk2', 'password1')
        for key in ('eap_type', 'auth', 'identity', 'password', 'anonymous_identity',
                    'ca_cert_usesystem', 'domain_suffix_match', 'ieee80211w'):
            self.assertIsNone(self.uci(f'wireless.yun_sta.{key}'), key)
        self.assertEqual(self.uci('wireless.yun_sta.key'), 'password1')

    def test_enterprise_without_server_check(self):
        self.first_boot()
        self.run_script(os.path.join(BASE, 'usr', 'bin', 'yun-wifi'), 'client-eap', 'Campus', 'wpa2',
                        'ttls', 'PAP', 'dan', 'secret', '', '')
        self.assertEqual(self.uci('wireless.yun_sta.auth'), 'PAP')
        self.assertIsNone(self.uci('wireless.yun_sta.ca_cert_usesystem'))
        self.assertIsNone(self.uci('wireless.yun_sta.anonymous_identity'))
        self.assertIsNone(self.uci('wireless.yun_sta.ieee80211w'))

    def test_enterprise_rejects_bad_input(self):
        self.first_boot()
        yun_wifi = os.path.join(BASE, 'usr', 'bin', 'yun-wifi')
        for args in (('Campus', 'psk2', 'peap', 'MSCHAPV2', 'dan', 'pw'),
                     ('Campus', 'wpa2', 'tls', 'MSCHAPV2', 'dan', 'pw'),
                     ('Campus', 'wpa2', 'peap', 'MSCHAPV2', 'dan', '')):
            p = subprocess.run(['sh', yun_wifi, 'client-eap', *args], capture_output=True, text=True, env=self.env)
            self.assertEqual(p.returncode, 1, args)
        self.assertEqual(self.uci('wireless.yun_sta.disabled'), '1')

    WG_CONF = """# From the router's WireGuard page
[Interface]
PrivateKey = yAnz5TF+lXXJte14tji3zlMNq+hd2rYUIgJBgB3fBmk=
Address = 10.8.0.5/32, fd00:8::5/128
DNS = 10.8.0.1

[Peer]
PublicKey = xTIBA5rboUvnH4htodjb6e697QjLERt1NAB4mZqp8Dg=
PresharedKey = /UwcSPg38hW/D9Y3tcS1FOV0K1wuURMbS0sesJEP5ak=
AllowedIPs = 10.8.0.0/24,fd00:8::/64
Endpoint = vpn.example.com:51820
"""

    def yun_vpn(self, *args, conf=None, check=True):
        if conf is not None:
            path = os.path.join(self.tmp.name, 'wg.conf')
            with open(path, 'w') as f:
                f.write(conf)
            args = (args[0], path) + args[1:]
        p = subprocess.run(['sh', os.path.join(BASE, 'usr', 'bin', 'yun-vpn'), *args],
                           capture_output=True, text=True, env=self.env)
        if check:
            self.assertEqual(p.returncode, 0, p.stderr)
        return p

    def uci_list(self, key):
        p = subprocess.run([UCI, '-q', '-c', self.conf, 'get', key], capture_output=True, text=True)
        return p.stdout.split()

    def test_vpn_import(self):
        self.first_boot()
        self.yun_vpn('import', conf=self.WG_CONF)
        self.assertEqual(self.uci('network.yunvpn.proto'), 'wireguard')
        self.assertEqual(self.uci('network.yunvpn.private_key'), 'yAnz5TF+lXXJte14tji3zlMNq+hd2rYUIgJBgB3fBmk=')
        self.assertEqual(self.uci_list('network.yunvpn.addresses'), ['10.8.0.5/32', 'fd00:8::5/128'])
        self.assertEqual(self.uci('network.yunvpn_server'), 'wireguard_yunvpn')
        self.assertEqual(self.uci('network.yunvpn_server.public_key'), 'xTIBA5rboUvnH4htodjb6e697QjLERt1NAB4mZqp8Dg=')
        self.assertEqual(self.uci('network.yunvpn_server.preshared_key'), '/UwcSPg38hW/D9Y3tcS1FOV0K1wuURMbS0sesJEP5ak=')
        self.assertEqual(self.uci_list('network.yunvpn_server.allowed_ips'), ['10.8.0.0/24', 'fd00:8::/64'])
        self.assertEqual(self.uci('network.yunvpn_server.endpoint_host'), 'vpn.example.com')
        self.assertEqual(self.uci('network.yunvpn_server.endpoint_port'), '51820')
        self.assertEqual(self.uci('network.yunvpn_server.persistent_keepalive'), '25')
        self.assertEqual(self.uci('network.yunvpn_server.route_allowed_ips'), '1')
        self.assertIn('yunvpn', self.uci('firewall.@zone[1].network').split())

        # Importing again replaces it, and doesn't list the zone twice.
        self.yun_vpn('import', conf=self.WG_CONF.replace('vpn.example.com:51820', '[2001:db8::1]:4500')
                     .replace('DNS = 10.8.0.1', 'MTU = 1380') + 'PersistentKeepalive = 15\n')
        self.assertEqual(self.uci('network.yunvpn_server.endpoint_host'), '2001:db8::1')
        self.assertEqual(self.uci('network.yunvpn_server.endpoint_port'), '4500')
        self.assertEqual(self.uci('network.yunvpn_server.persistent_keepalive'), '15')
        self.assertEqual(self.uci('network.yunvpn.mtu'), '1380')
        self.assertEqual(self.uci('firewall.@zone[1].network').split().count('yunvpn'), 1)

        self.yun_vpn('off')
        self.assertEqual(self.uci('network.yunvpn.disabled'), '1')
        self.yun_vpn('on')
        self.assertIsNone(self.uci('network.yunvpn.disabled'))
        self.yun_vpn('remove')
        self.assertIsNone(self.uci('network.yunvpn'))
        self.assertIsNone(self.uci('network.yunvpn_server'))
        self.assertNotIn('yunvpn', self.uci('firewall.@zone[1].network').split())

    def test_vpn_rejects_bad_files(self):
        self.first_boot()
        c = self.WG_CONF
        for bad, why in ((c.replace('PrivateKey = yAnz', 'PrivateKey = zzz'), 'PrivateKey'),
                         (c.replace('Endpoint = vpn.example.com:51820', ''), 'Endpoint'),
                         (c.replace('vpn.example.com', 'vpn.example.com;reboot'), 'Endpoint'),
                         (c.replace('10.8.0.5/32', '10.8.0.5/32 $(reboot)'), 'Address'),
                         (c + '[Peer]\nPublicKey = xTIBA5rboUvnH4htodjb6e697QjLERt1NAB4mZqp8Dg=\n', 'exactly one'),
                         ('hello', 'exactly one')):
            p = self.yun_vpn('import', conf=bad, check=False)
            self.assertEqual(p.returncode, 1, why)
            self.assertIn(why, p.stderr)
        self.assertIsNone(self.uci('network.yunvpn'))

    def test_direct_network(self):
        self.first_boot()
        yun_wifi = os.path.join(BASE, 'usr', 'bin', 'yun-wifi')
        ssid = self.uci('wireless.yun_ap.ssid')
        self.run_script(yun_wifi, 'client', 'Home', 'psk2', 'password1')
        self.assertEqual(self.uci('wireless.yun_ap.disabled'), '1')

        self.run_script(yun_wifi, 'direct', 'on', "yun pass'1")
        self.assertEqual(self.uci('wireless.yun_ap.disabled'), '0')         # next to the client
        self.assertEqual(self.uci('wireless.yun_sta.disabled'), '0')
        self.assertEqual(self.uci('wireless.yun_ap.encryption'), 'psk2')
        self.assertEqual(self.uci('wireless.yun_ap.key'), "yun pass'1")
        self.assertEqual(self.uci('wireless.yun_ap.ssid'), ssid)
        self.assertEqual(self.uci('arduino.@arduino[0].direct_ap'), '1')

        # Setup mode is the open network, as always ...
        self.run_script(yun_wifi, 'fallback')
        self.assertEqual(self.uci('wireless.yun_ap.encryption'), 'none')
        self.assertIsNone(self.uci('wireless.yun_ap.key'))
        # ... and back as a client, the direct network has its password again.
        self.run_script(yun_wifi, 'retry')
        self.assertEqual(self.uci('wireless.yun_ap.disabled'), '0')
        self.assertEqual(self.uci('wireless.yun_ap.encryption'), 'psk2')
        self.run_script(yun_wifi, 'client', 'Other', 'psk2', 'password2')
        self.assertEqual(self.uci('wireless.yun_ap.disabled'), '0')
        self.assertEqual(self.uci('wireless.yun_ap.key'), "yun pass'1")

        # sync (at boot, after an update) keeps it that way.
        subprocess.run([UCI, '-c', self.conf, 'set', 'wireless.yun_ap.disabled=1'], check=True)
        subprocess.run([UCI, '-c', self.conf, 'set', 'wireless.yun_ap.encryption=none'], check=True)
        subprocess.run([UCI, '-c', self.conf, 'commit', 'wireless'], check=True)
        self.run_script(yun_wifi, 'sync', '--no-reload')
        self.assertEqual(self.uci('wireless.yun_ap.disabled'), '0')
        self.assertEqual(self.uci('wireless.yun_ap.encryption'), 'psk2')

        self.run_script(yun_wifi, 'direct', 'off')
        self.assertEqual(self.uci('wireless.yun_ap.disabled'), '1')
        self.assertEqual(self.uci('wireless.yun_ap.encryption'), 'none')
        self.assertIsNone(self.uci('arduino.@arduino[0].direct_ap_key'))

    def test_direct_network_needs_a_password(self):
        self.first_boot()
        p = subprocess.run(['sh', os.path.join(BASE, 'usr', 'bin', 'yun-wifi'), 'direct', 'on', 'short'],
                           capture_output=True, text=True, env=self.env)
        self.assertEqual(p.returncode, 1)
        self.assertIsNone(self.uci('arduino.@arduino[0].direct_ap_key'))

    def test_client_needs_a_key(self):
        self.first_boot()
        p = subprocess.run(['sh', os.path.join(BASE, 'usr', 'bin', 'yun-wifi'), 'client', 'Home', 'psk2'],
                           capture_output=True, text=True, env=self.env)
        self.assertEqual(p.returncode, 1)
        self.assertEqual(self.uci('wireless.yun_sta.disabled'), '1')


    def test_rerun_after_update_keeps_the_setup(self):
        # sysupgrade keeps /etc/config and the new image runs uci-defaults
        # again: a board that's a Wi-Fi client with a static address must
        # stay exactly that.
        self.first_boot()
        yun_wifi = os.path.join(BASE, 'usr', 'bin', 'yun-wifi')
        self.run_script(yun_wifi, 'client', 'Home', 'sae-mixed', 'secret pass')
        subprocess.run([UCI, '-c', self.conf, 'batch'], input=(
            "set network.wwan.proto='static'\nset network.wwan.ipaddr='192.168.1.45'\n"
            "set network.lan.ipaddr='10.9.9.1'\nset wireless.yun_ap.ssid='My setup AP'\ncommit\n"),
            text=True, check=True)
        self.first_boot()
        self.assertEqual(self.uci('wireless.yun_sta.disabled'), '0')
        self.assertEqual(self.uci('wireless.yun_ap.disabled'), '1')
        self.assertEqual(self.uci('wireless.yun_sta.ssid'), 'Home')
        self.assertEqual(self.uci('wireless.yun_sta.encryption'), 'sae-mixed')
        self.assertEqual(self.uci('wireless.yun_sta.key'), 'secret pass')
        self.assertEqual(self.uci('wireless.yun_ap.ssid'), 'My setup AP')
        self.assertEqual(self.uci('network.wwan.proto'), 'static')
        self.assertEqual(self.uci('network.wwan.ipaddr'), '192.168.1.45')
        self.assertEqual(self.uci('network.lan.ipaddr'), '10.9.9.1')
        self.assertEqual(self.uci('arduino.@arduino[0].wifi_state'), 'client')


    def test_sync_puts_the_role_back(self):
        # What the 2026.2 update did to a client: sections rewritten to
        # setup mode while the saved role still says client.
        self.first_boot()
        yun_wifi = os.path.join(BASE, 'usr', 'bin', 'yun-wifi')
        self.run_script(yun_wifi, 'client', 'Home', 'psk2', 'secret pass')
        subprocess.run([UCI, '-c', self.conf, 'batch'], input=(
            "set wireless.yun_sta.disabled='1'\nset wireless.yun_ap.disabled='0'\ncommit\n"),
            text=True, check=True)
        open(self.calls, 'w').close()
        self.run_script(yun_wifi, 'sync')
        self.assertEqual(self.uci('wireless.yun_sta.disabled'), '0')
        self.assertEqual(self.uci('wireless.yun_ap.disabled'), '1')
        with open(self.calls) as f:
            self.assertIn('wifi reload', f.read())

    def test_sync_leaves_a_matching_setup_alone(self):
        self.first_boot()
        open(self.calls, 'w').close()
        self.run_script(os.path.join(BASE, 'usr', 'bin', 'yun-wifi'), 'sync')
        self.assertEqual(self.uci('wireless.yun_ap.disabled'), '0')
        with open(self.calls) as f:
            self.assertNotIn('wifi reload', f.read())

    def test_late_radio_finishes_the_wifi_setup(self):
        # First boot without the radio: the Wi-Fi part is left for later.
        with open(os.path.join(self.conf, 'wireless'), 'w') as f:
            f.write('')
        for name in ('wifi', 'lock'):
            with open(os.path.join(self.bin, name), 'w') as f:
                f.write(f'#!/bin/sh\necho "{name} $*" >> "{self.calls}"\n')
        defaults = os.path.join(self.tmp.name, 'uci-defaults')
        os.makedirs(defaults)
        script = os.path.join(defaults, '90-yun-network')
        shutil.copy(os.path.join(BASE, 'etc', 'uci-defaults', '90-yun-network'), script)
        p = subprocess.run(['sh', script], capture_output=True, text=True, env=self.env)
        self.assertEqual(p.returncode, 1)          # waits for the radio
        self.assertIsNone(self.uci('wireless.yun_ap'))

        # The radio appears: 10-wifi-detect writes radio0, then our hook.
        with open(os.path.join(self.conf, 'wireless'), 'w') as f:
            f.write(WIRELESS)
        open(self.calls, 'w').close()
        p = subprocess.run(['sh', os.path.join(BASE, 'etc', 'hotplug.d', 'ieee80211', '20-yun-wifi')],
                           capture_output=True, text=True,
                           env=dict(self.env, ACTION='add', YUN_UCI_DEFAULTS=defaults))
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(self.uci('wireless.yun_ap.disabled'), '0')
        self.assertEqual(self.uci('wireless.yun_sta.disabled'), '1')
        self.assertFalse(os.path.exists(script))   # done, not run again at the next boot
        with open(self.calls) as f:
            self.assertIn('wifi reload', f.read())


if __name__ == '__main__':
    unittest.main()
