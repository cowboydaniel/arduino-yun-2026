# SPDX-License-Identifier: GPL-2.0-or-later
#
# Python 3 port of YunBridge, originally Copyright 2013 Arduino LLC.
#
# Small non-blocking TCP servers used by the console (port 6571) and the
# mailbox / datastore JSON interface (port 5700).

import json
import logging
import select
import socket

log = logging.getLogger('bridge')

# Clients that don't read what we send them are dropped past this.
MAX_SENDBUF = 8192


def listen(address, port, backlog=5, timeout=30):
    """Bind a listening socket, retrying while an old bridge still holds it."""
    import time
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    deadline = time.monotonic() + timeout
    while True:
        try:
            sock.bind((address, port))
            break
        except OSError:
            if time.monotonic() >= deadline:
                sock.close()
                raise
            time.sleep(1)
    sock.listen(backlog)
    sock.setblocking(False)
    return sock


class TCPServer:
    def __init__(self, address, port, backlog=5):
        self.server = listen(address, port, backlog)
        self.clients = []
        self.sendbuf = {}
        self.recvbuf = {}

    def paused(self):
        """Return True to stop reading from clients for now."""
        return False

    def run(self):
        readers = [self.server] + ([] if self.paused() else self.clients)
        rd, _, _ = select.select(readers, [], [], 0)
        if self.server in rd:
            self.accept()
            rd.remove(self.server)
        for client in rd:
            self.receive(client)

        pending = [c for c in self.clients if self.sendbuf[c]]
        if pending:
            _, wr, _ = select.select([], pending, [], 0)
            for client in wr:
                try:
                    sent = client.send(self.sendbuf[client])
                    del self.sendbuf[client][:sent]
                except OSError:
                    self.close(client)

        for client in list(self.clients):
            if len(self.sendbuf[client]) > MAX_SENDBUF:
                log.info('dropping a client that stopped reading')
                self.close(client)

    def accept(self):
        try:
            client, _ = self.server.accept()
        except OSError:
            return
        client.setblocking(False)
        self.clients.append(client)
        self.sendbuf[client] = bytearray()
        self.recvbuf[client] = b''

    def receive(self, client):
        try:
            chunk = client.recv(4096)
        except OSError:
            self.close(client)
            return
        if not chunk:
            self.close(client)
            return
        rest = self.on_data(client, self.recvbuf[client] + chunk)
        if client in self.clients:
            self.recvbuf[client] = rest

    def on_data(self, client, data):
        """Handle received data; return whatever is left unconsumed."""
        return b''

    def send_all(self, data):
        for c in self.clients:
            self.sendbuf[c] += data

    def close(self, client):
        try:
            client.close()
        except OSError:
            pass
        if client in self.clients:
            self.clients.remove(client)
        self.sendbuf.pop(client, None)
        self.recvbuf.pop(client, None)


class JSONServer(TCPServer):
    """Receives a stream of concatenated JSON values from each client."""

    def __init__(self, address, port):
        super().__init__(address, port)
        self.queue = []
        self.decoder = json.JSONDecoder()

    def on_data(self, client, data):
        text = data.decode('utf-8', errors='replace')
        pos = 0
        while True:
            while pos < len(text) and text[pos].isspace():
                pos += 1
            if pos >= len(text):
                return b''
            try:
                obj, pos = self.decoder.raw_decode(text, pos)
            except json.JSONDecodeError:
                # Incomplete value: keep it for the next chunk. Give up on
                # clients that send more than 64 KB of something that isn't
                # JSON.
                rest = text[pos:].encode('utf-8')
                if len(rest) > 65536:
                    log.warning('dropping a client sending invalid JSON')
                    self.close(client)
                    return b''
                return rest
            self.queue.append(obj)

    def available(self):
        return len(self.queue) > 0

    def read(self):
        return self.queue.pop(0) if self.queue else None

    def write(self, obj):
        self.send_all(json.dumps(obj).encode('utf-8'))
