#!/usr/bin/env python3
# Check a linino-upgrade.bin before it goes anywhere near a Yun.
#
#   tools/check-image.py bin/targets/ath79/generic/*arduino_yun-2026*linino-upgrade.bin
#
# The image is written by the stock Linino sysupgrade into the "firmware"
# partition (flash 0x50000-0xfe0000). This checks everything that has to be
# right for the stock U-Boot, the loader and the new kernel to find each
# other, using the layout in docs/design.md.

import gzip
import struct
import sys
import zlib

FIRMWARE_OFFSET = 0x050000        # start of the firmware partition in flash
FIRMWARE_SIZE = 0xf90000          # 15936k, up to nvram at 0xfe0000
LOADER_OFFSET = 0xea0000 - FIRMWARE_OFFSET   # the old kernel slot U-Boot boots
ERASE_BLOCK = 0x10000
KERNEL_MAGIC = 0x68737173         # "hsqs", what stock sysupgrade checks for
UIMAGE_MAGIC = 0x27051956
LOADADDR = 0x80060000
JFFS2_EOF = b'\xde\xad\xc0\xde'

IH_COMP = {0: 'none', 1: 'gzip', 2: 'bzip2', 3: 'lzma'}

failures = 0


def check(ok, what):
    global failures
    print(('  ok    ' if ok else '  FAIL  ') + what)
    if not ok:
        failures += 1
    return ok


def uimage(data, offset, magic, name):
    """Check a legacy uImage header and payload; return (header, end offset)."""
    print(f'{name} at 0x{offset:06x} (flash 0x{FIRMWARE_OFFSET + offset:06x}):')
    hdr = data[offset:offset + 64]
    if not check(len(hdr) == 64, 'header is complete'):
        return None, offset
    (ih_magic, ih_hcrc, ih_time, ih_size, ih_load, ih_ep, ih_dcrc,
     ih_os, ih_arch, ih_type, ih_comp) = struct.unpack('>IIIIIIIBBBB', hdr[:32])
    ih_name = hdr[32:64].split(b'\0')[0].decode('ascii', 'replace')
    check(ih_magic == magic, f'magic 0x{ih_magic:08x} (want 0x{magic:08x})')
    zeroed = hdr[:4] + b'\0\0\0\0' + hdr[8:]
    check(zlib.crc32(zeroed) & 0xffffffff == ih_hcrc, 'header CRC')
    payload = data[offset + 64:offset + 64 + ih_size]
    check(len(payload) == ih_size, f'payload is complete ({ih_size} bytes)')
    check(zlib.crc32(payload) & 0xffffffff == ih_dcrc, 'data CRC')
    check(ih_os == 5 and ih_arch == 5 and ih_type == 2, 'Linux/MIPS kernel image')
    check(ih_comp == 3, f'lzma compressed (is {IH_COMP.get(ih_comp, ih_comp)}), as the stock U-Boot boots')
    check(ih_load == LOADADDR and ih_ep == LOADADDR,
          f'load and entry address 0x{ih_load:08x}/0x{ih_ep:08x} (stock kernel uses 0x{LOADADDR:08x})')
    print(f'        name "{ih_name}"')
    return ih_size, offset + 64 + ih_size


def main(path):
    raw = open(path, 'rb').read()
    print(f'{path}: {len(raw)} bytes')
    try:
        data = gzip.decompress(raw)
        print(f'  gzip, {len(data)} bytes decompressed (stock get_image unpacks it on the fly)')
    except OSError:
        data = raw
        print('  not gzipped')

    print('Stock Linino platform_check_image:')
    check(data[:4] == b'hsqs', f'first 4 bytes are "hsqs" (are {data[:4]!r})')

    size, kernel_end = uimage(data, 0, KERNEL_MAGIC, 'Kernel')

    # The rootfs starts at the next erase block after the kernel.
    rootfs = (kernel_end + ERASE_BLOCK - 1) // ERASE_BLOCK * ERASE_BLOCK
    print(f'Root filesystem at 0x{rootfs:06x}:')
    if check(data[rootfs:rootfs + 4] == b'hsqs', 'squashfs magic'):
        bytes_used = struct.unpack('<Q', data[rootfs + 40:rootfs + 48])[0]
        print(f'        squashfs is {bytes_used} bytes')
        marker = rootfs + (bytes_used + ERASE_BLOCK - 1) // ERASE_BLOCK * ERASE_BLOCK
        print(f'rootfs_data at 0x{marker:06x} (flash 0x{FIRMWARE_OFFSET + marker:06x}):')
        check(data[marker:marker + 4] == JFFS2_EOF,
              '0xdeadc0de marker, where stock "mtd -j" writes the saved settings')
        check(marker + ERASE_BLOCK <= LOADER_OFFSET, 'room for rootfs_data before the loader')
        pad = data[marker + 4:LOADER_OFFSET]
        # padjffs2 fills the rest of the marker's block itself; everything
        # after that should be erased flash.
        check(pad.count(0xff) >= len(pad) - ERASE_BLOCK, 'the rest up to the loader is 0xff (erased flash)')
        free = LOADER_OFFSET - marker
        print(f'        {free // 1024} KB left for settings and packages')

    check(len(data) > LOADER_OFFSET, 'image reaches the loader slot')
    uimage(data, LOADER_OFFSET, UIMAGE_MAGIC, 'Loader')

    print('Size:')
    check(len(data) <= FIRMWARE_SIZE,
          f'{len(data)} bytes fits the firmware partition ({FIRMWARE_SIZE} bytes, up to nvram)')

    print()
    if failures:
        print(f'{failures} check(s) FAILED: do not flash this image.')
        return 1
    print('All checks passed.')
    return 0


if __name__ == '__main__':
    if len(sys.argv) != 2:
        print(__doc__ or 'usage: check-image.py <linino-upgrade.bin>', file=sys.stderr)
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
