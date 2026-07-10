"""Shared API response helpers.

Provides consistent JSON response formatting for all API endpoints.
Error responses follow the format:
  {"error": {"code": "NOT_FOUND", "message": "Session 'foo' not found"}}
"""

from __future__ import annotations

import json
from typing import Any

from aiohttp import web


async def read_json_object(request: web.Request) -> dict[str, Any]:
    """Parse and validate a JSON *object* request body.

    Returns the parsed dict, or raises ``web.HTTPBadRequest`` (with the standard
    JSON error envelope) when the body is not valid JSON or is a non-object
    (array/scalar). Handlers that do ``body.get(...)`` / ``body.items()`` would
    otherwise raise on a non-dict body and surface as a 500 instead of a 400.
    """
    try:
        body = await request.json()
    except (json.JSONDecodeError, ValueError):
        raise web.HTTPBadRequest(
            text=json.dumps(
                {"error": {"code": "INVALID_JSON", "message": "Request body must be JSON"}}
            ),
            content_type="application/json",
        )
    if not isinstance(body, dict):
        raise web.HTTPBadRequest(
            text=json.dumps(
                {"error": {"code": "INVALID_JSON", "message": "Request body must be a JSON object"}}
            ),
            content_type="application/json",
        )
    return body


def error_response(
    status: int,
    code: str,
    message: str,
) -> web.Response:
    """Create a JSON error response.

    Args:
        status: HTTP status code (e.g. 400, 404, 500).
        code: Machine-readable error code (e.g. "NOT_FOUND").
        message: Human-readable error description.

    Returns:
        aiohttp JSON response.
    """
    return web.json_response(
        {"error": {"code": code, "message": message}},
        status=status,
    )


def json_ok(data: dict[str, Any] | list[Any], status: int = 200) -> web.Response:
    """Create a JSON success response.

    Args:
        data: Response payload (dict or list).
        status: HTTP status code (default 200).

    Returns:
        aiohttp JSON response.
    """
    return web.json_response(data, status=status)
