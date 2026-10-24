"""Machinery for the suite's "no test may reach the network" guard.

Lives outside `conftest.py` for the reason `redis_guard.py` gives: pytest
imports `conftest.py` as the top-level module `conftest`, so a test importing
`backend.tests.conftest` would get a second, different exception class.

A test may reach loopback, a Unix socket, and the servers the environment names
for the suite (the test Postgres). Anything else, an LLM server above all, is
refused before a packet leaves, and the attempt fails the test in teardown.
The fixture that installs this is `network_contact_guard` in `conftest.py`.

The seams are Python's: `socket.socket.connect` and its siblings, which
`socket.create_connection`, urllib3, httpx and asyncio all reach, and the
resolver functions on the `socket` module, which they look up at call time.
A library that connects from C (libpq, behind psycopg) is seen at its name
lookup only, and a subprocess is not seen at all. The container network the
suite runs on is the backstop for both.
"""

from __future__ import annotations

import ipaddress
import os
import socket
import traceback
from collections.abc import Callable, Iterable
from urllib.parse import urlsplit

# Variables naming a server the suite may reach. The suite has no Redis
# server: the Redis guard refuses every connection before a socket opens.
TEST_SERVER_URL_ENVS = ("NOJOIN_TEST_POSTGRES_URL",)

_INET_FAMILIES = frozenset({socket.AF_INET, socket.AF_INET6})
_REAL_CONNECT = socket.socket.connect
_REAL_CONNECT_EX = socket.socket.connect_ex
_REAL_SENDTO = socket.socket.sendto
_REAL_GETADDRINFO = socket.getaddrinfo
_REAL_GETHOSTBYNAME = socket.gethostbyname
_REAL_GETHOSTBYNAME_EX = socket.gethostbyname_ex
_REAL_GETHOSTBYADDR = socket.gethostbyaddr

# How this module's own frames start in a formatted stack.
_GUARD_FRAME = f'File "{__file__}"'

# Addresses the allowed servers resolved to, kept for the session: a client
# resolves the name and then connects to the address, and a pool may connect
# again in a later test.
_server_addresses: set[str] = set()


class NetworkContactedInTests(RuntimeError):
    """Raised instead of resolving or connecting to an outside host in a test.

    Not an ``OSError``, for the reason ``RedisContactedInTests`` gives: urllib3
    and redis-py retry connection errors, and nobody retries a
    ``RuntimeError``, so the refusal propagates on the first attempt.
    """


def server_hostnames(environ: dict[str, str] | None = None) -> frozenset[str]:
    """The hosts named by the suite's server URLs, lowercased.

    A libpq URL may list several hosts (``postgresql://u@a:5432,b:5433/db``),
    which ``urlsplit`` cannot split, so each is parsed on its own.
    """
    environ = os.environ if environ is None else environ
    names: set[str] = set()
    for variable in TEST_SERVER_URL_ENVS:
        url = environ.get(variable)
        if not url:
            continue
        netloc = urlsplit(url).netloc.rpartition("@")[2]
        for part in netloc.split(","):
            host = urlsplit(f"//{part}").hostname if part else None
            if host:
                names.add(_normal_name(host))
    return frozenset(names)


def call_site(stack: list[str]) -> str:
    """The frames that name the code which reached out, from a formatted stack.

    The application's frames when there are any, as the Redis guard reports;
    otherwise the test's own, for a test that opened the connection itself.
    The guard's frames are dropped from both.
    """
    stack = [frame for frame in stack if _GUARD_FRAME not in frame]
    app = [f for f in stack if "/backend/" in f and "/backend/tests/" not in f]
    tests = [f for f in stack if "/backend/tests/" in f]
    return "".join(app or tests or stack[-8:])


def _normal_name(name: str) -> str:
    return name.strip().rstrip(".").lower()


