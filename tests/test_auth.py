"""Tests for Bearer token authentication middleware."""

from __future__ import annotations

import pytest


# ---- No auth configured (dev mode) ----

@pytest.mark.asyncio
async def test_no_auth_allows_all_requests(client):
    """When DASHBOARD_TOKEN is not set, all requests should pass."""
    resp = await client.get("/api/sessions")
    assert resp.status == 200


@pytest.mark.asyncio
async def test_no_auth_allows_settings(client):
    """Settings endpoint accessible without auth when no token configured."""
    resp = await client.get("/api/settings")
    assert resp.status == 200


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
async def test_hooks_event_bypasses_auth(auth_client):
    """/api/hooks/event should bypass auth (internal bridge endpoint)."""
    resp = await auth_client.post(
        "/api/hooks/event",
        json={"type": "ping"},
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
    """WebSocket endpoint should require ?token= when auth is configured.

    Without the token, it should return 401 (not a WebSocket upgrade).
    """
    resp = await auth_client.get("/ws")
    assert resp.status == 401


@pytest.mark.asyncio
async def test_ws_accepts_correct_token_param(auth_client):
    """WebSocket endpoint with correct ?token= should not return 401.

    Note: we cannot fully test WebSocket upgrade with a regular GET,
    but we verify auth does not reject the request.
    """
    resp = await auth_client.get("/ws?token=test-secret-token")
    # Should not be 401. The actual WS handler may fail since
    # this is not a real WebSocket upgrade, but auth should pass.
    assert resp.status != 401
