#!/usr/bin/env python3
"""Extract the last meaningful assistant text message from Claude Code session JSONL."""
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


def extract_last_assistant_text(jsonl_path, max_chars=1000):
    """Read backwards to find last assistant text. Never falls back to older messages."""
    with open(jsonl_path) as f:
        lines = f.readlines()

    for line in reversed(lines[-200:]):
        try:
            obj = json.loads(line.strip())
            if obj.get("type") != "assistant":
                continue
            content = obj.get("message", {}).get("content", [])

            # If this turn has an interactive prompt, suppress notification
            # (the PreToolUse hook handles these separately)
            if has_interactive_tool(content):
                return None

            texts = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    t = block.get("text", "").strip()
                    if t:
                        texts.append(t)
            if not texts:
                # Tool-only turn (no text) — skip to find the text turn
                continue
            full_text = "\n".join(texts)
            full_text = strip_english_coach(full_text)
            full_text = tables_to_codeblocks(full_text)
            if not full_text:
                # Text was entirely English Coach block — return nothing
                return None
            if len(full_text) > max_chars:
                full_text = full_text[:max_chars] + "..."
            return full_text
        except:
            continue
    return None


def _state_file_for(jsonl_path):
    """Per-session dedup state file based on JSONL filename."""
    basename = os.path.splitext(os.path.basename(jsonl_path))[0]
    return os.path.join(STATE_DIR, basename)


def was_already_sent(jsonl_path, text):
    """Check if this exact message was already sent for this session."""
    msg_hash = hashlib.md5(text.encode()).hexdigest()
    try:
        with open(_state_file_for(jsonl_path)) as f:
            return f.read().strip() == msg_hash
    except FileNotFoundError:
        return False


def mark_as_sent(jsonl_path, text):
    """Record this message hash to prevent re-sends for this session."""
    msg_hash = hashlib.md5(text.encode()).hexdigest()
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
