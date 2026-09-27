#!/bin/sh
# Build the Arduino Yun 2026 firmware.
#
# Clones OpenWrt at the pinned release into $BUILD_DIR (default ../yun-build),
# applies the patches in openwrt/patches, and builds the arduino_yun-2026 image.
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

# Start from a clean tree so the patches always apply.
git checkout -q -- .
git clean -qfd target package
for p in "$REPO_DIR"/openwrt/patches/*.patch; do
	git apply "$p"
done

./scripts/feeds update -a
./scripts/feeds install -a

cp "$REPO_DIR/openwrt/config.seed" .config
make defconfig

make -j"$(nproc)" download
make -j"$(nproc)" "$@"

echo
echo "Images:"
ls -l bin/targets/ath79/generic/*arduino_yun-2026*
