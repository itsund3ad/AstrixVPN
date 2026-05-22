# Astrix — by UNDEAD (https://github.com/itsund3ad)
# HTTP tunnel exit server with long-poll, adaptive coalescing,
# Event-based upstream notification, session lifecycle.
# Optimized: TCP_NODELAY, parallel coalesce, Event-driven wakeup.

import asyncio
import logging
import socket
import time
from typing import Optional

from aiohttp import web, TCPConnector

from astrix_server.frame.frame import (
    Frame,
    ClientIDLen,
    FlagSYN,
    FlagFIN,
    FlagACK,
    FlagRST,
    marshal,
)
from astrix_server.crypto.crypto import Crypto, encode_batch, decode_batch
from astrix_server.session.session import SessionPool
from astrix_server.exit.dnscache import DNSCache
from astrix_server.exit.stats import Stats

logger = logging.getLogger("exit")

MaxFramePayload = 262144
ActiveDrainWindow = 0.350
LongPollWindow = 8.0
coalesceWindow = 0.025
coalesceWindowBusy = 0.010
coalesceWindowPoll = 0.100
upstreamReadBuf = 262144
idleSessionTimeout = 600.0

_MaxConcurrentUpstream = 256
_UpstreamSemaphore: Optional[asyncio.Semaphore] = None


def apply_socket_opts(runner: web.AppRunner) -> None:
    """Set TCP_NODELAY on all server sockets."""
    for sock in runner.server.sockets:
        try:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except (OSError, AttributeError):
            pass


def _make_app(config) -> web.Application:
    global _UpstreamSemaphore
    _UpstreamSemaphore = asyncio.Semaphore(_MaxConcurrentUpstream)

    app = web.Application()

    app["crypto"] = Crypto.from_hex_key(config.tunnel_key)
    app["pool"] = SessionPool()
    app["stats"] = Stats()
    app["dnscache"] = DNSCache()
    app["config"] = config
    app["upstream_connector"] = TCPConnector(
        ttl_dns_cache=600,
        enable_cleanup_closed=True,
    )

    app.on_startup.append(lambda a: logger.info("Astrix exit server started"))

    app.router.add_post("/tunnel", _handle_tunnel_post)
    app.router.add_get("/healthz", _handle_healthz)

    return app


async def _handle_healthz(request: web.Request) -> web.Response:
    stats: Stats = request.app["stats"]
    pool: SessionPool = request.app["pool"]
    return web.json_response({
        "status": "ok",
        "sessions": pool.active_count(),
        "polls_served": stats.polls_served,
        "bytes_in": stats.bytes_in,
        "bytes_out": stats.bytes_out,
        "uptime": time.monotonic() - stats.start_time,
    })


