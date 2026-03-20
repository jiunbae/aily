#!/usr/bin/env python3
"""
Discord <-> terminal multiplexer session bridge.

Monitors Discord threads named [agent] <session> and forwards user messages
to the corresponding session's Claude Code instance via SSH.

Supports tmux (full) and zellij (partial) backends via AILY_MULTIPLEXER env var.

Also handles ! commands for session/thread lifecycle management:
  !new <name> [host|dir] [dir] [-- cmd] -- create session + Discord thread
  !kill <name>        -- kill session + archive Discord thread
  !sessions           -- list all sessions with sync status
  !queue              -- list / add / execute deferred commands
  !lq                 -- list / clear / retry / status session limit queue

This is a deterministic forwarder -- no AI involved. Messages in [agent] threads
are ALWAYS forwarded to the session, never answered by a chatbot.

Thin platform layer -- all shared logic lives in bridge_core.py.
"""

import asyncio
import json
import logging
import sys
from typing import Any

import aiohttp

from bridge_core import (
    BridgeCore,
    BridgeState,
    PlatformBridge,
    load_env,
    parse_thread_name,
    SHORTCUTS,
)

# Discord gateway intents
INTENT_GUILDS = 1 << 0
INTENT_GUILD_MESSAGES = 1 << 9
INTENT_MESSAGE_CONTENT = 1 << 15

API_BASE = "https://discord.com/api/v10"


# --- Discord REST helper ---

async def discord_request(
    session: aiohttp.ClientSession, token: str,
    method: str, path: str, json_data: dict[str, Any] | None = None,
) -> dict[str, Any] | list[Any]:
    """Make a Discord REST API request with 429 rate-limit retry."""
    headers = {"Authorization": f"Bot {token}"}
    url = f"{API_BASE}{path}"

    async with session.request(method, url, headers=headers, json=json_data) as resp:
        if resp.status == 429:
            body = await resp.text()
            try:
                data = json.loads(body)
            except json.JSONDecodeError:
                data = {}
            retry_after = data.get("retry_after", 1.0)
            logging.warning("Rate limited, retrying after %.1fs", retry_after)
            await asyncio.sleep(retry_after)
            # Retry the request once
            async with session.request(method, url, headers=headers, json=json_data) as retry_resp:
                if retry_resp.status >= 400:
                    retry_body = await retry_resp.text()
                    print(f"[discord] {method} {path} -> {retry_resp.status}: "
                          f"{retry_body[:200]}", file=sys.stderr)
                    return {}
                text = await retry_resp.text()
                return json.loads(text) if text else {}
        if resp.status >= 400:
            body = await resp.text()
            print(f"[discord] {method} {path} -> {resp.status}: {body[:200]}",
                  file=sys.stderr)
            return {}
        text = await resp.text()
        return json.loads(text) if text else {}


# --- Discord platform implementation ---

