#!/usr/bin/env python3
"""
Discord ↔ tmux session bridge.

Monitors Discord threads named [agent] <session> and forwards user messages
to the corresponding tmux session's Claude Code instance via SSH.

Also handles ! commands for session/thread lifecycle management:
  !new <name> [host]  — create tmux session + Discord thread
  !kill <name>        — kill tmux session + archive Discord thread
  !sessions           — list all sessions with sync status

This is a deterministic forwarder — no AI involved. Messages in [agent] threads
are ALWAYS forwarded to tmux, never answered by a chatbot.
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

# Dashboard webhook URL (optional — if not set, events are silently skipped)
DASHBOARD_URL: str = os.environ.get("AILY_DASHBOARD_URL", "")
DASHBOARD_AUTH_TOKEN: str = os.environ.get("AILY_AUTH_TOKEN", "")

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
        headers: dict = {}
        if DASHBOARD_AUTH_TOKEN:
            headers["Authorization"] = f"Bearer {DASHBOARD_AUTH_TOKEN}"
        kwargs: dict = {"timeout": aiohttp.ClientTimeout(total=10), "headers": headers}
        if json_body is not None:
            kwargs["json"] = json_body
        async with _dashboard_http.request(method, url, **kwargs) as resp:
            if resp.status < 400:
                return await resp.json()
            body = await resp.text()
            print(f"[dashboard] {method} {path} {resp.status}: {body[:200]}", file=sys.stderr)
            return None
    except (aiohttp.ClientError, TimeoutError) as e:
        print(f"[dashboard] {method} {path} failed: {e}", file=sys.stderr)
        return None


# Discord gateway intents
INTENT_GUILDS = 1 << 0
INTENT_GUILD_MESSAGES = 1 << 9
INTENT_MESSAGE_CONTENT = 1 << 15

AGENT_PREFIX = "[agent] "
SEND_KEYS_DELAY = 0.3
SESSION_NAME_RE = re.compile(r'^[a-zA-Z0-9_-]+$')

API_BASE = "https://discord.com/api/v10"

# Globals set at startup
CHANNEL_ID: str = ""
GUILD_ID: str = ""
SSH_HOSTS: list[str] = []
DEFAULT_HOST: str = ""
THREAD_CLEANUP: str = "archive"
NEW_SESSION_AGENT: str = ""
CLAUDE_REMOTE_CONTROL: bool = False
_announced: bool = False

# Track background tasks to prevent GC and surface exceptions
_background_tasks: set[asyncio.Task] = set()


def _track_task(coro) -> asyncio.Task:
    """Create a tracked background task that logs exceptions on completion."""
    task = asyncio.create_task(coro)
    _background_tasks.add(task)

    def _on_done(t: asyncio.Task):
        _background_tasks.discard(t)
        if not t.cancelled() and t.exception():
            print(f"[bridge] background task failed: {t.exception()}", file=sys.stderr)

    task.add_done_callback(_on_done)
    return task


_SECRET_PATTERNS = re.compile(
    r'(?i)'
    r'((?:password|passwd|secret|token|api[_-]?key|access[_-]?key|private[_-]?key'
    r'|credential|auth|bearer|ssh[_-]?key|database[_-]?url|connection[_-]?string'
    r'|key[_-]?id|client[_-]?secret)'
    r'\s*[=:])\s*(?:"[^"]*"|\'[^\']*\'|\S+)',
)
_PEM_RE = re.compile(r'-----BEGIN [A-Z ]+-----[\s\S]*?-----END [A-Z ]+-----')


def _sanitize_backticks(text: str) -> str:
    """Escape triple backticks in text to prevent markdown injection."""
    return text.replace('```', r'\`\`\`')


def _redact_secrets(text: str) -> str:
    """Redact common secret patterns from shell output."""
    text = _SECRET_PATTERNS.sub(r'\1 [REDACTED]', text)
    text = _PEM_RE.sub('[REDACTED PEM KEY]', text)
    return text


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
            capture_output=True, text=True, timeout=timeout
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
        rc, out = run_ssh(host, f"tmux has-session -t {safe_name} 2>/dev/null && echo found")
        if rc == 0 and "found" in out:
            return host
    return None


def send_to_tmux(host: str, session: str, message: str) -> bool:
    """Send a message to a tmux session's Claude Code."""
    safe_session = shlex.quote(session)
    safe_message = shlex.quote(message)

    # Step 1: Type the text
    rc, _ = run_ssh(host, f'tmux send-keys -t {safe_session} {safe_message}')
    if rc != 0:
        return False

    # Step 2: Press Enter (separate command — critical for Claude Code)
    time.sleep(SEND_KEYS_DELAY)
    rc, _ = run_ssh(host, f'tmux send-keys -t {safe_session} Enter')
    return rc == 0


