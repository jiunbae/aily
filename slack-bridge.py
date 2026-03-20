#!/usr/bin/env python3
"""
Slack <-> terminal multiplexer session bridge.

Monitors Slack threads named [agent] <session> and forwards user messages
to the corresponding session's Claude Code instance via SSH.

Supports tmux (full) and zellij (partial) backends via AILY_MULTIPLEXER env var.

Also handles ! commands for session/thread lifecycle management:
  !new <name> [host|dir] [dir] [-- cmd] -- create session + Slack thread
  !kill <name>        -- kill session + close Slack thread
  !sessions           -- list all sessions with sync status
  !c / !d / !z / !q   -- keyboard shortcuts forwarded to session
  !lq                 -- session limit queue management

Uses Socket Mode (WebSocket) -- no public URL needed.
Requires: slack-sdk (pip install slack-sdk)

Thin platform layer -- all shared logic lives in bridge_core.py.
"""

import asyncio
import sys
from collections import OrderedDict

from slack_sdk.web.async_client import AsyncWebClient
from slack_sdk.socket_mode.aiohttp import SocketModeClient
from slack_sdk.socket_mode.request import SocketModeRequest
from slack_sdk.socket_mode.response import SocketModeResponse

from bridge_core import (
    BridgeCore,
    BridgeState,
    PlatformBridge,
    load_env,
    parse_thread_name,
    AGENT_PREFIX,
)


# ---------------------------------------------------------------------------
# Slack platform implementation
# ---------------------------------------------------------------------------