async def _handle_tunnel_post(request: web.Request) -> web.Response:
    crypto: Crypto = request.app["crypto"]
    pool: SessionPool = request.app["pool"]
    stats: Stats = request.app["stats"]
    upstream_connector: TCPConnector = request.app["upstream_connector"]
    now = time.time()

    stats.polls_served += 1

    body = await request.read()
    stats.bytes_in += len(body)

    try:
        client_id, frames = decode_batch(crypto, body)
    except Exception as e:
        logger.warning("batch decode failed: %s", e)
        return web.Response(status=400, body=b"bad request")

    response_frames: list[Frame] = []

    for f in frames:
        if f.flags & FlagSYN:
            session = await pool.get_or_create(
                f.session_id, flags=f.flags, target=f.target, now=now,
            )
            session.seq = f.seq + 1
            if _UpstreamSemaphore is not None and _UpstreamSemaphore.locked():
                response_frames.append(Frame(
                    session_id=f.session_id, seq=0,
                    flags=FlagRST, target="", payload=b"busy",
                ))
                continue
            asyncio.create_task(
                _upstream_worker(request.app, session, upstream_connector, now),
                name=f"upstream-{f.session_id.hex()[:8]}",
            )
        elif f.flags & FlagFIN:
            s = await pool.get(f.session_id)
            if s:
                s.enqueue_rx(f, now)
        elif f.flags & FlagACK:
            s = await pool.get(f.session_id)
            if s:
                s.seq = max(s.seq, f.seq + 1)
        elif f.flags & FlagRST:
            await pool.remove(f.session_id)
        elif f.flags == 0:
            s = await pool.get(f.session_id)
            if s:
                s.enqueue_rx(f, now)

    # Long-poll with adaptive coalesce
    wait_deadline = now + LongPollWindow
    active_start = now
    last_gc = now

    while True:
        remaining = wait_deadline - time.time()
        if remaining <= 0:
            break

        need_sleep = coalesceWindowPoll

        sessions = pool.snapshot_all()
        for s in sessions:
            if s.tx_q:
                snapshot = await pool.drain_tx_limited_txn(s.session_id, 8)
                if snapshot.frame_count:
                    response_frames.extend(snapshot.frames)
                    need_sleep = coalesceWindowBusy

        now_t = time.time()
        if response_frames:
            break
        elif need_sleep == coalesceWindowBusy:
            pass  # already set
        elif now_t - active_start < ActiveDrainWindow:
            need_sleep = coalesceWindow
        else:
            need_sleep = coalesceWindowPoll

        sleep_for = min(need_sleep, remaining)
        if sleep_for > 0:
            await asyncio.sleep(sleep_for)

        if now_t - last_gc >= 30:
            purged = await pool.cleanup_expired(idleSessionTimeout, now_t)
            if purged:
                logger.debug("GC purged %d expired sessions", purged)
            last_gc = now_t

    if response_frames:
        try:
            resp_body = encode_batch(crypto, client_id, response_frames)
        except Exception as e:
            logger.error("encode response batch failed: %s", e)
            raw = bytearray(ClientIDLen + 2)
            raw[:ClientIDLen] = client_id
            raw[ClientIDLen:ClientIDLen+2] = len(response_frames).to_bytes(2, "big")
            for f in response_frames:
                fdata = marshal(f)
                raw.extend(len(fdata).to_bytes(4, "big"))
                raw.extend(fdata)
            resp_body = raw
        stats.bytes_out += len(resp_body)
        return web.Response(body=resp_body, content_type="text/plain")
    else:
        resp_body = client_id + (0).to_bytes(2, "big")
        stats.bytes_out += len(resp_body)
        return web.Response(body=resp_body, content_type="text/plain")


async def _upstream_worker(
    app: web.Application,
    session,
    upstream_connector: TCPConnector,
    start_time: float,
):
    """Manage one upstream TCP connection with Event-based notification."""
    stats: Stats = app["stats"]

    if not session.target:
        logger.debug("upstream: no target for session %s", session.id.hex()[:8])
        return

    host, port = _parse_target(session.target)

    try:
        reader, writer = await asyncio.open_connection(
            host, port, connector=upstream_connector,
        )
        sock = writer.transport.get_extra_info("socket")
        if sock:
            try:
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            except (OSError, AttributeError):
                pass
        stats.active_conns += 1
    except (OSError, asyncio.TimeoutError) as e:
        logger.debug("upstream dial %s failed: %s", session.target, e)
        session.enqueue_tx(Frame(
            session_id=session.id, seq=0,
            flags=FlagRST, target="", payload=f"dial error: {e}".encode(),
        ), time.time())
        return

    async def read_upstream():
        """Event-driven read from upstream."""
        try:
            buf_size = upstreamReadBuf
            seq = 0
            while True:
                data = await reader.read(buf_size)
                if not data:
                    break
                f = Frame(session_id=session.id, seq=seq, flags=0, target="", payload=data)
                session.enqueue_tx(f, time.time())
                seq += 1
        except (OSError, asyncio.TimeoutError):
            pass
        finally:
            session.enqueue_tx(Frame(
                session_id=session.id, seq=0,
                flags=FlagFIN, target="", payload=b"",
            ), time.time())

    async def write_downstream():
        """Event-driven write to upstream, triggered by rx_event."""
        try:
            while True:
                await session.rx_event.wait()
                session.rx_event.clear()
                while session.rx_q:
                    f = session.rx_q.popleft()
                    if f.flags & (FlagRST | FlagFIN):
                        writer.close()
                        return
                    if f.payload:
                        try:
                            writer.write(f.payload)
                            await writer.drain()
                        except OSError:
                            return
        except (OSError, asyncio.TimeoutError):
            pass
        finally:
            try:
                writer.close()
            except OSError:
                pass

    tasks = [
        asyncio.create_task(read_upstream()),
        asyncio.create_task(write_downstream()),
    ]
    done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    for t in tasks:
        if not t.done():
            t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)

    try:
        writer.close()
    except OSError:
        pass

    stats.active_conns -= 1


def _parse_target(target: str) -> tuple[str, int]:
    if ":" in target:
        host, port_str = target.rsplit(":", 1)
        try:
            return host, int(port_str)
        except ValueError:
            return target, 0
    return target, 0
