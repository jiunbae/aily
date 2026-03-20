"""Session limit queue CRUD endpoints.

Endpoints:
  GET    /api/session-queue          - List queued messages (filter by status, platform, session_name, due)
  POST   /api/session-queue          - Enqueue a message
  PATCH  /api/session-queue/{id}     - Update queue item (status, retry_count, next_retry_at, last_error)
  DELETE /api/session-queue/{id}     - Cancel/remove queue item
  GET    /api/session-queue/stats    - Queue statistics by status
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone, timedelta

from aiohttp import web

from dashboard import db
from dashboard.api import error_response, json_ok

logger = logging.getLogger(__name__)

VALID_STATUSES = frozenset({"pending", "completed", "failed"})
VALID_PLATFORMS = frozenset({"discord", "slack"})


async def list_queue(request: web.Request) -> web.Response:
    """GET /api/session-queue

    Query params:
        status       - Filter by status (pending, completed, failed)
        platform     - Filter by platform (discord, slack)
        session_name - Filter by session name
        due          - If "true", only return items where next_retry_at <= now
        limit        - Page size (default: 50, max: 200)
        offset       - Pagination offset (default: 0)
    """
    params = request.query
    status_filter = params.get("status")
    platform_filter = params.get("platform")
    session_filter = params.get("session_name")
    due_only = params.get("due", "").lower() == "true"

    try:
        limit = min(int(params.get("limit", "50")), 200)
    except ValueError:
        limit = 50
    try:
        offset = max(int(params.get("offset", "0")), 0)
    except ValueError:
        offset = 0

    where_clauses: list[str] = []
    query_params: list[str] = []

    if status_filter:
        if status_filter not in VALID_STATUSES:
            return error_response(400, "INVALID_STATUS", f"Unknown status: {status_filter}")
        where_clauses.append("status = ?")
        query_params.append(status_filter)

    if platform_filter:
        if platform_filter not in VALID_PLATFORMS:
            return error_response(400, "INVALID_PLATFORM", f"Unknown platform: {platform_filter}")
        where_clauses.append("platform = ?")
        query_params.append(platform_filter)

    if session_filter:
        where_clauses.append("session_name = ?")
        query_params.append(session_filter)

    if due_only:
        now = datetime.now(timezone.utc).isoformat()
        where_clauses.append("next_retry_at <= ?")
        query_params.append(now)

    where_sql = ""
    if where_clauses:
        where_sql = "WHERE " + " AND ".join(where_clauses)

    count_row = await db.fetchone(
        f"SELECT COUNT(*) as cnt FROM session_limit_queue {where_sql}",
        tuple(query_params),
    )
    total = count_row["cnt"] if count_row else 0

    items = await db.fetchall(
        f"""SELECT * FROM session_limit_queue
            {where_sql}
            ORDER BY created_at DESC
            LIMIT ? OFFSET ?""",
        tuple(query_params) + (limit, offset),
    )

    return json_ok({"items": items, "total": total, "limit": limit, "offset": offset})


async def enqueue(request: web.Request) -> web.Response:
    """POST /api/session-queue

    Request body:
        session_name  (required)
        host          (required)
        platform      (required: discord|slack)
        channel_id    (required)
        thread_id     (optional)
        user_message  (required)
        user_name     (optional)
        source_msg_id (optional)
        max_retries   (optional, default 12)
        retry_interval (optional, default 1800)
    """
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return error_response(400, "INVALID_JSON", "Request body must be JSON")

    # Validate required fields
    required = ("session_name", "host", "platform", "channel_id", "user_message")
    for field in required:
        if not body.get(field, "").strip():
            return error_response(400, "MISSING_FIELD", f"'{field}' is required")

    platform = body["platform"].strip()
    if platform not in VALID_PLATFORMS:
        return error_response(400, "INVALID_PLATFORM", f"platform must be: {', '.join(sorted(VALID_PLATFORMS))}")

    now = db.now_iso()
    retry_interval = int(body.get("retry_interval", 1800))
    next_retry = (datetime.now(timezone.utc) + timedelta(seconds=retry_interval)).isoformat()

    cursor = await db.execute(
        """INSERT INTO session_limit_queue
           (session_name, host, platform, channel_id, thread_id, user_message,
            user_name, source_msg_id, status, retry_count, max_retries,
            retry_interval, next_retry_at, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', 0, ?, ?, ?, ?, ?)""",
        (
            body["session_name"].strip(),
            body["host"].strip(),
            platform,
            body["channel_id"].strip(),
            body.get("thread_id", "").strip() or None,
            body["user_message"].strip(),
            body.get("user_name", "").strip() or None,
            body.get("source_msg_id", "").strip() or None,
            int(body.get("max_retries", 12)),
            retry_interval,
            next_retry,
            now,
            now,
        ),
    )

    item = await db.fetchone(
        "SELECT * FROM session_limit_queue WHERE id = ?", (cursor.lastrowid,)
    )
    return json_ok({"item": dict(item) if item else {}}, status=201)


