# SPDX-License-Identifier: GPL-2.0-or-later
#
# Python 3 port of YunBridge, originally Copyright 2013 Arduino LLC.
#
# Console: the sketch's Console object, reachable with
# "telnet localhost 6571" on the Yun (or through the Arduino IDE).

from tcpserver import TCPServer

# The sketch is not reading: stop taking input from the network past this.
MAX_RECVBUF = 1024


class Console(TCPServer):
    def __init__(self, port=6571):
        super().__init__('127.0.0.1', port, backlog=1)
        self.input = bytearray()

    def paused(self):
        # Stop reading from the network while the sketch is behind.
        return len(self.input) >= MAX_RECVBUF

    def on_data(self, client, data):
        self.input += data
        # Echo to the other clients, so they all see the same session.
        for c in self.clients:
            if c is not client:
                self.sendbuf[c] += data
        return b''

    def read(self, maxlen):
        res = bytes(self.input[:maxlen])
        del self.input[:maxlen]
        return res

    def is_connected(self):
        return len(self.clients) > 0


class WriteCommand:
    def __init__(self, console):
        self.console = console

    def run(self, data):
        self.console.send_all(data)
        return b''


class ReadCommand:
    def __init__(self, console):
        self.console = console

    def run(self, data):
        return self.console.read(data[0])


class ConnectedCommand:
    def __init__(self, console):
        self.console = console

    def run(self, data):
        return b'\x01' if self.console.is_connected() else b'\x00'


def init(cp, port=6571):
    console = Console(port)
    cp.register(b'P', WriteCommand(console))
    cp.register(b'p', ReadCommand(console))
    cp.register(b'a', ConnectedCommand(console))
    cp.register_runner(console)
    return console
