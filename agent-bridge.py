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
import subprocess
import sys
import time

import aiohttp

# Discord gateway intents
INTENT_GUILDS = 1 << 0
INTENT_GUILD_MESSAGES = 1 << 9
INTENT_MESSAGE_CONTENT = 1 << 15

AGENT_PREFIX = "[agent] "
SSH_HOSTS = ["jiun-mini", "jiun-mbp"]
DEFAULT_HOST = "jiun-mini"
SEND_KEYS_DELAY = 0.3

API_BASE = "https://discord.com/api/v10"

# Globals set at startup
CHANNEL_ID: str = ""
GUILD_ID: str = ""


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


def find_session_host(session_name: str) -> str | None:
    """Find which SSH host has the tmux session."""
    for host in SSH_HOSTS:
        rc, out = run_ssh(host, f"tmux has-session -t {session_name} 2>/dev/null && echo found")
        if rc == 0 and "found" in out:
            return host
    return None


def send_to_tmux(host: str, session: str, message: str) -> bool:
    """Send a message to a tmux session's Claude Code."""
    escaped = message.replace('\\', '\\\\').replace('"', '\\"')

    # Step 1: Type the text
    rc, _ = run_ssh(host, f'tmux send-keys -t {session} "{escaped}"')
    if rc != 0:
        return False

    # Step 2: Press Enter (separate command — critical for Claude Code)
    time.sleep(SEND_KEYS_DELAY)
    rc, _ = run_ssh(host, f'tmux send-keys -t {session} Enter')
    return rc == 0


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
    return thread.get("id") if isinstance(thread, dict) else None


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


# --- ! commands ---

async def handle_command(http: aiohttp.ClientSession, token: str,
                         channel_id: str, message: dict):
    """Handle ! commands from Discord."""
    content = message.get("content", "").strip()
    parts = content.split(None, 2)
    cmd = parts[0].lower() if parts else ""

    if cmd == "!new":
        await cmd_new(http, token, channel_id, parts)
    elif cmd == "!kill":
        await cmd_kill(http, token, channel_id, parts)
    elif cmd in ("!sessions", "!ls"):
        await cmd_sessions(http, token, channel_id)
    else:
        await post_message(http, token, channel_id,
            "Unknown command. Available: `!new <name> [host]`, `!kill <name>`, `!sessions`")


async def cmd_new(http: aiohttp.ClientSession, token: str,
                  reply_to: str, parts: list[str]):
    """!new <session_name> [host] — create tmux session + Discord thread."""
    if len(parts) < 2:
        await post_message(http, token, reply_to,
            "Usage: `!new <session_name> [host]`\n"
            f"Available hosts: `{'`, `'.join(SSH_HOSTS)}`")
        return

    session_name = parts[1]
    host = parts[2] if len(parts) > 2 else DEFAULT_HOST

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
    rc, _ = await asyncio.to_thread(run_ssh, host,
        f"tmux new-session -d -s {session_name}")
    if rc != 0:
        await post_message(http, token, reply_to,
            f"Failed to create tmux session `{session_name}` on `{host}`.")
        return

    # Create Discord thread
    thread_name = f"{AGENT_PREFIX}{session_name}"
    thread_id = await ensure_thread(http, token, thread_name,
        f"tmux session: **{thread_name}** (`{host}`)")

    if thread_id:
        await post_message(http, token, thread_id,
            f"Session `{session_name}` created on `{host}`.")
        await post_message(http, token, reply_to,
            f"Created `{session_name}` on `{host}` + thread <#{thread_id}>")
    else:
        await post_message(http, token, reply_to,
            f"Created tmux `{session_name}` on `{host}` but failed to create thread.")


async def cmd_kill(http: aiohttp.ClientSession, token: str,
                   reply_to: str, parts: list[str]):
    """!kill <session_name> — kill tmux session + archive Discord thread."""
    if len(parts) < 2:
        await post_message(http, token, reply_to, "Usage: `!kill <session_name>`")
        return

    session_name = parts[1]

    # Kill tmux session
    host = await asyncio.to_thread(find_session_host, session_name)
    tmux_killed = False
    if host:
        rc, _ = await asyncio.to_thread(run_ssh, host,
            f"tmux kill-session -t {session_name}")
        tmux_killed = (rc == 0)

    # Archive Discord thread
    thread_name = f"{AGENT_PREFIX}{session_name}"
    thread_id = await find_thread(http, token, thread_name)
    thread_archived = False
    if thread_id:
        await post_message(http, token, thread_id,
            f"Session `{session_name}` killed. Archiving thread.")
        await archive_thread(http, token, thread_id)
        thread_archived = True

    # Report
    status = []
    if tmux_killed:
        status.append(f"Killed `{session_name}` on `{host}`")
    elif host:
        status.append(f"Failed to kill `{session_name}` on `{host}`")
    else:
        status.append(f"tmux `{session_name}` not found")
    if thread_archived:
        status.append("archived thread")
    else:
        status.append("no thread found")

    await post_message(http, token, reply_to, " / ".join(status))


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

    host = find_session_host(session_name)
    if not host:
        await post_message(http, token, channel_id,
            f"Session `{session_name}` not found on any host.\n"
            f"Available hosts: {', '.join(SSH_HOSTS)}")
        return

    await post_message(http, token, channel_id,
        f"Forwarding to `{session_name}` on `{host}`...")

    await asyncio.to_thread(send_to_tmux, host, session_name, user_message)


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
                        asyncio.create_task(
                            heartbeat_loop(ws, heartbeat_interval, lambda: sequence))

                    # Heartbeat ACK
                    elif op == 11:
                        pass

                    # Dispatch events
                    elif op == 0:
                        if t == "MESSAGE_CREATE":
                            asyncio.create_task(
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
    global CHANNEL_ID

    env_path = os.environ.get("AGENT_BRIDGE_ENV",
        os.path.expanduser("~/.claude/hooks/.notify-env"))

    if not os.path.exists(env_path):
        print(f"Config not found: {env_path}", file=sys.stderr)
        sys.exit(1)

    env = load_env(env_path)
    token = env.get("DISCORD_BOT_TOKEN")
    CHANNEL_ID = env.get("DISCORD_CHANNEL_ID", "")

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

    while True:
        try:
            await gateway_connect(token)
        except Exception as e:
            print(f"[bridge] Connection error: {e}, reconnecting in 5s...",
                  file=sys.stderr)
        await asyncio.sleep(5)


if __name__ == "__main__":
    asyncio.run(main())