class SlackPlatform(PlatformBridge):
    """Slack-specific bridge operations using slack-sdk."""

    platform_name = "slack"
    max_message_len = 3800

    def __init__(self, web_client: AsyncWebClient, channel_id: str):
        self._client = web_client
        self._channel_id = channel_id
        # Cache: thread_ts -> session_name (LRU via OrderedDict)
        self._THREAD_CACHE_MAX = 256
        self._thread_cache: OrderedDict[str, str] = OrderedDict()
        self._thread_cache_lock: asyncio.Lock = asyncio.Lock()

    # -- PlatformBridge interface ------------------------------------------

    async def post_message(self, channel_id: str, text: str, **kwargs) -> None:
        """Post a message, optionally in a thread (thread_ts kwarg)."""
        if len(text) > self.max_message_len:
            text = text[:self.max_message_len] + "\n...(truncated)"
        msg_kwargs = {"channel": channel_id, "text": text}
        thread_ts = kwargs.get("thread_ts")
        if thread_ts:
            msg_kwargs["thread_ts"] = thread_ts
        try:
            await self._client.chat_postMessage(**msg_kwargs)
        except Exception as e:
            print(f"[slack-bridge] post_message error: {e}", file=sys.stderr)

    async def find_thread(self, thread_name: str) -> str | None:
        """Find a parent message whose text starts with thread_name.

        Returns thread_ts (Slack's thread identifier) or None.
        """
        try:
            result = await self._client.conversations_history(
                channel=self._channel_id, limit=200
            )
            for msg in result.get("messages", []):
                text = msg.get("text", "")
                first_line = text.split("\n")[0].strip()
                if first_line == thread_name or text.startswith(thread_name):
                    return msg["ts"]
        except Exception as e:
            print(f"[slack-bridge] find_thread error: {e}", file=sys.stderr)
        return None

    async def create_thread(self, thread_name: str, starter_msg: str) -> str | None:
        """Create a new thread: post parent message, then welcome reply."""
        try:
            result = await self._client.chat_postMessage(
                channel=self._channel_id, text=starter_msg
            )
            parent_ts = result.get("ts")
            if not parent_ts:
                return None

            session_name = parse_thread_name(thread_name) or thread_name
            welcome = (
                f"*Welcome to {thread_name}*\n\n"
                "Type a message here to forward it to the session.\n\n"
                "*Commands:*\n"
                "`!sessions` \u2014 list all sessions\n"
                f"`!kill {session_name}` \u2014 kill this session + close thread\n"
                "`!c` `!d` `!z` `!q` `!esc` `!enter` \u2014 keyboard shortcuts"
            )
            await self._client.chat_postMessage(
                channel=self._channel_id, thread_ts=parent_ts, text=welcome
            )
            return parent_ts
        except Exception as e:
            print(f"[slack-bridge] create_thread error: {e}", file=sys.stderr)
            return None

    async def ensure_thread(self, thread_name: str, starter_msg: str | None = None) -> str | None:
        """Find or create a thread."""
        ts = await self.find_thread(thread_name)
        if ts:
            return ts
        if starter_msg is None:
            starter_msg = f"session: *{thread_name}*"
        return await self.create_thread(thread_name, starter_msg)

    async def archive_thread(self, thread_id: str) -> None:
        """Archive a thread (post closing message + :lock: reaction)."""
        try:
            await self._client.chat_postMessage(
                channel=self._channel_id,
                thread_ts=thread_id,
                text=":lock: Thread archived. Session closed.",
            )
            await self._client.reactions_add(
                channel=self._channel_id, timestamp=thread_id, name="lock"
            )
        except Exception as e:
            print(f"[slack-bridge] archive_thread error: {e}", file=sys.stderr)

    async def delete_thread(self, thread_id: str) -> None:
        """Delete a thread by deleting the parent message."""
        try:
            await self._client.chat_delete(
                channel=self._channel_id, ts=thread_id
            )
        except Exception as e:
            print(f"[slack-bridge] delete_thread error: {e}", file=sys.stderr)

    async def get_active_thread_sessions(self) -> set[str]:
        """Scan channel for active [agent] threads (not archived)."""
        active: set[str] = set()
        try:
            result = await self._client.conversations_history(
                channel=self._channel_id, limit=200
            )
            for msg in result.get("messages", []):
                text = msg.get("text", "")
                first_line = text.split("\n")[0].strip()
                session = parse_thread_name(first_line)
                if session:
                    reactions = msg.get("reactions", [])
                    is_locked = any(r.get("name") == "lock" for r in reactions)
                    if not is_locked:
                        active.add(session)
        except Exception as e:
            print(f"[slack-bridge] thread scan error: {e}", file=sys.stderr)
        return active

    # -- Thread cache (Slack-specific: maps thread_ts -> session_name) -----

    async def _cache_thread(self, thread_ts: str, session_name: str) -> None:
        """Add entry to thread cache with LRU eviction."""
        async with self._thread_cache_lock:
            self._thread_cache[thread_ts] = session_name
            while len(self._thread_cache) > self._THREAD_CACHE_MAX:
                self._thread_cache.popitem(last=False)

    async def get_thread_session(self, channel: str, thread_ts: str) -> str | None:
        """Get session name from a thread's parent message. Uses cache."""
        async with self._thread_cache_lock:
            if thread_ts in self._thread_cache:
                self._thread_cache.move_to_end(thread_ts)
                return self._thread_cache[thread_ts]

        try:
            result = await self._client.conversations_replies(
                channel=channel, ts=thread_ts, limit=1
            )
            messages = result.get("messages", [])
            if not messages:
                return None
            parent_text = messages[0].get("text", "")
        except Exception:
            return None

        first_line = parent_text.split("\n")[0].strip()
        session_name = parse_thread_name(first_line)
        if session_name:
            await self._cache_thread(thread_ts, session_name)
            return session_name
        return None

    async def invalidate_thread_cache(self, thread_id: str) -> None:
        """Remove a thread from the cache (called by core on !kill)."""
        async with self._thread_cache_lock:
            self._thread_cache.pop(thread_id, None)


# ---------------------------------------------------------------------------
# Socket Mode event handler
# ---------------------------------------------------------------------------

