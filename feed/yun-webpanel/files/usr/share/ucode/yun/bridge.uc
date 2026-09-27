// SPDX-License-Identifier: GPL-2.0-or-later
//
// Talk to the bridge's mailbox and datastore on 127.0.0.1:5700: a stream
// of JSON objects in both directions (see yun-bridge's mailbox.py).

'use strict';

import * as socket from 'socket';

const PORT = 5700;

// Index just past the first complete JSON object in `s`, or -1.
function json_end(s) {
	let depth = 0, in_str = false, esc = false;

	for (let i = 0; i < length(s); i++) {
		let c = substr(s, i, 1);

		if (in_str) {
			if (esc) esc = false;
			else if (c == '\\') esc = true;
			else if (c == '"') in_str = false;
		}
		else if (c == '"') in_str = true;
		else if (c == '{' || c == '[') depth++;
		else if (c == '}' || c == ']') {
			if (--depth == 0)
				return i + 1;
		}
	}

	return -1;
}

// Send one request. With `want`, wait up to `timeout` ms for the reply whose
// "response" is `want` (and whose "key" is `key`, when given) and return it.
// Returns null when the bridge isn't running or doesn't answer in time.
export function request(msg, want, key, timeout) {
	timeout ??= 3000;

	let sock = socket.connect('127.0.0.1', PORT, null, 1000);
	if (!sock)
		return null;

	sock.send(sprintf('%J', msg));

	if (!want) {
		sock.close();
		return true;
	}

	let buf = '';
	let deadline = clock()[0] * 1000 + clock()[1] / 1000000 + timeout;
	let reply = null;

	while (reply == null) {
		let now = clock()[0] * 1000 + clock()[1] / 1000000;
		if (now >= deadline)
			break;

		let ready = socket.poll(int(deadline - now), sock);
		if (!ready || !length(ready) || !ready[0][1])
			break;

		let chunk = sock.recv(4096);
		if (chunk == null || chunk == '')
			break;
		buf += chunk;

		// The bridge sends replies back to back with no separator, and
		// also broadcasts other clients' replies: pick ours out.
		while (length(buf)) {
			let end = json_end(buf);
			if (end < 0)
				break;
			let obj = json(substr(buf, 0, end));
			buf = ltrim(substr(buf, end));
			if (type(obj) == 'object' && obj.response == want &&
			    (key == null || obj.key == key)) {
				reply = obj;
				break;
			}
		}
	}

	sock.close();
	return reply;
};

export function get_all() {
	let r = request({ command: 'get' }, 'get');
	return r ? (r.value ?? {}) : null;
};

export function get(k) {
	let r = request({ command: 'get', key: k }, 'get', k);
	return r ? r.value : null;
};

export function put(k, v) {
	return request({ command: 'put', key: k, value: v }, 'put', k) != null;
};

export function del(k) {
	return request({ command: 'delete', key: k }, 'delete', k) != null;
};

export function mailbox(message) {
	return request({ command: 'raw', data: message }) != null;
};
