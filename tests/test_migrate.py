#!/usr/bin/env python3
# End-to-end test of the migration: tools/yun-migrate runs on a fake stock
# Yun (configs from docs/yun-probe2.txt), its settings archive is unpacked
# into a fake new system the way preinit restores sysupgrade.tgz, and
# 95-yun-migrate turns it into the new configuration.
#
# Needs OpenWrt's uci tool on the host: set UCI_BIN (it's skipped without).

import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, '..')
MIGRATE = os.path.join(ROOT, 'tools', 'yun-migrate')
IMPORT = os.path.join(ROOT, 'feed', 'yun-base', 'files', 'etc', 'uci-defaults', '95-yun-migrate')
UCI = os.environ.get('UCI_BIN', '/home/user/hostroot/bin/uci')

sys.path.insert(0, HERE)
from test_check_image import build as build_image  # noqa: E402

STOCK = {
    'system': """
config system
	option hostname 'workbench'
	option timezone 'AEST-10AEDT,M10.1.0,M4.1.0/3'
	option zonename 'Australia/Sydney'
""",
    'network': """
config interface 'loopback'
	option ifname 'lo'
	option proto 'static'
	option ipaddr '127.0.0.1'
	option netmask '255.0.0.0'

config interface 'lan'
	option _orig_ifname 'wlan0'
	option proto 'dhcp'

config interface 'wan'
	option ifname 'eth1'
	option proto 'static'
	option ipaddr '192.168.1.135'
	option netmask '255.255.255.0'
	option gateway '192.168.1.1'
	list dns '1.1.1.1'
	list dns '9.9.9.9'
""",
    'wireless': """
config wifi-device 'radio0'
	option type 'mac80211'
	option channel 'auto'
	option country 'AU'

config wifi-iface
	option device 'radio0'
	option network 'lan'
	option mode 'sta'
	option ssid "Dan's \\"Workshop\\" $net"
	option encryption 'psk2+ccmp'
	option key 'correct horse battery'
""",
    'arduino': """
config arduino
	option password '775e9f944188a7bcb36e9ca5dc51672b44bcceeb7d56d89dfb914eb3a1ff2d69'
	option secure_rest_api 'false'
	option socket_timeout '7'
	option access_point_wifi_name 'Arduino'
	option wifi_reset_step 'clear'

config wifi-iface
	option mode 'sta'
	option ssid 'Old network'
	option encryption 'psk2'
	option key 'old password'
""",
}

STOCK_SHADOW = 'root:$1$Zq9e.abc$T8Pgq/7vD1n1/nNUeZuBd0:16874:0:99999:7:::\ndaemon:*:0:0:99999:7:::\n'
NEW_SHADOW = 'root:::0:99999:7:::\ndaemon:*:0:0:99999:7:::\n'

# The new system's config after 90-yun-network, before the import.
NEW = {
    'system': "config system\n\toption hostname 'Arduino'\n\toption timezone 'UTC'\n",
    'network': ("config interface 'wan'\n\toption device 'eth0'\n\toption proto 'dhcp'\n\n"
                "config interface 'lan'\n\toption proto 'static'\n\toption ipaddr '192.168.240.1'\n\n"
                "config interface 'wwan'\n\toption proto 'dhcp'\n"),
    'wireless': ("config wifi-device 'radio0'\n\toption type 'mac80211'\n\n"
                 "config wifi-iface 'yun_ap'\n\toption mode 'ap'\n\toption ssid 'Arduino Yun-90A2DAF054D2'\n\toption disabled '0'\n\n"
                 "config wifi-iface 'yun_sta'\n\toption mode 'sta'\n\toption network 'wwan'\n\toption encryption 'psk2'\n\toption disabled '1'\n"),
    'arduino': "config arduino\n\toption secure_rest_api 'true'\n\toption wifi_state 'ap'\n",
}

