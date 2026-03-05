"""Authentication middleware with cookie session support.

Supports two auth methods:
1. Bearer token via Authorization header (for API clients, CLI, bridges)
2. Session cookie (for browser-based dashboard access)

Unauthenticated browser requests redirect to /login.
Unauthenticated API requests return 401 JSON.
If DASHBOARD_TOKEN is not set, all requests are allowed (dev mode).
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import time

from aiohttp import web

logger = logging.getLogger(__name__)

COOKIE_NAME = "aily_session"
COOKIE_MAX_AGE = 86400  # 24 hours

# Paths that skip dashboard authentication entirely
_NO_AUTH_PREFIXES = (
    "/healthz",
    "/api/install.sh",
    "/static/",
    "/login",
    "/logout",
)

# Track whether the missing-hook-secret warning has been emitted
_hook_secret_warned = False


async def verify_hook_signature(request: web.Request, secret: str) -> bool:
    """Verify HMAC-SHA256 signature of a hook request.

    Expects header ``X-Hook-Signature: sha256=<hex_digest>`` where the digest
    is computed over the raw request body using *secret* as the HMAC key.
    """
    header = request.headers.get("X-Hook-Signature", "")
    if not header.startswith("sha256="):
        return False
    provided = header[7:]  # strip "sha256=" prefix
    body = await request.read()
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(provided, expected)


def create_session_cookie(token: str) -> str:
    """Create a signed session cookie value.

    Format: <timestamp>.<signature>
    """
    ts = str(int(time.time()))
    sig = hmac.new(
        token.encode(), ts.encode(), hashlib.sha256
    ).hexdigest()
    return f"{ts}.{sig}"


def validate_session_cookie(cookie_value: str, token: str) -> bool:
    """Validate a signed session cookie.

    Checks signature and expiry (COOKIE_MAX_AGE seconds).
    """
    if not cookie_value or "." not in cookie_value:
        return False
    parts = cookie_value.split(".", 1)
    if len(parts) != 2:
        return False
    ts_str, sig = parts
    try:
        ts = int(ts_str)
    except ValueError:
        return False
    # Check expiry
    if time.time() - ts > COOKIE_MAX_AGE:
        return False
    # Check signature
    expected = hmac.new(
        token.encode(), ts_str.encode(), hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(sig, expected)


def _is_browser_request(request: web.Request) -> bool:
    """Heuristic: browser page navigation vs API/programmatic request."""
    accept = request.headers.get("Accept", "")
    # API routes are never browser navigations
    if request.path.startswith("/api/") or request.path == "/ws":
        return False
    return "text/html" in accept


@web.middleware
async def auth_middleware(
    request: web.Request,
    handler: web.RequestHandler,
) -> web.StreamResponse:
    """Authentication middleware.

    Checks Bearer token OR session cookie. Redirects browser requests
    to /login on failure; returns 401 JSON for API requests.
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

    # Hook endpoints: use HMAC signature verification instead of token auth
    if path.startswith("/api/hooks/"):
        global _hook_secret_warned  # noqa: PLW0603
        if config.hook_secret:
            if await verify_hook_signature(request, config.hook_secret):
                return await handler(request)
            logger.warning("Invalid hook signature: %s %s", request.method, path)
            return web.json_response(
                {"error": {"code": "UNAUTHORIZED", "message": "Invalid hook signature"}},
                status=401,
            )
        # No secret configured — allow through but warn once
        if not _hook_secret_warned:
            logger.warning(
                "HOOK_SECRET is not set; hook endpoints are unauthenticated. "
                "Set HOOK_SECRET to enable HMAC verification."
            )
            _hook_secret_warned = True
        return await handler(request)

    dashboard_token = config.dashboard_token

    # WebSocket: check ?token= query param or cookie
    if path == "/ws":
        token = request.query.get("token", "")
        if token and hmac.compare_digest(token, dashboard_token):
            return await handler(request)
        # Also accept session cookie for WebSocket
        cookie_value = request.cookies.get(COOKIE_NAME, "")
        if validate_session_cookie(cookie_value, dashboard_token):
            return await handler(request)
        logger.warning("Unauthorized WebSocket: %s", path)
        return web.json_response(
            {"error": {"code": "UNAUTHORIZED", "message": "Missing or invalid token"}},
            status=401,
        )

    # Check session cookie
    cookie_value = request.cookies.get(COOKIE_NAME, "")
    if validate_session_cookie(cookie_value, dashboard_token):
        return await handler(request)

    # Check Authorization header (timing-safe comparison)
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
        if hmac.compare_digest(token, dashboard_token):
            return await handler(request)

    # Auth failed — redirect browsers to login, return 401 for API
    logger.warning("Unauthorized request: %s %s", request.method, path)

    if _is_browser_request(request):
        raise web.HTTPFound(f"/login?next={path}")

    return web.json_response(
        {
            "error": {
                "code": "UNAUTHORIZED",
                "message": "Invalid or missing authentication token",
            }
        },
        status=401,
    )
