#!/usr/bin/env python3
"""
Slack <-> tmux session bridge.

Monitors Slack threads named [agent] <session> and forwards user messages
to the corresponding tmux session's Claude Code instance via SSH.

Also handles ! commands for session/thread lifecycle management:
  !new <name> [host]  — create tmux session + Slack thread
  !kill <name>        — kill tmux session + close Slack thread
  !sessions           — list all sessions with sync status

Uses Socket Mode (WebSocket) — no public URL needed.
Requires: slack-sdk (pip install slack-sdk)
"""

import asyncio
import json
import os
import re
import shlex
import subprocess
import sys
import time
from datetime import datetime, timezone

import aiohttp

from slack_sdk.web.async_client import AsyncWebClient
from slack_sdk.socket_mode.aiohttp import SocketModeClient
from slack_sdk.socket_mode.request import SocketModeRequest
from slack_sdk.socket_mode.response import SocketModeResponse

# Dashboard webhook URL (optional — if not set, events are silently skipped)
DASHBOARD_URL: str = os.environ.get("AILY_DASHBOARD_URL", "")

# Shared aiohttp session for dashboard POSTs (set in main)
_dashboard_http: aiohttp.ClientSession | None = None


async def emit_dashboard_event(event: dict):
    """POST an event to the aily dashboard webhook. Non-blocking, fire-and-forget."""
    if not DASHBOARD_URL or _dashboard_http is None:
        return
    url = f"{DASHBOARD_URL.rstrip('/')}/api/hooks/event"
    try:
        async with _dashboard_http.post(url, json=event, timeout=aiohttp.ClientTimeout(total=5)) as resp:
            if resp.status >= 400:
                body = await resp.text()
                print(f"[dashboard] POST {resp.status}: {body[:200]}", file=sys.stderr)
    except Exception as e:
        print(f"[dashboard] POST failed: {e}", file=sys.stderr)


def _fire_dashboard_event(event: dict):
    """Schedule a dashboard event from sync or async context without awaiting."""
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(emit_dashboard_event(event))
    except RuntimeError:
        pass  # No running loop — skip silently


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def dashboard_api(method: str, path: str, json_body: dict = None) -> dict | None:
    """Call dashboard REST API and return parsed JSON response, or None on failure."""
    if not DASHBOARD_URL or _dashboard_http is None:
        return None
    url = f"{DASHBOARD_URL.rstrip('/')}{path}"
    try:
        kwargs: dict = {"timeout": aiohttp.ClientTimeout(total=10)}
        if json_body is not None:
            kwargs["json"] = json_body
        async with _dashboard_http.request(method, url, **kwargs) as resp:
            if resp.status < 400:
                return await resp.json()
            body = await resp.text()
            print(f"[dashboard] {method} {path} {resp.status}: {body[:200]}", file=sys.stderr)
            return None
    except Exception as e:
        print(f"[dashboard] {method} {path} failed: {e}", file=sys.stderr)
        return None


AGENT_PREFIX = "[agent] "
SEND_KEYS_DELAY = 0.3
SESSION_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]+$")

# Globals set at startup
CHANNEL_ID: str = ""
SSH_HOSTS: list[str] = []
DEFAULT_HOST: str = ""
BOT_USER_ID: str = ""
THREAD_CLEANUP: str = "archive"
_announced: bool = False

# Cache: thread_ts -> session_name (avoid repeated conversations.replies calls)
_thread_cache: dict[str, str] = {}


def load_env(env_path: str) -> dict:
    """Load .notify-env file."""
    env = {}
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, val = line.split("=", 1)
                env[key.strip()] = val.strip().strip('"').strip("'")
    return env


