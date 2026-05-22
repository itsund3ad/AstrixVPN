# Astrix — by UNDEAD (https://github.com/itsund3ad)
# Server configuration loader for astrix-server

import json
import os
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlparse


@dataclass
class ServerConfig:
    server_host: str = "0.0.0.0"
    server_port: int = 8443
    tunnel_key: str = ""
    upstream_proxy: str = ""
    debug_timing: bool = False
    initial_response_bytes_pre_encode: int = 0

    @property
    def listen_addr(self) -> str:
        return f"{self.server_host}:{self.server_port}"


def load_server_config(path: str) -> ServerConfig:
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"config file '{path}' not found.\n"
            f"  Fix: copy the example and edit it:\n"
            f"      cp server_config.example.json {path}"
        )
    with open(path) as f:
        data = json.load(f)

    cfg = ServerConfig()

    cfg.server_host = data.get("server_host", "0.0.0.0")
    cfg.server_port = int(data.get("server_port", 8443))
    if not (1 <= cfg.server_port <= 65535):
        raise ValueError(f"server_port out of range: {cfg.server_port}")

    raw_key = data.get("tunnel_key", "").strip()
    if not raw_key or raw_key == "SAME_VALUE_AS_CLIENT_tunnel_key":
        raise ValueError(
            "tunnel_key is empty or placeholder.\n"
            "  Fix: paste the 64-char key from your client config"
        )
    if len(raw_key) != 64:
        raise ValueError(
            f"tunnel_key must be 64 hex chars, got {len(raw_key)}"
        )
    try:
        bytes.fromhex(raw_key)
    except ValueError:
        raise ValueError("tunnel_key contains non-hex characters")
    cfg.tunnel_key = raw_key

    raw_proxy = data.get("upstream_proxy", "").strip()
    if raw_proxy:
        parsed = urlparse(raw_proxy)
        if parsed.scheme != "socks5":
            raise ValueError(
                f"upstream_proxy must be socks5:// URL, got '{raw_proxy}'"
            )
        cfg.upstream_proxy = parsed.hostname or ""
        if parsed.port:
            cfg.upstream_proxy += f":{parsed.port}"
        else:
            cfg.upstream_proxy += ":1080"

    cfg.debug_timing = bool(data.get("debug_timing", False))

    raw_initial = int(data.get("initial_response_bytes_pre_encode", 0))
    if raw_initial < 0:
        raise ValueError("initial_response_bytes_pre_encode must be >= 0")
    cfg.initial_response_bytes_pre_encode = raw_initial

    return cfg
