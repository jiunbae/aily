"""Background worker: polls provider APIs for rate limit status.

Runs every 60 seconds (configurable). Each cycle:
1. Makes a minimal API call per provider to capture rate limit headers
2. Saves snapshot to DB
3. Detects limit reached / reset conditions
4. On reset: auto-executes pending commands if command queue is enabled
5. Publishes events to EventBus
6. Periodically cleans up old snapshots
"""

from __future__ import annotations

import asyncio
import logging

from dashboard.services.event_bus import Event
from dashboard.services.usage_service import UsageService

logger = logging.getLogger(__name__)

DEFAULT_POLL_INTERVAL = 60
CLEANUP_EVERY_N_POLLS = 60  # hourly at 60s interval


async def usage_poller(
    usage_svc: UsageService,
    interval: int = DEFAULT_POLL_INTERVAL,
) -> None:
    """Main usage polling loop. Runs indefinitely."""
    providers = usage_svc.providers
    logger.info(
        "Usage poller started (interval=%ds, providers=%s)", interval, providers
    )
    poll_count = 0

    while True:
        try:
            await _poll_once(usage_svc, providers)
            poll_count += 1

            if poll_count % CLEANUP_EVERY_N_POLLS == 0:
                deleted = await usage_svc.cleanup_old_snapshots()
                if deleted:
                    logger.info("Cleaned up %d old usage snapshots", deleted)
        except asyncio.CancelledError:
            logger.info("Usage poller stopped")
            return
        except Exception:
            logger.exception("Usage poller error")

        await asyncio.sleep(interval)


async def _poll_once(usage_svc: UsageService, providers: list[str]) -> None:
    """Execute one poll cycle for all configured providers."""
    any_reset = False

    for provider in providers:
        previous = await usage_svc.get_previous_snapshot(provider)
        snapshot = await usage_svc.poll_provider(provider)
        await usage_svc.save_snapshot(snapshot)

        # Skip event logic if poll failed completely
        if snapshot.get("poll_status_code") == 0:
            continue

        # Publish updated snapshot
        if usage_svc.event_bus:
            await usage_svc.event_bus.publish(
                Event.usage_updated(provider, snapshot)
            )

        # Detect limits reached
        at_limit = usage_svc.detect_limit_reached(snapshot)
        for limit_type in at_limit:
            if usage_svc.event_bus:
                await usage_svc.event_bus.publish(
                    Event.usage_limit_reached(provider, limit_type, snapshot)
                )
            logger.warning("Rate limit reached: %s/%s", provider, limit_type)

        # Detect resets
        resets = usage_svc.detect_reset(snapshot, previous)
        for limit_type in resets:
            if usage_svc.event_bus:
                await usage_svc.event_bus.publish(
                    Event.usage_reset(provider, limit_type, snapshot)
                )
            logger.info("Rate limit reset detected: %s/%s", provider, limit_type)

        if resets:
            any_reset = True

    # On any reset, execute pending commands if command queue is enabled
    if any_reset and usage_svc.enable_command_queue:
        pending = await usage_svc.get_pending_commands()
        if pending:
            logger.info(
                "Reset detected, executing %d pending commands", len(pending)
            )
            await usage_svc.execute_pending_commands()
