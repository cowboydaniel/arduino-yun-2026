#!/usr/bin/env ucode
// SPDX-License-Identifier: GPL-2.0-or-later
//
// The "yun" ubus object behind the Yun Panel (/www/yun/app.js).
//
// Wi-Fi details come from the iwinfo command line tool's JSON output (the
// ucode one in wifi-scripts) rather than the iwinfo ubus object, because
// that object lives in rpcd itself and calling it from here would block on
// ourselves.

'use strict';

import { readfile, writefile, popen, stat, glob, unlink, access, open, mkdir, lsdir } from 'fs';
import { cursor } from 'uci';
import * as ubus from 'ubus';
import * as bridge from 'yun.bridge';

// POSIX TZ strings for the time zones the panel offers.
const ZONES = {
	'UTC': 'UTC0',
	'Africa/Johannesburg': 'SAST-2',
	'America/Chicago': 'CST6CDT,M3.2.0,M11.1.0',
	'America/Denver': 'MST7MDT,M3.2.0,M11.1.0',
	'America/Los_Angeles': 'PST8PDT,M3.2.0,M11.1.0',
	'America/New_York': 'EST5EDT,M3.2.0,M11.1.0',
	'America/Sao_Paulo': '<-03>3',
	'America/Toronto': 'EST5EDT,M3.2.0,M11.1.0',
	'Asia/Dubai': '<+04>-4',
	'Asia/Kolkata': 'IST-5:30',
	'Asia/Shanghai': 'CST-8',
	'Asia/Singapore': '<+08>-8',
	'Asia/Tokyo': 'JST-9',
	'Australia/Adelaide': 'ACST-9:30ACDT,M10.1.0,M4.1.0/3',
	'Australia/Brisbane': 'AEST-10',
	'Australia/Melbourne': 'AEST-10AEDT,M10.1.0,M4.1.0/3',
	'Australia/Perth': 'AWST-8',
	'Australia/Sydney': 'AEST-10AEDT,M10.1.0,M4.1.0/3',
	'Europe/Amsterdam': 'CET-1CEST,M3.5.0,M10.5.0/3',
	'Europe/Berlin': 'CET-1CEST,M3.5.0,M10.5.0/3',
	'Europe/London': 'GMT0BST,M3.5.0/1,M10.5.0',
	'Europe/Madrid': 'CET-1CEST,M3.5.0,M10.5.0/3',
	'Europe/Paris': 'CET-1CEST,M3.5.0,M10.5.0/3',
	'Europe/Rome': 'CET-1CEST,M3.5.0,M10.5.0/3',
	'Pacific/Auckland': 'NZST-12NZDT,M9.5.0,M4.1.0/3',
};

const ENCRYPTIONS = ['none', 'psk', 'psk2', 'psk-mixed', 'sae', 'sae-mixed'];
const SKETCH = '/tmp/sketch.hex';

function trim_read(path) {
	let s = readfile(path);
	return s != null ? trim(s) : null;
}

function num(path) {
	let s = trim_read(path);
	return s != null ? +s : null;
}

// Run a fixed command line (never with user input in it) and return its
// output and exit code.
function run(cmd) {
	let p = popen(cmd + ' 2>&1', 'r');
	if (!p)
		return { code: -1, output: '' };
	let out = p.read('all') ?? '';
	let code = p.close();
	return { code, output: out };
}

// Start a command in the background, detached, after `delay` seconds. The
// arguments are passed to the shell as $1, $2, ... so they are never parsed
// as shell code.
function spawn_later(delay, script, args) {
	system(['/bin/sh', '-c',
		`(sleep ${int(delay)}; ${script}) </dev/null >/dev/null 2>&1 &`,
		'sh', ...(args ?? [])]);
}

function netifd_status(iface) {
	let conn = ubus.connect();
	let st = conn?.call(`network.interface.${iface}`, 'status');
	conn?.disconnect();
	return st;
}

function wireless_status() {
	let conn = ubus.connect();
	let st = conn?.call('network.wireless', 'status');
	conn?.disconnect();
	return st;
}

