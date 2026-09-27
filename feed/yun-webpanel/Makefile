# SPDX-License-Identifier: GPL-2.0-or-later

include $(TOPDIR)/rules.mk

PKG_NAME:=yun-webpanel
PKG_VERSION:=2026.1
PKG_RELEASE:=1
PKG_LICENSE:=GPL-2.0-or-later

include $(INCLUDE_DIR)/package.mk

define Package/yun-webpanel
  SECTION:=arduino
  CATEGORY:=Arduino
  TITLE:=Yun Panel and the Yun REST API
  DEPENDS:=+yun-base +uhttpd +uhttpd-mod-ubus +uhttpd-mod-ucode +rpcd +rpcd-mod-ucode \
	+cgi-io +ucode-mod-socket +ucode-mod-uci +ucode-mod-ubus +ucode-mod-fs
  PKGARCH:=all
endef

# The iwinfo command it uses comes with wifi-scripts (the ucode version).

define Package/yun-webpanel/description
 The board's web page (status, Wi-Fi setup, sketch upload, datastore and
 settings) and the /arduino, /data and /mailbox REST calls that sketches
 using BridgeServer and the datastore answer.
endef

define Build/Compile
endef

define Package/yun-webpanel/install
	$(INSTALL_DIR) $(1)/www/yun $(1)/usr/share/rpcd/ucode $(1)/usr/share/rpcd/acl.d \
		$(1)/usr/share/ucode/yun $(1)/usr/share/yun $(1)/etc/uci-defaults
	$(INSTALL_DATA) ./files/www/index.html $(1)/www/
	$(INSTALL_DATA) ./files/www/yun/* $(1)/www/yun/
	$(INSTALL_DATA) ./files/usr/share/rpcd/ucode/yun.uc $(1)/usr/share/rpcd/ucode/
	$(INSTALL_DATA) ./files/usr/share/rpcd/acl.d/yun.json $(1)/usr/share/rpcd/acl.d/
	$(INSTALL_DATA) ./files/usr/share/ucode/yun/bridge.uc $(1)/usr/share/ucode/yun/
	$(INSTALL_DATA) ./files/usr/share/yun/rest.uc $(1)/usr/share/yun/
	$(INSTALL_BIN) ./files/etc/uci-defaults/* $(1)/etc/uci-defaults/
endef

$(eval $(call BuildPackage,yun-webpanel))
