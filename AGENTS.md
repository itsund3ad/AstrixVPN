# Astrix — AGENTS.md

**Author:** UNDEAD (https://github.com/itsund3ad)  
**Fork of:** [GooseRelayVPN](https://github.com/kianmhz/GooseRelayVPN)  
**Goal:** Python port for max speed & min ping TCP-over-Apps-Script censorship circumvention.

## Repo layout

```
.
├── astrix-client/          → Python SOCKS5 client (PyInstaller → .exe)
├── astrix-server/          → Python VPS exit server (PyInstaller → .exe)
├── apps-script/Code.gs     → Optimized Google Apps Script forwarder
├── AGENTS.md
├── main.py                 → Python 3.14 placeholder
└── pyproject.toml
```

## Core architecture

```
Browser → SOCKS5 (127.0.0.1:1080) → astrix-client
  → Zstd-compressed + AES-256-GCM batch
  → HTTPS to Google edge IP (SNI=www.google.com, Host=script.google.com)
  → Apps Script doPost() — dumb pipe, never sees key
  → astrix-server (VPS :8443/tunnel) — decrypts, demux, dials target
  ← Same path in reverse via long-poll
```

- **Auth = AES-GCM tag.** No passwords, no certs. `tunnel_key = openssl rand -hex 32`.
- **Apps Script sees only base64 ciphertext.** Key lives only on client & VPS.
- **DNS through tunnel** — SOCKS5 resolver is no-op; clients must use `socks5h://`.
- **`client_id` is deterministic** — SHA-256 of tunnel_key, first 16 bytes.
- **Batch sealed once** — single AES-GCM nonce+tag per HTTP body.

## Project packages

### astrix-client (`astrix_client/`)
| Module | File | Purpose |
|---|---|---|
| `frame.frame` | `astrix_client/frame/frame.py` | Wire format marshal/unmarshal (byte-compat with Go) |
| `crypto.crypto` | `astrix_client/crypto/crypto.py` | EncodeBatch/DecodeBatch: Zstd + AES-256-GCM + base64 |
| `session.session` | `astrix_client/session/session.py` | Per-connection seq counters, rx/tx queues |
| `socks.server` | `astrix_client/socks/server.py` | SOCKS5 asyncio server (TCP_NODELAY/QUICKACK) |
| `socks.conn` | `astrix_client/socks/conn.py` | VirtualConn — Session adapter with pool injection |
| `carrier.client` | `astrix_client/carrier/client.py` | Long-poll loop, health monitor, endpoint failover |
| `carrier.fronting` | `astrix_client/carrier/fronting.py` | Domain-fronted HTTPS, persistent session pools |
| `config.client` | `astrix_client/config/client.py` | JSON config loader + validator |
| `cli.shell` | `astrix_client/cli/shell.py` | Rich TUI: setup wizard, diag, live stats |

### astrix-server (`astrix_server/`)
| Module | File | Purpose |
|---|---|---|
| `frame.frame` | `astrix_server/frame/frame.py` | Wire format (shared copy) |
| `crypto.crypto` | `astrix_server/crypto/crypto.py` | Batch seal/open (shared copy) |
| `session.session` | `astrix_server/session/session.py` | Session state (shared copy) |
| `exit.server` | `astrix_server/exit/server.py` | HTTP handler (POST /tunnel, GET /healthz) |
| `exit.dnscache` | `astrix_server/exit/dnscache.py` | DNS cache with 5-min TTL |
| `exit.stats` | `astrix_server/exit/stats.py` | Atomic stats counters |
| `config.server` | `astrix_server/config/server.py` | JSON config loader + validator |
| `cli.shell` | `astrix_server/cli/shell.py` | Rich TUI interactive shell |

### apps-script (`apps-script/Code.gs`)
- Deploy as: Execute as Me, Access: Anyone
- Each edit requires **new deployment** (Deploy → New deployment)
- Daily quota: ~20k calls/day per Google account
- `ENABLE_INVOCATION_COUNTING = false` (adds ~50ms/request; keep off for speed)

## Wire format (byte-compatible with Go upstream)

```
Frame:   session_id(16) || seq(u64 BE) || flags(u8) || target_len(u8) || target || payload_len(u32 BE) || payload
Flags:   SYN=1, FIN=2, ACK=4, RST=8
Batch:   base64( nonce(12) || AES-256-GCM( flags_byte || client_id(16) || u16_frame_count || [u32_len || frame_bytes]… ) )
Compression: Zstd level 1 (SpeedFastest), skip if <512 bytes
```

## Key performance constants (optimization targets)

| Constant | File | Astrix Default | Notes |
|---|---|---|---|
| `MaxFramePayload` | `carrier/client.py:13` / `exit/server.py:42` | 256 KB | Must match in both. Tune up to 1M. |
| `ActiveDrainWindow` | `exit/server.py:43` | 350 ms | Batch-with-work wait. Tune 200-500ms. |
| `LongPollWindow` | `exit/server.py:44` | 8 s | Empty poll hold time. |
| `coalesceWindow` | `exit/server.py:45` | 25 ms | Server-side frame gather. |
| `coalesceWindowBusy` | `exit/server.py:46` | 10 ms | Busy coalesce (faster). |
| `coalesceWindowPoll` | `exit/server.py:47` | 100 ms | Idle poll coalesce. |
| `pollIdleSleep` | `carrier/client.py:14` | 10 ms | Client idle poll sleep. |
| `pollTimeout` | `carrier/client.py:15` | 120 s | HTTP ceiling. |
| `workersPerEndpoint` | `carrier/client.py:16` | 3 | Parallel workers per endpoint. |
| `idleSlotsPerBucket` | `carrier/client.py:17` | 2 | Max idle polls per bucket. |
| `_MaxConcurrentUpstream` | `exit/server.py:52` | 256 | Server backpressure semaphore. |
| `_HealthPollInterval` | `carrier/client.py:21` | 30 s | Client health check interval. |
| `_MaxConsecutiveFailures` | `carrier/client.py:22` | 5 | Trigger endpoint quarantine. |
| `_BackoffBase` / `_BackoffMax` | `carrier/client.py:23-24` | 3s / 1h | Exponential backoff range. |
| `TxBufHighWater` | `session/session.py:18` | 8 MB | Per-session TX backpressure. |
| `upstreamReadBuf` | `exit/server.py:48` | 256 KB | Server upstream read chunk. |
| `dnsCacheTTL` | `exit/dnscache.py:9` | 5 min | Server DNS cache. |
| `idleSessionTimeout` | `exit/server.py:49` | 10 min | Server orphan GC. |
| `compressMinSize` | `crypto/crypto.py:16` | 512 B | Min batch size for Zstd. |
| `lockStripes` | `session/session.py:67` | 64 | SessionPool striped lock count. |

## Build / run commands

```sh
# Client
cd astrix-client && pip install -e .
python -m astrix_client                        # interactive shell
python -m astrix_client --config client_config.json --start   # headless

# Server
cd astrix-server && pip install -e .
python -m astrix_server                        # interactive shell
python -m astrix_server --config server_config.json --start   # headless

# PyInstaller .exe
cd astrix-client && python build_exe.py        # → dist/astrix-client(.exe)
cd astrix-server && python build_exe.py        # → dist/astrix-server(.exe)
```

## Config fields

### Client (`client_config.json`)
`socks_host` `socks_port` `google_host` `sni` `script_keys` `tunnel_key` `socks_user` `socks_pass` `coalesce_step_ms` `idle_slots_per_bucket` `debug_timing`

### Server (`server_config.json`)
`server_host` `server_port` `tunnel_key` `upstream_proxy` `debug_timing`

- `tunnel_key`: 64 hex chars, `openssl rand -hex 32`
- `script_keys`: plain string IDs or `{"id": "AKfycb...", "account": "acct-a"}` objects
- `sni`: single string or array for multiple throttle buckets
- `coalesce_step_ms`: 20-40 ms recommended range

## Astrix-specific optimizations over GooseRelayVPN

### Speed
- **Zero-copy inline marshal**: `marshal_into(buf, offset, frame)` writes directly to batch plaintext buffer — no intermediate `bytes` objects between `marshal()` and `encode_batch()`
- **Inline unmarshal**: `decode_batch()` decodes frames directly from the plaintext buffer rather than calling `unmarshal()` per frame — one less function call per frame on the decode path
- **Buffer pooling**: frame marshal/decompress use thread-safe pool of `bytearray`s to reduce GC
- **Connection pooling**: FrontedClient keeps persistent `aiohttp.ClientSession` per SNI host with keep-alive
- **Striped locking**: SessionPool uses 64-stripe async locks for minimal contention on hot drain path
- **Pre-allocated crypto**: `encode_batch()` computes exact plaintext size, allocates once, no intermediate lists
- **Zstd compressor pool**: Thread-safe pool of `ZstdCompressor`/`ZstdDecompressor` objects avoid per-call alloc
- **TCP_NODELAY/QUICKACK**: Applied on every socket (SOCKS5 accept, upstream dial, server listen)
- **Server concurrency semaphore**: `_MaxConcurrentUpstream=256` prevents upstream flood under load
- **Event-based wakeup**: VirtualConn and upstream workers use `asyncio.Event` instead of `asyncio.sleep(0.005)` — zero CPU overhead when idle
- **Adaptive compression**: Skips Zstd after 3 incompressible batches, rechecks every 10 — avoids wasting CPU on uncompressible data
- **Adaptive batch sizing**: Grows/shrinks frames-per-batch (1–64) based on observed server response volume — larger batches when pipe is flowing, smaller when idle

### Ease of use
- **Setup wizard**: Step-by-step config creation with field validation and key generation
- **Pre-flight diagnostics**: Port check, DNS resolution, HTTPS reachability test with progress bars
- **Live status dashboard**: Per-endpoint real-time stats (polls, bytes, RTT, error count)
- **Config import/export**: Save/load/raw-JSON-view for both client and server
- **Auto-generated client_id**: SHA-256 of tunnel_key → 16 bytes, no manual config needed
- **Session viewer (server)**: Rich table of active sessions with target, buffers, last activity

### Robustness
- **Health monitor loop**: Polls endpoints every 30s, quarantines after 5 consecutive failures
- **Exponential backoff**: Dead endpoints retried starting at 3s, doubling to max 1h
- **Auto-reconnect**: Dead endpoints automatically revived by health monitor
- **Per-endpoint stats tracking**: Consecutive failures, last error, avg RTT for diagnostics
- **Session GC**: Periodic cleanup of expired orphan sessions (10min timeout)

## Changelog

| Date | Changes |
|---|---|
| 2026-05-22 | Astrix v2 optimization: zero-copy inline marshal/unmarshal, Event-based wakeup, adaptive compression, adaptive batch sizing (1–64), endpoint quality scoring, watchdog timer, `--benchmark` mode, pipeline drain pattern, inline decode_batch (no per-frame function call), TCP_NODELAY+QUICKACK on all sockets, concurrency semaphore, setup wizard, diagnostics, health monitor, session viewer, import/export |

## MCP memory entities (knowledge graph)

For session continuity, the project graph is stored under:
- **Entity** `Astrix` — project root, author, goal
- **Entity** `astrix-client` / `astrix-server` / `apps-script` — components
- **Entity** `frame-module` / `crypto-module` / `session-module` / `carrier-module` / etc. — modules
- **Entity** `current-progress` — what's been done and what remains

Use `memory_read_graph` to restore full context after a new session starts.
