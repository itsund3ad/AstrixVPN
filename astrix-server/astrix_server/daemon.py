# Astrix — by UNDEAD (https://github.com/itsund3ad)
# Daemon: fork, pidfile, signal handlers, graceful shutdown.
# Designed for systemd integration on Linux VPS.

import asyncio
import logging
import os
import signal
import sys
from typing import Optional, Callable, Awaitable

logger = logging.getLogger("daemon")


class Daemon:
    """Systemd-compatible daemon wrapper with PID file and signal handling."""

    def __init__(
        self,
        pidfile: str,
        *,
        foreground: bool = False,
        log_file: Optional[str] = None,
    ):
        self._pidfile = pidfile
        self._foreground = foreground
        self._log_file = log_file
        self._running = False
        self._shutdown_event = asyncio.Event()
        self._main_task: Optional[asyncio.Task] = None

    def start(
        self,
        main_coro_factory: Callable[..., Awaitable[None]],
        **factory_kwargs,
    ):
        if not self._foreground:
            self._daemonize()
        self._write_pidfile()
        self._setup_signal_handlers()
        try:
            asyncio.run(self._run_async(main_coro_factory, **factory_kwargs))
        except KeyboardInterrupt:
            pass
        finally:
            self._remove_pidfile()

    def _daemonize(self):
        try:
            pid = os.fork()
            if pid > 0:
                sys.exit(0)
        except OSError as e:
            logger.error("fork #1 failed: %s", e)
            sys.exit(1)
        os.setsid()
        os.umask(0)
        try:
            pid = os.fork()
            if pid > 0:
                sys.exit(0)
        except OSError as e:
            logger.error("fork #2 failed: %s", e)
            sys.exit(1)
        sys.stdout.flush()
        sys.stderr.flush()
        if self._log_file:
            log_fd = os.open(self._log_file, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
            os.dup2(log_fd, sys.stdout.fileno())
            os.dup2(log_fd, sys.stderr.fileno())
            os.close(log_fd)
        else:
            devnull = os.open(os.devnull, os.O_RDWR)
            os.dup2(devnull, sys.stdin.fileno())
            os.dup2(devnull, sys.stdout.fileno())
            os.dup2(devnull, sys.stderr.fileno())
            os.close(devnull)

    def _write_pidfile(self):
        pid = os.getpid()
        try:
            with open(self._pidfile, "w") as f:
                f.write(f"{pid}\n")
            logger.info("PID %d written to %s", pid, self._pidfile)
        except OSError as e:
            logger.error("cannot write pidfile %s: %s", self._pidfile, e)

    def _remove_pidfile(self):
        try:
            if os.path.exists(self._pidfile):
                os.unlink(self._pidfile)
        except OSError as e:
            logger.error("cannot remove pidfile %s: %s", self._pidfile, e)

    def _setup_signal_handlers(self):
        signal.signal(signal.SIGTERM, self._handle_signal)
        signal.signal(signal.SIGINT, self._handle_signal)
        signal.signal(signal.SIGHUP, self._handle_sighup)

    def _handle_signal(self, signum, frame):
        sig_name = signal.Signals(signum).name
        logger.info("received %s, initiating graceful shutdown...", sig_name)
        self._shutdown_event.set()

    def _handle_sighup(self, signum, frame):
        logger.info("received SIGHUP — reloading (no-op for now)")

    async def _run_async(self, factory, **kwargs):
        self._running = True
        self._main_task = asyncio.create_task(factory(**kwargs))
        shutdown = asyncio.create_task(self._shutdown_event.wait())
        done, _ = await asyncio.wait(
            [self._main_task, shutdown],
            return_when=asyncio.FIRST_COMPLETED,
        )
        if shutdown in done:
            logger.info("shutting down...")
            self._main_task.cancel()
            try:
                await self._main_task
            except asyncio.CancelledError:
                pass
        self._running = False
