"""Tests for /api/session-queue endpoints."""

from __future__ import annotations

import pytest

from dashboard import db


# ---- Helper ----

async def _create_queue_item(client, **overrides):
    """Create a queue item via API and return the response data."""
    body = {
        "session_name": "test-session",
        "host": "testhost",
        "platform": "discord",
        "channel_id": "123456",
        "user_message": "hello world",
    }
    body.update(overrides)
    resp = await client.post("/api/session-queue", json=body)
    return resp


# ---- GET /api/session-queue ----

@pytest.mark.asyncio
async def test_list_queue_empty(client):
    """GET /api/session-queue on a fresh DB should return empty list."""
    resp = await client.get("/api/session-queue")
    assert resp.status == 200
    data = await resp.json()
    assert data["items"] == []
    assert data["total"] == 0


@pytest.mark.asyncio
async def test_list_queue_with_data(client):
    """GET /api/session-queue returns items after enqueue."""
    await _create_queue_item(client)

    resp = await client.get("/api/session-queue")
    assert resp.status == 200
    data = await resp.json()
    assert data["total"] == 1
    assert data["items"][0]["session_name"] == "test-session"
    assert data["items"][0]["status"] == "pending"


@pytest.mark.asyncio
async def test_list_queue_filter_status(client):
    """Filter by status works."""
    await _create_queue_item(client)

    resp = await client.get("/api/session-queue?status=completed")
    assert resp.status == 200
    data = await resp.json()
    assert data["total"] == 0

    resp = await client.get("/api/session-queue?status=pending")
    data = await resp.json()
    assert data["total"] == 1


@pytest.mark.asyncio
async def test_list_queue_filter_platform(client):
    """Filter by platform works."""
    await _create_queue_item(client, platform="slack")

    resp = await client.get("/api/session-queue?platform=discord")
    data = await resp.json()
    assert data["total"] == 0

    resp = await client.get("/api/session-queue?platform=slack")
    data = await resp.json()
    assert data["total"] == 1


@pytest.mark.asyncio
async def test_list_queue_invalid_status(client):
    """Invalid status returns 400."""
    resp = await client.get("/api/session-queue?status=invalid")
    assert resp.status == 400


# ---- POST /api/session-queue ----

@pytest.mark.asyncio
async def test_enqueue_success(client):
    """POST /api/session-queue creates a new item."""
    resp = await _create_queue_item(client)
    assert resp.status == 201
    data = await resp.json()
    assert data["item"]["session_name"] == "test-session"
    assert data["item"]["status"] == "pending"
    assert data["item"]["retry_count"] == 0
    assert data["item"]["max_retries"] == 12


@pytest.mark.asyncio
async def test_enqueue_missing_field(client):
    """POST /api/session-queue with missing field returns 400."""
    resp = await client.post("/api/session-queue", json={
        "session_name": "test",
        "host": "testhost",
        # missing platform, channel_id, user_message
    })
    assert resp.status == 400


@pytest.mark.asyncio
async def test_enqueue_invalid_platform(client):
    """POST /api/session-queue with invalid platform returns 400."""
    resp = await client.post("/api/session-queue", json={
        "session_name": "test",
        "host": "testhost",
        "platform": "telegram",
        "channel_id": "123",
        "user_message": "hello",
    })
    assert resp.status == 400


@pytest.mark.asyncio
async def test_enqueue_custom_retries(client):
    """POST /api/session-queue with custom max_retries and retry_interval."""
    resp = await _create_queue_item(client, max_retries=5, retry_interval=600)
    assert resp.status == 201
    data = await resp.json()
    assert data["item"]["max_retries"] == 5
    assert data["item"]["retry_interval"] == 600


# ---- PATCH /api/session-queue/{id} ----

@pytest.mark.asyncio
async def test_update_queue_item(client):
    """PATCH /api/session-queue/{id} updates fields."""
    resp = await _create_queue_item(client)
    item_id = (await resp.json())["item"]["id"]

    resp = await client.patch(f"/api/session-queue/{item_id}", json={
        "status": "completed",
        "retry_count": 3,
    })
    assert resp.status == 200
    data = await resp.json()
    assert data["item"]["status"] == "completed"
    assert data["item"]["retry_count"] == 3


@pytest.mark.asyncio
async def test_update_queue_item_not_found(client):
    """PATCH on non-existent ID returns 404."""
    resp = await client.patch("/api/session-queue/99999", json={"status": "failed"})
    assert resp.status == 404


@pytest.mark.asyncio
async def test_update_queue_item_invalid_status(client):
    """PATCH with invalid status returns 400."""
    resp = await _create_queue_item(client)
    item_id = (await resp.json())["item"]["id"]

    resp = await client.patch(f"/api/session-queue/{item_id}", json={
        "status": "invalid",
    })
    assert resp.status == 400


# ---- DELETE /api/session-queue/{id} ----

@pytest.mark.asyncio
async def test_delete_queue_item(client):
    """DELETE /api/session-queue/{id} removes the item."""
    resp = await _create_queue_item(client)
    item_id = (await resp.json())["item"]["id"]

    resp = await client.delete(f"/api/session-queue/{item_id}")
    assert resp.status == 200
    data = await resp.json()
    assert data["deleted"] is True

    # Verify gone
    resp = await client.get("/api/session-queue")
    data = await resp.json()
    assert data["total"] == 0


@pytest.mark.asyncio
async def test_delete_queue_item_not_found(client):
    """DELETE on non-existent ID returns 404."""
    resp = await client.delete("/api/session-queue/99999")
    assert resp.status == 404


# ---- GET /api/session-queue/stats ----

@pytest.mark.asyncio
async def test_queue_stats_empty(client):
    """Stats on empty queue."""
    resp = await client.get("/api/session-queue/stats")
    assert resp.status == 200
    data = await resp.json()
    assert data["stats"] == {}
    assert data["total"] == 0


@pytest.mark.asyncio
async def test_queue_stats_with_data(client):
    """Stats with items in different states."""
    await _create_queue_item(client)
    await _create_queue_item(client, session_name="another")

    # Mark one as completed
    resp = await client.get("/api/session-queue")
    items = (await resp.json())["items"]
    await client.patch(f"/api/session-queue/{items[0]['id']}", json={"status": "completed"})

    resp = await client.get("/api/session-queue/stats")
    assert resp.status == 200
    data = await resp.json()
    assert data["stats"]["pending"] == 1
    assert data["stats"]["completed"] == 1
    assert data["total"] == 2
