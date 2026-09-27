// Stand-in for the ubus module: answers from $UBUS_FIXTURE (JSON), and
// "session login" accepts root with the password in $ROOT_PASSWORD.
import { readfile, writefile } from 'fs';

export function connect() {
	let fx = json(readfile(getenv('UBUS_FIXTURE')) ?? '{}');
	return {
		call: function(obj, method, args) {
			if (obj == 'session' && method == 'login') {
				let log = getenv('LOGIN_LOG');
				if (log) writefile(log, (readfile(log) ?? '') + 'login\n');
				return (args.username == 'root' && args.password == getenv('ROOT_PASSWORD'))
					? { ubus_rpc_session: 'abc' } : null;
			}
			if (obj == 'session')
				return {};
			return fx[`${obj} ${method}`];
		},
		disconnect: () => true,
	};
};
