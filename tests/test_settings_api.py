"""Tests for /api/settings endpoints."""

from __future__ import annotations

import pytest


# ---- GET /api/settings ----

@pytest.mark.asyncio
async def test_get_settings_returns_defaults(client):
    """GET /api/settings returns settings with expected default keys."""
    resp = await client.get("/api/settings")
    assert resp.status == 200
    data = await resp.json()
    settings = data["settings"]

    # Should include known default keys
    assert "dashboard_url" in settings
    assert "ssh_hosts" in settings
    assert "discord_configured" in settings
    assert "slack_configured" in settings
    assert "enable_session_poller" in settings
    assert "poll_interval" in settings
    assert "enable_jsonl_ingester" in settings
    assert "jsonl_scan_interval" in settings
    assert "github_repo" in settings


@pytest.mark.asyncio
async def test_get_settings_discord_not_configured(client):
    """Without Discord credentials, discord_configured should be false."""
    resp = await client.get("/api/settings")
    data = await resp.json()
    assert data["settings"]["discord_configured"] == "false"


@pytest.mark.asyncio
async def test_get_settings_slack_not_configured(client):
    """Without Slack credentials, slack_configured should be false."""
    resp = await client.get("/api/settings")
    data = await resp.json()
    assert data["settings"]["slack_configured"] == "false"


@pytest.mark.asyncio
async def test_get_settings_ssh_hosts_from_config(client):
    """ssh_hosts should be populated from the config."""
    resp = await client.get("/api/settings")
    data = await resp.json()
    assert data["settings"]["ssh_hosts"] == "testhost"


# ---- PUT /api/settings ----

@pytest.mark.asyncio
async def test_put_settings_updates_writable_keys(client):
    """PUT /api/settings should update writable keys."""
    resp = await client.put(
        "/api/settings",
        json={
            "dashboard_url": "https://my-dashboard.example.com",
            "poll_interval": "120",
        },
    )
    assert resp.status == 200
    data = await resp.json()
    assert "dashboard_url" in data["updated"]
    assert "poll_interval" in data["updated"]


@pytest.mark.asyncio
async def test_put_settings_ignores_readonly_keys(client):
    """PUT /api/settings should silently ignore read-only keys."""
    resp = await client.put(
        "/api/settings",
        json={"discord_configured": "true"},
    )
    assert resp.status == 200
    data = await resp.json()
    assert "discord_configured" not in data["updated"]


@pytest.mark.asyncio
async def test_put_settings_ignores_unknown_keys(client):
    """PUT /api/settings should silently ignore unknown keys."""
    resp = await client.put(
        "/api/settings",
        json={"totally_unknown_key": "value"},
    )
    assert resp.status == 200
    data = await resp.json()
    assert data["updated"] == []


@pytest.mark.asyncio
async def test_put_settings_persists(client):
    """Updated settings should be visible on subsequent GET."""
    await client.put(
        "/api/settings",
        json={"dashboard_url": "https://updated.example.com"},
    )

    resp = await client.get("/api/settings")
    assert resp.status == 200
    data = await resp.json()
    assert data["settings"]["dashboard_url"] == "https://updated.example.com"


# ---- GET /api/install.sh ----

@pytest.mark.asyncio
async def test_get_install_script(client):
    """GET /api/install.sh returns a shell script."""
    resp = await client.get("/api/install.sh")
    assert resp.status == 200
    text = await resp.text()
    assert text.startswith("#!/bin/bash")
    assert "aily" in text
    # Content-Type should be text/plain
    assert "text/plain" in resp.headers.get("Content-Type", "")


@pytest.mark.asyncio
async def test_get_install_script_contains_repo(client):
    """Install script should reference the configured github repo."""
    resp = await client.get("/api/install.sh")
    text = await resp.text()
    assert "jiunbae/aily" in text


# ---- GET /api/settings/hooks ----

@pytest.mark.asyncio
async def test_get_hooks_info(client):
    """GET /api/settings/hooks returns hook metadata."""
    resp = await client.get("/api/settings/hooks")
    assert resp.status == 200
    data = await resp.json()
    assert "hooks" in data
    assert "install_command" in data
    assert "dashboard_url" in data
    hook_names = [h["name"] for h in data["hooks"]]
    assert "post.sh" in hook_names
