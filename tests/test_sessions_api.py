"""Tests for /api/sessions endpoints."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from dashboard import db


# ---- GET /api/sessions ----

@pytest.mark.asyncio
async def test_list_sessions_empty(client):
    """GET /api/sessions on a fresh DB should return an empty list."""
    resp = await client.get("/api/sessions")
    assert resp.status == 200
    data = await resp.json()
    assert data["sessions"] == []
    assert data["total"] == 0


@pytest.mark.asyncio
async def test_list_sessions_with_data(client):
    """GET /api/sessions returns sessions that exist in the DB."""
    now = db.now_iso()
    await db.execute(
        "INSERT INTO sessions (name, host, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
        ("test-session", "testhost", "active", now, now),
    )

    resp = await client.get("/api/sessions")
    assert resp.status == 200
    data = await resp.json()
    assert data["total"] == 1
    assert data["sessions"][0]["name"] == "test-session"
    assert data["sessions"][0]["status"] == "active"


@pytest.mark.asyncio
async def test_list_sessions_filter_by_status(client):
    """GET /api/sessions?status=active filters correctly."""
    now = db.now_iso()
    await db.execute(
        "INSERT INTO sessions (name, host, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
        ("active-session", "testhost", "active", now, now),
    )
    await db.execute(
        "INSERT INTO sessions (name, host, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
        ("closed-session", "testhost", "closed", now, now),
    )

    resp = await client.get("/api/sessions?status=active")
    assert resp.status == 200
    data = await resp.json()
    assert data["total"] == 1
    assert data["sessions"][0]["name"] == "active-session"


@pytest.mark.asyncio
async def test_list_sessions_invalid_status(client):
    """GET /api/sessions?status=bogus returns 400."""
    resp = await client.get("/api/sessions?status=bogus")
    assert resp.status == 400
    data = await resp.json()
    assert data["error"]["code"] == "INVALID_STATUS"


# ---- GET /api/sessions/{name} ----

@pytest.mark.asyncio
async def test_get_session_not_found(client):
    """GET /api/sessions/nonexistent returns 404."""
    resp = await client.get("/api/sessions/nonexistent")
    assert resp.status == 404
    data = await resp.json()
    assert data["error"]["code"] == "NOT_FOUND"


@pytest.mark.asyncio
async def test_get_session_found(client):
    """GET /api/sessions/{name} returns the session with message_count."""
    now = db.now_iso()
    await db.execute(
        "INSERT INTO sessions (name, host, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
        ("my-session", "testhost", "active", now, now),
    )

    resp = await client.get("/api/sessions/my-session")
    assert resp.status == 200
    data = await resp.json()
    assert data["session"]["name"] == "my-session"
    assert data["session"]["message_count"] == 0


# ---- POST /api/sessions ----

@pytest.mark.asyncio
async def test_create_session_success(client):
    """POST /api/sessions creates a session (with mocked SSH)."""
    with patch(
        "dashboard.services.session_service.ssh.create_tmux_session",
        new_callable=AsyncMock,
        return_value=True,
    ):
        resp = await client.post(
            "/api/sessions",
            json={"name": "new-session", "host": "testhost"},
        )
    assert resp.status == 201
    data = await resp.json()
    assert data["session"]["name"] == "new-session"
    assert data["session"]["status"] == "active"


@pytest.mark.asyncio
async def test_create_session_missing_name(client):
    """POST /api/sessions with empty name returns 400."""
    resp = await client.post(
        "/api/sessions",
        json={"name": "", "host": "testhost"},
    )
    assert resp.status == 400
    data = await resp.json()
    assert data["error"]["code"] == "MISSING_NAME"


@pytest.mark.asyncio
async def test_create_session_invalid_name(client):
    """POST /api/sessions with special characters returns 400."""
    resp = await client.post(
        "/api/sessions",
        json={"name": "bad name!@#", "host": "testhost"},
    )
    assert resp.status == 400
    data = await resp.json()
    assert data["error"]["code"] == "INVALID_NAME"


@pytest.mark.asyncio
async def test_create_session_invalid_host(client):
    """POST /api/sessions with unknown host returns 400."""
    resp = await client.post(
        "/api/sessions",
        json={"name": "my-session", "host": "unknown-host"},
    )
    assert resp.status == 400
    data = await resp.json()
    assert data["error"]["code"] == "INVALID_HOST"


@pytest.mark.asyncio
async def test_create_session_duplicate(client):
    """POST /api/sessions with existing name returns 409."""
    now = db.now_iso()
    await db.execute(
        "INSERT INTO sessions (name, host, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
        ("dupe-session", "testhost", "active", now, now),
    )

    resp = await client.post(
        "/api/sessions",
        json={"name": "dupe-session", "host": "testhost"},
    )
    assert resp.status == 409
    data = await resp.json()
    assert data["error"]["code"] == "ALREADY_EXISTS"


@pytest.mark.asyncio
async def test_create_session_ssh_failure(client):
    """POST /api/sessions returns 500 when SSH create fails."""
    with patch(
        "dashboard.services.session_service.ssh.create_tmux_session",
        new_callable=AsyncMock,
        return_value=False,
    ):
        resp = await client.post(
            "/api/sessions",
            json={"name": "fail-session", "host": "testhost"},
        )
    assert resp.status == 500
    data = await resp.json()
    assert data["error"]["code"] == "TMUX_CREATE_FAILED"


# ---- DELETE /api/sessions/{name} ----

@pytest.mark.asyncio
async def test_delete_session_not_found(client):
    """DELETE /api/sessions/nonexistent returns 404."""
    resp = await client.delete("/api/sessions/nonexistent")
    assert resp.status == 404


@pytest.mark.asyncio
async def test_delete_session_success(client):
    """DELETE /api/sessions/{name} closes the session (mocked SSH)."""
    now = db.now_iso()
    await db.execute(
        "INSERT INTO sessions (name, host, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
        ("kill-me", "testhost", "active", now, now),
    )

    with patch(
        "dashboard.services.session_service.ssh.has_session",
        new_callable=AsyncMock,
        return_value=True,
    ), patch(
        "dashboard.services.session_service.ssh.kill_tmux_session",
        new_callable=AsyncMock,
        return_value=True,
    ):
        resp = await client.delete("/api/sessions/kill-me")

    assert resp.status == 200
    data = await resp.json()
    assert data["deleted"] is True

    # Verify DB status updated
    session = await db.fetchone("SELECT status FROM sessions WHERE name = ?", ("kill-me",))
    assert session["status"] == "closed"


# ---- PATCH /api/sessions/{name} ----

@pytest.mark.asyncio
async def test_update_session_agent_type(client):
    """PATCH /api/sessions/{name} updates agent_type."""
    now = db.now_iso()
    await db.execute(
        "INSERT INTO sessions (name, host, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
        ("patch-me", "testhost", "active", now, now),
    )

    resp = await client.patch(
        "/api/sessions/patch-me",
        json={"agent_type": "claude"},
    )
    assert resp.status == 200
    data = await resp.json()
    assert data["session"]["agent_type"] == "claude"


@pytest.mark.asyncio
async def test_update_session_not_found(client):
    """PATCH /api/sessions/nonexistent returns 404."""
    resp = await client.patch(
        "/api/sessions/nonexistent",
        json={"agent_type": "claude"},
    )
    assert resp.status == 404


@pytest.mark.asyncio
async def test_update_session_no_fields(client):
    """PATCH /api/sessions/{name} with no valid fields returns 400."""
    now = db.now_iso()
    await db.execute(
        "INSERT INTO sessions (name, host, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
        ("no-fields", "testhost", "active", now, now),
    )

    resp = await client.patch(
        "/api/sessions/no-fields",
        json={"bogus_field": "value"},
    )
    assert resp.status == 400
    data = await resp.json()
    assert data["error"]["code"] == "NO_UPDATES"
