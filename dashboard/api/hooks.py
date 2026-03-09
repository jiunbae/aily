"""Claude Code HTTP hook endpoints.

Receives POST events directly from Claude Code's hook system,
replacing the fragile shell pipeline (notify-claude.sh -> extract-last-message.py -> post.sh).

Endpoints:
  POST /api/hooks/stop           - Stop event: extract last message, relay to Discord/Slack
  POST /api/hooks/session        - SessionStart/SessionEnd lifecycle events
  POST /api/hooks/tool-activity  - PostToolUse events for real-time activity feed
"""

from __future__ import annotations

import json
import logging
import re
from collections import deque
from pathlib import Path
from typing import Any

from aiohttp import web

from dashboard import db
from dashboard.api import error_response, json_ok
from dashboard.services.event_bus import Event, EventBus
from dashboard.services.message_service import MessageService
from dashboard.services.platform_service import PlatformService

logger = logging.getLogger(__name__)

_ALLOWED_TRANSCRIPT_DIRS = (
    Path("~/.claude").expanduser().resolve(),
)


def _validate_transcript_path(path: str) -> bool:
    """Validate that a transcript path is safe to read.

    Only allows .jsonl files under known Claude Code directories.
    Uses Path.resolve() to prevent directory traversal attacks.
    """
    try:
        resolved = Path(path).resolve()
    except (ValueError, OSError):
        return False
    if resolved.suffix != ".jsonl":
        return False
    return any(
        resolved.is_relative_to(allowed) for allowed in _ALLOWED_TRANSCRIPT_DIRS
    )


# ---------------------------------------------------------------------------
# JSONL extraction helpers (ported from hooks/extract-last-message.py)
# ---------------------------------------------------------------------------

def _strip_english_coach(text: str) -> str:
    """Remove the English Coach --- > ... --- block from the start."""
    stripped = text.strip()
    if not stripped.startswith("---"):
        return stripped
    parts = stripped.split("---")
    if len(parts) >= 3:
        result = "---".join(parts[2:]).strip()
        return result if result else stripped
    return stripped


def _tables_to_codeblocks(text: str) -> str:
    """Wrap markdown tables in code blocks (Discord doesn't render md tables)."""
    lines = text.split("\n")
    result: list[str] = []
    in_table = False
    for line in lines:
        is_table_line = bool(re.match(r"^\s*\|", line))
        if is_table_line and not in_table:
            in_table = True
            result.append("```")
            result.append(line)
        elif is_table_line and in_table:
            result.append(line)
        elif not is_table_line and in_table:
            in_table = False
            result.append("```")
            result.append(line)
        else:
            result.append(line)
    if in_table:
        result.append("```")
    return "\n".join(result)


def _has_interactive_tool(content: list[Any]) -> bool:
    """Check if content has AskUserQuestion or similar interactive tool calls."""
    for block in content:
        if isinstance(block, dict) and block.get("type") == "tool_use":
            if block.get("name") in ("AskUserQuestion", "EnterPlanMode"):
                return True
    return False


def extract_last_assistant_text(
    jsonl_path: str, max_chars: int = 1000
) -> str | None:
    """Read a Claude Code transcript JSONL and extract the last assistant text.

    Searches backwards through the last 200 lines to find the most recent
    assistant turn with text content. Skips tool-only turns.

    Args:
        jsonl_path: Absolute path to the transcript JSONL file.
        max_chars: Maximum character length before truncation.

    Returns:
        Extracted and formatted text, or None if nothing suitable found.
    """
    if not jsonl_path.endswith(".jsonl"):
        logger.warning("Refusing to read non-JSONL file: %s", jsonl_path)
        return None

    try:
        with open(jsonl_path) as f:
            lines = list(deque(f, maxlen=200))
    except (OSError, IOError) as exc:
        logger.warning("Cannot read transcript %s: %s", jsonl_path, exc)
        return None

    for line in reversed(lines):
        try:
            obj = json.loads(line.strip())
            if obj.get("type") != "assistant":
                continue
            content = obj.get("message", {}).get("content", [])

            # Interactive prompts are handled separately
            if _has_interactive_tool(content):
                return None

            texts: list[str] = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    t = block.get("text", "").strip()
                    if t:
                        texts.append(t)
            if not texts:
                # Tool-only turn -- keep searching for the text turn
                continue

            full_text = "\n".join(texts)
            full_text = _strip_english_coach(full_text)
            full_text = _tables_to_codeblocks(full_text)
            if not full_text:
                return None
            if len(full_text) > max_chars:
                full_text = full_text[:max_chars] + "..."
            return full_text
        except Exception:
            continue
    return None


