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

from dashboard import db
from dashboard.api import preferences as prefs_api
from dashboard.api import search as search_api
from dashboard.api import sessions as sessions_api
from dashboard.api import settings as settings_api
from dashboard.api import stats as stats_api
from dashboard.api import usage as usage_api
from dashboard.api import ws as ws_api
from dashboard.access_log import access_log_middleware
from dashboard.auth import (
    COOKIE_MAX_AGE,
    COOKIE_NAME,
    auth_middleware,
    create_session_cookie,
    validate_session_cookie,
)
from dashboard.config import Config
from dashboard.db import close_db, init_db
from dashboard.rate_limit import rate_limit_middleware
from dashboard.services.event_bus import EventBus
from dashboard.services.message_service import MessageService
from dashboard.services.platform_service import PlatformService
from dashboard.services.session_service import SessionService
from dashboard.workers.message_sync import message_sync_worker
from dashboard.workers.session_poller import session_poller

logger = logging.getLogger(__name__)


async def create_app() -> web.Application:
    """Create and configure the aiohttp application."""
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

    # Create JSONL service
    from dashboard.services.jsonl_service import JSONLService

    jsonl_svc = JSONLService(
        event_bus=event_bus,
        max_lines=config.jsonl_max_lines,
        max_content_length=config.jsonl_max_content_length,
    )

    # Create usage service (if API key configured and poller enabled)
    usage_svc = None
    has_usage_keys = config.anthropic_api_key or config.openai_api_key
    if has_usage_keys and config.enable_usage_poller:
        from dashboard.services.usage_service import UsageService

        usage_svc = UsageService(
            anthropic_api_key=config.anthropic_api_key,
            openai_api_key=config.openai_api_key,
            event_bus=event_bus,
            session_svc=session_svc,
            poll_model_anthropic=config.usage_poll_model_anthropic,
            poll_model_openai=config.usage_poll_model_openai,
            enable_command_queue=config.enable_command_queue,
            retention_hours=config.usage_retention_hours,
        )
        logger.info(
            "Usage service created (providers=%s, cmd_queue=%s)",
            usage_svc.providers,
            config.enable_command_queue,
        )

    # Create app with middleware
    app = web.Application(
        middlewares=[
            access_log_middleware,
            rate_limit_middleware,
            auth_middleware,
        ]
    )

    # Store config and services on the app for handler access
    app["config"] = config
    app["event_bus"] = event_bus
    app["session_service"] = session_svc
    app["platform_service"] = platform_svc
    app["message_service"] = message_svc
    app["jsonl_service"] = jsonl_svc
    app["ws_clients"] = set()
    if usage_svc:
        app["usage_service"] = usage_svc

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
    app.router.add_get("/api/sessions/{name}/export", sessions_api.export_session)
    app.router.add_post("/api/sessions", sessions_api.create_session)
    app.router.add_delete("/api/sessions/{name}", sessions_api.delete_session)
    app.router.add_post(
        "/api/sessions/{name}/send", sessions_api.send_message
    )
    app.router.add_get(
        "/api/sessions/{name}/messages", sessions_api.get_session_messages
    )
    app.router.add_post(
        "/api/sessions/{name}/sync", sessions_api.sync_session_messages
    )
    app.router.add_patch(
        "/api/sessions/{name}", sessions_api.update_session
    )
    app.router.add_post(
        "/api/sessions/bulk-delete", sessions_api.bulk_delete_sessions
    )
    app.router.add_post(
        "/api/sessions/{name}/ingest-jsonl", sessions_api.ingest_jsonl
    )

    # Preferences
    app.router.add_get("/api/preferences", prefs_api.get_preferences)
    app.router.add_put("/api/preferences", prefs_api.set_preferences)
    app.router.add_get("/api/preferences/{key}", prefs_api.get_preference)
    app.router.add_put("/api/preferences/{key}", prefs_api.set_preference)

    # Stats
    app.router.add_get("/api/stats", stats_api.get_stats)
    app.router.add_get("/api/messages/search", search_api.search_messages)

    # WebSocket
    app.router.add_get("/ws", ws_api.websocket_handler)

    # Bridge webhook (internal, no auth)
    app.router.add_post(
        "/api/hooks/event", sessions_api.receive_bridge_event
    )

    # Usage monitoring
    app.router.add_get("/api/usage", usage_api.get_current_usage)
    app.router.add_get("/api/usage/history", usage_api.get_usage_history)
    app.router.add_get("/api/usage/summary", usage_api.get_usage_summary)
    app.router.add_get("/api/usage/queue", usage_api.list_queue)
    app.router.add_post("/api/usage/queue", usage_api.enqueue_command)
    app.router.add_delete("/api/usage/queue/{id}", usage_api.cancel_queue_command)
    app.router.add_post("/api/usage/queue/execute", usage_api.execute_queue)

    # Settings
    settings_api.setup_routes(app)

    # Health check (no auth)
    app.router.add_get("/healthz", _healthz)