# Shell names for output capture (skip capture for non-shell processes)
_SHELL_NAMES = frozenset({"bash", "zsh", "sh", "fish", "dash", "ksh", "tcsh", "csh"})


def get_pane_command(host: str, session: str) -> str:
    """Get the current foreground command in a tmux session's active pane."""
    safe_session = shlex.quote(session)
    rc, out = run_ssh(
        host,
        f"tmux display-message -t {safe_session} -p '#{{pane_current_command}}'"
    )
    return out.strip() if rc == 0 else ""


def capture_pane_content(host: str, session: str) -> str:
    """Capture visible pane content from a tmux session."""
    safe_session = shlex.quote(session)
    rc, out = run_ssh(host, f"tmux capture-pane -t {safe_session} -p", timeout=10)
    return out if rc == 0 else ""


def _build_agent_command(agent: str, remote_control: bool) -> str | None:
    """Build the shell command to launch an agent. Returns None if agent is empty."""
    if not agent:
        return None
    if agent == "claude":
        return "claude remote-control" if remote_control else "claude"
    if agent == "codex":
        return "codex"
    if agent == "gemini":
        return "gemini"
    if agent == "opencode":
        return "opencode"
    return None


def capture_shell_output(
    host: str, session: str, pre_content: str,
    poll_interval: float = 1.0,
    stable_count: int = 2,
    max_wait: float = 30.0,
) -> str | None:
    """Poll tmux pane until output stabilizes, return new content.

    Returns None if a non-shell process takes over (e.g., Claude Code started).
    Returns empty string if no new output detected.
    """
    time.sleep(1.0)

    # Check: did the command spawn a non-shell process?
    pane_cmd = get_pane_command(host, session)
    if pane_cmd.lower() not in _SHELL_NAMES:
        return None

    last_content = ""
    stable_hits = 0
    deadline = time.monotonic() + max_wait

    while time.monotonic() < deadline:
        current = capture_pane_content(host, session)
        if not current:
            break

        if current == last_content:
            stable_hits += 1
            if stable_hits >= stable_count:
                break
        else:
            stable_hits = 0
            last_content = current

        time.sleep(poll_interval)

    # Final check: ensure shell is still the foreground process
    pane_cmd = get_pane_command(host, session)
    if pane_cmd.lower() not in _SHELL_NAMES:
        return None

    if not last_content:
        return ""

    # Diff: find new lines compared to pre_content
    pre_lines = pre_content.rstrip().split('\n') if pre_content.strip() else []
    post_lines = last_content.rstrip().split('\n') if last_content.strip() else []

    common_len = 0
    for i, (a, b) in enumerate(zip(pre_lines, post_lines)):
        if a == b:
            common_len = i + 1
        else:
            break

    new_lines = post_lines[common_len:]

    # Strip trailing empty lines
    while new_lines and not new_lines[-1].strip():
        new_lines.pop()

    return '\n'.join(new_lines)


# --- Discord REST helpers ---

