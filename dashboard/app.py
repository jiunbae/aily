"""aiohttp Application factory.

Creates and configures the dashboard web application:
- Initializes database
- Creates service instances
- Registers API routes
- Mounts static files
- Sets up Jinja2 templates
- Starts/stops background workers
- Registers page routes (/, /sessions, /sessions/{name})
"""

from __future__ import annotations

import asyncio
import logging
import os

from aiohttp import web

from dashboard.api import sessions as sessions_api
from dashboard.api import stats as stats_api
from dashboard.api import ws as ws_api
from dashboard.auth import auth_middleware
from dashboard.config import Config
from dashboard.db import close_db, init_db
from dashboard.services.event_bus import EventBus
from dashboard.services.message_service import MessageService
from dashboard.services.platform_service import PlatformService
from dashboard.services.session_service import SessionService
from dashboard.workers.session_poller import session_poller

logger = logging.getLogger(__name__)


async def create_app() -> web.Application:
    """Create and configure the aiohttp application."""
    logging.basicConfig(
        level=logging.INFO,
        format="[dashboard] %(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    config = Config.from_env()
    logger.info("Configuration loaded")
    logger.info("  SSH hosts: %s", config.ssh_hosts)
    logger.info("  DB path: %s", config.db_path)
    logger.info("  Discord configured: %s", bool(config.discord_bot_token))
    logger.info("  Slack configured: %s", bool(config.slack_bot_token))
    logger.info("  Auth token set: %s", bool(config.dashboard_token))
    logger.info("  Session poller: %s", config.enable_session_poller)

    # Initialize database
    await init_db(config.db_path)

    # Create services
    event_bus = EventBus()
    session_svc = SessionService(ssh_hosts=config.ssh_hosts)
    platform_svc = PlatformService(
        discord_bot_token=config.discord_bot_token,
        discord_channel_id=config.discord_channel_id,
        slack_bot_token=config.slack_bot_token,
        slack_channel_id=config.slack_channel_id,
    )
    message_svc = MessageService(event_bus=event_bus)

    # Create app with auth middleware
    app = web.Application(middlewares=[auth_middleware])

    # Store config and services on the app for handler access
    app["config"] = config
    app["event_bus"] = event_bus
    app["session_service"] = session_svc
    app["platform_service"] = platform_svc
    app["message_service"] = message_svc

    # Setup Jinja2 templates (if available)
    template_dir = os.path.join(os.path.dirname(__file__), "templates")
    try:
        import aiohttp_jinja2
        import jinja2

        if os.path.isdir(template_dir):
            aiohttp_jinja2.setup(
                app, loader=jinja2.FileSystemLoader(template_dir)
            )
            logger.info("Jinja2 templates loaded from %s", template_dir)
        else:
            logger.info("No templates directory found, skipping Jinja2 setup")
    except ImportError:
        logger.warning("aiohttp-jinja2 not installed, template rendering disabled")

    # Register API routes
    _setup_routes(app)

    # Mount static files (if directory exists)
    static_dir = os.path.join(os.path.dirname(__file__), "static")
    if os.path.isdir(static_dir):
        app.router.add_static("/static/", static_dir, name="static")
        logger.info("Static files served from %s", static_dir)

    # Register page routes (server-rendered HTML)
    _setup_page_routes(app)

    # Register lifecycle hooks
    app.on_startup.append(_on_startup)
    app.on_cleanup.append(_on_cleanup)

    return app


def _setup_routes(app: web.Application) -> None:
    """Register all API routes."""
    # Session CRUD
    app.router.add_get("/api/sessions", sessions_api.list_sessions)
    app.router.add_get("/api/sessions/{name}", sessions_api.get_session)
    app.router.add_post("/api/sessions", sessions_api.create_session)
    app.router.add_delete("/api/sessions/{name}", sessions_api.delete_session)
    app.router.add_post(
        "/api/sessions/{name}/send", sessions_api.send_message
    )

    # Stats
    app.router.add_get("/api/stats", stats_api.get_stats)

    # WebSocket
    app.router.add_get("/ws", ws_api.websocket_handler)

    # Bridge webhook (internal, no auth)
    app.router.add_post(
        "/api/hooks/event", sessions_api.receive_bridge_event
    )

    # Health check (no auth)
    app.router.add_get("/healthz", _healthz)


def _setup_page_routes(app: web.Application) -> None:
    """Register server-rendered page routes.

    These routes serve Jinja2 templates if available, otherwise return
    a simple JSON response indicating the dashboard is running.
    """
    app.router.add_get("/", _index_page)
    app.router.add_get("/sessions", _sessions_page)
    app.router.add_get("/sessions/{name}", _session_detail_page)


async def _healthz(request: web.Request) -> web.Response:
    """GET /healthz - Health check endpoint (no auth required).

    Returns 200 if the service is running and database is accessible.
    """
    from dashboard.db import get_db

    checks: dict[str, str] = {"status": "ok"}

    try:
        db_conn = get_db()
        await db_conn.execute("SELECT 1")
        checks["database"] = "ok"
    except Exception as e:
        checks["database"] = f"error: {e}"
        checks["status"] = "degraded"

    status_code = 200 if checks["status"] == "ok" else 503
    return web.json_response(checks, status=status_code)


async def _index_page(request: web.Request) -> web.Response:
    """GET / - Dashboard home page."""
    try:
        import aiohttp_jinja2

        return aiohttp_jinja2.render_template("index.html", request, {})
    except (ImportError, Exception):
        # Fallback if templates are not available
        return web.json_response(
            {
                "service": "aily-dashboard",
                "status": "running",
                "ui": "Templates not available. Use /api/* endpoints.",
            }
        )


async def _sessions_page(request: web.Request) -> web.Response:
    """GET /sessions - Session list page."""
    try:
        import aiohttp_jinja2

        return aiohttp_jinja2.render_template("sessions.html", request, {})
    except (ImportError, Exception):
        return web.json_response(
            {"redirect": "/api/sessions", "message": "Templates not available"}
        )


async def _session_detail_page(request: web.Request) -> web.Response:
    """GET /sessions/{name} - Session detail page."""
    name = request.match_info["name"]
    try:
        import aiohttp_jinja2

        return aiohttp_jinja2.render_template(
            "session_detail.html", request, {"session_name": name}
        )
    except (ImportError, Exception):
        return web.json_response(
            {
                "redirect": f"/api/sessions/{name}",
                "message": "Templates not available",
            }
        )


async def _on_startup(app: web.Application) -> None:
    """Start background workers on application startup."""
    config: Config = app["config"]
    session_svc: SessionService = app["session_service"]
    platform_svc: PlatformService = app["platform_service"]
    event_bus: EventBus = app["event_bus"]

    app["_worker_tasks"] = []

    if config.enable_session_poller:
        task = asyncio.create_task(
            session_poller(
                session_svc, platform_svc, event_bus, config.poll_interval
            )
        )
        app["_worker_tasks"].append(task)
        logger.info(
            "Session poller worker started (interval=%ds)",
            config.poll_interval,
        )

    logger.info("Dashboard started on %s:%d", config.host, config.port)


async def _on_cleanup(app: web.Application) -> None:
    """Stop background workers and close database on shutdown."""
    # Cancel workers
    for task in app.get("_worker_tasks", []):
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    # Close database
    await close_db()
    logger.info("Dashboard shutdown complete")
