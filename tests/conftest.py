"""Shared pytest fixtures for the aily dashboard test suite.

Provides:
- In-memory SQLite database (no disk I/O)
- Mock Config with no real SSH/Discord/Slack credentials
- aiohttp test client wired to the dashboard app
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from dashboard.auth import auth_middleware
from dashboard.config import Config
from dashboard.db import init_db, close_db, SCHEMA_SQL
from dashboard.rate_limit import rate_limit_middleware
from dashboard.services.event_bus import EventBus
from dashboard.services.message_service import MessageService
from dashboard.services.platform_service import PlatformService
from dashboard.services.session_service import SessionService
from dashboard.services.jsonl_service import JSONLService

from dashboard.api import sessions as sessions_api
from dashboard.api import settings as settings_api
from dashboard.api import preferences as prefs_api
from dashboard.api import stats as stats_api
from dashboard.api import ws as ws_api


def _make_config(**overrides) -> Config:
    """Create a test Config with safe defaults (no real credentials)."""
    defaults = dict(
        host="127.0.0.1",
        port=0,
        db_path=":memory:",
        ssh_hosts=["testhost"],
        discord_bot_token="",
        discord_channel_id="",
        slack_bot_token="",
        slack_app_token="",
        slack_channel_id="",
        dashboard_token="",
        dashboard_url="http://localhost:0",
        github_repo="jiunbae/aily",
        poll_interval=30,
        ingest_interval=15,
        enable_session_poller=False,
        enable_message_ingester=False,
        enable_platform_sync=False,
        enable_jsonl_ingester=False,
        jsonl_scan_interval=60,
        jsonl_max_lines=500,
        jsonl_max_content_length=5000,
        env_file="",
    )
    defaults.update(overrides)
    return Config(**defaults)


async def _build_app(config: Config | None = None) -> web.Application:
    """Build the aiohttp app the same way app.py does, but with test wiring."""
    if config is None:
        config = _make_config()

    # Initialize in-memory database
    await init_db(config.db_path)

    # Create services
    event_bus = EventBus()
    session_svc = SessionService(ssh_hosts=config.ssh_hosts)
    platform_svc = PlatformService()
    message_svc = MessageService(event_bus=event_bus)
    jsonl_svc = JSONLService(
        event_bus=event_bus,
        max_lines=config.jsonl_max_lines,
        max_content_length=config.jsonl_max_content_length,
    )

    app = web.Application(
        middlewares=[
            rate_limit_middleware,
            auth_middleware,
        ]
    )

    app["config"] = config
    app["event_bus"] = event_bus
    app["session_service"] = session_svc
    app["platform_service"] = platform_svc
    app["message_service"] = message_svc
    app["jsonl_service"] = jsonl_svc
    app["ws_clients"] = set()

    # -- routes (same as app._setup_routes) --
    app.router.add_get("/api/sessions", sessions_api.list_sessions)
    app.router.add_get("/api/sessions/{name}", sessions_api.get_session)
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

    # WebSocket
    app.router.add_get("/ws", ws_api.websocket_handler)

    # Bridge webhook
    app.router.add_post("/api/hooks/event", sessions_api.receive_bridge_event)

    # Settings
    settings_api.setup_routes(app)

    # Health check
    async def _healthz(request: web.Request) -> web.Response:
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

    app.router.add_get("/healthz", _healthz)

    return app


@pytest.fixture
def config():
    """Default test config (no auth, no platforms)."""
    return _make_config()


@pytest.fixture(autouse=True)
def _reset_rate_limit_state():
    """Reset global in-memory rate limit buckets between tests."""
    from dashboard import rate_limit

    rate_limit._buckets.clear()
    yield
    rate_limit._buckets.clear()


@pytest.fixture
def auth_config():
    """Config with dashboard_token set for auth tests."""
    return _make_config(dashboard_token="test-secret-token")


@pytest_asyncio.fixture
async def client(aiohttp_client, config):
    """aiohttp test client backed by in-memory SQLite. No auth."""
    app = await _build_app(config)
    c = await aiohttp_client(app)
    yield c
    await close_db()


@pytest_asyncio.fixture
async def auth_client(aiohttp_client, auth_config):
    """aiohttp test client with auth enabled."""
    app = await _build_app(auth_config)
    c = await aiohttp_client(app)
    yield c
    await close_db()
