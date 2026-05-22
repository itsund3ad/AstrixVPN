# Astrix — by UNDEAD (https://github.com/itsund3ad)
# Domain-fronted HTTPS client with connection pooling and SNI rotation.
# Optimized: reuses ClientSession, persistent connections, keep-alive.

import asyncio
import logging
import ssl
from typing import Optional
from urllib.parse import urlparse

import aiohttp

logger = logging.getLogger("carrier")

# Acceptable Google edge IPs
GOOGLE_IPS = ("216.239.38.120",)


class _ResolvedConnector(aiohttp.TCPConnector):
    """A TCPConnector that forces all connections to a specific IP:port
    regardless of the requested hostname.

    This is the key to domain fronting: we dial a Google edge IP while
    presenting a different SNI name inside the TLS handshake.
    """

    def __init__(self, google_ip: str, google_port: int, sni_host: str, **kwargs):
        self._google_ip = google_ip
        self._google_port = google_port
        self._sni_host = sni_host
        super().__init__(**kwargs)

    async def _resolve_host(self, host: str, port: int, family: int = 0) -> list:
        return [
            aiohttp.connector.AddrInfo(
                family=self._family,
                type=self._type,
                proto=0,
                canonname=self._sni_host,
                sockaddr=(self._google_ip, self._google_port),
            )
        ]


class FrontedClient:
    """Domain-fronted HTTP client with persistent connection pools.

    Manages one connection pool per SNI host. Requests are distributed
    round-robin across pools for multi-throttle-bucket throughput.
    """

    def __init__(
        self,
        google_ip: str = "216.239.38.120",
        sni_hosts: Optional[list[str]] = None,
        timeout: float = 120.0,
        max_connections: int = 20,
    ):
        if sni_hosts is None:
            sni_hosts = ["www.google.com"]

        self._google_ip = google_ip.split(":")[0] if ":" in google_ip else google_ip
        self._google_port = 443
        self._sni_hosts = sni_hosts
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._sni_index = 0

        # Pre-create all connectors with unique SNI per pool
        ssl_ctx = ssl.create_default_context()
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode = ssl.CERT_REQUIRED

        self._connectors: list[_ResolvedConnector] = []
        for sni in sni_hosts:
            conn = _ResolvedConnector(
                google_ip=self._google_ip,
                google_port=self._google_port,
                sni_host=sni,
                ssl=ssl_ctx,
                force_close=False,       # keep-alive connections
                limit=max_connections,
                limit_per_host=max_connections,
                enable_cleanup_closed=True,
                ttl_dns_cache=600,
            )
            self._connectors.append(conn)

        # Persistent sessions per pool
        self._sessions: list[aiohttp.ClientSession] = []
        for conn in self._connectors:
            self._sessions.append(
                aiohttp.ClientSession(
                    connector=conn,
                    timeout=self._timeout,
                )
            )

    def _next_session(self) -> tuple[aiohttp.ClientSession, str]:
        idx = self._sni_index % len(self._sessions)
        self._sni_index += 1
        return self._sessions[idx], self._sni_hosts[idx]

    async def post(
        self,
        url: str,
        body: bytes,
        headers: Optional[dict] = None,
    ) -> tuple[int, bytes]:
        """POST body through domain fronting.

        Returns (status_code, response_body).
        """
        session, sni = self._next_session()
        parsed = urlparse(url)
        if headers is None:
            headers = {}
        headers.setdefault("Content-Type", "text/plain")
        headers["Host"] = parsed.netloc

        try:
            async with session.post(url, data=body, headers=headers) as resp:
                resp_body = await resp.read()
                return resp.status, resp_body
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as e:
            return 0, str(e).encode()

    async def get(
        self,
        url: str,
        params: Optional[dict] = None,
    ) -> tuple[int, bytes]:
        """GET through domain fronting."""
        session, sni = self._next_session()
        parsed = urlparse(url)
        headers = {"Host": parsed.netloc}

        try:
            async with session.get(url, params=params, headers=headers) as resp:
                resp_body = await resp.read()
                return resp.status, resp_body
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as e:
            return 0, str(e).encode()

    async def close(self):
        for s in self._sessions:
            await s.close()
        for c in self._connectors:
            await c.close()
