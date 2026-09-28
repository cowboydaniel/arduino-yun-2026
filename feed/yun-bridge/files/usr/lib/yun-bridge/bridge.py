#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
#
# Python 3 port of YunBridge, originally Copyright 2013 Arduino LLC.
#
# The Linux side of the Arduino Bridge library. Bridge.begin() on the
# ATmega32U4 types "run-bridge" into the login shell on ttyATH0, which starts
# this program with the serial port as stdin and stdout.

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bridgelog  # noqa: E402
import packet  # noqa: E402

log = bridgelog.getLogger()

USAGE = '''usage: bridge.py [--debug] [--mailbox-port PORT] [--console-port PORT]

The Linux side of the Arduino Bridge library.

  --debug               log every command
  --mailbox-port PORT   (default 5700)
  --console-port PORT   (default 6571)'''


class CommandProcessor:
    def __init__(self):
        self.commands = {}
        self.runners = []
        self.finished = False

    def register_runner(self, runner):
        self.runners.append(runner)

    def register(self, key, command):
        self.commands[key] = command

    def run(self):
        for runner in self.runners:
            try:
                runner.run()
            except Exception:
                log.exception('runner %r failed', runner)

    def process(self, data):
        if data == b'XXXXX':
            log.info('MCU asked the bridge to quit')
            self.finished = True
            return b''

        if not data:
            return b''

        cmd = self.commands.get(data[0:1])
        if cmd is None:
            log.warning('unknown command %r', data[0:1])
            return b''
        try:
            return cmd.run(data[1:])
        except Exception:
            # The original bridge crashed here, which hangs the sketch until
            # the next Bridge.begin(). Log it and keep going instead.
            log.exception('command %r failed', data[0:1])
            return b''


def build_processor(mailbox_port=5700, console_port=6571):
    import console
    import files
    import mailbox
    import processes
    import sockets
    import sockets_udp

    cp = CommandProcessor()
    processes.init(cp)
    console.init(cp, port=console_port)
    mailbox.init(cp, port=mailbox_port)
    files.init(cp)
    sockets.init(cp)
    sockets_udp.init(cp)
    return cp


def setup_logging(debug):
    bridgelog.setup(debug)


def parse_args(argv):
    """(debug, mailbox_port, console_port), or exit with the usage."""
    debug, ports = False, {'--mailbox-port': 5700, '--console-port': 6571}
    argv = list(argv)
    while argv:
        a = argv.pop(0)
        name, _, value = a.partition('=')
        if a == '--debug':
            debug = True
        elif a in ('-h', '--help'):
            print(USAGE)
            sys.exit(0)
        elif name in ports:
            if not value:
                value = argv.pop(0) if argv else ''
            try:
                ports[name] = int(value)
            except ValueError:
                sys.exit('%s\nbridge.py: %s needs a port number' % (USAGE, name))
        else:
            sys.exit('%s\nbridge.py: unknown argument %s' % (USAGE, a))
    return debug, ports['--mailbox-port'], ports['--console-port']


def main(argv=None):
    debug, mailbox_port, console_port = parse_args(sys.argv[1:] if argv is None else argv)

    setup_logging(debug)
    cp = build_processor(mailbox_port, console_port)
    reader = packet.PacketReader(cp)
    log.info('bridge started')
    with packet.raw_tty(0):
        try:
            while reader.process() is not False:
                pass
        except EOFError:
            log.info('serial port closed')
    packet.run_hook('bridge-stopped')
    return 0


if __name__ == '__main__':
    sys.exit(main())
