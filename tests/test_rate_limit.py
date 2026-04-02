"""Tests for dashboard/rate_limit.py — token bucket rate limiter middleware."""

from __future__ import annotations

import pytest

from dashboard.rate_limit import _RateBucket, _buckets


# ===================================================================
# _RateBucket unit tests
# ===================================================================

class TestRateBucket:
    def test_consume_within_limit(self):
        bucket = _RateBucket(max_tokens=5, window=60)
        for _ in range(5):
            assert bucket.consume() is True

    def test_consume_exceeds_limit(self):
        bucket = _RateBucket(max_tokens=3, window=60)
        for _ in range(3):
            assert bucket.consume() is True
        assert bucket.consume() is False

    def test_bucket_refills_over_time(self, monkeypatch):
        """Simulate time passing so tokens refill."""
        import time as _time
        from dashboard import rate_limit

        bucket = _RateBucket(max_tokens=2, window=60)
        # Drain all tokens
        assert bucket.consume() is True
        assert bucket.consume() is True
        assert bucket.consume() is False

        # Simulate 60s passing (full refill)
        bucket.last_refill -= 60
        assert bucket.consume() is True


# ===================================================================
# Integration tests via the aiohttp test client
# ===================================================================

@pytest.mark.asyncio
async def test_requests_within_limit_succeed(client):
    """Normal requests within the limit should get 200."""
    resp = await client.get("/healthz")
    assert resp.status == 200


@pytest.mark.asyncio
async def test_exceeding_limit_returns_429(client):
    """Exceeding the rate limit should return 429."""
    # /api/sessions has a 30 req/min limit
    for i in range(30):
        resp = await client.get("/api/sessions")
        assert resp.status == 200, f"Request {i+1} failed unexpectedly"

    # The 31st request should be rate-limited
    resp = await client.get("/api/sessions")
    assert resp.status == 429


@pytest.mark.asyncio
async def test_retry_after_header_present(client):
    """429 responses must include a Retry-After header."""
    # Drain the /api/sessions bucket (30 req/min)
    for _ in range(30):
        await client.get("/api/sessions")

    resp = await client.get("/api/sessions")
    assert resp.status == 429
    assert "Retry-After" in resp.headers
    assert int(resp.headers["Retry-After"]) > 0


@pytest.mark.asyncio
async def test_429_response_body(client):
    """429 response body should contain RATE_LIMITED error code."""
    for _ in range(30):
        await client.get("/api/sessions")

    resp = await client.get("/api/sessions")
    assert resp.status == 429
    body = await resp.json()
    assert body["error"]["code"] == "RATE_LIMITED"


@pytest.mark.asyncio
async def test_bucket_expiry_reset(client):
    """After manually resetting the bucket, requests should succeed again."""
    # Drain the bucket
    for _ in range(30):
        await client.get("/api/sessions")

    resp = await client.get("/api/sessions")
    assert resp.status == 429

    # Simulate bucket refill by manipulating last_refill
    for key, bucket in _buckets.items():
        bucket.last_refill -= 120  # go back 2 minutes
        bucket.tokens = bucket.max_tokens  # force full refill

    resp = await client.get("/api/sessions")
    assert resp.status == 200


@pytest.mark.asyncio
async def test_healthz_bypasses_rate_limit(client):
    """Health check endpoint should never be rate-limited."""
    for _ in range(200):
        resp = await client.get("/healthz")
        assert resp.status == 200


@pytest.mark.asyncio
async def test_different_paths_have_separate_buckets(client):
    """Different API path prefixes should have independent rate limits."""
    # Drain /api/sessions (30 req/min)
    for _ in range(30):
        await client.get("/api/sessions")

    resp = await client.get("/api/sessions")
    assert resp.status == 429

    # /healthz should still work (bypasses rate limit)
    resp = await client.get("/healthz")
    assert resp.status == 200