class DiscordPlatform(PlatformBridge):
    """Discord-specific platform bridge using REST API and gateway."""

    platform_name = "discord"
    max_message_len = 1900

    def __init__(self, http: aiohttp.ClientSession, token: str,
                 channel_id: str, guild_id: str = ""):
        self.http = http
        self.token = token
        self.channel_id = channel_id
        self.guild_id = guild_id

    async def post_message(self, channel_id: str, content: str, **kwargs) -> Any:
        """Post a message to a channel or thread."""
        if len(content) > self.max_message_len:
            content = content[:self.max_message_len] + "\n...(truncated)"
        return await discord_request(
            self.http, self.token, "POST",
            f"/channels/{channel_id}/messages", {"content": content})

    async def find_thread(self, thread_name: str) -> str | None:
        """Find a thread by name. Checks active, archived, then message threads."""
        # 1. Active threads (guild-level endpoint)
        if self.guild_id:
            data = await discord_request(
                self.http, self.token, "GET",
                f"/guilds/{self.guild_id}/threads/active")
            if isinstance(data, dict):
                for t in data.get("threads", []):
                    if (t.get("name") == thread_name
                            and t.get("parent_id") == self.channel_id):
                        return t["id"]

        # 2. Archived threads
        data = await discord_request(
            self.http, self.token, "GET",
            f"/channels/{self.channel_id}/threads/archived/public")
        if isinstance(data, dict):
            for t in data.get("threads", []):
                if t.get("name") == thread_name:
                    return t["id"]

        # 3. Channel messages with thread metadata
        data = await discord_request(
            self.http, self.token, "GET",
            f"/channels/{self.channel_id}/messages?limit=50")
        if isinstance(data, list):
            for m in data:
                t = m.get("thread", {})
                if t.get("name") == thread_name:
                    return t["id"]

        return None

    async def create_thread(self, thread_name: str, starter_msg: str) -> str | None:
        """Create a new thread: post starter message, then create thread on it."""
        msg = await discord_request(
            self.http, self.token, "POST",
            f"/channels/{self.channel_id}/messages", {"content": starter_msg})
        msg_id = msg.get("id") if isinstance(msg, dict) else None
        if not msg_id:
            return None

        thread = await discord_request(
            self.http, self.token, "POST",
            f"/channels/{self.channel_id}/messages/{msg_id}/threads",
            {"name": thread_name})
        thread_id = thread.get("id") if isinstance(thread, dict) else None
        if thread_id:
            session_name = parse_thread_name(thread_name) or thread_name
            welcome = (
                f"**Welcome to {thread_name}** \U0001f44b\n\n"
                "Type a message here to forward it to the tmux session.\n\n"
                "**Commands:**\n"
                "`!sessions` \u2014 list all sessions\n"
                f"`!kill {session_name}` \u2014 kill this session + archive thread\n\n"
                "**Shortcuts:**\n"
                "`!c` Ctrl+C \u2022 `!d` Ctrl+D \u2022 `!z` Ctrl+Z\n"
                "`!q` quit pager \u2022 `!esc` Escape \u2022 `!enter` Enter"
            )
            await self.post_message(thread_id, welcome)
        return thread_id

    async def ensure_thread(self, thread_name: str,
                            starter_msg: str | None = None) -> str | None:
        """Find or create a thread. Unarchive if archived."""
        thread_id = await self.find_thread(thread_name)
        if thread_id:
            await discord_request(
                self.http, self.token, "PATCH",
                f"/channels/{thread_id}", {"archived": False})
            return thread_id
        if starter_msg is None:
            starter_msg = f"tmux session: **{thread_name}**"
        return await self.create_thread(thread_name, starter_msg)

    async def archive_thread(self, thread_id: str):
        """Archive a thread."""
        await discord_request(
            self.http, self.token, "PATCH",
            f"/channels/{thread_id}", {"archived": True})

    async def delete_thread(self, thread_id: str):
        """Delete a thread (Discord channel delete)."""
        await discord_request(
            self.http, self.token, "DELETE",
            f"/channels/{thread_id}")

    async def get_active_thread_sessions(self) -> set[str]:
        """List session names from active [agent] threads."""
        active: set[str] = set()
        if self.guild_id:
            data = await discord_request(
                self.http, self.token, "GET",
                f"/guilds/{self.guild_id}/threads/active")
            if isinstance(data, dict):
                for t in data.get("threads", []):
                    name = t.get("name", "")
                    if t.get("parent_id") == self.channel_id:
                        parsed = parse_thread_name(name)
                        if parsed:
                            active.add(parsed)
        return active


# --- Message handler ---

async def handle_message(
    core: BridgeCore, platform: DiscordPlatform,
    bot_user_id: str, message: dict[str, Any],
):
    """Handle a Discord MESSAGE_CREATE -- route to core.handle_command() or relay."""
    author = message.get("author", {})
    if author.get("id") == bot_user_id or author.get("bot"):
        return

    channel_id = message.get("channel_id", "")
    content = message.get("content", "").strip()

    # Check if we're in an [agent] thread
    ch = await discord_request(
        platform.http, platform.token, "GET", f"/channels/{channel_id}")
    is_agent_thread = (
        isinstance(ch, dict)
        and ch.get("type") in (11, 12)
        and parse_thread_name(ch.get("name", ""))
    )

    # In agent threads: handle shortcuts before global commands
    if is_agent_thread and content.lower() in SHORTCUTS:
        pass  # fall through to thread forwarding below
    elif content.startswith("!"):
        print(f"[bridge] command: {content[:80]}")
        await core.handle_command(channel_id, content)
        return

    # Only forward messages in [agent] threads
    if not is_agent_thread:
        return

    thread_name = ch.get("name", "") if isinstance(ch, dict) else ""
    session_name = parse_thread_name(thread_name)
    if not session_name:
        return
    user_name = author.get("username", "unknown")

    # Build message: text content + attachment URLs
    parts_msg = []
    if content:
        parts_msg.append(content)
    for att in message.get("attachments", []):
        url = att.get("url", "")
        if url:
            parts_msg.append(url)
    user_message = " ".join(parts_msg)
    if not user_message:
        return

    print(f"[bridge] {user_name} -> [{session_name}]: ({len(user_message)} chars)")

    # Typing indicator
    await discord_request(
        platform.http, platform.token, "POST",
        f"/channels/{channel_id}/typing")

    # Delegate to core for session lookup, shortcut handling, forwarding,
    # and shell output capture
    await core.relay_message(
        channel_id=channel_id,
        session_name=session_name,
        user_message=user_message,
        user_name=user_name,
        source_id=message.get("id", ""),
    )


