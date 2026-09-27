# SPDX-License-Identifier: GPL-2.0-or-later
#
# Python 3 port of YunBridge, originally Copyright 2013 Arduino LLC.
#
# Process: runs Linux commands for the sketch (Process.run(),
# Process.runShellCommand() and friends).

import os
import subprocess

# Output kept for the sketch per process. Past this we stop reading, and the
# process blocks on its own writes until the sketch reads some (so streaming
# a big file through Process works). Process.run() only reads after the
# process has exited, so a command run that way can print up to this much
# (plus a 64 KB pipe). The original bridge stopped at about 64 KB.
MAX_BUFFERED = 256 * 1024


class Proc:
    def __init__(self, args):
        self.popen = subprocess.Popen(
            args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            # stderr used to be a pipe nobody read, which could hang the
            # process once it filled up.
            stderr=subprocess.DEVNULL, close_fds=True)
        os.set_blocking(self.popen.stdout.fileno(), False)
        self.out = bytearray()
        self.eof = False

    def pump(self):
        if self.eof or len(self.out) >= MAX_BUFFERED:
            return
        try:
            chunk = os.read(self.popen.stdout.fileno(), MAX_BUFFERED - len(self.out))
        except BlockingIOError:
            return
        except OSError:
            chunk = b''
        if chunk:
            self.out += chunk
        else:
            self.eof = True

    def kill(self):
        if self.popen.poll() is None:
            try:
                self.popen.kill()
            except OSError:
                pass
        try:
            self.popen.wait(timeout=1)
        except subprocess.TimeoutExpired:
            pass
        for f in (self.popen.stdin, self.popen.stdout):
            try:
                f.close()
            except OSError:
                pass


class Processes:
    def __init__(self):
        self.procs = {}
        self.next_id = 0

    def run(self):
        for p in self.procs.values():
            p.pump()

    def create(self, args):
        if len(self.procs) >= 256:
            return None
        try:
            proc = Proc(args)
        except (OSError, ValueError):
            return None
        while self.next_id in self.procs:
            self.next_id = (self.next_id + 1) % 256
        self.procs[self.next_id] = proc
        return self.next_id

    def get(self, pid):
        return self.procs.get(pid)

    def clean(self, pid):
        p = self.procs.pop(pid, None)
        if p is not None:
            p.kill()


def returncode_bytes(rc):
    rc &= 0xFFFF
    return bytes(((rc >> 8) & 0xFF, rc & 0xFF))


class RunCommand:
    def __init__(self, procs):
        self.procs = procs

    def run(self, data):
        args = [os.fsdecode(a) for a in data.split(b'\xFE')]
        pid = self.procs.create(args)
        if pid is None:
            return b'\x01\x00'
        return bytes((0, pid))


class RunningCommand:
    def __init__(self, procs):
        self.procs = procs

    def run(self, data):
        p = self.procs.get(data[0])
        return b'\x01' if p is not None and p.popen.poll() is None else b'\x00'


class WaitCommand:
    def __init__(self, procs):
        self.procs = procs

    def run(self, data):
        p = self.procs.get(data[0])
        if p is None:
            return b'\x00\x00'
        # Keep draining output while waiting, so a chatty process can't
        # deadlock against a full pipe.
        while True:
            try:
                return returncode_bytes(p.popen.wait(timeout=0.05))
            except subprocess.TimeoutExpired:
                p.pump()


class CleanUpCommand:
    def __init__(self, procs):
        self.procs = procs

    def run(self, data):
        self.procs.clean(data[0])
        return b''


class ReadOutputCommand:
    def __init__(self, procs):
        self.procs = procs

    def run(self, data):
        p = self.procs.get(data[0])
        if p is None:
            return b''
        p.pump()
        res = bytes(p.out[:data[1]])
        del p.out[:data[1]]
        return res


class AvailableOutputCommand:
    def __init__(self, procs):
        self.procs = procs

    def run(self, data):
        p = self.procs.get(data[0])
        if p is None:
            return b'\x00'
        p.pump()
        return bytes((min(len(p.out), 255),))


class WriteInputCommand:
    def __init__(self, procs):
        self.procs = procs

    def run(self, data):
        p = self.procs.get(data[0])
        if p is not None:
            try:
                p.popen.stdin.write(data[1:])
                p.popen.stdin.flush()
            except (OSError, ValueError):
                pass
        return b''


def init(cp):
    procs = Processes()
    cp.register(b'R', RunCommand(procs))
    cp.register(b'r', RunningCommand(procs))
    cp.register(b'W', WaitCommand(procs))
    cp.register(b'w', CleanUpCommand(procs))
    cp.register(b'O', ReadOutputCommand(procs))
    cp.register(b'o', AvailableOutputCommand(procs))
    cp.register(b'I', WriteInputCommand(procs))
    cp.register_runner(procs)
    return procs
