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
if a[a.index('-e') + 1] == '@.tag_name':
    print(d['tag_name'])
else:
    for x in d['assets']:
        print(x['browser_download_url'])
'''


class YunUpdateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        t = self.tmp.name
        b = os.path.join(t, 'bin')
        os.makedirs(b)
        for name, body in (('uclient-fetch', FAKE_FETCH), ('jsonfilter', FAKE_JSONFILTER),
                           ('sysupgrade', '#!/bin/sh\necho "sysupgrade $*" >> "$LOG"\n')):
            with open(os.path.join(b, name), 'w') as f:
                f.write(body)
            os.chmod(os.path.join(b, name), 0o755)
        os.makedirs(os.path.join(t, 'root', 'etc'))
        self.release = os.path.join(t, 'root', 'etc', 'yun_release')
        with open(self.release, 'w') as f:
            f.write("VERSION='2026.1'\n")
        self.log = os.path.join(t, 'log')
        self.env = dict(os.environ, PATH=b + os.pathsep + os.environ['PATH'],
                        ROOT=os.path.join(t, 'root'), LOG=self.log)

    def update(self, *args, **env):
        return subprocess.run(['sh', os.path.join(FILES, 'usr', 'bin', 'yun-update'), *args],
                              capture_output=True, text=True, env=dict(self.env, **env))

    def test_check(self):
        p = self.update('check', '--json')
        self.assertEqual(p.stdout.strip(), '{"current":"2026.1","latest":"2026.2","available":true}')
        with open(self.release, 'w') as f:
            f.write("VERSION='2026.2'\n")
        self.assertIn('"available":false', self.update('check', '--json').stdout)
        self.assertIn('Up to date', self.update('check').stdout)

    def test_apply_checks_the_download(self):
        p = self.update('apply')
        self.assertEqual(p.returncode, 0, p.stderr)
        with open(self.log) as f:
            self.assertEqual(f.read().splitlines(), ['sysupgrade -T /tmp/yun-update/sysupgrade.bin',
                                                     'sysupgrade /tmp/yun-update/sysupgrade.bin'])

    def test_damaged_download_is_not_installed(self):
        p = self.update('apply', IMAGE_DATA='DAMAGED')
        self.assertEqual(p.returncode, 1)
        self.assertIn('damaged', p.stderr)
        self.assertFalse(os.path.exists(self.log))
