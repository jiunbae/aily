"""Session CRUD endpoints and bridge webhook receiver.

Endpoints:
  GET    /api/sessions          - List sessions with filter/sort/pagination
  GET    /api/sessions/{name}   - Session detail with message count
  POST   /api/sessions          - Create tmux session
  DELETE /api/sessions/{name}   - Kill session + archive threads
  POST   /api/sessions/{name}/send - Send message to tmux
  POST   /api/hooks/event       - Bridge webhook receiver (internal, no auth)
"""

from __future__ import annotations

import json
import logging
from typing import Any

from aiohttp import web

from dashboard import db
from dashboard.api import error_response, json_ok
from dashboard.services.event_bus import Event, EventBus
from dashboard.services.message_service import MessageService
from dashboard.services.platform_service import PlatformService
from dashboard.services.session_service import SessionService

logger = logging.getLogger(__name__)

# Valid session statuses
VALID_STATUSES = frozenset({"active", "idle", "closed", "orphan", "unreachable"})

# Valid sort fields
VALID_SORT_FIELDS = frozenset({"name", "created_at", "updated_at", "status", "host"})


async def list_sessions(request: web.Request) -> web.Response:
    """GET /api/sessions

    Query params:
        status - Filter by session status
        host   - Filter by SSH host
        q      - Search session name substring
        sort   - Sort field (prefix with - for descending, default: -updated_at)
        limit  - Page size (default: 50, max: 200)
        offset - Pagination offset (default: 0)
    """
    params = request.query

    # Parse query parameters
    status_filter = params.get("status")
    host_filter = params.get("host")
    q = params.get("q", "").strip()
    sort_field = params.get("sort", "-updated_at")

    try:
        limit = min(int(params.get("limit", "50")), 200)
    except ValueError:
        limit = 50
    try:
        offset = max(int(params.get("offset", "0")), 0)
    except ValueError:
        offset = 0

    # Validate status filter
    if status_filter and status_filter not in VALID_STATUSES:
        return error_response(
            400, "INVALID_STATUS", f"Unknown status: {status_filter}"
        )

    # Build query
    where_clauses: list[str] = []
    query_params: list[Any] = []

    if status_filter:
        where_clauses.append("status = ?")
        query_params.append(status_filter)
    if host_filter:
        where_clauses.append("host = ?")
        query_params.append(host_filter)
    if q:
        where_clauses.append("name LIKE ?")
        query_params.append(f"%{q}%")

    where_sql = ""
    if where_clauses:
        where_sql = "WHERE " + " AND ".join(where_clauses)

    # Parse sort
    descending = sort_field.startswith("-")
    field_name = sort_field.lstrip("-")
    if field_name not in VALID_SORT_FIELDS:
        field_name = "updated_at"
    order_dir = "DESC" if descending else "ASC"

    # Count total
    count_row = await db.fetchone(
        f"SELECT COUNT(*) as cnt FROM sessions {where_sql}",
        tuple(query_params),
    )
    total = count_row["cnt"] if count_row else 0

    # Fetch page
    sessions = await db.fetchall(
        f"""SELECT * FROM sessions {where_sql}
            ORDER BY {field_name} {order_dir}
            LIMIT ? OFFSET ?""",
        tuple(query_params) + (limit, offset),
    )

    return json_ok(
        {
            "sessions": sessions,
            "total": total,
            "limit": limit,
            "offset": offset,
        }
    )


async def get_session(request: web.Request) -> web.Response:
    """GET /api/sessions/{name}

    Returns session detail with message count.
    """
    name = request.match_info["name"]

    session = await db.fetchone(
        "SELECT * FROM sessions WHERE name = ?", (name,)
    )
    if not session:
        return error_response(404, "NOT_FOUND", f"Session '{name}' not found")

    # Get message count
    count_row = await db.fetchone(
        "SELECT COUNT(*) as cnt FROM messages WHERE session_name = ?",
        (name,),
    )
    session_data = dict(session)
    session_data["message_count"] = count_row["cnt"] if count_row else 0

    return json_ok({"session": session_data})


