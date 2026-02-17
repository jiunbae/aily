"""Access logging middleware."""

from __future__ import annotations

import logging
import time

from aiohttp import web

logger = logging.getLogger("dashboard.access")


@web.middleware
async def access_log_middleware(
    request: web.Request,
    handler: web.RequestHandler,
) -> web.StreamResponse:
    """Log HTTP requests with method, path, status, and duration."""
    # Skip noisy paths
    if request.path in ("/healthz", "/ws") or request.path.startswith("/static/"):
        return await handler(request)

    start = time.monotonic()
    try:
        response = await handler(request)
        duration_ms = (time.monotonic() - start) * 1000
        logger.info(
            "%s %s %d %.1fms",
            request.method,
            request.path,
            response.status,
            duration_ms,
        )
        return response
    except web.HTTPException as exc:
        duration_ms = (time.monotonic() - start) * 1000
        logger.warning(
            "%s %s %d %.1fms",
            request.method,
            request.path,
            exc.status,
            duration_ms,
        )
        raise
