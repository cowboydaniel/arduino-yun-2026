# To do

See [docs/design.md](docs/design.md) for the reasons behind each item.

## Build

- [x] Install GNU awk and rsync on the build machine. Without them the build stops at OpenWrt's prerequisite check.
- [x] Build a first (bare) `arduino_yun-2026` image: kernel 6.12.94, 2.7 MB kernel and 3.8 MB rootfs, 8 MB left for settings.
- [x] `tools/check-image.py` passes on it, and the loader is built to look for `hsqs` at flash 0x50000 only.
- [x] Build the full image with the Arduino packages: 9 MB rootfs, 3 MB left for settings and packages (python3-openssl left out, curl in).

## First hardware test (needs a serial console for recovery)

Don't flash the test Yún until its owner confirms. Follow [docs/recovery.md](docs/recovery.md).

- [x] Back up every flash partition to the PC (2026-09-27): each copy matched the board's MD5, plus a whole-chip image for an SPI programmer.
- [x] Rehearse recovery (2026-09-27), over Wi-Fi with `tools/uboot-rehearsal`: U-Boot took `lin` and commands from the 32U4, and TFTP'd the stock kernel (1310720 bytes) and rootfs (15007744 bytes, CRC32 `0910774e` as expected) into RAM, then booted normally. Not yet run for real: the `erase`, `cp.b` and `cmp.b` of a restore.
- [ ] ~~Flash a `BARE=1` image first.~~ Skipped: the first flash was the full image (below). Still to check over serial if it doesn't boot: the loader finding the kernel at 0x9f050000, the kernel finding the rootfs, rootfs_data being created.
- [ ] Check Ethernet, Wi-Fi, the USB host and SD card, the LEDs and the WLAN RST button.
- [ ] Check that `sysupgrade.bin` from the new firmware writes only `firmware` and the board still boots.

## Second hardware test: the full image

- [x] `yun-migrate -t` on stock Linino (2026-09-27). It found three bugs, now fixed: stock busybox has no `sha256sum` (use the MD5 there), the RAM check read `MemFree` not `MemAvailable`, and the settings archive was world-readable.
- [ ] `yun-migrate` for real: flashed on 2026-09-27 at 20:45 with the cloud-built image (MD5 `c57f88ebc290cd894107a36d954ca4b9`). The write finished and the board rebooted at 20:46; waiting for its first boot. Then check the imported settings in `/etc/yun-migrate.log`.
- [ ] `Bridge.begin()` starts the bridge from the serial console; the Bridge examples (Process, FileIO, HttpClient, Console, Bridge/datastore, Mailbox) work.
- [ ] Upload a sketch over Wi-Fi from the Arduino IDE (discovery, SSH, `run-avrdude` over libgpiod).
- [ ] Upload a `.hex` from the Yún Panel, and the REST API (`/arduino/...`, `/data/...`, `/mailbox/...`) with and without the password.
- [ ] Wi-Fi: joining from the panel, falling back to setup mode when the network is gone, WLAN RST for 5 s and 30 s.
- [ ] `yun-update` from one release to the next.

## Done without hardware

- [x] OpenWrt device with the loader in the old kernel slot, stock network roles, 0xff padding, read-only U-Boot partitions.
- [x] YunBridge ported to Python 3, with a test that plays the 32U4 (`tests/test_bridge.py`).
- [x] `run-bridge`, `kill-bridge`, `reset-mcu`, `run-avrdude` (libgpiod), `merge-sketch-with-bootloader.lua`.
- [x] mDNS `_arduino._tcp` announcement for the IDE (umdns).
- [x] WLAN RST button (5 s Wi-Fi setup mode, 30 s factory reset) and the Wi-Fi fallback at boot.
- [x] The Yún Panel (front end, rpcd backend) and the stock REST API.
- [x] `yun-migrate` and the first-boot settings import.
- [x] `yun-update`.
- [x] `tools/check-image.py` and the backup and recovery guide.
- [x] First boot keeps Ethernet reachable (firewall, SSH) even if the Wi-Fi radio isn't ready, and the settings import waits for the Wi-Fi sections instead of losing them.
- [x] GitHub Actions: tests on every push, firmware builds on demand and releases for tags.

## Release

- [ ] First tagged release with `linino-upgrade.bin`, `sysupgrade.bin`, `SHA256SUMS` and `MD5SUMS`.
- [ ] User docs: installing from stock, the panel, recovering.
- [x] License: GPL-2.0-or-later, except the OpenWrt patches, which are GPL-2.0-only like OpenWrt (see `LICENSE`).
