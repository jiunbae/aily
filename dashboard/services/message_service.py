"""Message ingestion service.

Handles ingestion from bridge webhook events (Phase 1).
JSONL ingestion is Phase 2 — the ingest_bridge_event method is the
primary entry point for Phase 1.

Deduplication uses SHA-256 hashing: INSERT OR IGNORE on the dedup_hash
unique index handles all dedup.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from typing import Any

from dashboard import db
from dashboard.services.event_bus import Event, EventBus

logger = logging.getLogger(__name__)


def compute_dedup_hash(
    session_name: str, source: str, source_id: str | None, content: str
) -> str:
    """Compute a deterministic hash for message deduplication.

    Same algorithm as specified in the merged plan:
    - If source_id is available, hash source:source_id (globally unique)
    - Otherwise, hash session_name:source:content[:200] (content-based)

    Args:
        session_name: The tmux session name.
        source: Message source (discord, slack, jsonl, hook, tmux).
        source_id: Platform-specific message ID, if available.
        content: Message content text.

    Returns:
        SHA-256 hex digest.
    """
    if source_id:
        key = f"{source}:{source_id}"
    else:
        key = f"{session_name}:{source}:{content[:200]}"
    return hashlib.sha256(key.encode()).hexdigest()


class MessageService:
    """Handles message ingestion and deduplication."""

    def __init__(self, event_bus: EventBus) -> None:
        self.event_bus = event_bus

    async def ingest_bridge_event(self, event_data: dict[str, Any]) -> None:
        """Ingest an event from a bridge webhook.

        Expected payload (from POST /api/hooks/event):
        {
            "type": "message.relayed",
            "session_name": "my-session",
            "platform": "discord",
            "content": "...",
            "role": "user",
            "source_id": "123456789",
            "source_author": "jiun",
            "timestamp": "2026-02-13T10:30:00Z"
        }

        Args:
            event_data: The parsed JSON body from the bridge webhook.
        """
        session_name = event_data.get("session_name", "").strip()
        if not session_name:
            logger.warning("Bridge event missing session_name, ignoring")
            return

        # Check session exists in DB
        session = await db.fetchone(
            "SELECT name FROM sessions WHERE name = ?", (session_name,)
        )
        if not session:
            logger.debug(
                "Bridge event for unknown session '%s', ignoring", session_name
            )
            return

        content = event_data.get("content", "").strip()
        if not content:
            return

        # Determine source and role
        platform = event_data.get("platform", "hook")
        source = platform if platform in ("discord", "slack", "tmux") else "hook"
        role = event_data.get("role", "user")
        if role not in ("user", "assistant", "system"):
            role = "user"

        source_id = event_data.get("source_id") or event_data.get("external_id")
        source_author = event_data.get("source_author", "")

        # Parse timestamp
        ts_str = event_data.get("timestamp")
        try:
            timestamp = (
                datetime.fromisoformat(ts_str).isoformat()
                if ts_str
                else db.now_iso()
            )
        except (ValueError, TypeError):
            timestamp = db.now_iso()

        # Compute dedup hash
        dedup_hash = compute_dedup_hash(session_name, source, source_id, content)

        # INSERT OR IGNORE — dedup_hash unique index handles duplicates
        cursor = await db.insert_or_ignore(
            "messages",
            {
                "session_name": session_name,
                "role": role,
                "content": content,
                "source": source,
                "source_id": source_id,
                "source_author": source_author,
                "timestamp": timestamp,
                "ingested_at": db.now_iso(),
                "dedup_hash": dedup_hash,
            },
        )

        if cursor.rowcount and cursor.rowcount > 0:
            logger.info(
                "Ingested bridge message for '%s' from %s",
                session_name,
                source,
            )
            # Publish event for WebSocket clients
            await self.event_bus.publish(
                Event.message_new(
                    {
                        "session_name": session_name,
                        "role": role,
                        "content": content[:200],
                        "source": source,
                        "timestamp": timestamp,
                    }
                )
            )

        # Also store as an event for the activity feed
        await db.insert_or_ignore(
            "events",
            {
                "event_type": event_data.get("type", "bridge.event"),
                "session_name": session_name,
                "payload": str(event_data),
                "created_at": db.now_iso(),
            },
        )
