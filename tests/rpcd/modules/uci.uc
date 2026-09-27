// Stand-in for the uci module: config from $UCI_FIXTURE (JSON), changes
// logged to $UCI_LOG.
import { readfile, writefile } from 'fs';

export function cursor() {
	let conf = json(readfile(getenv('UCI_FIXTURE')) ?? '{}');
	let log = [];
	return {
		get: (c, s, o) => conf?.[c]?.[s]?.[o],
		set: function(c, s, o, v) { conf[c] ??= {}; conf[c][s] ??= {}; conf[c][s][o] = v; push(log, [c, s, o, v]); },
		save: (c) => true,
		commit: function(c) { writefile(getenv('UCI_LOG'), sprintf('%J', log)); return true; },
		unload: () => true,
	};
};
