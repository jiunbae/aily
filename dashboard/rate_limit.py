"""Simple in-memory rate limiting middleware."""

from __future__ import annotations

import logging
import os
import time

from aiohttp import web

logger = logging.getLogger(__name__)

# Rate limit config: max requests per window (seconds)
_RATE_LIMITS: dict[str, tuple[int, int]] = {
    "/api/hooks/": (60, 60),  # 60 req/min for webhooks
    "/api/sessions": (30, 60),  # 30 req/min for session API
    "/api/": (60, 60),  # 60 req/min for other API
}
_DEFAULT_LIMIT = (120, 60)  # 120 req/min default


class _RateBucket:
    __slots__ = ("tokens", "last_refill", "max_tokens", "refill_rate", "last_access")

    def __init__(self, max_tokens: int, window: int) -> None:
        self.max_tokens = max_tokens
        self.tokens = float(max_tokens)
        self.last_refill = time.monotonic()
        self.refill_rate = max_tokens / window
        self.last_access = time.monotonic()

    def consume(self) -> bool:
        now = time.monotonic()
        elapsed = now - self.last_refill
        self.tokens = min(self.max_tokens, self.tokens + elapsed * self.refill_rate)
        self.last_refill = now
        self.last_access = now
        if self.tokens >= 1:
            self.tokens -= 1
            return True
        return False


# Per-IP buckets keyed by (ip, path_prefix)
_buckets: dict[tuple[str, str], _RateBucket] = {}

_MAX_BUCKETS = 10_000
_BUCKET_TTL = 600  # 10 minutes
_last_cleanup = 0.0


def _cleanup_stale_buckets() -> None:
    """Remove buckets that haven't been accessed within the TTL."""
    global _last_cleanup
    now = time.monotonic()
    if now - _last_cleanup < 60:  # cleanup at most every 60s
        return
    _last_cleanup = now
    stale_keys = [
        k for k, b in _buckets.items()
        if now - b.last_access > _BUCKET_TTL
    ]
    for k in stale_keys:
        del _buckets[k]
    if stale_keys:
        logger.debug("Cleaned up %d stale rate limit buckets", len(stale_keys))


def _get_limit(path: str) -> tuple[str, int, int]:
    """Find the matching rate limit for a path."""
    for prefix, (max_req, window) in _RATE_LIMITS.items():
        if path.startswith(prefix):
            return prefix, max_req, window
    return "", _DEFAULT_LIMIT[0], _DEFAULT_LIMIT[1]


def _client_ip(request: web.Request) -> str:
    """Extract client IP, respecting X-Forwarded-For only when TRUST_PROXY is set.

    When TRUST_PROXY is not set (default), only ``request.remote`` is used,
    preventing clients from spoofing their IP via the X-Forwarded-For header.
    """
    if os.environ.get("TRUST_PROXY", "").lower() in ("1", "true", "yes"):
        xff = request.headers.get("X-Forwarded-For", "")
        if xff:
            return xff.split(",")[0].strip()
    peer = request.remote
    return peer or "unknown"


@web.middleware
async def rate_limit_middleware(
    request: web.Request,
    handler: web.RequestHandler,
) -> web.StreamResponse:
    """Token bucket rate limiter per client IP."""
    path = request.path

    # Skip rate limiting for health checks and static files
    if path == "/healthz" or path.startswith("/static/") or path == "/ws":
        return await handler(request)

    _cleanup_stale_buckets()

    ip = _client_ip(request)
    prefix, max_req, window = _get_limit(path)
    key = (ip, prefix)

    if len(_buckets) >= _MAX_BUCKETS and key not in _buckets:
        # Too many tracked clients, reject to prevent OOM
        logger.warning("Rate limiter at capacity (%d buckets)", len(_buckets))
        return web.json_response(
            {"error": {"code": "RATE_LIMITED", "message": "Too many requests"}},
            status=429,
            headers={"Retry-After": "60"},
        )

    if key not in _buckets:
        _buckets[key] = _RateBucket(max_req, window)

    bucket = _buckets[key]
    if not bucket.consume():
        logger.warning("Rate limited: %s %s from %s", request.method, path, ip)
        return web.json_response(
            {"error": {"code": "RATE_LIMITED", "message": "Too many requests"}},
            status=429,
            headers={"Retry-After": str(window)},
        )

    return await handler(request)