function ipv4_of(st) {
	return st?.['ipv4-address']?.[0]?.address;
}

function gateway_of(st) {
	for (let r in st?.route ?? [])
		if (r.target == '0.0.0.0' && r.mask == 0)
			return r.nexthop;
	return null;
}

function netdev(ifname) {
	if (!ifname || !stat(`/sys/class/net/${ifname}`))
		return {};
	return {
		mac: uc(trim_read(`/sys/class/net/${ifname}/address`) ?? ''),
		carrier: num(`/sys/class/net/${ifname}/carrier`) == 1,
		rx_bytes: num(`/sys/class/net/${ifname}/statistics/rx_bytes`),
		tx_bytes: num(`/sys/class/net/${ifname}/statistics/tx_bytes`),
	};
}

// wpa_supplicant's key management for the network we're on, e.g. "WPA2-PSK"
// or "WPA2-PSK SAE".
function encryption_of(text) {
	text = lc(text ?? '');
	if (text == '' || text == 'none' || text == 'unknown')
		return 'none';
	if (index(text, '802.1x') >= 0 || index(text, 'eap') >= 0)
		return 'eap';
	let sae = index(text, 'sae') >= 0, psk = index(text, 'psk') >= 0;
	if (sae && psk)
		return 'sae-mixed';
	if (sae)
		return 'sae';
	if (index(text, 'wpa2') >= 0 || index(text, 'rsn') >= 0)
		return 'psk2';
	return 'psk';
}

// A scanned network's RSN key management list, e.g. [ "WPA PSK", "SAE" ].
function scan_encryption(crypto) {
	let keys = crypto?.key_mgmt ?? [];
	if (!length(keys))
		return 'none';
	let sae = false, psk = false, eap = false;
	for (let k in keys) {
		k = lc(k);
		if (index(k, 'sae') >= 0) sae = true;
		else if (index(k, 'psk') >= 0) psk = true;
		else if (index(k, '802.1x') >= 0 || index(k, 'fils') >= 0) eap = true;
	}
	if (sae && psk) return 'sae-mixed';
	if (sae) return 'sae';
	if (psk) return 'psk2';
	return eap ? 'eap' : 'none';
}

// iwinfo's link quality is 0..70.
function quality_pct(q) {
	return q != null ? int(+q * 100 / 70) : null;
}

function iwinfo_json(ifname, cmd) {
	if (!match(ifname ?? '', /^[A-Za-z0-9._-]+$/))
		return null;
	let r = run(`iwinfo -j ${ifname} ${cmd}`);
	return r.code == 0 ? json(r.output) : null;
}

function iwinfo_info(ifname) {
	let bss = iwinfo_json(ifname, 'info')?.[0];
	if (!bss)
		return {};
	let stations = iwinfo_json(ifname, 'assoclist') ?? {};
	return {
		ssid: bss.ssid,
		channel: bss.channel != null ? +bss.channel : null,
		signal: bss.signal ? int(bss.signal) : null,
		quality: quality_pct(bss.quality),
		encryption: encryption_of(bss.encryption),
		// A client lists the access point it's connected to here.
		stations: length(keys(stations)),
	};
}

function wifi_ifaces() {
	let res = {};
	for (let radio, r in wireless_status() ?? {})
		for (let i in r.interfaces ?? [])
			res[i.section] = i.ifname;
	return res;
}

function yun_version() {
	let v = {};
	for (let line in split(readfile('/etc/yun_release') ?? '', '\n')) {
		let m = match(line, /^([A-Z_]+)='?([^']*)'?$/);
		if (m) v[m[1]] = m[2];
	}
	return v;
}

function openwrt_release() {
	let v = {};
	for (let line in split(readfile('/etc/openwrt_release') ?? '', '\n')) {
		let m = match(line, /^([A-Z_]+)='?([^']*)'?$/);
		if (m) v[m[1]] = m[2];
	}
	return v;
}

