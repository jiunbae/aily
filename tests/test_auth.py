"""Tests for Bearer token authentication middleware."""

from __future__ import annotations

import pytest


# ---- Auth enforcement (no token configured → block) ----

@pytest.mark.asyncio
async def test_no_token_blocks_requests(noauth_client):
    """When DASHBOARD_TOKEN is empty, all requests should be blocked with 503."""
    resp = await noauth_client.get("/api/sessions")
    assert resp.status == 503
    data = await resp.json()
    assert data["error"]["code"] == "SERVER_ERROR"


@pytest.mark.asyncio
async def test_no_token_blocks_settings(noauth_client):
    """Settings endpoint blocked when no token configured."""
    resp = await noauth_client.get("/api/settings")
    assert resp.status == 503


# ---- Auth configured ----

@pytest.mark.asyncio
async def test_auth_rejects_without_token(auth_client):
    """When DASHBOARD_TOKEN is set and no Bearer header, return 401."""
    resp = await auth_client.get("/api/sessions")
    assert resp.status == 401
    data = await resp.json()
    assert data["error"]["code"] == "UNAUTHORIZED"


@pytest.mark.asyncio
async def test_auth_rejects_wrong_token(auth_client):
    """When Bearer token does not match, return 401."""
    resp = await auth_client.get(
        "/api/sessions",
        headers={"Authorization": "Bearer wrong-token"},
    )
    assert resp.status == 401


@pytest.mark.asyncio
async def test_auth_passes_with_correct_token(auth_client):
    """When correct Bearer token is provided, request succeeds."""
    resp = await auth_client.get(
        "/api/sessions",
        headers={"Authorization": "Bearer test-secret-token"},
    )
    assert resp.status == 200


@pytest.mark.asyncio
async def test_healthz_bypasses_auth(auth_client):
    """/healthz should bypass auth even when token is configured."""
    resp = await auth_client.get("/healthz")
    assert resp.status == 200


@pytest.mark.asyncio
async def test_hooks_event_requires_auth(auth_client):
    """/api/hooks/event should require Bearer token when no HOOK_SECRET set."""
    resp = await auth_client.post(
        "/api/hooks/event",
        json={"type": "ping"},
    )
    assert resp.status == 401


@pytest.mark.asyncio
async def test_hooks_event_with_bearer(auth_client):
    """/api/hooks/event should accept Bearer token."""
    resp = await auth_client.post(
        "/api/hooks/event",
        json={"type": "ping"},
        headers={"Authorization": "Bearer test-secret-token"},
    )
    # Should not be 401 -- the handler may return 202
    assert resp.status != 401


@pytest.mark.asyncio
async def test_install_sh_bypasses_auth(auth_client):
    """/api/install.sh should bypass auth."""
    resp = await auth_client.get("/api/install.sh")
    assert resp.status == 200


@pytest.mark.asyncio
async def test_ws_requires_token_query_param(auth_client):
    """WebSocket endpoint should require ?token= when auth is configured."""
    resp = await auth_client.get("/ws")
    assert resp.status == 401


@pytest.mark.asyncio
async def test_ws_accepts_correct_token_param(auth_client):
    """WebSocket endpoint with correct ?token= should not return 401."""
    resp = await auth_client.get("/ws?token=test-secret-token")
    assert resp.status != 401
