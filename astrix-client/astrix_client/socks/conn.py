# Astrix — by UNDEAD (https://github.com/itsund3ad)
# VirtualConn: adapter between SOCKS5 transport and Session pool.
# Optimized: Event-based notification instead of polling sleep,
# zero-copy data flow, batched writes.

import asyncio
import logging
import time
from typing import Optional

from astrix_client.session.session import SessionPool, Session
from astrix_client.frame.frame import Frame, FlagSYN, FlagFIN, FlagRST

logger = logging.getLogger("socks.conn")


class VirtualConn:
    """Bridges a SOCKS5 TCP connection ↔ Astrix Session.

    Uses asyncio.Event for wakeup instead of polling sleep,
    resulting in near-zero CPU overhead when idle.
    """

    __slots__ = (
        "_pool", "_transport", "_target", "_closed",
        "_session", "_read_task", "_tx_queue",
    )

    def __init__(
        self,
        pool: SessionPool,
        transport: asyncio.Transport,
        target_host: str,
        target_port: int,
    ):
        self._pool = pool
        self._transport = transport
        self._target = f"{target_host}:{target_port}"
        self._closed = False
        self._session: Optional[Session] = None
        self._read_task: Optional[asyncio.Task] = None
        self._tx_queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=4096)

    async def open(self) -> bytes:
        """Create session, send SYN, start event-driven read loop."""
        loop = asyncio.get_running_loop()
        t = time.monotonic_ns()
        session_id = loop.time().to_bytes(4, "big") + b"\x00" * 4 + t.to_bytes(8, "big")

        now = time.time()
        self._session = await self._pool.get_or_create(
            session_id,
            flags=FlagSYN,
            target=self._target,
            now=now,
        )
        syn = Frame(
            session_id=session_id,
            seq=0,
            flags=FlagSYN,
            target=self._target,
            payload=b"",
        )
        self._session.enqueue_tx(syn, now)

        self._read_task = asyncio.create_task(
            self._read_loop(),
            name=f"vconn-rx-{session_id.hex()[:8]}",
        )

        # Start tx drain task (SOCKS5 data → session tx queue)
        asyncio.create_task(
            self._tx_drain_loop(),
            name=f"vconn-tx-{session_id.hex()[:8]}",
        )

        logger.debug("VirtualConn opened: %s -> %s", session_id.hex()[:12], self._target)
        return session_id

    async def write(self, data: bytes):
        """Enqueue data for batched delivery to session tx queue."""
        if self._closed or self._session is None:
            return
        await self._tx_queue.put(data)

    async def _tx_drain_loop(self):
        """Drain data from tx_queue and enqueue to session as frames."""
        session = self._session
        if session is None:
            return
        try:
            while not self._closed:
                data = await self._tx_queue.get()
                seq = session.seq
                session.seq += 1
                f = Frame(
                    session_id=session.session_id,
                    seq=seq,
                    flags=0,
                    target="",
                    payload=data,
                )
                now = time.time()
                session.enqueue_tx(f, now)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error("tx_drain error: %s", e)

    async def _read_loop(self):
        """Event-driven: wait for rx_event, deliver frames to SOCKS5 transport."""
        session = self._session
        if session is None:
            return
        try:
            while not self._closed:
                await session.rx_event.wait()
                if self._closed:
                    break
                session.rx_event.clear()

                while session.rx_q and not self._closed:
                    f = session.rx_q.popleft()
                    if f.flags & (FlagFIN | FlagRST):
                        logger.debug("VirtualConn got FIN/RST, closing")
                        await self.close()
                        return
                    if f.payload and self._transport and not self._transport.is_closing():
                        self._transport.write(f.payload)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error("VirtualConn read loop error: %s", e)
        finally:
            if not self._closed:
                await self.close()

    async def close(self):
        if self._closed:
            return
        self._closed = True
        if self._read_task:
            self._read_task.cancel()
            try:
                await self._read_task
            except asyncio.CancelledError:
                pass
        if self._session:
            fin = Frame(
                session_id=self._session.session_id,
                seq=0,
                flags=FlagFIN,
                target="",
                payload=b"",
            )
            now = time.time()
            self._session.enqueue_tx(fin, now)
            self._session.rx_event.set()
        if self._transport and not self._transport.is_closing():
            self._transport.close()

    @property
    def session_id(self) -> Optional[bytes]:
        return self._session.session_id if self._session else None

    @property
    def target(self) -> str:
        return self._target
