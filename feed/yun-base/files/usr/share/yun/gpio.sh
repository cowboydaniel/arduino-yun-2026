# Helpers for the Yun's GPIOs, sourced by the yun-base scripts.
#
# The kernel exports the level shifters between the AR9331 and the 32U4 by
# name (see the device tree): yun:oe:spi, yun:oe:hs and yun:oe:uart. Other
# lines are driven through the GPIO chip, whose sysfs numbers no longer start
# at 0 on current kernels.

GPIO_MCU_RESET=18
GPIO_SYSFS=${GPIO_SYSFS:-/sys/class/gpio}

# yun_oe_spi 1|0: connect or disconnect the 32U4's SPI/ISP pins.
yun_oe_spi() {
	local f=$GPIO_SYSFS/yun:oe:spi/value
	if [ ! -w "$f" ]; then
		echo "$f not found: is this an Arduino Yun?" >&2
		return 1
	fi
	echo "$1" > "$f"
}

# The sysfs number of line <n> of the AR9331 GPIO controller.
yun_gpio_sysfs() {
	local chip base
	for chip in $GPIO_SYSFS/gpiochip*; do
		[ "$(cat "$chip/ngpio" 2>/dev/null)" = 30 ] || continue
		base=$(cat "$chip/base")
		echo $((base + $1))
		return 0
	done
	return 1
}

# yun_gpio_pulse <line>: drive a line high, then low, then release it.
yun_gpio_pulse() {
	local n
	n=$(yun_gpio_sysfs "$1") || return 1
	[ -d $GPIO_SYSFS/gpio$n ] || echo $n > $GPIO_SYSFS/export || return 1
	echo high > $GPIO_SYSFS/gpio$n/direction
	echo 0 > $GPIO_SYSFS/gpio$n/value
	echo $n > $GPIO_SYSFS/unexport
}
