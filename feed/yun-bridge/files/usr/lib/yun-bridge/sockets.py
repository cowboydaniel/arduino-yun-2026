# SPDX-License-Identifier: GPL-2.0-or-later
#
# Python 3 port of YunBridge, originally Copyright 2013 Arduino LLC.
#
# TCP sockets for the sketch: BridgeClient (and its SSL variant),
# BridgeServer and YunServer.

import errno
import logging
import os
import select
import socket

from tcpserver import listen

try:
    import ssl
    WOULD_BLOCK = (BlockingIOError, ssl.SSLWantReadError, ssl.SSLWantWriteError)
except ImportError:
    # python3-openssl is not installed: plain sockets still work.
    ssl = None
    WOULD_BLOCK = (BlockingIOError,)

log = logging.getLogger('bridge')

MAX_RXBUF = 1024
CA_PATH = '/etc/ssl/certs'
SSL_CONNECT_TIMEOUT = 15


class Client:
    def __init__(self, sock=None):
        self.sock = sock
        self.txbuf = bytearray()
        self.rxbuf = bytearray()
        self.connecting = False
        self.connected = sock is not None
        if sock is not None:
            sock.setblocking(False)

    def connect(self, host, port):
        # Name lookup blocks, as it did in the original bridge.
        try:
            family, stype, proto, _, addr = socket.getaddrinfo(
                host, port, socket.AF_INET, socket.SOCK_STREAM)[0]
        except (OSError, IndexError) as e:
            log.info('cannot resolve %s: %s', host, e)
            return
        self.sock = socket.socket(family, stype, proto)
        self.sock.setblocking(False)
        rc = self.sock.connect_ex(addr)
        if rc not in (0, errno.EINPROGRESS, errno.EWOULDBLOCK):
            log.info('connect to %s:%d failed: %s', host, port, os.strerror(rc))
            self.close()
            return
        self.connecting = True

    def connect_ssl(self, host, port):
        if ssl is None:
            log.warning('SSL connection requested but python3-openssl is missing')
            return
        ctx = ssl.create_default_context()
        try:
            ctx.load_verify_locations(capath=CA_PATH)
        except OSError:
            pass
        try:
            raw = socket.create_connection((host, port), timeout=SSL_CONNECT_TIMEOUT)
            self.sock = ctx.wrap_socket(raw, server_hostname=host)
        except OSError as e:
            log.info('SSL connect to %s:%d failed: %s', host, port, e)
            self.sock = None
            return
        self.sock.setblocking(False)
        self.connected = True

    def _recv(self):
        try:
            chunk = self.sock.recv(MAX_RXBUF - len(self.rxbuf))
        except WOULD_BLOCK:
            return
        except OSError:
            self.close()
            return
        if not chunk:
            self.close()
            return
        self.rxbuf += chunk

    def _send(self):
        try:
            sent = self.sock.send(self.txbuf)
        except WOULD_BLOCK:
            return
        except OSError:
            self.close()
            return
        del self.txbuf[:sent]

    def run(self):
        if self.sock is None:
            return
        if self.connecting:
            _, wr, err = select.select([], [self.sock], [self.sock], 0)
            if err:
                self.close()
                return
            if not wr:
                return
            self.connecting = False
            if self.sock.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR) != 0:
                self.close()
                return
            self.connected = True
        if not self.connected:
            return

        want_read = len(self.rxbuf) < MAX_RXBUF
        rd, wr, _ = select.select([self.sock] if want_read else [],
                                  [self.sock] if self.txbuf else [], [], 0)
        # SSL sockets can hold decrypted data that select() doesn't see.
        pending = getattr(self.sock, 'pending', lambda: 0)()
        if want_read and (rd or pending):
            self._recv()
        if self.connected and wr:
            self._send()

    def recv(self, maxlen):
        res = bytes(self.rxbuf[:maxlen])
        del self.rxbuf[:maxlen]
        return res

    def send(self, data):
        self.txbuf += data

    def close(self):
        if self.sock is not None:
            try:
                self.sock.close()
            except OSError:
                pass
        self.connected = False
        self.connecting = False


