{%
// SPDX-License-Identifier: GPL-2.0-or-later
//
// The Yun's REST API, as served by the stock firmware's web panel, run as a
// uhttpd ucode handler for these prefixes:
//
//   /arduino/<path>        passed to the sketch's BridgeServer on port 5555
//                          ("<path>\r\n"), which answers like a CGI script
//   /data/get[/<key>]      read the datastore
//   /data/put/<key>/<val>  write it
//   /data/delete/<key>     delete a key
//   /mailbox/<message>     send a message to the sketch's Mailbox
//
// ?callback=fn (or ?jsonp=fn) wraps JSON answers for JSONP, as before.
// When arduino.@arduino[0].secure_rest_api isn't "false", every call needs
// HTTP basic auth as root with the root password.

import * as socket from 'socket';
import * as ubus from 'ubus';
import { cursor } from 'uci';
import * as bridge from 'yun.bridge';

// Remember good credentials for a minute, so a sketch polling the API
// doesn't cost a password check (crypt) on every request.
let auth_cache = {};

function reply(status, type, body, extra) {
	let out = `Status: ${status}\r\nContent-Type: ${type}\r\nCache-Control: no-cache\r\n`;
	for (let k, v in extra ?? {})
		out += `${k}: ${v}\r\n`;
	uhttpd.send(out + '\r\n' + (body ?? ''));
}

function urldecode(s) {
	s = replace(s, '+', ' ');
	return replace(s, /%([0-9A-Fa-f]{2})/g, (m, h) => chr(hex(h)));
}

function query_param(qs, names) {
	for (let pair in split(qs ?? '', '&')) {
		let kv = split(pair, '=', 2);
		if (kv[0] in names)
			return urldecode(kv[1] ?? '');
	}
	return null;
}

function reply_json(obj, callback) {
	if (callback && match(callback, /^[A-Za-z_$][A-Za-z0-9_$.]*$/))
		reply('200 OK', 'application/javascript', `${callback}(${sprintf('%J', obj)});`);
	else
		reply('200 OK', 'application/json', sprintf('%J', obj));
}

function check_auth(env) {
	let header = env.HTTP_AUTHORIZATION ?? env.headers?.authorization;
	let m = header ? match(header, /^Basic\s+(\S+)$/i) : null;
	if (!m)
		return false;

	let now = time();
	if (auth_cache[m[1]] && auth_cache[m[1]] > now)
		return true;

	let cred = b64dec(m[1]);
	let i = cred ? index(cred, ':') : -1;
	if (i < 0 || substr(cred, 0, i) != 'root')
		return false;

	// Let rpcd check the password against /etc/shadow.
	let conn = ubus.connect();
	let s = conn?.call('session', 'login', { username: 'root', password: substr(cred, i + 1), timeout: 60 });
	if (s?.ubus_rpc_session)
		conn.call('session', 'destroy', { ubus_rpc_session: s.ubus_rpc_session });
	conn?.disconnect();

	if (!s?.ubus_rpc_session)
		return false;
	auth_cache = { [m[1]]: now + 60 };
	return true;
}

function handle_data(parts, callback) {
	let cmd = parts[0], key = parts[1], value = parts[2];
	let r;

	if (cmd == 'get')
		r = bridge.request(key != null ? { command: 'get', key } : { command: 'get' }, 'get', key);
	else if (cmd == 'put' && key != null && value != null)
		r = bridge.request({ command: 'put', key, value }, 'put', key);
	else if (cmd == 'delete' && key != null)
		r = bridge.request({ command: 'delete', key }, 'delete', key);
	else
		return reply('404 Not Found', 'text/plain', 'Unknown data command\n');

	if (r == null)
		return reply('503 Service Unavailable', 'text/plain', 'The bridge is not running\n');
	reply_json(r, callback);
}

// Answer with what the sketch's BridgeServer client wrote: either plain text,
// or "Status: 200" style headers, a blank line and a body.
function handle_arduino(path, callback, timeout) {
	let sock = socket.connect('127.0.0.1', 5555, null, 1000);
	if (!sock)
		return reply('502 Bad Gateway', 'text/plain', 'Could not connect to YunServer. Is the sketch running?\n');

	sock.setopt(socket.SOL_SOCKET, socket.SO_RCVTIMEO, { sec: timeout, usec: 0 });
	sock.send(path + '\r\n');

	let resp = '';
	while (length(resp) < 65536) {
		let chunk = sock.recv(4096);
		if (chunk == null || chunk == '')
			break;
		resp += chunk;
	}
	sock.close();

	if (!match(resp, /^Status/))
		return reply('200 OK', 'text/plain', resp);

	let sep = index(resp, '\r\n\r\n');
	let head = sep >= 0 ? substr(resp, 0, sep) : resp;
	let body = sep >= 0 ? substr(resp, sep + 4) : '';
	let status = '200 OK', type = 'text/plain', extra = {};

	for (let line in split(head, '\r\n')) {
		let kv = match(line, /^([^:]+):\s*(.*)$/);
		if (!kv)
			continue;
		let k = lc(kv[1]);
		if (k == 'status')
			status = kv[2];
		else if (k == 'content-type')
			type = kv[2];
		else if (!match(kv[1], /[\r\n]/))
			extra[kv[1]] = kv[2];
	}

	if (type == 'application/json' && callback && match(callback, /^[A-Za-z_$][A-Za-z0-9_$.]*$/))
		return reply(status, 'application/javascript', `${callback}(${body});`);
	reply(status, type, body, extra);
}

global.handle_request = function(env) {
	let uci = cursor();
	let secure = uci.get('arduino', '@arduino[0]', 'secure_rest_api') != 'false';
	let timeout = +(uci.get('arduino', '@arduino[0]', 'socket_timeout') ?? 5) || 5;
	uci.unload();

	if (secure && !check_auth(env))
		return reply('401 Unauthorized', 'text/plain', 'Authorization required\n',
			{ 'WWW-Authenticate': 'Basic realm="arduino"' });

	let uri = split(env.REQUEST_URI ?? '', '?', 2);
	let callback = query_param(uri[1], [ 'callback', 'jsonp' ]);
	let parts = map(filter(split(uri[0], '/'), p => p != ''), urldecode);
	let prefix = shift(parts);

	if (prefix == 'data')
		return handle_data(parts, callback);

	if (prefix == 'mailbox') {
		if (!length(parts))
			return reply('400 Bad Request', 'text/plain', 'Empty message\n');
		return bridge.mailbox(join('/', parts))
			? reply('200 OK', 'text/plain', '')
			: reply('503 Service Unavailable', 'text/plain', 'The bridge is not running\n');
	}

	if (prefix == 'arduino') {
		if (!length(parts))
			return reply('404 Not Found', 'text/plain', '');
		// One request line: drop any decoded line breaks.
		return handle_arduino(replace(join('/', parts), /[\r\n]/g, ''), callback, timeout);
	}

	reply('404 Not Found', 'text/plain', '');
};
%}
