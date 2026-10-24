"""The suite's network guard: no test reaches a host outside the suite (#702).

Each test here makes the contact the guard exists to stop, checks what was
recorded, and clears it, as `test_celery_dispatch_guards.py` does for Redis.
192.0.2.0/24 is a documentation range that routes nowhere.
"""

import asyncio
import socket

import pytest

from backend.processing.llm_backends.ollama import OllamaLLMBackend
from backend.tests.network_guard import (
    NetworkContactedInTests,
    NetworkGuard,
    server_hostnames,
)

OUTSIDE_ADDRESS = "192.0.2.10"


def test_the_refusal_is_not_a_connection_error():
    # urllib3 and redis-py retry those; a RuntimeError fails on the first try.
    assert not issubclass(NetworkContactedInTests, OSError)


def test_a_connection_to_an_outside_address_is_refused(network_contact_guard):
    with pytest.raises(NetworkContactedInTests, match=OUTSIDE_ADDRESS):
        socket.create_connection((OUTSIDE_ADDRESS, 11434), timeout=1)

    assert network_contact_guard.hosts == [f"connect to {OUTSIDE_ADDRESS} port 11434"]
    # No application code made the call, so the test's own frame is the site.
    site = network_contact_guard.attempts[-1]
    assert "test_network_guard.py" in site
    assert 'tests/network_guard.py"' not in site
    network_contact_guard.clear()


def test_a_lookup_of_an_outside_name_is_refused(network_contact_guard):
    with pytest.raises(NetworkContactedInTests, match="llm.example.com"):
        socket.getaddrinfo("llm.example.com", 443)
    with pytest.raises(NetworkContactedInTests):
        socket.gethostbyname("llm.example.com")

    assert network_contact_guard.hosts == [
        "resolve llm.example.com port 443",
        "resolve llm.example.com",
    ]
    network_contact_guard.clear()


def test_an_asyncio_connection_is_refused_at_the_socket(network_contact_guard):
    # asyncio skips the lookup for an address, so this reaches sock.connect.
    async def _open():
        await asyncio.open_connection(OUTSIDE_ADDRESS, 80)

    with pytest.raises(NetworkContactedInTests):
        asyncio.run(_open())

    assert network_contact_guard.hosts == [f"connect to {OUTSIDE_ADDRESS} port 80"]
    network_contact_guard.clear()


def test_a_swallowed_ollama_call_still_fails_the_test(network_contact_guard):
    """The incident this guards against: a test aimed at a real Ollama server.

    ``list_models`` catches every error and lists nothing, so without the
    guard the test passes whether or not a server answered.
    """
    backend = OllamaLLMBackend(
        api_url=f"http://{OUTSIDE_ADDRESS}:11434",
        model="llama3",
        allow_private_api_url=True,
    )

    assert backend.list_models() == []

    assert network_contact_guard.hosts == [f"connect to {OUTSIDE_ADDRESS} port 11434"]
    message = network_contact_guard.failure_message()
    assert message is not None
    assert f"{OUTSIDE_ADDRESS} port 11434" in message
    assert "llm_backends/ollama.py" in message
    assert "_fetch_model_names" in message
    network_contact_guard.clear()
    assert network_contact_guard.failure_message() is None


def test_loopback_and_unix_sockets_are_allowed(network_contact_guard, tmp_path):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]
        socket.create_connection(("127.0.0.1", port), timeout=1).close()
        socket.create_connection(("localhost", port), timeout=1).close()

    path = str(tmp_path / "s")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
        server.bind(path)
        server.listen(1)
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.connect(path)

    assert network_contact_guard.hosts == []


def test_the_test_postgres_hosts_are_allowed():
    names = server_hostnames(
        {
            "NOJOIN_TEST_POSTGRES_URL": (
                "postgresql://postgres:test@Agent-PG:5432,replica.test:5433/nojoin_test"
            )
        }
    )
    assert names == {"agent-pg", "replica.test"}

    guard = NetworkGuard(names)
    guard.check_name("agent-pg.", 5432)
    guard.check_name("replica.test", 5433)
    with pytest.raises(NetworkContactedInTests):
        guard.check_name("db", 5432)
    assert guard.hosts == ["resolve db port 5432"]


def test_no_server_url_allows_only_loopback():
    assert server_hostnames({}) == frozenset()
    assert (
        server_hostnames({"NOJOIN_TEST_POSTGRES_URL": "postgresql:///x"}) == frozenset()
    )
