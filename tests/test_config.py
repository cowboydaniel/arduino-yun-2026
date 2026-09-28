#!/usr/bin/env python3
# The kernel must be configured exactly like OpenWrt's official ath79/generic
# build, so that its version string (vermagic) matches and kmod-* packages
# from downloads.openwrt.org install with apk. Official builds use the
# defaults plus the two settings below (their config.buildinfo); anything
# else that changes the kernel's configuration breaks that.

import os
import re
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SEEDS = [os.path.join(HERE, '..', 'openwrt', name) for name in ('config.seed', 'config-yun.seed')]

# What the official 25.12 build sets that changes the kernel.
OFFICIAL = {
    'CONFIG_ALL_KMODS': 'y',
    'CONFIG_KERNEL_KALLSYMS': None,      # "is not set"
    'CONFIG_DEVEL': 'y',                 # allows kmod-usb-test
    # RTC drivers other ath79 boards include: they turn on CONFIG_RTC_CLASS.
    'CONFIG_PACKAGE_kmod-rtc-ds1307': 'm',
    'CONFIG_PACKAGE_kmod-rtc-ds1374': 'm',
    'CONFIG_PACKAGE_kmod-rtc-pcf8563': 'm',
}


def settings():
    out = {}
    for path in SEEDS:
        with open(path) as f:
            for line in f:
                m = re.match(r'^(CONFIG_[\w.+-]+)=(.*)$', line) or re.match(r'^# (CONFIG_[\w.+-]+) is not set$', line)
                if m:
                    out[m.group(1)] = m.group(2) if m.lastindex == 2 else None
    return out


class OfficialKernelTest(unittest.TestCase):
    def test_same_kernel_settings_as_official(self):
        s = settings()
        for key, value in OFFICIAL.items():
            self.assertIn(key, s)
            self.assertEqual(s[key], value, key)
        extra = sorted(k for k in s if k.startswith('CONFIG_KERNEL_') and k not in OFFICIAL)
        self.assertEqual(extra, [], 'kernel options that differ from the official build')

    def test_no_kernel_modules_left_out(self):
        # With ALL_KMODS every module is built; deselecting one changes the
        # kernel's configuration.
        s = settings()
        dropped = sorted(k for k, v in s.items() if k.startswith('CONFIG_PACKAGE_kmod-') and v is None)
        self.assertEqual(dropped, [])


if __name__ == '__main__':
    unittest.main()
