# Astrix

**TCP-over-Apps-Script Censorship Circumvention**

[![Python](https://img.shields.io/badge/python-3.14%2B-blue)](https://python.org)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Version](https://img.shields.io/badge/version-0.1.0-orange)](VERSION)

Astrix is a high-performance Python port of [GooseRelayVPN](https://github.com/kianmhz/GooseRelayVPN) — a tool that tunnels TCP traffic through Google Apps Script to bypass internet censorship. It uses domain fronting, AES-256-GCM encryption, and Zstandard compression to create an obfuscated tunnel that appears as normal HTTPS traffic to Google edge IPs.

```
Browser ──► SOCKS5 :1080 ──► astrix-client
  ──► Zstd + AES-256-GCM batch
  ──► HTTPS to Google edge (SNI=www.google.com, Host=script.google.com)
  ──► Apps Script doPost() — dumb pipe, never sees key
  ──► astrix-server :8443 — decrypts, demux, dials target
  ◄── Same path in reverse via long-poll
```

---

## Features

- **Domain fronting** — traffic appears as normal Google HTTPS
- **AES-256-GCM AEAD** — military-grade encryption, tag-based auth (no passwords)
- **Zstandard compression** — adaptive, skips incompressible data automatically
- **SOCKS5 proxy** — standard proxy protocol, RFC 1929 auth support
- **Multi-endpoint failover** — spread load across multiple Google accounts
- **Auto-reconnect** — exponential backoff (3s → 1h), health monitoring
- **Adaptive batch sizing** — 1–64 frames per HTTP request based on live RTT
- **Event-driven I/O** — zero CPU overhead when idle
- **Rich TUI** — interactive shell with setup wizard, diagnostics, live stats
- **systemd integration** — PID file, graceful shutdown, log rotation
- **Environment config** — all settings overridable via env vars

---

## Quick Start

### Installation

```bash
# VPS one-command install (root):
sudo bash deploy/install.sh client     # SOCKS5 proxy client
sudo bash deploy/install.sh server     # VPS exit server
sudo bash deploy/install.sh all        # both

# Or pip install from source:
pip install -e astrix-client
pip install -e astrix-server
```

### Configure

```bash
# Create a config interactively (no arguments = TUI):
astrix-client

# Or edit the JSON directly:
nano client_config.json
nano server_config.json
```

**Minimum `client_config.json`:**
```json
{
    "socks_host": "127.0.0.1",
    "socks_port": 1080,
    "google_host": "216.239.38.120",
    "sni": ["www.google.com"],
    "script_keys": ["AKfycb..."],
    "tunnel_key": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
}
```

**Minimum `server_config.json`:**
```json
{
    "server_host": "0.0.0.0",
    "server_port": 8443,
    "tunnel_key": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
}
```

> **`tunnel_key` must be identical on client and server.** Generate with:
> ```bash
> openssl rand -hex 32
> ```

### Run

```bash
# Interactive shell:
astrix-client
astrix-server

# Headless (no TUI):
astrix-client --config /etc/astrix/client.json --start
astrix-server --config /etc/astrix/server.json --start

# Daemon (systemd mode):
astrix-client --config /etc/astrix/client.json --daemon --log-file /var/log/astrix/client.log
astrix-server --config /etc/astrix/server.json --daemon --log-file /var/log/astrix/server.log

# Benchmark mode:
astrix-client --config /etc/astrix/client.json --benchmark
astrix-server --config /etc/astrix/server.json --benchmark

# Via systemd:
systemctl start astrix-client
systemctl start astrix-server
journalctl -u astrix-client -f
```

---

## Architecture

### Components

| Component | Role |
|---|---|
| **astrix-client** | Runs on your machine. SOCKS5 proxy + domain-fronting carrier |
| **astrix-server** | Runs on a VPS. HTTP tunnel handler + upstream dialer |
| **Apps Script** | Google Apps Script web app. Dumb pipe — never sees encryption keys |

### Wire protocol

```
Frame:   session_id(16) || seq(u64 BE) || flags(u8) || target_len(u8)
         || target || payload_len(u32 BE) || payload
Flags:   SYN=1, FIN=2, ACK=4, RST=8

Batch:   base64( nonce(12) || AES-256-GCM( flags_byte || client_id(16)
         || u16_frame_count || [u32_len || frame_bytes]… ) )

Compression: Zstd level 1, skipped if <512 bytes or incompressible
```

Byte-compatible with the upstream Go implementation.

### Performance features

| Optimization | Description |
|---|---|
| Zero-copy marshal | `marshal_into()` writes directly to batch buffer — no intermediate bytes |
| Inline unmarshal | `decode_batch()` reads frames from plaintext without per-frame function calls |
| Buffer pooling | Thread-safe pools for marshal buffers, Zstd compressors/decompressors |
| Connection pooling | Persistent `aiohttp.ClientSession` per SNI host with keep-alive |
| Striped locking | 64-stripe async locks for minimal contention |
| Event-based wakeup | `asyncio.Event` replaces polling sleeps — zero CPU at idle |
| Adaptive compression | Skips Zstd after 3 incompressible batches, rechecks every 10 |
| Adaptive batch sizing | 1–64 frames per request based on observed RTT |
| Endpoint quality scoring | Auto-selects best endpoint by median RTT |
| TCP_NODELAY/QUICKACK | Applied on every socket |
| Pipeline mode | Next drain starts while waiting for HTTP response |
| Watchdog timer | Forced reconnect after 120s idle with active sessions |

---

## Configuration

### File-based config

**Client fields** (`client_config.json`):

| Field | Default | Description |
|---|---|---|
| `socks_host` | `127.0.0.1` | SOCKS5 listen address |
| `socks_port` | `1080` | SOCKS5 listen port |
| `socks_user` | `""` | SOCKS5 auth username |
| `socks_pass` | `""` | SOCKS5 auth password |
| `google_host` | `216.239.38.120` | Google edge IP for domain fronting |
| `sni` | `["www.google.com"]` | SNI hostnames (one per throttle bucket) |
| `script_keys` | `[]` | Apps Script deployment IDs |
| `tunnel_key` | — | 64-char hex key (shared with server) |
| `coalesce_step_ms` | `25` | Frame coalescing window |
| `idle_slots_per_bucket` | `2` | Concurrent idle polls (0–3) |
| `debug_timing` | `false` | Enable debug timing logs |

**Server fields** (`server_config.json`):

| Field | Default | Description |
|---|---|---|
| `server_host` | `0.0.0.0` | HTTP listen address |
| `server_port` | `8443` | HTTP listen port |
| `tunnel_key` | — | 64-char hex key (shared with client) |
| `upstream_proxy` | `""` | Optional SOCKS5 upstream proxy |
| `debug_timing` | `false` | Enable debug timing logs |

### Environment variables

All fields are overridable via `ASTRIX_*` environment variables (takes priority over JSON config):

```bash
# Client:
ASTRIX_TUNNEL_KEY=abc... ASTRIX_SOCKS_PORT=9090 astrix-client --start

# Server:
ASTRIX_TUNNEL_KEY=abc... ASTRIX_SERVER_PORT=443 astrix-server --start

# Full list: see --help
```

---

## Deployment

### systemd (recommended for VPS)

```bash
sudo bash deploy/install.sh client --auto-config
sudo bash deploy/install.sh server --auto-config
```

This installs Python dependencies, creates configs with random keys, and enables systemd services.

**Manual:**
```bash
sudo cp deploy/astrix-client.service /etc/systemd/system/
sudo cp deploy/astrix-server.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now astrix-client
sudo systemctl enable --now astrix-server
```

### Docker

```bash
# Client
docker build -t astrix-client -f deploy/Dockerfile.client .
docker run -v /etc/astrix:/etc/astrix astrix-client

# Server
docker build -t astrix-server -f deploy/Dockerfile.server .
docker run -p 8443:8443 -v /etc/astrix:/etc/astrix astrix-server
```

---

## Development

### Requirements

- Python **3.14+**
- `pip install -e astrix-client`
- `pip install -e astrix-server`

### Testing

```bash
pip install -e astrix-client
python -m astrix_client  # interactive shell
```

### Version bumping

```bash
# Current version: 0.1.0
./scripts/bump_version.sh patch    # → 0.1.1
./scripts/bump_version.sh minor    # → 0.2.0
./scripts/bump_version.sh major    # → 1.0.0
./scripts/bump_version.sh 0.5.0    # → 0.5.0 (explicit)
```

---

## Project structure

```
.
├── astrix-client/           → Python SOCKS5 client
│   ├── astrix_client/
│   │   ├── carrier/         → Long-poll + domain fronting
│   │   ├── cli/             → Rich TUI interactive shell
│   │   ├── config/          → JSON + env var loader
│   │   ├── crypto/          → AES-256-GCM + Zstd
│   │   ├── frame/           → Wire format marshal/unmarshal
│   │   ├── session/         → Session state management
│   │   ├── socks/           → SOCKS5 proxy server
│   │   ├── daemon.py        → systemd daemon wrapper
│   │   └── __main__.py      → Entry point
│   └── pyproject.toml
├── astrix-server/           → Python VPS exit server
│   ├── astrix_server/
│   │   ├── cli/             → Rich TUI interactive shell
│   │   ├── config/          → JSON + env var loader
│   │   ├── crypto/          → AES-256-GCM + Zstd (shared copy)
│   │   ├── exit/            → HTTP handler + upstream
│   │   ├── frame/           → Wire format (shared copy)
│   │   ├── session/         → Session state (shared copy)
│   │   ├── daemon.py        → systemd daemon wrapper
│   │   └── __main__.py      → Entry point
│   └── pyproject.toml
├── apps-script/Code.gs      → Google Apps Script forwarder
├── deploy/
│   ├── astrix-client.service → systemd unit for client
│   ├── astrix-server.service → systemd unit for server
│   └── install.sh           → One-command VPS installer
├── scripts/
│   └── bump_version.sh      → Version bumper
├── VERSION                  → Single version source
├── AGENTS.md                → AI agent instructions
└── README.md
```

---

## License

This project is a fork of [GooseRelayVPN](https://github.com/kianmhz/GooseRelayVPN) by Kianmhz.

**Author:** UNDEAD ([github.com/itsund3ad](https://github.com/itsund3ad))

MIT License — see [LICENSE](LICENSE) for details.
