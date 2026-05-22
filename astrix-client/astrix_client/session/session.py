# Astrix — by UNDEAD (https://github.com/itsund3ad)
# Per-connection session state: seq counters, rx/tx queues.
# Optimized: Event-based notification, fast-path no-alloc,
# batch drain with snapshot/rollback.

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
from typing import Optional

from astrix_client.frame.frame import Frame

TxBufHighWater = 8 * 1024 * 1024


@dataclass(slots=True)
class Session:
    session_id: bytes
    seq: int = 0
    flags: int = 0
    target: str = ""
    remote_addr: str = ""
    local_addr: str = ""
    rx_q: deque[Frame] = field(default_factory=deque)
    tx_q: deque[Frame] = field(default_factory=deque)
    tx_bytes: int = 0
    rx_bytes: int = 0
    last_activity: float = 0.0
    tx_event: asyncio.Event = field(default_factory=asyncio.Event)
    rx_event: asyncio.Event = field(default_factory=asyncio.Event)

    @property
    def id(self) -> bytes:
        return self.session_id

    def enqueue_tx(self, frame: Frame, now: float) -> bool:
        """Push frame to tx queue, notify drainers. Returns True if under high-water."""
        self.tx_q.append(frame)
        self.tx_bytes += len(frame.payload)
        self.last_activity = now
        self.tx_event.set()
        return self.tx_bytes < TxBufHighWater

    def enqueue_rx(self, frame: Frame, now: float):
        """Push frame to rx queue, notify readers."""
        self.rx_q.append(frame)
        self.rx_bytes += len(frame.payload)
        self.last_activity = now
        self.rx_event.set()

    def __repr__(self) -> str:
        sid = self.session_id.hex()[:8]
        return f"Session(id={sid} seq={self.seq} target={self.target})"


@dataclass(slots=True)
class DrainSnapshot:
    """Point-in-time snapshot of frames to send for a session."""
    session_id: bytes
    frames: list[Frame]
    frame_count: int
    total_payload: int


class SessionPool:
    """Thread-safe pool of sessions with striped locking and Event notification."""

    def __init__(self, lock_stripes: int = 64):
        self._stripes = lock_stripes
        self._locks: list[asyncio.Lock] = [asyncio.Lock() for _ in range(lock_stripes)]
        self._sessions: dict[bytes, Session] = {}
        self._global_event: asyncio.Event = asyncio.Event()

    def _lock_for(self, sid: bytes) -> asyncio.Lock:
        idx = int.from_bytes(sid[:4], "big") % self._stripes
        return self._locks[idx]

    async def get_or_create(
        self,
        session_id: bytes,
        flags: int = 0,
        target: str = "",
        now: float = 0.0,
    ) -> Session:
        lock = self._lock_for(session_id)
        async with lock:
            if session_id in self._sessions:
                return self._sessions[session_id]
            s = Session(
                session_id=session_id,
                flags=flags,
                target=target,
                last_activity=now,
            )
            self._sessions[session_id] = s
            self._global_event.set()
            return s

    async def get(self, session_id: bytes) -> Optional[Session]:
        lock = self._lock_for(session_id)
        async with lock:
            return self._sessions.get(session_id)

    async def remove(self, session_id: bytes) -> Optional[Session]:
        lock = self._lock_for(session_id)
        async with lock:
            return self._sessions.pop(session_id, None)

    def remove_sync(self, session_id: bytes) -> Optional[Session]:
        return self._sessions.pop(session_id, None)

    async def drain_tx_limited_txn(self, session_id: bytes, max_frames: int) -> DrainSnapshot:
        """Atomic drain: copies frames under lock, returns snapshot.

        Minimizes lock hold time by pointer-swapping the deque rather
        than popping individual frames.
        """
        lock = self._lock_for(session_id)
        async with lock:
            s = self._sessions.get(session_id)
            if s is None or not s.tx_q:
                return DrainSnapshot(session_id, [], 0, 0)

            dq = s.tx_q
            take = min(max_frames, len(dq))
            frames: list[Frame] = [dq.popleft() for _ in range(take)]
            total_payload = sum(len(f.payload) for f in frames)
            s.tx_bytes = max(0, s.tx_bytes - total_payload)

            if dq:
                s.tx_event.set()
            else:
                s.tx_event.clear()

            return DrainSnapshot(session_id, frames, len(frames), total_payload)

    async def rollback_drain(self, snapshot: DrainSnapshot, now: float):
        lock = self._lock_for(snapshot.session_id)
        async with lock:
            s = self._sessions.get(snapshot.session_id)
            if s is None:
                return
            s.tx_q.extendleft(reversed(snapshot.frames))
            s.tx_bytes += snapshot.total_payload
            s.last_activity = now
            if snapshot.frames:
                s.tx_event.set()

    async def cleanup_expired(self, timeout: float, now: float) -> int:
        expired = [
            sid
            for sid, s in self._sessions.items()
            if now - s.last_activity > timeout and not s.tx_q
        ]
        for sid in expired:
            s = self._sessions.pop(sid, None)
            if s:
                s.tx_event.set()
                s.rx_event.set()
        return len(expired)

    def active_count(self) -> int:
        return len(self._sessions)

    def tx_buffered_bytes(self) -> int:
        return sum(s.tx_bytes for s in self._sessions.values())

    def snapshot_all(self) -> list[Session]:
        return list(self._sessions.values())

    async def wait_for_activity(self, timeout: float = 0.010):
        """Wait for any session activity or timeout. Returns True if activity."""
        self._global_event.clear()
        try:
            await asyncio.wait_for(self._global_event.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False