async def discord_request(session: aiohttp.ClientSession, token: str,
                          method: str, path: str, json_data: dict = None) -> dict | list:
    """Make a Discord REST API request."""
    headers = {"Authorization": f"Bot {token}"}
    url = f"{API_BASE}{path}"

    async with session.request(method, url, headers=headers, json=json_data) as resp:
        if resp.status >= 400:
            body = await resp.text()
            print(f"[discord] {method} {path} -> {resp.status}: {body[:200]}", file=sys.stderr)
            return {}
        text = await resp.text()
        return json.loads(text) if text else {}


async def post_message(http: aiohttp.ClientSession, token: str,
                       channel_id: str, content: str):
    """Post a message to a channel or thread."""
    if len(content) > 1900:
        content = content[:1900] + "\n...(truncated)"
    await discord_request(http, token, "POST",
                          f"/channels/{channel_id}/messages", {"content": content})


# --- Thread management ---

async def find_thread(http: aiohttp.ClientSession, token: str, thread_name: str) -> str | None:
    """Find a thread by name. Checks active, archived, then message threads."""
    global GUILD_ID

    # 1. Active threads (guild-level endpoint)
    if GUILD_ID:
        data = await discord_request(http, token, "GET",
            f"/guilds/{GUILD_ID}/threads/active")
        for t in data.get("threads", []):
            if t.get("name") == thread_name and t.get("parent_id") == CHANNEL_ID:
                return t["id"]

    # 2. Archived threads
    data = await discord_request(http, token, "GET",
        f"/channels/{CHANNEL_ID}/threads/archived/public")
    if isinstance(data, dict):
        for t in data.get("threads", []):
            if t.get("name") == thread_name:
                return t["id"]

    # 3. Channel messages with thread metadata
    data = await discord_request(http, token, "GET",
        f"/channels/{CHANNEL_ID}/messages?limit=50")
    if isinstance(data, list):
        for m in data:
            t = m.get("thread", {})
            if t.get("name") == thread_name:
                return t["id"]

    return None


async def create_thread(http: aiohttp.ClientSession, token: str,
                        thread_name: str, starter_msg: str) -> str | None:
    """Create a new thread: post starter message, then create thread on it."""
    msg = await discord_request(http, token, "POST",
        f"/channels/{CHANNEL_ID}/messages", {"content": starter_msg})
    msg_id = msg.get("id") if isinstance(msg, dict) else None
    if not msg_id:
        return None
    thread = await discord_request(http, token, "POST",
        f"/channels/{CHANNEL_ID}/messages/{msg_id}/threads", {"name": thread_name})
    thread_id = thread.get("id") if isinstance(thread, dict) else None
    if thread_id:
        session_name = thread_name.removeprefix(AGENT_PREFIX) if thread_name.startswith(AGENT_PREFIX) else thread_name
        welcome = (
            f"**Welcome to {thread_name}** \U0001f44b\n\n"
            "Type a message here to forward it to the tmux session.\n\n"
            "**Commands:**\n"
            "`!sessions` \u2014 list all sessions\n"
            f"`!kill {session_name}` \u2014 kill this session + archive thread"
        )
        await post_message(http, token, thread_id, welcome)
    return thread_id


async def ensure_thread(http: aiohttp.ClientSession, token: str,
                        thread_name: str, starter_msg: str = None) -> str | None:
    """Find or create a thread. Unarchive if archived."""
    thread_id = await find_thread(http, token, thread_name)
    if thread_id:
        await discord_request(http, token, "PATCH",
            f"/channels/{thread_id}", {"archived": False})
        return thread_id
    if starter_msg is None:
        starter_msg = f"tmux session: **{thread_name}**"
    return await create_thread(http, token, thread_name, starter_msg)


async def archive_thread(http: aiohttp.ClientSession, token: str, thread_id: str):
    """Archive a thread."""
    await discord_request(http, token, "PATCH",
        f"/channels/{thread_id}", {"archived": True})


async def delete_thread(http: aiohttp.ClientSession, token: str, thread_id: str):
    """Delete a thread (Discord channel delete)."""
    await discord_request(http, token, "DELETE", f"/channels/{thread_id}")


# --- ! commands ---

