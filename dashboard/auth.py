"""Authentication middleware with cookie session support.

Supports two auth methods:
1. Bearer token via Authorization header (for API clients, CLI, bridges)
2. Session cookie (for browser-based dashboard access)

WebSocket connections also accept short-lived nonces (tickets) that are
generated per page load and expire after 60 seconds / single use.

Unauthenticated browser requests redirect to /login.
Unauthenticated API requests return 401 JSON.
Authentication is ALWAYS enforced — config.py auto-generates a token if not set.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import time
from urllib.parse import quote

from aiohttp import web

logger = logging.getLogger(__name__)

COOKIE_NAME = "aily_session"
COOKIE_MAX_AGE = 86400  # 24 hours

# ── WebSocket nonces (short-lived, single-use) ──────────────────────
_WS_NONCE_TTL = 60  # seconds
_ws_nonces: dict[str, float] = {}  # nonce -> expiry timestamp


def create_ws_nonce() -> str:
    """Create a single-use WebSocket nonce valid for _WS_NONCE_TTL seconds."""
    _purge_expired_nonces()
    nonce = secrets.token_urlsafe(32)
    _ws_nonces[nonce] = time.time() + _WS_NONCE_TTL
    return nonce


def validate_ws_nonce(nonce: str) -> bool:
    """Validate and consume a WebSocket nonce (single-use)."""
    _purge_expired_nonces()
    expiry = _ws_nonces.pop(nonce, None)
    if expiry is None:
        return False
    return time.time() < expiry


def _purge_expired_nonces() -> None:
    """Remove expired nonces to prevent unbounded growth."""
    now = time.time()
    expired = [k for k, v in _ws_nonces.items() if v <= now]
    for k in expired:
        del _ws_nonces[k]

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


def verify_hook_secret(request: web.Request, secret: str) -> bool:
    """Verify shared secret from a hook request.

    Claude Code HTTP hooks send headers as static values (no HMAC computation),
    so we use a simple shared secret comparison via ``X-Hook-Secret`` header.
    Uses timing-safe comparison to prevent timing attacks.
    """
    provided = request.headers.get("X-Hook-Secret", "")
    if not provided:
        return False
    return hmac.compare_digest(provided, secret)


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
    if not config:
        # App misconfigured — deny by default
        logger.error("No config found on app — denying request")
        return web.json_response(
            {"error": {"code": "SERVER_ERROR", "message": "Server misconfigured"}},
            status=500,
        )

    if not config.dashboard_token:
        # Token must always be set (config.py auto-generates if missing).
        # If we somehow get here, block everything and log loudly.
        logger.critical(
            "DASHBOARD_TOKEN is empty — all requests blocked. "
            "This should never happen; check config loading."
        )
        return web.json_response(
            {"error": {"code": "SERVER_ERROR", "message": "Auth not configured"}},
            status=503,
        )

    path = request.path
    dashboard_token = config.dashboard_token

    # Skip auth for exempted paths
    for prefix in _NO_AUTH_PREFIXES:
        if path.startswith(prefix):
            return await handler(request)

    # Hook endpoints: use shared secret verification instead of token auth
    if path.startswith("/api/hooks/"):
        global _hook_secret_warned  # noqa: PLW0603
        if config.hook_secret:
            # Accept either shared secret or Bearer token (for bridge compat)
            if verify_hook_secret(request, config.hook_secret):
                return await handler(request)
            auth_header = request.headers.get("Authorization", "")
            if auth_header.startswith("Bearer ") and hmac.compare_digest(
                auth_header[7:], dashboard_token
            ):
                return await handler(request)
            logger.warning("Invalid hook secret: %s %s", request.method, path)
            return web.json_response(
                {"error": {"code": "UNAUTHORIZED", "message": "Invalid hook secret"}},
                status=401,
            )
        # No secret configured — require Bearer token as fallback
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer ") and hmac.compare_digest(
            auth_header[7:], dashboard_token
        ):
            return await handler(request)
        if not _hook_secret_warned:
            logger.warning(
                "HOOK_SECRET is not set; hook endpoints require Bearer token. "
                "Set HOOK_SECRET to enable HMAC verification."
            )
            _hook_secret_warned = True
        logger.warning("Unauthorized hook request (no HOOK_SECRET configured): %s %s", request.method, path)
        return web.json_response(
            {"error": {"code": "UNAUTHORIZED", "message": "Hook secret or Bearer token required"}},
            status=401,
        )

    # WebSocket: check ?token= query param (nonce or real token) or cookie
    if path == "/ws":
        token = request.query.get("token", "")
        if token:
            # Try short-lived nonce first (single-use, consumed on success)
            if validate_ws_nonce(token):
                return await handler(request)
            # Fall back to real token comparison (for API clients)
            if hmac.compare_digest(token, dashboard_token):
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
        raise web.HTTPFound(f"/login?next={quote(path)}")

    return web.json_response(
        {
            "error": {
                "code": "UNAUTHORIZED",
                "message": "Invalid or missing authentication token",
            }
        },
        status=401,
    )
