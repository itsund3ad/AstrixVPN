# Astrix — by UNDEAD (https://github.com/itsund3ad)
# SOCKS5 asyncio server with RFC 1929 username/password auth.
# Optimized: TCP_NODELAY/TCP_QUICKACK on accepted sockets, zero-copy reads.

import asyncio
import logging
import socket
import struct
from typing import Optional

from astrix_client.socks.conn import VirtualConn

logger = logging.getLogger("socks")

SOCKS_VERSION = 0x05

# Auth methods
METHOD_NO_AUTH = 0x00
METHOD_USER_PASS = 0x02
METHOD_NO_ACCEPTABLE = 0xFF

# Address types
ATYP_IPV4 = 0x01
ATYP_DOMAINNAME = 0x03
ATYP_IPV6 = 0x04

# Commands
CMD_CONNECT = 0x01

# Reply codes
REP_SUCCESS = 0x00
REP_GENERAL_FAILURE = 0x01
REP_CONNECTION_NOT_ALLOWED = 0x02
REP_NET_UNREACHABLE = 0x03
REP_HOST_UNREACHABLE = 0x04
REP_CONNECTION_REFUSED = 0x05
REP_TTL_EXPIRED = 0x06
REP_COMMAND_NOT_SUPPORTED = 0x07
REP_ADDRESS_TYPE_NOT_SUPPORTED = 0x08


def _apply_socket_opts(transport: asyncio.Transport):
    """Apply TCP_NODELAY and TCP_QUICKACK for minimal latency."""
    sock = transport.get_extra_info("socket")
    if sock is None:
        return
    try:
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    except (OSError, AttributeError):
        pass
    try:
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_QUICKACK, 1)
    except (OSError, AttributeError):
        pass


