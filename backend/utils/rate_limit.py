import asyncio
import hashlib
import logging
import os
import time
from collections import defaultdict, deque
from typing import Optional

import redis.asyncio as redis
from fastapi import HTTPException, Request, status

logger = logging.getLogger(__name__)

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
RATE_LIMIT_KEY_PREFIX = "nojoin:ratelimit"

_redis_client: Optional[redis.Redis] = None
_fallback_lock = asyncio.Lock()
_fallback_windows: dict[str, deque[float]] = defaultdict(deque)


import ipaddress
import socket

_dns_cache: dict[str, tuple[list[ipaddress.IPv4Address | ipaddress.IPv6Address], float]] = {}
DNS_CACHE_TTL = 60.0


def _parse_trusted_proxies(proxies_str: str) -> list:
    if not proxies_str:
        return []
    
    trusted = []
    for item in proxies_str.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            if "/" in item:
                trusted.append(ipaddress.ip_network(item, strict=False))
            else:
                trusted.append(ipaddress.ip_address(item))
            continue
        except ValueError:
            pass
        trusted.append(item)
    return trusted


def _mask_hostname(hostname: str) -> str:
    if not hostname:
        return ""
    if len(hostname) <= 2:
        return "***"
    return f"{hostname[0]}***{hostname[-1]}"


def _resolve_hostname(hostname: str) -> list:
    now = time.time()
    if hostname in _dns_cache:
        cached_ips, expires = _dns_cache[hostname]
        if now < expires:
            return cached_ips
            
    ips = []
    try:
        infos = socket.getaddrinfo(hostname, None)
        for info in infos:
            ip_str = info[4][0]
            try:
                ips.append(ipaddress.ip_address(ip_str))
            except ValueError:
                pass
    except socket.gaierror as exc:
        masked = _mask_hostname(hostname)
        logger.warning(
            f"Failed to resolve trusted proxy hostname {masked}: "
            f"[Errno {exc.errno}] {exc.strerror}"
        )
        
    _dns_cache[hostname] = (ips, now + DNS_CACHE_TTL)
    return ips


def _is_ip_in_trusted(ip_str: str, trusted_list: list) -> bool:
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
        
    for target in trusted_list:
        if isinstance(target, (ipaddress.IPv4Address, ipaddress.IPv6Address)):
            if ip == target:
                return True
        elif isinstance(target, (ipaddress.IPv4Network, ipaddress.IPv6Network)):
            if ip in target:
                return True
        elif isinstance(target, str):
            resolved_ips = _resolve_hostname(target)
            if ip in resolved_ips:
                return True
    return False


def get_client_address(request: Request) -> str:
    if not request.client or not request.client.host:
        return "unknown"

    direct_peer = request.client.host
    
    trusted_proxies_env = os.getenv("NOJOIN_TRUSTED_PROXIES", "127.0.0.1,::1,nginx")
    trusted_list = _parse_trusted_proxies(trusted_proxies_env)
    
    if not _is_ip_in_trusted(direct_peer, trusted_list):
        return direct_peer

    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        ips = [ip.strip() for ip in forwarded_for.split(",") if ip.strip()]
        client_ip = direct_peer
        for hop in reversed(ips):
            if _is_ip_in_trusted(client_ip, trusted_list):
                client_ip = hop
            else:
                break
        return client_ip

    x_real_ip = request.headers.get("x-real-ip")
    if x_real_ip:
        return x_real_ip.strip()

    return direct_peer



def _build_rate_limit_key(
    namespace: str,
    client_address: str,
    discriminator: Optional[str] = None,
) -> str:
    key = f"{RATE_LIMIT_KEY_PREFIX}:{namespace}:{client_address}"
    if discriminator:
        digest = hashlib.sha256(discriminator.strip().lower().encode("utf-8")).hexdigest()[:16]
        key = f"{key}:{digest}"
    return key


async def _get_redis() -> Optional[redis.Redis]:
    global _redis_client
    if _redis_client is None:
        try:
            _redis_client = redis.from_url(REDIS_URL, decode_responses=True)
            await _redis_client.ping()
        except Exception as exc:
            logger.warning(f"Could not connect to Redis for rate limiting: {exc}")
            _redis_client = None
            return None
    return _redis_client


async def _consume_redis_window(key: str, window_seconds: int) -> Optional[tuple[int, int]]:
    client = await _get_redis()
    if client is None:
        return None

    try:
        count = int(await client.incr(key))
        if count == 1:
            await client.expire(key, window_seconds)

        ttl = int(await client.ttl(key))
        if ttl < 0:
            ttl = window_seconds

        return count, ttl
    except Exception as exc:
        logger.warning(f"Redis-backed rate limiting failed for {key}: {exc}")
        return None


async def _consume_fallback_window(key: str, window_seconds: int) -> tuple[int, int]:
    now = time.time()

    async with _fallback_lock:
        window = _fallback_windows[key]
        cutoff = now - window_seconds

        while window and window[0] <= cutoff:
            window.popleft()

        window.append(now)
        retry_after = window_seconds
        if window:
            retry_after = max(1, int(window_seconds - (now - window[0])))

        if not window:
            _fallback_windows.pop(key, None)

        return len(window), retry_after


async def enforce_rate_limit(
    request: Request,
    namespace: str,
    limit: int,
    window_seconds: int,
    *,
    discriminator: Optional[str] = None,
    detail: str = "Too many requests. Please try again later.",
) -> None:
    client_address = get_client_address(request)
    key = _build_rate_limit_key(namespace, client_address, discriminator)

    result = await _consume_redis_window(key, window_seconds)
    if result is None:
        result = await _consume_fallback_window(key, window_seconds)

    count, retry_after = result
    if count > limit:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=detail,
            headers={"Retry-After": str(retry_after)},
        )


_fallback_concurrency: dict[str, int] = defaultdict(int)


async def acquire_concurrency_limit(key: str, limit: int) -> bool:
    client = await _get_redis()
    if client is not None:
        try:
            val = await client.incr(key)
            if val > limit:
                await client.decr(key)
                return False
            return True
        except Exception as exc:
            logger.warning(f"Redis concurrency check failed: {exc}")
            # Fall through to in-memory fallback

    async with _fallback_lock:
        val = _fallback_concurrency[key]
        if val >= limit:
            return False
        _fallback_concurrency[key] = val + 1
        return True


async def release_concurrency_limit(key: str):
    client = await _get_redis()
    if client is not None:
        try:
            await client.decr(key)
            # Clean up key if it drops to 0 or below to prevent memory leak
            val = await client.get(key)
            if val is not None and int(val) <= 0:
                await client.delete(key)
            return
        except Exception as exc:
            logger.warning(f"Redis concurrency release failed: {exc}")
            # Fall through to in-memory fallback

    async with _fallback_lock:
        if key in _fallback_concurrency:
            _fallback_concurrency[key] -= 1
            if _fallback_concurrency[key] <= 0:
                _fallback_concurrency.pop(key, None)


import contextlib


@contextlib.asynccontextmanager
async def enforce_upload_concurrency(
    request: Request,
    namespace: str,
    user_id: str,
    limit: int,
):
    key = f"{RATE_LIMIT_KEY_PREFIX}:concurrency:{namespace}:{user_id}"
    acquired = await acquire_concurrency_limit(key, limit)
    if not acquired:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many concurrent uploads. Please wait for active uploads to finish."
        )
    try:
        yield
    finally:
        await release_concurrency_limit(key)