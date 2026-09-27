Modern OpenWrt for the Arduino Yún, with updates and all the old Arduino stuff.

**Status:** a first image builds and passes every offline check, and the software is tested off the board, but nothing has run on a Yún yet. There's nothing to download. See [TODO.md](TODO.md).

## What's in it

- OpenWrt 25.12 with a current kernel, installed **in place** from the stock Linino firmware with its own updater. No serial cable, U-Boot prompt or TFTP needed, and U-Boot is never touched. [How it works](docs/design.md).
- The Bridge library's Linux side, ported to Python 3: existing sketches run unchanged.
- Uploading sketches over Wi-Fi from the Arduino IDE, or from the web panel.
- A new web panel: status, Wi-Fi setup, drag and drop sketch upload, the live datastore, settings and firmware updates.
- The stock REST API (`/arduino`, `/data`, `/mailbox`), the WLAN RST button, the setup access point, and keeping your settings when moving from stock.

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