class SOCKS5Protocol(asyncio.Protocol):
    """SOCKS5 connection handler — one instance per accepted connection."""

    def __init__(
        self,
        pool,
        conn_callback,
        *,
        auth_required: bool = False,
        users: Optional[dict] = None,
    ):
        self._pool = pool
        self._conn_callback = conn_callback
        self._auth_required = auth_required
        self._users = users or {}
        self._transport: Optional[asyncio.Transport] = None
        self._virtual: Optional[VirtualConn] = None
        self._buffer = bytearray()
        self._state = 0
        # 0: handshake, 1: auth, 2: command, 3: established
        self._target_host = ""
        self._target_port = 0

    def connection_made(self, transport: asyncio.Transport):
        self._transport = transport
        _apply_socket_opts(transport)
        peername = transport.get_extra_info("peername")
        logger.debug("SOCKS5 connection from %s", peername)

    def connection_lost(self, exc: Optional[Exception]):
        if self._virtual:
            self._virtual.close()
        self._transport = None

    def data_received(self, data: bytes):
        self._buffer.extend(data)
        if self._state == 0:
            self._handle_handshake()
        elif self._state == 1:
            self._handle_auth()
        elif self._state == 2:
            self._handle_command()
        elif self._state == 3:
            self._handle_tunnel_data(data)

    def _write(self, data: bytes):
        if self._transport and not self._transport.is_closing():
            self._transport.write(data)

    def _handle_handshake(self):
        buf = self._buffer
        if len(buf) < 3:
            return
        ver = buf[0]
        if ver != SOCKS_VERSION:
            self._write(bytes([0xFF, METHOD_NO_ACCEPTABLE]))
            self._transport.close()
            return
        nmethods = buf[1]
        if len(buf) < 2 + nmethods:
            return
        methods = buf[2 : 2 + nmethods]

        def have(m):
            return m in methods

        if self._auth_required and have(METHOD_USER_PASS):
            self._write(bytes([SOCKS_VERSION, METHOD_USER_PASS]))
            self._state = 1
        elif not self._auth_required and have(METHOD_NO_AUTH):
            self._write(bytes([SOCKS_VERSION, METHOD_NO_AUTH]))
            self._state = 2
        else:
            self._write(bytes([SOCKS_VERSION, METHOD_NO_ACCEPTABLE]))
            self._transport.close()
            return
        self._buffer = bytearray()

    def _handle_auth(self):
        buf = self._buffer
        if len(buf) < 5:
            return
        ver = buf[0]
        if ver != 0x01:
            self._write(bytes([0x01, 0xFF]))
            self._transport.close()
            return
        ulen = buf[1]
        if len(buf) < 2 + ulen + 1:
            return
        uname = buf[2 : 2 + ulen].decode("utf-8", errors="replace")
        off = 2 + ulen
        plen = buf[off]
        off += 1
        if len(buf) < off + plen:
            return
        passwd = buf[off : off + plen].decode("utf-8", errors="replace")

        expected = self._users.get(uname)
        if expected is None or expected != passwd:
            self._write(bytes([0x01, 0xFF]))
            self._transport.close()
            return

        self._write(bytes([0x01, 0x00]))
        self._state = 2
        self._buffer = bytearray()

    def _handle_command(self):
        buf = self._buffer
        if len(buf) < 4:
            return
        ver = buf[0]
        cmd = buf[1]
        if ver != SOCKS_VERSION or cmd != CMD_CONNECT:
            self._write(bytes([SOCKS_VERSION, REP_COMMAND_NOT_SUPPORTED, 0x00, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00]))
            self._transport.close()
            return

        atyp = buf[3]
        host, header_len = self._parse_addr(buf, 4, atyp)
        if host is None:
            return  # need more data

        self._target_host = host
        plen = len(buf)
        if plen < header_len + 2:
            return
        self._target_port = struct.unpack_from(">H", buf, header_len)[0]
        self._buffer = bytearray()

        # Create VirtualConn with pool and hand it to the session manager
        self._virtual = VirtualConn(
            pool=self._pool,
            transport=self._transport,
            target_host=self._target_host,
            target_port=self._target_port,
        )

        if self._transport and not self._transport.is_closing():
            # Send SOCKS5 success response
            bound_addr = self._transport.get_extra_info("sockname") or ("0.0.0.0", 0)
            resp = bytearray()
            resp.append(SOCKS_VERSION)
            resp.append(REP_SUCCESS)
            resp.append(0x00)  # reserved
            if ":" in str(bound_addr[0]):
                resp.append(ATYP_IPV6)
                resp.extend(socket.inet_pton(socket.AF_INET6, bound_addr[0]))
            else:
                resp.append(ATYP_IPV4)
                resp.extend(socket.inet_aton(bound_addr[0]))
            resp.extend(struct.pack(">H", bound_addr[1]))
            self._transport.write(bytes(resp))

        self._state = 3
        # Fire off async connection handling
        cb = self._conn_callback(self._virtual)
        if cb is not None:
            asyncio.create_task(cb, name=f"socks-callback-{self._target_host}")

    def _parse_addr(self, data: bytes, offset: int, atyp: int) -> tuple[Optional[str], int]:
        if atyp == ATYP_IPV4:
            if len(data) < offset + 4:
                return None, 0
            host = socket.inet_ntoa(data[offset : offset + 4])
            return host, offset + 4
        elif atyp == ATYP_IPV6:
            if len(data) < offset + 16:
                return None, 0
            host = socket.inet_ntop(socket.AF_INET6, data[offset : offset + 16])
            return host, offset + 16
        elif atyp == ATYP_DOMAINNAME:
            if len(data) < offset + 1:
                return None, 0
            dlen = data[offset]
            offset += 1
            if len(data) < offset + dlen:
                return None, 0
            host = data[offset : offset + dlen].decode("utf-8", errors="replace")
            return host, offset + dlen
        else:
            return "", offset

    def _handle_tunnel_data(self, data: bytes):
        """Forward SOCKS5 data to the session's tx queue."""
        if self._virtual and not self._virtual._closed:
            asyncio.create_task(
                self._virtual.write(data),
                name="socks-vconn-write",
            )

    def close(self):
        if self._transport and not self._transport.is_closing():
            self._transport.close()


class SOCKS5Server:
    """SOCKS5 asyncio server with TCP_NODELAY on listen socket."""

    def __init__(
        self,
        pool,
        host: str = "127.0.0.1",
        port: int = 1080,
        auth_required: bool = False,
        users: Optional[dict] = None,
        conn_callback=None,
    ):
        self._pool = pool
        self._host = host
        self._port = port
        self._auth_required = auth_required
        self._users = users
        self._conn_callback = conn_callback
        self._server: Optional[asyncio.AbstractServer] = None

    async def start(self):
        loop = asyncio.get_running_loop()

        def factory():
            return SOCKS5Protocol(
                self._pool,
                self._conn_callback,
                auth_required=self._auth_required,
                users=self._users,
            )

        self._server = await loop.create_server(
            factory,
            host=self._host,
            port=self._port,
        )

        # Enable TCP_NODELAY on the server socket itself
        for sock in self._server.sockets:
            try:
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            except (OSError, AttributeError):
                pass

        logger.info("SOCKS5 listening on %s:%s", self._host, self._port)
        return self._server

    async def stop(self):
        if self._server:
            self._server.close()
            await self._server.wait_closed()
