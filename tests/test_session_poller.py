"""Regression tests for session poll reconciliation."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from dashboard import db
from dashboard.services.event_bus import EventBus
from dashboard.workers.session_poller import _poll_once


@pytest.mark.asyncio
async def test_missing_platform_ids_are_reconciled_on_later_poll(client):
    now = db.now_iso()
    await db.execute(
        """INSERT INTO sessions (name, host, status, created_at, updated_at)
           VALUES (?, ?, 'active', ?, ?)""",
        ("alpha", "testhost", now, now),
    )
    session_svc = SimpleNamespace(
        list_all_sessions=AsyncMock(return_value={"testhost": ["alpha"]})
    )
    platform_svc = SimpleNamespace(
        has_discord=True,
        has_slack=True,
        sync_thread_ids_bulk=AsyncMock(
            return_value={
                "alpha": {
                    "discord_thread_id": "discord-1",
                    "slack_thread_ts": "slack-1",
                }
            }
        ),
    )

    await _poll_once(session_svc, platform_svc, EventBus(), cleanup_counter=0)

    row = await db.fetchone("SELECT * FROM sessions WHERE name = ?", ("alpha",))
    assert row["discord_thread_id"] == "discord-1"
    assert row["slack_thread_ts"] == "slack-1"
    platform_svc.sync_thread_ids_bulk.assert_awaited_once_with({"alpha"})
