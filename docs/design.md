# Design and findings

This page records what we know about the Yún, and how the in-place update is meant to work. Nothing here has been tested on hardware yet.

## Requirements

- **In-place update.** A Yún running stock Linino must move to the new firmware without a serial cable, U-Boot prompt or TFTP. It works like a normal Linux update: you run the update, then reboot to apply it.
- **No extra storage.** It must work on a board with no SD card or USB storage.
- **Keep settings** across the update where possible: Wi-Fi, hostname, root password, SSH keys, time zone and REST password.
- **Only the Yún Rev1** is supported.

## Findings

### Upstream OpenWrt already supports the Yún

OpenWrt 25.12.5 has an `arduino_yun` device in `ath79/generic`, with a device tree at `target/linux/ath79/dts/ar9331_arduino_yun.dts`. It includes:

- the console on `ttyATH0` at 250000 baud, the same UART the Bridge uses
- the 32U4 level shifters exported as named GPIOs: `yun:oe:spi` (GPIO 21), `yun:oe:hs` (GPIO 22) and `yun:oe:uart` (GPIO 23, active low)
- the WLAN RST button (`config`, GPIO 20), the USB LED and USB host

The upstream image **can't** be installed in place, though. It expects a different flash layout, and the OpenWrt wiki method replaces U-Boot over serial and TFTP.

Upstream image sizes for 25.12.5 (from the release sysupgrade image):

| Part | Size |
| --- | --- |
| Kernel (lzma uImage) | 2,741,496 bytes (~2.6 MB) |
| squashfs rootfs | 4,382,914 bytes (~4.2 MB) |

OpenWrt 25.12 uses `apk` as its package manager, not `opkg`.

### Stock Linino layout

This was read from a stock Yún running Linino 1.5.3 (`built=Fri May 13 09:22:20 UTC 2016`, kernel 3.18.23):

```
mtdparts=spi0.0:256k(u-boot)ro,64k(u-boot-env)ro,14656k(rootfs),1280k(kernel),64k(nvram),64k(art),15936k@0x50000(firmware)
```

| Offset | Size | Name |
| --- | --- | --- |
| 0x000000 | 256k | u-boot (ro) |
| 0x040000 | 64k | u-boot-env (ro) |
| 0x050000 | 14656k | rootfs (squashfs + jffs2 rootfs_data) |
| 0xea0000 | 1280k | kernel |
| 0xfe0000 | 64k | nvram |
| 0xff0000 | 64k | art (Wi-Fi calibration, MAC) |

- The stock U-Boot (1.1.4) boots with `bootm 0x9fea0000`, so it always starts the uImage at 0xea0000. This value is compiled in (`CONFIG_BOOTCOMMAND` in `uboot-linino/patches/005-linino-16M.patch`), and the board has no saved U-Boot environment (`fw_printenv` fails).
- u-boot and u-boot-env are read-only from Linux, so the bootloader can't be changed in place. It shouldn't be anyway.
- The modern kernel (~2.6 MB) doesn't fit in the 1280k kernel slot.

Other details from the same board (full output in [`stock-linino-probe.txt`](stock-linino-probe.txt), and copies of the stock upgrade scripts in [`stock-linino/`](stock-linino/)):

| Item | Version |
| --- | --- |
| U-Boot | 1.1.4-linino-gca1b422d-dirty (Sep 15 2014) |
| SoC | Atheros AR9330 rev 1, MIPS 24Kc |
| Python | 2.7.9 |
| avrdude | 6.3 |
| curl / wget | 7.40.0 / 1.17.1, with ca-certificates 20150426 and PolarSSL ustream |
| mtd | 21 (Chaos Calmer era) |
| dropbear | 2015.67, with RSA and DSS host keys only |

The 2015 CA bundle means HTTPS downloads on stock firmware will probably fail against current servers. The migration should expect the image to be uploaded from a PC.

### How stock sysupgrade works

From `/lib/upgrade/platform.sh` and `/lib/upgrade/common.sh` on the board:

- `platform_check_image` for board `yun` accepts an image only if its first 4 bytes are `68737173` (squashfs `hsqs`) or `19852003` (jffs2).
- `get_image` decompresses gzip images on the fly, so a `.gz` image is fine and the check runs on the decompressed data.
- `default_do_upgrade` pipes the image into `mtd [-j <config.tgz>] write - firmware`, which writes from 0x50000 onward.
- With `-j`, when `mtd` reaches the `0xdeadc0de` end-of-rootfs marker, it writes the saved config as jffs2 there, skips that many bytes of input, and then **keeps writing the rest of the image**. This was checked in the Chaos Calmer `mtd.c`. Anything after the rootfs, such as a loader at 0xea0000, still gets written.
- `sysupgrade -f <file.tgz>` restores a chosen config archive instead of the default one. This lets us carry over only the settings we want, and not old Linino config files that would break the new system.
- The image is read from `/tmp`, which is RAM (about 30 MB of tmpfs, 64 MB of RAM in total). No SD card is needed.

