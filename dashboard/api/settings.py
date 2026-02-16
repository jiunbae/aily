"""System settings API.

GET  /api/settings           -- Get all system settings
PUT  /api/settings           -- Update system settings (merge)
GET  /api/settings/hooks     -- Get hook installation instructions
GET  /api/install.sh         -- Downloadable installer script
POST /api/settings/test      -- Test connectivity (dashboard, platforms, SSH)

Settings are stored in the kv table with a "setting:" key prefix.
Distinct from user preferences (pref: prefix).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time

from aiohttp import web

from dashboard import db
from dashboard.api import error_response, json_ok

logger = logging.getLogger(__name__)

# Default settings schema
DEFAULTS: dict[str, str] = {
    "dashboard_url": "https://aily.jiun.dev",
    "ssh_hosts": "",
    "discord_configured": "",
    "slack_configured": "",
    "enable_session_poller": "true",
    "poll_interval": "30",
    "enable_jsonl_ingester": "false",
    "jsonl_scan_interval": "60",
}

# Keys that can be written by the user
WRITABLE_KEYS = frozenset({
    "dashboard_url",
    "ssh_hosts",
    "enable_session_poller",
    "poll_interval",
    "enable_jsonl_ingester",
    "jsonl_scan_interval",
})

# Read-only keys derived from runtime config
READONLY_KEYS = frozenset({
    "discord_configured",
    "slack_configured",
})

SETTING_PREFIX = "setting:"


def _get_runtime_settings(request: web.Request) -> dict[str, str]:
    """Derive read-only settings from runtime config."""
    config = request.app.get("config")
    if not config:
        return {}

    return {
        "discord_configured": "true" if config.discord_bot_token else "false",
        "slack_configured": "true" if config.slack_bot_token else "false",
    }


async def get_settings(request: web.Request) -> web.Response:
    """GET /api/settings

    Returns all system settings merged with defaults and runtime values.
    """
    rows = await db.fetchall(
        "SELECT key, value FROM kv WHERE key LIKE 'setting:%'"
    )

    settings = dict(DEFAULTS)
    for row in rows:
        key = row["key"].removeprefix(SETTING_PREFIX)
        if key in DEFAULTS:
            settings[key] = row["value"]

    # Override with runtime-derived values
    settings.update(_get_runtime_settings(request))

    # Also populate ssh_hosts from config if not explicitly set
    config = request.app.get("config")
    if config and not settings["ssh_hosts"]:
        settings["ssh_hosts"] = ",".join(config.ssh_hosts)

    # Include config-derived values for the frontend
    settings["enable_session_poller"] = (
        str(config.enable_session_poller).lower() if config else "true"
    )
    settings["poll_interval"] = str(config.poll_interval) if config else "30"
    settings["enable_jsonl_ingester"] = (
        str(config.enable_jsonl_ingester).lower() if config else "false"
    )
    settings["jsonl_scan_interval"] = (
        str(config.jsonl_scan_interval) if config else "60"
    )

    return json_ok({"settings": settings})


async def put_settings(request: web.Request) -> web.Response:
    """PUT /api/settings

    Merge provided settings. Only writable keys are accepted.
    Request body: {"dashboard_url": "https://...", "poll_interval": "60"}
    """
    try:
        body = await request.json()
    except (json.JSONDecodeError, Exception):
        return error_response(400, "INVALID_JSON", "Request body must be JSON")

    now = db.now_iso()
    updated_keys: list[str] = []

    for key, value in body.items():
        if key not in WRITABLE_KEYS:
            continue
        value_str = str(value)

        await db.execute(
            """INSERT INTO kv (key, value, updated)
               VALUES (?, ?, ?)
               ON CONFLICT(key) DO UPDATE SET value = ?, updated = ?""",
            (f"{SETTING_PREFIX}{key}", value_str, now, value_str, now),
        )
        updated_keys.append(key)

    return json_ok({"updated": updated_keys})


async def get_hooks(request: web.Request) -> web.Response:
    """GET /api/settings/hooks

    Returns hook installation instructions and commands.
    """
    config = request.app.get("config")
    dashboard_url = "https://aily.jiun.dev"

    # Try to get from stored settings
    row = await db.fetchone(
        "SELECT value FROM kv WHERE key = ?",
        (f"{SETTING_PREFIX}dashboard_url",),
    )
    if row and row["value"]:
        dashboard_url = row["value"]

    install_script = f"curl -sSL {dashboard_url}/api/settings/hooks/install.sh | bash"
    git_clone = "git clone https://github.com/jiunbae/aily && cd aily && ./aily init"

    hooks = [
        {
            "name": "post.sh",
            "description": "Event dispatcher hook (sends events to dashboard)",
            "path": "hooks/post.sh",
        },
        {
            "name": "slack-post.sh",
            "description": "Slack notification hook",
            "path": "hooks/slack-post.sh",
        },
        {
            "name": "slack-lib.sh",
            "description": "Slack API helper library",
            "path": "hooks/slack-lib.sh",
        },
        {
            "name": "thread-sync.sh",
            "description": "Thread synchronization hook",
            "path": "hooks/thread-sync.sh",
        },
    ]

    return json_ok({
        "install_command": install_script,
        "git_clone_command": git_clone,
        "dashboard_url": dashboard_url,
        "hooks": hooks,
    })


async def test_connection(request: web.Request) -> web.Response:
    """POST /api/settings/test

    Test connectivity to various services.
    Body: {"type": "dashboard"|"discord"|"slack"|"ssh", "host": "..."}
    """
    try:
        body = await request.json()
    except (json.JSONDecodeError, Exception):
        return error_response(400, "INVALID_JSON", "Request body must be JSON")

    test_type = str(body.get("type", "")).lower()
    host = str(body.get("host", "")).strip()

    if test_type not in ("dashboard", "discord", "slack", "ssh"):
        return error_response(
            400, "INVALID_TYPE",
            "Test type must be one of: dashboard, discord, slack, ssh"
        )

    result = {"type": test_type, "status": "unknown", "message": "", "details": {}}

    try:
        if test_type == "dashboard":
            result = await _test_dashboard(request)
        elif test_type == "discord":
            result = await _test_discord(request)
        elif test_type == "slack":
            result = await _test_slack(request)
        elif test_type == "ssh":
            if not host:
                return error_response(
                    400, "MISSING_HOST", "SSH test requires a host parameter"
                )
            result = await _test_ssh(host)
    except Exception as exc:
        result["status"] = "error"
        result["message"] = str(exc)

    return json_ok({"result": result})


async def _test_dashboard(request: web.Request) -> dict:
    """Test dashboard health endpoint."""
    import aiohttp

    config = request.app.get("config")
    dashboard_url = "https://aily.jiun.dev"

    row = await db.fetchone(
        "SELECT value FROM kv WHERE key = ?",
        (f"{SETTING_PREFIX}dashboard_url",),
    )
    if row and row["value"]:
        dashboard_url = row["value"]

    start = time.monotonic()
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{dashboard_url}/healthz", timeout=aiohttp.ClientTimeout(total=5)
            ) as resp:
                elapsed = round((time.monotonic() - start) * 1000)
                data = await resp.json()
                return {
                    "type": "dashboard",
                    "status": "ok" if resp.status == 200 else "error",
                    "message": f"Dashboard reachable ({elapsed}ms)",
                    "details": {
                        "url": dashboard_url,
                        "status_code": resp.status,
                        "response_ms": elapsed,
                        "database": data.get("database", "unknown"),
                    },
                }
    except Exception as exc:
        elapsed = round((time.monotonic() - start) * 1000)
        return {
            "type": "dashboard",
            "status": "error",
            "message": f"Dashboard unreachable: {exc}",
            "details": {"url": dashboard_url, "response_ms": elapsed},
        }


async def _test_discord(request: web.Request) -> dict:
    """Test Discord bot token by calling /users/@me."""
    config = request.app.get("config")
    if not config or not config.discord_bot_token:
        return {
            "type": "discord",
            "status": "not_configured",
            "message": "Discord bot token not set",
            "details": {},
        }

    import aiohttp

    start = time.monotonic()
    try:
        async with aiohttp.ClientSession() as session:
            headers = {"Authorization": f"Bot {config.discord_bot_token}"}
            async with session.get(
                "https://discord.com/api/v10/users/@me",
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                elapsed = round((time.monotonic() - start) * 1000)
                if resp.status == 200:
                    data = await resp.json()
                    return {
                        "type": "discord",
                        "status": "ok",
                        "message": f"Bot connected as {data.get('username', 'unknown')}",
                        "details": {
                            "bot_name": data.get("username", ""),
                            "bot_id": data.get("id", ""),
                            "channel_id": config.discord_channel_id or "not set",
                            "response_ms": elapsed,
                        },
                    }
                else:
                    return {
                        "type": "discord",
                        "status": "error",
                        "message": f"Discord API returned {resp.status}",
                        "details": {"status_code": resp.status, "response_ms": elapsed},
                    }
    except Exception as exc:
        elapsed = round((time.monotonic() - start) * 1000)
        return {
            "type": "discord",
            "status": "error",
            "message": f"Discord test failed: {exc}",
            "details": {"response_ms": elapsed},
        }


async def _test_slack(request: web.Request) -> dict:
    """Test Slack bot token by calling auth.test."""
    config = request.app.get("config")
    if not config or not config.slack_bot_token:
        return {
            "type": "slack",
            "status": "not_configured",
            "message": "Slack bot token not set",
            "details": {},
        }

    import aiohttp

    start = time.monotonic()
    try:
        async with aiohttp.ClientSession() as session:
            headers = {"Authorization": f"Bearer {config.slack_bot_token}"}
            async with session.post(
                "https://slack.com/api/auth.test",
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                elapsed = round((time.monotonic() - start) * 1000)
                data = await resp.json()
                if data.get("ok"):
                    return {
                        "type": "slack",
                        "status": "ok",
                        "message": f"Bot connected as {data.get('user', 'unknown')}",
                        "details": {
                            "bot_name": data.get("user", ""),
                            "team": data.get("team", ""),
                            "channel_id": config.slack_channel_id or "not set",
                            "response_ms": elapsed,
                        },
                    }
                else:
                    return {
                        "type": "slack",
                        "status": "error",
                        "message": f"Slack auth failed: {data.get('error', 'unknown')}",
                        "details": {"error": data.get("error", ""), "response_ms": elapsed},
                    }
    except Exception as exc:
        elapsed = round((time.monotonic() - start) * 1000)
        return {
            "type": "slack",
            "status": "error",
            "message": f"Slack test failed: {exc}",
            "details": {"response_ms": elapsed},
        }


async def _test_ssh(host: str) -> dict:
    """Test SSH connectivity to a host."""
    start = time.monotonic()
    try:
        proc = await asyncio.create_subprocess_exec(
            "ssh", "-o", "ConnectTimeout=5",
            "-o", "StrictHostKeyChecking=accept-new",
            "-o", "BatchMode=yes",
            host, "tmux", "list-sessions", "-F", "#{session_name}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=10)
        elapsed = round((time.monotonic() - start) * 1000)

        if proc.returncode == 0:
            sessions = [
                s.strip() for s in stdout.decode().strip().split("\n") if s.strip()
            ]
            return {
                "type": "ssh",
                "status": "ok",
                "message": f"Connected to {host} ({len(sessions)} tmux sessions)",
                "details": {
                    "host": host,
                    "tmux_sessions": len(sessions),
                    "session_names": sessions[:20],
                    "response_ms": elapsed,
                },
            }
        else:
            err_msg = stderr.decode().strip() or f"Exit code {proc.returncode}"
            return {
                "type": "ssh",
                "status": "error",
                "message": f"SSH to {host} failed: {err_msg}",
                "details": {"host": host, "response_ms": elapsed},
            }
    except asyncio.TimeoutError:
        elapsed = round((time.monotonic() - start) * 1000)
        return {
            "type": "ssh",
            "status": "error",
            "message": f"SSH to {host} timed out after 10s",
            "details": {"host": host, "response_ms": elapsed},
        }
    except Exception as exc:
        elapsed = round((time.monotonic() - start) * 1000)
        return {
            "type": "ssh",
            "status": "error",
            "message": f"SSH to {host} failed: {exc}",
            "details": {"host": host, "response_ms": elapsed},
        }


async def get_install_script(request: web.Request) -> web.Response:
    """GET /api/install.sh

    Returns a self-contained installer script that downloads and sets up
    the aily CLI tool. Can be piped to bash:
        curl -sSL https://aily.jiun.dev/api/install.sh | bash
    """
    # Determine dashboard URL from settings or request
    dashboard_url = "https://aily.jiun.dev"
    row = await db.fetchone(
        "SELECT value FROM kv WHERE key = ?",
        (f"{SETTING_PREFIX}dashboard_url",),
    )
    if row and row["value"]:
        dashboard_url = row["value"]

    script = f'''#!/bin/bash
# aily CLI installer
# Usage: curl -sSL {dashboard_url}/api/install.sh | bash
set -euo pipefail

REPO="https://raw.githubusercontent.com/jiunbae/aily/main"
INSTALL_DIR="${{AILY_INSTALL_DIR:-$HOME/.local/bin}}"
HOOKS_DIR="$HOME/.claude/hooks"

echo "=== aily CLI Installer ==="
echo ""

# Check dependencies
for cmd in curl jq; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "ERROR: $cmd is required but not found. Install it first."
    exit 1
  fi
done

# Create directories
mkdir -p "$INSTALL_DIR" "$HOOKS_DIR"

# Download aily CLI
echo "Downloading aily CLI..."
curl -sSL "$REPO/aily" -o "$INSTALL_DIR/aily"
chmod +x "$INSTALL_DIR/aily"
echo "  Installed: $INSTALL_DIR/aily"

# Download hook files
echo "Downloading hooks..."
for hook in post.sh discord-post.sh discord-lib.sh slack-post.sh slack-lib.sh \\
            thread-sync.sh discord-thread-sync.sh notify-claude.sh notify-codex.py \\
            notify-codex-wrapper.sh notify-gemini.sh extract-last-message.py \\
            format-question.py ask-question-notify.sh; do
  curl -sSL "$REPO/hooks/$hook" -o "$HOOKS_DIR/$hook" 2>/dev/null && \\
    chmod +x "$HOOKS_DIR/$hook" && \\
    echo "  $hook" || true
done

# Download install.sh and .env.example
curl -sSL "$REPO/install.sh" -o "$INSTALL_DIR/aily-install.sh"
chmod +x "$INSTALL_DIR/aily-install.sh"
curl -sSL "$REPO/.env.example" -o "$HOOKS_DIR/.env.example" 2>/dev/null || true

# Check PATH
if ! echo "$PATH" | grep -q "$INSTALL_DIR"; then
  echo ""
  echo "Add to your shell profile:"
  echo "  export PATH=\\"$INSTALL_DIR:\\$PATH\\""
  echo ""
fi

echo ""
echo "=== Installed! ==="
echo ""
echo "Run setup wizard:"
echo "  aily init"
echo ""
echo "Or with dashboard URL pre-configured:"
echo "  AILY_DASHBOARD_URL={dashboard_url} aily init"
echo ""
'''
    return web.Response(
        text=script,
        content_type="text/plain",
        headers={"Content-Disposition": 'attachment; filename="install.sh"'},
    )


def setup_routes(app: web.Application) -> None:
    """Register settings API routes on the application."""
    app.router.add_get("/api/settings", get_settings)
    app.router.add_put("/api/settings", put_settings)
    app.router.add_get("/api/settings/hooks", get_hooks)
    app.router.add_get("/api/install.sh", get_install_script)
    app.router.add_post("/api/settings/test", test_connection)
