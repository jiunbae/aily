"""WebSocket handler for real-time event streaming.

GET /ws upgrades to a WebSocket connection. Events from the EventBus
are fanned out to all connected clients. Supports session filtering
via subscribe messages, heartbeat keepalives, message history fetch,
and typing indicators.

Protocol:
  Server -> Client:
    {"type": "session.created", "payload": {...}, "timestamp": ...}
    {"type": "session.updated", "payload": {...}, "timestamp": ...}
    {"type": "session.closed", "payload": {...}, "timestamp": ...}
    {"type": "session.status_changed", "payload": {...}, "timestamp": ...}
    {"type": "message.new", "payload": {...}, "timestamp": ...}
    {"type": "typing.start", "payload": {...}, "timestamp": ...}
    {"type": "typing.stop", "payload": {...}, "timestamp": ...}
    {"type": "typing.user", "payload": {...}, "timestamp": ...}
    {"type": "sync.complete", "payload": {...}, "timestamp": ...}
    {"type": "history", "payload": {...}}
    {"type": "heartbeat", "payload": {}, "timestamp": ...}

  Client -> Server:
    {"type": "subscribe", "sessions": ["fix-auth"]}   // filter events
    {"type": "ping"}                                    // keepalive
    {"type": "fetch_history", "session": "...", "limit": 50, "offset": 0}
    {"type": "typing", "session": "..."}               // user typing
"""

from __future__ import annotations

import asyncio
import json
import logging

import aiohttp
from aiohttp import web

from dashboard.services.event_bus import Event, EventBus

logger = logging.getLogger(__name__)

HEARTBEAT_INTERVAL = 30.0
MAX_QUEUE_SIZE = 256


async def websocket_handler(request: web.Request) -> web.WebSocketResponse:
    """GET /ws - WebSocket upgrade handler.

    1. Subscribes to the EventBus.
    2. Sends initial heartbeat on connect.
    3. Runs two concurrent tasks:
       - send_events: drains the queue and sends events to the client
       - receive_messages: reads client messages (subscribe, ping)
    4. Heartbeat every 30s to keep the connection alive.
    """
    ws = web.WebSocketResponse(heartbeat=HEARTBEAT_INTERVAL)
    await ws.prepare(request)
    ws_clients: set[web.WebSocketResponse] = request.app.setdefault(
        "ws_clients", set()
    )
    ws_clients.add(ws)

    event_bus: EventBus = request.app["event_bus"]
    queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=MAX_QUEUE_SIZE)

    # Session filter — if non-empty, only events for these sessions are sent
    subscribed_sessions: set[str] = set()

    subscriber_id = await event_bus.subscribe(queue)
    logger.info(
        "WebSocket client connected (subscriber=%d, total=%d)",
        subscriber_id,
        event_bus.subscriber_count,
    )

    # Send initial heartbeat
    try:
        await ws.send_str(Event.heartbeat().to_json())
    except Exception:
        await event_bus.unsubscribe(subscriber_id)
        return ws

    try:

        async def send_events() -> None:
            """Drain the event queue and send to WebSocket client."""
            while not ws.closed:
                try:
                    event = await asyncio.wait_for(
                        queue.get(), timeout=HEARTBEAT_INTERVAL
                    )
                except asyncio.TimeoutError:
                    # Send heartbeat on timeout
                    if not ws.closed:
                        try:
                            await ws.send_str(Event.heartbeat().to_json())
                        except Exception:
                            break
                    continue

                # Apply session filter if active
                if subscribed_sessions:
                    session_name = event.payload.get(
                        "name"
                    ) or event.payload.get("session_name")
                    if session_name and session_name not in subscribed_sessions:
                        continue

                # Send event to client
                if not ws.closed:
                    try:
                        await ws.send_str(event.to_json())
                    except Exception:
                        break

        send_task = asyncio.create_task(send_events())

        # Read client messages
        async for msg in ws:
            if msg.type == aiohttp.WSMsgType.TEXT:
                try:
                    data = json.loads(msg.data)
                    msg_type = data.get("type")

                    if msg_type == "ping":
                        await ws.send_json({"type": "pong"})
                    elif msg_type == "subscribe":
                        # Update session filter
                        sessions = data.get("sessions", [])
                        subscribed_sessions.clear()
                        if isinstance(sessions, list):
                            subscribed_sessions.update(sessions)
                        logger.debug(
                            "Subscriber %d filtering: %s",
                            subscriber_id,
                            subscribed_sessions or "all",
                        )
                    elif msg_type == "fetch_history":
                        # Fetch message history and send back over WS
                        session_name = data.get("session", "")
                        limit = min(int(data.get("limit", 50)), 200)
                        offset = max(int(data.get("offset", 0)), 0)

                        if session_name:
                            from dashboard import db as _db

                            messages = await _db.fetchall(
                                """SELECT * FROM messages
                                   WHERE session_name = ?
                                   ORDER BY timestamp ASC
                                   LIMIT ? OFFSET ?""",
                                (session_name, limit, offset),
                            )
                            count_row = await _db.fetchone(
                                """SELECT COUNT(*) as cnt FROM messages
                                   WHERE session_name = ?""",
                                (session_name,),
                            )
                            total = count_row["cnt"] if count_row else 0

                            response = {
                                "type": "history",
                                "payload": {
                                    "session": session_name,
                                    "messages": messages,
                                    "total": total,
                                    "limit": limit,
                                    "offset": offset,
                                },
                            }
                            if not ws.closed:
                                await ws.send_json(response)
                    elif msg_type == "typing":
                        # Relay typing indicator to other subscribers
                        session_name = data.get("session", "")
                        if session_name:
                            await event_bus.publish(
                                Event(
                                    type="typing.user",
                                    payload={"session_name": session_name},
                                )
                            )
                except (json.JSONDecodeError, Exception):
                    pass  # Ignore malformed messages

            elif msg.type in (
                aiohttp.WSMsgType.ERROR,
                aiohttp.WSMsgType.CLOSED,
            ):
                break

        send_task.cancel()
        try:
            await send_task
        except asyncio.CancelledError:
            pass

    finally:
        ws_clients.discard(ws)
        await event_bus.unsubscribe(subscriber_id)
        logger.info(
            "WebSocket client disconnected (subscriber=%d, total=%d)",
            subscriber_id,
            event_bus.subscriber_count,
        )

    return ws
