"""Background worker: polls tmux sessions across SSH hosts and syncs DB state.

Runs every 30 seconds (configurable). For each host:
1. Lists all tmux sessions via SSH
2. Inserts newly discovered sessions into the database
3. Marks sessions that disappeared as 'closed'
4. Skips infrastructure sessions (agent-bridge, slack-bridge)
5. Syncs platform thread IDs for new sessions
6. Publishes events to EventBus for WebSocket clients
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from dashboard import db
from dashboard.services.event_bus import Event, EventBus
from dashboard.services.platform_service import PlatformService
from dashboard.services.session_service import SessionService

logger = logging.getLogger(__name__)

DEFAULT_POLL_INTERVAL = 30
_POLLER_CONCURRENCY = 5


async def session_poller(
    session_svc: SessionService,
    platform_svc: PlatformService,
    event_bus: EventBus,
    interval: int = DEFAULT_POLL_INTERVAL,
) -> None:
    """Main polling loop. Runs indefinitely.

    Args:
        session_svc: Session service for SSH operations.
        platform_svc: Platform service for thread ID sync.
        event_bus: EventBus for publishing real-time events.
        interval: Seconds between poll cycles.
    """
    logger.info("Session poller started (interval=%ds)", interval)
    consecutive_errors = 0
    cleanup_counter = 0
    MAX_BACKOFF = 300  # cap at 5 minutes
    while True:
        try:
            cleanup_counter = await _poll_once(
                session_svc, platform_svc, event_bus, cleanup_counter
            )
            consecutive_errors = 0
        except Exception:
            consecutive_errors += 1
            backoff = min(interval * (2 ** consecutive_errors), MAX_BACKOFF)
            logger.exception(
                "Session poller error (attempt %d, next retry in %ds)",
                consecutive_errors, backoff,
            )
            await asyncio.sleep(backoff)
            continue
        await asyncio.sleep(interval)


async def _process_new_session(
    sem: asyncio.Semaphore,
    name: str,
    host: str,
    now: str,
    session_svc: SessionService,
    platform_svc: PlatformService,
    event_bus: EventBus,
) -> None:
    """Process a single newly discovered session (called concurrently)."""
    async with sem:
        # Insert new session (short, standalone write).
        await db.execute(
            """INSERT OR IGNORE INTO sessions
               (name, host, status, created_at, updated_at)
               VALUES (?, ?, 'active', ?, ?)""",
            (name, host, now, now),
        )

        logger.info("Discovered new session: %s on %s", name, host)

        # Slow I/O (Discord/Slack HTTP, SSH up to 15s) is done OUTSIDE any DB
        # transaction. A db.batch() holds the write lock for its whole body, so
        # doing network/SSH inside one stalls every dashboard write for its
        # duration.
        discord_thread_id = None
        slack_thread_ts = None
        try:
            thread_ids = await platform_svc.sync_thread_ids(name)
            discord_thread_id = thread_ids.get("discord_thread_id")
            slack_thread_ts = thread_ids.get("slack_thread_ts")
        except Exception:
            logger.exception("Failed to sync thread IDs for session '%s'", name)

        cwd = None
        try:
            cwd = await session_svc.get_session_cwd(host, name)
        except Exception:
            logger.debug("Failed to get working directory for session '%s'", name, exc_info=True)

        # Apply the fetched values and record the event in one short transaction.
        session_row = None
        async with db.batch():
            if discord_thread_id:
                await db.execute(
                    "UPDATE sessions SET discord_thread_id = ? WHERE name = ?",
                    (discord_thread_id, name),
                )
            if slack_thread_ts:
                await db.execute(
                    "UPDATE sessions SET slack_thread_ts = ? WHERE name = ?",
                    (slack_thread_ts, name),
                )
            if cwd:
                await db.execute(
                    "UPDATE sessions SET working_dir = ? WHERE name = ?",
                    (cwd, name),
                )

            session_row = await db.fetchone(
                "SELECT * FROM sessions WHERE name = ?", (name,)
            )
            if session_row:
                await db.execute(
                    """INSERT INTO events (event_type, session_name, payload, created_at)
                       VALUES (?, ?, ?, ?)""",
                    ("session.created", name, json.dumps({"host": host}), now),
                )

        # Publish after the transaction commits.
        if session_row:
            await event_bus.publish(Event.session_created(dict(session_row)))


async def _poll_once(
    session_svc: SessionService,
    platform_svc: PlatformService,
    event_bus: EventBus,
    cleanup_counter: int = 0,
) -> int:
    """Execute one poll cycle.

    Queries all SSH hosts for tmux sessions, then reconciles against the DB.
    """
    # Get live sessions from all hosts
    host_sessions = await session_svc.list_all_sessions()

    # Flatten to {name: host} mapping
    live: dict[str, str] = {}
    for host, names in host_sessions.items():
        for name in names:
            live[name] = host

    # Include closed rows so a rediscovered session can be reactivated.
    db_sessions_rows = await db.fetchall("SELECT * FROM sessions")
    db_sessions: dict[str, dict[str, Any]] = {
        row["name"]: row for row in db_sessions_rows
    }

    now = db.now_iso()

    # --- New sessions: in tmux but not in DB (parallel with semaphore) ---
    new_sessions = {name: host for name, host in live.items() if name not in db_sessions}
    if new_sessions:
        sem = asyncio.Semaphore(_POLLER_CONCURRENCY)
        tasks = [
            _process_new_session(sem, name, host, now, session_svc, platform_svc, event_bus)
            for name, host in new_sessions.items()
        ]
        await asyncio.gather(*tasks, return_exceptions=True)

    # --- Existing sessions: update status ---
    for name, session in db_sessions.items():
        if name in live:
            host = live[name]
            updates: list[str] = []
            params: list[Any] = []

            # Session is alive — ensure it's marked active
            if session["status"] in ("closed", "idle", "orphan", "unreachable"):
                updates.append("status = 'active'")
                if session["status"] == "closed":
                    updates.append("closed_at = NULL")

            # Update host if it moved
            if session["host"] != host:
                updates.append("host = ?")
                params.append(host)

            # Always update the updated_at timestamp
            updates.append("updated_at = ?")
            params.append(now)

            if updates:
                params.append(name)
                await db.execute(
                    f"UPDATE sessions SET {', '.join(updates)} WHERE name = ?",
                    tuple(params),
                )

                # Publish update if status or host changed
                if session["status"] != "active" or session["host"] != host:
                    updated_row = await db.fetchone(
                        "SELECT * FROM sessions WHERE name = ?", (name,)
                    )
                    if updated_row:
                        await event_bus.publish(
                            Event.session_updated(dict(updated_row))
                        )
        else:
            if session["status"] == "closed":
                continue
            if session["host"] not in host_sessions:
                if session["status"] != "unreachable":
                    await db.execute(
                        "UPDATE sessions SET status = 'unreachable', updated_at = ? WHERE name = ?",
                        (now, name),
                    )
                    updated_row = await db.fetchone(
                        "SELECT * FROM sessions WHERE name = ?", (name,)
                    )
                    if updated_row:
                        await event_bus.publish(
                            Event.session_status_changed(
                                dict(updated_row), session["status"], "unreachable"
                            )
                        )
                continue
            # Session gone from tmux — mark as closed
            if session["status"] != "closed":
                await db.execute(
                    """UPDATE sessions
                       SET status = 'closed', closed_at = ?, updated_at = ?
                       WHERE name = ?""",
                    (now, now, name),
                )

                logger.info(
                    "Session gone: %s (was on %s)", name, session["host"]
                )

                closed_row = await db.fetchone(
                    "SELECT * FROM sessions WHERE name = ?", (name,)
                )
                if closed_row:
                    await event_bus.publish(
                        Event.session_closed(dict(closed_row))
                    )
                    await db.execute(
                        """INSERT INTO events
                           (event_type, session_name, payload, created_at)
                           VALUES (?, ?, ?, ?)""",
                        (
                            "session.closed",
                            name,
                            json.dumps({"host": session["host"]}),
                            now,
                        ),
                    )

    # Periodic cleanup of old events
    cleanup_counter += 1
    if cleanup_counter >= 100:
        cleanup_counter = 0
        try:
            deleted = await db.cleanup_old_events(30)
            if deleted:
                logger.info("Cleaned up %d old events", deleted)
        except Exception:
            logger.exception("Failed to clean up old events")

    return cleanup_counter