def _setup_page_routes(app: web.Application) -> None:
    """Register server-rendered page routes.

    These routes serve Jinja2 templates if available, otherwise return
    a simple JSON response indicating the dashboard is running.
    """
    app.router.add_get("/login", _login_page)
    app.router.add_post("/login", _login_submit)
    app.router.add_post("/logout", _logout)
    app.router.add_get("/", _index_page)
    app.router.add_get("/sessions", _sessions_page)
    app.router.add_get("/sessions/{name}", _session_detail_page)
    app.router.add_get("/settings", _settings_page)


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


async def _get_theme() -> str:
    """Get the current theme preference."""
    try:
        row = await db.fetchone(
            "SELECT value FROM kv WHERE key = ?", ("pref:theme",)
        )
        return row["value"] if row else "dark"
    except Exception:
        return "dark"


async def _page_context(request: web.Request, **extra: str) -> dict:
    """Build common template context for page handlers."""
    ctx: dict = {"theme": await _get_theme(), **extra}
    config = request.app.get("config")
    # Only pass ws_token if authenticated via cookie (token already known to server)
    if config and config.dashboard_token:
        cookie_value = request.cookies.get(COOKIE_NAME, "")
        if validate_session_cookie(cookie_value, config.dashboard_token):
            ctx["ws_token"] = config.dashboard_token
    return ctx


async def _login_page(request: web.Request) -> web.Response:
    """GET /login - Login page."""
    config = request.app.get("config")
    # If no auth configured, redirect to home
    if not config or not config.dashboard_token:
        raise web.HTTPFound("/")
    # If already authenticated via cookie, redirect to home
    cookie_value = request.cookies.get(COOKIE_NAME, "")
    if validate_session_cookie(cookie_value, config.dashboard_token):
        raise web.HTTPFound("/")
    try:
        import aiohttp_jinja2

        next_url = request.query.get("next", "/")
        ctx = {"theme": await _get_theme(), "next": next_url, "error": ""}
        return aiohttp_jinja2.render_template("login.html", request, ctx)
    except (ImportError, Exception):
        return web.json_response(
            {"error": "Login page requires templates. Use Authorization: Bearer header."},
            status=401,
        )


async def _login_submit(request: web.Request) -> web.Response:
    """POST /login - Validate token and set session cookie."""
    import hmac as _hmac

    config = request.app.get("config")
    if not config or not config.dashboard_token:
        raise web.HTTPFound("/")

    data = await request.post()
    token = data.get("token", "")
    next_url = data.get("next", "/")

    # Validate next URL (prevent open redirect)
    if not next_url.startswith("/") or next_url.startswith("//"):
        next_url = "/"

    if token and _hmac.compare_digest(token, config.dashboard_token):
        # Set session cookie and redirect
        cookie_value = create_session_cookie(config.dashboard_token)
        response = web.HTTPFound(next_url)
        response.set_cookie(
            COOKIE_NAME,
            cookie_value,
            max_age=COOKIE_MAX_AGE,
            httponly=True,
            samesite="Lax",
            path="/",
        )
        raise response

    # Invalid token — re-render login with error
    try:
        import aiohttp_jinja2

        ctx = {
            "theme": await _get_theme(),
            "next": next_url,
            "error": "Invalid token",
        }
        return aiohttp_jinja2.render_template(
            "login.html", request, ctx, status=401
        )
    except (ImportError, Exception):
        return web.json_response({"error": "Invalid token"}, status=401)