async def handle_command(http: aiohttp.ClientSession, token: str,
                         channel_id: str, message: dict):
    """Handle ! commands from Discord."""
    content = message.get("content", "").strip()
    parts = content.split(None, 3)
    cmd = parts[0].lower() if parts else ""

    if cmd == "!new":
        await cmd_new(http, token, channel_id, parts)
    elif cmd == "!kill":
        await cmd_kill(http, token, channel_id, parts)
    elif cmd in ("!sessions", "!ls"):
        await cmd_sessions(http, token, channel_id)
    elif cmd == "!queue":
        await cmd_queue(http, token, channel_id, parts)
    else:
        await post_message(http, token, channel_id,
            "Unknown command. Available: `!new <name> [host] [pwd]`, `!kill <name>`, `!sessions`, `!queue`")


async def cmd_new(http: aiohttp.ClientSession, token: str,
                  reply_to: str, parts: list[str]):
    """!new <session_name> [host] [pwd] — create tmux session + Discord thread."""
    if len(parts) < 2:
        await post_message(http, token, reply_to,
            "Usage: `!new <session_name> [host] [pwd]`\n"
            f"Available hosts: `{'`, `'.join(SSH_HOSTS)}`")
        return

    session_name = parts[1]
    host = parts[2] if len(parts) > 2 else DEFAULT_HOST
    working_dir = parts[3] if len(parts) > 3 else None

    if not is_valid_session_name(session_name):
        await post_message(http, token, reply_to,
            "Invalid session name. Use only `a-z A-Z 0-9 _ -` (max 64 chars).")
        return

    if host not in SSH_HOSTS:
        await post_message(http, token, reply_to,
            f"Unknown host `{host}`. Available: `{'`, `'.join(SSH_HOSTS)}`")
        return

    # Check if session already exists
    existing = await asyncio.to_thread(find_session_host, session_name)
    if existing:
        await post_message(http, token, reply_to,
            f"Session `{session_name}` already exists on `{existing}`.")
        return

    # Create tmux session
    safe_name = shlex.quote(session_name)
    tmux_cmd = f"tmux new-session -d -s {safe_name}"
    if working_dir:
        tmux_cmd += f" -c {shlex.quote(working_dir)}"
    rc, _ = await asyncio.to_thread(run_ssh, host, tmux_cmd)
    if rc != 0:
        await post_message(http, token, reply_to,
            f"Failed to create tmux session `{session_name}` on `{host}`.")
        return

    # Launch agent in session (if configured)
    agent_cmd = _build_agent_command(NEW_SESSION_AGENT, CLAUDE_REMOTE_CONTROL)
    agent_label = ""
    if agent_cmd:
        # Small delay for shell initialization
        await asyncio.sleep(0.5)
        launched = await asyncio.to_thread(send_to_tmux, host, session_name, agent_cmd)
        if launched:
            agent_label = f" | agent: `{agent_cmd}`"
        else:
            agent_label = f" | failed to launch `{agent_cmd}`"

    # Create Discord thread
    cwd_label = f" in `{working_dir}`" if working_dir else ""
    thread_name = f"{AGENT_PREFIX}{session_name}"
    thread_id = await ensure_thread(http, token, thread_name,
        f"tmux session: **{thread_name}** (`{host}`{cwd_label})")

    if thread_id:
        await post_message(http, token, thread_id,
            f"Session `{session_name}` created on `{host}`{cwd_label}.{agent_label}")
        await post_message(http, token, reply_to,
            f"Created `{session_name}` on `{host}`{cwd_label} + thread <#{thread_id}>{agent_label}")
    else:
        await post_message(http, token, reply_to,
            f"Created tmux `{session_name}` on `{host}`{cwd_label} but failed to create thread.{agent_label}")


