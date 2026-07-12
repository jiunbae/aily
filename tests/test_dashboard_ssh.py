"""Tests for local and failed dashboard session command execution."""

from __future__ import annotations

import pytest

from dashboard import ssh


class _Process:
    returncode = 0

    async def communicate(self):
        return b"ok\n", b""


@pytest.mark.asyncio
async def test_localhost_runs_without_ssh_daemon(monkeypatch):
    calls = []

    async def create(*args, **kwargs):
        calls.append((args, kwargs))
        return _Process()

    monkeypatch.setattr("asyncio.create_subprocess_exec", create)

    rc, out = await ssh.run_ssh("localhost", "printf ok")

    assert (rc, out) == (0, "ok")
    assert calls[0][0][:3] == ("bash", "-c", "printf ok")


@pytest.mark.asyncio
async def test_list_sessions_preserves_transport_failure(monkeypatch):
    async def fail(*args, **kwargs):
        return 255, ""

    monkeypatch.setattr(ssh, "run_ssh", fail)

    with pytest.raises(ssh.SSHCommandError):
        await ssh.list_sessions("host-a")
