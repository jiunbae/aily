"""Tests for GET /healthz endpoint."""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_healthz_returns_200(client):
    """GET /healthz should return 200 with status ok."""
    resp = await client.get("/healthz")
    assert resp.status == 200
    data = await resp.json()
    assert data["status"] == "ok"
    assert data["database"] == "ok"


@pytest.mark.asyncio
async def test_healthz_bypasses_auth(auth_client):
    """GET /healthz should return 200 even when auth is required and no token provided."""
    resp = await auth_client.get("/healthz")
    assert resp.status == 200
    data = await resp.json()
    assert data["status"] == "ok"
