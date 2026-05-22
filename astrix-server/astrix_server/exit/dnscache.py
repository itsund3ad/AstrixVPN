# Astrix — by UNDEAD (https://github.com/itsund3ad)
# DNS cache with TTL for the exit server.

import asyncio
import time
from dataclasses import dataclass


dnsCacheTTL = 300.0  # 5 minutes


@dataclass
class DNSEntry:
    ip: str
    expires: float


class DNSCache:
    """Simple DNS cache that keeps hostname→IP resolutions for dnsCacheTTL.

    The exit server's DNS resolver produces a single IP per hostname
    (the first A record from the system resolver).  This cache reuses
    that result so repeated connections to the same target (CDN, video
    host) skip the resolver round-trip.
    """

    def __init__(self):
        self._entries: dict[str, DNSEntry] = {}
        self._lock = asyncio.Lock()

    async def get(self, host: str) -> str | None:
        async with self._lock:
            entry = self._entries.get(host)
            if entry is None:
                return None
            if time.time() > entry.expires:
                del self._entries[host]
                return None
            return entry.ip

    async def set(self, host: str, ip: str):
        async with self._lock:
            self._entries[host] = DNSEntry(
                ip=ip,
                expires=time.time() + dnsCacheTTL,
            )
