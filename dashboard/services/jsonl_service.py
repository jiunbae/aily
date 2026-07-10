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
import re
import shlex
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

        # Reject unsafe characters to prevent shell injection
        if not re.fullmatch(r"[a-zA-Z0-9._-]+", sanitized_cwd):
            logger.warning(
                "Rejecting unsafe sanitized_cwd %r for session %s",
                sanitized_cwd, session_name,
            )
            return None

        project_dir = f"$HOME/.claude/projects/{shlex.quote(sanitized_cwd)}"

        # `ls -t` sorts by mtime (newest first) and is portable across GNU and
        # BSD/macOS. `find -printf` is GNU-only and silently fails on BSD hosts.
        # sanitized_cwd is regex-whitelisted above, so the glob is safe.
        rc, out = await ssh.run_ssh(
            host,
            f"ls -t {project_dir}/*.jsonl 2>/dev/null | head -1",
            timeout=10,
        )
        if rc != 0 or not out.strip():
            return None

        return out.strip()

    async def _count_lines(self, host: str, jsonl_path: str) -> int:
        """Return the total line count of a remote file (0 on error)."""
        rc, out = await ssh.run_ssh(
            host, f"wc -l < {shlex.quote(jsonl_path)}", timeout=30
        )
        if rc != 0 or not out.strip():
            return 0
        try:
            return int(out.strip().split()[0])
        except (ValueError, IndexError):
            return 0

    async def read_jsonl_from(
        self, host: str, jsonl_path: str, start_line: int
    ) -> list[str]:
        """Read a JSONL file from ``start_line`` (1-based) to EOF via SSH.

        Reading from a persisted line offset (rather than a fixed-size tail)
        means no lines are skipped when a session emits more than ``max_lines``
        between scans.

        Returns:
            List of non-empty line strings.
        """
        start_line = max(1, start_line)
        rc, out = await ssh.run_ssh(
            host,
            f"tail -n +{start_line} {shlex.quote(jsonl_path)}",
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

            # Extract timestamp. Do NOT fall back to costInMillis — that is a
            # cost/duration, not an epoch, so fromtimestamp() would yield bogus
            # ~1970 timestamps and corrupt message ordering. Use "now" instead.
            timestamp = obj.get("timestamp", "")
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

        # Step 2 & 3: Read only unprocessed lines using a persisted line offset.
        # A fixed "last max_lines" tail lost data whenever a session emitted more
        # than max_lines between scans — the stored marker scrolled out of the
        # window and everything before the new tail was skipped forever. Tracking
        # a line offset and reading from it forward closes that gap.
        kv_key = f"jsonl_line_offset:{session_name}"
        offset_row = await db.fetchone(
            "SELECT value FROM kv WHERE key = ?", (kv_key,)
        )
        processed_lines: int | None = None
        if offset_row and offset_row["value"]:
            try:
                processed_lines = int(offset_row["value"])
            except (TypeError, ValueError):
                processed_lines = None

        if processed_lines is None:
            # First scan for this session: bound the initial read to the last
            # max_lines lines (older history is not backfilled), then track from
            # the current end of file.
            total = await self._count_lines(host, jsonl_path)
            start_line = max(1, total - self.max_lines + 1)
        else:
            start_line = processed_lines + 1

        new_lines = await self.read_jsonl_from(host, jsonl_path, start_line)
        if not new_lines:
            return 0

        # Advance conservatively by the number of lines actually read. If the
        # file grew mid-read we re-read the overlap next scan; dedup
        # (insert_or_ignore on a per-line hash) makes that idempotent, so a line
        # is never skipped.
        new_offset = start_line - 1 + len(new_lines)

        # Step 4: Parse and insert (batched — single commit for all writes)
        messages = self.parse_jsonl_lines(new_lines, session_name)
        ingested = 0
        new_events: list[dict[str, Any]] = []

        async with db.batch():
            for msg_data in messages:
                cursor = await db.insert_or_ignore("messages", msg_data)
                if cursor.rowcount and cursor.rowcount > 0:
                    ingested += 1
                    new_events.append(
                        {
                            "session_name": session_name,
                            "role": msg_data["role"],
                            "content": msg_data["content"],
                            "source": "jsonl",
                            "timestamp": msg_data["timestamp"],
                        }
                    )

            # Step 5: Persist the new line offset (inside the same batch).
            offset_str = str(new_offset)
            await db.execute(
                """INSERT INTO kv (key, value, updated)
                   VALUES (?, ?, ?)
                   ON CONFLICT(key) DO UPDATE SET value = ?, updated = ?""",
                (kv_key, offset_str, db.now_iso(), offset_str, db.now_iso()),
            )

        # Publish events after commit so subscribers see committed data
        for event_data in new_events:
            await self.event_bus.publish(Event.message_new(event_data))

        if ingested > 0:
            logger.info(
                "Ingested %d JSONL messages for session '%s' from %s",
                ingested, session_name, jsonl_path,
            )

        return ingested