def run_ssh(host: str, cmd: str, timeout: int = 15) -> tuple[int, str]:
    """Run a command over SSH. Returns (returncode, stdout)."""
    try:
        result = subprocess.run(
            ["ssh", host, cmd],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return result.returncode, result.stdout.strip()
    except subprocess.TimeoutExpired:
        return 1, ""
    except Exception as e:
        return 1, str(e)


def is_valid_session_name(name: str) -> bool:
    """Check if session name is safe for use in shell commands."""
    return bool(SESSION_NAME_RE.match(name)) and len(name) <= 64


def find_session_host(session_name: str) -> str | None:
    """Find which SSH host has the tmux session."""
    safe_name = shlex.quote(session_name)
    for host in SSH_HOSTS:
        rc, out = run_ssh(
            host, f"tmux has-session -t {safe_name} 2>/dev/null && echo found"
        )
        if rc == 0 and "found" in out:
            return host
    return None


def send_to_tmux(host: str, session: str, message: str) -> bool:
    """Send a message to a tmux session's Claude Code."""
    safe_session = shlex.quote(session)
    safe_message = shlex.quote(message)

    # Step 1: Type the text
    rc, _ = run_ssh(host, f"tmux send-keys -t {safe_session} {safe_message}")
    if rc != 0:
        return False

    # Step 2: Press Enter (separate command — critical for Claude Code)
    time.sleep(SEND_KEYS_DELAY)
    rc, _ = run_ssh(host, f"tmux send-keys -t {safe_session} Enter")
    return rc == 0


# --- Slack REST helpers ---


async def find_thread_ts(
    client: AsyncWebClient, thread_name: str
) -> str | None:
    """Find a parent message whose text starts with thread_name."""
    try:
        result = await client.conversations_history(
            channel=CHANNEL_ID, limit=200
        )
        for msg in result.get("messages", []):
            text = msg.get("text", "")
            first_line = text.split("\n")[0].strip()
            if first_line == thread_name or text.startswith(thread_name):
                return msg["ts"]
    except Exception as e:
        print(f"[slack-bridge] find_thread_ts error: {e}", file=sys.stderr)
    return None


async def create_thread(
    client: AsyncWebClient, thread_name: str, starter_msg: str
) -> str | None:
    """Create a new thread: post parent message, then welcome reply."""
    try:
        result = await client.chat_postMessage(
            channel=CHANNEL_ID, text=starter_msg
        )
        parent_ts = result.get("ts")
        if not parent_ts:
            return None

        session_name = (
            thread_name.removeprefix(AGENT_PREFIX)
            if thread_name.startswith(AGENT_PREFIX)
            else thread_name
        )
        welcome = (
            f"*Welcome to {thread_name}*\n\n"
            "Type a message here to forward it to the tmux session.\n\n"
            "*Commands:*\n"
            "`!sessions` \u2014 list all sessions\n"
            f"`!kill {session_name}` \u2014 kill this session + close thread"
        )
        await client.chat_postMessage(
            channel=CHANNEL_ID, thread_ts=parent_ts, text=welcome
        )
        return parent_ts
    except Exception as e:
        print(f"[slack-bridge] create_thread error: {e}", file=sys.stderr)
        return None


async def ensure_thread(
    client: AsyncWebClient, thread_name: str, starter_msg: str = None
) -> str | None:
    """Find or create a thread."""
    ts = await find_thread_ts(client, thread_name)
    if ts:
        return ts
    if starter_msg is None:
        starter_msg = f"tmux session: *{thread_name}*"
    return await create_thread(client, thread_name, starter_msg)


async def archive_thread(client: AsyncWebClient, thread_ts: str):
    """Archive a thread (post closing message + lock reaction)."""
    try:
        await client.chat_postMessage(
            channel=CHANNEL_ID,
            thread_ts=thread_ts,
            text=":lock: Thread archived. Session closed.",
        )
        await client.reactions_add(
            channel=CHANNEL_ID, timestamp=thread_ts, name="lock"
        )
    except Exception as e:
        print(f"[slack-bridge] archive_thread error: {e}", file=sys.stderr)


async def delete_thread(client: AsyncWebClient, thread_ts: str):
    """Delete a thread by deleting the parent message."""
    try:
        await client.chat_delete(channel=CHANNEL_ID, ts=thread_ts)
    except Exception as e:
        print(f"[slack-bridge] delete_thread error: {e}", file=sys.stderr)


async def post_message(
    client: AsyncWebClient,
    channel_id: str,
    text: str,
    thread_ts: str = None,
):
    """Post a message, optionally in a thread."""
    if len(text) > 3800:
        text = text[:3800] + "\n...(truncated)"
    kwargs = {"channel": channel_id, "text": text}
    if thread_ts:
        kwargs["thread_ts"] = thread_ts
    try:
        await client.chat_postMessage(**kwargs)
    except Exception as e:
        print(f"[slack-bridge] post_message error: {e}", file=sys.stderr)


# --- Resolve thread parent ---


async def get_thread_session(
    client: AsyncWebClient, channel: str, thread_ts: str
) -> str | None:
    """Get session name from a thread's parent message. Uses cache."""
    if thread_ts in _thread_cache:
        return _thread_cache[thread_ts]

    try:
        result = await client.conversations_replies(
            channel=channel, ts=thread_ts, limit=1
        )
        messages = result.get("messages", [])
        if not messages:
            return None
        parent_text = messages[0].get("text", "")
    except Exception:
        return None

    first_line = parent_text.split("\n")[0].strip()
    if first_line.startswith(AGENT_PREFIX):
        session_name = first_line[len(AGENT_PREFIX) :]
        # Strip any trailing text after session name
        session_name = session_name.split()[0] if " " in session_name else session_name
        _thread_cache[thread_ts] = session_name
        return session_name

    if parent_text.startswith(AGENT_PREFIX):
        session_name = parent_text[len(AGENT_PREFIX) :].split()[0]
        _thread_cache[thread_ts] = session_name
        return session_name

    return None


# --- ! commands ---


async def handle_command(
    client: AsyncWebClient,
    channel: str,
    text: str,
    thread_ts: str = None,
):
    """Handle ! commands from Slack."""
    parts = text.split(None, 3)
    cmd = parts[0].lower() if parts else ""

    if cmd == "!new":
        await cmd_new(client, channel, parts, thread_ts)
    elif cmd == "!kill":
        await cmd_kill(client, channel, parts, thread_ts)
    elif cmd in ("!sessions", "!ls"):
        await cmd_sessions(client, channel, thread_ts)
    elif cmd == "!queue":
        await cmd_queue(client, channel, parts, thread_ts)
    else:
        await post_message(
            client,
            channel,
            "Unknown command. Available: `!new <name> [host] [pwd]`, `!kill <name>`, `!sessions`, `!queue`",
            thread_ts=thread_ts,
        )


async def cmd_new(
    client: AsyncWebClient,
    reply_to: str,
    parts: list[str],
    thread_ts: str = None,
):
    """!new <session_name> [host] [pwd] — create tmux session + Slack thread."""
    if len(parts) < 2:
        await post_message(
            client,
            reply_to,
            f"Usage: `!new <session_name> [host] [pwd]`\n"
            f"Available hosts: `{'`, `'.join(SSH_HOSTS)}`",
            thread_ts=thread_ts,
        )
        return

    session_name = parts[1]
    host = parts[2] if len(parts) > 2 else DEFAULT_HOST
    working_dir = parts[3] if len(parts) > 3 else None

    if not is_valid_session_name(session_name):
        await post_message(
            client,
            reply_to,
            "Invalid session name. Use only `a-z A-Z 0-9 _ -` (max 64 chars).",
            thread_ts=thread_ts,
        )
        return

    if host not in SSH_HOSTS:
        await post_message(
            client,
            reply_to,
            f"Unknown host `{host}`. Available: `{'`, `'.join(SSH_HOSTS)}`",
            thread_ts=thread_ts,
        )
        return

    # Check if session already exists
    existing = await asyncio.to_thread(find_session_host, session_name)
    if existing:
        await post_message(
            client,
            reply_to,
            f"Session `{session_name}` already exists on `{existing}`.",
            thread_ts=thread_ts,
        )
        return

    # Create tmux session
    safe_name = shlex.quote(session_name)
    tmux_cmd = f"tmux new-session -d -s {safe_name}"
    if working_dir:
        tmux_cmd += f" -c {shlex.quote(working_dir)}"
    rc, _ = await asyncio.to_thread(run_ssh, host, tmux_cmd)
    if rc != 0:
        await post_message(
            client,
            reply_to,
            f"Failed to create tmux session `{session_name}` on `{host}`.",
            thread_ts=thread_ts,
        )
        return

    # Create Slack thread
    cwd_label = f" in `{working_dir}`" if working_dir else ""
    thread_name = f"{AGENT_PREFIX}{session_name}"
    new_ts = await ensure_thread(
        client, thread_name, f"tmux session: *{thread_name}* (`{host}`{cwd_label})"
    )

    if new_ts:
        await post_message(
            client, CHANNEL_ID, f"Session `{session_name}` created on `{host}`{cwd_label}.", new_ts
        )
        await post_message(
            client,
            reply_to,
            f"Created `{session_name}` on `{host}`{cwd_label} + thread",
            thread_ts=thread_ts,
        )
    else:
        await post_message(
            client,
            reply_to,
            f"Created tmux `{session_name}` on `{host}`{cwd_label} but failed to create thread.",
            thread_ts=thread_ts,
        )


async def cmd_kill(
    client: AsyncWebClient,
    reply_to: str,
    parts: list[str],
    thread_ts: str = None,
):
    """!kill <session_name> — kill tmux session + archive Slack thread."""
    if len(parts) < 2:
        await post_message(
            client,
            reply_to,
            "Usage: `!kill <session_name>`",
            thread_ts=thread_ts,
        )
        return

    session_name = parts[1]

    if not is_valid_session_name(session_name):
        await post_message(
            client,
            reply_to,
            "Invalid session name. Use only `a-z A-Z 0-9 _ -` (max 64 chars).",
            thread_ts=thread_ts,
        )
        return

    # Kill tmux session
    host = await asyncio.to_thread(find_session_host, session_name)
    tmux_killed = False
    if host:
        safe_name = shlex.quote(session_name)
        rc, _ = await asyncio.to_thread(
            run_ssh, host, f"tmux kill-session -t {safe_name}"
        )
        tmux_killed = rc == 0

    # Clean up Slack thread
    thread_name = f"{AGENT_PREFIX}{session_name}"
    ts = await find_thread_ts(client, thread_name)
    thread_cleaned = False
    cleanup_action = "archived"
    if ts:
        if THREAD_CLEANUP == "delete":
            await delete_thread(client, ts)
            thread_cleaned = True
            cleanup_action = "deleted"
        else:
            await post_message(
                client, CHANNEL_ID,
                f"Session `{session_name}` killed. Archiving thread.", ts,
            )
            await archive_thread(client, ts)
            thread_cleaned = True
            cleanup_action = "archived"
        # Clear cache
        _thread_cache.pop(ts, None)

    # Report
    status = []
    if tmux_killed:
        status.append(f"Killed `{session_name}` on `{host}`")
    elif host:
        status.append(f"Failed to kill `{session_name}` on `{host}`")
    else:
        status.append(f"tmux `{session_name}` not found")
    if thread_cleaned:
        status.append(f"{cleanup_action} thread")
    else:
        status.append("no thread found")

    await post_message(client, reply_to, " / ".join(status), thread_ts=thread_ts)

    # Emit dashboard event for session lifecycle tracking
    _fire_dashboard_event({
        "type": "session.killed",
        "session_name": session_name,
        "platform": "slack",
        "host": host or "",
        "tmux_killed": tmux_killed,
        "thread_cleanup": cleanup_action if thread_cleaned else "none",
        "timestamp": _now_iso(),
    })


async def cmd_sessions(
    client: AsyncWebClient,
    reply_to: str,
    thread_ts: str = None,
):
    """!sessions — list all sessions with thread sync status."""
    # Gather tmux sessions from all hosts
    all_sessions: dict[str, str] = {}
    for host in SSH_HOSTS:
        rc, out = await asyncio.to_thread(
            run_ssh,
            host,
            "tmux list-sessions -F '#{session_name}' 2>/dev/null || true",
        )
        if rc == 0 and out:
            for name in out.strip().split("\n"):
                name = name.strip()
                if name:
                    if name in all_sessions:
                        all_sessions[name] += f", {host}"
                    else:
                        all_sessions[name] = host

    # Gather active Slack threads (search channel messages for [agent] prefix)
    active_threads: set[str] = set()
    try:
        result = await client.conversations_history(channel=CHANNEL_ID, limit=200)
        for msg in result.get("messages", []):
            text = msg.get("text", "")
            first_line = text.split("\n")[0].strip()
            if first_line.startswith(AGENT_PREFIX):
                session = first_line[len(AGENT_PREFIX) :].split()[0]
                # Skip archived threads (those with :lock: reaction)
                reactions = msg.get("reactions", [])
                is_locked = any(r.get("name") == "lock" for r in reactions)
                if not is_locked:
                    active_threads.add(session)
    except Exception as e:
        print(f"[slack-bridge] sessions thread scan error: {e}", file=sys.stderr)

    if not all_sessions and not active_threads:
        await post_message(
            client, reply_to, "No sessions found.", thread_ts=thread_ts
        )
        return

    all_names = sorted(set(all_sessions.keys()) | active_threads)
    lines = ["```"]
    for name in all_names:
        host = all_sessions.get(name, "---")
        has_tmux = name in all_sessions
        has_thread = name in active_threads

        if has_tmux and has_thread:
            sync = "synced"
        elif has_tmux:
            sync = "no thread"
        else:
            sync = "orphan thread"

        lines.append(f"  {name:<20} {host:<24} {sync}")
    lines.append("```")

    await post_message(client, reply_to, "\n".join(lines), thread_ts=thread_ts)


async def cmd_queue(
    client: AsyncWebClient,
    reply_to: str,
    parts: list[str],
    thread_ts: str = None,
):
    """!queue [add <session> <command> | execute] — manage deferred command queue."""
    subcmd = parts[1].lower() if len(parts) > 1 else ""

    if subcmd == "add":
        if len(parts) < 4:
            await post_message(
                client, reply_to,
                "Usage: `!queue add <session_name> <command>`",
                thread_ts=thread_ts,
            )
            return
        session_name = parts[2]
        command = parts[3]
        result = await dashboard_api("POST", "/api/usage/queue", {
            "session_name": session_name,
            "command": command,
        })
        if result:
            cmd_id = result.get("command", {}).get("id", "?")
            await post_message(
                client, reply_to,
                f"Queued command #{cmd_id} for `{session_name}`: `{command}`",
                thread_ts=thread_ts,
            )
        else:
            await post_message(
                client, reply_to,
                "Failed to queue command. Is the usage monitor enabled?",
                thread_ts=thread_ts,
            )

    elif subcmd == "execute":
        result = await dashboard_api("POST", "/api/usage/queue/execute")
        if result:
            count = result.get("executed", 0)
            await post_message(
                client, reply_to,
                f"Executed {count} pending command(s).",
                thread_ts=thread_ts,
            )
        else:
            await post_message(
                client, reply_to,
                "Failed to execute queue. Is the usage monitor enabled?",
                thread_ts=thread_ts,
            )

    else:
        # List pending commands
        result = await dashboard_api("GET", "/api/usage/queue?status=pending")
        if not result:
            await post_message(
                client, reply_to,
                "Dashboard unavailable or usage monitor not enabled.",
                thread_ts=thread_ts,
            )
            return

        commands = result.get("commands", [])
        total = result.get("total", 0)
        if total == 0:
            await post_message(
                client, reply_to,
                "No pending commands in queue.",
                thread_ts=thread_ts,
            )
            return

        lines = [f"*Command Queue* ({total} pending)", "```"]
        lines.append(f"  {'ID':<6} {'SESSION':<20} {'HOST':<12} COMMAND")
        for cmd in commands[:15]:
            lines.append(
                f"  {cmd['id']:<6} {cmd['session_name']:<20} "
                f"{cmd['host']:<12} {cmd['command']}"
            )
        if total > 15:
            lines.append(f"  ... and {total - 15} more")
        lines.append("```")
        await post_message(
            client, reply_to, "\n".join(lines), thread_ts=thread_ts
        )


# --- Socket Mode event handler ---


async def handle_socket_event(
    client: SocketModeClient, req: SocketModeRequest
):
    """Handle incoming Socket Mode events."""
    # Acknowledge immediately
    response = SocketModeResponse(envelope_id=req.envelope_id)
    await client.send_socket_mode_response(response)

    if req.type != "events_api":
        return

    event = req.payload.get("event", {})
    if event.get("type") != "message":
        return
    if event.get("subtype"):  # Ignore edits, deletes, bot messages, etc.
        return
    if event.get("bot_id"):  # Ignore bot messages
        return
    if event.get("user") == BOT_USER_ID:  # Ignore our own messages
        return

    channel = event.get("channel", "")
    thread_ts = event.get("thread_ts")  # None if not in a thread
    text = event.get("text", "").strip()

    if not text:
        return

    # Handle ! commands (in channel or thread)
    if text.startswith("!"):
        print(f"[slack-bridge] command: {text[:80]}")
        await handle_command(client.web_client, channel, text, thread_ts)
        return

    # Only forward messages that are in threads
    if not thread_ts:
        return

    # Check if the parent message matches [agent] pattern
    session_name = await get_thread_session(client.web_client, channel, thread_ts)
    if not session_name:
        return

    user_id = event.get("user", "unknown")
    print(f"[slack-bridge] {user_id} -> [{session_name}]: {text[:80]}")

    host = find_session_host(session_name)
    if not host:
        await post_message(
            client.web_client,
            channel,
            f"Session `{session_name}` not found on any host.\n"
            f"Available hosts: {', '.join(SSH_HOSTS)}",
            thread_ts=thread_ts,
        )
        return

    await post_message(
        client.web_client,
        channel,
        f"Forwarding to `{session_name}` on `{host}`...",
        thread_ts=thread_ts,
    )

    # Emit dashboard event: user message relayed to tmux
    _fire_dashboard_event({
        "type": "message.relayed",
        "session_name": session_name,
        "platform": "slack",
        "content": text,
        "role": "user",
        "source_id": event.get("client_msg_id", event.get("ts", "")),
        "source_author": user_id,
        "timestamp": _now_iso(),
    })

    await asyncio.to_thread(send_to_tmux, host, session_name, text)


# --- Main ---


async def main():
    global CHANNEL_ID, SSH_HOSTS, DEFAULT_HOST, BOT_USER_ID, THREAD_CLEANUP
    global DASHBOARD_URL, _dashboard_http

    env_path = os.environ.get(
        "AGENT_BRIDGE_ENV", os.path.expanduser("~/.claude/hooks/.notify-env")
    )

    if not os.path.exists(env_path):
        print(f"Config not found: {env_path}", file=sys.stderr)
        sys.exit(1)

    env = load_env(env_path)
    bot_token = env.get("SLACK_BOT_TOKEN")
    app_token = env.get("SLACK_APP_TOKEN")
    CHANNEL_ID = env.get("SLACK_CHANNEL_ID", "")

    # Dashboard URL from env var (K8s) or .notify-env file
    if not DASHBOARD_URL:
        DASHBOARD_URL = env.get("AILY_DASHBOARD_URL", "")

    # SSH hosts from env (comma-separated) or defaults
    hosts_str = env.get("SSH_HOSTS", "")
    if hosts_str:
        SSH_HOSTS = [h.strip() for h in hosts_str.split(",") if h.strip()]
    else:
        SSH_HOSTS = ["localhost"]
    DEFAULT_HOST = SSH_HOSTS[0] if SSH_HOSTS else ""

    # Thread cleanup mode: "archive" (default) or "delete"
    THREAD_CLEANUP = env.get("THREAD_CLEANUP", "archive").lower()
    if THREAD_CLEANUP not in ("archive", "delete"):
        THREAD_CLEANUP = "archive"

    if not bot_token:
        print("SLACK_BOT_TOKEN not set", file=sys.stderr)
        sys.exit(1)
    if not app_token:
        print("SLACK_APP_TOKEN not set (required for Socket Mode)", file=sys.stderr)
        sys.exit(1)
    if not CHANNEL_ID:
        print("SLACK_CHANNEL_ID not set", file=sys.stderr)
        sys.exit(1)

    web_client = AsyncWebClient(token=bot_token)

    # Get bot user info
    auth = await web_client.auth_test()
    BOT_USER_ID = auth.get("user_id", "")
    print(f"[slack-bridge] Connected as {auth.get('user', '')} ({BOT_USER_ID})")
    print(f"[slack-bridge] Channel: {CHANNEL_ID}")
    print(f"[slack-bridge] SSH hosts: {SSH_HOSTS}")
    print(f"[slack-bridge] Thread prefix: '{AGENT_PREFIX}'")
    print(f"[slack-bridge] Thread cleanup: {THREAD_CLEANUP}")
    if DASHBOARD_URL:
        print(f"[slack-bridge] Dashboard: {DASHBOARD_URL}")
    else:
        print("[slack-bridge] Dashboard: not configured (AILY_DASHBOARD_URL not set)")

    # Create a shared aiohttp session for dashboard POSTs
    _dashboard_http = aiohttp.ClientSession()

    socket_client = SocketModeClient(app_token=app_token, web_client=web_client)
    socket_client.socket_mode_request_listeners.append(handle_socket_event)

    print("[slack-bridge] Starting Socket Mode connection...")
    await socket_client.connect()

    # Announce commands on first connect
    global _announced
    if not _announced:
        _announced = True
        announce_text = (
            "*aily bridge connected*\n"
            "Available commands:\n"
            "- `!new <name> [host] [pwd]` — create tmux session\n"
            "- `!kill <name>` — kill tmux session\n"
            "- `!sessions` — list active sessions\n"
            f"Hosts: `{'`, `'.join(SSH_HOSTS)}`"
        )
        await post_message(web_client, CHANNEL_ID, announce_text)

    # Keep alive
    try:
        while True:
            await asyncio.sleep(1)
    finally:
        await _dashboard_http.close()


if __name__ == "__main__":
    asyncio.run(main())
