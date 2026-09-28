# SPDX-License-Identifier: GPL-2.0-or-later
#
# The little the bridge needs from Python's logging module: messages to
# stderr and the system log. The logging module (and argparse, and
# logging.handlers, which the bridge used to load) cost well over a
# megabyte of the Yun's RAM for as long as a sketch uses the Bridge.

import socket
import sys
import time

DEBUG, INFO, WARNING, ERROR = 10, 20, 30, 40
_SEVERITY = {DEBUG: 7, INFO: 6, WARNING: 4, ERROR: 3}
_NAMES = {DEBUG: 'DEBUG', INFO: 'INFO', WARNING: 'WARNING', ERROR: 'ERROR'}

_level = INFO
_stream = sys.stderr
_syslog = None


def setup(debug=False, stream=None, syslog_path='/dev/log'):
    """Log DEBUG and up if debug, else INFO and up; also to syslog_path."""
    global _level, _stream, _syslog
    _level = DEBUG if debug else INFO
    if stream is not None:
        _stream = stream
    if _syslog is not None:
        _syslog.close()
        _syslog = None
    if syslog_path:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        try:
            s.connect(syslog_path)
            _syslog = s
        except OSError:
            s.close()


class Logger:
    def _log(self, level, msg, args, exc=False):
        if level < _level:
            return
        if args:
            try:
                msg = msg % args
            except (TypeError, ValueError):
                msg = '%s %r' % (msg, args)
        if exc:
            import traceback
            msg = '%s\n%s' % (msg, traceback.format_exc().rstrip())
        try:
            _stream.write('%s %s %s\n' % (time.strftime('%Y-%m-%d %H:%M:%S'), _NAMES[level], msg))
            _stream.flush()
        except (OSError, ValueError):
            pass
        if _syslog is not None:
            try:
                # Facility "user" (1).
                _syslog.send(('<%d>bridge: %s' % (8 + _SEVERITY[level], msg)).encode('utf-8', 'replace'))
            except OSError:
                pass

    def debug(self, msg, *args):
        self._log(DEBUG, msg, args)

    def info(self, msg, *args):
        self._log(INFO, msg, args)

    def warning(self, msg, *args):
        self._log(WARNING, msg, args)

    def error(self, msg, *args):
        self._log(ERROR, msg, args)

    def exception(self, msg, *args):
        self._log(ERROR, msg, args, exc=True)


_logger = Logger()


def getLogger(name=None):
    return _logger
