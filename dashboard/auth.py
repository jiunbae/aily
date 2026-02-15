"""Bearer token authentication middleware.

Checks Authorization: Bearer <token> header against DASHBOARD_TOKEN.
Skips auth for health checks, internal hook endpoints, WebSocket, and static files.
If DASHBOARD_TOKEN is not set, all requests are allowed (dev mode).
"""

from __future__ import annotations

import logging

from aiohttp import web

logger = logging.getLogger(__name__)

# Paths that skip authentication
_NO_AUTH_PREFIXES = (
    "/healthz",
    "/api/hooks/",
    "/ws",
    "/static/",
)


@web.middleware
async def auth_middleware(
    request: web.Request,
    handler: web.RequestHandler,
) -> web.StreamResponse:
    """aiohttp middleware for Bearer token authentication.

    If DASHBOARD_TOKEN is configured, requires a valid Authorization header
    on all requests except health checks, hooks, WebSocket, and static files.
    """
    config = request.app.get("config")
    if not config or not config.dashboard_token:
        # No token configured — allow all requests (dev mode)
        return await handler(request)

    path = request.path

    # Skip auth for exempted paths
    for prefix in _NO_AUTH_PREFIXES:
        if path.startswith(prefix):
            return await handler(request)

    # Check Authorization header
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
        if token == config.dashboard_token:
            return await handler(request)

    logger.warning("Unauthorized request: %s %s", request.method, path)
    return web.json_response(
        {
            "error": {
                "code": "UNAUTHORIZED",
                "message": "Invalid or missing authentication token",
            }
        },
        status=401,
    )