def _session_name_from_cwd(cwd: str) -> str:
    """Derive a session name from the working directory path.

    Uses the last path component as the session/project name.
    Falls back to 'unknown' if the path is empty or root.
    """
    name = Path(cwd).name if cwd else ""
    return name or "unknown"


# ---------------------------------------------------------------------------
# Route handlers
# ---------------------------------------------------------------------------

async def handle_stop(request: web.Request) -> web.Response:
    """POST /api/hooks/stop

    Handle Claude Code Stop hook event. Extracts the last assistant message
    from the transcript JSONL and relays it to Discord/Slack.
    """
    try:
        body = await request.json()
    except (json.JSONDecodeError, Exception):
        return error_response(400, "INVALID_JSON", "Request body must be JSON")

    session_id = body.get("session_id", "")
    transcript_path = body.get("transcript_path", "")
    cwd = body.get("cwd", "")
    session_name = _session_name_from_cwd(cwd)

    logger.info(
        "Hook stop event: session=%s cwd=%s", session_name, cwd
    )

    if not transcript_path:
        return error_response(
            400, "MISSING_TRANSCRIPT", "transcript_path is required"
        )

    if not _validate_transcript_path(transcript_path):
        return error_response(
            400, "INVALID_PATH", "Invalid transcript path"
        )

    # Extract last assistant message from the JSONL transcript
    text = extract_last_assistant_text(transcript_path)
    if not text:
        logger.debug("No extractable text from %s", transcript_path)
        return json_ok({"accepted": True, "relayed": False})

    event_bus: EventBus = request.app["event_bus"]
    message_svc: MessageService = request.app["message_service"]
    platform_svc: PlatformService = request.app["platform_service"]

    # Look up session in DB (may not exist yet for new sessions)
    session = await db.fetchone(
        "SELECT * FROM sessions WHERE name = ?", (session_name,)
    )

    # Store the message via MessageService
    await message_svc.ingest_bridge_event({
        "type": "message.relayed",
        "session_name": session_name,
        "platform": "hook",
        "content": text,
        "role": "assistant",
        "source_id": session_id,
        "timestamp": db.now_iso(),
    })

    # Relay to Discord
    relayed_to: list[str] = []
    if platform_svc.has_discord:
        thread_id = (
            session.get("discord_thread_id") if session else None
        ) or await platform_svc.find_discord_thread(session_name)
        if thread_id and await platform_svc.post_discord_message(thread_id, text):
            relayed_to.append("discord")

    # Relay to Slack
    if platform_svc.has_slack:
        thread_ts = (
            session.get("slack_thread_ts") if session else None
        ) or await platform_svc.find_slack_thread(session_name)
        slack_channel = (
            (session.get("slack_channel_id") if session else None)
            or platform_svc.slack_channel_id
        )
        if thread_ts and await platform_svc.post_slack_message(slack_channel, thread_ts, text):
            relayed_to.append("slack")

    # Publish event for WebSocket clients
    await event_bus.publish(Event.hook_stop({
        "session_name": session_name,
        "session_id": session_id,
        "relayed_to": relayed_to,
    }))

    return json_ok({"accepted": True, "relayed": bool(relayed_to)})


