# To do

See [docs/design.md](docs/design.md) for the reasons behind each item.

## Build

- [x] Install GNU awk on the build machine (`sudo apt install gawk`). Without it the build stops at OpenWrt's prerequisite check.
- [ ] Run `scripts/build.sh` and get a first `arduino_yun-2026` image.
- [ ] Check the build output: the loader uImage at offset 0xe50000 in the decompressed `linino-upgrade.bin`, and the kernel header magic `hsqs` at offset 0.
- [ ] Confirm that the stock U-Boot 1.1.4 boots the lzma-loader uImage. The loader's load address and `LZMA_TEXT_START` come from `Device/loader-okli-uimage`.

## First hardware test (needs a serial console for recovery)

Don't flash the test Yún until its owner confirms.


- [ ] Test the recovery path before flashing: YunSerialTerminal, stop in U-Boot, then TFTP.
- [ ] From stock Linino, run `sysupgrade -n /tmp/linino-upgrade.bin` (no settings yet) and watch the boot over serial.
- [ ] Check that the kernel finds the rootfs and that rootfs_data is created, and check Ethernet, Wi-Fi, the USB host and the WLAN RST button.
- [ ] Check that `sysupgrade.bin` from the new firmware writes only `firmware` and the board still boots.

## Update tooling

- [ ] `yun-migrate` for stock Linino: fetch or accept the image, check the checksum, build a settings tgz, then run `sysupgrade -f`.
  - Stock HTTPS may fail because of 2016-era CA certificates and TLS. Plan for uploading the image from a PC (`scp` or the web panel) as a fallback.
- [ ] First-boot `uci-defaults` script that turns the Linino settings (Wi-Fi, hostname, root password, dropbear keys, time zone, REST password) into 25.12 UCI.
- [ ] `yun-update` for the new firmware: download, verify, and apply at the next reboot, keeping settings.
- [ ] Update `GOALS.md` and the docs once the flow is final.

## Arduino packages (an OpenWrt feed in this repo)

- [ ] Port YunBridge to Python 3, and check that it fits in flash with `python3-light`.
- [ ] `run-bridge`, `kill-bridge` and `reset-mcu`, using named GPIOs or libgpiod instead of fixed sysfs numbers.
- [ ] `run-avrdude` and `merge-sketch-with-bootloader.lua`, with avrdude `linuxgpio` over libgpiod.
- [ ] mDNS `_arduino._tcp` advertisement for IDE discovery.
- [ ] WLAN RST button behaviour (5 s Wi-Fi reset, 30 s factory reset).
- [ ] Test the Bridge examples (Process, FileIO, HttpClient, Console) and a Wi-Fi sketch upload from the Arduino IDE.
- [ ] Web panel for Wi-Fi setup.

## Release

- [ ] Build images in GitHub Actions and publish `linino-upgrade.bin`, `sysupgrade.bin` and checksums to GitHub Releases.
- [ ] Write user docs: how to update from stock, and how to recover.
- [ ] Choose a license for the repo.