function meminfo() {
	let m = {};
	for (let line in split(readfile('/proc/meminfo') ?? '', '\n')) {
		let kv = match(line, /^(\w+):\s+(\d+) kB/);
		if (kv) m[kv[1]] = +kv[2] * 1024;
	}
	return { total: m.MemTotal, free: m.MemFree, available: m.MemAvailable ?? m.MemFree,
		swap_total: m.SwapTotal ?? 0, swap_free: m.SwapFree ?? 0 };
}

function storage() {
	let res = [];
	let p = popen('df -k /overlay /mnt/* 2>/dev/null', 'r');
	if (!p)
		return res;
	let seen = {};
	p.read('line');   // header
	for (let line = p.read('line'); length(line); line = p.read('line')) {
		let f = split(trim(line), /\s+/);
		if (length(f) < 6 || seen[f[0]])
			continue;
		seen[f[0]] = true;
		let mount = f[5];
		let name = mount == '/overlay' ? 'Internal flash' :
			(index(f[0], '/dev/sd') == 0 || index(f[0], '/dev/mmc') == 0) ? `SD card (${mount})` : mount;
		push(res, { name, mount, total: +f[1] * 1024, used: +f[2] * 1024 });
	}
	p.close();
	return res;
}

function bridge_running() {
	for (let d in glob('/proc/[0-9]*/cmdline')) {
		let c = readfile(d);
		if (c && index(c, '/usr/lib/yun-bridge/bridge.py') >= 0)
			return true;
	}
	return false;
}

// Newlines and NULs would break the config files and tools these go to.
function bad_chars(s) {
	return index(s, '\n') >= 0 || index(s, '\r') >= 0 || index(s, '\u0000') >= 0;
}

function root_password_set() {
	for (let line in split(readfile('/etc/shadow') ?? '', '\n')) {
		let f = split(line, ':');
		if (f[0] == 'root')
			return !(f[1] in [ '', '!', '*', 'x' ]);
	}
	return false;
}


// --- Terminal ---------------------------------------------------------------
//
// Each command runs as a background job whose output goes to a file, which
// the panel polls. Output is capped (a runaway command can't fill /tmp, which
// is RAM), commands get no input, and the working directory carries over
// from one command to the next the way the panel passes it back.

const SHELL_DIR = '/tmp/yun-shell';
const SHELL_MAX_OUTPUT = 1048576;
const SHELL_MAX_JOBS = 3;

// The job, run by /bin/sh with $1 = directory, $2 = command, $3 = file prefix.
// The EXIT trap records the exit code and directory even when the command
// itself runs "exit".
const SHELL_JOB = `
{
	sh -c 'echo $$ > "$3.pid"
		cd "$1" 2>/dev/null || cd /root
		trap '"'"'rc=$?; pwd > "$3.cwd"; echo $rc > "$3.rc"'"'"' EXIT
		eval "$2"' sh "$1" "$2" "$3" </dev/null 2>&1 |
		head -c ${SHELL_MAX_OUTPUT} > "$3.out"
	touch "$3.done"
} >/dev/null 2>&1 &
`;

function shell_job_path(id) {
	return match(id ?? '', /^[0-9a-f]{16}$/) ? `${SHELL_DIR}/${id}` : null;
}

function shell_running() {
	let n = 0;
	for (let f in glob(`${SHELL_DIR}/*.pid`))
		if (!stat(replace(f, /\.pid$/, '.done')))
			n++;
	return n;
}

// Files of jobs nobody has polled for an hour.
function shell_cleanup() {
	let old = time() - 3600;
	for (let f in glob(`${SHELL_DIR}/*`)) {
		let st = stat(f);
		if (st && st.mtime < old)
			unlink(f);
	}
}

