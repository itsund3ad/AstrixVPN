# Astrix — by UNDEAD (https://github.com/itsund3ad)
# Environment variable loader for server config.
# Overrides values from JSON config with ASTRIX_* env vars.

import os

from astrix_server.config.server import ServerConfig


def apply_env_overrides(cfg: ServerConfig) -> ServerConfig:
    """Apply ASTRIX_* environment variables on top of a loaded config."""
    host = os.environ.get("ASTRIX_SERVER_HOST")
    if host:
        cfg.server_host = host

    port = os.environ.get("ASTRIX_SERVER_PORT")
    if port:
        cfg.server_port = int(port)

    tunnel_key = os.environ.get("ASTRIX_TUNNEL_KEY")
    if tunnel_key:
        cfg.tunnel_key = tunnel_key.strip()

    proxy = os.environ.get("ASTRIX_UPSTREAM_PROXY")
    if proxy is not None:
        cfg.upstream_proxy = proxy.strip()

    debug = os.environ.get("ASTRIX_DEBUG_TIMING")
    if debug is not None:
        cfg.debug_timing = debug.lower() in ("1", "true", "yes")

    return cfg
