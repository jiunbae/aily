"""Regression tests for Slack Socket Mode connection health checks."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


def _load_slack_bridge():
    path = Path(__file__).resolve().parents[1] / "slack-bridge.py"
    spec = importlib.util.spec_from_file_location("slack_bridge_for_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.asyncio
async def test_socket_health_awaits_async_sdk_method():
    module = _load_slack_bridge()

    class Client:
        async def is_connected(self):
            return False

    assert await module._socket_mode_is_connected(Client()) is False


@pytest.mark.asyncio
async def test_socket_health_supports_sync_sdk_method():
    module = _load_slack_bridge()

    class Client:
        def is_connected(self):
            return True

    assert await module._socket_mode_is_connected(Client()) is True