PROC = {
    'cpuinfo': 'system type\t\t: Atheros AR9330 rev 1\nmachine\t\t\t: Arduino Yun\n',
    'mtd': ('dev:    size   erasesize  name\n'
            'mtd0: 00040000 00010000 "u-boot"\nmtd1: 00010000 00010000 "u-boot-env"\n'
            'mtd2: 00e50000 00010000 "rootfs"\nmtd3: 00500000 00010000 "rootfs_data"\n'
            'mtd4: 00140000 00010000 "kernel"\nmtd5: 00010000 00010000 "nvram"\n'
            'mtd6: 00010000 00010000 "art"\nmtd7: 00f90000 00010000 "firmware"\n'),
    'meminfo': 'MemTotal:          60904 kB\nMemFree:            3248 kB\nMemAvailable:      30000 kB\n',
    'mounts': '/dev/root /rom squashfs ro 0 0\n/dev/sda /overlay ext4 rw 0 0\n',
}

HEXDUMP = '''#!/usr/bin/env python3
# Stand-in for busybox "hexdump -v -e '4/1 \\"%02x\\"'"
import sys
sys.stdout.write(sys.stdin.buffer.read().hex())
'''


def write(path, text, mode=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        f.write(text)
    if mode:
        os.chmod(path, mode)


@unittest.skipUnless(os.access(UCI, os.X_OK), 'no host uci (set UCI_BIN)')
class MigrateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        t = self.tmp.name
        self.bin = os.path.join(t, 'bin')
        write(os.path.join(self.bin, 'hexdump'), HEXDUMP, 0o755)
        write(os.path.join(self.bin, 'logger'), '#!/bin/sh\n', 0o755)
        write(os.path.join(self.bin, 'sysupgrade'), '#!/bin/sh\necho "sysupgrade $*" > "$SYSUPGRADE_LOG"\n', 0o755)
        # uci on the "new system": point it at that system's /etc/config.
        write(os.path.join(self.bin, 'uci'),
              f'#!/bin/sh\ncase " $* " in *" -c "*) exec {UCI} "$@";; esac\nexec {UCI} -c "$ROOT/etc/config" "$@"\n', 0o755)

        self.stock = os.path.join(t, 'stock')
        for name, text in STOCK.items():
            write(os.path.join(self.stock, 'etc', 'config', name), text)
        write(os.path.join(self.stock, 'etc', 'shadow'), STOCK_SHADOW)
        write(os.path.join(self.stock, 'etc', 'dropbear', 'dropbear_rsa_host_key'), 'RSAKEY')
        write(os.path.join(self.stock, 'etc', 'dropbear', 'dropbear_dss_host_key'), 'DSSKEY')
        write(os.path.join(self.stock, 'etc', 'dropbear', 'authorized_keys'), 'ssh-ed25519 AAAA me@pc\n')
        self.proc = os.path.join(t, 'proc')
        for name, text in PROC.items():
            write(os.path.join(self.proc, name), text)

        self.new = os.path.join(t, 'new')
        for name, text in NEW.items():
            write(os.path.join(self.new, 'etc', 'config', name), text)
        write(os.path.join(self.new, 'etc', 'shadow'), NEW_SHADOW)

        self.image = os.path.join(t, 'linino-upgrade.bin')
        with open(self.image, 'wb') as f:
            f.write(build_image())
        self.settings = os.path.join(t, 'yun-settings.tgz')

    def env(self, root):
        return dict(os.environ, PATH=self.bin + os.pathsep + os.environ['PATH'], ROOT=root,
                    PROC=self.proc, SYSUPGRADE_LOG=os.path.join(self.tmp.name, 'sysupgrade.log'))

    def migrate(self, *args):
        # yun-migrate writes its archive to /tmp/yun-settings.tgz; keep a copy.
        p = subprocess.run(['sh', MIGRATE, *args], capture_output=True, text=True, env=self.env(self.stock))
        if os.path.exists('/tmp/yun-settings.tgz'):
            shutil.move('/tmp/yun-settings.tgz', self.settings)
        return p

    def restore_and_import(self):
        # What preinit does with sysupgrade.tgz on first boot.
        with tarfile.open(self.settings) as tar:
            names = tar.getnames()
            tar.extractall(self.new, filter='data')
        p = subprocess.run(['sh', IMPORT], capture_output=True, text=True, env=self.env(self.new))
        self.assertEqual(p.returncode, 0, p.stderr)
        return names

    def uci(self, key):
        p = subprocess.run([UCI, '-q', '-c', os.path.join(self.new, 'etc', 'config'), 'get', key],
                           capture_output=True, text=True)
        return p.stdout.strip() if p.returncode == 0 else None

    def test_end_to_end(self):
        p = self.migrate('-t', self.image)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn('image OK', p.stdout)

        names = self.restore_and_import()
        # Only what we mean to carry over, and never the DSS key.
        self.assertIn('etc/dropbear/dropbear_rsa_host_key', names)
        self.assertNotIn('etc/dropbear/dropbear_dss_host_key', names)
        self.assertFalse(any(n.startswith('etc/config') for n in names))

        self.assertEqual(self.uci('system.@system[0].hostname'), 'workbench')
        self.assertEqual(self.uci('system.@system[0].zonename'), 'Australia/Sydney')
        self.assertEqual(self.uci('system.@system[0].timezone'), 'AEST-10AEDT,M10.1.0,M4.1.0/3')
        self.assertEqual(self.uci('wireless.radio0.country'), 'AU')
        self.assertEqual(self.uci('wireless.yun_sta.ssid'), 'Dan\'s "Workshop" $net')
        self.assertEqual(self.uci('wireless.yun_sta.encryption'), 'psk2')
        self.assertEqual(self.uci('wireless.yun_sta.key'), 'correct horse battery')
        self.assertEqual(self.uci('wireless.yun_sta.disabled'), '0')
        self.assertEqual(self.uci('wireless.yun_ap.disabled'), '1')
        self.assertEqual(self.uci('arduino.@arduino[0].wifi_state'), 'client')
        self.assertEqual(self.uci('arduino.@arduino[0].secure_rest_api'), 'false')
        self.assertEqual(self.uci('arduino.@arduino[0].socket_timeout'), '7')
        self.assertEqual(self.uci('network.wan.proto'), 'static')
        self.assertEqual(self.uci('network.wan.ipaddr'), '192.168.1.135')
        self.assertEqual(self.uci('network.wan.gateway'), '192.168.1.1')
        self.assertEqual(self.uci('network.wan.dns'), '1.1.1.1 9.9.9.9')
        self.assertEqual(self.uci('network.wan.device'), 'eth0')      # the new name is kept
        self.assertEqual(self.uci('network.wwan.proto'), 'dhcp')

        with open(os.path.join(self.new, 'etc', 'shadow')) as f:
            shadow = f.read()
        self.assertTrue(shadow.startswith('root:$1$Zq9e.abc$T8Pgq/7vD1n1/nNUeZuBd0::0:'), shadow)
        self.assertIn('daemon:*:', shadow)
        with open(os.path.join(self.new, 'etc', 'dropbear', 'authorized_keys')) as f:
            self.assertIn('me@pc', f.read())

        self.assertFalse(os.path.exists(os.path.join(self.new, 'etc', 'yun-migrate')))
        with open(os.path.join(self.new, 'etc', 'yun-migrate.log')) as f:
            log = f.read()
        self.assertIn('root password: kept', log)
        self.assertNotIn('correct horse', log)       # no secrets in the log

    def test_fallback_uses_the_saved_client_settings(self):
        write(os.path.join(self.stock, 'etc', 'config', 'wireless'),
              "config wifi-device 'radio0'\n\toption country 'AU'\n\n"
              "config wifi-iface\n\toption mode 'ap'\n\toption ssid 'Arduino'\n\toption encryption 'none'\n")
        arduino = STOCK['arduino'].replace("'clear'", "'timed_out'")
        write(os.path.join(self.stock, 'etc', 'config', 'arduino'), arduino)
        self.assertEqual(self.migrate('-t', self.image).returncode, 0)
        self.restore_and_import()
        self.assertEqual(self.uci('wireless.yun_sta.ssid'), 'Old network')
        self.assertEqual(self.uci('wireless.yun_sta.key'), 'old password')
        self.assertEqual(self.uci('arduino.@arduino[0].wifi_state'), 'client')

    def test_setup_mode_board_stays_in_setup_mode(self):
        write(os.path.join(self.stock, 'etc', 'config', 'wireless'),
              "config wifi-device 'radio0'\n\nconfig wifi-iface\n\toption mode 'ap'\n\toption ssid 'Arduino'\n")
        self.assertEqual(self.migrate('-t', self.image).returncode, 0)
        self.restore_and_import()
        self.assertEqual(self.uci('wireless.yun_sta.disabled'), '1')
        self.assertEqual(self.uci('wireless.yun_ap.disabled'), '0')
        self.assertEqual(self.uci('arduino.@arduino[0].wifi_state'), 'ap')

    def test_wep_network_falls_back_to_setup_mode(self):
        write(os.path.join(self.stock, 'etc', 'config', 'wireless'),
              "config wifi-iface\n\toption mode 'sta'\n\toption ssid 'Old'\n\toption encryption 'wep'\n\toption key '12345'\n")
        self.assertEqual(self.migrate('-t', self.image).returncode, 0)
        self.restore_and_import()
        self.assertEqual(self.uci('wireless.yun_sta.disabled'), '1')
        with open(os.path.join(self.new, 'etc', 'yun-migrate.log')) as f:
            self.assertIn("doesn't support", f.read())

    def test_flashes_with_the_settings(self):
        p = self.migrate('-y', self.image)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn('SD card', p.stdout)      # the fake board uses extroot
        with open(os.path.join(self.tmp.name, 'sysupgrade.log')) as f:
            self.assertEqual(f.read().strip(), f'sysupgrade -f /tmp/yun-settings.tgz {self.image}')

    def test_flash_without_settings(self):
        p = self.migrate('-y', '-n', self.image)
        self.assertEqual(p.returncode, 0, p.stderr)
        with open(os.path.join(self.tmp.name, 'sysupgrade.log')) as f:
            self.assertEqual(f.read().strip(), f'sysupgrade -n {self.image}')

    def test_checksum(self):
        import hashlib
        with open(self.image, 'rb') as f:
            good = hashlib.sha256(f.read()).hexdigest()
        self.assertEqual(self.migrate('-t', '-n', self.image, good.upper()).returncode, 0)
        p = self.migrate('-t', '-n', self.image, '0' * 64)
        self.assertEqual(p.returncode, 1)
        self.assertIn("SHA-256 doesn't match", p.stderr)

    def test_refuses_wrong_images_and_boards(self):
        bad = os.path.join(self.tmp.name, 'bad.bin')
        import gzip
        with open(bad, 'wb') as f:
            f.write(gzip.compress(gzip.decompress(build_image())[:0xe50000]))
        p = self.migrate('-t', bad)
        self.assertEqual(p.returncode, 1)
        self.assertIn('no loader', p.stderr)

        write(os.path.join(self.proc, 'mtd'), PROC['mtd'] + 'mtd8: 00140000 00010000 "loader"\n')
        p = self.migrate('-t', self.image)
        self.assertIn('already runs Yun 2026', p.stderr)

        write(os.path.join(self.proc, 'cpuinfo'), 'machine : TP-LINK TL-WR703N\n')
        p = self.migrate('-t', self.image)
        self.assertIn("doesn't look like an Arduino Yun", p.stderr)


if __name__ == '__main__':
    unittest.main()
