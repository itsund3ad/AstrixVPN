# Astrix — by UNDEAD (https://github.com/itsund3ad)
# Long-poll carrier with adaptive batch sizing, pipeline mode,
# endpoint quality scoring, health monitoring, and watchdog.

import asyncio
import logging
import time
import statistics
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Optional

from astrix_client.config.client import ClientConfig, ScriptKeyEntry
from astrix_client.frame.frame import (
    Frame,
    FlagSYN,
)
from astrix_client.crypto.crypto import Crypto, encode_batch, decode_batch
from astrix_client.session.session import SessionPool, DrainSnapshot
from astrix_client.carrier.fronting import FrontedClient

logger = logging.getLogger("carrier")

# Performance tuning
MaxFramePayload = 262144
pollIdleSleep = 0.010
pollTimeout = 120.0
workersPerEndpoint = 3
idleSlotsPerBucket = 2

# Health monitor
_HealthPollInterval = 30.0
_MaxConsecutiveFailures = 5
_BackoffBase = 3.0
_BackoffMax = 3600.0

# Watchdog
_WatchdogInterval = 15.0
_WatchdogMaxIdle = 120.0

# Quality scoring
_QualityMinSamples = 3
_QualityStaleAfter = 10

# Adaptive batch sizing
_BatchSizeMin = 1
_BatchSizeMax = 64
_BatchSizeDefault = 8
_BatchRTTWindow = 5  # samples

_DiagDrainInterval = 5.0


@dataclass(slots=True)
class EndpointState:
    id: str
    account: str
    base_url: str
    consecutive_failures: int = 0
    is_dead: bool = False
    last_error: str = ""
    total_polls: int = 0
    total_bytes_sent: int = 0
    total_bytes_recv: int = 0
    avg_rtt_ms: float = 0.0
    backoff: float = _BackoffBase
    rtt_samples: list[float] = field(default_factory=list)
    quality_score: float = 100.0
    batch_size: int = _BatchSizeDefault

    def record_rtt(self, rtt_ms: float):
        self.rtt_samples.append(rtt_ms)
        if len(self.rtt_samples) > 20:
            self.rtt_samples.pop(0)
        if len(self.rtt_samples) >= _QualityMinSamples:
            m = statistics.median(self.rtt_samples)
            # Score: lower RTT = higher score. 100 - (rtt / 10)
            self.quality_score = max(10.0, 100.0 - m / 10.0)
        self.avg_rtt_ms = 0.9 * self.avg_rtt_ms + 0.1 * rtt_ms

    @property
    def score(self) -> float:
        return self.quality_score