async def update_queue_item(request: web.Request) -> web.Response:
    """PATCH /api/session-queue/{id}

    Update status, retry_count, next_retry_at, last_error, completed_at.
    """
    item_id = request.match_info["id"]
    try:
        item_id = int(item_id)
    except ValueError:
        return error_response(400, "INVALID_ID", "ID must be an integer")

    existing = await db.fetchone(
        "SELECT * FROM session_limit_queue WHERE id = ?", (item_id,)
    )
    if not existing:
        return error_response(404, "NOT_FOUND", f"Queue item {item_id} not found")

    try:
        body = await request.json()
    except json.JSONDecodeError:
        return error_response(400, "INVALID_JSON", "Request body must be JSON")

    updates: list[str] = []
    params: list[str | int | None] = []

    if "status" in body:
        status = str(body["status"]).strip()
        if status not in VALID_STATUSES:
            return error_response(400, "INVALID_STATUS", f"status must be: {', '.join(sorted(VALID_STATUSES))}")
        updates.append("status = ?")
        params.append(status)

    if "retry_count" in body:
        updates.append("retry_count = ?")
        params.append(int(body["retry_count"]))

    if "next_retry_at" in body:
        updates.append("next_retry_at = ?")
        params.append(str(body["next_retry_at"]))

    if "last_error" in body:
        updates.append("last_error = ?")
        params.append(str(body["last_error"]) if body["last_error"] else None)

    if "completed_at" in body:
        updates.append("completed_at = ?")
        params.append(str(body["completed_at"]) if body["completed_at"] else None)

    if not updates:
        return error_response(400, "NO_UPDATES", "No valid fields to update")

    updates.append("updated_at = ?")
    params.append(db.now_iso())
    params.append(item_id)

    await db.execute(
        f"UPDATE session_limit_queue SET {', '.join(updates)} WHERE id = ?",
        tuple(params),
    )

    updated = await db.fetchone(
        "SELECT * FROM session_limit_queue WHERE id = ?", (item_id,)
    )
    return json_ok({"item": dict(updated) if updated else {}})


async def delete_queue_item(request: web.Request) -> web.Response:
    """DELETE /api/session-queue/{id}"""
    item_id = request.match_info["id"]
    try:
        item_id = int(item_id)
    except ValueError:
        return error_response(400, "INVALID_ID", "ID must be an integer")

    existing = await db.fetchone(
        "SELECT * FROM session_limit_queue WHERE id = ?", (item_id,)
    )
    if not existing:
        return error_response(404, "NOT_FOUND", f"Queue item {item_id} not found")

    await db.execute("DELETE FROM session_limit_queue WHERE id = ?", (item_id,))
    return json_ok({"deleted": True})


async def queue_stats(request: web.Request) -> web.Response:
    """GET /api/session-queue/stats — counts by status."""
    rows = await db.fetchall(
        "SELECT status, COUNT(*) as count FROM session_limit_queue GROUP BY status"
    )
    stats = {row["status"]: row["count"] for row in rows}
    total = sum(stats.values())
    return json_ok({"stats": stats, "total": total})
