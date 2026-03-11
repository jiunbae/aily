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

from dashboard.app import _setup_routes
from dashboard.auth import auth_middleware
from dashboard.config import Config
from dashboard.db import init_db, close_db, SCHEMA_SQL
from dashboard.rate_limit import rate_limit_middleware
from dashboard.services.event_bus import EventBus
from dashboard.services.message_service import MessageService
from dashboard.services.platform_service import PlatformService
from dashboard.services.session_service import SessionService
from dashboard.services.jsonl_service import JSONLService


TEST_TOKEN = "test-secret-token"


def _make_config(**overrides) -> Config:
    """Create a test Config with safe defaults (no real credentials).

    Auth is always enabled (dashboard_token set) to match production behavior.
    """
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
        dashboard_token=TEST_TOKEN,
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

    # Register all API routes (reuse the real setup from app.py)
    _setup_routes(app)

    return app


@pytest.fixture
def config():
    """Default test config (auth enabled, no platforms)."""
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
    """Config with dashboard_token set for auth tests (same as default)."""
    return _make_config(dashboard_token=TEST_TOKEN)


@pytest.fixture
def noauth_config():
    """Config with empty dashboard_token for testing auth enforcement."""
    return _make_config(dashboard_token="")


class AuthenticatedClient:
    """Wraps aiohttp TestClient to auto-inject Bearer token on every request."""

    def __init__(self, raw_client: TestClient, token: str):
        self._client = raw_client
        self._token = token
        self._auth_headers = {"Authorization": f"Bearer {token}"}

    def _merge_headers(self, kwargs):
        headers = dict(self._auth_headers)
        if "headers" in kwargs:
            headers.update(kwargs["headers"])
        kwargs["headers"] = headers
        return kwargs

    async def get(self, path, **kwargs):
        return await self._client.get(path, **self._merge_headers(kwargs))

    async def post(self, path, **kwargs):
        return await self._client.post(path, **self._merge_headers(kwargs))

    async def put(self, path, **kwargs):
        return await self._client.put(path, **self._merge_headers(kwargs))

    async def patch(self, path, **kwargs):
        return await self._client.patch(path, **self._merge_headers(kwargs))

    async def delete(self, path, **kwargs):
        return await self._client.delete(path, **self._merge_headers(kwargs))

    @property
    def session(self):
        return self._client.session


@pytest_asyncio.fixture
async def client(aiohttp_client, config):
    """Authenticated test client — auto-injects Bearer token on every request."""
    app = await _build_app(config)
    raw = await aiohttp_client(app)
    yield AuthenticatedClient(raw, TEST_TOKEN)
    await close_db()


@pytest_asyncio.fixture
async def auth_client(aiohttp_client, auth_config):
    """Raw test client with auth enabled (no auto-inject, for auth-specific tests)."""
    app = await _build_app(auth_config)
    c = await aiohttp_client(app)
    yield c
    await close_db()


@pytest_asyncio.fixture
async def noauth_client(aiohttp_client, noauth_config):
    """Test client with empty token — for testing auth enforcement."""
    app = await _build_app(noauth_config)
    c = await aiohttp_client(app)
    yield c
    await close_db()