async def handle_session(request: web.Request) -> web.Response:
    """POST /api/hooks/session

    Handle Claude Code SessionStart and SessionEnd hook events.
    Tracks session lifecycle in the database.
    """
    try:
        body = await request.json()
    except (json.JSONDecodeError, Exception):
        return error_response(400, "INVALID_JSON", "Request body must be JSON")

    hook_event = body.get("hook_event_name", "")
    session_id = body.get("session_id", "")
    cwd = body.get("cwd", "")
    session_name = _session_name_from_cwd(cwd)

    logger.info(
        "Hook session event: %s session=%s cwd=%s",
        hook_event, session_name, cwd,
    )

    event_bus: EventBus = request.app["event_bus"]
    now = db.now_iso()

    if hook_event == "SessionStart":
        model = body.get("model", "")
        source = body.get("source", "")

        # Upsert session: create if not exists, update if exists
        existing = await db.fetchone(
            "SELECT * FROM sessions WHERE name = ?", (session_name,)
        )
        if existing:
            await db.execute(
                """UPDATE sessions
                   SET status = 'active', updated_at = ?,
                       agent_type = COALESCE(?, agent_type),
                       working_dir = COALESCE(?, working_dir)
                   WHERE name = ?""",
                (now, "claude", cwd or None, session_name),
            )
        else:
            await db.execute(
                """INSERT INTO sessions
                   (name, host, status, agent_type, working_dir, created_at, updated_at)
                   VALUES (?, 'localhost', 'active', 'claude', ?, ?, ?)""",
                (session_name, cwd or None, now, now),
            )

        # Store model/session_id metadata in events table
        await db.insert_or_ignore(
            "events",
            {
                "event_type": "hook.session_start",
                "session_name": session_name,
                "payload": json.dumps({
                    "session_id": session_id,
                    "model": model,
                    "source": source,
                    "cwd": cwd,
                }),
                "created_at": now,
            },
        )

        session_data = await db.fetchone(
            "SELECT * FROM sessions WHERE name = ?", (session_name,)
        )
        await event_bus.publish(
            Event.hook_session_start(dict(session_data) if session_data else {})
        )

    elif hook_event == "SessionEnd":
        existing = await db.fetchone(
            "SELECT * FROM sessions WHERE name = ?", (session_name,)
        )
        if existing:
            await db.execute(
                """UPDATE sessions
                   SET status = 'idle', updated_at = ?
                   WHERE name = ?""",
                (now, session_name),
            )

            await db.insert_or_ignore(
                "events",
                {
                    "event_type": "hook.session_end",
                    "session_name": session_name,
                    "payload": json.dumps({
                        "session_id": session_id,
                        "cwd": cwd,
                    }),
                    "created_at": now,
                },
            )

            session_data = await db.fetchone(
                "SELECT * FROM sessions WHERE name = ?", (session_name,)
            )
            await event_bus.publish(
                Event.hook_session_end(
                    dict(session_data) if session_data else {}
                )
            )
        else:
            logger.debug(
                "SessionEnd for unknown session '%s', ignoring",
                session_name,
            )

    else:
        return error_response(
            400,
            "UNKNOWN_EVENT",
            f"Unknown hook_event_name: {hook_event}",
        )

    return json_ok({"accepted": True})


async def handle_tool_activity(request: web.Request) -> web.Response:
    """POST /api/hooks/tool-activity

    Handle Claude Code PostToolUse hook events.
    Stores tool usage in the events table and publishes to WebSocket.
    """
    try:
        body = await request.json()
    except (json.JSONDecodeError, Exception):
        return error_response(400, "INVALID_JSON", "Request body must be JSON")

    session_id = body.get("session_id", "")
    cwd = body.get("cwd", "")
    tool_name = body.get("tool_name", "")
    tool_input = body.get("tool_input", {})
    session_name = _session_name_from_cwd(cwd)

    now = db.now_iso()

    # Store in events table
    await db.insert_or_ignore(
        "events",
        {
            "event_type": "hook.tool_use",
            "session_name": session_name,
            "payload": json.dumps({
                "session_id": session_id,
                "tool_name": tool_name,
                "tool_input": tool_input,
            }),
            "created_at": now,
        },
    )

    # Publish for real-time WebSocket feed
    event_bus: EventBus = request.app["event_bus"]
    await event_bus.publish(Event.hook_tool_use({
        "session_name": session_name,
        "session_id": session_id,
        "tool_name": tool_name,
        "tool_input": tool_input,
    }))

    return json_ok({"accepted": True})
