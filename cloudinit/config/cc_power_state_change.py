# vi: ts=4 expandtab
#
#    Copyright (C) 2011 Canonical Ltd.
#    Copyright (C) 2014 Amazon.com, Inc. or its affiliates.
#
#    Author: Scott Moser <scott.moser@canonical.com>
#    Author: Andrew Jorgensen <ajorgens@amazon.com>
#
#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU General Public License version 3, as
#    published by the Free Software Foundation.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU General Public License for more details.
#
#    You should have received a copy of the GNU General Public License
#    along with this program.  If not, see <http://www.gnu.org/licenses/>.

from cloudinit.settings import PER_INSTANCE
from cloudinit import util

import errno
import os
import re
import subprocess
import time

frequency = PER_INSTANCE

EXIT_FAIL = 254


def handle(_name, cfg, _cloud, log, _args):

    try:
        (args, timeout) = load_power_state(cfg, log)
        if args is None:
            log.debug("no power_state provided. doing nothing")
            return
    except Exception:
        log.warning("Not performing power state change!", exc_info=True)
        return

    mypid = os.getpid()
    cmdline = util.load_file("/proc/%s/cmdline" % mypid)

    if not cmdline:
        log.warn("power_state: failed to get cmdline of current process")
        return

    devnull_fp = open(os.devnull, "w")

    log.debug("After pid %s ends, will execute: %s" % (mypid, ' '.join(args)))

    util.fork_cb(run_after_pid_gone, mypid, cmdline, timeout, log, execmd,
                 [args, devnull_fp])


def load_power_state(cfg, log):
    # returns a tuple of shutdown_command, timeout
    # shutdown_command is None if no config found
    pstate = cfg.get('power_state')

    if pstate is None:
        return (None, None)

    if not isinstance(pstate, dict):
        raise TypeError("power_state is not a dict.")

    opt_map = {'halt': '-H', 'poweroff': '-P', 'reboot': '-r'}

    mode = util.get_cfg_option_str(pstate, 'mode')
    log.debug('mode: %s', mode)
    if mode not in opt_map:
        raise TypeError("power_state[mode] required, must be one of: %s." %
                        ','.join(opt_map.keys()))

    # Cast to a str so that there's no confusion below.
    delay = util.get_cfg_option_str(pstate, 'delay', 'now')
    log.debug('delay: %s', delay)
    # Valid values for the delay are now or +m, where m is a number of minutes
    # from now. now is an alias for +0. shutdown also accepts absolute time in
    # hh:mm, but this doesn't make sense in this context, where it is not known
    # what time it will be when the module is run.
    if delay != 'now':
        if not re.match(r'\+?[0-9]+', delay):
            raise TypeError(
                'power_state[delay] must be \'now\' or \'+m\' (minutes).')
        # the yaml parser consumes the + and makes it a positive integer, so we
        # try to be more forgiving here.
        if delay[0] != '+':
            delay = '+%s' % delay

    args = ["shutdown", opt_map[mode], delay]
    message = util.get_cfg_option_str(pstate, 'message')
    if message:
        args.append(message)
    log.debug('message: %s', message)

    try:
        timeout = float(pstate.get('timeout', 30.0))
    except ValueError:
        raise ValueError("failed to convert timeout '%s' to float." %
                         pstate['timeout'])

    return (args, timeout)


def doexit(sysexit):
    os._exit(sysexit)  # pylint: disable=W0212


def execmd(exe_args, output=None, data_in=None):
    try:
        proc = subprocess.Popen(exe_args, stdin=subprocess.PIPE,
                                stdout=output, stderr=subprocess.STDOUT)
        proc.communicate(data_in)
        ret = proc.returncode  # pylint: disable=E1101
    except Exception:
        doexit(EXIT_FAIL)
    doexit(ret)


def run_after_pid_gone(pid, pidcmdline, timeout, log, func, args):
    # wait until pid, with /proc/pid/cmdline contents of pidcmdline
    # is no longer alive.  After it is gone, or timeout has passed
    # execute func(args)
    msg = None
    end_time = time.time() + timeout

    cmdline_f = "/proc/%s/cmdline" % pid

    def fatal(msg):
        if log:
            log.warn(msg)
        doexit(EXIT_FAIL)

    known_errnos = (errno.ENOENT, errno.ESRCH)

    while True:
        if time.time() > end_time:
            msg = "timeout reached before %s ended" % pid
            break

        try:
            cmdline = ""
            with open(cmdline_f) as fp:
                cmdline = fp.read()
            if cmdline != pidcmdline:
                msg = "cmdline changed for %s [now: %s]" % (pid, cmdline)
                break

        except IOError as ioerr:
            if ioerr.errno in known_errnos:
                msg = "pidfile '%s' gone [%d]" % (cmdline_f, ioerr.errno)
            else:
                fatal("IOError during wait: %s" % ioerr)
            break

        except Exception as e:
            fatal("Unexpected Exception: %s" % e)

        time.sleep(.25)

    if not msg:
        fatal("Unexpected error in run_after_pid_gone")

    if log:
        log.debug(msg)
    func(*args)