async def cmd_kill(http: aiohttp.ClientSession, token: str,
                   reply_to: str, parts: list[str]):
    """!kill <session_name> — kill tmux session + archive Discord thread."""
    if len(parts) < 2:
        await post_message(http, token, reply_to, "Usage: `!kill <session_name>`")
        return

    session_name = parts[1]

    if not is_valid_session_name(session_name):
        await post_message(http, token, reply_to,
            "Invalid session name. Use only `a-z A-Z 0-9 _ -` (max 64 chars).")
        return

    # Kill tmux session
    host = await asyncio.to_thread(find_session_host, session_name)
    tmux_killed = False
    if host:
        safe_name = shlex.quote(session_name)
        rc, _ = await asyncio.to_thread(run_ssh, host,
            f"tmux kill-session -t {safe_name}")
        tmux_killed = (rc == 0)

    # Clean up Discord thread
    thread_name = f"{AGENT_PREFIX}{session_name}"
    thread_id = await find_thread(http, token, thread_name)
    thread_cleaned = False
    cleanup_action = "archived"
    if thread_id:
        if THREAD_CLEANUP == "delete":
            await delete_thread(http, token, thread_id)
            thread_cleaned = True
            cleanup_action = "deleted"
        else:
            await post_message(http, token, thread_id,
                f"Session `{session_name}` killed. Archiving thread.")
            await archive_thread(http, token, thread_id)
            thread_cleaned = True
            cleanup_action = "archived"

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

    await post_message(http, token, reply_to, " / ".join(status))

    # Emit dashboard event for session lifecycle tracking
    _fire_dashboard_event({
        "type": "session.killed",
        "session_name": session_name,
        "platform": "discord",
        "host": host or "",
        "tmux_killed": tmux_killed,
        "thread_cleanup": cleanup_action if thread_cleaned else "none",
        "timestamp": _now_iso(),
    })


async def cmd_sessions(http: aiohttp.ClientSession, token: str, reply_to: str):
    """!sessions — list all sessions with thread sync status."""
    # Gather tmux sessions from all hosts
    all_sessions: dict[str, str] = {}
    for host in SSH_HOSTS:
        rc, out = await asyncio.to_thread(run_ssh, host,
            "tmux list-sessions -F '#{session_name}' 2>/dev/null || true")
        if rc == 0 and out:
            for name in out.strip().split("\n"):
                name = name.strip()
                if name:
                    if name in all_sessions:
                        all_sessions[name] += f", {host}"
                    else:
                        all_sessions[name] = host

    # Gather active Discord threads
    active_threads: set[str] = set()
    if GUILD_ID:
        data = await discord_request(http, token, "GET",
            f"/guilds/{GUILD_ID}/threads/active")
        if isinstance(data, dict):
            for t in data.get("threads", []):
                name = t.get("name", "")
                if name.startswith(AGENT_PREFIX) and t.get("parent_id") == CHANNEL_ID:
                    active_threads.add(name[len(AGENT_PREFIX):])

    if not all_sessions and not active_threads:
        await post_message(http, token, reply_to, "No sessions found.")
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

    await post_message(http, token, reply_to, "\n".join(lines))


