#!/bin/sh
# Build the Arduino Yun 2026 firmware.
#
# Clones OpenWrt at the pinned release into $BUILD_DIR (default ../yun-build),
# applies the patches in openwrt/patches, adds feed/ as the "arduino" package
# feed, and builds the arduino_yun-2026 image.
#
# BARE=1 builds plain OpenWrt on the new flash layout, without the Arduino
# packages: that's the image for a first boot test on hardware.
#
# The full build needs about 15 GB of disk space.

set -e

OPENWRT_VERSION=v25.12.5
REPO_DIR=$(cd "$(dirname "$0")/.." && pwd)
BUILD_DIR=${BUILD_DIR:-$REPO_DIR/../yun-build}
SRC=$BUILD_DIR/openwrt

mkdir -p "$BUILD_DIR"

if [ ! -d "$SRC" ]; then
	git clone --branch "$OPENWRT_VERSION" --depth 1 \
		https://github.com/openwrt/openwrt.git "$SRC"
fi

cd "$SRC"

# Apply the patches to a clean tree, but only when they aren't applied
# already: resetting and re-applying them touches the patched files, and
# make then rebuilds everything that depends on them.
applied=1
for p in "$REPO_DIR"/openwrt/patches/*.patch; do
	git apply --reverse --check "$p" 2>/dev/null || applied=0
done
if [ $applied = 0 ]; then
	git reset -q
	git checkout -q -- .
	git clean -qfd target package
	for p in "$REPO_DIR"/openwrt/patches/*.patch; do
		git apply "$p"
	done
fi

cp feeds.conf.default feeds.conf
echo "src-link arduino $REPO_DIR/feed" >> feeds.conf
./scripts/feeds update -a
./scripts/feeds install -a

cp "$REPO_DIR/openwrt/config.seed" .config
[ "${BARE:-0}" = 1 ] || cat "$REPO_DIR/openwrt/config-yun.seed" >> .config
make defconfig

make -j"$(nproc)" download
make -j"$(nproc)" "$@"

echo
echo "Images:"
ls -l bin/targets/ath79/generic/*arduino_yun-2026*

echo
python3 "$REPO_DIR/tools/check-image.py" bin/targets/ath79/generic/*arduino_yun-2026*linino-upgrade.bin
