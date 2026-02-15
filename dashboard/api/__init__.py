"""Shared API response helpers.

Provides consistent JSON response formatting for all API endpoints.
Error responses follow the format:
  {"error": {"code": "NOT_FOUND", "message": "Session 'foo' not found"}}
"""

from __future__ import annotations

from typing import Any

from aiohttp import web


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
