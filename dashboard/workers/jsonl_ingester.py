"""Background worker: scans active sessions for JSONL data and ingests it.

Runs every 60 seconds (configurable). For each active session with a known
working directory:
1. Discovers the Claude Code JSONL file path on the SSH host
2. Reads the tail of the file
3. Parses new lines and ingests messages
4. Tracks offset to avoid re-processing
"""

from __future__ import annotations

import asyncio
import logging

from dashboard import db
from dashboard.services.jsonl_service import JSONLService

logger = logging.getLogger(__name__)

DEFAULT_SCAN_INTERVAL = 60


async def jsonl_ingester(
    jsonl_svc: JSONLService,
    interval: int = DEFAULT_SCAN_INTERVAL,
) -> None:
    """Main JSONL ingestion loop. Runs indefinitely.

    Args:
        jsonl_svc: JSONLService instance.
        interval: Seconds between scan cycles.
    """
    logger.info("JSONL ingester started (interval=%ds)", interval)

    # Wait for session poller to populate data
    await asyncio.sleep(30)

    while True:
        try:
            await _scan_once(jsonl_svc)
        except Exception:
            logger.exception("JSONL ingester error")
        await asyncio.sleep(interval)


async def _scan_once(jsonl_svc: JSONLService) -> None:
    """Scan all active sessions with working directories for JSONL data."""
    sessions = await db.fetchall(
        """SELECT name, host, working_dir FROM sessions
           WHERE status = 'active'
             AND host IS NOT NULL
             AND working_dir IS NOT NULL
             AND working_dir != ''"""
    )

    if not sessions:
        return

    total_ingested = 0
    for session in sessions:
        try:
            ingested = await jsonl_svc.ingest_for_session(
                host=session["host"],
                session_name=session["name"],
                working_dir=session["working_dir"],
            )
            total_ingested += ingested
        except Exception:
            logger.exception(
                "Failed to ingest JSONL for session '%s'", session["name"]
            )

        # Rate limit SSH commands
        await asyncio.sleep(2)

    if total_ingested > 0:
        logger.info("JSONL scan complete: %d new messages", total_ingested)
