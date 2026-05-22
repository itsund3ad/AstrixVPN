# Astrix — by UNDEAD (https://github.com/itsund3ad)
# Main entry point: interactive shell, headless, daemon, benchmark modes.

import asyncio
import logging
import os
import sys

from astrix_client import __version__
from astrix_client.cli.shell import ClientShell


def setup_logging(log_file=None, verbose=False, quiet=False):
    level = logging.DEBUG if verbose else (logging.WARNING if quiet else logging.INFO)
    handlers = [logging.StreamHandler(sys.stdout)]
    if log_file:
        handlers.append(logging.FileHandler(log_file))

    logging.basicConfig(
        level=level,
        format="%(asctime)s  %(name)-7s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
        handlers=handlers,
    )


def _start_tunnel_headless(shell):
    """Start tunnel in headless mode (no interactive shell)."""
    from astrix_client.config.client import load_client_config

    shell.config = load_client_config(shell.config_path)

    # Apply env var overrides
    from astrix_client.config.env import apply_env_overrides
    shell.config = apply_env_overrides(shell.config)

    asyncio.run(shell._start_tunnel())


def _start_daemon(shell, log_file, verbose, quiet):
    """Start in daemon mode with PID file and signal handling."""
    from astrix_client.daemon import Daemon
    from astrix_client.config.client import load_client_config
    from astrix_client.config.env import apply_env_overrides

    shell.config = load_client_config(shell.config_path)
    shell.config = apply_env_overrides(shell.config)

    pidfile = "/var/run/astrix-client.pid"

    daemon = Daemon(
        pidfile,
        foreground=False,
        log_file=log_file,
    )
    daemon.start(shell._start_tunnel)


def main():
    argv = sys.argv[1:]
    config_path = "client_config.json"
    log_file = None
    verbose = False
    quiet = False
    daemon_mode = False
    headless = False
    benchmark = False

    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--config" and i + 1 < len(argv):
            config_path = argv[i + 1]
            i += 2
        elif a == "--log-file" and i + 1 < len(argv):
            log_file = argv[i + 1]
            i += 2
        elif a == "--daemon":
            daemon_mode = True
            i += 1
        elif a == "--start":
            headless = True
            i += 1
        elif a == "--benchmark":
            benchmark = True
            i += 1
        elif a == "--verbose":
            verbose = True
            i += 1
        elif a == "--quiet":
            quiet = True
            i += 1
        elif a == "--version":
            print(f"Astrix Client v{__version__}")
            return
        elif a in ("--help", "-h"):
            print(f"Astrix Client v{__version__}")
            print()
            print("Usage: astrix-client [options]")
            print()
            print("Options:")
            print("  --config PATH       Config file path (default: client_config.json)")
            print("  --start             Start in headless mode (no TUI)")
            print("  --daemon            Run as daemon (forks to background)")
            print("  --log-file PATH     Write logs to file")
            print("  --verbose           Verbose logging (DEBUG level)")
            print("  --quiet             Quiet logging (WARNING only)")
            print("  --benchmark         Benchmark mode (verbose performance stats)")
            print("  --version           Show version")
            print("  --help              Show this help")
            print()
            print("Environment variables:")
            print("  ASTRIX_TUNNEL_KEY, ASTRIX_SOCKS_HOST, ASTRIX_SOCKS_PORT,")
            print("  ASTRIX_SOCKS_USER, ASTRIX_SOCKS_PASS, ASTRIX_GOOGLE_HOST,")
            print("  ASTRIX_SNI, ASTRIX_SCRIPT_KEYS, ASTRIX_COALESCE_STEP_MS,")
            print("  ASTRIX_IDLE_SLOTS, ASTRIX_DEBUG_TIMING")
            return
        else:
            i += 1

    setup_logging(log_file=log_file, verbose=verbose, quiet=quiet)

    shell = ClientShell()
    shell.config_path = config_path

    if benchmark:
        logging.getLogger("carrier").setLevel(logging.DEBUG)
        logging.getLogger("crypto").setLevel(logging.DEBUG)
        print(f"Astrix Client v{__version__} — Benchmark Mode")
        _start_tunnel_headless(shell)
    elif daemon_mode:
        _start_daemon(shell, log_file, verbose, quiet)
    elif headless:
        _start_tunnel_headless(shell)
    else:
        try:
            asyncio.run(shell.run())
        except KeyboardInterrupt:
            print("\nGoodbye!")
        except Exception as e:
            print(f"Fatal: {e}")
            sys.exit(1)


if __name__ == "__main__":
    main()
