Modern OpenWrt for the Arduino Yún, with updates and all the old Arduino stuff.

**Status:** working. It has been installed in place on an Arduino Yún Rev1 running the stock firmware (OpenWrtYun 1.6.2): the board came up on OpenWrt 25.12.5 with its Wi-Fi, hostname, time zone, root password and SSH keys carried over. Download it from [Releases](https://github.com/cowboydaniel/arduino-yun-2026/releases).

## What's in it

- OpenWrt 25.12 with a current kernel, installed **in place** from the stock Linino firmware with its own updater. No serial cable, U-Boot prompt or TFTP needed, and U-Boot is never touched. [How it works](docs/design.md).
- The Bridge library's Linux side, ported to Python 3: existing sketches run unchanged.
- Uploading sketches over Wi-Fi from the Arduino IDE, or from the web panel.
- An SD card is mounted at `/mnt/sd` like on stock, and if one is in within two minutes of boot, a 256 MB swap file on it gives the 64 MB board room to breathe. Run `yun-sdswap stop` before taking the card out; turn it off with `uci set arduino.@arduino[0].sd_swap=0`.
- A new web panel: status, Wi-Fi setup, drag and drop sketch upload, the live datastore, a terminal for running Linux commands from any browser (phones too), settings, and firmware updates you can follow as they download.
- The stock REST API (`/arduino`, `/data`, `/mailbox`), the WLAN RST button, the setup access point, and keeping your settings when moving from stock.

## Installing from the stock firmware

You need the Yún on your network running its stock firmware, and a PC that can reach it over SSH. Only the Yún Rev1 is supported.

1. **Back up first.** Follow steps 1 and 2 of [backup and recovery](docs/recovery.md). It takes a few minutes, and it means you can always go back.
2. **Download** `openwrt-ath79-generic-arduino_yun-2026-squashfs-linino-upgrade.bin`, `yun-migrate` and `MD5SUMS` from the [latest release](https://github.com/cowboydaniel/arduino-yun-2026/releases/latest).
3. **Copy them to the Yún:**

   ```sh
   scp openwrt-ath79-generic-arduino_yun-2026-squashfs-linino-upgrade.bin yun-migrate root@arduino.local:/tmp/
   ```

4. **Check and install.** Use the image's MD5 from `MD5SUMS`. The stock firmware can't check a SHA-256.

   ```sh
   ssh root@arduino.local
   sh /tmp/yun-migrate -t /tmp/openwrt-ath79-generic-arduino_yun-2026-squashfs-linino-upgrade.bin <md5>   # dry run: checks only
   sh /tmp/yun-migrate /tmp/openwrt-ath79-generic-arduino_yun-2026-squashfs-linino-upgrade.bin <md5>
   ```

   It asks before flashing. The Yún then writes the new firmware and restarts. Don't unplug it: after about three minutes it's back on its old address, with its settings. Log in with the same password or SSH key as before.

`yun-migrate` keeps your Wi-Fi network and password, country, hostname, time zone, root password, SSH host key and authorized keys, and Ethernet and Wi-Fi address settings. Nothing else is copied over. If you used an SD card as extra storage (extroot), its files stay on the card, and the new firmware uses the internal flash.

The stock firmware's SSH server is old. Recent OpenSSH clients need `-o KexAlgorithms=+diffie-hellman-group14-sha1 -o HostKeyAlgorithms=+ssh-rsa -o PubkeyAcceptedAlgorithms=+ssh-rsa` for these commands, or the same lines in `~/.ssh/config`.

## Updating

On Yún 2026, `yun-update check` looks for a newer release, and `yun-update apply` downloads, verifies and installs it, keeping your settings. The web panel's **Settings → Firmware** does the same, and shows the download's progress and when the Yún is back on the new version. Packages you added with `apk` aren't kept across an update.

## Layout

| Path | |
| --- | --- |
| `openwrt/` | The OpenWrt patch adding the `arduino_yun-2026` device, and the build config. |
| `feed/` | The Arduino packages: `yun-bridge`, `yun-base`, `yun-webpanel`. |
| `scripts/build.sh` | Builds the firmware. |
| `tools/` | `yun-migrate` (install from stock), `check-image.py`, and the U-Boot capture sketch. |
| `tests/` | Tests for everything that can run without a board. |
| `docs/` | Design notes, what we found on a stock Yún, and [backup and recovery](docs/recovery.md). |

## Building

On Linux with OpenWrt's [build prerequisites](https://openwrt.org/docs/guide-developer/toolchain/install-buildsystem) (about 15 GB of disk):

```sh
scripts/build.sh            # the full firmware
BARE=1 scripts/build.sh     # plain OpenWrt on the new layout, for a first boot test
```

The images end up in `../yun-build/openwrt/bin/targets/ath79/generic/`, and the build checks `linino-upgrade.bin` with `tools/check-image.py`. GitHub Actions can build them too (the Firmware workflow).

## Previewing the web panel

```sh
python3 feed/yun-webpanel/demo/serve.py     # http://localhost:8080, password "arduino"
```

## Tests

```sh
python3 -m unittest discover -s tests
```

The panel backend, REST API and first-boot tests also need host builds of ucode and uci; see `.github/workflows/tests.yml`, and set `UCODE_BUILD` and `UCI_BIN`. Without them those tests are skipped.