# --- Gateway ---

async def gateway_connect(
    token: str, core: BridgeCore, platform: DiscordPlatform,
    announced: dict[str, bool],
):
    """Connect to Discord gateway via WebSocket and listen for messages."""
    async with aiohttp.ClientSession() as http:
        # Get gateway URL
        gw = await discord_request(http, token, "GET", "/gateway/bot")
        ws_url = (gw.get("url", "wss://gateway.discord.gg") if isinstance(gw, dict) else "wss://gateway.discord.gg") + "?v=10&encoding=json"

        # Get bot user info
        me = await discord_request(http, token, "GET", "/users/@me")
        bot_user_id = me.get("id", "") if isinstance(me, dict) else ""
        print(f"[bridge] Connected as {me.get('username') if isinstance(me, dict) else '?'} ({bot_user_id})")

        # Cache guild ID
        if not platform.guild_id and platform.channel_id:
            ch = await discord_request(http, token, "GET",
                                       f"/channels/{platform.channel_id}")
            platform.guild_id = (ch.get("guild_id", "")
                                 if isinstance(ch, dict) else "")
            if platform.guild_id:
                print(f"[bridge] Guild: {platform.guild_id}")

        # Announce commands on first connect
        if not announced.get("done"):
            announced["done"] = True
            lines = [
                "**aily bridge connected**",
                "Available commands:",
                "- `!new <name> [host|dir] [dir] [-- cmd]` \u2014 create tmux session",
                "- `!kill <name>` \u2014 kill tmux session",
                "- `!sessions` \u2014 list active sessions",
                "- `!queue` \u2014 list / add / execute deferred commands",
                "- `!lq` \u2014 session limit queue",
                f"Hosts: `{'`, `'.join(core.state.ssh_hosts)}`",
            ]
            # Append live usage status if dashboard is reachable
            usage = await core.dashboard_api("GET", "/api/usage")
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
            await platform.post_message(platform.channel_id, "\n".join(lines))

        # Update the platform's http session for this gateway cycle
        platform.http = http

        intents = INTENT_GUILDS | INTENT_GUILD_MESSAGES | INTENT_MESSAGE_CONTENT
        sequence = None
        hb_task = None

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

                    # Hello -- identify and start heartbeating
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
                                    "device": "agent-bridge",
                                },
                            },
                        })
                        hb_task = asyncio.create_task(
                            heartbeat_loop(ws, heartbeat_interval,
                                           lambda: sequence))

                    # Heartbeat ACK
                    elif op == 11:
                        pass

                    # Dispatch events
                    elif op == 0:
                        if t == "MESSAGE_CREATE":
                            core.track_task(
                                handle_message(core, platform, bot_user_id, d))

                    # Reconnect / Invalid session
                    elif op in (7, 9):
                        print(f"[bridge] Gateway op {op}, reconnecting...",
                              file=sys.stderr)
                        break

                elif msg.type == aiohttp.WSMsgType.CLOSE:
                    print(f"[bridge] WebSocket CLOSE: code={ws.close_code} "
                          f"data={msg.data} extra={msg.extra}", file=sys.stderr)
                    break
                elif msg.type in (aiohttp.WSMsgType.CLOSED,
                                  aiohttp.WSMsgType.ERROR):
                    print(f"[bridge] WebSocket closed/error: type={msg.type} "
                          f"data={msg.data} extra={msg.extra}", file=sys.stderr)
                    break

            # Cancel heartbeat task on disconnect
            if hb_task is not None:
                hb_task.cancel()
                try:
                    await hb_task
                except asyncio.CancelledError:
                    pass

            # Log close reason for debugging
            if ws.close_code:
                print(f"[bridge] Gateway closed: code={ws.close_code}",
                      file=sys.stderr)
                if ws.close_code == 4004:
                    print("[bridge] FATAL: Invalid bot token (close code 4004). "
                          "Exiting.", file=sys.stderr)
                    sys.exit(1)
                if ws.close_code == 4014:
                    print("[bridge] FATAL: Message Content Intent not enabled "
                          "(close code 4014). Exiting.", file=sys.stderr)
                    print("[bridge] Enable it at: https://discord.com/developers/"
                          "applications -> Bot -> Privileged Intents",
                          file=sys.stderr)
                    sys.exit(1)


