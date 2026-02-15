"""Background worker: syncs Discord/Slack thread messages into the messages table.

Runs every 5 minutes (configurable). For each session with a discord_thread_id:
1. Gets the last ingested message source_id for that session+source
2. Fetches new messages from Discord API after that point
3. Ingests them via MessageService (with dedup)
"""

from __future__ import annotations

import asyncio
import logging

from dashboard import db
from dashboard.services.event_bus import EventBus
from dashboard.services.message_service import MessageService
from dashboard.services.platform_service import PlatformService

logger = logging.getLogger(__name__)

DEFAULT_SYNC_INTERVAL = 300  # 5 minutes


async def message_sync_worker(
    platform_svc: PlatformService,
    message_svc: MessageService,
    event_bus: EventBus,
    interval: int = DEFAULT_SYNC_INTERVAL,
) -> None:
    """Main sync loop. Runs indefinitely.

    Args:
        platform_svc: Platform service for fetching messages.
        message_svc: Message service for ingestion.
        event_bus: EventBus for publishing events.
        interval: Seconds between sync cycles.
    """
    logger.info("Message sync worker started (interval=%ds)", interval)

    # Initial sync: wait a bit for session poller to populate sessions first
    await asyncio.sleep(15)

    while True:
        try:
            await _sync_once(platform_svc, message_svc, event_bus)
        except Exception:
            logger.exception("Message sync error")
        await asyncio.sleep(interval)


async def _sync_once(
    platform_svc: PlatformService,
    message_svc: MessageService,
    event_bus: EventBus,
) -> None:
    """Execute one sync cycle across all sessions with platform threads."""
    total_ingested = 0

    # --- Discord sync ---
    if platform_svc.has_discord:
        # Get bot user ID for role detection
        bot_user_id = await _get_discord_bot_user_id(platform_svc)

        # Find all active sessions with discord threads
        sessions = await db.fetchall(
            """SELECT name, discord_thread_id FROM sessions
               WHERE discord_thread_id IS NOT NULL
                 AND discord_thread_id != ''
                 AND status = 'active'"""
        )

        for session in sessions:
            session_name = session["name"]
            thread_id = session["discord_thread_id"]

            try:
                # Get the last message source_id we already have
                last_msg = await db.fetchone(
                    """SELECT source_id FROM messages
                       WHERE session_name = ? AND source = 'discord'
                       ORDER BY timestamp DESC LIMIT 1""",
                    (session_name,),
                )
                after = last_msg["source_id"] if last_msg else None

                # Fetch new messages from Discord
                messages = await platform_svc.fetch_all_discord_thread_messages(
                    thread_id, after=after
                )

                if messages:
                    ingested = await message_svc.ingest_discord_messages(
                        session_name, messages, bot_user_id=bot_user_id
                    )
                    total_ingested += ingested

            except Exception:
                logger.exception(
                    "Failed to sync Discord messages for session '%s'",
                    session_name,
                )

            # Small delay between sessions to avoid rate limits
            await asyncio.sleep(1)

    # --- Slack sync ---
    if platform_svc.has_slack:
        slack_bot_user_id = await platform_svc.get_slack_bot_user_id()
        slack_sessions = await db.fetchall(
            """SELECT name, slack_thread_ts, slack_channel_id FROM sessions
               WHERE slack_thread_ts IS NOT NULL
                 AND slack_thread_ts != ''
                 AND status = 'active'"""
        )

        for session in slack_sessions:
            session_name = session["name"]
            thread_ts = session["slack_thread_ts"]
            channel_id = session["slack_channel_id"] or platform_svc.slack_channel_id

            try:
                # Get last known Slack message ts
                last_msg = await db.fetchone(
                    """SELECT source_id FROM messages
                       WHERE session_name = ? AND source = 'slack'
                       ORDER BY timestamp DESC LIMIT 1""",
                    (session_name,),
                )
                after_ts = last_msg["source_id"] if last_msg else None

                messages = await platform_svc.fetch_all_slack_thread_messages(
                    channel_id, thread_ts, after_ts=after_ts
                )
                if messages:
                    ingested = await message_svc.ingest_slack_messages(
                        session_name, messages, bot_user_id=slack_bot_user_id
                    )
                    total_ingested += ingested
            except Exception:
                logger.exception(
                    "Failed to sync Slack messages for session '%s'",
                    session_name,
                )

            await asyncio.sleep(1)

    if total_ingested > 0:
        logger.info("Message sync complete: %d new messages", total_ingested)


async def _get_discord_bot_user_id(platform_svc: PlatformService) -> str:
    """Get the bot's own user ID from Discord API."""
    import aiohttp

    if not platform_svc.has_discord:
        return ""

    try:
        headers = {"Authorization": f"Bot {platform_svc.discord_token}"}
        async with aiohttp.ClientSession() as http:
            async with http.get(
                "https://discord.com/api/v10/users/@me",
                headers=headers,
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("id", "")
    except Exception:
        logger.exception("Failed to get Discord bot user ID")

    return ""