async def cmd_queue(http: aiohttp.ClientSession, token: str,
                    reply_to: str, parts: list[str]):
    """!queue [add <session> <command> | execute] — manage deferred command queue."""
    subcmd = parts[1].lower() if len(parts) > 1 else ""

    if subcmd == "add":
        if len(parts) < 4:
            await post_message(http, token, reply_to,
                "Usage: `!queue add <session_name> <command>`")
            return
        session_name = parts[2]
        if not is_valid_session_name(session_name):
            await post_message(http, token, reply_to,
                "Invalid session name. Use only `a-z A-Z 0-9 _ -` (max 64 chars).")
            return
        command = " ".join(parts[3:])
        result = await dashboard_api("POST", "/api/usage/queue", {
            "session_name": session_name,
            "command": command,
        })
        if result:
            cmd_id = result.get("command", {}).get("id", "?")
            await post_message(http, token, reply_to,
                f"Queued command #{cmd_id} for `{session_name}`: `{command}`")
        else:
            await post_message(http, token, reply_to,
                "Failed to queue command. Is the usage monitor enabled?")

    elif subcmd == "execute":
        result = await dashboard_api("POST", "/api/usage/queue/execute")
        if result:
            count = result.get("executed", 0)
            await post_message(http, token, reply_to,
                f"Executed {count} pending command(s).")
        else:
            await post_message(http, token, reply_to,
                "Failed to execute queue. Is the usage monitor enabled?")

    else:
        # List pending commands
        result = await dashboard_api("GET", "/api/usage/queue?status=pending")
        if not result:
            await post_message(http, token, reply_to,
                "Dashboard unavailable or usage monitor not enabled.")
            return

        commands = result.get("commands", [])
        total = result.get("total", 0)
        if total == 0:
            await post_message(http, token, reply_to, "No pending commands in queue.")
            return

        lines = [f"**Command Queue** ({total} pending)", "```"]
        lines.append(f"  {'ID':<6} {'SESSION':<20} {'HOST':<12} COMMAND")
        for cmd in commands[:15]:
            lines.append(
                f"  {cmd.get('id', '?'):<6} {cmd.get('session_name', '?'):<20} "
                f"{cmd.get('host', '?'):<12} {cmd.get('command', '?')}"
            )
        if total > 15:
            lines.append(f"  ... and {total - 15} more")
        lines.append("```")
        await post_message(http, token, reply_to, "\n".join(lines))


# --- Shell output capture ---

async def _capture_and_post_output(
    http: aiohttp.ClientSession, token: str,
    channel_id: str, host: str, session: str,
    pre_content: str,
):
    """Background task: capture shell output and post to Discord.

    Skips capture if a non-shell process (e.g., Claude Code) is running.
    pre_content must be captured BEFORE send_to_tmux to avoid missing
    fast command output.
    """
    try:
        output = await asyncio.to_thread(
            capture_shell_output, host, session, pre_content
        )

        if output is None:
            # Non-shell process (Claude Code etc.) — its own hooks handle output
            return
        if not output.strip():
            return

        output = _redact_secrets(output)
        output = _sanitize_backticks(output)

        if len(output) > 1800:
            output = output[:1800] + "\n...(truncated)"

        safe_name = session.replace('`', "'")
        await post_message(http, token, channel_id,
            f"Shell output from `{safe_name}`:\n```\n{output}\n```")

    except Exception as e:
        print(f"[bridge] output capture error: {e}", file=sys.stderr)


# --- Message routing ---

async def handle_message(http: aiohttp.ClientSession, token: str,
                         bot_user_id: str, message: dict):
    """Handle a Discord message — commands or forward to tmux."""
    author = message.get("author", {})
    if author.get("id") == bot_user_id or author.get("bot"):
        return

    channel_id = message.get("channel_id", "")
    content = message.get("content", "").strip()

    # Handle ! commands (work in main channel AND threads)
    if content.startswith("!"):
        print(f"[bridge] command: {content[:80]}")
        await handle_command(http, token, channel_id, message)
        return

    # Forward messages in [agent] threads to tmux
    ch = await discord_request(http, token, "GET", f"/channels/{channel_id}")
    if not isinstance(ch, dict) or ch.get("type") not in (11, 12):
        return

    thread_name = ch.get("name", "")
    if not thread_name.startswith(AGENT_PREFIX):
        return

    session_name = thread_name[len(AGENT_PREFIX):]
    user_message = content
    user_name = author.get("username", "unknown")

    if not user_message:
        return

    print(f"[bridge] {user_name} -> [{session_name}]: {user_message[:80]}")

    host = await asyncio.to_thread(find_session_host, session_name)
    if not host:
        await post_message(http, token, channel_id,
            f"Session `{session_name}` not found on any host.\n"
            f"Available hosts: {', '.join(SSH_HOSTS)}")
        return

    await post_message(http, token, channel_id,
        f"Forwarding to `{session_name}` on `{host}`...")

    # Emit dashboard event: user message relayed to tmux
    _fire_dashboard_event({
        "type": "message.relayed",
        "session_name": session_name,
        "platform": "discord",
        "content": user_message,
        "role": "user",
        "source_id": message.get("id", ""),
        "source_author": user_name,
        "timestamp": _now_iso(),
    })

    # Capture pane BEFORE sending — critical for catching fast command output
    pre_content = await asyncio.to_thread(capture_pane_content, host, session_name)

    sent = await asyncio.to_thread(send_to_tmux, host, session_name, user_message)
    if sent:
        _track_task(
            _capture_and_post_output(
                http, token, channel_id, host, session_name, pre_content
            )
        )
    else:
        await post_message(http, token, channel_id,
            f"Failed to send to `{session_name}` on `{host}`. The session may have exited.")