async def heartbeat_loop(ws, interval: float, get_sequence):
    """Send heartbeats at the specified interval."""
    while True:
        await asyncio.sleep(interval)
        try:
            await ws.send_json({"op": 1, "d": get_sequence()})
        except Exception:
            break


# --- Entry point ---

async def main():
    import os

    _xdg_config = os.environ.get("XDG_CONFIG_HOME",
                                  os.path.expanduser("~/.config"))
    _default_path = os.path.join(_xdg_config, "aily", "env")
    env_path = os.environ.get("AGENT_BRIDGE_ENV", _default_path)

    if not os.path.exists(env_path):
        print(f"Config not found: {env_path}", file=sys.stderr)
        sys.exit(1)

    env = load_env(env_path)

    # Load shared config via BridgeCore
    state = BridgeCore.load_common_config(env)

    # Discord-specific env vars
    token = env.get("DISCORD_BOT_TOKEN")
    channel_id = env.get("DISCORD_CHANNEL_ID", "")

    if not token:
        print("DISCORD_BOT_TOKEN not set", file=sys.stderr)
        sys.exit(1)
    if not channel_id:
        print("DISCORD_CHANNEL_ID not set", file=sys.stderr)
        sys.exit(1)

    print("[bridge] Starting agent bridge...")
    print(f"[bridge] Multiplexer: {state.mux.name}")
    print(f"[bridge] SSH hosts: {state.ssh_hosts}")
    print(f"[bridge] Channel: {channel_id}")
    print(f"[bridge] Thread format: '{state.thread_name_format}'")
    print(f"[bridge] Thread cleanup: {state.thread_cleanup}")
    if state.default_working_dir:
        print(f"[bridge] Default working dir: {state.default_working_dir}")
    if state.new_session_agent:
        rc_label = (" + remote-control"
                     if state.new_session_agent == "claude"
                     and state.claude_remote_control else "")
        print(f"[bridge] Auto-launch agent: {state.new_session_agent}{rc_label}")
    if state.dashboard_url:
        print(f"[bridge] Dashboard: {state.dashboard_url}")
    else:
        print("[bridge] Dashboard: not configured (AILY_DASHBOARD_URL not set)")
    if state.session_queue_enabled:
        print(f"[bridge] Session limit queue: enabled "
              f"(interval={state.session_queue_retry_interval}s, "
              f"max_retries={state.session_queue_max_retries}, "
              f"detect_delay={state.session_queue_detect_delay}s)")
    else:
        print("[bridge] Session limit queue: disabled")

    # Create shared aiohttp session for dashboard POSTs
    dashboard_http = aiohttp.ClientSession()
    state.dashboard_http = dashboard_http

    # Create platform and core
    platform = DiscordPlatform(
        http=dashboard_http,  # temporary; replaced per gateway_connect cycle
        token=token,
        channel_id=channel_id,
    )
    core = BridgeCore(state, platform)

    announced: dict[str, bool] = {"done": False}
    reconnect_delay = 5
    max_delay = 300  # 5 minutes cap
    consecutive_failures = 0

    # Start session limit retry loop if enabled
    retry_task = None
    if state.session_queue_enabled and state.dashboard_url:
        retry_task = asyncio.create_task(core.session_limit_retry_loop())
        print("[bridge] Session limit retry loop started")

    try:
        while True:
            try:
                await gateway_connect(token, core, platform, announced)
                reconnect_delay = 5  # reset on successful connection
                consecutive_failures = 0
            except Exception as e:
                consecutive_failures += 1
                print(f"[bridge] Connection error (attempt {consecutive_failures}): "
                      f"{e}, reconnecting in {reconnect_delay}s...", file=sys.stderr)
                if consecutive_failures >= 50:
                    print("[bridge] Too many consecutive failures, exiting.",
                          file=sys.stderr)
                    sys.exit(1)
            await asyncio.sleep(reconnect_delay)
            reconnect_delay = min(reconnect_delay * 2, max_delay)
    finally:
        if retry_task:
            retry_task.cancel()
            try:
                await retry_task
            except asyncio.CancelledError:
                pass
        await dashboard_http.close()


if __name__ == "__main__":
    asyncio.run(main())
