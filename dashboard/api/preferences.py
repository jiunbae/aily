"""User preferences API.

GET  /api/preferences          -- Get all preferences
PUT  /api/preferences          -- Set preferences (merge)
GET  /api/preferences/{key}    -- Get single preference
PUT  /api/preferences/{key}    -- Set single preference

Preferences are stored in the kv table with a "pref:" key prefix.
Since this is a single-user system, no user differentiation is needed.
"""

from __future__ import annotations

import logging

from aiohttp import web

from dashboard import db
from dashboard.api import error_response, json_ok, read_json_object

logger = logging.getLogger(__name__)

# Default preferences
DEFAULTS: dict[str, str] = {
    "theme": "dark",
    "sidebar_collapsed": "false",
    "message_font_size": "14",
    "notifications_enabled": "true",
    "auto_scroll": "true",
    "show_system_messages": "true",
    "compact_mode": "false",
}

# Allowed preference keys (prevent arbitrary kv pollution)
ALLOWED_KEYS = frozenset(DEFAULTS.keys())

PREF_PREFIX = "pref:"


async def get_preferences(request: web.Request) -> web.Response:
    """GET /api/preferences

    Returns all user preferences merged with defaults.
    """
    rows = await db.fetchall(
        "SELECT key, value FROM kv WHERE key LIKE 'pref:%'"
    )

    prefs = dict(DEFAULTS)
    for row in rows:
        key = row["key"].removeprefix(PREF_PREFIX)
        if key in ALLOWED_KEYS:
            prefs[key] = row["value"]

    return json_ok({"preferences": prefs})


async def set_preferences(request: web.Request) -> web.Response:
    """PUT /api/preferences

    Merge provided preferences. Only known keys are accepted.
    Request body: {"theme": "light", "compact_mode": "true"}
    """
    body = await read_json_object(request)

    now = db.now_iso()
    updated_keys: list[str] = []

    for key, value in body.items():
        if key not in ALLOWED_KEYS:
            continue
        value_str = str(value)

        await db.execute(
            """INSERT INTO kv (key, value, updated)
               VALUES (?, ?, ?)
               ON CONFLICT(key) DO UPDATE SET value = ?, updated = ?""",
            (f"{PREF_PREFIX}{key}", value_str, now, value_str, now),
        )
        updated_keys.append(key)

    return json_ok({"updated": updated_keys})


async def get_preference(request: web.Request) -> web.Response:
    """GET /api/preferences/{key}"""
    key = request.match_info["key"]

    if key not in ALLOWED_KEYS:
        return error_response(404, "UNKNOWN_KEY", f"Unknown preference: {key}")

    row = await db.fetchone(
        "SELECT value FROM kv WHERE key = ?", (f"{PREF_PREFIX}{key}",)
    )

    value = row["value"] if row else DEFAULTS.get(key, "")
    return json_ok({"key": key, "value": value})


async def set_preference(request: web.Request) -> web.Response:
    """PUT /api/preferences/{key}

    Request body: {"value": "light"}
    """
    key = request.match_info["key"]

    if key not in ALLOWED_KEYS:
        return error_response(404, "UNKNOWN_KEY", f"Unknown preference: {key}")

    body = await read_json_object(request)

    value = str(body.get("value", ""))
    now = db.now_iso()

    await db.execute(
        """INSERT INTO kv (key, value, updated)
           VALUES (?, ?, ?)
           ON CONFLICT(key) DO UPDATE SET value = ?, updated = ?""",
        (f"{PREF_PREFIX}{key}", value, now, value, now),
    )

    return json_ok({"key": key, "value": value})
