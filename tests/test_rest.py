#!/usr/bin/env python3
# Tests for the REST API handler (/usr/share/yun/rest.uc), run under a host
# build of ucode as uhttpd would run it, against the real Python bridge and
# a fake sketch (YunServer) on port 5555. See test_rpcd.py for UCODE_BUILD.

import base64
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, '..')
SHARE = os.path.join(ROOT, 'feed', 'yun-webpanel', 'files', 'usr', 'share')
UCODE = os.environ.get('UCODE_BUILD', '/home/user/ucode/build')

HARNESS = '''{%
global.uhttpd = { send: (s) => print(s) };
include(getenv("REST_UC"));
for (let env in json(getenv("REQUESTS"))) {
	handle_request(env);
	print("\\n=====\\n");
}
%}
'''


def port_free(port):
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind(('127.0.0.1', port))
        return True
    except OSError:
        return False
    finally:
        s.close()


class FakeYunServer(threading.Thread):
    """A sketch using BridgeServer: reads one line, answers, closes."""

    def __init__(self, answer):
        super().__init__(daemon=True)
        self.answer = answer
        self.received = []
        self.srv = socket.socket()
        self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.srv.bind(('127.0.0.1', 5555))
        self.srv.listen(5)
        self.srv.settimeout(0.2)
        self.stop = False

    def run(self):
        while not self.stop:
            try:
                conn, _ = self.srv.accept()
            except socket.timeout:
                continue
            data = b''
            while not data.endswith(b'\r\n'):
                chunk = conn.recv(100)
                if not chunk:
                    break
                data += chunk
            self.received.append(data)
            conn.sendall(self.answer)
            conn.close()
        self.srv.close()


