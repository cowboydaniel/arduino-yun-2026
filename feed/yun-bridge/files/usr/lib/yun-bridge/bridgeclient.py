# SPDX-License-Identifier: GPL-2.0-or-later
#
# Python 3 port of YunBridge, originally Copyright 2013 Arduino LLC.
#
# Talk to the sketch's datastore and mailbox from Linux programs:
#
#   import sys
#   sys.path.insert(0, '/usr/lib/yun-bridge')
#   from bridgeclient import BridgeClient
#
#   client = BridgeClient()
#   client.put('D13', '1')
#   print(client.get('D13'))

import json
import socket
import time


class JSONConnection:
    def __init__(self, host='127.0.0.1', port=5700, timeout=10):
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.buf = ''
        self.decoder = json.JSONDecoder()

    def send(self, obj):
        self.sock.sendall(json.dumps(obj).encode('utf-8'))

    def recv(self, timeout):
        deadline = time.monotonic() + timeout
        while True:
            text = self.buf.lstrip()
            if text:
                try:
                    obj, end = self.decoder.raw_decode(text)
                    self.buf = text[end:]
                    return obj
                except ValueError:
                    pass
            left = deadline - time.monotonic()
            if left <= 0:
                return None
            self.sock.settimeout(left)
            try:
                chunk = self.sock.recv(4096)
            except socket.timeout:
                return None
            if not chunk:
                return None
            self.buf += chunk.decode('utf-8', errors='replace')

    def close(self):
        self.sock.close()


class BridgeClient:
    def __init__(self, host='127.0.0.1', port=5700, timeout=10):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.conn = None
        self.keep_open = False

    def _open(self):
        if self.conn is None:
            self.conn = JSONConnection(self.host, self.port, self.timeout)
        return self.conn

    def _done(self):
        if self.conn is not None and not self.keep_open:
            self.conn.close()
            self.conn = None

    def _wait(self, response, key=None):
        deadline = time.monotonic() + self.timeout
        while True:
            r = self.conn.recv(max(0, deadline - time.monotonic()))
            if r is None:
                return None
            if isinstance(r, dict) and r.get('response') == response and \
                    (key is None or r.get('key') == key):
                return r

    def begin(self):
        """Keep one connection open for several calls, until close()."""
        self._open()
        self.keep_open = True

    def close(self):
        self.keep_open = False
        self._done()

    def get(self, key):
        self._open().send({'command': 'get', 'key': key})
        try:
            r = self._wait('get', key)
            return None if r is None else r.get('value')
        finally:
            self._done()

    def getall(self):
        self._open().send({'command': 'get'})
        try:
            r = self._wait('get')
            return None if r is None else r.get('value')
        finally:
            self._done()

    def put(self, key, value):
        self._open().send({'command': 'put', 'key': key, 'value': value})
        try:
            r = self._wait('put', key)
            return None if r is None else r.get('value')
        finally:
            self._done()

    def delete(self, key):
        self._open().send({'command': 'delete', 'key': key})
        try:
            r = self._wait('delete', key)
            return None if r is None else r.get('value')
        finally:
            self._done()

    def mailbox(self, message):
        self._open().send({'command': 'raw', 'data': message})
        self._done()
