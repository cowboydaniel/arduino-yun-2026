# Design and findings

This page records what we know about the Yún, and how the in-place update is meant to work. The facts about the stock board come from probes of a real Yún (see the files linked below); the new firmware itself hasn't run on hardware yet.

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

This was read from a stock Yún running OpenWrtYun ChaosCalmer 1.6.2 (`built=Fri May 13 09:22:20 UTC 2016`, kernel 3.18.23):

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

- The stock U-Boot (1.1.4) boots with `bootm 0x9fea0000`, so it always starts the uImage at 0xea0000. The board does have a saved environment in `u-boot-env` (its CRC matches, see [`uboot-env.txt`](uboot-env.txt)); `fw_printenv` just isn't set up for it. The saved `bootcmd` is the same `bootm 0x9fea0000`, the console runs at 250000 baud, and autoboot is stopped by typing `lin` ([`uboot-boot-capture.txt`](uboot-boot-capture.txt)).
- U-Boot verifies the uImage CRC, then unpacks an lzma kernel to 0x80060000 and jumps there. U-Boot itself lives at the top of RAM (0x83fc8000, boot params from 0x83f77fb0), so the loader's relocation to 0x81800000 doesn't collide with it.
- The saved `bootargs` are ignored by the new kernel, which takes its command line from the device tree.
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

From the second probe ([`yun-probe2.txt`](yun-probe2.txt)):

- The flash is a Winbond W25Q128 (16 MB). The stock kernel uImage is lzma, loaded and started at 0x80060000.
- The ISP lines to the 32U4 are GPIO 18 (reset, inverted), 11 (SCK), 27 (MOSI) and 8 (MISO); GPIO 21 enables the SPI level shifter. Stock drives them through a `spi-gpio` device and avrdude's `linuxspi`; the new firmware uses avrdude's `linuxgpio` with libgpiod on the same pins, which needs no kernel changes.
- The UART level shifter enable (GPIO 23) is driven low, as the upstream device tree does.
- The console is `::askconsole:/bin/ash --login` with no password, which is how `Bridge.begin()` gets a shell to type `run-bridge` into. The kernel console log level is 7.
- The stock network roles are `lan` = Wi-Fi (client with DHCP, or the setup access point) and `wan` = the Ethernet jack (DHCP). Stock Linux calls the jack `eth1`; the new kernel calls it `eth0`.
- The probed board keeps its root filesystem on an SD card (extroot) with a swap file there, and has about 12 MB of RAM free.

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
- `Device/arduino_yun-2026` in `image/generic.mk`, with a `pad-to-ff` step so the gap between the rootfs and the loader is erased flash (0xff) rather than zeros
- `arduino,yun-2026` in `board.d/02_network` (the Ethernet jack is a DHCP client, `wan`, with the stock MAC; not upstream's 192.168.1.1 LAN with a DHCP server) and in `uboot-envtools`
- `u-boot-env` is read-only as well as `u-boot`

[`tools/check-image.py`](../tools/check-image.py) checks a built `linino-upgrade.bin` against all of the above before it goes near a board.

### Safety

U-Boot is never written, so a failed update can always be recovered: stop in U-Boot over YunSerialTerminal and write back a backup over TFTP. [`recovery.md`](recovery.md) has the exact steps, and a rehearsal that changes nothing.

## Update flow

From stock Linino, once ([`tools/yun-migrate`](../tools/yun-migrate)):

1. Copy `yun-migrate` and `linino-upgrade.bin` to `/tmp` on the Yún (scp, from a PC).
2. `sh /tmp/yun-migrate /tmp/linino-upgrade.bin <sha256>` checks the board, its flash layout and the image, saves the settings worth keeping into a small archive, and runs `sysupgrade -f <archive> <image>`. `-t` does everything except flash.
3. On first boot, preinit unpacks the archive and `95-yun-migrate` turns it into the new configuration: hostname, time zone, Wi-Fi network and country, static addresses, the root password hash, the RSA host key and authorized keys, and the REST API setting.

On the new firmware, `yun-update` (also behind the panel's firmware card) downloads the latest release's `sysupgrade.bin` from GitHub, checks it against the release's `SHA256SUMS` and installs it, keeping settings.

## Arduino software

The packages are in [`feed/`](../feed), built into the image by `scripts/build.sh`:

| Package | What it is |
| --- | --- |
| `yun-bridge` | YunBridge ported to Python 3 (`python3-light`). Same serial protocol and ports, so sketches work unchanged; also fixes several crashes and hangs in the original. Brings `curl`, which the Bridge library's `HttpClient` runs. `python3-openssl` is left out to save 1.8 MB of flash, so `BridgeSSLClient` needs `apk add python3-openssl`. |
| `yun-base` | `run-avrdude`, `merge-sketch-with-bootloader.lua` (now a shell script), `reset-mcu`, the WLAN RST button (5 s: setup mode, 30 s: factory reset), `yun-wifi` (setup access point, Wi-Fi client, and falling back to the access point when the network is gone at boot), `_arduino._tcp` over umdns for the IDE, `yun-update` and the first-boot scripts. |
| `yun-webpanel` | The Yún Panel at `/` (an rpcd ucode plugin behind a static page), and the stock REST API (`/arduino`, `/data`, `/mailbox`) as a uhttpd ucode handler. |

The network setup: `wan` is the Ethernet jack (DHCP), `lan` is the setup access point (192.168.240.1, open, "Arduino Yun-<MAC>"), and `wwan` is the Wi-Fi client. The firewall accepts connections from `wan` and `wwan`, like the stock firmware, because the Yún is a device on someone's network rather than a router.

## Original sources

The original sources are in [arduino/openwrt-packages-yun](https://github.com/arduino/openwrt-packages-yun) (`arduino/`), [arduino/YunBridge](https://github.com/arduino/YunBridge) and [arduino/YunWebUI](https://github.com/arduino/YunWebUI).