@unittest.skipUnless(os.path.exists(os.path.join(UCODE, 'ucode')), 'no ucode build (set UCODE_BUILD)')
class RestTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not port_free(5700) or not port_free(5555):
            raise unittest.SkipTest('ports 5700/5555 in use')
        cls.bridge = subprocess.Popen(
            [sys.executable, os.path.join(ROOT, 'feed', 'yun-bridge', 'files', 'usr', 'lib', 'yun-bridge', 'bridge.py'),
             '--mailbox-port', '5700', '--console-port', '16572'],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.monotonic() + 10
        while True:
            try:
                socket.create_connection(('127.0.0.1', 5700), timeout=1).close()
                break
            except OSError:
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.1)

    @classmethod
    def tearDownClass(cls):
        cls.bridge.kill()
        cls.bridge.wait()
        cls.bridge.stdin.close()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.secure = True

    def run_requests(self, *envs):
        t = self.tmp.name
        with open(os.path.join(t, 'uci.json'), 'w') as f:
            json.dump({'arduino': {'@arduino[0]': {
                'secure_rest_api': 'true' if self.secure else 'false', 'socket_timeout': '2'}}}, f)
        with open(os.path.join(t, 'ubus.json'), 'w') as f:
            f.write('{}')
        harness = os.path.join(t, 'harness.uc')
        with open(harness, 'w') as f:
            f.write(HARNESS)
        env = dict(os.environ, LD_LIBRARY_PATH=UCODE,
                   UCI_FIXTURE=os.path.join(t, 'uci.json'), UBUS_FIXTURE=os.path.join(t, 'ubus.json'),
                   LOGIN_LOG=os.path.join(t, 'logins'), ROOT_PASSWORD='s3cret!',
                   REST_UC=os.path.join(SHARE, 'yun', 'rest.uc'), REQUESTS=json.dumps(envs))
        p = subprocess.run(
            [os.path.join(UCODE, 'ucode'), '-T',
             '-L', os.path.join(UCODE, '*.so'),
             '-L', os.path.join(HERE, 'rpcd', 'modules', '*.uc'),
             '-L', os.path.join(SHARE, 'ucode', '*.uc'), harness],
            env=env, capture_output=True, timeout=30)
        self.assertEqual(p.returncode, 0, p.stderr.decode())
        out = []
        for raw in p.stdout.decode().split('\n=====\n')[:-1]:
            head, _, body = raw.partition('\r\n\r\n')
            lines = head.split('\r\n')
            headers = dict(l.split(': ', 1) for l in lines)
            out.append((headers.pop('Status'), headers, body))
        return out

    def req(self, uri, auth='root:s3cret!'):
        env = {'REQUEST_URI': uri, 'REQUEST_METHOD': 'GET'}
        if auth:
            env['HTTP_AUTHORIZATION'] = 'Basic ' + base64.b64encode(auth.encode()).decode()
        return env

    def one(self, uri, **kw):
        return self.run_requests(self.req(uri, **kw))[0]

    def test_auth(self):
        status, headers, _ = self.one('/data/get', auth=None)
        self.assertEqual(status, '401 Unauthorized')
        self.assertEqual(headers['WWW-Authenticate'], 'Basic realm="arduino"')
        self.assertEqual(self.one('/data/get', auth='root:wrong')[0], '401 Unauthorized')
        self.assertEqual(self.one('/data/get', auth='admin:s3cret!')[0], '401 Unauthorized')
        self.assertEqual(self.one('/data/get')[0], '200 OK')

    def test_auth_is_cached(self):
        results = self.run_requests(self.req('/data/get'), self.req('/data/get'), self.req('/data/get'))
        self.assertEqual([r[0] for r in results], ['200 OK'] * 3)
        with open(os.path.join(self.tmp.name, 'logins')) as f:
            self.assertEqual(f.read().count('login'), 1)

    def test_open_api(self):
        self.secure = False
        self.assertEqual(self.one('/data/get', auth=None)[0], '200 OK')

    def test_datastore(self):
        status, headers, body = self.one('/data/put/temp/23.5')
        self.assertEqual(status, '200 OK')
        self.assertEqual(headers['Content-Type'], 'application/json')
        self.assertEqual(json.loads(body), {'response': 'put', 'key': 'temp', 'value': '23.5'})
        self.assertEqual(json.loads(self.one('/data/get/temp')[2])['value'], '23.5')
        self.assertEqual(json.loads(self.one('/data/put/msg/hello%20world')[2])['value'], 'hello world')
        self.assertEqual(json.loads(self.one('/data/get')[2])['value'], {'temp': '23.5', 'msg': 'hello world'})
        self.assertEqual(json.loads(self.one('/data/delete/temp')[2])['value'], '23.5')
        self.assertEqual(self.one('/data/bogus')[0], '404 Not Found')

    def test_jsonp(self):
        self.one('/data/put/k/v')
        status, headers, body = self.one('/data/get/k?callback=show')
        self.assertEqual(headers['Content-Type'], 'application/javascript')
        self.assertTrue(body.startswith('show({') and body.endswith('});'), body)
        # Anything that isn't a plain function name is ignored.
        status, headers, body = self.one('/data/get/k?callback=alert(1)//')
        self.assertEqual(headers['Content-Type'], 'application/json')

    def test_mailbox(self):
        self.assertEqual(self.one('/mailbox/hello')[0], '200 OK')
        self.assertEqual(self.one('/mailbox')[0], '400 Bad Request')

    def test_arduino_plain(self):
        srv = FakeYunServer(b'Pin D13 set to 1\n')
        srv.start()
        self.addCleanup(lambda: (setattr(srv, 'stop', True), srv.join()))
        status, headers, body = self.one('/arduino/digital/13/1')
        self.assertEqual(status, '200 OK')
        self.assertEqual(body, 'Pin D13 set to 1\n')
        self.assertEqual(srv.received, [b'digital/13/1\r\n'])

    def test_arduino_headers_and_no_line_injection(self):
        srv = FakeYunServer(b'Status: 201\r\nContent-Type: application/json\r\nX-Sketch: yes\r\n\r\n{"a":1}')
        srv.start()
        self.addCleanup(lambda: (setattr(srv, 'stop', True), srv.join()))
        status, headers, body = self.one('/arduino/a%0D%0Ab?callback=cb')
        self.assertEqual(status, '201')
        self.assertEqual(headers['Content-Type'], 'application/javascript')
        self.assertEqual(body, 'cb({"a":1});')
        self.assertEqual(srv.received, [b'ab\r\n'])
        status, headers, body = self.one('/arduino/x')
        self.assertEqual(headers['X-Sketch'], 'yes')

    def test_arduino_without_sketch(self):
        self.assertEqual(self.one('/arduino/digital/13')[0], '502 Bad Gateway')


if __name__ == '__main__':
    unittest.main()