async def _logout(request: web.Request) -> web.Response:
    """POST /logout - Clear session cookie."""
    response = web.HTTPFound("/login")
    response.del_cookie(COOKIE_NAME, path="/")
    raise response


async def _index_page(request: web.Request) -> web.Response:
    """GET / - Dashboard home page."""
    try:
        import aiohttp_jinja2

        ctx = await _page_context(request)
        return aiohttp_jinja2.render_template("index.html", request, ctx)
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

        ctx = await _page_context(request)
        return aiohttp_jinja2.render_template("sessions.html", request, ctx)
    except (ImportError, Exception):
        return web.json_response(
            {"redirect": "/api/sessions", "message": "Templates not available"}
        )


async def _session_detail_page(request: web.Request) -> web.Response:
    """GET /sessions/{name} - Session detail page."""
    name = request.match_info["name"]
    try:
        import aiohttp_jinja2

        ctx = await _page_context(request, session_name=name)
        return aiohttp_jinja2.render_template(
            "session_detail.html", request, ctx
        )
    except (ImportError, Exception):
        return web.json_response(
            {
                "redirect": f"/api/sessions/{name}",
                "message": "Templates not available",
            }
        )


async def _settings_page(request: web.Request) -> web.Response:
    """GET /settings - Settings page."""
    try:
        import aiohttp_jinja2

        ctx = await _page_context(request)
        return aiohttp_jinja2.render_template("settings.html", request, ctx)
    except (ImportError, Exception):
        return web.json_response(
            {"redirect": "/api/settings", "message": "Templates not available"}
        )


async def _on_startup(app: web.Application) -> None:
    """Start background workers on application startup."""
    config: Config = app["config"]
    session_svc: SessionService = app["session_service"]
    platform_svc: PlatformService = app["platform_service"]
    event_bus: EventBus = app["event_bus"]

    app["_worker_tasks"] = []
    message_svc: MessageService = app["message_service"]

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

    # Start message sync worker (Discord/Slack → messages table)
    if platform_svc.has_discord or platform_svc.has_slack:
        task = asyncio.create_task(
            message_sync_worker(
                platform_svc, message_svc, event_bus, interval=300
            )
        )
        app["_worker_tasks"].append(task)
        logger.info("Message sync worker started (interval=300s)")

    # Start JSONL ingester worker
    if config.enable_jsonl_ingester:
        from dashboard.workers.jsonl_ingester import jsonl_ingester

        jsonl_svc_ref = app["jsonl_service"]
        task = asyncio.create_task(
            jsonl_ingester(jsonl_svc_ref, interval=config.jsonl_scan_interval)
        )
        app["_worker_tasks"].append(task)
        logger.info(
            "JSONL ingester started (interval=%ds)", config.jsonl_scan_interval
        )

    # Start usage poller worker
    usage_svc_ref = app.get("usage_service")
    if config.enable_usage_poller and usage_svc_ref:
        from dashboard.workers.usage_poller import usage_poller as _usage_poller

        task = asyncio.create_task(
            _usage_poller(usage_svc_ref, interval=config.usage_poll_interval)
        )
        app["_worker_tasks"].append(task)
        logger.info(
            "Usage poller started (interval=%ds, providers=%s)",
            config.usage_poll_interval,
            usage_svc_ref.providers,
        )

    logger.info("Dashboard started on %s:%d", config.host, config.port)


async def _on_cleanup(app: web.Application) -> None:
    """Stop background workers, drain WebSockets, and close database on shutdown."""
    # Cancel workers
    for task in app.get("_worker_tasks", []):
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    # Close all WebSocket connections gracefully
    ws_clients: set = app.get("ws_clients", set())
    if ws_clients:
        logger.info("Closing %d WebSocket connections", len(ws_clients))
        close_tasks = []
        for ws in list(ws_clients):
            close_tasks.append(ws.close(code=1001, message=b"Server shutting down"))
        await asyncio.gather(*close_tasks, return_exceptions=True)

    # Close usage service HTTP session
    usage_svc = app.get("usage_service")
    if usage_svc:
        await usage_svc.close()

    # Close shared HTTP session
    platform_svc: PlatformService = app["platform_service"]
    await platform_svc.close()

    # Close database
    await close_db()
    logger.info("Dashboard shutdown complete")
