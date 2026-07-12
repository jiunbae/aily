"""Tests for small helpers in agent-bridge.py."""

from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path


def _load_agent_bridge():
    path = Path(__file__).resolve().parents[1] / "agent-bridge.py"
    spec = importlib.util.spec_from_file_location("agent_bridge_for_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_gateway_ws_url_adds_version_params():
    module = _load_agent_bridge()

    assert (
        module._gateway_ws_url("wss://gateway.discord.gg")
        == "wss://gateway.discord.gg?v=10&encoding=json"
    )


def test_gateway_ws_url_preserves_existing_query():
    module = _load_agent_bridge()

    assert (
        module._gateway_ws_url("wss://gateway.discord.gg?compress=zlib-stream")
        == "wss://gateway.discord.gg?compress=zlib-stream&v=10&encoding=json"
    )


def test_all_non_reconnectable_gateway_codes_are_fatal():
    module = _load_agent_bridge()

    assert set(module._FATAL_GATEWAY_CLOSE_REASONS) == {
        4004, 4010, 4011, 4012, 4013, 4014,
    }


class _FakeWebSocket:
    def __init__(self):
        self.sent = []
        self.closed = []

    async def send_json(self, payload):
        self.sent.append(payload)

    async def close(self, **kwargs):
        self.closed.append(kwargs)


def test_send_heartbeat_marks_ack_pending():
    module = _load_agent_bridge()
    ws = _FakeWebSocket()
    acked = asyncio.Event()
    acked.set()

    asyncio.run(module.send_heartbeat(ws, 42, acked))

    assert ws.sent == [{"op": 1, "d": 42}]
    assert not acked.is_set()


def test_heartbeat_loop_closes_connection_when_ack_is_missing(monkeypatch):
    module = _load_agent_bridge()
    ws = _FakeWebSocket()
    acked = asyncio.Event()
    acked.set()
    monkeypatch.setattr(module.random, "random", lambda: 0.0)

    async def run_loop():
        await module.heartbeat_loop(ws, 0, lambda: 7, acked)

    asyncio.run(run_loop())

    assert ws.sent == [{"op": 1, "d": 7}]
    assert ws.closed == [{"code": 4000, "message": b"heartbeat ACK timeout"}]