# --- Gateway ---

async def gateway_connect(token: str):
    """Connect to Discord gateway via WebSocket and listen for messages."""
    global GUILD_ID

    async with aiohttp.ClientSession() as http:
        # Get gateway URL
        gw = await discord_request(http, token, "GET", "/gateway/bot")
        ws_url = gw.get("url", "wss://gateway.discord.gg") + "?v=10&encoding=json"

        # Get bot user info
        me = await discord_request(http, token, "GET", "/users/@me")
        bot_user_id = me.get("id", "")
        print(f"[bridge] Connected as {me.get('username')} ({bot_user_id})")

        # Cache guild ID
        if not GUILD_ID and CHANNEL_ID:
            ch = await discord_request(http, token, "GET", f"/channels/{CHANNEL_ID}")
            GUILD_ID = ch.get("guild_id", "") if isinstance(ch, dict) else ""
            if GUILD_ID:
                print(f"[bridge] Guild: {GUILD_ID}")

        # Announce commands on first connect
        global _announced
        if not _announced:
            _announced = True
            lines = [
                "**aily bridge connected**",
                "Available commands:",
                "- `!new <name> [host] [pwd]` — create tmux session",
                "- `!kill <name>` — kill tmux session",
                "- `!sessions` — list active sessions",
                "- `!queue` — list / add / execute deferred commands",
                f"Hosts: `{'`, `'.join(SSH_HOSTS)}`",
            ]
            # Append live usage status if dashboard is reachable
            usage = await dashboard_api("GET", "/api/usage")
            if usage and usage.get("usage"):
                lines.append("")
                lines.append("**API Usage**")
                for provider, snap in usage["usage"].items():
                    req_rem = snap.get("requests_remaining")
                    req_lim = snap.get("requests_limit")
                    tok_rem = snap.get("tokens_remaining")
                    tok_lim = snap.get("tokens_limit")
                    parts_ = [f"**{provider}**:"]
                    if req_lim is not None:
                        parts_.append(f"requests {req_rem}/{req_lim}")
                    if tok_lim is not None:
                        parts_.append(f"tokens {tok_rem}/{tok_lim}")
                    lines.append("  ".join(parts_))
                qs = usage.get("queue_stats", {})
                pending = qs.get("pending", 0)
                if pending:
                    lines.append(f"Queued commands: **{pending}** pending")
            await post_message(http, token, CHANNEL_ID, "\n".join(lines))

        intents = INTENT_GUILDS | INTENT_GUILD_MESSAGES | INTENT_MESSAGE_CONTENT
        sequence = None

        async with http.ws_connect(ws_url) as ws:
            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    data = json.loads(msg.data)
                    op = data.get("op")
                    t = data.get("t")
                    d = data.get("d")
                    s = data.get("s")

                    if s is not None:
                        sequence = s

                    # Hello — identify and start heartbeating
                    if op == 10:
                        heartbeat_interval = d["heartbeat_interval"] / 1000
                        await ws.send_json({
                            "op": 2,
                            "d": {
                                "token": token,
                                "intents": intents,
                                "properties": {
                                    "os": "linux",
                                    "browser": "agent-bridge",
                                    "device": "agent-bridge"
                                }
                            }
                        })
                        _track_task(
                            heartbeat_loop(ws, heartbeat_interval, lambda: sequence))

                    # Heartbeat ACK
                    elif op == 11:
                        pass

                    # Dispatch events
                    elif op == 0:
                        if t == "MESSAGE_CREATE":
                            _track_task(
                                handle_message(http, token, bot_user_id, d))

                    # Reconnect / Invalid session
                    elif op in (7, 9):
                        print(f"[bridge] Gateway op {op}, reconnecting...",
                              file=sys.stderr)
                        break

                elif msg.type in (aiohttp.WSMsgType.CLOSED,
                                  aiohttp.WSMsgType.ERROR):
                    print(f"[bridge] WebSocket closed/error", file=sys.stderr)
                    break


