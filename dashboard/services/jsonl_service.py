"""JSONL file reading and parsing for Claude Code session data.

Reads Claude Code session JSONL files via SSH. Parses the complex
nested message format (assistant blocks with text/tool_use/tool_result).
Supports incremental reading -- tracks the last line hash to avoid
re-processing already-ingested lines.

JSONL format (Claude Code ~/.claude/projects/<cwd>/*.jsonl):
    {"type": "user", "message": {"role": "user", "content": "..."}, ...}
    {"type": "assistant", "message": {"role": "assistant", "content": [...]}, ...}
    {"type": "tool_result", ...}
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any

from dashboard import db, ssh
from dashboard.services.event_bus import Event, EventBus
from dashboard.services.message_service import compute_dedup_hash

logger = logging.getLogger(__name__)


class JSONLService:
    """Reads and parses Claude Code JSONL session files."""

    def __init__(
        self,
        event_bus: EventBus,
        max_lines: int = 500,
        max_content_length: int = 5000,
    ) -> None:
        self.event_bus = event_bus
        self.max_lines = max_lines
        self.max_content_length = max_content_length

    async def discover_jsonl_path(
        self, host: str, session_name: str, working_dir: str | None
    ) -> str | None:
        """Find the JSONL file for a session on a remote host.

        Claude Code stores session data in:
            ~/.claude/projects/<sanitized-cwd>/*.jsonl

        where <sanitized-cwd> replaces / with -.

        Args:
            host: SSH host.
            session_name: tmux session name.
            working_dir: Session working directory (from tmux pane_current_path).

        Returns:
            Full path to the latest JSONL file, or None.
        """
        if not working_dir:
            return None

        # Sanitize cwd the same way Claude Code does
        sanitized_cwd = working_dir.replace("/", "-")
        # Remove leading dash
        if sanitized_cwd.startswith("-"):
            sanitized_cwd = sanitized_cwd[1:]

        project_dir = f"~/.claude/projects/{sanitized_cwd}"

        rc, out = await ssh.run_ssh(
            host,
            f"ls -t {project_dir}/*.jsonl 2>/dev/null | head -1",
            timeout=10,
        )
        if rc != 0 or not out.strip():
            return None

        return out.strip()

    async def read_jsonl_tail(
        self, host: str, jsonl_path: str
    ) -> list[str]:
        """Read the last N lines of a JSONL file via SSH.

        Args:
            host: SSH host.
            jsonl_path: Full path to the JSONL file.

        Returns:
            List of non-empty line strings.
        """
        rc, out = await ssh.run_ssh(
            host,
            f"tail -{self.max_lines} {jsonl_path}",
            timeout=30,
        )
        if rc != 0 or not out:
            return []

        return [line for line in out.split("\n") if line.strip()]

    def parse_jsonl_lines(
        self, lines: list[str], session_name: str
    ) -> list[dict[str, Any]]:
        """Parse JSONL lines into normalized message dicts.

        Handles Claude Code's nested message format:
        - type=user: user messages (content is a string)
        - type=assistant: AI messages (content is a list of blocks)
        - Other types (tool_result, system, etc.) are skipped

        Args:
            lines: Raw JSONL line strings.
            session_name: For dedup hash computation.

        Returns:
            List of normalized message dicts ready for DB insertion.
        """
        messages: list[dict[str, Any]] = []

        for line in lines:
            try:
                obj = json.loads(line.strip())
            except json.JSONDecodeError:
                continue

            msg_type = obj.get("type", "")
            if msg_type not in ("user", "assistant"):
                continue

            # Extract content
            if msg_type == "user":
                content = self._extract_user_content(obj)
                role = "user"
            else:
                content = self._extract_assistant_content(obj)
                role = "assistant"

            if not content:
                continue

            # Truncate if too long
            if len(content) > self.max_content_length:
                content = content[: self.max_content_length] + "...(truncated)"

            # Extract timestamp
            timestamp = obj.get("timestamp", "")
            if not timestamp:
                # Try costInMillis or other timestamp sources
                cost_ms = obj.get("costInMillis")
                if cost_ms:
                    try:
                        timestamp = datetime.fromtimestamp(
                            cost_ms / 1000, tz=timezone.utc
                        ).isoformat()
                    except (ValueError, TypeError, OSError):
                        timestamp = ""

            if not timestamp:
                timestamp = db.now_iso()

            # Compute stable dedup hash using content fingerprint
            # JSONL has no message ID, so we hash session+source+content prefix
            line_hash = hashlib.sha256(line.encode()).hexdigest()[:16]
            dedup_hash = compute_dedup_hash(
                session_name, "jsonl", line_hash, content
            )

            messages.append(
                {
                    "session_name": session_name,
                    "role": role,
                    "content": content,
                    "source": "jsonl",
                    "source_id": line_hash,
                    "source_author": "claude" if role == "assistant" else "user",
                    "timestamp": timestamp,
                    "ingested_at": db.now_iso(),
                    "dedup_hash": dedup_hash,
                }
            )

        return messages

    def _extract_user_content(self, obj: dict) -> str:
        """Extract text from a user-type JSONL entry.

        User messages can have:
        - message.content as a string
        - message.content as a list of blocks
        """
        message = obj.get("message", {})
        content = message.get("content", "")

        if isinstance(content, str):
            return content.strip()

        if isinstance(content, list):
            texts = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    t = block.get("text", "").strip()
                    if t:
                        texts.append(t)
                elif isinstance(block, str):
                    texts.append(block.strip())
            return "\n".join(texts)

        return ""

    def _extract_assistant_content(self, obj: dict) -> str:
        """Extract text from an assistant-type JSONL entry.

        Assistant messages have content as a list of blocks:
        [{"type": "text", "text": "..."}, {"type": "tool_use", ...}]

        We only extract text blocks. Tool use/result blocks are skipped
        for the main message content (they could be shown separately).
        """
        message = obj.get("message", {})
        content = message.get("content", [])

        if isinstance(content, str):
            return content.strip()

        if not isinstance(content, list):
            return ""

        texts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                t = block.get("text", "").strip()
                if t:
                    texts.append(t)

        return "\n".join(texts)

    async def ingest_for_session(
        self,
        host: str,
        session_name: str,
        working_dir: str | None,
    ) -> int:
        """Full JSONL ingestion flow for a single session.

        1. Discover JSONL file path
        2. Read tail lines
        3. Filter out already-ingested lines (via kv offset tracking)
        4. Parse and insert new messages

        Args:
            host: SSH host.
            session_name: tmux session name.
            working_dir: Session working directory.

        Returns:
            Number of new messages ingested.
        """
        # Step 1: Find the JSONL file
        jsonl_path = await self.discover_jsonl_path(
            host, session_name, working_dir
        )
        if not jsonl_path:
            return 0

        # Step 2: Read tail
        lines = await self.read_jsonl_tail(host, jsonl_path)
        if not lines:
            return 0

        # Step 3: Check last processed position (via kv)
        kv_key = f"jsonl_offset:{session_name}"
        offset_row = await db.fetchone(
            "SELECT value FROM kv WHERE key = ?", (kv_key,)
        )
        last_line_hash = ""
        if offset_row:
            last_line_hash = offset_row["value"]

        # Find new lines (after the last processed line)
        new_lines = lines
        if last_line_hash:
            found_idx = -1
            for i, line in enumerate(lines):
                h = hashlib.sha256(line.encode()).hexdigest()[:32]
                if h == last_line_hash:
                    found_idx = i
                    break
            if found_idx >= 0:
                new_lines = lines[found_idx + 1 :]

        if not new_lines:
            return 0

        # Step 4: Parse and insert
        messages = self.parse_jsonl_lines(new_lines, session_name)
        ingested = 0
        for msg_data in messages:
            cursor = await db.insert_or_ignore("messages", msg_data)
            if cursor.rowcount and cursor.rowcount > 0:
                ingested += 1
                # Publish event for WebSocket
                await self.event_bus.publish(
                    Event.message_new(
                        {
                            "session_name": session_name,
                            "role": msg_data["role"],
                            "content": msg_data["content"],
                            "source": "jsonl",
                            "timestamp": msg_data["timestamp"],
                        }
                    )
                )

        # Step 5: Update offset tracker
        if lines:
            latest_hash = hashlib.sha256(lines[-1].encode()).hexdigest()[:32]
            await db.execute(
                """INSERT INTO kv (key, value, updated)
                   VALUES (?, ?, ?)
                   ON CONFLICT(key) DO UPDATE SET value = ?, updated = ?""",
                (kv_key, latest_hash, db.now_iso(), latest_hash, db.now_iso()),
            )

        if ingested > 0:
            logger.info(
                "Ingested %d JSONL messages for session '%s' from %s",
                ingested, session_name, jsonl_path,
            )

        return ingested
