#!/bin/bash
# Gemini CLI AfterAgent hook entry point.
# Reads JSON from stdin, extracts last response from transcript, posts to enabled platforms.
#
# Registered in ~/.gemini/settings.json → hooks.AfterAgent
# IMPORTANT: Must output valid JSON to stdout. All logging to stderr.

set -euo pipefail

HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Read stdin JSON (Gemini passes hook data via stdin). If stdin is empty or broken,
# still return valid JSON to stdout.
STDIN_DATA="$(cat || true)"

# Extract transcript_path and cwd from stdin JSON (single parse).
TRANSCRIPT_PATH=""
GEMINI_CWD=""
PARSED=$(
  python3 - <<'PY' 2>/dev/null <<<"$STDIN_DATA" || true
import json, sys
try:
    data = json.load(sys.stdin)
except Exception:
    data = {}
tp = data.get("transcript_path") or ""
cwd = data.get("cwd") or ""
print(tp)
print(cwd)
PY
)
IFS=$'\n' read -r TRANSCRIPT_PATH GEMINI_CWD <<< "$PARSED"

# Output valid JSON to stdout (Gemini CLI expects this). Do this before any slow work.
printf '%s\n' '{}'

# Fast exits (after emitting stdout JSON).
ENV_FILE="${HOOK_DIR}/.notify-env"
if [[ ! -f "$ENV_FILE" ]]; then
  exit 0
fi
if [[ -z "${TMUX:-}" && -z "${TMUX_PANE:-}" ]]; then
  exit 0
fi

# Fork to background for Discord posting
(
  set -euo pipefail

  LAST_MESSAGE=""
  if [[ -n "${TRANSCRIPT_PATH}" && -f "${TRANSCRIPT_PATH}" ]]; then
    # Extract last assistant message from Gemini transcript JSONL.
    LAST_MESSAGE=$(
      python3 - "${TRANSCRIPT_PATH}" <<'PY' 2>/dev/null || true
import json, re, sys

path = sys.argv[1]

def tables_to_codeblocks(text: str) -> str:
    code_fence = chr(96) * 3
    lines = text.splitlines()
    out = []
    in_table = False
    for line in lines:
        is_table_line = bool(re.match(r"^\\s*\\|", line))
        if is_table_line and not in_table:
            in_table = True
            out.append(code_fence)
            out.append(line)
        elif is_table_line and in_table:
            out.append(line)
        elif (not is_table_line) and in_table:
            in_table = False
            out.append(code_fence)
            out.append(line)
        else:
            out.append(line)
    if in_table:
        out.append(code_fence)
    return "\\n".join(out)

def is_assistant(obj: dict) -> bool:
    if obj.get("type") == "assistant":
        return True
    role = obj.get("role")
    if role in ("assistant", "model"):
        return True
    msg = obj.get("message")
    if isinstance(msg, dict):
        if msg.get("type") == "assistant":
            return True
        role = msg.get("role")
        if role in ("assistant", "model"):
            return True
    return False

def extract_text(obj: dict) -> str:
    msg = obj.get("message") if isinstance(obj.get("message"), dict) else obj
    content = None
    if isinstance(msg, dict):
        content = msg.get("content")
        if content is None and "parts" in msg:
            content = msg.get("parts")
        if content is None and "text" in msg:
            content = msg.get("text")
    if content is None:
        content = obj.get("content") or obj.get("parts") or obj.get("text")

    texts = []
    def add_text(t):
        if not isinstance(t, str):
            return
        t = t.strip()
        if t:
            texts.append(t)

    if isinstance(content, str):
        add_text(content)
    elif isinstance(content, list):
        for part in content:
            if isinstance(part, str):
                add_text(part)
            elif isinstance(part, dict):
                if part.get("type") == "text":
                    add_text(part.get("text"))
                elif "text" in part:
                    add_text(part.get("text"))
                elif "content" in part and isinstance(part.get("content"), str):
                    add_text(part.get("content"))

    return "\\n".join(texts).strip()

def extract_last(path: str, max_chars: int = 1000, min_chars: int = 20) -> str:
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except Exception:
        return ""

    for line in reversed(lines[-200:]):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue
        if not isinstance(obj, dict):
            continue
        if not is_assistant(obj):
            continue
        text = extract_text(obj)
        if not text or len(text) < min_chars:
            continue
        text = tables_to_codeblocks(text)
        if len(text) > max_chars:
            text = text[:max_chars] + "..."
        return text
    return ""

sys.stdout.write(extract_last(path))
PY
    )
  fi

  if [[ -n "${GEMINI_CWD}" ]]; then
    cd "${GEMINI_CWD}" 2>/dev/null || true
  fi

  exec bash "${HOOK_DIR}/post.sh" "gemini" "${LAST_MESSAGE}"
) 1>/dev/null &

disown
exit 0
