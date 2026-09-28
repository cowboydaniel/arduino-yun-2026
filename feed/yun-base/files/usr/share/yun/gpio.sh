# Helpers for the Yun's GPIOs, sourced by the yun-base scripts.
#
# The kernel exports the level shifters between the AR9331 and the 32U4 by
# name (see the device tree): yun:oe:spi, yun:oe:hs and yun:oe:uart. Other
# lines are driven through the GPIO chip, whose sysfs numbers no longer start
# at 0 on current kernels.

GPIO_MCU_RESET=18
GPIO_SYSFS=${GPIO_SYSFS:-/sys/class/gpio}
SPI_SYSFS=${SPI_SYSFS:-/sys/bus/spi}
PLATFORM_SYSFS=${PLATFORM_SYSFS:-/sys/bus/platform}
DEV_DIR=${DEV_DIR:-/dev}

# The 32U4's ISP pins as SPI bus 1 (spi-isp in the device tree), as the stock
# firmware set them up with spi-gpio-custom.
ISP_SPI=spi1.0
ISP_SPIDEV=$DEV_DIR/spidev1.0
ISP_BUS=spi-isp

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

# yun_isp_spidev: make sure spidev drives the ISP bus, and print its device
# node. The device tree can't name spidev directly, so bind it by hand.
yun_isp_spidev() {
	local dev=$SPI_SYSFS/devices/$ISP_SPI i=0
	if [ ! -e "$dev" ]; then
		echo "the 32U4's ISP bus ($ISP_SPI) is missing" >&2
		return 1
	fi
	if [ ! -e "$ISP_SPIDEV" ]; then
		[ -d "$SPI_SYSFS/drivers/spidev" ] || insmod spidev 2>/dev/null
		echo spidev > "$dev/driver_override"
		[ -e "$dev/driver" ] || echo "$ISP_SPI" > "$SPI_SYSFS/drivers/spidev/bind"
		while [ ! -e "$ISP_SPIDEV" ] && [ $i -lt 5 ]; do
			sleep 1
			i=$((i + 1))
		done
	fi
	if [ ! -e "$ISP_SPIDEV" ]; then
		echo "$ISP_SPIDEV didn't appear" >&2
		return 1
	fi
	echo "$ISP_SPIDEV"
}

# yun_isp_release / yun_isp_take_back: let go of the ISP pins for avrdude's
# linuxgpio programmer, and give them back to the SPI bus afterwards.
yun_isp_release() {
	[ -e "$PLATFORM_SYSFS/devices/$ISP_BUS/driver" ] || return 0
	echo "$ISP_BUS" > "$PLATFORM_SYSFS/drivers/spi_gpio/unbind"
}

yun_isp_take_back() {
	[ -e "$PLATFORM_SYSFS/devices/$ISP_BUS/driver" ] && return 0
	echo "$ISP_BUS" > "$PLATFORM_SYSFS/drivers/spi_gpio/bind"
}
