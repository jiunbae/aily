"""Focused tests for platform synchronization request boundaries."""

from __future__ import annotations

import pytest

from dashboard.services.platform_service import PlatformService


@pytest.mark.asyncio
async def test_incremental_slack_sync_passes_oldest_boundary(monkeypatch):
    service = PlatformService(
        slack_bot_token="xoxb-test",
        slack_channel_id="C123",
    )
    calls = []

    async def fetch_page(channel_id, thread_ts, limit, cursor, oldest):
        calls.append((channel_id, thread_ts, limit, cursor, oldest))
        return [], None

    monkeypatch.setattr(service, "fetch_slack_thread_messages", fetch_page)

    result = await service.fetch_all_slack_thread_messages(
        "C123", "1700000000.000001", after_ts="1700000100.000002"
    )

    assert result == []
    assert calls == [
        ("C123", "1700000000.000001", 200, None, "1700000100.000002")
    ]
