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


def get_client_address(request: Request) -> str:
    x_real_ip = request.headers.get("x-real-ip")
    if x_real_ip:
        return x_real_ip.split(",")[0].strip()

    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()

    if request.client and request.client.host:
        return request.client.host

    return "unknown"


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