def _address(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(host.partition("%")[0])
    except ValueError:
        return None


def _is_loopback(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    mapped = getattr(ip, "ipv4_mapped", None)
    return ip.is_loopback or bool(mapped and mapped.is_loopback)


class NetworkGuard:
    """Decides each lookup and connection, and records the refused ones."""

    def __init__(self, server_names: Iterable[str]) -> None:
        self.server_names = frozenset(server_names)
        self.attempts: list[str] = []
        self.hosts: list[str] = []

    def install(self, setattr_: Callable[[object, str, object], None]) -> None:
        """Patch the socket seams with ``setattr_`` (a monkeypatch's)."""
        guard = self

        def connect(sock, address):
            guard.check_socket_address(sock, address)
            return _REAL_CONNECT(sock, address)

        def connect_ex(sock, address):
            guard.check_socket_address(sock, address)
            return _REAL_CONNECT_EX(sock, address)

        def sendto(sock, data, *args):
            guard.check_socket_address(sock, args[-1] if args else None)
            return _REAL_SENDTO(sock, data, *args)

        def getaddrinfo(host, port, *args, **kwargs):
            guard.check_name(host, port)
            answer = _REAL_GETADDRINFO(host, port, *args, **kwargs)
            guard.learn(host, (item[4][0] for item in answer if item[4]))
            return answer

        def gethostbyname(host):
            guard.check_name(host, None)
            return _REAL_GETHOSTBYNAME(host)

        def gethostbyname_ex(host):
            guard.check_name(host, None)
            return _REAL_GETHOSTBYNAME_EX(host)

        def gethostbyaddr(host):
            guard.check_name(host, None)
            return _REAL_GETHOSTBYADDR(host)

        setattr_(socket.socket, "connect", connect)
        setattr_(socket.socket, "connect_ex", connect_ex)
        setattr_(socket.socket, "sendto", sendto)
        setattr_(socket, "getaddrinfo", getaddrinfo)
        setattr_(socket, "gethostbyname", gethostbyname)
        setattr_(socket, "gethostbyname_ex", gethostbyname_ex)
        setattr_(socket, "gethostbyaddr", gethostbyaddr)

    def check_socket_address(self, sock: socket.socket, address) -> None:
        # A Unix socket, a socketpair and anything else off IP leave the host
        # by no route of their own.
        if sock.family not in _INET_FAMILIES or not isinstance(address, tuple):
            return
        host, port = str(address[0]), address[1]
        if not self.address_allowed(host):
            self.refuse("connect to", host, port)

    def check_name(self, host, port) -> None:
        """A lookup: of a name, or of an address on the way to connecting."""
        if isinstance(host, bytes):
            host = host.decode("idna", errors="replace")
        # No host is a passive lookup for a local bind, never an outside one.
        if not host:
            return
        if _address(host) is not None:
            if not self.address_allowed(host):
                self.refuse("connect to", host, port)
            return
        if not self.name_allowed(host):
            self.refuse("resolve", host, port)

    def name_allowed(self, host: str) -> bool:
        name = _normal_name(host)
        return (
            name == "localhost"
            or name.endswith(".localhost")
            or name in self.server_names
        )

    def address_allowed(self, host: str) -> bool:
        ip = _address(host)
        if ip is None:
            return self.name_allowed(host)
        if _is_loopback(ip) or str(ip) in _server_addresses:
            return True
        # The address may be a server's that this session has not resolved
        # yet, as when the test Postgres was reached before the guard was up.
        for name in self.server_names:
            try:
                answer = _REAL_GETADDRINFO(name, None)
            except OSError:
                continue
            self.learn(name, (item[4][0] for item in answer if item[4]))
        return str(ip) in _server_addresses

    def learn(self, host, addresses: Iterable[str]) -> None:
        if isinstance(host, bytes):
            host = host.decode("idna", errors="replace")
        if isinstance(host, str) and _normal_name(host) in self.server_names:
            _server_addresses.update(
                str(ip) for ip in map(_address, addresses) if ip is not None
            )

    def refuse(self, action: str, host: str, port) -> None:
        target = host if port in (None, 0, "") else f"{host} port {port}"
        self.hosts.append(f"{action} {target}")
        self.attempts.append(call_site(traceback.format_stack()))
        raise NetworkContactedInTests(
            f"A test tried to {action} {target}. Tests reach only loopback, "
            "Unix sockets and the test Postgres; stub the client instead."
        )

    def failure_message(self) -> str | None:
        """What the test fails with in teardown, or None when nothing was refused."""
        if not self.attempts:
            return None
        return (
            f"Test reached outside the suite's network ({len(self.attempts)} "
            f"attempt(s): {', '.join(dict.fromkeys(self.hosts))}). Stub the "
            "client, or the resolver for a URL check, instead. Last call site:"
            f"\n\n{self.attempts[-1]}"
        )

    def clear(self) -> None:
        """Acknowledge the attempts so far, as a test of the guard does."""
        self.attempts.clear()
        self.hosts.clear()
