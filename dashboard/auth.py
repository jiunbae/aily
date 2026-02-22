"""Bearer token authentication middleware.

Checks Authorization: Bearer <token> header against DASHBOARD_TOKEN.
Skips auth for health checks, internal hook endpoints, and static files.
WebSocket auth uses ?token= query param.
If DASHBOARD_TOKEN is not set, all requests are allowed (dev mode).
"""

from __future__ import annotations

import hmac
import logging

from aiohttp import web

logger = logging.getLogger(__name__)

# Paths that skip authentication
_NO_AUTH_PREFIXES = (
    "/healthz",
    "/api/hooks/",
    "/api/install.sh",
    "/static/",
    "/favicon",
)

# Page routes served by Jinja2 templates (browser access, no Bearer token)
_NO_AUTH_EXACT = frozenset((
    "/",
    "/sessions",
    "/settings",
))


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
    if path in _NO_AUTH_EXACT or path.startswith("/sessions/"):
        return await handler(request)
    for prefix in _NO_AUTH_PREFIXES:
        if path.startswith(prefix):
            return await handler(request)

    # WebSocket: check ?token= query param
    if path == "/ws":
        token = request.query.get("token", "")
        if token and hmac.compare_digest(token, config.dashboard_token):
            return await handler(request)
        logger.warning("Unauthorized WebSocket: %s", path)
        return web.json_response(
            {"error": {"code": "UNAUTHORIZED", "message": "Missing or invalid token"}},
            status=401,
        )

    # Check Authorization header (timing-safe comparison)
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
        if hmac.compare_digest(token, config.dashboard_token):
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
