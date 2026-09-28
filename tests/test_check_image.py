#!/usr/bin/env python3
# Tests for tools/check-image.py, on synthetic images laid out the way the
# arduino_yun-2026 recipe builds linino-upgrade.bin.

import gzip
import os
import struct
import subprocess
import sys
import tempfile
import time
import unittest
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
CHECK = os.path.join(HERE, '..', 'tools', 'check-image.py')


def uimage(payload, magic, load=0x80060000, comp=3, name=b'test'):
    hdr = struct.pack('>IIIIIIIBBBB', magic, 0, int(time.time()), len(payload), load, load,
                      zlib.crc32(payload) & 0xffffffff, 5, 5, 2, comp) + name.ljust(32, b'\0')
    hcrc = zlib.crc32(hdr) & 0xffffffff
    return hdr[:4] + struct.pack('>I', hcrc) + hdr[8:] + payload


def pad_to(data, size, fill=b'\0'):
    return data + fill * (size - len(data))


def build(pad_fill=b'\xff', loader_magic=0x27051956, kernel_magic=0x68737173, marker=b'\xde\xad\xc0\xde',
          loader_size=20000, tail=True):
    kernel = uimage(os.urandom(200000), kernel_magic)
    img = pad_to(kernel, (len(kernel) + 0xffff) // 0x10000 * 0x10000)
    squash_used = 300000
    squash = b'hsqs' + b'\0' * 36 + struct.pack('<Q', squash_used)
    squash = pad_to(squash, squash_used, b'\x5a')
    img += squash
    img = pad_to(img, (len(img) + 0xffff) // 0x10000 * 0x10000)
    img += marker + b'\0' * (0x10000 - 4)      # padjffs2 fills the marker's block
    img = pad_to(img, 0xe50000, pad_fill)
    img += uimage(os.urandom(loader_size), loader_magic, name=b'loader')
    if tail:
        img = pad_to(img, 0xe60000, b'\xff')
        img = pad_to(img, 0xf90000, b'\xff')
    return gzip.compress(img)


class CheckImageTest(unittest.TestCase):
    def run_check(self, data):
        with tempfile.NamedTemporaryFile(suffix='.bin') as f:
            f.write(data)
            f.flush()
            p = subprocess.run([sys.executable, CHECK, f.name], capture_output=True, text=True)
        return p.returncode, p.stdout

    def test_good_image(self):
        rc, out = self.run_check(build())
        self.assertEqual(rc, 0, out)
        self.assertIn('All checks passed', out)

    def test_zero_padding_is_rejected(self):
        rc, out = self.run_check(build(pad_fill=b'\0'))
        self.assertEqual(rc, 1)
        self.assertIn('FAIL  the rest up to the loader is 0xff', out)

    def test_wrong_kernel_magic(self):
        rc, out = self.run_check(build(kernel_magic=0x27051956))
        self.assertEqual(rc, 1)
        self.assertIn('FAIL  first 4 bytes are "hsqs"', out)

    def test_missing_loader(self):
        rc, out = self.run_check(build(loader_magic=0x12345678))
        self.assertEqual(rc, 1)

    def test_old_image_without_the_erased_tail(self):
        rc, out = self.run_check(build(tail=False))
        self.assertEqual(rc, 1)
        self.assertIn('FAIL  all 0xff up to nvram', out)

    def test_loader_too_big(self):
        rc, out = self.run_check(build(loader_size=70000))
        self.assertEqual(rc, 1)
        self.assertIn('FAIL  loader fits', out)

    def test_free_space_counts_both_sides_of_the_loader(self):
        rc, out = self.run_check(build())
        self.assertIn('KB left for settings and packages (before and after the loader)', out)
        kb = int(out.split(' KB left')[0].split()[-1])
        self.assertGreater(kb, 14000)

    def test_missing_marker(self):
        rc, out = self.run_check(build(marker=b'\xff\xff\xff\xff'))
        self.assertEqual(rc, 1)
        self.assertIn('FAIL  0xdeadc0de marker', out)

    def test_corrupt_gzip_payload(self):
        data = bytearray(gzip.decompress(build()))
        data[100] ^= 0xff
        rc, out = self.run_check(gzip.compress(bytes(data)))
        self.assertEqual(rc, 1)
        self.assertIn('FAIL  data CRC', out)


if __name__ == '__main__':
    unittest.main()