async def create_session(request: web.Request) -> web.Response:
    """POST /api/sessions

    Request body: {"name": "my-session", "host": "dev-box"}
    Creates a tmux session and records it in the database.
    """
    try:
        body = await request.json()
    except (json.JSONDecodeError, Exception):
        return error_response(400, "INVALID_JSON", "Request body must be JSON")

    name = body.get("name", "").strip()
    host = body.get("host", "").strip()

    # Validate name
    if not name:
        return error_response(400, "MISSING_NAME", "Session name is required")

    session_svc: SessionService = request.app["session_service"]
    if not session_svc.is_valid_session_name(name):
        return error_response(
            400,
            "INVALID_NAME",
            "Name must be alphanumeric/dash/underscore, max 64 chars",
        )

    # Validate host
    if not host:
        host = session_svc.default_host
    if host not in session_svc.ssh_hosts:
        return error_response(
            400,
            "INVALID_HOST",
            f"Unknown host '{host}'. Available: {session_svc.ssh_hosts}",
        )

    # Check if session already exists in DB
    existing = await db.fetchone(
        "SELECT name FROM sessions WHERE name = ?", (name,)
    )
    if existing:
        return error_response(
            409, "ALREADY_EXISTS", f"Session '{name}' already exists"
        )

    # Create tmux session via SSH
    success = await session_svc.create_session(name, host)
    if not success:
        return error_response(
            500,
            "TMUX_CREATE_FAILED",
            f"Failed to create tmux session '{name}' on '{host}'",
        )

    # Record in database
    now = db.now_iso()
    await db.execute(
        """INSERT INTO sessions (name, host, status, created_at, updated_at)
           VALUES (?, ?, 'active', ?, ?)""",
        (name, host, now, now),
    )

    # Fetch complete record
    session = await db.fetchone(
        "SELECT * FROM sessions WHERE name = ?", (name,)
    )
    session_data = dict(session) if session else {"name": name, "host": host}

    # Publish event
    event_bus: EventBus = request.app["event_bus"]
    await event_bus.publish(Event.session_created(session_data))

    return json_ok({"session": session_data}, status=201)


async def delete_session(request: web.Request) -> web.Response:
    """DELETE /api/sessions/{name}

    Kills the tmux session and archives platform threads.
    """
    name = request.match_info["name"]

    # Check session exists in DB
    session = await db.fetchone(
        "SELECT * FROM sessions WHERE name = ?", (name,)
    )
    if not session:
        return error_response(404, "NOT_FOUND", f"Session '{name}' not found")

    session_svc: SessionService = request.app["session_service"]
    event_bus: EventBus = request.app["event_bus"]
    platform_svc: PlatformService = request.app["platform_service"]

    # Kill tmux session
    tmux_killed, kill_host = await session_svc.kill_session(name)

    # Archive platform threads
    archived_platforms = await platform_svc.archive_threads(dict(session))

    # Update DB
    now = db.now_iso()
    await db.execute(
        """UPDATE sessions
           SET status = 'closed', closed_at = ?, updated_at = ?
           WHERE name = ?""",
        (now, now, name),
    )

    # Publish event
    updated_session = await db.fetchone(
        "SELECT * FROM sessions WHERE name = ?", (name,)
    )
    if updated_session:
        await event_bus.publish(Event.session_closed(dict(updated_session)))

    return json_ok(
        {
            "deleted": True,
            "tmux_killed": tmux_killed,
            "threads_archived": archived_platforms,
        }
    )


async def send_message(request: web.Request) -> web.Response:
    """POST /api/sessions/{name}/send

    Send a message to the tmux session (types it into Claude Code).
    Request body: {"message": "your message text"}
    """
    name = request.match_info["name"]

    try:
        body = await request.json()
    except (json.JSONDecodeError, Exception):
        return error_response(400, "INVALID_JSON", "Request body must be JSON")

    message = body.get("message", "").strip()
    if not message:
        return error_response(
            400, "MISSING_MESSAGE", "Message text is required"
        )

    session_svc: SessionService = request.app["session_service"]

    # Find the host where the session is running
    host = await session_svc.find_host(name)
    if not host:
        return error_response(
            404,
            "SESSION_NOT_FOUND",
            f"tmux session '{name}' not found on any host",
        )

    # Send to tmux
    success = await session_svc.send_to_session(host, name, message)
    if not success:
        return error_response(
            500, "SEND_FAILED", "Failed to send message to tmux session"
        )

    return json_ok({"sent": True, "host": host})


async def receive_bridge_event(request: web.Request) -> web.Response:
    """POST /api/hooks/event

    Webhook receiver for bridge processes. Internal endpoint, no auth.
    Accepts fire-and-forget event pushes from Discord/Slack bridges.
    """
    try:
        body = await request.json()
    except (json.JSONDecodeError, Exception):
        return error_response(400, "INVALID_JSON", "Request body must be JSON")

    message_svc: MessageService = request.app["message_service"]

    try:
        await message_svc.ingest_bridge_event(body)
    except Exception:
        logger.exception("Error processing bridge event")
        # Still return 202 — bridges should not retry on dashboard errors
        pass

    return json_ok({"accepted": True}, status=202)
