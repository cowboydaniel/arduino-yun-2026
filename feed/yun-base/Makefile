# SPDX-License-Identifier: GPL-2.0-or-later

include $(TOPDIR)/rules.mk

PKG_NAME:=yun-base
PKG_VERSION:=2026.4
PKG_RELEASE:=1
PKG_LICENSE:=GPL-2.0-or-later

include $(INCLUDE_DIR)/package.mk

define Package/yun-base
  SECTION:=arduino
  CATEGORY:=Arduino
  TITLE:=Arduino Yun board support
  DEPENDS:=+avrdude +umdns +kmod-spi-dev
  PKGARCH:=all
endef

define Package/yun-base/description
 Everything that makes the board a Yun: flashing the ATmega32U4 over
 SPI (run-avrdude, used by the Arduino IDE's network upload), the WLAN RST
 button, the Wi-Fi setup access point and client roles, the mDNS
 announcement the IDE uses to find the board, and swap on the SD card.
endef

define Package/yun-base/conffiles
/etc/config/arduino
endef

define Build/Compile
endef

define Package/yun-base/install
	$(INSTALL_DIR) $(1)/usr/bin $(1)/usr/share/yun $(1)/etc/arduino $(1)/etc/config \
		$(1)/etc/init.d $(1)/etc/rc.button $(1)/etc/uci-defaults $(1)/etc/umdns
	$(INSTALL_BIN) ./files/usr/bin/* $(1)/usr/bin/
	$(INSTALL_DATA) ./files/usr/share/yun/gpio.sh $(1)/usr/share/yun/
	$(INSTALL_DATA) ./files/etc/arduino/Caterina-Yun.hex $(1)/etc/arduino/
	$(INSTALL_DATA) ./files/etc/avrdude-yun.conf $(1)/etc/
	$(INSTALL_CONF) ./files/etc/config/arduino $(1)/etc/config/
	$(INSTALL_BIN) ./files/etc/init.d/yun-wifi ./files/etc/init.d/yun-sdswap $(1)/etc/init.d/
	$(INSTALL_BIN) ./files/etc/rc.button/BTN_0 $(1)/etc/rc.button/
	$(INSTALL_BIN) ./files/etc/uci-defaults/* $(1)/etc/uci-defaults/
	$(INSTALL_DATA) ./files/etc/umdns/arduino.json $(1)/etc/umdns/
	$(INSTALL_DIR) $(1)/etc/hotplug.d/ieee80211 $(1)/lib/upgrade
	$(INSTALL_BIN) ./files/etc/hotplug.d/ieee80211/20-yun-wifi $(1)/etc/hotplug.d/ieee80211/
	$(INSTALL_DIR) $(1)/etc/hotplug.d/block
	$(INSTALL_BIN) ./files/etc/hotplug.d/block/20-yun-sd $(1)/etc/hotplug.d/block/
	$(INSTALL_DATA) ./files/lib/upgrade/yun-free-ram.sh $(1)/lib/upgrade/
	# Where the stock firmware put the SD card for sketches and web pages.
	# /mnt/sd is pointed at the card's real mount point when it appears
	# (hotplug.d/block/20-yun-sd); /mnt/sda1 is the usual one.
	$(INSTALL_DIR) $(1)/mnt $(1)/www
	$(LN) /mnt/sda1 $(1)/mnt/sd
	$(LN) /mnt/sd/arduino/www $(1)/www/sd
	echo "VERSION='$(PKG_VERSION)'" > $(1)/etc/yun_release
endef

$(eval $(call BuildPackage,yun-base))
