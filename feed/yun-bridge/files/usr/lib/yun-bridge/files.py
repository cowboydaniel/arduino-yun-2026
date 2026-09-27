# SPDX-License-Identifier: GPL-2.0-or-later
#
# Python 3 port of YunBridge, originally Copyright 2013 Arduino LLC.
#
# FileIO: the sketch's FileSystem and File objects.

import errno
import os

EBADF = errno.EBADF


def err_byte(e):
    """An OSError as the one-byte error code the sketch expects."""
    return (e.errno or 255) & 0xFF


def u32(n):
    return bytes(((n >> 24) & 0xFF, (n >> 16) & 0xFF, (n >> 8) & 0xFF, n & 0xFF))


class Files:
    def __init__(self):
        self.files = {}
        self.next_id = 0

    def _new_id(self):
        if len(self.files) >= 256:
            return None
        while self.next_id in self.files:
            self.next_id = (self.next_id + 1) % 256
        return self.next_id

    def open(self, filename, mode):
        fid = self._new_id()
        if fid is None:
            return errno.EMFILE, None
        try:
            self.files[fid] = open(filename, mode)
        except OSError as e:
            return err_byte(e), None
        return 0, fid

    def close(self, fid):
        f = self.files.pop(fid, None)
        if f is not None:
            try:
                f.close()
            except OSError:
                pass

    def get(self, fid):
        return self.files.get(fid)


class OpenCommand:
    def __init__(self, files):
        self.files = files

    def run(self, data):
        mode = chr(data[0])
        if mode not in 'rwa':
            return bytes((errno.EINVAL, 0))
        name = os.fsdecode(data[1:])
        err, fid = self.files.open(name, mode + 'b')
        if err:
            return bytes((err, 0))
        return bytes((0, fid))


class CloseCommand:
    def __init__(self, files):
        self.files = files

    def run(self, data):
        self.files.close(data[0])
        return b'\x00'


class ReadCommand:
    def __init__(self, files):
        self.files = files

    def run(self, data):
        f = self.files.get(data[0])
        if f is None:
            return bytes((EBADF,))
        try:
            return b'\x00' + f.read(data[1])
        except OSError as e:
            return bytes((err_byte(e),))


class WriteCommand:
    def __init__(self, files):
        self.files = files

    def run(self, data):
        f = self.files.get(data[0])
        if f is None:
            return bytes((EBADF,))
        try:
            f.write(data[1:])
            f.flush()
        except OSError as e:
            return bytes((err_byte(e),))
        return b'\x00'


class SeekCommand:
    def __init__(self, files):
        self.files = files

    def run(self, data):
        f = self.files.get(data[0])
        if f is None:
            return bytes((EBADF,))
        pos = int.from_bytes(data[1:5], 'big')
        try:
            f.seek(pos)
        except OSError as e:
            return bytes((err_byte(e),))
        return b'\x00'


class TellCommand:
    def __init__(self, files):
        self.files = files

    def run(self, data):
        f = self.files.get(data[0])
        if f is None:
            return b'\xff' + u32(0)
        try:
            return b'\x00' + u32(f.tell())
        except OSError as e:
            return bytes((err_byte(e),)) + u32(0)


class SizeCommand:
    def __init__(self, files):
        self.files = files

    def run(self, data):
        f = self.files.get(data[0])
        if f is None:
            return b'\xff' + u32(0)
        try:
            return b'\x00' + u32(os.fstat(f.fileno()).st_size)
        except OSError as e:
            return bytes((err_byte(e),)) + u32(0)


class IsDirectoryCommand:
    def run(self, data):
        return b'\x01' if os.path.isdir(os.fsdecode(data)) else b'\x00'


def init(cp):
    files = Files()
    cp.register(b'F', OpenCommand(files))
    cp.register(b'f', CloseCommand(files))
    cp.register(b'G', ReadCommand(files))
    cp.register(b'g', WriteCommand(files))
    cp.register(b'i', IsDirectoryCommand())
    cp.register(b's', SeekCommand(files))
    cp.register(b'S', TellCommand(files))
    cp.register(b't', SizeCommand(files))
    return files