## Design: a loader in the old kernel slot

The key idea is to leave U-Boot untouched and put a tiny loader where U-Boot expects the kernel.

New layout (device `arduino_yun-2026`, compatible `arduino,yun-2026`):

| Offset | Size | Name | Contents |
| --- | --- | --- | --- |
| 0x000000 | 256k | u-boot | unchanged, never written |
| 0x040000 | 64k | u-boot-env | unchanged, never written |
| 0x050000 | 14656k | firmware | kernel uImage, then squashfs, then jffs2 rootfs_data |
| 0xea0000 | 1280k | loader | OpenWrt lzma-loader (~20 KB) as an lzma uImage |
| 0xfe0000 | 64k | nvram | unchanged |
| 0xff0000 | 64k | art | unchanged |

Boot sequence:

1. The stock U-Boot runs `bootm 0x9fea0000`, which starts the loader.
2. The loader scans flash from 0x50000 for a uImage with the magic `0x68737173`, decompresses that kernel and jumps to it.
3. The kernel takes its command line from the device tree (`CONFIG_MIPS_CMDLINE_FROM_DTB=y`), so the old Linino `bootargs` from U-Boot are ignored.
4. The `firmware` partition uses `openwrt,uimage` with `openwrt,ih-magic = <0x68737173>`, so the kernel finds the rootfs and rootfs_data automatically.

The kernel uImage is given the squashfs magic, so the image starts with `hsqs`. That's exactly what the stock updater checks for, so stock Linino accepts it as a normal update. OpenWrt's D-Link COVR devices use the same method (`Device/dlink_covr` in `generic.mk`).

The build produces two images:

- `linino-upgrade.bin`: kernel + rootfs, padded to 14656k, with the loader appended and the whole thing gzipped. You install it from stock Linino with the stock `sysupgrade`.
- `sysupgrade.bin`: kernel + rootfs with metadata. It's for later updates on the new firmware, and it only rewrites `firmware`, not the loader.

The changes to OpenWrt are in [`openwrt/patches/0001-ath79-add-arduino-yun-2026-layout.patch`](../openwrt/patches/0001-ath79-add-arduino-yun-2026-layout.patch):

- new `dts/ar9331_arduino_yun-2026.dts`, a copy of the upstream Yún device tree with the new partitions
- `Device/arduino_yun-2026` in `image/generic.mk`
- `arduino,yun-2026` added next to `arduino,yun` in `board.d/02_network` and `uboot-envtools`

### Safety

U-Boot is never written, so a failed update can always be recovered. The recovery is to use YunSerialTerminal over the Yún's USB port, stop in U-Boot and TFTP an image, as described on the [OpenWrt wiki page](https://openwrt.org/toh/arduino.cc/yun).

## Update flow (planned)

From stock Linino (a one-time migration):

1. A `yun-migrate` script runs on stock Linino. It gets the image into `/tmp`, checks its checksum, and builds a small config archive with only the settings to keep.
2. It runs `sysupgrade -f /tmp/yun-settings.tgz /tmp/linino-upgrade.bin`.
3. On first boot, a `uci-defaults` script in the new image turns the saved settings into 25.12 UCI config.

On the new firmware:

- A `yun-update` command downloads and checks the image, keeps the settings, and applies it at the next reboot.
- Arduino packages update separately with `apk`.

## Arduino software to port

The original sources are in [arduino/openwrt-packages-yun](https://github.com/arduino/openwrt-packages-yun) (`arduino/`) and [arduino/YunBridge](https://github.com/arduino/YunBridge).

| Original | Notes |
| --- | --- |
| `cpu-mcu-bridge` (YunBridge, ~2,400 lines of Python 2) | Port to Python 3. It lived in `/usr/lib/python2.7/bridge`. |
| `yun-scripts`: `run-bridge`, `kill-bridge` | `Bridge.begin()` on the 32U4 sends `run-bridge` to the login shell on `ttyATH0`. |
| `yun-scripts`: `reset-mcu` | Pulses GPIO 18. With kernel 6.12, sysfs GPIO numbers have a chip base offset, so use a named export or libgpiod. |
| `yun-scripts`: `run-avrdude`, `merge-sketch-with-bootloader.lua` | Used by the IDE's Wi-Fi upload. It uses avrdude `linuxgpio` and turns on `yun:oe:spi` (GPIO 21). OpenWrt packages has avrdude 7.3 built with libgpiod. |
| `yun-conf`: `/etc/avahi/services/arduino.service` | mDNS `_arduino._tcp`, `board=yun`, for IDE network discovery. Consider `umdns` instead of avahi to save flash. |
| `yun-scripts`: Wi-Fi reset button handling | 5 s press resets Wi-Fi, 30 s press does a factory reset. |
| `luci-app-arduino-webpanel` | The web panel for Wi-Fi setup. Rewrite for current LuCI or keep it separate. |
