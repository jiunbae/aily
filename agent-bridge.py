#!/usr/bin/env python3
"""
Discord ↔ tmux session bridge.

Monitors Discord threads named [agent] <session> and forwards user messages
to the corresponding tmux session's Claude Code instance via SSH.

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
SEND_KEYS_DELAY = 0.3
CAPTURE_DELAY = 8

API_BASE = "https://discord.com/api/v10"


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


def send_to_tmux(host: str, session: str, message: str) -> str:
    """Send a message to a tmux session's Claude Code and capture output."""
    escaped = message.replace('\\', '\\\\').replace('"', '\\"')

    # Step 1: Type the text
    rc, _ = run_ssh(host, f'tmux send-keys -t {session} "{escaped}"')
    if rc != 0:
        return f"Failed to send keys to {session} on {host}"

    # Step 2: Press Enter (separate command — critical for Claude Code)
    time.sleep(SEND_KEYS_DELAY)
    rc, _ = run_ssh(host, f'tmux send-keys -t {session} Enter')
    if rc != 0:
        return f"Failed to send Enter to {session} on {host}"

    # Step 3: Wait and capture output
    time.sleep(CAPTURE_DELAY)
    rc, output = run_ssh(host, f'tmux capture-pane -t {session} -p | tail -40')
    if rc != 0:
        return f"Failed to capture pane from {session} on {host}"

    return output


async def discord_request(session: aiohttp.ClientSession, token: str,
                          method: str, path: str, json_data: dict = None) -> dict:
    """Make a Discord REST API request."""
    headers = {"Authorization": f"Bot {token}"}
    url = f"{API_BASE}{path}"

    async with session.request(method, url, headers=headers, json=json_data) as resp:
        if resp.status >= 400:
            body = await resp.text()
            print(f"[discord] {method} {path} → {resp.status}: {body[:200]}", file=sys.stderr)
            return {}
        text = await resp.text()
        return json.loads(text) if text else {}


async def post_to_thread(session: aiohttp.ClientSession, token: str,
                         thread_id: str, content: str):
    """Post a message to a Discord thread."""
    if len(content) > 1900:
        content = content[:1900] + "\n...(truncated)"
    await discord_request(session, token, "POST",
                          f"/channels/{thread_id}/messages", {"content": content})


async def handle_message(http: aiohttp.ClientSession, token: str,
                         bot_user_id: str, message: dict):
    """Handle a Discord message — forward to tmux if in an [agent] thread."""
    author = message.get("author", {})
    if author.get("id") == bot_user_id or author.get("bot"):
        return

    channel_id = message.get("channel_id", "")

    # Look up channel info to check if it's a thread
    ch = await discord_request(http, token, "GET", f"/channels/{channel_id}")
    if ch.get("type") not in (11, 12):  # PUBLIC_THREAD or PRIVATE_THREAD
        return

    thread_name = ch.get("name", "")
    if not thread_name.startswith(AGENT_PREFIX):
        return

    session_name = thread_name[len(AGENT_PREFIX):]
    user_message = message.get("content", "").strip()
    user_name = author.get("username", "unknown")

    if not user_message:
        return

    print(f"[bridge] {user_name} → [{session_name}]: {user_message[:80]}")

    # Find the host
    host = find_session_host(session_name)
    if not host:
        await post_to_thread(http, token, channel_id,
            f"Session `{session_name}` not found on any host.\n"
            f"Available hosts: {', '.join(SSH_HOSTS)}")
        return

    await post_to_thread(http, token, channel_id,
        f"⏳ Forwarding to `{session_name}` on `{host}`...")

    # Run tmux interaction in a thread to not block the event loop
    output = await asyncio.to_thread(send_to_tmux, host, session_name, user_message)

    await post_to_thread(http, token, channel_id, f"```\n{output}\n```")


async def gateway_connect(token: str):
    """Connect to Discord gateway via WebSocket and listen for messages."""
    async with aiohttp.ClientSession() as http:
        # Get gateway URL
        gw = await discord_request(http, token, "GET", "/gateway/bot")
        ws_url = gw.get("url", "wss://gateway.discord.gg") + "?v=10&encoding=json"

        # Get bot user info
        me = await discord_request(http, token, "GET", "/users/@me")
        bot_user_id = me.get("id", "")
        print(f"[bridge] Connected as {me.get('username')} ({bot_user_id})")

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
    env_path = os.environ.get("AGENT_BRIDGE_ENV",
        os.path.expanduser("~/.claude/hooks/.notify-env"))

    if not os.path.exists(env_path):
        print(f"Config not found: {env_path}", file=sys.stderr)
        sys.exit(1)

    env = load_env(env_path)
    token = env.get("DISCORD_BOT_TOKEN")
    if not token:
        print("DISCORD_BOT_TOKEN not set", file=sys.stderr)
        sys.exit(1)

    print(f"[bridge] Starting agent bridge...")
    print(f"[bridge] SSH hosts: {SSH_HOSTS}")
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
