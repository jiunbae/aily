"""In-process pub/sub for real-time WebSocket event distribution.

Each WebSocket connection registers an asyncio.Queue as a subscriber.
When an event is published, it is placed into every subscriber's queue.
If a queue is full (slow consumer), the event is dropped for that subscriber.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Event:
    """An event to be distributed via the EventBus."""

    type: str
    payload: dict[str, Any]
    timestamp: float = field(default_factory=time.time)

    def to_json(self) -> str:
        """Serialize event to JSON string for WebSocket transmission."""
        return json.dumps(
            {
                "type": self.type,
                "payload": self.payload,
                "timestamp": self.timestamp,
            }
        )

    @classmethod
    def session_created(cls, session_data: dict[str, Any]) -> Event:
        return cls(type="session.created", payload=session_data)

    @classmethod
    def session_updated(cls, session_data: dict[str, Any]) -> Event:
        return cls(type="session.updated", payload=session_data)

    @classmethod
    def session_closed(cls, session_data: dict[str, Any]) -> Event:
        return cls(type="session.closed", payload=session_data)

    @classmethod
    def message_new(cls, message_data: dict[str, Any]) -> Event:
        return cls(type="message.new", payload=message_data)

    @classmethod
    def session_status_changed(
        cls, session_data: dict[str, Any], old_status: str, new_status: str
    ) -> Event:
        """Session status transition with before/after state."""
        return cls(
            type="session.status_changed",
            payload={
                **session_data,
                "old_status": old_status,
                "new_status": new_status,
            },
        )

    @classmethod
    def typing_start(cls, session_name: str) -> Event:
        """Agent is producing output in this session."""
        return cls(
            type="typing.start",
            payload={"session_name": session_name},
        )

    @classmethod
    def typing_stop(cls, session_name: str) -> Event:
        """Agent stopped producing output."""
        return cls(
            type="typing.stop",
            payload={"session_name": session_name},
        )

    @classmethod
    def sync_complete(
        cls, session_name: str, source: str, count: int
    ) -> Event:
        """A sync operation completed for a session."""
        return cls(
            type="sync.complete",
            payload={
                "session_name": session_name,
                "source": source,
                "new_messages": count,
            },
        )

    @classmethod
    def heartbeat(cls) -> Event:
        return cls(type="heartbeat", payload={})

    @classmethod
    def usage_updated(cls, provider: str, snapshot: dict[str, Any]) -> Event:
        return cls(type="usage.updated", payload={"provider": provider, **snapshot})

    @classmethod
    def usage_limit_reached(
        cls, provider: str, limit_type: str, snapshot: dict[str, Any]
    ) -> Event:
        return cls(
            type="usage.limit_reached",
            payload={"provider": provider, "limit_type": limit_type, **snapshot},
        )

    @classmethod
    def usage_reset(
        cls, provider: str, limit_type: str, snapshot: dict[str, Any]
    ) -> Event:
        return cls(
            type="usage.reset",
            payload={"provider": provider, "limit_type": limit_type, **snapshot},
        )

    @classmethod
    def command_queued(cls, command_data: dict[str, Any]) -> Event:
        return cls(type="command.queued", payload=command_data)

    @classmethod
    def command_executed(cls, command_data: dict[str, Any]) -> Event:
        return cls(type="command.executed", payload=command_data)

    @classmethod
    def command_failed(cls, command_data: dict[str, Any]) -> Event:
        return cls(type="command.failed", payload=command_data)


class EventBus:
    """asyncio.Queue-based subscriber management.

    Thread-safe via asyncio.Lock. Each subscriber gets its own queue
    with a configurable max size. Slow consumers that let their queue
    fill up will have events dropped (QueueFull protection).
    """

    def __init__(self) -> None:
        self._subscribers: dict[int, asyncio.Queue[Event]] = {}
        self._counter = itertools.count()
        self._lock = asyncio.Lock()

    async def subscribe(self, queue: asyncio.Queue[Event]) -> int:
        """Register a subscriber queue and return its unique ID.

        Args:
            queue: The asyncio.Queue to receive events.

        Returns:
            A unique subscriber ID for use with unsubscribe().
        """
        async with self._lock:
            sub_id = next(self._counter)
            self._subscribers[sub_id] = queue
            logger.debug("Subscriber %d registered (total: %d)",
                         sub_id, len(self._subscribers))
            return sub_id

    async def unsubscribe(self, subscriber_id: int) -> None:
        """Remove a subscriber by ID.

        Args:
            subscriber_id: The ID returned by subscribe().
        """
        async with self._lock:
            self._subscribers.pop(subscriber_id, None)
            logger.debug("Subscriber %d removed (total: %d)",
                         subscriber_id, len(self._subscribers))

    async def publish(self, event: Event) -> int:
        """Publish an event to all subscribers.

        Events are placed into each subscriber's queue. If a queue is full,
        the event is dropped for that subscriber (slow consumer protection).

        Args:
            event: The event to publish.

        Returns:
            Number of subscribers that received the event.
        """
        async with self._lock:
            subscribers = dict(self._subscribers)

        delivered = 0
        for sub_id, queue in subscribers.items():
            try:
                queue.put_nowait(event)
                delivered += 1
            except asyncio.QueueFull:
                logger.warning(
                    "EventBus: dropping event for slow subscriber %d", sub_id
                )
        return delivered

    @property
    def subscriber_count(self) -> int:
        """Return the current number of subscribers."""
        return len(self._subscribers)
