"""Usage monitoring and command queue API endpoints.

GET    /api/usage                  - Current rate limit status per provider
GET    /api/usage/history          - Historical snapshots with pagination
GET    /api/usage/summary          - Aggregated usage summary
GET    /api/usage/queue            - List command queue entries
POST   /api/usage/queue            - Add command to queue
DELETE /api/usage/queue/{id}       - Cancel a pending command
POST   /api/usage/queue/execute    - Manually trigger pending command execution
"""

from __future__ import annotations

import json
import logging
from typing import Any

from aiohttp import web

from dashboard import db
from dashboard.api import error_response, json_ok

logger = logging.getLogger(__name__)


def _get_usage_svc(request: web.Request):
    """Get UsageService from app, or None."""
    return request.app.get("usage_service")


async def get_current_usage(request: web.Request) -> web.Response:
    """GET /api/usage — latest snapshot per provider + queue stats."""
    # Get latest snapshot per provider using a window function (single query)
    rows = await db.fetchall(
        """WITH ranked AS (
             SELECT *, ROW_NUMBER() OVER(PARTITION BY provider ORDER BY polled_at DESC) AS rn
             FROM usage_snapshots
           )
           SELECT * FROM ranked WHERE rn = 1
           ORDER BY provider"""
    )
    snapshots: dict[str, Any] = {row["provider"]: dict(row) for row in rows}

    usage_svc = _get_usage_svc(request)
    queue_stats = {}
    if usage_svc:
        queue_stats = await usage_svc.get_queue_stats()

    return json_ok({
        "usage": snapshots,
        "queue_stats": queue_stats,
    })


async def get_usage_history(request: web.Request) -> web.Response:
    """GET /api/usage/history — paginated snapshot history.

    Query params: provider, limit (default 60, max 500), offset, since
    """
    params = request.query
    provider = params.get("provider", "").strip()
    try:
        limit = min(int(params.get("limit", "60")), 500)
    except ValueError:
        limit = 60
    try:
        offset = max(int(params.get("offset", "0")), 0)
    except ValueError:
        offset = 0
    since = params.get("since", "").strip()

    where_clauses: list[str] = []
    query_params: list[Any] = []

    if provider:
        where_clauses.append("provider = ?")
        query_params.append(provider)
    if since:
        where_clauses.append("polled_at > ?")
        query_params.append(since)

    where_sql = ""
    if where_clauses:
        where_sql = "WHERE " + " AND ".join(where_clauses)

    count_row = await db.fetchone(
        f"SELECT COUNT(*) as cnt FROM usage_snapshots {where_sql}",
        tuple(query_params),
    )
    total = count_row["cnt"] if count_row else 0

    snapshots = await db.fetchall(
        f"""SELECT * FROM usage_snapshots
            {where_sql}
            ORDER BY polled_at DESC
            LIMIT ? OFFSET ?""",
        tuple(query_params) + (limit, offset),
    )

    return json_ok({
        "snapshots": snapshots,
        "total": total,
        "limit": limit,
        "offset": offset,
    })


async def get_usage_summary(request: web.Request) -> web.Response:
    """GET /api/usage/summary — aggregated stats.

    Query params: hours (default 24, max 168), provider
    """
    try:
        hours = min(int(request.query.get("hours", "24")), 168)
    except ValueError:
        hours = 24
    provider = request.query.get("provider", "").strip()

    where_parts = ["polled_at > datetime('now', ?)"]
    query_params: list[Any] = [f"-{hours} hours"]

    if provider:
        where_parts.append("provider = ?")
        query_params.append(provider)

    where_sql = "WHERE " + " AND ".join(where_parts)

    total_row = await db.fetchone(
        f"""SELECT COUNT(*) as total_polls,
                  SUM(CASE WHEN error_message IS NOT NULL THEN 1 ELSE 0 END) as error_polls,
                  SUM(CASE WHEN requests_remaining = 0 THEN 1 ELSE 0 END) as at_request_limit,
                  SUM(CASE WHEN input_tokens_remaining = 0 THEN 1 ELSE 0 END) as at_input_limit,
                  SUM(CASE WHEN output_tokens_remaining = 0 THEN 1 ELSE 0 END) as at_output_limit,
                  MIN(requests_remaining) as min_requests_remaining,
                  MIN(input_tokens_remaining) as min_input_remaining,
                  MIN(output_tokens_remaining) as min_output_remaining
           FROM usage_snapshots
           {where_sql}""",
        tuple(query_params),
    )

    return json_ok({
        "summary": dict(total_row) if total_row else {},
        "hours": hours,
    })