// Kill a job's process and everything it started.
function shell_kill_tree(pid) {
	let children = {};
	for (let d in lsdir('/proc') ?? []) {
		if (!match(d, /^[0-9]+$/))
			continue;
		let st = readfile(`/proc/${d}/stat`);
		let m = st ? match(st, /\) \S (\d+)/) : null;
		if (m)
			push(children[m[1]] ??= [], d);
	}
	let all = [], todo = [ '' + pid ];
	while (length(todo)) {
		let p = shift(todo);
		push(all, p);
		for (let c in children[p] ?? [])
			push(todo, c);
	}
	system([ 'kill', '-TERM', ...all ]);
	system([ 'sh', '-c', 'sleep 1; kill -KILL "$@" 2>/dev/null', 'sh', ...all ]);
}

const methods = {
	status: {
		call: function() {
			let uci = cursor();
			let loadavg = split(readfile('/proc/loadavg') ?? '', ' ');
			let rel = openwrt_release();
			let yun = yun_version();
			let state = uci.get('arduino', '@arduino[0]', 'wifi_state') ?? 'ap';
			let ifaces = wifi_ifaces();

			// Wi-Fi: whichever of the two roles is active.
			let client = state == 'client';
			let wifname = client ? ifaces.yun_sta : ifaces.yun_ap;
			let wst = netifd_status(client ? 'wwan' : 'lan');
			let info = wifname ? iwinfo_info(wifname) : {};
			let wifi = {
				mode: state,
				ifname: wifname,
				ssid: info.ssid ?? uci.get('wireless', client ? 'yun_sta' : 'yun_ap', 'ssid'),
				ap_ssid: uci.get('wireless', 'yun_ap', 'ssid'),
				connected: client ? (info.stations ?? 0) > 0 : true,
				signal: client ? info.signal : null,
				quality: client ? info.quality : null,
				channel: info.channel,
				encryption: info.encryption,
				ipv4: ipv4_of(wst),
				...netdev(wifname),
			};
			if (!client)
				wifi.clients = info.stations ?? 0;
			delete wifi.carrier;

			let est = netifd_status('wan');
			let ethernet = {
				ipv4: ipv4_of(est),
				gateway: gateway_of(est),
				...netdev(est?.l3_device ?? est?.device ?? 'eth0'),
			};

			let result = {
				hostname: uci.get('system', '@system[0]', 'hostname'),
				zonename: uci.get('system', '@system[0]', 'zonename') ?? 'UTC',
				model: trim_read('/tmp/sysinfo/model') ?? 'Arduino Yún',
				time: time(),
				uptime: int(+split(readfile('/proc/uptime') ?? '0', ' ')[0]),
				load: [ +loadavg[0], +loadavg[1], +loadavg[2] ],
				memory: meminfo(),
				storage: storage(),
				firmware: {
					version: yun.VERSION ? `Yún ${yun.VERSION}` : null,
					openwrt: rel.DISTRIB_DESCRIPTION,
					kernel: trim_read('/proc/sys/kernel/osrelease'),
					description: rel.DISTRIB_DESCRIPTION,
				},
				bridge: { running: bridge_running() },
				rest_secure: uci.get('arduino', '@arduino[0]', 'secure_rest_api') != 'false',
				password_set: root_password_set(),
				wifi,
				ethernet,
			};
			uci.unload();
			return result;
		}
	},

	wifi_scan: {
		call: function() {
			let ifaces = wifi_ifaces();
			let dev = ifaces.yun_sta ?? ifaces.yun_ap;
			if (!dev)
				return { error: 'Wi-Fi is not running' };

			let results = [];
			for (let cell in iwinfo_json(dev, 'scan') ?? [])
				push(results, {
					bssid: cell.bssid,
					ssid: cell.ssid,
					channel: cell.channel,
					signal: cell.dbm,
					quality: quality_pct(cell.quality),
					encryption: scan_encryption(cell.crypto),
				});
			return { results: filter(results, r => r.ssid != null && r.ssid != '' && r.encryption != 'eap') };
		}
	},

	wifi_client: {
		args: { ssid: '', encryption: '', key: '' },
		call: function(req) {
			let ssid = req.args.ssid, enc = req.args.encryption, key = req.args.key ?? '';
			if (type(ssid) != 'string' || length(ssid) < 1 || length(ssid) > 32)
				return { error: 'The network name must be 1 to 32 characters' };
			if (!(enc in ENCRYPTIONS))
				return { error: 'Unsupported security type' };
			if (enc != 'none' && (length(key) < 8 || length(key) > 63))
				return { error: 'The password must be 8 to 63 characters' };
			if (bad_chars(ssid + key))
				return { error: 'Invalid characters' };
			// Reply first: switching networks drops this connection.
			spawn_later(2, 'exec yun-wifi client "$1" "$2" "$3"', [ ssid, enc, key ]);
			return { ok: true };
		}
	},

	wifi_setup_ap: {
		call: function() {
			spawn_later(2, 'exec yun-wifi ap');
			return { ok: true };
		}
	},

	sketch_flash: {
		call: function() {
			let st = stat(SKETCH);
			if (!st || st.size == 0)
				return { error: 'No sketch uploaded' };
			if (st.size > 256 * 1024)
				return { error: 'That file is too big for an ATmega32U4 sketch' };
			let merge = run(`merge-sketch-with-bootloader.lua ${SKETCH}`);
			if (merge.code != 0) {
				unlink(SKETCH);
				return { code: merge.code, output: merge.output };
			}
			run('kill-bridge');
			let r = run(`run-avrdude ${SKETCH} -q -q`);
			unlink(SKETCH);
			return { code: r.code, output: r.output };
		}
	},

	mcu_reset: {
		call: function() {
			let r = run('reset-mcu');
			return r.code == 0 ? { ok: true } : { error: r.output };
		}
	},

	bridge_data: {
		call: function() {
			let values = bridge.get_all();
			return { running: values != null, values: values ?? {} };
		}
	},

	bridge_put: {
		args: { key: '', value: '' },
		call: function(req) {
			if (type(req.args.key) != 'string' || req.args.key == '')
				return { error: 'A key is required' };
			return bridge.put(req.args.key, '' + (req.args.value ?? ''))
				? { ok: true } : { error: 'The bridge is not running' };
		}
	},

	bridge_delete: {
		args: { key: '' },
		call: function(req) {
			return bridge.del('' + req.args.key) ? { ok: true } : { error: 'The bridge is not running' };
		}
	},

	mailbox_send: {
		args: { message: '' },
		call: function(req) {
			return bridge.mailbox('' + req.args.message) ? { ok: true } : { error: 'The bridge is not running' };
		}
	},

	settings_set: {
		args: { hostname: '', zonename: '', rest_secure: false },
		call: function(req) {
			let a = req.args, uci = cursor();
			if (a.hostname != null) {
				if (!match(a.hostname, /^[A-Za-z0-9][A-Za-z0-9-]{0,62}$/))
					return { error: 'Use letters, digits and dashes for the name' };
				uci.set('system', '@system[0]', 'hostname', a.hostname);
			}
			if (a.zonename != null && a.zonename != '') {
				if (!ZONES[a.zonename])
					return { error: 'Unknown time zone' };
				uci.set('system', '@system[0]', 'zonename', a.zonename);
				uci.set('system', '@system[0]', 'timezone', ZONES[a.zonename]);
			}
			if (a.rest_secure != null)
				uci.set('arduino', '@arduino[0]', 'secure_rest_api', a.rest_secure ? 'true' : 'false');
			uci.save('system');
			uci.save('arduino');
			uci.commit('system');
			uci.commit('arduino');
			uci.unload();
			// Apply the name and time zone, and re-announce over mDNS.
			spawn_later(1, '/etc/init.d/system reload; /etc/init.d/umdns reload');
			return { ok: true };
		}
	},

	password_set: {
		args: { password: '' },
		call: function(req) {
			let pw = req.args.password;
			if (type(pw) != 'string' || length(pw) < 8 || length(pw) > 128 || bad_chars(pw))
				return { error: 'Use 8 to 128 characters' };
			let p = popen('passwd root >/dev/null 2>&1', 'w');
			if (!p)
				return { error: 'passwd failed' };
			p.write(pw + '\n' + pw + '\n');
			return p.close() == 0 ? { ok: true } : { error: 'passwd failed' };
		}
	},

	shell_start: {
		args: { command: '', cwd: '' },
		call: function(req) {
			let cmd = req.args.command, cwd = req.args.cwd ?? '/root';
			if (type(cmd) != 'string' || trim(cmd) == '' || length(cmd) > 4096)
				return { error: 'Type a command' };
			if (index(cmd, '\u0000') >= 0 || type(cwd) != 'string' || index(cwd, '\u0000') >= 0)
				return { error: 'Invalid characters' };
			mkdir(SHELL_DIR, 0700);
			shell_cleanup();
			if (shell_running() >= SHELL_MAX_JOBS)
				return { error: `Already running ${SHELL_MAX_JOBS} commands; stop one first` };
			let id = hexenc(readfile('/dev/urandom', 8));
			let path = shell_job_path(id);
			system([ '/bin/sh', '-c', SHELL_JOB, 'sh', cwd, cmd, path ]);
			return { id };
		}
	},

	shell_poll: {
		args: { id: '', offset: 0 },
		call: function(req) {
			let path = shell_job_path(req.args.id);
			if (!path || !stat(`${path}.out`))
				return { error: 'No such command' };
			let offset = int(req.args.offset ?? 0);
			let f = open(`${path}.out`, 'r');
			f.seek(offset);
			let output = f.read(65536) ?? '';
			f.close();
			offset += length(output);
			let done = stat(`${path}.done`) != null;
			let size = stat(`${path}.out`)?.size ?? 0;
			// Base64: output can be any bytes, and a chunk can end halfway
			// through a UTF-8 character; the panel decodes it as a stream.
			let res = { output: b64enc(output), offset, done: done && offset >= size };
			if (res.done) {
				let rc = trim_read(`${path}.rc`);
				res.rc = (rc != null && !stat(`${path}.stopped`)) ? +rc : null;  // null: stopped
				res.cwd = trim_read(`${path}.cwd`);
				res.truncated = size >= SHELL_MAX_OUTPUT;
				for (let ext in [ 'out', 'pid', 'rc', 'cwd', 'done', 'stopped' ])
					unlink(`${path}.${ext}`);
			}
			return res;
		}
	},

	shell_stop: {
		args: { id: '' },
		call: function(req) {
			let path = shell_job_path(req.args.id);
			let pid = path ? trim_read(`${path}.pid`) : null;
			if (!pid || !match(pid, /^[0-9]+$/))
				return { error: 'No such command' };
			if (!stat(`${path}.done`)) {
				writefile(`${path}.stopped`, '');
				shell_kill_tree(pid);
			}
			return { ok: true };
		}
	},

	update_check: {
		call: function() {
			if (!access('/usr/bin/yun-update', 'x'))
				return { error: 'yun-update is not installed' };
			let r = run('yun-update check --json');
			let res = json(r.output);
			return type(res) == 'object' ? res : { error: trim(r.output) || 'update check failed' };
		}
	},

	update_apply: {
		call: function() {
			if (!access('/usr/bin/yun-update', 'x'))
				return { error: 'yun-update is not installed' };
			mkdir('/tmp/yun-update');
			writefile('/tmp/yun-update/stage', 'starting\n');
			spawn_later(1, 'exec yun-update apply');
			return { ok: true };
		}
	},

	// What "yun-update apply" is doing, for the panel to show. It answers
	// until sysupgrade stops the web server to write the flash.
	update_progress: {
		call: function() {
			let log = trim(readfile('/tmp/yun-update.log') ?? '');
			let lines = log != '' ? split(log, '\n') : [];
			let total = trim_read('/tmp/yun-update/total');
			return {
				stage: trim_read('/tmp/yun-update/stage') ?? 'idle',
				downloaded: stat('/tmp/yun-update/sysupgrade.bin')?.size ?? 0,
				total: total != null ? +total : null,
				message: length(lines) ? replace(lines[-1], /^yun-update: /, '') : null
			};
		}
	},
};

return { yun: methods };
