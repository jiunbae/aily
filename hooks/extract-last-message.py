#!/usr/bin/env python3
"""Extract the last meaningful assistant text message from Claude Code session JSONL.

DEPRECATED: This logic has been ported to dashboard/api/hooks.py for use with
HTTP hooks. This file is kept for backward compatibility with shell-based hooks.
"""
import fcntl, hashlib, json, os, sys, glob, re

_xdg_data = os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share"))
STATE_DIR = os.path.join(_xdg_data, "aily", "dedup")

def get_project_dir(cwd):
    sanitized = cwd.replace("/", "-")
    return os.path.expanduser(f"~/.claude/projects/{sanitized}")

def find_latest_jsonl(project_dir):
    pattern = os.path.join(project_dir, "*.jsonl")
    files = glob.glob(pattern)
    if not files:
        return None
    return max(files, key=os.path.getmtime)

def strip_english_coach(text):
    """Remove the English Coach --- > ... --- block from the start."""
    stripped = text.strip()
    if not stripped.startswith("---"):
        return stripped
    # Split on --- markers, take everything after the second ---
    parts = stripped.split("---")
    if len(parts) >= 3:
        result = "---".join(parts[2:]).strip()
        return result if result else stripped
    return stripped

def tables_to_codeblocks(text):
    """Wrap markdown tables in code blocks (Discord doesn't render md tables)."""
    lines = text.split("\n")
    result = []
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

def has_interactive_tool(content):
    """Check if content has AskUserQuestion or similar interactive tool calls."""
    for block in content:
        if isinstance(block, dict) and block.get("type") == "tool_use":
            if block.get("name") in ("AskUserQuestion", "EnterPlanMode"):
                return True
    return False


def _tail_lines(path, max_lines=30, chunk_size=262144):
    """Read up to max_lines lines from the end of a file without loading the entire file.

    Reads fixed-size chunks from the tail until enough newlines are gathered. Avoids
    f.readlines() which loads multi-megabyte JSONL transcripts entirely into memory.
    Uses a chunk list (joined once) instead of repeated bytes-prepend to keep memory O(n).

    Note: Claude Code transcript lines can be very large (one full turn each, often
    20–40KB), so max_lines=30 is enough headroom to find the last assistant text turn
    without dragging in megabytes of older history."""
    try:
        size = os.path.getsize(path)
    except OSError:
        return []
    if size == 0:
        return []
    chunks = []
    seen_newlines = 0
    with open(path, "rb") as f:
        pos = size
        while pos > 0 and seen_newlines <= max_lines:
            read_size = min(chunk_size, pos)
            pos -= read_size
            f.seek(pos)
            chunk = f.read(read_size)
            seen_newlines += chunk.count(b"\n")
            chunks.append(chunk)
    # chunks were appended tail-first; reverse for correct order before join
    chunks.reverse()
    text = b"".join(chunks).decode("utf-8", errors="replace")
    return text.splitlines()[-max_lines:]


def _scan_for_assistant_text(lines, max_chars):
    for line in reversed(lines):
        try:
            obj = json.loads(line.strip())
            if obj.get("type") != "assistant":
                continue
            content = obj.get("message", {}).get("content", [])

            # If this turn has an interactive prompt, suppress notification
            # (the PreToolUse hook handles these separately)
            if has_interactive_tool(content):
                return ("suppress", None)

            texts = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    t = block.get("text", "").strip()
                    if t:
                        texts.append(t)
            if not texts:
                continue
            full_text = "\n".join(texts)
            full_text = strip_english_coach(full_text)
            full_text = tables_to_codeblocks(full_text)
            if not full_text:
                return ("suppress", None)
            if len(full_text) > max_chars:
                full_text = full_text[:max_chars] + "..."
            return ("found", full_text)
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            continue
    return ("not_found", None)


def extract_last_assistant_text(jsonl_path, max_chars=1000):
    """Read backwards to find last assistant text. Never falls back to older messages.

    Two-pass strategy: try a small tail first (covers ~99% of cases cheaply); only on
    miss do we widen to the larger window."""
    for max_lines in (30, 200):
        lines = _tail_lines(jsonl_path, max_lines=max_lines)
        status, text = _scan_for_assistant_text(lines, max_chars)
        if status == "found":
            return text
        if status == "suppress":
            return None
    return None


def _state_file_for(jsonl_path):
    """Per-session dedup state file based on JSONL filename."""
    basename = os.path.splitext(os.path.basename(jsonl_path))[0]
    return os.path.join(STATE_DIR, basename)


def was_already_sent(jsonl_path, text):
    """Check if this exact message was already sent for this session."""
    msg_hash = hashlib.sha256(text.encode()).hexdigest()[:32]
    try:
        with open(_state_file_for(jsonl_path)) as f:
            return f.read().strip() == msg_hash
    except FileNotFoundError:
        return False


def mark_as_sent(jsonl_path, text):
    """Record this message hash to prevent re-sends for this session."""
    msg_hash = hashlib.sha256(text.encode()).hexdigest()[:32]
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(_state_file_for(jsonl_path), "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        f.write(msg_hash)

def main():
    cwd = os.environ.get("PWD", os.getcwd())
    project_dir = get_project_dir(cwd)
    jsonl = find_latest_jsonl(project_dir)
    if not jsonl:
        sys.exit(0)
    text = extract_last_assistant_text(jsonl)
    if not text:
        sys.exit(0)
    if was_already_sent(jsonl, text):
        sys.exit(0)
    mark_as_sent(jsonl, text)
    print(text)

if __name__ == "__main__":
    main()