async def heartbeat_loop(ws, interval: float, get_sequence):
    """Send heartbeats at the specified interval."""
    while True:
        await asyncio.sleep(interval)
        try:
            await ws.send_json({"op": 1, "d": get_sequence()})
        except Exception:
            break


async def main():
    global CHANNEL_ID, SSH_HOSTS, DEFAULT_HOST, THREAD_CLEANUP, DASHBOARD_URL, DASHBOARD_AUTH_TOKEN, _dashboard_http
    global NEW_SESSION_AGENT, CLAUDE_REMOTE_CONTROL

    _xdg_config = os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))
    _new_path = os.path.join(_xdg_config, "aily", "env")
    _old_path = os.path.expanduser("~/.claude/hooks/.notify-env")
    _default_path = _new_path if os.path.exists(_new_path) else _old_path
    env_path = os.environ.get("AGENT_BRIDGE_ENV", _default_path)

    if not os.path.exists(env_path):
        print(f"Config not found: {env_path}", file=sys.stderr)
        sys.exit(1)

    env = load_env(env_path)
    token = env.get("DISCORD_BOT_TOKEN")
    CHANNEL_ID = env.get("DISCORD_CHANNEL_ID", "")

    # Dashboard URL and auth token from env var (K8s) or .notify-env file
    if not DASHBOARD_URL:
        DASHBOARD_URL = env.get("AILY_DASHBOARD_URL", "")
    if not DASHBOARD_AUTH_TOKEN:
        DASHBOARD_AUTH_TOKEN = env.get("AILY_AUTH_TOKEN", "")

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

    # Agent auto-launch on !new
    NEW_SESSION_AGENT = env.get("NEW_SESSION_AGENT", "").lower().strip()
    CLAUDE_REMOTE_CONTROL = env.get("CLAUDE_REMOTE_CONTROL", "false").lower() == "true"

    if not token:
        print("DISCORD_BOT_TOKEN not set", file=sys.stderr)
        sys.exit(1)
    if not CHANNEL_ID:
        print("DISCORD_CHANNEL_ID not set", file=sys.stderr)
        sys.exit(1)

    print(f"[bridge] Starting agent bridge...")
    print(f"[bridge] SSH hosts: {SSH_HOSTS}")
    print(f"[bridge] Channel: {CHANNEL_ID}")
    print(f"[bridge] Thread prefix: '{AGENT_PREFIX}'")
    print(f"[bridge] Thread cleanup: {THREAD_CLEANUP}")
    if NEW_SESSION_AGENT:
        rc_label = " + remote-control" if NEW_SESSION_AGENT == "claude" and CLAUDE_REMOTE_CONTROL else ""
        print(f"[bridge] Auto-launch agent: {NEW_SESSION_AGENT}{rc_label}")
    if DASHBOARD_URL:
        print(f"[bridge] Dashboard: {DASHBOARD_URL}")
    else:
        print("[bridge] Dashboard: not configured (AILY_DASHBOARD_URL not set)")

    # Create a shared aiohttp session for dashboard POSTs
    _dashboard_http = aiohttp.ClientSession()
    try:
        while True:
            try:
                await gateway_connect(token)
            except Exception as e:
                print(f"[bridge] Connection error: {e}, reconnecting in 5s...",
                      file=sys.stderr)
            await asyncio.sleep(5)
    finally:
        await _dashboard_http.close()


if __name__ == "__main__":
    asyncio.run(main())
