"""Transaction lifecycle regressions for the shared SQLite connection."""

from __future__ import annotations

import pytest

from dashboard import db


@pytest.mark.asyncio
async def test_nested_batch_reuses_outer_transaction(client):
    async with db.batch():
        await db.execute(
            "INSERT INTO events (event_type, payload, created_at) VALUES (?, ?, ?)",
            ("outer", "{}", db.now_iso()),
        )
        async with db.batch():
            await db.execute(
                "INSERT INTO events (event_type, payload, created_at) VALUES (?, ?, ?)",
                ("inner", "{}", db.now_iso()),
            )

    row = await db.fetchone("SELECT COUNT(*) AS count FROM events")
    assert row == {"count": 2}
