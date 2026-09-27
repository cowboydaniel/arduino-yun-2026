#!/usr/bin/env python3
# End-to-end tests for the Python 3 bridge. Each test starts bridge.py with
# pipes as its "serial port" and plays the part of the Bridge library on the
# ATmega32U4, packet by packet.

import os
import select
import socket
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
BRIDGE_DIR = os.path.join(HERE, '..', 'feed', 'yun-bridge', 'files', 'usr', 'lib', 'yun-bridge')
sys.path.insert(0, BRIDGE_DIR)

import packet  # noqa: E402
from bridgeclient import BridgeClient  # noqa: E402


def original_crc(data):
    """The CRC code from the original Python 2 YunBridge packet.py."""
    result = 0xFFFF
    for ch in data:
        crc = result & 0xFFFF
        d = ch & 0xFF
        d = d ^ (crc & 0xFF)
        tmp = (d << 4) & 0xFF
        d = d ^ tmp
        hi8 = (crc >> 8) & 0xFF
        result = (((d << 8) | hi8) ^ (d >> 4) ^ (d << 3)) & 0xFFFF
    return result


def free_port():
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    port = s.getsockname()[1]
    s.close()
    return port


class MCU:
    """The sketch side of the protocol."""

    def __init__(self):
        self.mailbox_port = free_port()
        self.console_port = free_port()
        self.proc = subprocess.Popen(
            [sys.executable, '-u', os.path.join(BRIDGE_DIR, 'bridge.py'),
             '--mailbox-port', str(self.mailbox_port),
             '--console-port', str(self.console_port)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.index = 0
        self.buf = b''

    def read_exact(self, n, timeout=5):
        deadline = time.monotonic() + timeout
        fd = self.proc.stdout.fileno()
        while len(self.buf) < n:
            left = deadline - time.monotonic()
            if left <= 0:
                raise TimeoutError('no answer from the bridge')
            r, _, _ = select.select([fd], [], [], left)
            if r:
                chunk = os.read(fd, 4096)
                if not chunk:
                    raise EOFError('bridge exited: ' + self.proc.stderr.read().decode())
                self.buf += chunk
        res, self.buf = self.buf[:n], self.buf[n:]
        return res

    def read_response(self):
        head = self.read_exact(4)
        assert head[0] == 0xFF, head
        n = (head[2] << 8) | head[3]
        payload = self.read_exact(n)
        c = self.read_exact(2)
        assert original_crc(head + payload) == (c[0] << 8) | c[1], 'bad CRC from bridge'
        return head[1], payload

    def send_raw(self, index, payload):
        self.proc.stdin.write(packet.encode(index, payload))
        self.proc.stdin.flush()

    def transfer(self, payload):
        self.send_raw(self.index, payload)
        index, res = self.read_response()
        assert index == self.index, (index, self.index)
        self.index = (self.index + 1) & 0xFF
        return res

    def begin(self):
        self.index = 0
        return self.transfer(b'XX100')

    def close(self):
        if self.proc.poll() is None:
            try:
                self.transfer(b'XXXXX')
            except (OSError, EOFError, TimeoutError, AssertionError):
                pass
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
        for f in (self.proc.stdin, self.proc.stdout, self.proc.stderr):
            try:
                f.close()
            except OSError:
                pass

    def wait_until(self, cmd, check, timeout=5):
        deadline = time.monotonic() + timeout
        while True:
            res = self.transfer(cmd)
            if check(res):
                return res
            if time.monotonic() > deadline:
                raise TimeoutError('%r never returned the expected answer (last %r)' % (cmd, res))
            time.sleep(0.02)


class CRCTest(unittest.TestCase):
    def test_matches_original(self):
        for data in (b'', b'\xff', b'\xff\x00\x00\x05XX100', bytes(range(256)) * 3):
            self.assertEqual(packet.crc(data), original_crc(data))


class BridgeTest(unittest.TestCase):
    def setUp(self):
        self.mcu = MCU()
        self.addCleanup(self.mcu.close)
        # Wait for the TCP servers to come up.
        deadline = time.monotonic() + 10
        while True:
            try:
                socket.create_connection(('127.0.0.1', self.mcu.mailbox_port), timeout=1).close()
                break
            except OSError:
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.05)

    def test_handshake(self):
        self.assertEqual(self.mcu.begin(), b'\x00161')

    def test_bad_handshake(self):
        self.mcu.index = 0
        self.assertEqual(self.mcu.transfer(b'XX999'), b'\x02')

    def test_repeated_packet_gets_same_answer(self):
        self.mcu.begin()
        first = self.mcu.transfer(b'a')
        self.mcu.send_raw((self.mcu.index - 1) & 0xFF, b'a')
        index, again = self.mcu.read_response()
        self.assertEqual(index, (self.mcu.index - 1) & 0xFF)
        self.assertEqual(first, again)

    def test_bad_crc_is_ignored(self):
        self.mcu.begin()
        bad = bytearray(packet.encode(self.mcu.index, b'a'))
        bad[-1] ^= 0xFF
        self.mcu.proc.stdin.write(bytes(bad))
        self.mcu.proc.stdin.flush()
        self.assertEqual(self.mcu.transfer(b'a'), b'\x00')

    def test_unknown_command_does_not_crash(self):
        self.mcu.begin()
        self.assertEqual(self.mcu.transfer(b'~'), b'')
        self.assertEqual(self.mcu.transfer(b'a'), b'\x00')

    def test_quit(self):
        self.mcu.begin()
        self.assertEqual(self.mcu.transfer(b'XXXXX'), b'')
        self.assertEqual(self.mcu.proc.wait(timeout=5), 0)

    def test_datastore(self):
        self.mcu.begin()
        self.assertEqual(self.mcu.transfer(b'Dkey\xfevalue'), b'\x01')
        self.assertEqual(self.mcu.transfer(b'dkey'), b'value')
        self.assertEqual(self.mcu.transfer(b'dmissing'), b'')
        self.assertEqual(self.mcu.transfer(b'Dno-separator'), b'\x00')

        client = BridgeClient(port=self.mcu.mailbox_port, timeout=5)
        results = {}

        # The bridge only serves the JSON socket between packets, so keep
        # talking to it from this thread while the client waits.
        import threading
        t = threading.Thread(target=lambda: results.update(
            get=client.get('key'), put=client.put('D13', '1'), all=client.getall()))
        t.start()
        while t.is_alive():
            self.mcu.transfer(b'a')
            time.sleep(0.01)
        self.assertEqual(results['get'], 'value')
        self.assertEqual(results['put'], '1')
        self.assertEqual(results['all'], {'key': 'value', 'D13': '1'})
        self.assertEqual(self.mcu.transfer(b'dD13'), b'1')

    def test_mailbox_from_linux(self):
        self.mcu.begin()
        client = BridgeClient(port=self.mcu.mailbox_port)
        client.mailbox('hello')
        res = self.mcu.wait_until(b'n', lambda r: r != b'\x00\x00')
        self.assertEqual(res, b'\x00\x05')
        self.assertEqual(self.mcu.transfer(b'm'), b'hello')
        self.assertEqual(self.mcu.transfer(b'n'), b'\x00\x00')

    def test_mailbox_to_linux(self):
        self.mcu.begin()
        conn = socket.create_connection(('127.0.0.1', self.mcu.mailbox_port))
        self.addCleanup(conn.close)
        conn.settimeout(0.05)
        self.mcu.transfer(b'a')   # let the bridge accept the connection
        self.mcu.transfer(b'Mhi there')
        data = b''
        deadline = time.monotonic() + 5
        while b'}' not in data and time.monotonic() < deadline:
            self.mcu.transfer(b'a')
            try:
                data += conn.recv(4096)
            except socket.timeout:
                pass
        import json
        self.assertEqual(json.loads(data), {'request': 'raw', 'data': 'hi there'})

    def test_process_output_and_exit_code(self):
        self.mcu.begin()
        res = self.mcu.transfer(b'R/bin/sh\xfe-c\xfeecho hello; exit 3')
        self.assertEqual(res[0], 0)
        pid = res[1:2]
        self.assertEqual(self.mcu.transfer(b'W' + pid), b'\x00\x03')
        self.assertEqual(self.mcu.transfer(b'o' + pid), b'\x06')
        self.assertEqual(self.mcu.transfer(b'O' + pid + b'\x03'), b'hel')
        self.assertEqual(self.mcu.transfer(b'O' + pid + b'\xff'), b'lo\n')
        self.assertEqual(self.mcu.transfer(b'r' + pid), b'\x00')
        self.mcu.transfer(b'w' + pid)
        self.assertEqual(self.mcu.transfer(b'o' + pid), b'\x00')

    def test_process_with_lots_of_output_finishes(self):
        # The original bridge only buffered 32 bytes, so a process writing
        # more than a pipe's worth never exited.
        self.mcu.begin()
        res = self.mcu.transfer(b'Rhead\xfe-c\xfe200000\xfe/dev/zero')
        pid = res[1:2]
        self.assertEqual(self.mcu.transfer(b'W' + pid), b'\x00\x00')
        self.assertEqual(self.mcu.transfer(b'o' + pid), b'\xff')

    def test_process_stdin(self):
        self.mcu.begin()
        pid = self.mcu.transfer(b'Rcat')[1:2]
        self.mcu.transfer(b'I' + pid + b'ping')
        self.mcu.wait_until(b'o' + pid, lambda r: r == b'\x04')
        self.assertEqual(self.mcu.transfer(b'O' + pid + b'\x10'), b'ping')
        self.mcu.transfer(b'w' + pid)

    def test_missing_program(self):
        self.mcu.begin()
        self.assertEqual(self.mcu.transfer(b'R/does/not/exist'), b'\x01\x00')

    def test_files(self):
        self.mcu.begin()
        with tempfile.TemporaryDirectory() as d:
            name = os.path.join(d, 'f.txt').encode()
            res = self.mcu.transfer(b'Fw' + name)
            self.assertEqual(res[0], 0)
            fid = res[1:2]
            self.assertEqual(self.mcu.transfer(b'g' + fid + b'hello world'), b'\x00')
            self.assertEqual(self.mcu.transfer(b't' + fid), b'\x00\x00\x00\x00\x0b')
            self.assertEqual(self.mcu.transfer(b'S' + fid), b'\x00\x00\x00\x00\x0b')
            self.assertEqual(self.mcu.transfer(b'f' + fid), b'\x00')

            fid = self.mcu.transfer(b'Fr' + name)[1:2]
            self.assertEqual(self.mcu.transfer(b's' + fid + b'\x00\x00\x00\x06'), b'\x00')
            self.assertEqual(self.mcu.transfer(b'G' + fid + b'\x10'), b'\x00world')
            self.mcu.transfer(b'f' + fid)

            self.assertEqual(self.mcu.transfer(b'i' + d.encode()), b'\x01')
            self.assertEqual(self.mcu.transfer(b'i' + name), b'\x00')
            missing = os.path.join(d, 'nope').encode()
            self.assertEqual(self.mcu.transfer(b'Fr' + missing), bytes((2, 0)))  # ENOENT
            # Bad file ids used to crash the original bridge.
            self.assertEqual(self.mcu.transfer(b'G\x63\x10'), b'\x09')
            self.assertEqual(self.mcu.transfer(b'g\x63data'), b'\x09')

    def test_tcp_server(self):
        self.mcu.begin()
        port = free_port()
        self.assertEqual(self.mcu.transfer(b'N' + bytes((port >> 8, port & 0xFF)) + b'127.0.0.1'), b'\x01')
        conn = socket.create_connection(('127.0.0.1', port))
        self.addCleanup(conn.close)
        cid = self.mcu.wait_until(b'k', lambda r: r != b'')
        conn.sendall(b'GET / HTTP/1.0\r\n')
        data = b''
        deadline = time.monotonic() + 5
        while len(data) < 16 and time.monotonic() < deadline:
            data += self.mcu.transfer(b'K' + cid + b'\x40')
        self.assertEqual(data, b'GET / HTTP/1.0\r\n')
        self.mcu.transfer(b'l' + cid + b'reply')
        self.mcu.transfer(b'a')
        conn.settimeout(5)
        self.assertEqual(conn.recv(100), b'reply')
        self.assertEqual(self.mcu.transfer(b'L' + cid), b'\x01')
        self.mcu.transfer(b'j' + cid)
        self.assertEqual(self.mcu.transfer(b'L' + cid), b'\x00')

    def test_tcp_client(self):
        self.mcu.begin()
        srv = socket.socket()
        srv.bind(('127.0.0.1', 0))
        srv.listen(1)
        self.addCleanup(srv.close)
        port = srv.getsockname()[1]
        cid = self.mcu.transfer(b'C' + bytes((port >> 8, port & 0xFF)) + b'localhost')
        self.assertEqual(len(cid), 1)
        conn, _ = srv.accept()
        self.addCleanup(conn.close)
        self.mcu.wait_until(b'L' + cid, lambda r: r == b'\x01')
        self.assertEqual(self.mcu.transfer(b'c' + cid), b'\x00')
        self.mcu.transfer(b'l' + cid + b'ping')
        self.mcu.transfer(b'a')
        conn.settimeout(5)
        self.assertEqual(conn.recv(10), b'ping')
        conn.sendall(b'pong')
        data = b''
        deadline = time.monotonic() + 5
        while data != b'pong' and time.monotonic() < deadline:
            data += self.mcu.transfer(b'K' + cid + b'\x10')
        self.assertEqual(data, b'pong')
        conn.close()
        self.mcu.wait_until(b'L' + cid, lambda r: r == b'\x00')

    def test_tcp_client_refused(self):
        self.mcu.begin()
        port = free_port()
        cid = self.mcu.transfer(b'C' + bytes((port >> 8, port & 0xFF)) + b'127.0.0.1')
        self.mcu.wait_until(b'c' + cid, lambda r: r == b'\x00')
        self.assertEqual(self.mcu.transfer(b'L' + cid), b'\x00')

    def test_udp(self):
        self.mcu.begin()
        port = free_port()
        res = self.mcu.transfer(b'e' + bytes((port >> 8, port & 0xFF)) + b'127.0.0.1')
        self.assertEqual(res[0], 0)
        sid = res[1:2]
        peer = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        peer.bind(('127.0.0.1', 0))
        self.addCleanup(peer.close)
        peer.sendto(b'datagram', ('127.0.0.1', port))
        res = self.mcu.wait_until(b'Q' + sid, lambda r: r[0] == 1)
        self.assertEqual(res, b'\x01\x00\x08')
        peer_port = peer.getsockname()[1]
        self.assertEqual(self.mcu.transfer(b'T' + sid),
                         b'\x01\x7f\x00\x00\x01' + bytes((peer_port >> 8, peer_port & 0xFF)))
        self.assertEqual(self.mcu.transfer(b'u' + sid + b'\x04'), b'data')
        self.assertEqual(self.mcu.transfer(b'U' + sid), b'\x01\x00\x04')

        pp = bytes((peer_port >> 8, peer_port & 0xFF))
        self.assertEqual(self.mcu.transfer(b'E' + sid + pp + b'127.0.0.1'), b'\x01')
        self.assertEqual(self.mcu.transfer(b'h' + sid + b'answer'), b'\x01')
        self.assertEqual(self.mcu.transfer(b'H' + sid), b'\x01')
        self.mcu.transfer(b'a')
        peer.settimeout(5)
        self.assertEqual(peer.recvfrom(100)[0], b'answer')
        # Closing a UDP socket crashed the original bridge.
        self.assertEqual(self.mcu.transfer(b'q' + sid), b'')
        self.assertEqual(self.mcu.transfer(b'a'), b'\x00')

    def test_console(self):
        self.mcu.begin()
        self.assertEqual(self.mcu.transfer(b'a'), b'\x00')
        conn = socket.create_connection(('127.0.0.1', self.mcu.console_port))
        self.addCleanup(conn.close)
        self.mcu.wait_until(b'a', lambda r: r == b'\x01')
        self.mcu.transfer(b'Phello from the sketch')
        self.mcu.transfer(b'a')
        conn.settimeout(5)
        self.assertEqual(conn.recv(100), b'hello from the sketch')
        conn.sendall(b'typed')
        res = self.mcu.wait_until(b'p\x03', lambda r: r != b'')
        self.assertEqual(res, b'typ')
        self.assertEqual(self.mcu.transfer(b'p\x10'), b'ed')


if __name__ == '__main__':
    unittest.main()