async def list_queue(request: web.Request) -> web.Response:
    """GET /api/usage/queue — list command queue entries.

    Query params: status, limit (default 50, max 200), offset
    """
    params = request.query
    status_filter = params.get("status", "").strip()
    try:
        limit = min(int(params.get("limit", "50")), 200)
    except ValueError:
        limit = 50
    try:
        offset = max(int(params.get("offset", "0")), 0)
    except ValueError:
        offset = 0

    where_clauses: list[str] = []
    query_params: list[Any] = []

    valid_statuses = {"pending", "executing", "completed", "failed", "cancelled"}
    if status_filter:
        if status_filter not in valid_statuses:
            return error_response(
                400, "INVALID_STATUS", f"Unknown status: {status_filter}"
            )
        where_clauses.append("status = ?")
        query_params.append(status_filter)

    where_sql = ""
    if where_clauses:
        where_sql = "WHERE " + " AND ".join(where_clauses)

    count_row = await db.fetchone(
        f"SELECT COUNT(*) as cnt FROM command_queue {where_sql}",
        tuple(query_params),
    )
    total = count_row["cnt"] if count_row else 0

    commands = await db.fetchall(
        f"""SELECT * FROM command_queue
            {where_sql}
            ORDER BY
                CASE status WHEN 'pending' THEN 0 WHEN 'executing' THEN 1 ELSE 2 END,
                priority DESC, created_at ASC
            LIMIT ? OFFSET ?""",
        tuple(query_params) + (limit, offset),
    )

    return json_ok({
        "commands": commands,
        "total": total,
        "limit": limit,
        "offset": offset,
    })


async def enqueue_command(request: web.Request) -> web.Response:
    """POST /api/usage/queue — add a command to the deferred queue.

    Body: {session_name, command, host?, priority?}
    """
    usage_svc = _get_usage_svc(request)
    if not usage_svc:
        return error_response(503, "DISABLED", "Usage monitoring is not enabled")

    try:
        body = await request.json()
    except (json.JSONDecodeError, Exception):
        return error_response(400, "INVALID_JSON", "Request body must be JSON")

    session_name = body.get("session_name", "").strip()
    command = body.get("command", "").strip()

    if not session_name:
        return error_response(400, "MISSING_SESSION", "session_name is required")
    if not command:
        return error_response(400, "MISSING_COMMAND", "command is required")

    # Resolve host if not provided
    host = body.get("host", "").strip()
    if not host:
        session = await db.fetchone(
            "SELECT host FROM sessions WHERE name = ?", (session_name,)
        )
        if not session or not session.get("host"):
            return error_response(
                404, "SESSION_NOT_FOUND",
                f"Session '{session_name}' not found or has no host",
            )
        host = session["host"]

    try:
        priority = int(body.get("priority", 0))
    except (ValueError, TypeError):
        return error_response(400, "INVALID_PRIORITY", "priority must be an integer")
    entry = await usage_svc.enqueue_command(session_name, host, command, priority)
    return json_ok({"command": entry}, status=201)


async def cancel_queue_command(request: web.Request) -> web.Response:
    """DELETE /api/usage/queue/{id} — cancel a pending command."""
    usage_svc = _get_usage_svc(request)
    if not usage_svc:
        return error_response(503, "DISABLED", "Usage monitoring is not enabled")

    try:
        cmd_id = int(request.match_info["id"])
    except ValueError:
        return error_response(400, "INVALID_ID", "Command ID must be an integer")

    cancelled = await usage_svc.cancel_command(cmd_id)
    if not cancelled:
        return error_response(
            404, "NOT_FOUND", "Command not found or not in pending status"
        )
    return json_ok({"cancelled": True, "id": cmd_id})


async def execute_queue(request: web.Request) -> web.Response:
    """POST /api/usage/queue/execute — manually trigger pending commands."""
    usage_svc = _get_usage_svc(request)
    if not usage_svc:
        return error_response(503, "DISABLED", "Usage monitoring is not enabled")

    results = await usage_svc.execute_pending_commands()
    return json_ok({"results": results, "executed": len(results)})
