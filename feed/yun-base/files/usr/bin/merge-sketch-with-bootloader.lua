#!/bin/sh
# Append the Yun's Caterina bootloader to a sketch in Intel HEX format, so
# that flashing it over ISP (run-avrdude) keeps the bootloader.
#
# The Arduino IDE runs this by name before a network upload, so it keeps its
# old name, but it's a shell script now: current OpenWrt has no Lua by default.
#
# Usage: merge-sketch-with-bootloader.lua <sketch.hex>

BOOTLOADER=${BOOTLOADER:-/etc/arduino/Caterina-Yun.hex}

if [ $# -ne 1 ]; then
	echo "Missing sketch file name"
	exit 1
fi
SKETCH=$1

if [ ! -r "$SKETCH" ]; then
	echo "Unable to open file $SKETCH for reading"
	exit 1
fi

TMP="$SKETCH.merge.$$"
awk -v boot="$BOOTLOADER" '
	function clean(s) { gsub(/[\r \t]/, "", s); return s }
	BEGIN {
		n = 0
		while ((getline line < boot) > 0) {
			line = clean(line)
			if (line != "") bl[++n] = line
		}
		if (n == 0) { print "Unable to read " boot > "/dev/stderr"; failed = 1; exit 1 }
	}
	{
		line = clean($0)
		if (line == "") next
		# Already merged: leave the file as it is.
		if (line == bl[1]) merged = 1
		# Drop end-of-file records; the bootloader brings its own.
		if (!merged && substr(line, 8, 2) == "01") next
		out[++m] = line
	}
	END {
		if (failed) exit 1
		for (i = 1; i <= m; i++) print out[i]
		if (!merged) for (i = 1; i <= n; i++) print bl[i]
	}
' "$SKETCH" > "$TMP" || { rm -f "$TMP"; exit 1; }

mv "$TMP" "$SKETCH"
