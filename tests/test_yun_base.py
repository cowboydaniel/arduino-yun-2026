#!/usr/bin/env python3
# Tests for yun-base's sketch tools: merge-sketch-with-bootloader.lua and
# run-avrdude, run with a fake avrdude and a fake GPIO sysfs directory.

import os
import stat
import subprocess
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
FILES = os.path.join(HERE, '..', 'feed', 'yun-base', 'files')
MERGE = os.path.join(FILES, 'usr', 'bin', 'merge-sketch-with-bootloader.lua')
RUN_AVRDUDE = os.path.join(FILES, 'usr', 'bin', 'run-avrdude')
BOOTLOADER = os.path.join(FILES, 'etc', 'arduino', 'Caterina-Yun.hex')

SKETCH = (':100000000C945C000C946E000C946E000C946E00CA\r\n'
          ':100010000C946E000C946E000C946E000C946E00A8\r\n'
          ':00000001FF\r\n')


def original_merge(sketch_text):
    """What the stock Lua script produced: drop the last line, append the
    bootloader, strip CR/LF/spaces and empty lines."""
    lines = sketch_text.split('\n')
    if lines and lines[-1] == '':
        lines.pop()
    lines.pop()
    with open(BOOTLOADER) as f:
        lines += f.read().split('\n')
    out = [l.replace('\r', '').replace(' ', '') for l in lines]
    return ''.join(l + '\n' for l in out if l)


class MergeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.hex = os.path.join(self.tmp.name, 'sketch.hex')
        with open(self.hex, 'w', newline='') as f:
            f.write(SKETCH)

    def merge(self):
        return subprocess.run([MERGE, self.hex], capture_output=True, text=True,
                              env=dict(os.environ, BOOTLOADER=BOOTLOADER))

    def test_same_result_as_the_lua_script(self):
        self.assertEqual(self.merge().returncode, 0)
        with open(self.hex) as f:
            self.assertEqual(f.read(), original_merge(SKETCH))

    def test_only_one_eof_record_at_the_end(self):
        self.merge()
        with open(self.hex) as f:
            lines = f.read().splitlines()
        eof = [l for l in lines if l[7:9] == '01']
        self.assertEqual(eof, [lines[-1]])

    def test_merging_twice_changes_nothing(self):
        self.merge()
        with open(self.hex) as f:
            once = f.read()
        self.assertEqual(self.merge().returncode, 0)
        with open(self.hex) as f:
            self.assertEqual(f.read(), once)

    def test_missing_file(self):
        p = subprocess.run([MERGE, os.path.join(self.tmp.name, 'nope.hex')], capture_output=True, text=True)
        self.assertEqual(p.returncode, 1)
        p = subprocess.run([MERGE], capture_output=True, text=True)
        self.assertEqual(p.returncode, 1)


FAKE_AVRDUDE = r'''#!/bin/sh
echo "spi=$(cat "$GPIO_SYSFS/yun:oe:spi/value") $*" >> "$LOG"
case "$*" in
*efuse:r:-:d*) echo "${EFUSE_READ:-203}" ;;
esac
exit 0
'''


class RunAvrdudeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        t = self.tmp.name
        os.makedirs(os.path.join(t, 'gpio', 'yun:oe:spi'))
        self.spi = os.path.join(t, 'gpio', 'yun:oe:spi', 'value')
        with open(self.spi, 'w') as f:
            f.write('0\n')
        self.avrdude = os.path.join(t, 'avrdude')
        with open(self.avrdude, 'w') as f:
            f.write(FAKE_AVRDUDE)
        os.chmod(self.avrdude, 0o755)
        self.hex = os.path.join(t, 'sketch.hex')
        with open(self.hex, 'w') as f:
            f.write(':00000001FF\n')
        self.log = os.path.join(t, 'log')

    def run_avrdude(self, *args, efuse='203'):
        env = dict(os.environ, YUN_SHARE=os.path.join(FILES, 'usr', 'share', 'yun'),
                   GPIO_SYSFS=os.path.join(self.tmp.name, 'gpio'), AVRDUDE_BIN=self.avrdude,
                   LOG=self.log, EFUSE_READ=efuse)
        p = subprocess.run([RUN_AVRDUDE, self.hex, *args], capture_output=True, text=True, env=env)
        try:
            with open(self.log) as f:
                calls = f.read().splitlines()
        except FileNotFoundError:
            calls = []
        return p, calls

    def flash_call(self, calls):
        return calls[-1]

    def test_ide_style_call(self):
        # What the Arduino IDE runs for a network upload.
        p, calls = self.run_avrdude('-q', '-q', '-patmega32u4')
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(len(calls), 2)
        self.assertIn('efuse:r:-:d', calls[0])
        flash = self.flash_call(calls)
        self.assertTrue(flash.startswith('spi=1 '), flash)     # level shifter on while flashing
        self.assertIn('-c yun -P gpiochip0 -p m32u4', flash)
        self.assertIn('-U lfuse:w:0xff:m -U hfuse:w:0xd8:m', flash)
        self.assertNotIn('efuse:w', flash)                     # 0xCB is already right
        self.assertIn(f'-U flash:w:{self.hex}:i', flash)
        self.assertTrue(flash.endswith('-q -q'))
        with open(self.spi) as f:
            self.assertEqual(f.read().strip(), '0')            # and off again afterwards

    def test_stock_config_style_call(self):
        # /etc/config/arduino on stock: run-avrdude /tmp/sketch.hex '-q -q' -pm32u4
        p, calls = self.run_avrdude('-q -q', '-pm32u4')
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn('-p m32u4', self.flash_call(calls))

    def test_separate_option_value(self):
        p, calls = self.run_avrdude('-p', 'm32u4', '-v')
        self.assertEqual(p.returncode, 0, p.stderr)
        flash = self.flash_call(calls)
        self.assertIn('-p m32u4', flash)
        self.assertTrue(flash.endswith('-v'))

    def test_wrong_efuse_is_fixed(self):
        p, calls = self.run_avrdude(efuse='255')
        self.assertIn('-U efuse:w:0xcb:m', self.flash_call(calls))
        p, calls = self.run_avrdude(efuse='251')       # 0xFB: same low nibble
        self.assertNotIn('efuse:w', self.flash_call(calls))

    def test_other_mcu_gets_no_default_fuses(self):
        p, calls = self.run_avrdude('-patmega328p')
        self.assertEqual(len(calls), 1)
        self.assertNotIn('fuse', calls[0])
        self.assertIn('-p atmega328p', calls[0])

    def test_missing_value(self):
        p, calls = self.run_avrdude('-p')
        self.assertEqual(p.returncode, 1)
        self.assertEqual(calls, [])

    def test_not_a_yun(self):
        os.remove(self.spi)
        p, calls = self.run_avrdude()
        self.assertEqual(p.returncode, 1)
        self.assertIn('is this an Arduino Yun', p.stderr)
        self.assertEqual(calls, [])



HOOK_HARNESS = r"""#!/bin/sh
# Stands in for /sbin/sysupgrade: sets what it has parsed by the time it
# sources /lib/upgrade, then sources the hook.
TEST=${TEST:-0} HELP=0 CONF_BACKUP_LIST=0 CONF_BACKUP=${CONF_BACKUP:-} CONF_RESTORE=
IMAGE=$1
v() { echo "$*"; }
. "$HOOK"
echo "stage1 done"
exit ${STAGE1_EXIT:-0}
"""


class FreeRamHookTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        t = self.tmp.name
        self.calls = os.path.join(t, 'calls')
        self.initd = os.path.join(t, 'init.d')
        os.makedirs(self.initd)
        for s in ('uhttpd', 'rpcd', 'umdns', 'cron', 'odhcpd', 'dnsmasq', 'network'):
            path = os.path.join(self.initd, s)
            with open(path, 'w') as f:
                f.write(f'#!/bin/sh\necho "{s} $1" >> "{self.calls}"\n')
            os.chmod(path, 0o755)
        self.proc = os.path.join(t, 'proc')
        os.makedirs(os.path.join(self.proc, 'sys', 'vm'))
        os.makedirs(os.path.join(self.proc, '1'))
        with open(os.path.join(self.proc, 'meminfo'), 'w') as f:
            f.write('MemAvailable:      19636 kB\n')
        self.harness = os.path.join(t, 'sysupgrade')      # $0 must be sysupgrade
        with open(self.harness, 'w') as f:
            f.write(HOOK_HARNESS)
        os.chmod(self.harness, 0o755)

    def run_sysupgrade(self, *args, pid1='/sbin/procd', **env):
        exe = os.path.join(self.proc, '1', 'exe')
        if os.path.lexists(exe):
            os.remove(exe)
        os.symlink(pid1, exe)
        return subprocess.run([self.harness, *args], capture_output=True, text=True, env=dict(
            os.environ, HOOK=os.path.join(FILES, 'lib', 'upgrade', 'yun-free-ram.sh'),
            YUN_INITD=self.initd, YUN_PROC=self.proc, **env))

    def recorded(self):
        try:
            with open(self.calls) as f:
                return f.read().splitlines()
        except FileNotFoundError:
            return []

    def test_upgrade_frees_memory_and_leaves_it_to_stage2(self):
        p = self.run_sysupgrade('/tmp/sysupgrade.bin', pid1='/tmp/root/sbin/upgraded')
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn('19636 KB available', p.stdout)
        self.assertEqual(self.recorded(), ['uhttpd stop', 'rpcd stop', 'umdns stop', 'cron stop',
                                           'odhcpd stop', 'dnsmasq stop'])   # nothing restarted
        with open(os.path.join(self.proc, 'sys', 'vm', 'drop_caches')) as f:
            self.assertEqual(f.read().strip(), '3')

    def test_failed_upgrade_restarts_services(self):
        p = self.run_sysupgrade('/tmp/sysupgrade.bin', STAGE1_EXIT='1')
        self.assertEqual(p.returncode, 1)
        self.assertEqual(self.recorded()[6:], ['uhttpd start', 'rpcd start', 'umdns start', 'cron start',
                                               'odhcpd start', 'dnsmasq start'])

    def test_url_keeps_dns_for_the_download(self):
        self.run_sysupgrade('https://example.com/sysupgrade.bin', pid1='/tmp/root/sbin/upgraded')
        self.assertNotIn('dnsmasq stop', self.recorded())

    def test_checks_and_backups_leave_services_alone(self):
        self.run_sysupgrade('/tmp/sysupgrade.bin', TEST='1')
        self.run_sysupgrade('', CONF_BACKUP='/tmp/backup.tgz')
        self.run_sysupgrade('')
        self.assertEqual(self.recorded(), [])
        self.assertNotIn('network stop', self.recorded())


class LuciRedirectTest(unittest.TestCase):
    def test_old_panel_url_goes_to_the_new_one(self):
        cgi = os.path.join(HERE, '..', 'feed', 'yun-webpanel', 'files', 'www', 'cgi-bin', 'luci')
        out = subprocess.run([cgi], capture_output=True).stdout
        head = out.split(b'\r\n\r\n')[0].split(b'\r\n')
        self.assertEqual(head[0], b'Status: 302 Found')
        self.assertIn(b'Location: /?yun', head)


if __name__ == '__main__':
    unittest.main()


FAKE_FETCH = r'''#!/bin/sh
# uclient-fetch -q -T 30 -O <out> <url>, answering like GitHub.
out=$5; url=$6
name=openwrt-ath79-generic-arduino_yun-2026-squashfs-sysupgrade.bin
case "$url" in
*releases/latest) cat > "$out" <<J
{"tag_name":"v2026.2","assets":[
 {"browser_download_url":"https://github.com/x/releases/download/v2026.2/$name"},
 {"browser_download_url":"https://github.com/x/releases/download/v2026.2/SHA256SUMS"}]}
J
;;
*sysupgrade.bin) printf '%s' "${IMAGE_DATA:-IMAGE}" > "$out" ;;
*SHA256SUMS) printf '%s  %s\n' "$(printf IMAGE | sha256sum | cut -d' ' -f1)" "$name" > "$out" ;;
*) exit 1 ;;
esac
'''

FAKE_JSONFILTER = r'''#!/usr/bin/env python3
import json, sys
a = sys.argv[1:]
d = json.load(open(a[a.index('-i') + 1]))
e = a[a.index('-e') + 1]
if e == '@.tag_name':
    print(d['tag_name'])
elif e.endswith('.size'):
    print(11862289)
else:
    for x in d['assets']:
        print(x['browser_download_url'])
'''

