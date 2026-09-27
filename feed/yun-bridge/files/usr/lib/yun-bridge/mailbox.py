# SPDX-License-Identifier: GPL-2.0-or-later
#
# Python 3 port of YunBridge, originally Copyright 2013 Arduino LLC.
#
# Mailbox and datastore (Bridge.put()/Bridge.get()). Linux programs and the
# web server's /data and /mailbox REST calls reach them through a JSON
# stream on 127.0.0.1:5700; bridgeclient.py is the Python client for it.

import json
from collections import deque

from tcpserver import JSONServer


def to_text(data):
    """MCU bytes -> str, losslessly (invalid UTF-8 survives a round trip)."""
    return data.decode('utf-8', errors='surrogateescape')


def to_bytes(value):
    if isinstance(value, bytes):
        return value
    if not isinstance(value, str):
        value = json.dumps(value)
    return value.encode('utf-8', errors='surrogateescape')


class Mailbox:
    def __init__(self, port=5700):
        self.server = JSONServer('127.0.0.1', port)
        self.incoming = deque()
        self.data_store = {}

    def run(self):
        self.server.run()
        while self.server.available():
            msg = self.server.read()
            if isinstance(msg, dict):
                self.ext_command(msg)

    def ext_command(self, msg):
        command = msg.get('command')

        if command == 'raw':
            if 'data' in msg:
                self.incoming.append(to_bytes(msg['data']))
        elif command == 'get':
            if 'key' in msg:
                k = str(msg['key'])
                self.server.write({'response': 'get', 'key': k,
                                   'value': self.data_store.get(k)})
            else:
                self.server.write({'response': 'get', 'value': self.data_store})
        elif command == 'put':
            if 'key' not in msg or 'value' not in msg:
                return
            k = str(msg['key'])
            v = msg['value'] if isinstance(msg['value'], str) else json.dumps(msg['value'])
            self.data_store[k] = v
            self.server.write({'response': 'put', 'key': k, 'value': v})
        elif command == 'delete':
            if 'key' not in msg:
                return
            k = str(msg['key'])
            v = self.data_store.pop(k, None)
            if v is not None:
                self.server.write({'response': 'delete', 'key': k, 'value': v})
            else:
                self.server.write({'response': 'delete', 'key': k})

    def send(self, obj):
        self.server.write({'request': 'raw', 'data': obj})

    def recv(self):
        return self.incoming.popleft() if self.incoming else None

    def peek(self):
        return self.incoming[0] if self.incoming else None


class SendCommand:
    def __init__(self, mb):
        self.mb = mb

    def run(self, data):
        self.mb.send(to_text(data))
        return b''


class SendJSONCommand:
    def __init__(self, mb):
        self.mb = mb

    def run(self, data):
        text = to_text(data)
        try:
            obj, _ = json.JSONDecoder().raw_decode(text)
        except ValueError:
            obj = text
        self.mb.send(obj)
        return b''


class RecvCommand:
    def __init__(self, mb):
        self.mb = mb

    def run(self, data):
        msg = self.mb.recv()
        return b'' if msg is None else msg


class AvailableCommand:
    def __init__(self, mb):
        self.mb = mb

    def run(self, data):
        msg = self.mb.peek()
        n = 0 if msg is None else len(msg)
        return bytes(((n >> 8) & 0xFF, n & 0xFF))


class DatastoreGetCommand:
    def __init__(self, mb):
        self.mb = mb

    def run(self, data):
        v = self.mb.data_store.get(to_text(data))
        return b'' if v is None else to_bytes(v)


class DatastorePutCommand:
    def __init__(self, mb):
        self.mb = mb

    def run(self, data):
        parts = data.split(b'\xFE')
        if len(parts) != 2:
            return b'\x00'
        self.mb.data_store[to_text(parts[0])] = to_text(parts[1])
        return b'\x01'


def init(cp, port=5700):
    mb = Mailbox(port)
    cp.register(b'M', SendCommand(mb))
    cp.register(b'J', SendJSONCommand(mb))
    cp.register(b'm', RecvCommand(mb))
    cp.register(b'n', AvailableCommand(mb))
    cp.register(b'D', DatastorePutCommand(mb))
    cp.register(b'd', DatastoreGetCommand(mb))
    cp.register_runner(mb)
    return mb
