# Astrix — by UNDEAD (https://github.com/itsund3ad)
# Client configuration loader for astrix-client

import json
import os
import re
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlparse


@dataclass
class ScriptKeyEntry:
    id: str
    account: str = ""


@dataclass
class ClientConfig:
    socks_host: str = "127.0.0.1"
    socks_port: int = 1080
    socks_user: str = ""
    socks_pass: str = ""
    google_host: str = "216.239.38.120"
    sni_hosts: list[str] = field(default_factory=lambda: ["www.google.com"])
    script_keys: list[ScriptKeyEntry] = field(default_factory=list)
    tunnel_key: str = ""
    relay_urls: list[str] = field(default_factory=list)
    debug_timing: bool = False
    coalesce_step_ms: int = 0
    idle_slots_per_bucket: int = 2

    def client_id_bytes(self) -> bytes:
        """Generate a deterministic 16-byte client ID from tunnel_key.

        Uses first 16 bytes of SHA-256 of the tunnel_key.
        """
        import hashlib
        h = hashlib.sha256(self.tunnel_key.encode()).digest()
        return h[:16]

    @property
    def listen_addr(self) -> str:
        return f"{self.socks_host}:{self.socks_port}"

    @property
    def use_fronting(self) -> bool:
        return len(self.relay_urls) == 0

    @property
    def script_urls(self) -> list[str]:
        if self.relay_urls:
            return self.relay_urls
        return [
            f"https://script.google.com/macros/s/{entry.id}/exec"
            for entry in self.script_keys
        ]

    @property
    def script_accounts(self) -> list[str]:
        return [entry.account for entry in self.script_keys]

    @property
    def coalesce_max_ms(self) -> int:
        return self.coalesce_step_ms * 25 if self.coalesce_step_ms > 0 else 0


def _normalize_deployment_id(v: str) -> str:
    v = v.strip()
    if not v:
        return ""
    v = v.rstrip("/")
    v = v.removesuffix("/exec")
    v = v.strip("/")
    parts = v.split("/")
    for i in range(len(parts) - 1):
        if parts[i] == "s":
            return parts[i + 1]
    return parts[-1] if parts else v


def _validate_deployment_id(id: str, index: int) -> None:
    if not id:
        raise ValueError(f"script_keys[{index}]: empty value")
    if id in ("REPLACE_WITH_DEPLOYMENT_ID", "OPTIONAL_SECOND_DEPLOYMENT_ID"):
        raise ValueError(
            f"script_keys[{index}]: still contains placeholder text"
        )
    if "/edit" in id or "script.google.com/d/" in id:
        raise ValueError(
            f"script_keys[{index}]: looks like an editor URL, not a Deployment ID"
        )
    if re.search(r"\s", id):
        raise ValueError(
            f"script_keys[{index}]: contains whitespace"
        )
    if not id.startswith("AKfycb"):
        raise ValueError(
            f"script_keys[{index}]: deployment IDs start with 'AKfycb'"
        )
    if len(id) < 50:
        raise ValueError(
            f"script_keys[{index}]: too short ({len(id)} chars; expected ~70)"
        )


def _parse_script_keys(raw) -> list[ScriptKeyEntry]:
    if not raw:
        return []
    entries: list[ScriptKeyEntry] = []
    seen_ids: set[str] = set()
    for i, item in enumerate(raw):
        if isinstance(item, str):
            entry = ScriptKeyEntry(id=item)
        elif isinstance(item, dict):
            entry = ScriptKeyEntry(
                id=item.get("id", ""),
                account=item.get("account", ""),
            )
        else:
            raise ValueError(
                f"script_keys[{i}]: must be a string or object with 'id' field"
            )
        dedup_id = _normalize_deployment_id(entry.id)
        if dedup_id in seen_ids:
            continue
        seen_ids.add(dedup_id)
        _validate_deployment_id(dedup_id, i)
        entries.append(ScriptKeyEntry(id=dedup_id, account=entry.account.strip()))
    return entries


def _parse_sni(raw) -> list[str]:
    if not raw:
        return ["www.google.com"]
    if isinstance(raw, str):
        return [raw.strip()] if raw.strip() else ["www.google.com"]
    if isinstance(raw, list):
        hosts = [h.strip() for h in raw if h.strip()]
        return hosts if hosts else ["www.google.com"]
    return ["www.google.com"]


def load_client_config(path: str) -> ClientConfig:
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"config file '{path}' not found.\n"
            f"  Fix: copy the example and edit it:\n"
            f"      cp client_config.example.json {path}"
        )
    with open(path) as f:
        data = json.load(f)

    cfg = ClientConfig()

    cfg.socks_host = data.get("socks_host", "127.0.0.1")
    cfg.socks_port = int(data.get("socks_port", 1080))
    if not (1 <= cfg.socks_port <= 65535):
        raise ValueError(f"socks_port out of range: {cfg.socks_port}")

    cfg.socks_user = data.get("socks_user", "").strip()
    cfg.socks_pass = data.get("socks_pass", "").strip()
    if (cfg.socks_user == "") != (cfg.socks_pass == ""):
        raise ValueError("socks_user and socks_pass must both be set or both empty")

    cfg.google_host = data.get("google_host", "216.239.38.120")
    cfg.sni_hosts = _parse_sni(data.get("sni"))

    cfg.relay_urls = data.get("relay_urls", [])

    if not cfg.relay_urls:
        raw_script_keys = data.get("script_keys", [])
        if not raw_script_keys:
            raise ValueError("script_keys is empty")
        cfg.script_keys = _parse_script_keys(raw_script_keys)

    raw_key = data.get("tunnel_key", "").strip()
    if not raw_key or raw_key == "REPLACE_WITH_64_HEX_CHARACTER_RANDOM_KEY":
        raise ValueError(
            "tunnel_key is empty or placeholder.\n"
            "  Fix: openssl rand -hex 32"
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

    cfg.debug_timing = bool(data.get("debug_timing", False))

    step = int(data.get("coalesce_step_ms", 0))
    if step < 0:
        raise ValueError("coalesce_step_ms must be >= 0")
    cfg.coalesce_step_ms = step

    slots = int(data.get("idle_slots_per_bucket", 2))
    if slots < 0 or slots > 3:
        raise ValueError("idle_slots_per_bucket must be 0-3")
    cfg.idle_slots_per_bucket = slots if slots > 0 else 2

    return cfg