class Carrier:
    """Long-poll carrier with adaptive pipeline and quality scoring."""

    def __init__(
        self,
        config: ClientConfig,
        session_pool: SessionPool,
        conn_callback: Callable,
    ):
        self._config = config
        self._pool = session_pool
        self._conn_callback = conn_callback

        self._crypto = Crypto.from_hex_key(config.tunnel_key)
        self._client_id = config.client_id_bytes()

        self._endpoints: list[EndpointState] = []
        for entry in config.script_keys:
            if isinstance(entry, str):
                self._endpoints.append(
                    EndpointState(
                        id=entry,
                        account="",
                        base_url=f"https://script.google.com/macros/s/{entry}/exec",
                    )
                )
            elif isinstance(entry, ScriptKeyEntry):
                base = f"https://script.google.com/macros/s/{entry.id}/exec"
                self._endpoints.append(
                    EndpointState(
                        id=entry.id,
                        account=entry.account or "",
                        base_url=base,
                    )
                )

        sni_hosts = config.sni if isinstance(config.sni, list) else [config.sni]
        self._fronting = FrontedClient(
            google_ip=config.google_host,
            sni_hosts=sni_hosts,
            timeout=pollTimeout,
        )

        self._running = False
        self._tasks: list[asyncio.Task] = []
        self._health_task: Optional[asyncio.Task] = None
        self._watchdog_task: Optional[asyncio.Task] = None
        self._diag_task: Optional[asyncio.Task] = None
        self._start_time: float = 0.0
        self._last_activity: float = 0.0

    async def start(self):
        self._running = True
        self._start_time = time.monotonic()
        self._last_activity = self._start_time

        for ep in self._endpoints:
            for i in range(workersPerEndpoint):
                t = asyncio.create_task(
                    self._worker_loop(ep, i),
                    name=f"worker-{ep.id[:8]}-{i}",
                )
                self._tasks.append(t)

        self._health_task = asyncio.create_task(
            self._health_loop(),
            name="health-monitor",
        )
        self._watchdog_task = asyncio.create_task(
            self._watchdog_loop(),
            name="watchdog",
        )
        self._diag_task = asyncio.create_task(
            self._diag_loop(),
            name="diag-reporter",
        )

        logger.info(
            "Carrier started: %d endpoints, %d workers, adaptive batch enabled",
            len(self._endpoints),
            len(self._tasks),
        )

    async def stop(self):
        self._running = False
        for t in [self._diag_task, self._watchdog_task, self._health_task]:
            if t:
                t.cancel()
                try:
                    await t
                except asyncio.CancelledError:
                    pass
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        await self._fronting.close()
        logger.info("Carrier stopped")

    def _best_endpoint(self) -> Optional[EndpointState]:
        """Select endpoint with highest quality score, excluding dead ones."""
        alive = [ep for ep in self._endpoints if not ep.is_dead]
        if not alive:
            return None
        return max(alive, key=lambda ep: ep.score)

    async def _worker_loop(self, ep: EndpointState, worker_id: int):
        """Continuous drain→encode→post→decode→dispatch loop with pipeline."""
        while self._running:
            if ep.is_dead:
                await asyncio.sleep(ep.backoff)
                continue

            try:
                # --- Pipeline stage 1: Drain frames ---
                frames, await_count = await self._drain_all(ep, worker_id)
                if not frames:
                    # Wait for session activity with timeout
                    has_activity = await self._pool.wait_for_activity(pollIdleSleep)
                    if not has_activity:
                        continue
                    frames, await_count = await self._drain_all(ep, worker_id)
                    if not frames:
                        continue

                self._last_activity = time.monotonic()

                # --- Pipeline stage 2: Encode (CPU-bound, uses pool) ---
                t0 = time.monotonic()
                body = encode_batch(self._crypto, self._client_id, frames)
                encode_time = time.monotonic() - t0

                # --- Pipeline stage 3: POST (network-bound) ---
                t1 = time.monotonic()
                status, resp_body = await self._fronting.post(
                    ep.base_url, body
                )
                elapsed = (time.monotonic() - t1) * 1000

                # Update endpoint stats
                ep.total_polls += 1
                ep.total_bytes_sent += len(body)
                ep.record_rtt(elapsed)

                if status == 200:
                    ep.consecutive_failures = 0
                    if resp_body:
                        t2 = time.monotonic()
                        cid, resp_frames = decode_batch(
                            self._crypto, resp_body
                        )
                        decode_time = time.monotonic() - t2
                        await self._dispatch_response_frames(resp_frames)
                        ep.total_bytes_recv += len(resp_body)

                        # Adaptive batch sizing based on RTT
                        if resp_frames and len(resp_frames) >= ep.batch_size * 0.8:
                            ep.batch_size = min(_BatchSizeMax, ep.batch_size + 2)
                        elif not resp_frames and ep.batch_size > _BatchSizeMin:
                            ep.batch_size = max(_BatchSizeMin, ep.batch_size - 1)

                elif status in (302, 429):
                    ep.consecutive_failures += 1
                    ep.last_error = f"HTTP {status}"
                    logger.warning(
                        "%s HTTP %d, failures=%d",
                        ep.id[:12], status, ep.consecutive_failures,
                    )
                    await asyncio.sleep(1.0)
                else:
                    ep.consecutive_failures += 1
                    ep.last_error = f"HTTP {status}"
                    failed_sids = {f.session_id for f in frames if f.flags & FlagSYN}
                    for sid in failed_sids:
                        await self._pool.remove(sid)
                    logger.warning(
                        "%s HTTP %d, failures=%d — %d sessions dropped",
                        ep.id[:12], status, ep.consecutive_failures,
                        len(failed_sids),
                    )

            except asyncio.CancelledError:
                break

    async def _drain_all(
        self, ep: EndpointState, worker_id: int
    ) -> tuple[list[Frame], int]:
        """Collect frames from active sessions with adaptive batch size."""
        frames: list[Frame] = []
        total_size = 0
        await_count = 0
        max_drain = MaxFramePayload
        max_frames = ep.batch_size

        sids = list(self._pool._sessions.keys())
        if not sids:
            return frames, 0

        for i in range(worker_id, len(sids), workersPerEndpoint):
            sid = sids[i]
            snapshot = await self._pool.drain_tx_limited_txn(sid, max_frames)
            if not snapshot.frame_count:
                continue
            for f in snapshot.frames:
                elen = f.encoded_len()
                if total_size + elen > max_drain:
                    await self._pool.rollback_drain(snapshot, time.time())
                    break
                frames.append(f)
                total_size += elen
                await_count += 1

        return frames, await_count

    async def _dispatch_response_frames(self, frames: list[Frame]):
        now = time.time()
        for f in frames:
            s = await self._pool.get(f.session_id)
            if s:
                s.enqueue_rx(f, now)

    async def _health_loop(self):
        """Monitor endpoint health, revive dead endpoints, auto-select best."""
        while self._running:
            await asyncio.sleep(_HealthPollInterval)
            now_t = time.monotonic()
            for ep in self._endpoints:
                if ep.consecutive_failures >= _MaxConsecutiveFailures and not ep.is_dead:
                    ep.is_dead = True
                    logger.warning(
                        "%s dead after %d failures, retry in %.0fs",
                        ep.id[:12], ep.consecutive_failures, ep.backoff,
                    )
                    asyncio.create_task(self._revive_endpoint(ep))
                elif ep.is_dead:
                    asyncio.create_task(self._revive_endpoint(ep))

            best = self._best_endpoint()
            if best:
                logger.debug("Best endpoint: %s (score=%.1f, rtt=%.0fms)",
                             best.id[:12], best.score, best.avg_rtt_ms)

    async def _revive_endpoint(self, ep: EndpointState):
        await asyncio.sleep(ep.backoff)
        if not self._running:
            return
        ep.is_dead = False
        ep.consecutive_failures = 0
        ep.backoff = min(ep.backoff * 2, _BackoffMax)
        logger.info("%s revived, next backoff=%.0fs", ep.id[:12], ep.backoff)

    async def _watchdog_loop(self):
        """Watchdog: if no activity for _WatchdogMaxIdle, force reconnect."""
        while self._running:
            await asyncio.sleep(_WatchdogInterval)
            idle = time.monotonic() - self._last_activity
            if idle > _WatchdogMaxIdle and self._pool.active_count() > 0:
                logger.warning(
                    "Watchdog: %ds idle with %d sessions — forcing reconnect",
                    int(idle), self._pool.active_count(),
                )
                for ep in self._endpoints:
                    if not ep.is_dead:
                        ep.consecutive_failures = _MaxConsecutiveFailures

    async def _diag_loop(self):
        """Periodic diagnostics."""
        while self._running:
            await asyncio.sleep(_DiagDrainInterval)
            if not logger.isEnabledFor(logging.DEBUG):
                continue
            active = self._pool.active_count()
            tx_buf = self._pool.tx_buffered_bytes()
            alive = sum(1 for ep in self._endpoints if not ep.is_dead)
            total = len(self._endpoints)
            best = self._best_endpoint()
            best_score = best.score if best else 0
            uptime = time.monotonic() - self._start_time
            logger.debug(
                "sessions=%d tx_buf=%.1fKB endpoints=%d/%d best_score=%.0f uptime=%.0fs",
                active, tx_buf / 1024, alive, total, best_score, uptime,
            )

    def get_diagnostics(self) -> dict:
        now = time.monotonic()
        result = {
            "uptime": now - self._start_time,
            "running": self._running,
            "endpoints": [],
            "sessions_all": [],
        }
        for ep in self._endpoints:
            result["endpoints"].append({
                "id": ep.id[:16],
                "account": ep.account,
                "alive": not ep.is_dead,
                "polls": ep.total_polls,
                "bytes_sent": ep.total_bytes_sent,
                "bytes_recv": ep.total_bytes_recv,
                "avg_rtt_ms": round(ep.avg_rtt_ms, 1),
                "quality_score": round(ep.score, 1),
                "batch_size": ep.batch_size,
                "failures": ep.consecutive_failures,
                "last_error": ep.last_error,
            })
        for s in self._pool.snapshot_all():
            result["sessions_all"].append({
                "id": s.session_id.hex()[:12],
                "target": s.target,
                "seq": s.seq,
                "tx_frames": len(s.tx_q),
                "rx_frames": len(s.rx_q),
                "tx_bytes": s.tx_bytes,
                "rx_bytes": s.rx_bytes,
            })
        return result
