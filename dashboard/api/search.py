"""Message search endpoint using FTS5."""

from __future__ import annotations

import logging
import re
from typing import Any

from aiohttp import web

from dashboard import db

logger = logging.getLogger(__name__)


def _sanitize_fts_query(q: str) -> str:
    """Sanitize user input for FTS5 phrase search."""
    # Remove FTS5 special characters and operators
    q = re.sub(r'["\*\^\(\)\{\}:]', '', q)
    # Remove FTS5 boolean operators
    q = re.sub(r'\b(AND|OR|NOT|NEAR)\b', '', q, flags=re.IGNORECASE)
    q = q.strip()
    if not q:
        return '""'
    # Wrap in double quotes for phrase search
    safe_q = q.replace('"', '')
    return f'"{safe_q}"'


async def search_messages(request: web.Request) -> web.Response:
    """GET /api/messages/search?q=text&session=name&role=assistant&limit=50&offset=0"""
    q = request.query.get("q", "").strip()
    if not q or len(q) < 2:
        return web.json_response(
            {
                "error": {
                    "code": "BAD_REQUEST",
                    "message": "Query must be at least 2 characters",
                }
            },
            status=400,
        )

    session_filter = request.query.get("session", "").strip()
    role_filter = request.query.get("role", "").strip()

    try:
        limit = min(int(request.query.get("limit", "50")), 200)
    except ValueError:
        limit = 50
    try:
        offset = max(int(request.query.get("offset", "0")), 0)
    except ValueError:
        offset = 0

    safe_q = _sanitize_fts_query(q)

    conditions = ["messages_fts MATCH ?"]
    params: list[Any] = [safe_q]

    if session_filter:
        conditions.append("m.session_name = ?")
        params.append(session_filter)
    if role_filter and role_filter in ("user", "assistant", "system"):
        conditions.append("m.role = ?")
        params.append(role_filter)

    where = " AND ".join(conditions)
    params.extend([limit, offset])

    sql = f"""
        SELECT m.id, m.session_name, m.role, m.content, m.timestamp,
               snippet(messages_fts, 0, '<mark>', '</mark>', '...', 40) as snippet
        FROM messages m
        JOIN messages_fts ON m.id = messages_fts.rowid
        WHERE {where}
        ORDER BY rank
        LIMIT ? OFFSET ?
    """

    rows = await db.fetchall(sql, tuple(params))

    count_sql = f"""
        SELECT COUNT(*) as total
        FROM messages m
        JOIN messages_fts ON m.id = messages_fts.rowid
        WHERE {where}
    """
    count_row = await db.fetchone(count_sql, tuple(params[:-2]))
    total = count_row["total"] if count_row else 0

    return web.json_response(
        {
            "results": rows,
            "total": total,
            "query": q,
            "limit": limit,
            "offset": offset,
        }
    )
