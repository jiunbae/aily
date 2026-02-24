"""Anthropic and OpenAI API usage monitoring and command queue management.

Polls provider APIs to capture rate limit headers, detects resets,
and manages a deferred command queue that executes when limits recover.
"""

from __future__ import annotations

import logging
from typing import Any

import aiohttp

from dashboard import db
from dashboard.services.event_bus import Event, EventBus
from dashboard.services.session_service import SessionService

logger = logging.getLogger(__name__)

# Anthropic rate limit headers → snapshot field names
ANTHROPIC_HEADERS = {
    "anthropic-ratelimit-requests-limit": "requests_limit",
    "anthropic-ratelimit-requests-remaining": "requests_remaining",
    "anthropic-ratelimit-requests-reset": "requests_reset",
    "anthropic-ratelimit-input-tokens-limit": "input_tokens_limit",
    "anthropic-ratelimit-input-tokens-remaining": "input_tokens_remaining",
    "anthropic-ratelimit-input-tokens-reset": "input_tokens_reset",
    "anthropic-ratelimit-output-tokens-limit": "output_tokens_limit",
    "anthropic-ratelimit-output-tokens-remaining": "output_tokens_remaining",
    "anthropic-ratelimit-output-tokens-reset": "output_tokens_reset",
    "anthropic-ratelimit-tokens-limit": "tokens_limit",
    "anthropic-ratelimit-tokens-remaining": "tokens_remaining",
    "anthropic-ratelimit-tokens-reset": "tokens_reset",
}

# OpenAI rate limit headers → snapshot field names
OPENAI_HEADERS = {
    "x-ratelimit-limit-requests": "requests_limit",
    "x-ratelimit-remaining-requests": "requests_remaining",
    "x-ratelimit-reset-requests": "requests_reset",
    "x-ratelimit-limit-tokens": "tokens_limit",
    "x-ratelimit-remaining-tokens": "tokens_remaining",
    "x-ratelimit-reset-tokens": "tokens_reset",
}

ANTHROPIC_API_BASE = "https://api.anthropic.com"
OPENAI_API_BASE = "https://api.openai.com"
ANTHROPIC_VERSION = "2023-06-01"

# All snapshot columns for DB insert
SNAPSHOT_COLUMNS = [
    "provider", "polled_at", "poll_model", "poll_status_code", "error_message",
    "requests_limit", "requests_remaining", "requests_reset",
    "input_tokens_limit", "input_tokens_remaining", "input_tokens_reset",
    "output_tokens_limit", "output_tokens_remaining", "output_tokens_reset",
    "tokens_limit", "tokens_remaining", "tokens_reset",
]

# Limit types to check for resets
LIMIT_TYPES = ("requests", "input_tokens", "output_tokens", "tokens")