class Sockets:
    def __init__(self):
        self.server = None
        self.clients = {}
        self.next_id = 0

    def run(self):
        for c in list(self.clients.values()):
            c.run()

    def _add(self, client):
        if len(self.clients) >= 256:
            client.close()
            return None
        while self.next_id in self.clients:
            self.next_id = (self.next_id + 1) % 256
        self.clients[self.next_id] = client
        return self.next_id

    def connect(self, host, port, use_ssl=False):
        c = Client()
        if use_ssl:
            c.connect_ssl(host, port)
        else:
            c.connect(host, port)
        return self._add(c)

    def listen(self, address, port):
        if self.server is not None:
            self.server.close()
            self.server = None
        try:
            self.server = listen(address, port, backlog=1)
            return True
        except OSError as e:
            log.warning('listen on %s:%d failed: %s', address, port, e)
            return False

    def accept(self):
        if self.server is None:
            return None
        rd, _, _ = select.select([self.server], [], [], 0)
        if not rd:
            return None
        try:
            sock, _ = self.server.accept()
        except OSError:
            return None
        return self._add(Client(sock))

    def get(self, cid):
        return self.clients.get(cid)

    def close(self, cid):
        c = self.clients.pop(cid, None)
        if c is not None:
            c.close()


def host_port(data):
    return data[2:].decode('ascii', errors='replace'), (data[0] << 8) | data[1]


class ListenCommand:
    def __init__(self, s):
        self.s = s

    def run(self, data):
        host, port = host_port(data)
        return b'\x01' if self.s.listen(host, port) else b'\x00'


class ConnectCommand:
    def __init__(self, s, use_ssl=False):
        self.s = s
        self.use_ssl = use_ssl

    def run(self, data):
        host, port = host_port(data)
        cid = self.s.connect(host, port, self.use_ssl)
        return b'' if cid is None else bytes((cid,))


class AcceptCommand:
    def __init__(self, s):
        self.s = s

    def run(self, data):
        cid = self.s.accept()
        return b'' if cid is None else bytes((cid,))


class WriteCommand:
    def __init__(self, s):
        self.s = s

    def run(self, data):
        c = self.s.get(data[0])
        if c is not None:
            c.send(data[1:])
        return b''


class WriteToAllCommand:
    def __init__(self, s):
        self.s = s

    def run(self, data):
        for c in self.s.clients.values():
            c.send(data)
        return b''


class ReadCommand:
    def __init__(self, s):
        self.s = s

    def run(self, data):
        c = self.s.get(data[0])
        return b'' if c is None else c.recv(data[1])


class ConnectedCommand:
    def __init__(self, s):
        self.s = s

    def run(self, data):
        c = self.s.get(data[0])
        return b'\x01' if c is not None and c.connected else b'\x00'


class ConnectingCommand:
    def __init__(self, s):
        self.s = s

    def run(self, data):
        c = self.s.get(data[0])
        return b'\x01' if c is not None and c.connecting else b'\x00'


class CloseCommand:
    def __init__(self, s):
        self.s = s

    def run(self, data):
        self.s.close(data[0])
        return b''


def init(cp):
    s = Sockets()
    cp.register(b'N', ListenCommand(s))
    cp.register(b'k', AcceptCommand(s))
    cp.register(b'K', ReadCommand(s))
    cp.register(b'l', WriteCommand(s))
    cp.register(b'L', ConnectedCommand(s))
    cp.register(b'j', CloseCommand(s))
    cp.register(b'c', ConnectingCommand(s))
    cp.register(b'C', ConnectCommand(s))
    cp.register(b'Z', ConnectCommand(s, use_ssl=True))
    cp.register(b'b', WriteToAllCommand(s))
    cp.register_runner(s)
    return s
