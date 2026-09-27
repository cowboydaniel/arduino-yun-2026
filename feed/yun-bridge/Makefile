# SPDX-License-Identifier: GPL-2.0-or-later

include $(TOPDIR)/rules.mk

PKG_NAME:=yun-bridge
PKG_VERSION:=2026.1
PKG_RELEASE:=1
PKG_LICENSE:=GPL-2.0-or-later

include $(INCLUDE_DIR)/package.mk

define Package/yun-bridge
  SECTION:=arduino
  CATEGORY:=Arduino
  TITLE:=Arduino Yun Bridge (Linux side, Python 3)
  # curl: the Bridge library's HttpClient runs it on the Linux side.
  # python3-openssl (1.8 MB) is left out to save flash; install it with apk
  # for BridgeSSLClient.
  DEPENDS:=+python3-light +python3-logging +curl
  PKGARCH:=all
endef

define Package/yun-bridge/description
 The Linux side of the Arduino Bridge library: runs commands, files,
 sockets, the console, mailbox and datastore for the sketch on the
 ATmega32U4, over the serial port.
endef

define Build/Compile
endef

define Package/yun-bridge/install
	$(INSTALL_DIR) $(1)/usr/lib/yun-bridge $(1)/usr/bin $(1)/etc/sysctl.d
	$(INSTALL_DATA) ./files/usr/lib/yun-bridge/*.py $(1)/usr/lib/yun-bridge/
	$(INSTALL_BIN) ./files/usr/bin/run-bridge ./files/usr/bin/kill-bridge $(1)/usr/bin/
	$(INSTALL_DATA) ./files/etc/sysctl.d/90-yun-console.conf $(1)/etc/sysctl.d/
	# Scripts written for the stock firmware add this to sys.path to import
	# bridgeclient.
	$(INSTALL_DIR) $(1)/usr/lib/python2.7
	$(LN) ../yun-bridge $(1)/usr/lib/python2.7/bridge
endef

$(eval $(call BuildPackage,yun-bridge))
