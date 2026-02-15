"""Discord/Slack thread status sync.

Uses the same API patterns as agent-bridge.py and slack-bridge.py
to find and manage platform threads for tmux sessions.
"""

from __future__ import annotations

import logging
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)

AGENT_PREFIX = "[agent] "


class PlatformService:
    """Syncs platform thread state with dashboard sessions.

    Uses Discord REST API and Slack Web API to:
    - Find threads matching session names
    - Archive threads when sessions are killed
    - Sync thread IDs for newly discovered sessions
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

        thread_name = f"{AGENT_PREFIX}{session_name}"
        headers = {"Authorization": f"Bot {self.discord_token}"}
        api = "https://discord.com/api/v10"

        try:
            async with aiohttp.ClientSession() as http:
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
                                if (
                                    t.get("name") == thread_name
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
                            if t.get("name") == thread_name:
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

        thread_name = f"{AGENT_PREFIX}{session_name}"
        headers = {"Authorization": f"Bearer {self.slack_token}"}

        try:
            async with aiohttp.ClientSession() as http:
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
                                if (
                                    first_line == thread_name
                                    or text.startswith(thread_name)
                                ):
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
                async with aiohttp.ClientSession() as http:
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
                async with aiohttp.ClientSession() as http:
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
            async with aiohttp.ClientSession() as http:
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
