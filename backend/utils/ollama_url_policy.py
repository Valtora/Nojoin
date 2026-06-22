from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

FORBIDDEN_INTERNAL_HOSTNAMES = frozenset(
    {
        "localhost",
        "socket-proxy",
        "db",
        "redis",
        "worker",
        "api",
        "frontend",
        "nginx",
    }
)
LOCALHOST_HOSTNAMES = frozenset({"localhost", "127.0.0.1", "::1"})
SPECIAL_LOCAL_HOSTNAMES = frozenset({"host.docker.internal"})


class OllamaURLValidationError(ValueError):
    """Raised when an Ollama API URL fails outbound SSRF policy checks."""


def validate_ollama_api_url(
    url: str | None,
    *,
    allow_private: bool = False,
    trusted_url: str | None = None,
) -> str:
    """Validate and normalise an Ollama base URL."""
    if not url:
        raise OllamaURLValidationError("Ollama API URL is required.")

    normalised_url, hostname = _normalise_url(url)
    trusted_normalised = _normalise_optional_url(trusted_url)

    if trusted_normalised and normalised_url == trusted_normalised:
        return normalised_url

    lowered_hostname = hostname.lower()
    if lowered_hostname in FORBIDDEN_INTERNAL_HOSTNAMES:
        raise OllamaURLValidationError(
            "Internal Nojoin service hostnames are not allowed for Ollama."
        )

    if lowered_hostname in LOCALHOST_HOSTNAMES:
        raise OllamaURLValidationError("Loopback Ollama hostnames are not allowed.")

    if lowered_hostname in SPECIAL_LOCAL_HOSTNAMES:
        if allow_private:
            return normalised_url
        raise OllamaURLValidationError(
            "This Ollama hostname is only allowed when explicitly configured installation-wide."
        )

    addresses = _resolve_hostname(hostname)
    if not addresses:
        raise OllamaURLValidationError("Could not resolve Ollama hostname.")

    for address in addresses:
        ip_obj = ipaddress.ip_address(address)
        if allow_private:
            if _is_never_allowed_address(ip_obj):
                raise OllamaURLValidationError(
                    "Loopback, link-local, multicast, unspecified, and reserved Ollama addresses are not allowed."
                )
            continue

        if not ip_obj.is_global:
            raise OllamaURLValidationError(
                "Private or reserved Ollama addresses are not allowed."
            )

    return normalised_url


def _normalise_optional_url(url: str | None) -> str | None:
    if not url:
        return None
    normalised_url, _ = _normalise_url(url)
    return normalised_url


def _normalise_url(url: str) -> tuple[str, str]:
    parsed = urlparse(url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or not parsed.hostname
    ):
        raise OllamaURLValidationError("Invalid Ollama API URL format.")

    hostname = parsed.hostname.strip()
    if not hostname:
        raise OllamaURLValidationError("Invalid Ollama API URL format.")

    return url.rstrip("/"), hostname


def _resolve_hostname(hostname: str) -> list[str]:
    try:
        ip_obj = ipaddress.ip_address(hostname)
    except ValueError:
        try:
            addr_info = socket.getaddrinfo(hostname, None, proto=socket.IPPROTO_TCP)
        except socket.gaierror as exc:
            raise OllamaURLValidationError(
                "Could not resolve Ollama hostname."
            ) from exc
        addresses = {item[4][0] for item in addr_info if item[4]}
        return sorted(addresses)

    return [str(ip_obj)]


def _is_never_allowed_address(ip_obj: ipaddress._BaseAddress) -> bool:
    if ip_obj.is_loopback or ip_obj.is_link_local or ip_obj.is_multicast:
        return True
    if ip_obj.is_unspecified or ip_obj.is_reserved:
        return True
    if getattr(ip_obj, "is_site_local", False):
        return True
    return False