FAKE_SYSUPGRADE = '''#!/bin/sh
echo "sysupgrade $*" >> "$CALLS"
case "$1" in -T) exit 0 ;; esac
exit ${SYSUPGRADE_EXIT:-0}
'''


class SdSwapTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        t = self.tmp.name
        b = os.path.join(t, 'bin')
        os.makedirs(b)
        self.calls = os.path.join(t, 'calls')
        self.proc = os.path.join(t, 'proc')
        os.makedirs(self.proc)
        self.card = os.path.join(t, 'card')
        os.makedirs(self.card)
        self.swapfile = os.path.join(self.card, 'yun-swapfile')
        fakes = {
            'uci': '#!/bin/sh\ncase "$3" in\n*sd_swap) printf %s "$SD_SWAP" ;;\n*sd_swap_size) printf %s "$SD_SWAP_SIZE" ;;\nesac\n',
            'mkswap': '#!/bin/sh\necho "mkswap $*" >> "$CALLS"\n',
            'swapon': '#!/bin/sh\necho "swapon $*" >> "$CALLS"\nprintf "%s file 1024 0 10\\n" "$3" >> "$PROC/swaps"\n',
            'swapoff': '#!/bin/sh\necho "swapoff $*" >> "$CALLS"\n',
            'logger': '#!/bin/sh\n',
        }
        for name, body in fakes.items():
            with open(os.path.join(b, name), 'w') as f:
                f.write(body)
            os.chmod(os.path.join(b, name), 0o755)
        self.bin = b
        self.set_mounts(f'/dev/sda1 {self.card} vfat rw,relatime,fmask=0022 0 0')
        with open(os.path.join(self.proc, 'swaps'), 'w') as f:
            f.write('Filename\t\t\t\tType\t\tSize\t\tUsed\t\tPriority\n/dev/zram0 partition 27808 0 100\n')
        with open(os.path.join(self.proc, 'uptime'), 'w') as f:
            f.write('300.12 250.00\n')
        self.env = dict(os.environ, PATH=b + os.pathsep + os.environ['PATH'], PROC=self.proc,
                        CALLS=self.calls, SD_SWAP='', SD_SWAP_SIZE='1')

    def set_mounts(self, *lines):
        with open(os.path.join(self.proc, 'mounts'), 'w') as f:
            f.write('/dev/root /rom squashfs ro,relatime 0 0\ntmpfs /tmp tmpfs rw,nosuid,nodev 0 0\n')
            f.write(''.join(l + '\n' for l in lines))

    def sdswap(self, *args, **env):
        return subprocess.run(['sh', os.path.join(FILES, 'usr', 'bin', 'yun-sdswap'), *args],
                              capture_output=True, text=True, env=dict(self.env, **env), timeout=20)

    def recorded(self):
        try:
            with open(self.calls) as f:
                return f.read().splitlines()
        except FileNotFoundError:
            return []

    def test_boot_makes_and_uses_a_swap_file(self):
        p = self.sdswap('wait')
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(os.path.getsize(self.swapfile), 1048576)
        self.assertEqual(stat.S_IMODE(os.stat(self.swapfile).st_mode), 0o600)
        self.assertEqual(self.recorded(), [f'mkswap {self.swapfile}', f'swapon -p 10 {self.swapfile}'])
        self.assertIn(f'swapping to {self.swapfile}', self.sdswap('status').stdout)

    def test_default_size_is_256_mb(self):
        with open(os.path.join(self.bin, 'dd'), 'w') as f:
            f.write('#!/bin/sh\necho "dd $*" >> "$CALLS"\nexit 1\n')
        os.chmod(os.path.join(self.bin, 'dd'), 0o755)
        with open(os.path.join(self.bin, 'df'), 'w') as f:
            f.write('#!/bin/sh\necho "Filesystem 1024-blocks Used Available Capacity Mounted on"\n'
                    'echo "/dev/sda1 60000000 1000 59999000 1% /mnt/sda1"\n')
        os.chmod(os.path.join(self.bin, 'df'), 0o755)
        p = self.sdswap('start', SD_SWAP_SIZE='')
        self.assertEqual(p.returncode, 1)
        self.assertIn('making a 256 MB swap file', p.stdout)
        self.assertIn('count=256', self.recorded()[0])
        self.assertFalse(os.path.exists(self.swapfile + '.new'))

    def test_existing_file_is_reused(self):
        with open(self.swapfile, 'wb') as f:
            f.write(b'S' * 1048576)
        self.sdswap('start')
        with open(self.swapfile, 'rb') as f:
            self.assertEqual(f.read(1), b'S')      # not written again
        self.assertEqual(self.recorded(), [f'mkswap {self.swapfile}', f'swapon -p 10 {self.swapfile}'])

    def test_file_of_another_size_is_replaced(self):
        with open(self.swapfile, 'wb') as f:
            f.write(b'S' * 4096)
        self.sdswap('start', SD_SWAP_SIZE='2')
        self.assertEqual(os.path.getsize(self.swapfile), 2 * 1048576)

    def test_already_swapping(self):
        self.sdswap('start')
        self.assertEqual(self.sdswap('start').returncode, 0)
        self.assertEqual(len(self.recorded()), 2)

    def test_no_card(self):
        self.set_mounts()
        p = self.sdswap('wait')                 # uptime is past two minutes
        self.assertEqual(p.returncode, 0)
        self.assertEqual(self.recorded(), [])
        self.assertIn('no SD card', self.sdswap('start').stdout)

    def test_only_writable_card_filesystems(self):
        self.set_mounts(f'/dev/sda1 {self.card} vfat ro,relatime 0 0',
                        f'/dev/sdb1 {self.card} iso9660 rw 0 0',
                        f'/dev/mtdblock6 {self.card} jffs2 rw 0 0')
        self.assertEqual(self.sdswap('start').returncode, 1)
        self.assertEqual(self.recorded(), [])
        self.set_mounts(f'/dev/sda2 {self.card} exfat rw,relatime 0 0')
        self.assertEqual(self.sdswap('start').returncode, 0)

    def test_not_enough_space(self):
        with open(os.path.join(self.bin, 'df'), 'w') as f:
            f.write('#!/bin/sh\necho "Filesystem 1024-blocks Used Available Capacity Mounted on"\n'
                    'echo "/dev/sda1 100000 99000 1000 99% /mnt/sda1"\n')
        os.chmod(os.path.join(self.bin, 'df'), 0o755)
        p = self.sdswap('wait')
        self.assertEqual(p.returncode, 0)
        self.assertIn('not enough space', p.stdout)
        self.assertFalse(os.path.exists(self.swapfile))
        self.assertEqual(self.recorded(), [])

    def test_turned_off(self):
        p = self.sdswap('wait', SD_SWAP='0')
        self.assertEqual(self.recorded(), [])
        self.assertFalse(os.path.exists(self.swapfile))

    def test_stop(self):
        self.sdswap('start')
        self.assertEqual(self.sdswap('stop').returncode, 0)
        self.assertEqual(self.recorded()[-1], f'swapoff {self.swapfile}')


class YunUpdateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        t = self.tmp.name
        b = os.path.join(t, 'bin')
        os.makedirs(b)
        self.calls = os.path.join(t, 'calls')
        for name, body in (('uclient-fetch', FAKE_FETCH), ('jsonfilter', FAKE_JSONFILTER),
                           ('sysupgrade', FAKE_SYSUPGRADE), ('logger', '#!/bin/sh\n')):
            with open(os.path.join(b, name), 'w') as f:
                f.write(body)
            os.chmod(os.path.join(b, name), 0o755)
        root = os.path.join(t, 'root')
        os.makedirs(os.path.join(root, 'etc', 'init.d'))
        for s in ('uhttpd', 'rpcd', 'umdns', 'cron', 'odhcpd', 'dnsmasq'):
            path = os.path.join(root, 'etc', 'init.d', s)
            with open(path, 'w') as f:
                f.write(f'#!/bin/sh\necho "{s} $1" >> "$CALLS"\n')
            os.chmod(path, 0o755)
        self.release = os.path.join(root, 'etc', 'yun_release')
        with open(self.release, 'w') as f:
            f.write("VERSION='2026.1'\n")
        self.proc = os.path.join(t, 'proc')
        os.makedirs(os.path.join(self.proc, 'sys', 'vm'))
        self.set_memory(19636)
        self.env = dict(os.environ, PATH=b + os.pathsep + os.environ['PATH'], ROOT=root, PROC=self.proc,
                        CALLS=self.calls, CONSOLE='', YUN_UPDATE_LOG=os.path.join(t, 'update.log'))

    def set_memory(self, available_kb):
        with open(os.path.join(self.proc, 'meminfo'), 'w') as f:
            f.write(f'MemTotal:          55624 kB\nMemFree:            3000 kB\nMemAvailable:   {available_kb:8d} kB\n')

    def update(self, *args, **env):
        return subprocess.run(['sh', os.path.join(FILES, 'usr', 'bin', 'yun-update'), *args],
                              capture_output=True, text=True, env=dict(self.env, **env))

    def recorded(self):
        try:
            with open(self.calls) as f:
                return f.read().splitlines()
        except FileNotFoundError:
            return []

    def test_check(self):
        p = self.update('check', '--json')
        self.assertEqual(p.stdout.strip(), '{"current":"2026.1","latest":"2026.2","available":true}')
        with open(self.release, 'w') as f:
            f.write("VERSION='2026.2'\n")
        self.assertIn('"available":false', self.update('check', '--json').stdout)
        self.assertIn('Up to date', self.update('check').stdout)

    def stage(self):
        with open('/tmp/yun-update/stage') as f:
            return f.read().strip()

    def test_apply_frees_memory_then_installs(self):
        p = self.update('apply')
        self.assertEqual(p.returncode, 0, p.stderr)
        # The web panel stays up to show the progress; sysupgrade's own hook
        # stops it before writing the flash.
        self.assertEqual(self.recorded(), [
            'umdns stop', 'cron stop', 'odhcpd stop',
            'sysupgrade -T /tmp/yun-update/sysupgrade.bin',
            'dnsmasq stop',        # only after the download, which needs DNS
            'sysupgrade -v /tmp/yun-update/sysupgrade.bin'])
        self.assertIn('download verified', p.stdout)
        self.assertEqual(self.stage(), 'installing')
        with open('/tmp/yun-update/total') as f:
            self.assertEqual(f.read().strip(), '11862289')

    def test_tight_memory_stops_the_web_panel_too(self):
        self.set_memory(16000)     # 11584 KB image + 5120 KB margin needed
        p = self.update('apply')
        calls = self.recorded()
        self.assertEqual(calls[:5], ['umdns stop', 'cron stop', 'odhcpd stop', 'uhttpd stop', 'rpcd stop'])
        self.assertIn('stopping the web panel', p.stdout)

    def test_not_enough_memory(self):
        self.set_memory(12000)
        p = self.update('apply')
        self.assertEqual(p.returncode, 1)
        self.assertIn('not enough free RAM', p.stderr)
        calls = self.recorded()
        self.assertFalse(any(c.startswith('sysupgrade') for c in calls))
        self.assertEqual(calls[-5:], ['umdns start', 'cron start', 'odhcpd start', 'uhttpd start', 'rpcd start'])
        self.assertEqual(self.stage(), 'failed')

    def test_damaged_download_is_not_installed(self):
        p = self.update('apply', IMAGE_DATA='DAMAGED')
        self.assertEqual(p.returncode, 1)
        self.assertIn('damaged', p.stderr)
        calls = self.recorded()
        self.assertFalse(any(c.startswith('sysupgrade') for c in calls))
        self.assertNotIn('uhttpd stop', calls)           # the panel stayed up to show it
        self.assertIn('odhcpd start', calls)
        self.assertEqual(self.stage(), 'failed')
        self.assertFalse(os.path.exists('/tmp/yun-update/sysupgrade.bin'))

    def test_failed_sysupgrade_restarts_services(self):
        p = self.update('apply', SYSUPGRADE_EXIT='1')
        self.assertEqual(p.returncode, 1)
        calls = self.recorded()
        self.assertIn('dnsmasq start', calls)
        self.assertIn('umdns start', calls)
