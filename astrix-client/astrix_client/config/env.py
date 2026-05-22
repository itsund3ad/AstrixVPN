# Astrix — by UNDEAD (https://github.com/itsund3ad)
# Environment variable loader for client config.
# Overrides values from JSON config with ASTRIX_* env vars.

import json
import os
from typing import Optional

from astrix_client.config.client import ClientConfig, ScriptKeyEntry


def apply_env_overrides(cfg: ClientConfig) -> ClientConfig:
    """Apply ASTRIX_* environment variables on top of a loaded config.

    Env vars take priority over JSON config values.
    """
    socks_host = os.environ.get("ASTRIX_SOCKS_HOST")
    if socks_host:
        cfg.socks_host = socks_host

    socks_port = os.environ.get("ASTRIX_SOCKS_PORT")
    if socks_port:
        cfg.socks_port = int(socks_port)

    socks_user = os.environ.get("ASTRIX_SOCKS_USER")
    if socks_user is not None:
        cfg.socks_user = socks_user

    socks_pass = os.environ.get("ASTRIX_SOCKS_PASS")
    if socks_pass is not None:
        cfg.socks_pass = socks_pass

    google_host = os.environ.get("ASTRIX_GOOGLE_HOST")
    if google_host:
        cfg.google_host = google_host

    sni = os.environ.get("ASTRIX_SNI")
    if sni:
        cfg.sni_hosts = [h.strip() for h in sni.split(",") if h.strip()]

    script_keys = os.environ.get("ASTRIX_SCRIPT_KEYS")
    if script_keys:
        try:
            parsed = json.loads(script_keys)
            cfg.script_keys = []
            for item in parsed:
                if isinstance(item, str):
                    cfg.script_keys.append(ScriptKeyEntry(id=item))
                elif isinstance(item, dict):
                    cfg.script_keys.append(
                        ScriptKeyEntry(id=item.get("id", ""), account=item.get("account", ""))
                    )
        except json.JSONDecodeError:
            cfg.script_keys = [
                ScriptKeyEntry(id=k.strip())
                for k in script_keys.split(",")
                if k.strip()
            ]

    tunnel_key = os.environ.get("ASTRIX_TUNNEL_KEY")
    if tunnel_key:
        cfg.tunnel_key = tunnel_key.strip()

    coalesce = os.environ.get("ASTRIX_COALESCE_STEP_MS")
    if coalesce:
        cfg.coalesce_step_ms = int(coalesce)

    idle_slots = os.environ.get("ASTRIX_IDLE_SLOTS")
    if idle_slots:
        cfg.idle_slots_per_bucket = max(0, min(3, int(idle_slots)))

    debug = os.environ.get("ASTRIX_DEBUG_TIMING")
    if debug is not None:
        cfg.debug_timing = debug.lower() in ("1", "true", "yes")

    return cfg
