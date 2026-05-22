# Astrix — by UNDEAD (https://github.com/itsund3ad)
# Main entry point: interactive shell, headless, daemon, benchmark modes.

import asyncio
import logging
import sys

from astrix_server import __version__
from astrix_server.cli.shell import ServerShell


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


def _start_server_headless(shell):
    from astrix_server.config.server import load_server_config

    shell.config = load_server_config(shell.config_path)

    from astrix_server.config.env import apply_env_overrides
    shell.config = apply_env_overrides(shell.config)

    try:
        asyncio.run(shell._start_server())
    except KeyboardInterrupt:
        print("\nStopped.")


def _start_daemon(shell, log_file, verbose, quiet):
    from astrix_server.daemon import Daemon
    from astrix_server.config.server import load_server_config
    from astrix_server.config.env import apply_env_overrides

    shell.config = load_server_config(shell.config_path)
    shell.config = apply_env_overrides(shell.config)

    pidfile = "/var/run/astrix-server.pid"

    daemon = Daemon(
        pidfile,
        foreground=False,
        log_file=log_file,
    )
    daemon.start(shell._start_server)


def main():
    argv = sys.argv[1:]
    config_path = "server_config.json"
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
            print(f"Astrix Server v{__version__}")
            return
        elif a in ("--help", "-h"):
            print(f"Astrix Server v{__version__}")
            print()
            print("Usage: astrix-server [options]")
            print()
            print("Options:")
            print("  --config PATH       Config file path (default: server_config.json)")
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
            print("  ASTRIX_TUNNEL_KEY, ASTRIX_SERVER_HOST, ASTRIX_SERVER_PORT,")
            print("  ASTRIX_UPSTREAM_PROXY, ASTRIX_DEBUG_TIMING")
            return
        else:
            i += 1

    setup_logging(log_file=log_file, verbose=verbose, quiet=quiet)

    shell = ServerShell()
    shell.config_path = config_path

    if benchmark:
        logging.getLogger("exit").setLevel(logging.DEBUG)
        print(f"Astrix Server v{__version__} — Benchmark Mode")
        _start_server_headless(shell)
    elif daemon_mode:
        _start_daemon(shell, log_file, verbose, quiet)
    elif headless:
        _start_server_headless(shell)
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
