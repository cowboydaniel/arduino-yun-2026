# Sourced by /sbin/sysupgrade (and by its stage 2, where this does nothing).
#
# A Yun has about 55 MB of RAM and typically 15 MB available; the image
# (about 12 MB) is in /tmp, which is RAM too. Stage 2 stops everything, but
# only after stage 1 has checked the image and packed the settings, and
# sysupgrade stalled for lack of memory with services already dying. So for
# a real upgrade (not -T, -b, -r, -l or --help), stop what an update
# doesn't need before any of that. Every update goes through here: the web
# panel, yun-update, and sysupgrade typed at a console.
#
# If sysupgrade gives up before handing over to stage 2, start them again.

yun_free_ram_services="uhttpd rpcd umdns cron odhcpd"
# Only the tests (tests/test_yun_base.py) change these.
YUN_INITD=${YUN_INITD:-/etc/init.d}
YUN_PROC=${YUN_PROC:-/proc}

yun_free_ram() {
	local s
	YUN_STOPPED=
	for s in $yun_free_ram_services; do
		[ -x $YUN_INITD/$s ] || continue
		$YUN_INITD/$s stop >/dev/null 2>&1
		YUN_STOPPED="$YUN_STOPPED $s"
	done
	# The bridge's Python needs about 8 MB; stage 2 would kill it anyway.
	pkill -f /usr/lib/yun-bridge/bridge.py 2>/dev/null
	# DNS is only needed if sysupgrade still has to download the image.
	case "$IMAGE" in
	http://*|https://*) ;;
	*)
		if [ -x $YUN_INITD/dnsmasq ]; then
			$YUN_INITD/dnsmasq stop >/dev/null 2>&1
			YUN_STOPPED="$YUN_STOPPED dnsmasq"
		fi
		;;
	esac
	sync
	echo 3 > $YUN_PROC/sys/vm/drop_caches 2>/dev/null
	v "Stopped services to free memory: $(awk '/^MemAvailable:/ { print $2 }' $YUN_PROC/meminfo) KB available"
	trap yun_free_ram_undo EXIT
}

yun_free_ram_undo() {
	local s
	# Handed over to stage 2: procd has become upgraded, leave it alone.
	sleep 2
	case "$(readlink $YUN_PROC/1/exe 2>/dev/null)" in
	*upgraded*) return ;;
	esac
	for s in $YUN_STOPPED; do
		$YUN_INITD/$s start >/dev/null 2>&1
	done
}

case "${0##*/}" in
sysupgrade)
	[ "${TEST:-0}" = 0 ] && [ "${HELP:-0}" = 0 ] && [ "${CONF_BACKUP_LIST:-0}" = 0 ] &&
		[ -z "$CONF_BACKUP$CONF_RESTORE" ] && [ -n "$IMAGE" ] &&
		yun_free_ram
	;;
esac