async def handle_socket_event(
    core: BridgeCore,
    platform: SlackPlatform,
    bot_user_id: str,
    socket_client: SocketModeClient,
    req: SocketModeRequest,
) -> None:
    """Handle incoming Socket Mode events."""
    # Acknowledge immediately
    response = SocketModeResponse(envelope_id=req.envelope_id)
    await socket_client.send_socket_mode_response(response)

    if req.type != "events_api":
        return

    event = req.payload.get("event", {})
    if event.get("type") != "message":
        return
    if event.get("subtype"):
        return
    if event.get("bot_id"):
        return
    if event.get("user") == bot_user_id:
        return

    channel = event.get("channel", "")
    thread_ts = event.get("thread_ts")
    text = event.get("text", "").strip()

    if not text:
        return

    # Handle ! commands (in channel or thread)
    if text.startswith("!"):
        # Check if it's a shortcut in a thread -- route to relay
        is_shortcut = text.lower() in core.SHORTCUTS
        if is_shortcut and thread_ts:
            # Fall through to thread message handling below
            pass
        else:
            print(f"[slack-bridge] command: {text[:80]}")
            await core.handle_command(channel, text, thread_ts=thread_ts)
            return

    # Only forward messages that are in threads
    if not thread_ts:
        return

    # Check if the parent message matches [agent] pattern
    session_name = await platform.get_thread_session(channel, thread_ts)
    if not session_name:
        return

    user_id = event.get("user", "unknown")
    source_id = event.get("client_msg_id", event.get("ts", ""))

    print(f"[slack-bridge] {user_id} -> [{session_name}]: ({len(text)} chars)")

    await core.relay_message(
        channel_id=channel,
        session_name=session_name,
        user_message=text,
        user_name=user_id,
        source_id=source_id,
        thread_ts=thread_ts,
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main():
    import os
    import aiohttp

    _xdg_config = os.environ.get("XDG_CONFIG_HOME",
                                  os.path.expanduser("~/.config"))
    _default_path = os.path.join(_xdg_config, "aily", "env")
    env_path = os.environ.get("AGENT_BRIDGE_ENV", _default_path)

    if not os.path.exists(env_path):
        print(f"Config not found: {env_path}", file=sys.stderr)
        sys.exit(1)

    env = load_env(env_path)

    # Slack-specific tokens
    bot_token = env.get("SLACK_BOT_TOKEN")
    app_token = env.get("SLACK_APP_TOKEN")
    channel_id = env.get("SLACK_CHANNEL_ID", "")

    if not bot_token:
        print("SLACK_BOT_TOKEN not set", file=sys.stderr)
        sys.exit(1)
    if not app_token:
        print("SLACK_APP_TOKEN not set (required for Socket Mode)", file=sys.stderr)
        sys.exit(1)
    if not channel_id:
        print("SLACK_CHANNEL_ID not set", file=sys.stderr)
        sys.exit(1)

    # Build shared state from common config
    state = BridgeCore.load_common_config(env)

    # Create Slack platform + core
    web_client = AsyncWebClient(token=bot_token)
    platform = SlackPlatform(web_client, channel_id)
    core = BridgeCore(state, platform)

    # Get bot user info
    auth = await web_client.auth_test()
    bot_user_id = auth.get("user_id", "")

    # Print startup info
    print(f"[slack-bridge] Connected as {auth.get('user', '')} ({bot_user_id})")
    print(f"[slack-bridge] Multiplexer: {state.mux.name}")
    print(f"[slack-bridge] Channel: {channel_id}")
    print(f"[slack-bridge] SSH hosts: {state.ssh_hosts}")
    print(f"[slack-bridge] Thread format: '{state.thread_name_format}'")
    print(f"[slack-bridge] Thread cleanup: {state.thread_cleanup}")
    if state.new_session_agent:
        rc_label = (" + remote-control"
                     if state.new_session_agent == "claude"
                     and state.claude_remote_control else "")
        print(f"[slack-bridge] Auto-launch agent: {state.new_session_agent}{rc_label}")
    if state.dashboard_url:
        print(f"[slack-bridge] Dashboard: {state.dashboard_url}")
    else:
        print("[slack-bridge] Dashboard: not configured (AILY_DASHBOARD_URL not set)")
    if state.session_queue_enabled:
        print(f"[slack-bridge] Session limit queue: enabled "
              f"(interval={state.session_queue_retry_interval}s, "
              f"max_retries={state.session_queue_max_retries}, "
              f"detect_delay={state.session_queue_detect_delay}s)")
    else:
        print("[slack-bridge] Session limit queue: disabled")

    # Initialize dashboard HTTP session
    dashboard_http = aiohttp.ClientSession()
    state.dashboard_http = dashboard_http

    announced = False
    reconnect_delay = 5
    max_delay = 300  # 5 minutes cap
    consecutive_failures = 0

    # Start session limit retry loop if enabled
    retry_task = None
    if state.session_queue_enabled and state.dashboard_url:
        retry_task = asyncio.create_task(core.session_limit_retry_loop())
        print("[slack-bridge] Session limit retry loop started")

    try:
        while True:
            socket_client = None
            try:
                socket_client = SocketModeClient(
                    app_token=app_token, web_client=web_client
                )

                # Bind event handler with closure over core/platform/bot_user_id
                async def _handler(client, req):
                    await handle_socket_event(core, platform, bot_user_id, client, req)

                socket_client.socket_mode_request_listeners.append(_handler)

                print("[slack-bridge] Starting Socket Mode connection...")
                await socket_client.connect()
                consecutive_failures = 0
                reconnect_delay = 5

                if not announced:
                    announced = True
                    lines = [
                        "*aily bridge connected*",
                        "Available commands:",
                        "- `!new <name> [host|dir] [dir] [-- cmd]` \u2014 create tmux session",
                        "- `!kill <name>` \u2014 kill tmux session",
                        "- `!sessions` \u2014 list active sessions",
                        "- `!queue` \u2014 list / add / execute deferred commands",
                        "- `!lq` \u2014 session limit queue",
                        "`!c` `!d` `!z` `!q` `!esc` `!enter` \u2014 keyboard shortcuts",
                        f"Hosts: `{'`, `'.join(core.state.ssh_hosts)}`",
                    ]
                    # Append live usage status if dashboard is reachable
                    usage = await core.dashboard_api("GET", "/api/usage")
                    if usage and usage.get("usage"):
                        lines.append("")
                        lines.append("*API Usage*")
                        for provider, snap in usage["usage"].items():
                            req_rem = snap.get("requests_remaining")
                            req_lim = snap.get("requests_limit")
                            tok_rem = snap.get("tokens_remaining")
                            tok_lim = snap.get("tokens_limit")
                            parts_ = [f"*{provider}*:"]
                            if req_lim is not None:
                                parts_.append(f"requests {req_rem}/{req_lim}")
                            if tok_lim is not None:
                                parts_.append(f"tokens {tok_rem}/{tok_lim}")
                            lines.append("  ".join(parts_))
                        qs = usage.get("queue_stats", {})
                        pending = qs.get("pending", 0)
                        if pending:
                            lines.append(f"Queued commands: *{pending}* pending")
                    await platform.post_message(channel_id, "\n".join(lines))

                # Keep alive
                while True:
                    await asyncio.sleep(1)
            except KeyboardInterrupt:
                break
            except Exception as e:
                consecutive_failures += 1
                print(
                    f"[slack-bridge] Connection error (attempt {consecutive_failures}): "
                    f"{e}, reconnecting in {reconnect_delay}s...",
                    file=sys.stderr,
                )
                if consecutive_failures >= 50:
                    print("[slack-bridge] Too many consecutive failures, exiting.",
                          file=sys.stderr)
                    break
            finally:
                if socket_client is not None:
                    try:
                        await socket_client.close()
                    except Exception:
                        pass
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
