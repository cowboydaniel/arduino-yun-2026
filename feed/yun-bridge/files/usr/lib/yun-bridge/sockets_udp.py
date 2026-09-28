# SPDX-License-Identifier: GPL-2.0-or-later
#
# Python 3 port of YunBridge, originally Copyright 2013 Arduino LLC.
#
# UDP sockets for the sketch (BridgeUDP).

import bridgelog
import select
import socket

log = bridgelog.getLogger()

MAX_QUEUED = 32


class UDPSocket:
    def __init__(self, address, port):
        self.rx = []            # [(data, (addr, port))]
        self.tx = []
        self.cur_rx = None
        self.cur_rx_addr = None
        self.cur_tx = None
        self.cur_tx_addr = None
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.setblocking(False)
        self.opened = False
        try:
            self.sock.bind((address, port))
            self.opened = True
        except OSError as e:
            log.info('UDP bind to %s:%d failed: %s', address, port, e)

    def run(self):
        if not self.opened:
            return
        rd, wr, _ = select.select([self.sock], [self.sock] if self.tx else [], [], 0)
        if rd:
            try:
                data, addr = self.sock.recvfrom(65536)
            except BlockingIOError:
                pass
            except OSError:
                self.close()
                return
            else:
                if len(self.rx) < MAX_QUEUED:
                    self.rx.append((data, addr))
        if wr and self.tx:
            data, addr = self.tx.pop(0)
            try:
                self.sock.sendto(data, addr)
            except OSError as e:
                log.info('UDP send to %s failed: %s', addr, e)

    def recv_next(self):
        if not self.rx:
            return None
        self.cur_rx, self.cur_rx_addr = self.rx.pop(0)
        return len(self.cur_rx)

    def recv(self, maxlen):
        if self.cur_rx is None:
            return None
        res, self.cur_rx = self.cur_rx[:maxlen], self.cur_rx[maxlen:]
        return res

    def available(self):
        return None if self.cur_rx is None else len(self.cur_rx)

    def send_start(self, address, port):
        self.cur_tx = bytearray()
        self.cur_tx_addr = (address, port)

    def send(self, data):
        if self.cur_tx is None:
            return False
        self.cur_tx += data
        return True

    def send_end(self):
        if self.cur_tx is None:
            return False
        self.tx.append((bytes(self.cur_tx), self.cur_tx_addr))
        self.cur_tx = self.cur_tx_addr = None
        return True

    def set_broadcast(self, on):
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1 if on else 0)

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass
        self.opened = False


class UDPSockets:
    def __init__(self):
        self.sockets = {}
        self.next_id = 0

    def run(self):
        for s in list(self.sockets.values()):
            s.run()

    def create(self, address, port):
        if len(self.sockets) >= 256:
            return None
        s = UDPSocket(address, port)
        while self.next_id in self.sockets:
            self.next_id = (self.next_id + 1) % 256
        self.sockets[self.next_id] = s
        return self.next_id

    def get(self, sid):
        return self.sockets.get(sid)

    def close(self, sid):
        s = self.sockets.pop(sid, None)
        if s is not None:
            s.close()


def len3(n):
    return bytes((1, (n >> 8) & 0xFF, n & 0xFF))


class CreateCommand:
    def __init__(self, u):
        self.u = u

    def run(self, data):
        port = (data[0] << 8) | data[1]
        sid = self.u.create(data[2:].decode('ascii', errors='replace'), port)
        return b'\x01\x00' if sid is None else bytes((0, sid))


class CloseCommand:
    def __init__(self, u):
        self.u = u

    def run(self, data):
        # The original called an undefined "server" here and crashed.
        self.u.close(data[0])
        return b''


class WriteBeginCommand:
    def __init__(self, u, broadcast=False):
        self.u = u
        self.broadcast = broadcast

    def run(self, data):
        s = self.u.get(data[0])
        if s is None:
            return b'\x00'
        port = (data[1] << 8) | data[2]
        s.set_broadcast(self.broadcast)
        host = '<broadcast>' if self.broadcast else data[3:].decode('ascii', errors='replace')
        s.send_start(host, port)
        return b'\x01'


class WriteCommand:
    def __init__(self, u):
        self.u = u

    def run(self, data):
        s = self.u.get(data[0])
        return b'\x01' if s is not None and s.send(data[1:]) else b'\x00'


class WriteEndCommand:
    def __init__(self, u):
        self.u = u

    def run(self, data):
        s = self.u.get(data[0])
        return b'\x01' if s is not None and s.send_end() else b'\x00'


class RecvBeginCommand:
    def __init__(self, u):
        self.u = u

    def run(self, data):
        s = self.u.get(data[0])
        n = None if s is None else s.recv_next()
        return b'\x00\x00\x00' if n is None else len3(n)


class RecvCommand:
    def __init__(self, u):
        self.u = u

    def run(self, data):
        s = self.u.get(data[0])
        if s is None:
            return b'\x00'
        res = s.recv(data[1])
        return b'' if res is None else res


class AvailableCommand:
    def __init__(self, u):
        self.u = u

    def run(self, data):
        s = self.u.get(data[0])
        n = None if s is None else s.available()
        return b'\x00\x00\x00' if n is None else len3(n)


class RemoteIPCommand:
    def __init__(self, u):
        self.u = u

    def run(self, data):
        s = self.u.get(data[0])
        if s is None:
            return b''
        if not s.cur_rx_addr:
            return b'\x00'
        addr, port = s.cur_rx_addr
        return b'\x01' + socket.inet_aton(addr) + bytes(((port >> 8) & 0xFF, port & 0xFF))


def init(cp):
    u = UDPSockets()
    cp.register(b'e', CreateCommand(u))
    cp.register(b'v', WriteBeginCommand(u, broadcast=True))
    cp.register(b'E', WriteBeginCommand(u))
    cp.register(b'h', WriteCommand(u))
    cp.register(b'H', WriteEndCommand(u))
    cp.register(b'q', CloseCommand(u))
    cp.register(b'Q', RecvBeginCommand(u))
    cp.register(b'u', RecvCommand(u))
    cp.register(b'U', AvailableCommand(u))
    cp.register(b'T', RemoteIPCommand(u))
    cp.register_runner(u)
    return u
