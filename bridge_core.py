"""
Shared bridge core for Discord and Slack bridges.

Contains all platform-agnostic logic: multiplexer interaction, SSH commands,
session management, dashboard integration, command handlers, and session
limit queue (detection, retry loop, queue commands).

Platform-specific bridges implement the PlatformBridge protocol and delegate
shared work to BridgeCore.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol, runtime_checkable

import aiohttp

from multiplexer import Multiplexer
from session_limit import detect_session_limit


# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------

AGENT_PREFIX = "[agent] "  # legacy fallback
SEND_KEYS_DELAY = 0.3
SESSION_NAME_RE = re.compile(r'^[a-zA-Z0-9_-]+$')
_SAFE_PATH_RE = re.compile(r'^[a-zA-Z0-9_./@:~-]+$')

_SECRET_PATTERNS = re.compile(
    r'(?i)'
    r'((?:password|passwd|secret|token|api[_-]?key|access[_-]?key|private[_-]?key'
    r'|credential|auth|bearer|ssh[_-]?key|database[_-]?url|connection[_-]?string'
    r'|key[_-]?id|client[_-]?secret)'
    r'\s*[=:])\s*(?:"[^"]*"|\'[^\']*\'|\S+)',
)
_PEM_RE = re.compile(r'-----BEGIN [A-Z ]+-----[\s\S]*?-----END [A-Z ]+-----')

# Infrastructure sessions that should be hidden from session lists
_INFRA_SESSIONS = {"aily-bridge", "slack-bridge", "aily-dashboard"}

# Shell names for output capture (skip capture for non-shell processes)
_SHELL_NAMES = frozenset({"bash", "zsh", "sh", "fish", "dash", "ksh", "tcsh", "csh"})

# Shortcut commands: message -> multiplexer key sequence
_SHORTCUTS = {
    "!c":     "C-c",
    "!d":     "C-d",
    "!z":     "C-z",
    "!q":     "q",
    "!enter": "Enter",
    "!esc":   "Escape",
}

_MAX_BACKGROUND_TASKS = 20


# ---------------------------------------------------------------------------
# BridgeState dataclass
# ---------------------------------------------------------------------------

@dataclass
class BridgeState:
    mux: Multiplexer
    ssh_hosts: list[str]
    default_host: str
    default_working_dir: str
    thread_name_format: str
    thread_cleanup: str
    new_session_agent: str
    claude_remote_control: bool
    dashboard_url: str
    dashboard_auth_token: str
    # Session limit queue config
    session_queue_enabled: bool = True
    session_queue_retry_interval: int = 1800
    session_queue_max_retries: int = 12
    session_queue_detect_delay: int = 15
    dashboard_http: aiohttp.ClientSession | None = None
    background_tasks: set[asyncio.Task[None]] = field(default_factory=set)
    background_sem: asyncio.Semaphore = field(
        default_factory=lambda: asyncio.Semaphore(_MAX_BACKGROUND_TASKS)
    )


# ---------------------------------------------------------------------------
# PlatformBridge protocol
# ---------------------------------------------------------------------------

@runtime_checkable
class PlatformBridge(Protocol):
    """Protocol that platform-specific bridges must implement."""

    async def post_message(self, channel_id: str, text: str, **kwargs) -> None: ...

    async def find_thread(self, thread_name: str) -> str | None: ...

    async def create_thread(self, thread_name: str, starter_msg: str) -> str | None: ...

    async def ensure_thread(self, thread_name: str, starter_msg: str = "") -> str | None: ...

    async def archive_thread(self, thread_id: str) -> None: ...

    async def delete_thread(self, thread_id: str) -> None: ...

    async def get_active_thread_sessions(self) -> set[str]: ...

    @property
    def platform_name(self) -> str: ...

    @property
    def max_message_len(self) -> int: ...


# ---------------------------------------------------------------------------
# BridgeCore -- shared logic
# ---------------------------------------------------------------------------

class BridgeCore:
    """Platform-agnostic bridge logic.

    Holds a ``BridgeState`` and a ``PlatformBridge`` reference.  All methods
    that were synchronous in the original bridges remain synchronous here;
    callers wrap them with ``asyncio.to_thread`` as needed.
    """

    # Expose shortcuts as class attribute for platform bridges
    SHORTCUTS = _SHORTCUTS

    def __init__(self, state: BridgeState, platform: PlatformBridge) -> None:
        self.state = state
        self.platform = platform

    # -- static helpers (no self/state needed) ------------------------------

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def load_env(env_path: str) -> dict[str, str]:
        """Load env config file."""
        env: dict[str, str] = {}
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, val = line.split("=", 1)
                    env[key.strip()] = val.strip().strip('"').strip("'")
        return env

    @staticmethod
    def _validate_path(path: str) -> bool:
        """Reject paths with shell metacharacters or directory traversal."""
        return bool(_SAFE_PATH_RE.match(path)) and '..' not in path

    @staticmethod
    def is_valid_session_name(name: str) -> bool:
        """Check if session name is safe for use in shell commands."""
        return bool(SESSION_NAME_RE.match(name)) and len(name) <= 64

    @staticmethod
    def _sanitize_backticks(text: str) -> str:
        """Escape triple backticks in text to prevent markdown injection."""
        return text.replace('```', r'\`\`\`')

    @staticmethod
    def _redact_secrets(text: str) -> str:
        """Redact common secret patterns from shell output."""
        text = _SECRET_PATTERNS.sub(r'\1 [REDACTED]', text)
        text = _PEM_RE.sub('[REDACTED PEM KEY]', text)
        return text

    @staticmethod
    def _is_prompt_line(line: str) -> bool:
        """Detect common shell prompt lines (powerline, starship, p10k, plain)."""
        stripped = line.strip()
        if not stripped:
            return False
        clean = re.sub(r'\x1b\[[0-9;]*m', '', stripped)

        # Lines containing many box-drawing chars -- prompt decorations
        decor_count = sum(1 for c in clean if c in '\u2500\u2550\u2501')
        if decor_count >= 5:
            return True
        # Lines ending with common prompt chars
        if clean.rstrip().endswith(('\u276f', '$ ', '% ', '> ', '$', '%', '>')):
            return True
        # Lines starting with prompt indicators
        if re.match(r'^\s*[\u276f$%>]\s*$', clean):
            return True
        return False

    # -- instance methods using self.state ----------------------------------

    def format_thread_name(self, session: str, host: str = "") -> str:
        """Build thread name from format template."""
        if not host:
            host = self.state.default_host or "localhost"
        return (
            self.state.thread_name_format
            .replace("{session}", session)
            .replace("{host}", host)
        )

    def parse_thread_name(self, thread_name: str) -> str | None:
        """Extract session name from a thread name using the format template.

        Builds a regex from thread_name_format by replacing {session} with a
        capture group and {host} with a wildcard, then matches against the
        thread name.  Falls back to legacy AGENT_PREFIX stripping.
        """
        fmt = re.escape(self.state.thread_name_format)
        fmt = fmt.replace(re.escape("{session}"), r"([a-zA-Z0-9_-]+)")
        fmt = fmt.replace(re.escape("{host}"), r".+")
        m = re.match(f"^{fmt}$", thread_name)
        if m:
            return m.group(1)
        # Legacy fallback: [agent] <session>
        if thread_name.startswith(AGENT_PREFIX):
            return thread_name[len(AGENT_PREFIX):]
        return None

    def run_ssh(self, host: str, cmd: str, timeout: int = 15) -> tuple[int, str]:
        """Run a command over SSH (or locally for localhost).

        Returns (returncode, stdout).
        """
        try:
            if host in ("localhost", "127.0.0.1", "::1"):
                result = subprocess.run(
                    ["bash", "-c", cmd],
                    capture_output=True, text=True, timeout=timeout,
                )
            else:
                result = subprocess.run(
                    ["ssh", host, cmd],
                    capture_output=True, text=True, timeout=timeout,
                )
            return result.returncode, result.stdout.strip()
        except subprocess.TimeoutExpired:
            return 1, ""
        except Exception as e:
            return 1, str(e)

    def find_session_host(self, session_name: str) -> str | None:
        """Find which SSH host has the multiplexer session."""
        mux = self.state.mux
        safe_name = shlex.quote(session_name)
        for host in self.state.ssh_hosts:
            rc, out = self.run_ssh(
                host, f"{mux.has_session_cmd(safe_name)} 2>/dev/null && echo found"
            )
            if rc == 0 and "found" in out:
                # Verify it's not a prefix match against an infra session
                _, exact = self.run_ssh(
                    host, f"{mux.list_sessions_cmd()} 2>/dev/null"
                )
                sessions = exact.splitlines()
                if session_name in sessions:
                    return host
                if any(
                    s.startswith(session_name) and s in _INFRA_SESSIONS
                    for s in sessions
                ):
                    continue
                return host
        return None

    def send_keys_raw(self, host: str, session: str, keys: str) -> bool:
        """Send raw key sequences (e.g., C-c, C-d, C-z) to a session."""
        mux = self.state.mux
        safe_session = shlex.quote(session)
        rc, _ = self.run_ssh(host, mux.send_raw_key_cmd(safe_session, keys))
        return rc == 0

    def send_to_session(self, host: str, session: str, message: str) -> bool:
        """Send a message to a multiplexer session's Claude Code."""
        mux = self.state.mux
        safe_session = shlex.quote(session)
        safe_message = shlex.quote(message)

        # Step 1: Type the text
        rc, _ = self.run_ssh(host, mux.send_keys_cmd(safe_session, safe_message))
        if rc != 0:
            return False

        # Step 2: Press Enter (separate command -- critical for Claude Code)
        time.sleep(SEND_KEYS_DELAY)
        rc, _ = self.run_ssh(host, mux.send_enter_cmd(safe_session))
        if rc != 0:
            # Clear ghost text to prevent corruption of next message
            self.run_ssh(host, mux.send_raw_key_cmd(safe_session, "C-c"))
            return False
        return True

    # Backward-compatible alias
    send_to_tmux = send_to_session

    def get_pane_command(self, host: str, session: str) -> str:
        """Get the current foreground command in a session's active pane.

        Returns empty string if the multiplexer doesn't support this.
        """
        mux = self.state.mux
        if not mux.supports_pane_command:
            return ""
        safe_session = shlex.quote(session)
        rc, out = self.run_ssh(host, mux.get_pane_command_cmd(safe_session))
        return out.strip() if rc == 0 else ""

    def capture_pane_content(self, host: str, session: str) -> str:
        """Capture visible pane content from a multiplexer session."""
        mux = self.state.mux
        if not mux.supports_detached_capture:
            logging.debug(
                "Skipping pane capture: %s doesn't support detached capture",
                mux.name,
            )
            return ""
        safe_session = shlex.quote(session)
        rc, out = self.run_ssh(
            host, mux.capture_pane_cmd(safe_session), timeout=10
        )
        return out if rc == 0 else ""

    def _build_agent_command(self, agent: str, remote_control: bool) -> str | None:
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
        self,
        host: str,
        session: str,
        pre_content: str,
        poll_interval: float = 1.0,
        stable_count: int = 2,
        max_wait: float = 30.0,
    ) -> str | None:
        """Poll pane until output stabilizes, return new content.

        Returns None if a non-shell process takes over (e.g., Claude Code started).
        Returns empty string if no new output detected.
        """
        time.sleep(1.0)

        # Check: did the command spawn a non-shell process?
        pane_cmd = self.get_pane_command(host, session)
        if pane_cmd and pane_cmd.lower() not in _SHELL_NAMES:
            return None

        last_content = ""
        stable_hits = 0
        deadline = time.monotonic() + max_wait

        while time.monotonic() < deadline:
            current = self.capture_pane_content(host, session)
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
        pane_cmd = self.get_pane_command(host, session)
        if pane_cmd and pane_cmd.lower() not in _SHELL_NAMES:
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

        # Strip prompt/decoration lines from both ends
        while new_lines and (
            not new_lines[-1].strip() or self._is_prompt_line(new_lines[-1])
        ):
            new_lines.pop()
        while new_lines and (
            not new_lines[0].strip() or self._is_prompt_line(new_lines[0])
        ):
            new_lines.pop(0)

        # Strip the command echo line (first line often repeats the sent command)
        if new_lines and re.match(r'^[\u276f$%>]\s+\S', new_lines[0].strip()):
            new_lines.pop(0)

        return '\n'.join(new_lines)

    # -- async helpers (dashboard, background tasks) ------------------------

    async def emit_dashboard_event(self, event: dict[str, Any]) -> None:
        """POST an event to the aily dashboard webhook. Non-blocking, fire-and-forget."""
        state = self.state
        if not state.dashboard_url or state.dashboard_http is None:
            return
        url = f"{state.dashboard_url.rstrip('/')}/api/hooks/event"
        try:
            headers: dict[str, str] = {}
            if state.dashboard_auth_token:
                headers["Authorization"] = f"Bearer {state.dashboard_auth_token}"
            async with state.dashboard_http.post(
                url,
                json=event,
                timeout=aiohttp.ClientTimeout(total=5),
                headers=headers,
            ) as resp:
                if resp.status >= 400:
                    body = await resp.text()
                    print(
                        f"[dashboard] POST {resp.status}: {body[:200]}",
                        file=sys.stderr,
                    )
        except Exception as e:
            print(f"[dashboard] POST failed: {e}", file=sys.stderr)

    def _fire_dashboard_event(self, event: dict[str, Any]) -> None:
        """Schedule a dashboard event from sync or async context without awaiting."""
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self.emit_dashboard_event(event))
        except RuntimeError:
            pass  # No running loop -- skip silently

    async def dashboard_api(
        self, method: str, path: str, json_body: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        """Call dashboard REST API and return parsed JSON response, or None on failure."""
        state = self.state
        if not state.dashboard_url or state.dashboard_http is None:
            return None
        url = f"{state.dashboard_url.rstrip('/')}{path}"
        try:
            headers: dict[str, str] = {}
            if state.dashboard_auth_token:
                headers["Authorization"] = f"Bearer {state.dashboard_auth_token}"
            timeout = aiohttp.ClientTimeout(total=10)
            req_kwargs: dict[str, Any] = {"timeout": timeout, "headers": headers}
            if json_body is not None:
                req_kwargs["json"] = json_body
            async with state.dashboard_http.request(method, url, **req_kwargs) as resp:  # type: ignore[arg-type]
                if resp.status < 400:
                    return await resp.json()
                body = await resp.text()
                print(
                    f"[dashboard] {method} {path} {resp.status}: {body[:200]}",
                    file=sys.stderr,
                )
                return None
        except (aiohttp.ClientError, TimeoutError) as e:
            print(f"[dashboard] {method} {path} failed: {e}", file=sys.stderr)
            return None

    def _track_task(self, coro) -> asyncio.Task[None]:
        """Create a tracked background task that logs exceptions on completion."""
        state = self.state

        async def _limited():
            async with state.background_sem:
                return await coro

        task = asyncio.create_task(_limited())
        state.background_tasks.add(task)

        def _on_done(t: asyncio.Task[None]):
            state.background_tasks.discard(t)
            if not t.cancelled() and t.exception():
                print(
                    f"[bridge] background task failed: {t.exception()}",
                    file=sys.stderr,
                )

        task.add_done_callback(_on_done)
        return task

    # Public alias for platform bridges
    track_task = _track_task

    # -- capture and post output -------------------------------------------

    async def capture_and_post_output(
        self,
        reply_channel: str,
        host: str,
        session: str,
        pre_content: str,
        **reply_kwargs,
    ) -> None:
        """Background task: capture shell output and post to platform.

        Skips capture if a non-shell process (e.g., Claude Code) is running.
        pre_content must be captured BEFORE send_to_session to avoid missing
        fast command output.
        """
        try:
            output = await asyncio.to_thread(
                self.capture_shell_output, host, session, pre_content
            )

            if output is None:
                return
            if not output.strip():
                return

            output = self._redact_secrets(output)
            output = self._sanitize_backticks(output)

            max_len = self.platform.max_message_len
            # Reserve space for wrapper text
            content_limit = max_len - 200
            if len(output) > content_limit:
                output = output[:content_limit] + "\n...(truncated)"

            safe_name = session.replace('`', "'")
            await self.platform.post_message(
                reply_channel,
                f"Shell output from `{safe_name}`:\n```\n{output}\n```",
                **reply_kwargs,
            )

        except Exception as e:
            print(f"[bridge] output capture error: {e}", file=sys.stderr)

    # -- session limit queue ------------------------------------------------

    async def _enqueue_session_limit(
        self,
        session_name: str,
        host: str,
        channel_id: str,
        thread_id: str,
        user_message: str,
        user_name: str | None = None,
        source_msg_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Enqueue a rate-limited message for later retry via dashboard API."""
        state = self.state
        return await self.dashboard_api("POST", "/api/session-queue", {
            "session_name": session_name,
            "host": host,
            "platform": self.platform.platform_name,
            "channel_id": channel_id,
            "thread_id": thread_id,
            "user_message": user_message,
            "user_name": user_name,
            "source_msg_id": source_msg_id,
            "max_retries": state.session_queue_max_retries,
            "retry_interval": state.session_queue_retry_interval,
        })

    async def _fetch_due_queue_items(self) -> list[dict[str, Any]]:
        """Fetch queue items that are due for retry."""
        platform_name = self.platform.platform_name
        result = await self.dashboard_api(
            "GET",
            f"/api/session-queue?status=pending&platform={platform_name}&due=true",
        )
        if not result:
            return []
        return result.get("items", [])

    async def _mark_queue_item_completed(self, item_id: int) -> None:
        """Mark a queue item as completed."""
        await self.dashboard_api("PATCH", f"/api/session-queue/{item_id}", {
            "status": "completed",
            "completed_at": self._now_iso(),
        })

    async def _mark_queue_item_failed(self, item_id: int, error: str) -> None:
        """Mark a queue item as permanently failed."""
        await self.dashboard_api("PATCH", f"/api/session-queue/{item_id}", {
            "status": "failed",
            "last_error": error,
            "completed_at": self._now_iso(),
        })

    async def _update_queue_retry(self, item: dict[str, Any]) -> None:
        """Increment retry count and schedule next retry."""
        state = self.state
        next_retry = (
            datetime.now(timezone.utc)
            + timedelta(seconds=item.get("retry_interval", state.session_queue_retry_interval))
        ).isoformat()
        await self.dashboard_api("PATCH", f"/api/session-queue/{item['id']}", {
            "retry_count": item.get("retry_count", 0) + 1,
            "next_retry_at": next_retry,
            "last_error": "Still rate limited",
        })

    async def detect_session_limit_after_send(
        self,
        channel_id: str,
        host: str,
        session_name: str,
        pre_content: str,
        user_message: str,
        user_name: str | None = None,
        source_msg_id: str | None = None,
        **reply_kwargs,
    ) -> None:
        """Background task: detect session limit after sending and enqueue if needed."""
        state = self.state
        if not state.session_queue_enabled:
            return
        try:
            await asyncio.sleep(state.session_queue_detect_delay)
            post_content = await asyncio.to_thread(
                self.capture_pane_content, host, session_name
            )
            if not post_content:
                return
            error_line = detect_session_limit(pre_content, post_content)
            if not error_line:
                return

            print(
                f"[session-queue] Rate limit detected for {session_name}: {error_line}",
                file=sys.stderr,
            )

            # thread_id for the enqueue: use reply_kwargs thread_ts for Slack,
            # or channel_id for Discord (which uses channel_id as thread)
            thread_id = reply_kwargs.get("thread_ts", channel_id)

            result = await self._enqueue_session_limit(
                session_name, host, channel_id, thread_id,
                user_message, user_name, source_msg_id,
            )
            if result:
                item = result.get("item", {})
                await self.platform.post_message(
                    channel_id,
                    f"Session limit detected for `{session_name}`.\n"
                    f"Message queued for auto-retry (#{item.get('id', '?')}, "
                    f"interval: {state.session_queue_retry_interval}s, "
                    f"max retries: {state.session_queue_max_retries}).\n"
                    f"Error: `{error_line[:200]}`",
                    **reply_kwargs,
                )
            else:
                await self.platform.post_message(
                    channel_id,
                    f"Session limit detected for `{session_name}` "
                    f"but failed to enqueue retry.\n"
                    f"Error: `{error_line[:200]}`",
                    **reply_kwargs,
                )
        except Exception as e:
            print(f"[session-queue] Detection error: {e}", file=sys.stderr)

    async def session_limit_retry_loop(self) -> None:
        """Background task: periodically check for queued messages and retry."""
        state = self.state
        while True:
            await asyncio.sleep(60)
            if not state.session_queue_enabled:
                continue
            try:
                pending = await self._fetch_due_queue_items()
                if not pending:
                    continue

                for item in pending:
                    session_name = item.get("session_name", "")
                    host = await asyncio.to_thread(
                        self.find_session_host, session_name
                    )
                    if not host:
                        await self._mark_queue_item_failed(
                            item["id"], "Session no longer exists"
                        )
                        thread_id = item.get("thread_id") or item.get("channel_id", "")
                        if thread_id:
                            reply_kw = {}
                            # For Slack, thread_id is a thread_ts
                            if item.get("platform") == "slack":
                                reply_kw["thread_ts"] = thread_id
                                ch = item.get("channel_id", thread_id)
                            else:
                                ch = thread_id
                            await self.platform.post_message(
                                ch,
                                f"Retry failed for `{session_name}`: "
                                f"session no longer exists. Removed from queue.",
                                **reply_kw,
                            )
                        continue

                    # Capture pre-content
                    pre_content = await asyncio.to_thread(
                        self.capture_pane_content, host, session_name
                    )

                    # Retry sending
                    sent = await asyncio.to_thread(
                        self.send_to_session, host, session_name,
                        item.get("user_message", ""),
                    )
                    if not sent:
                        await self._mark_queue_item_failed(
                            item["id"], "Failed to send to session"
                        )
                        thread_id = item.get("thread_id") or item.get("channel_id", "")
                        if thread_id:
                            reply_kw = {}
                            if item.get("platform") == "slack":
                                reply_kw["thread_ts"] = thread_id
                                ch = item.get("channel_id", thread_id)
                            else:
                                ch = thread_id
                            await self.platform.post_message(
                                ch,
                                f"Retry failed for `{session_name}`: "
                                f"could not send to session.",
                                **reply_kw,
                            )
                        continue

                    # Check if still rate limited
                    await asyncio.sleep(state.session_queue_detect_delay)
                    post_content = await asyncio.to_thread(
                        self.capture_pane_content, host, session_name
                    )
                    error_line = (
                        detect_session_limit(pre_content, post_content)
                        if post_content else None
                    )

                    thread_id = item.get("thread_id") or item.get("channel_id", "")
                    reply_kw = {}
                    if item.get("platform") == "slack":
                        reply_kw["thread_ts"] = thread_id
                        ch = item.get("channel_id", thread_id)
                    else:
                        ch = thread_id

                    if error_line:
                        # Still rate limited
                        retry_count = item.get("retry_count", 0) + 1
                        max_retries = item.get(
                            "max_retries", state.session_queue_max_retries
                        )
                        if retry_count >= max_retries:
                            await self._mark_queue_item_failed(
                                item["id"],
                                f"Max retries exceeded: {error_line}",
                            )
                            if ch:
                                await self.platform.post_message(
                                    ch,
                                    f"Retry permanently failed for `{session_name}` "
                                    f"after {max_retries} attempts.\n"
                                    f"Last error: `{error_line[:200]}`",
                                    **reply_kw,
                                )
                        else:
                            await self._update_queue_retry(item)
                            print(
                                f"[session-queue] Retry {retry_count}/{max_retries} "
                                f"still rate limited for {session_name}",
                                file=sys.stderr,
                            )
                        break  # Stop draining -- still rate limited
                    else:
                        # Success
                        await self._mark_queue_item_completed(item["id"])
                        if ch:
                            await self.platform.post_message(
                                ch,
                                f"Queued message successfully retried "
                                f"for `{session_name}`.",
                                **reply_kw,
                            )
            except Exception as e:
                print(f"[session-queue] Retry loop error: {e}", file=sys.stderr)

    # -- message relay (shared forwarding logic) ----------------------------

    async def relay_message(
        self,
        channel_id: str,
        session_name: str,
        user_message: str,
        user_name: str,
        source_id: str = "",
        **reply_kwargs,
    ) -> None:
        """Relay a user message to a multiplexer session.

        Handles: session lookup, shortcut keys, forwarding, output capture,
        session-limit detection, and dashboard events.
        """
        platform = self.platform
        state = self.state

        host = await asyncio.to_thread(self.find_session_host, session_name)
        if not host:
            await platform.post_message(
                channel_id,
                f"Session `{session_name}` not found on any host.\n"
                f"Available hosts: {', '.join(state.ssh_hosts)}",
                **reply_kwargs,
            )
            return

        # Handle shortcut commands (e.g., !c -> Ctrl+C)
        shortcut_key = _SHORTCUTS.get(user_message.lower())
        if shortcut_key:
            sent = await asyncio.to_thread(
                self.send_keys_raw, host, session_name, shortcut_key
            )
            if sent:
                await platform.post_message(
                    channel_id,
                    f"Sent `{shortcut_key}` to `{session_name}`",
                    **reply_kwargs,
                )
            else:
                await platform.post_message(
                    channel_id,
                    f"Failed to send `{shortcut_key}` to `{session_name}`",
                    **reply_kwargs,
                )
            return

        await platform.post_message(
            channel_id,
            f"Forwarding to `{session_name}` on `{host}`...",
            **reply_kwargs,
        )

        # Emit dashboard event: user message relayed to session
        self._fire_dashboard_event({
            "type": "message.relayed",
            "session_name": session_name,
            "platform": platform.platform_name,
            "content": user_message,
            "role": "user",
            "source_id": source_id,
            "source_author": user_name,
            "timestamp": self._now_iso(),
        })

        # Capture pane BEFORE sending -- critical for catching fast command output
        pre_content = await asyncio.to_thread(
            self.capture_pane_content, host, session_name
        )

        sent = await asyncio.to_thread(
            self.send_to_session, host, session_name, user_message
        )
        if sent:
            self._track_task(
                self.capture_and_post_output(
                    channel_id, host, session_name, pre_content, **reply_kwargs
                )
            )
            # Session limit detection (runs in background)
            if state.session_queue_enabled:
                self._track_task(
                    self.detect_session_limit_after_send(
                        channel_id, host, session_name,
                        pre_content, user_message,
                        user_name=user_name,
                        source_msg_id=source_id,
                        **reply_kwargs,
                    )
                )
        else:
            await platform.post_message(
                channel_id,
                f"Failed to send to `{session_name}` on `{host}`. "
                f"The session may have exited.",
                **reply_kwargs,
            )

    # -- command handlers ---------------------------------------------------

    async def cmd_new(
        self, reply_channel: str, raw_args: str, **reply_kwargs
    ) -> None:
        """!new <name> [host|dir] [dir] [-- cmd] -- create session + thread."""
        platform = self.platform
        state = self.state

        if not raw_args:
            await platform.post_message(
                reply_channel,
                "Usage: `!new <name> [host|dir] [dir] [-- command]`\n"
                f"Available hosts: `{'`, `'.join(state.ssh_hosts)}`",
                **reply_kwargs,
            )
            return

        # Split off shell command after --
        shell_cmd: str | None = None
        if " -- " in raw_args:
            args_part, shell_cmd = raw_args.split(" -- ", 1)
            shell_cmd = shell_cmd.strip()
        else:
            args_part = raw_args

        parts = args_part.split()
        if not parts:
            await platform.post_message(
                reply_channel,
                "Usage: `!new <name> [host|dir] [dir] [-- command]`\n"
                f"Available hosts: `{'`, `'.join(state.ssh_hosts)}`",
                **reply_kwargs,
            )
            return

        session_name = parts[0]

        # Parse host and working_dir
        host = state.default_host
        working_dir = state.default_working_dir or None
        rest = parts[1:]
        if rest:
            if rest[0].startswith("/") or rest[0].startswith("~"):
                working_dir = rest[0]
            else:
                host = rest[0]
                if len(rest) > 1:
                    working_dir = rest[1]

        if not self.is_valid_session_name(session_name):
            await platform.post_message(
                reply_channel,
                "Invalid session name. Use only `a-z A-Z 0-9 _ -` (max 64 chars).",
                **reply_kwargs,
            )
            return

        if working_dir and not self._validate_path(working_dir):
            await platform.post_message(
                reply_channel,
                "Invalid working directory. Path contains disallowed characters.",
                **reply_kwargs,
            )
            return

        if host not in state.ssh_hosts:
            await platform.post_message(
                reply_channel,
                f"Unknown host `{host}`. Available: `{'`, `'.join(state.ssh_hosts)}`",
                **reply_kwargs,
            )
            return

        # Check if session already exists
        existing = await asyncio.to_thread(self.find_session_host, session_name)
        if existing:
            await platform.post_message(
                reply_channel,
                f"Session `{session_name}` already exists on `{existing}`.",
                **reply_kwargs,
            )
            return

        # Create multiplexer session
        mux = state.mux
        safe_name = shlex.quote(session_name)
        # Expand leading ~ to $HOME before quoting (tilde won't expand inside quotes)
        if working_dir and working_dir.startswith("~/"):
            safe_dir = '"$HOME"/' + shlex.quote(working_dir[2:])
        elif working_dir and working_dir == "~":
            safe_dir = '"$HOME"'
        else:
            safe_dir = shlex.quote(working_dir) if working_dir else None
        create_cmd = mux.new_session_cmd(safe_name, safe_dir)
        rc, _ = await asyncio.to_thread(self.run_ssh, host, create_cmd)
        if rc != 0:
            await platform.post_message(
                reply_channel,
                f"Failed to create {mux.name} session `{session_name}` on `{host}`.",
                **reply_kwargs,
            )
            return

        # Set marker so thread-sync.sh skips this session
        if mux.supports_environment:
            marker_cmd = mux.set_environment_cmd(
                safe_name, "AILY_BRIDGE_MANAGED", "1"
            )
            await asyncio.to_thread(self.run_ssh, host, marker_cmd)

        # Launch shell command or agent in session
        agent_label = ""
        if shell_cmd:
            await asyncio.sleep(0.5)
            launched = await asyncio.to_thread(
                self.send_to_session, host, session_name, shell_cmd
            )
            if launched:
                agent_label = f" | cmd: `{shell_cmd}`"
            else:
                agent_label = f" | failed to run `{shell_cmd}`"
        else:
            agent_cmd = self._build_agent_command(
                state.new_session_agent, state.claude_remote_control
            )
            if agent_cmd:
                await asyncio.sleep(0.5)
                launched = await asyncio.to_thread(
                    self.send_to_session, host, session_name, agent_cmd
                )
                if launched:
                    agent_label = f" | agent: `{agent_cmd}`"
                else:
                    agent_label = f" | failed to launch `{agent_cmd}`"

        # Create platform thread
        cwd_label = f" in `{working_dir}`" if working_dir else ""
        thread_name = self.format_thread_name(session_name, host)
        thread_id = await platform.ensure_thread(
            thread_name,
            f"tmux session: **{thread_name}** (`{host}`{cwd_label})",
        )

        if thread_id:
            await platform.post_message(
                thread_id,
                f"Session `{session_name}` created on `{host}`{cwd_label}.{agent_label}",
            )
            await platform.post_message(
                reply_channel,
                f"Created `{session_name}` on `{host}`{cwd_label} + thread{agent_label}",
                **reply_kwargs,
            )
        else:
            await platform.post_message(
                reply_channel,
                f"Created tmux `{session_name}` on `{host}`{cwd_label} "
                f"but failed to create thread.{agent_label}",
                **reply_kwargs,
            )

    async def cmd_kill(
        self, reply_channel: str, parts: list[str], **reply_kwargs
    ) -> None:
        """!kill <session_name> -- kill session + cleanup thread."""
        platform = self.platform
        state = self.state

        if len(parts) < 2:
            await platform.post_message(
                reply_channel, "Usage: `!kill <session_name>`", **reply_kwargs
            )
            return

        session_name = parts[1]

        if not self.is_valid_session_name(session_name):
            await platform.post_message(
                reply_channel,
                "Invalid session name. Use only `a-z A-Z 0-9 _ -` (max 64 chars).",
                **reply_kwargs,
            )
            return

        # Kill multiplexer session
        mux = state.mux
        host = await asyncio.to_thread(self.find_session_host, session_name)
        session_killed = False
        if host:
            safe_name = shlex.quote(session_name)
            rc, _ = await asyncio.to_thread(
                self.run_ssh, host, mux.kill_session_cmd(safe_name)
            )
            session_killed = rc == 0

        # Clean up platform thread
        thread_name = self.format_thread_name(
            session_name, host or state.default_host
        )
        thread_id = await platform.find_thread(thread_name)
        thread_cleaned = False
        cleanup_action = "archived"
        if thread_id:
            if state.thread_cleanup == "delete":
                await platform.delete_thread(thread_id)
                thread_cleaned = True
                cleanup_action = "deleted"
            else:
                await platform.post_message(
                    thread_id,
                    f"Session `{session_name}` killed. Archiving thread.",
                )
                await platform.archive_thread(thread_id)
                thread_cleaned = True
                cleanup_action = "archived"

        # Report
        status: list[str] = []
        if session_killed:
            status.append(f"Killed `{session_name}` on `{host}`")
        elif host:
            status.append(f"Failed to kill `{session_name}` on `{host}`")
        else:
            status.append(f"`{session_name}` not found")
        if thread_cleaned:
            status.append(f"{cleanup_action} thread")
        else:
            status.append("no thread found")

        await platform.post_message(
            reply_channel, " / ".join(status), **reply_kwargs
        )

        # Emit dashboard event
        self._fire_dashboard_event({
            "type": "session.killed",
            "session_name": session_name,
            "platform": platform.platform_name,
            "host": host or "",
            "session_killed": session_killed,
            "thread_cleanup": cleanup_action if thread_cleaned else "none",
            "timestamp": self._now_iso(),
        })

    async def cmd_sessions(self, reply_channel: str, **reply_kwargs) -> None:
        """!sessions -- list all sessions with thread sync status."""
        platform = self.platform
        state = self.state
        mux = state.mux

        # Gather sessions from all hosts
        all_sessions: dict[str, str] = {}
        for host in state.ssh_hosts:
            rc, out = await asyncio.to_thread(
                self.run_ssh,
                host,
                f"{mux.list_sessions_cmd()} 2>/dev/null || true",
            )
            if rc == 0 and out:
                for name in out.strip().split("\n"):
                    name = name.strip()
                    if name and name not in _INFRA_SESSIONS:
                        if name in all_sessions:
                            all_sessions[name] += f", {host}"
                        else:
                            all_sessions[name] = host

        # Gather active threads from the platform
        active_threads = await platform.get_active_thread_sessions()

        if not all_sessions and not active_threads:
            await platform.post_message(
                reply_channel, "No sessions found.", **reply_kwargs
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

        await platform.post_message(
            reply_channel, "\n".join(lines), **reply_kwargs
        )

    async def cmd_queue(
        self, reply_channel: str, parts: list[str], **reply_kwargs
    ) -> None:
        """!queue [add <session> <command> | execute] -- manage deferred command queue."""
        platform = self.platform
        subcmd = parts[1].lower() if len(parts) > 1 else ""

        if subcmd == "add":
            if len(parts) < 4:
                await platform.post_message(
                    reply_channel,
                    "Usage: `!queue add <session_name> <command>`",
                    **reply_kwargs,
                )
                return
            session_name = parts[2]
            if not self.is_valid_session_name(session_name):
                await platform.post_message(
                    reply_channel,
                    "Invalid session name. Use only `a-z A-Z 0-9 _ -` (max 64 chars).",
                    **reply_kwargs,
                )
                return
            command = " ".join(parts[3:])
            result = await self.dashboard_api("POST", "/api/usage/queue", {
                "session_name": session_name,
                "command": command,
            })
            if result:
                cmd_id = result.get("command", {}).get("id", "?")
                await platform.post_message(
                    reply_channel,
                    f"Queued command #{cmd_id} for `{session_name}`: `{command}`",
                    **reply_kwargs,
                )
            else:
                await platform.post_message(
                    reply_channel,
                    "Failed to queue command. Is the usage monitor enabled?",
                    **reply_kwargs,
                )

        elif subcmd == "execute":
            result = await self.dashboard_api("POST", "/api/usage/queue/execute")
            if result:
                count = result.get("executed", 0)
                await platform.post_message(
                    reply_channel,
                    f"Executed {count} pending command(s).",
                    **reply_kwargs,
                )
            else:
                await platform.post_message(
                    reply_channel,
                    "Failed to execute queue. Is the usage monitor enabled?",
                    **reply_kwargs,
                )

        else:
            # List pending commands
            result = await self.dashboard_api(
                "GET", "/api/usage/queue?status=pending"
            )
            if not result:
                await platform.post_message(
                    reply_channel,
                    "Dashboard unavailable or usage monitor not enabled.",
                    **reply_kwargs,
                )
                return

            commands = result.get("commands", [])
            total = result.get("total", 0)
            if total == 0:
                await platform.post_message(
                    reply_channel,
                    "No pending commands in queue.",
                    **reply_kwargs,
                )
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
            await platform.post_message(
                reply_channel, "\n".join(lines), **reply_kwargs
            )

    async def cmd_limit_queue(
        self, reply_channel: str, parts: list[str], **reply_kwargs
    ) -> None:
        """!lq [clear|retry|status] -- manage session limit retry queue."""
        platform = self.platform
        platform_name = platform.platform_name
        subcmd = parts[1].lower() if len(parts) > 1 else ""

        if subcmd == "clear":
            result = await self.dashboard_api(
                "GET",
                f"/api/session-queue?status=pending&platform={platform_name}",
            )
            if not result:
                await platform.post_message(
                    reply_channel, "Dashboard unavailable.", **reply_kwargs
                )
                return
            items = result.get("items", [])
            if not items:
                await platform.post_message(
                    reply_channel,
                    "No pending items in limit queue.",
                    **reply_kwargs,
                )
                return
            cleared = 0
            for item in items:
                await self.dashboard_api(
                    "DELETE", f"/api/session-queue/{item['id']}"
                )
                cleared += 1
            await platform.post_message(
                reply_channel,
                f"Cleared {cleared} pending item(s) from limit queue.",
                **reply_kwargs,
            )

        elif subcmd == "retry":
            result = await self.dashboard_api(
                "GET",
                f"/api/session-queue?status=pending&platform={platform_name}",
            )
            if not result:
                await platform.post_message(
                    reply_channel, "Dashboard unavailable.", **reply_kwargs
                )
                return
            items = result.get("items", [])
            if not items:
                await platform.post_message(
                    reply_channel,
                    "No pending items to retry.",
                    **reply_kwargs,
                )
                return
            now = self._now_iso()
            for item in items:
                await self.dashboard_api(
                    "PATCH", f"/api/session-queue/{item['id']}",
                    {"next_retry_at": now},
                )
            await platform.post_message(
                reply_channel,
                f"Forced immediate retry for {len(items)} item(s). "
                f"Will process within 60s.",
                **reply_kwargs,
            )

        elif subcmd == "status":
            result = await self.dashboard_api("GET", "/api/session-queue/stats")
            if not result:
                await platform.post_message(
                    reply_channel, "Dashboard unavailable.", **reply_kwargs
                )
                return
            stats = result.get("stats", {})
            total = result.get("total", 0)
            lines = [f"**Session Limit Queue** (total: {total})"]
            for status_name, count in sorted(stats.items()):
                lines.append(f"  {status_name}: {count}")

            pending_result = await self.dashboard_api(
                "GET",
                f"/api/session-queue?status=pending&platform={platform_name}&limit=5",
            )
            if pending_result:
                pending_items = pending_result.get("items", [])
                if pending_items:
                    lines.append("\n**Pending items:**")
                    for item in pending_items:
                        lines.append(
                            f"  #{item.get('id')} `{item.get('session_name')}` "
                            f"retry {item.get('retry_count')}/{item.get('max_retries')} "
                            f"next: {item.get('next_retry_at', '?')[:19]}"
                        )
            await platform.post_message(
                reply_channel, "\n".join(lines), **reply_kwargs
            )

        else:
            # Default: list pending items
            result = await self.dashboard_api(
                "GET",
                f"/api/session-queue?status=pending&platform={platform_name}",
            )
            if not result:
                await platform.post_message(
                    reply_channel,
                    "Dashboard unavailable or session queue not enabled.",
                    **reply_kwargs,
                )
                return

            items = result.get("items", [])
            total = result.get("total", 0)
            if total == 0:
                await platform.post_message(
                    reply_channel,
                    "No pending items in session limit queue.",
                    **reply_kwargs,
                )
                return

            lines = [f"**Session Limit Queue** ({total} pending)", "```"]
            lines.append(f"  {'ID':<6} {'SESSION':<20} {'RETRY':<10} NEXT RETRY")
            for item in items[:15]:
                retry = f"{item.get('retry_count', 0)}/{item.get('max_retries', '?')}"
                next_at = item.get('next_retry_at', '?')[:19]
                lines.append(
                    f"  {item.get('id', '?'):<6} {item.get('session_name', '?'):<20} "
                    f"{retry:<10} {next_at}"
                )
            if total > 15:
                lines.append(f"  ... and {total - 15} more")
            lines.append("```")
            lines.append(
                "Use `!lq clear` to cancel all, `!lq retry` to force "
                "immediate retry, `!lq status` for details."
            )
            await platform.post_message(
                reply_channel, "\n".join(lines), **reply_kwargs
            )

    async def handle_command(
        self, reply_channel: str, text: str, **reply_kwargs
    ) -> None:
        """Dispatch ! commands to the appropriate handler."""
        platform = self.platform
        parts = text.split(None, 3)
        cmd = parts[0].lower() if parts else ""

        if cmd == "!new":
            raw_after_cmd = text[len("!new"):].strip()
            await self.cmd_new(reply_channel, raw_after_cmd, **reply_kwargs)
        elif cmd == "!kill":
            await self.cmd_kill(reply_channel, parts, **reply_kwargs)
        elif cmd in ("!sessions", "!ls"):
            await self.cmd_sessions(reply_channel, **reply_kwargs)
        elif cmd == "!queue":
            await self.cmd_queue(reply_channel, parts, **reply_kwargs)
        elif cmd in ("!lq", "!limit-queue"):
            await self.cmd_limit_queue(reply_channel, parts, **reply_kwargs)
        else:
            await platform.post_message(
                reply_channel,
                "Unknown command. Available: `!new <name> [host|dir] [dir] [-- cmd]`,"
                " `!kill <name>`, `!sessions`, `!queue`, `!lq`",
                **reply_kwargs,
            )

    # -- config loader ------------------------------------------------------

    @staticmethod
    def load_common_config(env: dict[str, str]) -> BridgeState:
        """Parse all shared config from an env dict and return a BridgeState."""
        from multiplexer import get_backend

        # SSH hosts
        hosts_str = env.get("SSH_HOSTS", "")
        if hosts_str:
            ssh_hosts = [h.strip() for h in hosts_str.split(",") if h.strip()]
        else:
            ssh_hosts = ["localhost"]
        default_host = ssh_hosts[0] if ssh_hosts else ""

        # Default working directory
        default_working_dir = env.get("DEFAULT_WORKING_DIR", "")

        # Thread cleanup mode
        thread_cleanup = env.get("THREAD_CLEANUP", "archive").lower()
        if thread_cleanup not in ("archive", "delete"):
            thread_cleanup = "archive"

        # Thread name format
        thread_name_format = env.get(
            "THREAD_NAME_FORMAT", "[agent] {session} - {host}"
        )

        # Agent auto-launch
        new_session_agent = env.get("NEW_SESSION_AGENT", "").lower().strip()
        claude_remote_control = (
            env.get("CLAUDE_REMOTE_CONTROL", "false").lower() == "true"
        )

        # Dashboard
        dashboard_url = os.environ.get("AILY_DASHBOARD_URL", "") or env.get(
            "AILY_DASHBOARD_URL", ""
        )
        dashboard_auth_token = os.environ.get("AILY_AUTH_TOKEN", "") or env.get(
            "AILY_AUTH_TOKEN", ""
        )

        # Multiplexer backend
        mux_type = env.get("AILY_MULTIPLEXER", "") or None
        mux = get_backend(mux_type)

        # Session limit queue config
        session_queue_enabled = (
            os.getenv(
                "AILY_SESSION_QUEUE_ENABLED",
                env.get("AILY_SESSION_QUEUE_ENABLED", "true"),
            ).lower() == "true"
        )
        session_queue_retry_interval = int(os.getenv(
            "AILY_SESSION_QUEUE_RETRY_INTERVAL",
            env.get("AILY_SESSION_QUEUE_RETRY_INTERVAL", "1800"),
        ))
        session_queue_max_retries = int(os.getenv(
            "AILY_SESSION_QUEUE_MAX_RETRIES",
            env.get("AILY_SESSION_QUEUE_MAX_RETRIES", "12"),
        ))
        session_queue_detect_delay = int(os.getenv(
            "AILY_SESSION_QUEUE_DETECT_DELAY",
            env.get("AILY_SESSION_QUEUE_DETECT_DELAY", "15"),
        ))

        return BridgeState(
            mux=mux,
            ssh_hosts=ssh_hosts,
            default_host=default_host,
            default_working_dir=default_working_dir,
            thread_name_format=thread_name_format,
            thread_cleanup=thread_cleanup,
            new_session_agent=new_session_agent,
            claude_remote_control=claude_remote_control,
            dashboard_url=dashboard_url,
            dashboard_auth_token=dashboard_auth_token,
            session_queue_enabled=session_queue_enabled,
            session_queue_retry_interval=session_queue_retry_interval,
            session_queue_max_retries=session_queue_max_retries,
            session_queue_detect_delay=session_queue_detect_delay,
        )


# Module-level convenience aliases for backward compatibility
load_env = BridgeCore.load_env
SHORTCUTS = _SHORTCUTS

# Default thread name format (used by standalone parse_thread_name)
_DEFAULT_THREAD_FORMAT = "[agent] {session} - {host}"


def parse_thread_name(thread_name: str, fmt: str = _DEFAULT_THREAD_FORMAT) -> str | None:
    """Standalone thread name parser (for use outside BridgeCore context).

    Extracts session name from thread_name using the given format template.
    Falls back to legacy AGENT_PREFIX stripping.
    """
    escaped = re.escape(fmt)
    escaped = escaped.replace(re.escape("{session}"), r"([a-zA-Z0-9_-]+)")
    escaped = escaped.replace(re.escape("{host}"), r".+")
    m = re.match(f"^{escaped}$", thread_name)
    if m:
        return m.group(1)
    if thread_name.startswith(AGENT_PREFIX):
        return thread_name[len(AGENT_PREFIX):]
    return None
