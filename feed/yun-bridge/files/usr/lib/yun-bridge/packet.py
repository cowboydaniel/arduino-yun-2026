# SPDX-License-Identifier: GPL-2.0-or-later
#
# Python 3 port of YunBridge, originally Copyright 2013 Arduino LLC.
#
# The serial packet format used by the Bridge library on the ATmega32U4:
#
#   0xFF | index | len_hi | len_lo | payload (len bytes) | crc_hi | crc_lo
#
# The CRC is CRC-CCITT (avr-libc _crc_ccitt_update) over every byte from the
# 0xFF start byte to the end of the payload, starting from 0xFFFF.

import logging
import os
import select
import subprocess
import termios
import tty
from contextlib import contextmanager

log = logging.getLogger('bridge')

START = 0xFF
BRIDGE_VERSION = b'161'

# How long to wait for each byte of a packet, as in the original bridge.
BYTE_TIMEOUT = 0.050


def crc_update(crc, byte):
    byte ^= crc & 0xFF
    byte ^= (byte << 4) & 0xFF
    return (((byte << 8) | (crc >> 8)) ^ (byte >> 4) ^ (byte << 3)) & 0xFFFF


def crc(data, crc=0xFFFF):
    for b in data:
        crc = crc_update(crc, b)
    return crc


def encode(index, payload):
    head = bytes((START, index & 0xFF, (len(payload) >> 8) & 0xFF, len(payload) & 0xFF))
    c = crc(head + payload)
    return head + payload + bytes((c >> 8, c & 0xFF))


@contextmanager
def raw_tty(fd):
    """Put the serial console into raw mode (no echo, no line editing)."""
    old = None
    if os.isatty(fd):
        old = termios.tcgetattr(fd)
        tty.setraw(fd)
    try:
        yield
    finally:
        if old is not None:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)


def run_hook(name):
    """Run an optional notification script such as /usr/bin/bridge-started."""
    path = '/usr/bin/' + name
    if os.access(path, os.X_OK):
        try:
            subprocess.call([path])
        except OSError as e:
            log.warning('%s failed: %s', path, e)


class ResetCommand:
    """'X': handshake sent by Bridge.begin(), 'XX100' -> version."""

    def run(self, data):
        if data[0:1] != b'X':
            run_hook('bridge-error')
            return b'\x01'
        if data[1:4] != b'100':
            run_hook('bridge-error')
            return b'\x02'
        run_hook('bridge-started')
        return b'\x00' + BRIDGE_VERSION


class PacketReader:
    def __init__(self, processor, infd=0, outfd=1):
        self.index = 999
        self.last_response = None
        self.processor = processor
        self.infd = infd
        self.outfd = outfd
        self.buf = bytearray()
        processor.register(b'X', ResetCommand())

    def read_byte(self, timeout=BYTE_TIMEOUT):
        if not self.buf:
            r, _, _ = select.select([self.infd], [], [], timeout)
            if not r:
                return None
            chunk = os.read(self.infd, 4096)
            if not chunk:
                raise EOFError('serial port closed')
            self.buf += chunk
        b = self.buf[0]
        del self.buf[0]
        return b

    def send(self, index, payload):
        data = encode(index, payload)
        while data:
            n = os.write(self.outfd, data)
            data = data[n:]

    def read_packet(self):
        """Return (index, payload), or None on a timeout or bad CRC."""
        while True:
            b = self.read_byte()
            if b is None:
                return None
            if b == START:
                break
        head = [START]
        for _ in range(3):
            b = self.read_byte()
            if b is None:
                return None
            head.append(b)
        length = (head[2] << 8) | head[3]
        payload = bytearray()
        for _ in range(length):
            b = self.read_byte()
            if b is None:
                return None
            payload.append(b)
        hi = self.read_byte()
        if hi is None:
            return None
        lo = self.read_byte()
        if lo is None:
            return None
        if crc(bytes(head) + payload) != (hi << 8) | lo:
            log.debug('dropping packet with bad CRC')
            return None
        return head[1], bytes(payload)

    def process(self):
        """Handle one packet. Returns False once the MCU has asked us to quit."""
        if self.processor.finished:
            return False

        self.processor.run()

        pkt = self.read_packet()
        if pkt is None:
            return None
        index, data = pkt

        # A reset ('XX...') starts a new sequence of indexes.
        if len(data) == 5 and data[0:2] == b'XX':
            self.index = index

        # A repeated packet: the MCU didn't get our answer, so send it again.
        if self.index != index:
            if self.last_response is not None:
                self.send(index, self.last_response)
            return True

        result = self.processor.process(data)
        self.send(self.index, result)
        self.index = (self.index + 1) & 0xFF
        self.last_response = result
        return True
