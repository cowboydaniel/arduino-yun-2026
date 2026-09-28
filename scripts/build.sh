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

# Only the packages feed is needed (Python 3, avrdude, libgpiod, curl), at
# the commit this OpenWrt release pins, from GitHub's mirror: the other
# feeds and git.openwrt.org have failed to download on GitHub's runners and
# stopped the whole build.
sed -n 's,^src-git packages https://git.openwrt.org/feed/packages.git,src-git packages https://github.com/openwrt/packages.git,p' \
	feeds.conf.default > feeds.conf
grep -q '^src-git packages ' feeds.conf || { echo "no packages feed in feeds.conf.default" >&2; exit 1; }
echo "src-link arduino $REPO_DIR/feed" >> feeds.conf
for try in 1 2 3; do
	./scripts/feeds update -a && break
	[ $try = 3 ] && exit 1
	echo "feeds update failed, trying again in 30 s" >&2
	sleep 30
done
# Drop packages left over from feeds this script no longer uses.
./scripts/feeds uninstall -a >/dev/null

# Patches for packages from OpenWrt's feeds, laid out like feeds/:
# openwrt/feed-patches/<feed>/<path to package>/*.patch
for p in "$REPO_DIR"/openwrt/feed-patches/*/*/*/*.patch; do
	[ -e "$p" ] || continue
	rel=${p#"$REPO_DIR"/openwrt/feed-patches/}
	mkdir -p "feeds/${rel%/*}/patches"
	cp "$p" "feeds/${rel%/*}/patches/"
done

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

# The kernel must match OpenWrt's official build (see openwrt/config.seed),
# or no kmod-* from downloads.openwrt.org installs on the Yun.
echo
ours=$(cat build_dir/target-*/linux-ath79_generic/linux-*/.vermagic)
official=$(curl -fsS -m 60 "https://downloads.openwrt.org/releases/${OPENWRT_VERSION#v}/targets/ath79/generic/packages/index.json" |
	python3 -c 'import json, sys; print(json.load(sys.stdin)["packages"]["kernel"].split("~")[1].split("-")[0])' 2>/dev/null)
if [ -z "$official" ]; then
	echo "Couldn't get the official kernel's version from downloads.openwrt.org; not checked."
elif [ "$ours" = "$official" ]; then
	echo "Kernel matches the official build ($ours): official kmods install."
else
	echo "The kernel ($ours) doesn't match the official build ($official):" >&2
	echo "kmods from downloads.openwrt.org won't install. See openwrt/config.seed." >&2
	[ "${YUN_ALLOW_KERNEL_MISMATCH:-0}" = 1 ] || exit 1
fi
