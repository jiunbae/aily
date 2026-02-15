"""Dashboard statistics endpoint.

GET /api/stats returns aggregate counts for sessions, messages, hosts,
and platform configuration status.
"""

from __future__ import annotations

from aiohttp import web

from dashboard import db
from dashboard.api import json_ok

# Statuses to count
SESSION_STATUSES = ("active", "idle", "closed", "orphan", "unreachable")


async def get_stats(request: web.Request) -> web.Response:
    """GET /api/stats

    Returns:
    {
        "sessions": {"total": 15, "active": 5, "idle": 3, ...},
        "messages": {"total": 1234, "last_24h": 89},
        "hosts": ["dev-a", "dev-b"],
        "platforms": {"discord": true, "slack": false}
    }
    """
    # Session counts by status
    status_counts: dict[str, int] = {}
    for status in SESSION_STATUSES:
        row = await db.fetchone(
            "SELECT COUNT(*) as cnt FROM sessions WHERE status = ?",
            (status,),
        )
        status_counts[status] = row["cnt"] if row else 0

    total_sessions = sum(status_counts.values())

    # Message counts
    total_msg_row = await db.fetchone(
        "SELECT COUNT(*) as cnt FROM messages"
    )
    total_messages = total_msg_row["cnt"] if total_msg_row else 0

    recent_msg_row = await db.fetchone(
        """SELECT COUNT(*) as cnt FROM messages
           WHERE timestamp > datetime('now', '-24 hours')"""
    )
    recent_messages = recent_msg_row["cnt"] if recent_msg_row else 0

    # Active hosts (distinct hosts with active sessions)
    host_rows = await db.fetchall(
        "SELECT DISTINCT host FROM sessions WHERE status = 'active'"
    )
    hosts = [row["host"] for row in host_rows if row["host"]]

    # Platform configuration status
    config = request.app.get("config")
    platforms = {
        "discord": bool(config and config.discord_bot_token),
        "slack": bool(config and config.slack_bot_token),
    }

    # All configured SSH hosts (not just active ones)
    all_hosts = config.ssh_hosts if config else []

    return json_ok(
        {
            "sessions": {"total": total_sessions, **status_counts},
            "messages": {"total": total_messages, "last_24h": recent_messages},
            "hosts": hosts,
            "configured_hosts": all_hosts,
            "platforms": platforms,
        }
    )
