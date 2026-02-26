"""Discord/Slack thread status sync.

Uses the same API patterns as agent-bridge.py and slack-bridge.py
to find and manage platform threads for tmux sessions.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)

AGENT_PREFIX = "[agent] "  # legacy fallback
THREAD_NAME_FORMAT = os.environ.get("THREAD_NAME_FORMAT", "[agent] {session} - {host}")


def parse_thread_name(thread_name: str) -> str | None:
    """Extract session name from a thread name using the format template."""
    fmt = re.escape(THREAD_NAME_FORMAT)
    fmt = fmt.replace(re.escape("{session}"), r"([a-zA-Z0-9_-]+)")
    fmt = fmt.replace(re.escape("{host}"), r".+")
    m = re.match(f"^{fmt}$", thread_name)
    if m:
        return m.group(1)
    if thread_name.startswith(AGENT_PREFIX):
        return thread_name[len(AGENT_PREFIX):]
    return None


class PlatformService:
    """Syncs platform thread state with dashboard sessions.

    Uses Discord REST API and Slack Web API to:
    - Find threads matching session names
    - Archive threads when sessions are killed
    - Sync thread IDs for newly discovered sessions

    Reuses a single aiohttp.ClientSession for all HTTP requests to
    avoid per-request TCP connection overhead (~200ms savings per call).
    """

    def __init__(
        self,
        discord_bot_token: str = "",
        discord_channel_id: str = "",
        slack_bot_token: str = "",
        slack_channel_id: str = "",
    ) -> None:
        self.discord_token = discord_bot_token
        self.discord_channel_id = discord_channel_id
        self.slack_token = slack_bot_token
        self.slack_channel_id = slack_channel_id
        self._http: aiohttp.ClientSession | None = None

    async def _get_http(self) -> aiohttp.ClientSession:
        """Get or create a reusable HTTP session."""
        if self._http is None or self._http.closed:
            self._http = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=30)
            )
        return self._http

    async def close(self) -> None:
        """Close the shared HTTP session. Call on app cleanup."""
        if self._http and not self._http.closed:
            await self._http.close()
            self._http = None

    @property
    def has_discord(self) -> bool:
        """Check if Discord is configured."""
        return bool(self.discord_token and self.discord_channel_id)

    @property
    def has_slack(self) -> bool:
        """Check if Slack is configured."""
        return bool(self.slack_token and self.slack_channel_id)

    async def find_discord_thread(self, session_name: str) -> str | None:
        """Find a Discord thread matching [agent] <session_name>.

        Same search pattern as find_thread() in agent-bridge.py:
        1. Check active threads (guild-level)
        2. Check archived threads (channel-level)

        Args:
            session_name: The tmux session name.

        Returns:
            Discord thread/channel ID, or None.
        """
        if not self.has_discord:
            return None

        headers = {"Authorization": f"Bot {self.discord_token}"}
        api = "https://discord.com/api/v10"

        try:
            http = await self._get_http()

            # Get guild ID from channel
            async with http.get(
                f"{api}/channels/{self.discord_channel_id}",
                headers=headers,
            ) as resp:
                if resp.status != 200:
                    return None
                ch = await resp.json()
                guild_id = ch.get("guild_id")

            # 1. Active threads (guild-level endpoint)
            if guild_id:
                async with http.get(
                    f"{api}/guilds/{guild_id}/threads/active",
                    headers=headers,
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        for t in data.get("threads", []):
                            name = t.get("name", "")
                            if (
                                parse_thread_name(name) == session_name
                                and t.get("parent_id") == self.discord_channel_id
                            ):
                                return t["id"]

            # 2. Archived threads
            async with http.get(
                f"{api}/channels/{self.discord_channel_id}/threads/archived/public",
                headers=headers,
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    for t in data.get("threads", []):
                        if parse_thread_name(t.get("name", "")) == session_name:
                            return t["id"]
        except Exception:
            logger.exception("Error finding Discord thread for '%s'", session_name)

        return None

    async def find_slack_thread(self, session_name: str) -> str | None:
        """Find a Slack thread matching [agent] <session_name>.

        Same search pattern as find_thread_ts() in slack-bridge.py:
        searches channel history for a parent message starting with the
        agent prefix.

        Args:
            session_name: The tmux session name.

        Returns:
            Slack thread timestamp, or None.
        """
        if not self.has_slack:
            return None

        headers = {"Authorization": f"Bearer {self.slack_token}"}

        try:
            http = await self._get_http()
            async with http.get(
                "https://slack.com/api/conversations.history",
                params={"channel": self.slack_channel_id, "limit": "200"},
                headers=headers,
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get("ok"):
                        for msg in data.get("messages", []):
                            text = msg.get("text", "")
                            first_line = text.split("\n")[0].strip()
                            if parse_thread_name(first_line) == session_name:
                                return msg["ts"]
        except Exception:
            logger.exception("Error finding Slack thread for '%s'", session_name)

        return None

    async def sync_thread_ids(
        self, session_name: str
    ) -> dict[str, str | None]:
        """Find thread IDs across all configured platforms for a session.

        Args:
            session_name: The tmux session name.

        Returns:
            Dict with optional keys: discord_thread_id, slack_thread_ts.
        """
        result: dict[str, str | None] = {}
        if self.has_discord:
            result["discord_thread_id"] = await self.find_discord_thread(
                session_name
            )
        if self.has_slack:
            result["slack_thread_ts"] = await self.find_slack_thread(
                session_name
            )
        return result

    async def archive_threads(
        self, session_data: dict[str, Any]
    ) -> list[str]:
        """Archive threads across all platforms for a session.

        Uses the same archive patterns as the bridges:
        - Discord: PATCH channel with {"archived": true}
        - Slack: Post closing message + lock reaction

        Args:
            session_data: Session dict with discord_thread_id, slack_thread_ts, etc.

        Returns:
            List of platform names that were successfully archived.
        """
        archived: list[str] = []

        discord_thread_id = session_data.get("discord_thread_id")
        if self.has_discord and discord_thread_id:
            try:
                headers = {"Authorization": f"Bot {self.discord_token}"}
                http = await self._get_http()
                async with http.patch(
                    f"https://discord.com/api/v10/channels/{discord_thread_id}",
                    headers=headers,
                    json={"archived": True},
                ) as resp:
                    if resp.status < 400:
                        archived.append("discord")
                        logger.info(
                            "Archived Discord thread %s", discord_thread_id
                        )
            except Exception:
                logger.exception("Failed to archive Discord thread")

        slack_thread_ts = session_data.get("slack_thread_ts")
        slack_channel = session_data.get("slack_channel_id") or self.slack_channel_id
        if self.has_slack and slack_thread_ts:
            try:
                headers = {
                    "Authorization": f"Bearer {self.slack_token}",
                    "Content-Type": "application/json",
                }
                http = await self._get_http()
                # Post closing message
                await http.post(
                    "https://slack.com/api/chat.postMessage",
                    headers=headers,
                    json={
                        "channel": slack_channel,
                        "thread_ts": slack_thread_ts,
                        "text": ":lock: Thread archived. Session closed.",
                    },
                )
                # Add lock reaction
                await http.post(
                    "https://slack.com/api/reactions.add",
                    headers=headers,
                    json={
                        "channel": slack_channel,
                        "timestamp": slack_thread_ts,
                        "name": "lock",
                    },
                )
                archived.append("slack")
                logger.info("Archived Slack thread %s", slack_thread_ts)
            except Exception:
                logger.exception("Failed to archive Slack thread")

        return archived

    async def fetch_slack_thread_messages(
        self, channel_id: str, thread_ts: str, limit: int = 200, cursor: str | None = None
    ) -> tuple[list[dict[str, Any]], str | None]:
        """Fetch messages from a Slack thread using conversations.replies.

        Args:
            channel_id: Slack channel ID containing the thread.
            thread_ts: Parent message timestamp identifying the thread.
            limit: Max messages per page (1-200, Slack default 100).
            cursor: Pagination cursor for next page.

        Returns:
            Tuple of (list of Slack message dicts, next_cursor or None).
        """
        if not self.has_slack:
            return [], None

        headers = {"Authorization": f"Bearer {self.slack_token}"}
        params: dict[str, str] = {
            "channel": channel_id,
            "ts": thread_ts,
            "limit": str(min(limit, 200)),
        }
        if cursor:
            params["cursor"] = cursor

        try:
            http = await self._get_http()
            async with http.get(
                "https://slack.com/api/conversations.replies",
                headers=headers,
                params=params,
            ) as resp:
                if resp.status != 200:
                    logger.warning(
                        "Slack replies fetch failed: %d for thread %s",
                        resp.status, thread_ts,
                    )
                    return [], None
                data = await resp.json()
                if not data.get("ok"):
                    logger.warning("Slack API error: %s", data.get("error"))
                    return [], None

                messages = data.get("messages", [])
                # First message is the parent -- skip it
                if messages and messages[0].get("ts") == thread_ts:
                    messages = messages[1:]

                next_cursor = (
                    data.get("response_metadata", {}).get("next_cursor") or None
                )
                return messages, next_cursor
        except Exception:
            logger.exception("Error fetching Slack replies for thread %s", thread_ts)
            return [], None

    async def fetch_all_slack_thread_messages(
        self,
        channel_id: str,
        thread_ts: str,
        after_ts: str | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch ALL replies from a Slack thread using pagination.

        Args:
            channel_id: Slack channel ID.
            thread_ts: Parent message timestamp.
            after_ts: Only return messages with ts > after_ts (for incremental sync).

        Returns:
            List of all Slack message dicts, oldest first (Slack default order).
        """
        import asyncio

        all_messages: list[dict[str, Any]] = []
        cursor: str | None = None

        while True:
            batch, next_cursor = await self.fetch_slack_thread_messages(
                channel_id, thread_ts, limit=200, cursor=cursor
            )
            if not batch:
                break

            # Filter by after_ts if provided
            if after_ts:
                batch = [m for m in batch if m.get("ts", "") > after_ts]

            all_messages.extend(batch)
            cursor = next_cursor

            if not cursor:
                break

            # Rate limit safety
            await asyncio.sleep(1)

        return all_messages

    async def get_slack_bot_user_id(self) -> str:
        """Get the Slack bot's own user ID via auth.test.

        Returns:
            The bot user ID string, or empty string on failure.
        """
        if not self.has_slack:
            return ""

        headers = {"Authorization": f"Bearer {self.slack_token}"}
        try:
            http = await self._get_http()
            async with http.get(
                "https://slack.com/api/auth.test",
                headers=headers,
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get("ok"):
                        return data.get("user_id", "")
        except Exception:
            logger.exception("Failed to get Slack bot user ID")
        return ""

    async def fetch_discord_thread_messages(
        self, thread_id: str, limit: int = 100, after: str | None = None
    ) -> list[dict[str, Any]]:
        """Fetch messages from a Discord thread.

        Args:
            thread_id: Discord thread/channel ID.
            limit: Max messages to fetch (1-100).
            after: Fetch messages after this message ID (for pagination).

        Returns:
            List of Discord message dicts, oldest first.
        """
        if not self.has_discord:
            return []

        headers = {"Authorization": f"Bot {self.discord_token}"}
        api = "https://discord.com/api/v10"
        params: dict[str, str] = {"limit": str(min(limit, 100))}
        if after:
            params["after"] = after

        try:
            http = await self._get_http()
            async with http.get(
                f"{api}/channels/{thread_id}/messages",
                headers=headers,
                params=params,
            ) as resp:
                if resp.status != 200:
                    logger.warning(
                        "Discord messages fetch failed: %d for thread %s",
                        resp.status,
                        thread_id,
                    )
                    return []
                messages = await resp.json()
                # Discord returns newest first, reverse to oldest first
                messages.reverse()
                return messages
        except Exception:
            logger.exception(
                "Error fetching Discord messages for thread %s", thread_id
            )
            return []

    async def fetch_all_discord_thread_messages(
        self, thread_id: str, after: str | None = None
    ) -> list[dict[str, Any]]:
        """Fetch ALL messages from a Discord thread using pagination.

        Args:
            thread_id: Discord thread/channel ID.
            after: Fetch messages after this message ID.

        Returns:
            List of all Discord message dicts, oldest first.
        """
        all_messages: list[dict[str, Any]] = []
        cursor = after

        while True:
            batch = await self.fetch_discord_thread_messages(
                thread_id, limit=100, after=cursor
            )
            if not batch:
                break
            all_messages.extend(batch)
            cursor = batch[-1]["id"]
            # Safety: Discord rate limit friendly
            if len(batch) < 100:
                break

        return all_messages