class UsageService:
    """Manages multi-provider API usage monitoring and command queue."""

    def __init__(
        self,
        anthropic_api_key: str = "",
        openai_api_key: str = "",
        event_bus: EventBus | None = None,
        session_svc: SessionService | None = None,
        poll_model_anthropic: str = "claude-haiku-4-5-20251001",
        poll_model_openai: str = "gpt-4o-mini",
        enable_command_queue: bool = False,
        retention_hours: int = 168,
    ) -> None:
        self.anthropic_api_key = anthropic_api_key
        self.openai_api_key = openai_api_key
        self.event_bus = event_bus
        self.session_svc = session_svc
        self.poll_model_anthropic = poll_model_anthropic
        self.poll_model_openai = poll_model_openai
        self.enable_command_queue = enable_command_queue
        self.retention_hours = retention_hours
        self._http: aiohttp.ClientSession | None = None

    @property
    def providers(self) -> list[str]:
        """Return list of configured providers."""
        result: list[str] = []
        if self.anthropic_api_key:
            result.append("anthropic")
        if self.openai_api_key:
            result.append("openai")
        return result

    async def _get_http(self) -> aiohttp.ClientSession:
        if self._http is None or self._http.closed:
            self._http = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=15)
            )
        return self._http

    async def close(self) -> None:
        if self._http and not self._http.closed:
            await self._http.close()

    # --- Polling ---

    async def poll_provider(self, provider: str) -> dict[str, Any]:
        """Make a minimal API call and extract rate limit headers.

        Returns a snapshot dict with all parsed rate limit fields.
        """
        http = await self._get_http()
        now = db.now_iso()
        snapshot: dict[str, Any] = {"provider": provider, "polled_at": now}

        try:
            if provider == "anthropic":
                snapshot.update(await self._poll_anthropic(http))
            elif provider == "openai":
                snapshot.update(await self._poll_openai(http))
            else:
                snapshot["error_message"] = f"Unknown provider: {provider}"
                snapshot["poll_status_code"] = 0
        except Exception as e:
            snapshot["poll_status_code"] = 0
            snapshot["error_message"] = str(e)[:500]
            logger.exception("Usage poll failed for %s", provider)

        return snapshot

    async def _poll_anthropic(self, http: aiohttp.ClientSession) -> dict[str, Any]:
        """Poll Anthropic via /v1/messages/count_tokens."""
        result: dict[str, Any] = {"poll_model": self.poll_model_anthropic}

        async with http.post(
            f"{ANTHROPIC_API_BASE}/v1/messages/count_tokens",
            json={
                "model": self.poll_model_anthropic,
                "messages": [{"role": "user", "content": "hi"}],
            },
            headers={
                "x-api-key": self.anthropic_api_key,
                "anthropic-version": ANTHROPIC_VERSION,
                "content-type": "application/json",
            },
        ) as resp:
            result["poll_status_code"] = resp.status
            result.update(self._parse_headers(resp.headers, ANTHROPIC_HEADERS))

            if resp.status not in (200, 429):
                body = await resp.text()
                result["error_message"] = f"HTTP {resp.status}: {body[:200]}"

        return result

    async def _poll_openai(self, http: aiohttp.ClientSession) -> dict[str, Any]:
        """Poll OpenAI via /v1/chat/completions with max_tokens=1."""
        result: dict[str, Any] = {"poll_model": self.poll_model_openai}

        async with http.post(
            f"{OPENAI_API_BASE}/v1/chat/completions",
            json={
                "model": self.poll_model_openai,
                "messages": [{"role": "user", "content": "hi"}],
                "max_tokens": 1,
            },
            headers={
                "Authorization": f"Bearer {self.openai_api_key}",
                "Content-Type": "application/json",
            },
        ) as resp:
            result["poll_status_code"] = resp.status
            result.update(self._parse_headers(resp.headers, OPENAI_HEADERS))

            if resp.status not in (200, 429):
                body = await resp.text()
                result["error_message"] = f"HTTP {resp.status}: {body[:200]}"

        return result

    @staticmethod
    def _parse_headers(
        headers: Any, header_map: dict[str, str]
    ) -> dict[str, Any]:
        """Extract rate limit values from response headers."""
        result: dict[str, Any] = {}
        for header_name, field_name in header_map.items():
            value = headers.get(header_name)
            if value is None:
                continue
            if field_name.endswith("_reset"):
                result[field_name] = value
            else:
                try:
                    result[field_name] = int(value)
                except ValueError:
                    result[field_name] = value
        return result

    # --- Snapshot persistence ---

    async def save_snapshot(self, snapshot: dict[str, Any]) -> int:
        data = {col: snapshot.get(col) for col in SNAPSHOT_COLUMNS}
        cursor = await db.execute(
            f"""INSERT INTO usage_snapshots
                ({', '.join(SNAPSHOT_COLUMNS)})
                VALUES ({', '.join('?' for _ in SNAPSHOT_COLUMNS)})""",
            tuple(data.values()),
        )
        return cursor.lastrowid or 0

    async def get_previous_snapshot(self, provider: str) -> dict[str, Any] | None:
        return await db.fetchone(
            """SELECT * FROM usage_snapshots
               WHERE provider = ? AND poll_status_code IN (200, 429)
               ORDER BY polled_at DESC LIMIT 1""",
            (provider,),
        )

    # --- Reset / limit detection ---

    @staticmethod
    def detect_reset(
        current: dict[str, Any], previous: dict[str, Any] | None
    ) -> list[str]:
        """Compare snapshots — return limit types where remaining increased."""
        if not previous:
            return []
        resets: list[str] = []
        for limit_type in LIMIT_TYPES:
            key = f"{limit_type}_remaining"
            curr_val = current.get(key)
            prev_val = previous.get(key) if previous else None
            if curr_val is not None and prev_val is not None:
                if curr_val > prev_val:
                    resets.append(limit_type)
        return resets

    @staticmethod
    def detect_limit_reached(snapshot: dict[str, Any]) -> list[str]:
        """Return limit types that hit zero remaining."""
        at_limit: list[str] = []
        for limit_type in LIMIT_TYPES:
            remaining = snapshot.get(f"{limit_type}_remaining")
            if remaining is not None and remaining <= 0:
                at_limit.append(limit_type)
        return at_limit

    async def cleanup_old_snapshots(self) -> int:
        cursor = await db.execute(
            """DELETE FROM usage_snapshots
               WHERE polled_at < datetime('now', ?)""",
            (f"-{self.retention_hours} hours",),
        )
        return cursor.rowcount or 0

    # --- Command Queue ---

    async def enqueue_command(
        self, session_name: str, host: str, command: str, priority: int = 0
    ) -> dict[str, Any]:
        now = db.now_iso()
        cursor = await db.execute(
            """INSERT INTO command_queue
               (session_name, host, command, status, priority, created_at, updated_at)
               VALUES (?, ?, ?, 'pending', ?, ?, ?)""",
            (session_name, host, command, priority, now, now),
        )
        row_id = cursor.lastrowid
        entry = await db.fetchone(
            "SELECT * FROM command_queue WHERE id = ?", (row_id,)
        )
        entry_dict = dict(entry) if entry else {"id": row_id}

        if self.event_bus:
            await self.event_bus.publish(Event.command_queued(entry_dict))
        return entry_dict

    async def get_pending_commands(self, limit: int = 50) -> list[dict[str, Any]]:
        return await db.fetchall(
            """SELECT * FROM command_queue
               WHERE status = 'pending'
               ORDER BY priority DESC, created_at ASC
               LIMIT ?""",
            (limit,),
        )

    async def execute_pending_commands(self) -> list[dict[str, Any]]:
        """Execute all pending commands via SessionService."""
        if not self.session_svc:
            logger.warning("Cannot execute commands: no SessionService")
            return []

        commands = await self.get_pending_commands()
        results: list[dict[str, Any]] = []

        for cmd in commands:
            cmd_id = cmd["id"]
            now = db.now_iso()

            await db.execute(
                "UPDATE command_queue SET status = 'executing', updated_at = ? WHERE id = ?",
                (now, cmd_id),
            )

            try:
                success = await self.session_svc.send_to_session(
                    cmd["host"], cmd["session_name"], cmd["command"]
                )
                if success:
                    await db.execute(
                        """UPDATE command_queue
                           SET status = 'completed', executed_at = ?, updated_at = ?
                           WHERE id = ?""",
                        (now, now, cmd_id),
                    )
                    result = {**cmd, "status": "completed", "executed_at": now}
                    if self.event_bus:
                        await self.event_bus.publish(Event.command_executed(result))
                else:
                    await db.execute(
                        """UPDATE command_queue
                           SET status = 'failed', error = 'send_to_session returned false', updated_at = ?
                           WHERE id = ?""",
                        (now, cmd_id),
                    )
                    result = {**cmd, "status": "failed", "error": "send failed"}
                    if self.event_bus:
                        await self.event_bus.publish(Event.command_failed(result))
            except Exception as e:
                await db.execute(
                    """UPDATE command_queue
                       SET status = 'failed', error = ?, updated_at = ?
                       WHERE id = ?""",
                    (str(e)[:500], now, cmd_id),
                )
                result = {**cmd, "status": "failed", "error": str(e)[:200]}
                if self.event_bus:
                    await self.event_bus.publish(Event.command_failed(result))
                logger.exception("Command %d failed", cmd_id)

            results.append(result)

        return results

    async def cancel_command(self, cmd_id: int) -> bool:
        row = await db.fetchone(
            "SELECT status FROM command_queue WHERE id = ?", (cmd_id,)
        )
        if not row or row["status"] != "pending":
            return False
        await db.execute(
            "UPDATE command_queue SET status = 'cancelled', updated_at = ? WHERE id = ?",
            (db.now_iso(), cmd_id),
        )
        return True

    async def get_queue_stats(self) -> dict[str, int]:
        rows = await db.fetchall(
            "SELECT status, COUNT(*) as cnt FROM command_queue GROUP BY status"
        )
        return {row["status"]: row["cnt"] for row in rows}
