#!/bin/bash
# Shared Discord posting logic for all agent hooks.
# Usage: discord-post.sh <agent_name> <message_text>
#
# Required env vars (from .notify-env in same dir): DISCORD_BOT_TOKEN, DISCORD_CHANNEL_ID
# Requires: running inside a tmux session (TMUX/TMUX_PANE set)
#
# NOTE: This script does NOT fork. Entrypoints should background it to avoid hook timeouts.

set -euo pipefail

AGENT_NAME="${1:-unknown}"
MESSAGE_TEXT="${2:-}"

HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${HOOK_DIR}/.notify-env"
if [[ ! -f "$ENV_FILE" ]]; then
  exit 0
fi

# shellcheck source=/dev/null
source "$ENV_FILE"

# Detect tmux session
TMUX_BIN="/opt/homebrew/bin/tmux"
if [[ ! -x "$TMUX_BIN" ]]; then
  TMUX_BIN="tmux"
fi

TMUX_SESSION=""
if [[ -n "${TMUX_PANE:-}" ]]; then
  TMUX_SESSION=$("$TMUX_BIN" display-message -t "${TMUX_PANE}" -p '#{session_name}' 2>/dev/null || true)
elif [[ -n "${TMUX:-}" ]]; then
  TMUX_SESSION=$("$TMUX_BIN" display-message -p '#S' 2>/dev/null || true)
fi

if [[ -z "${DISCORD_BOT_TOKEN:-}" || -z "${DISCORD_CHANNEL_ID:-}" || -z "${TMUX_SESSION}" ]]; then
  exit 0
fi

HOSTNAME_SHORT=$(hostname -s 2>/dev/null || hostname)
PROJECT=$(basename "${PWD:-unknown}")
TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')

json_payload_content() {
  python3 - <<'PY'
import json, sys
content = sys.stdin.read()
print(json.dumps({"content": content}))
PY
}

json_payload_thread_name() {
  python3 - "$1" <<'PY'
import json, sys
print(json.dumps({"name": sys.argv[1]}))
PY
}

THREAD_NAME="[agent] ${TMUX_SESSION}"
AUTH="Authorization: Bot ${DISCORD_BOT_TOKEN}"

# Get guild ID from channel
GUILD_ID=$(curl -sf -H "$AUTH" \
  "https://discord.com/api/v10/channels/${DISCORD_CHANNEL_ID}" 2>/dev/null \
  | python3 - <<'PY' 2>/dev/null || true
import json, sys
try:
    print(json.load(sys.stdin).get("guild_id", ""))
except Exception:
    pass
PY
)

# Find existing thread (active) - must use guild endpoint
THREAD_ID=""
if [[ -n "$GUILD_ID" ]]; then
  THREAD_ID=$(curl -sf -H "$AUTH" \
    "https://discord.com/api/v10/guilds/${GUILD_ID}/threads/active" 2>/dev/null \
    | python3 - "$THREAD_NAME" "$DISCORD_CHANNEL_ID" <<'PY' 2>/dev/null || true
import json, sys
name = sys.argv[1]
parent = sys.argv[2]
try:
    data = json.load(sys.stdin)
    for t in data.get("threads", []):
        if t.get("name") == name and t.get("parent_id") == parent:
            print(t.get("id", ""))
            break
except Exception:
    pass
PY
  )
fi

# Check archived
if [[ -z "$THREAD_ID" ]]; then
  THREAD_ID=$(curl -sf -H "$AUTH" \
    "https://discord.com/api/v10/channels/${DISCORD_CHANNEL_ID}/threads/archived/public" 2>/dev/null \
    | python3 - "$THREAD_NAME" <<'PY' 2>/dev/null || true
import json, sys
name = sys.argv[1]
try:
    data = json.load(sys.stdin)
    for t in data.get("threads", []):
        if t.get("name") == name:
            print(t.get("id", ""))
            break
except Exception:
    pass
PY
  )
fi

# Check channel messages for thread metadata
if [[ -z "$THREAD_ID" ]]; then
  THREAD_ID=$(curl -sf -H "$AUTH" \
    "https://discord.com/api/v10/channels/${DISCORD_CHANNEL_ID}/messages?limit=50" 2>/dev/null \
    | python3 - "$THREAD_NAME" <<'PY' 2>/dev/null || true
import json, sys
name = sys.argv[1]
try:
    msgs = json.load(sys.stdin)
    for m in msgs:
        t = (m or {}).get("thread") or {}
        if t.get("name") == name:
            print(t.get("id", ""))
            break
except Exception:
    pass
PY
  )
fi

# Create thread if not found
if [[ -z "$THREAD_ID" ]]; then
  STARTER_CONTENT=$(printf '🖥 **tmux session: %s** (%s)' "$THREAD_NAME" "$HOSTNAME_SHORT")
  STARTER_PAYLOAD=$(json_payload_content <<< "$STARTER_CONTENT")

  STARTER_MSG_ID=$(curl -sf -X POST \
    -H "$AUTH" -H "Content-Type: application/json" \
    -d "$STARTER_PAYLOAD" \
    "https://discord.com/api/v10/channels/${DISCORD_CHANNEL_ID}/messages" 2>/dev/null \
    | python3 - <<'PY' 2>/dev/null || true
import json, sys
try:
    print(json.load(sys.stdin).get("id", ""))
except Exception:
    pass
PY
  )

  if [[ -n "$STARTER_MSG_ID" ]]; then
    THREAD_PAYLOAD=$(json_payload_thread_name "$THREAD_NAME")
    THREAD_ID=$(curl -sf -X POST \
      -H "$AUTH" -H "Content-Type: application/json" \
      -d "$THREAD_PAYLOAD" \
      "https://discord.com/api/v10/channels/${DISCORD_CHANNEL_ID}/messages/${STARTER_MSG_ID}/threads" 2>/dev/null \
      | python3 - <<'PY' 2>/dev/null || true
import json, sys
try:
    print(json.load(sys.stdin).get("id", ""))
except Exception:
    pass
PY
    )
  fi
fi

# Unarchive if needed
if [[ -n "$THREAD_ID" ]]; then
  curl -sf -X PATCH -H "$AUTH" -H "Content-Type: application/json" \
    -d '{"archived": false}' \
    "https://discord.com/api/v10/channels/${THREAD_ID}" > /dev/null 2>&1 || true
fi

# Post to thread
if [[ -n "$THREAD_ID" ]]; then
  if [[ -n "$MESSAGE_TEXT" && ${#MESSAGE_TEXT} -gt 1000 ]]; then
    MESSAGE_TEXT="${MESSAGE_TEXT:0:1000}..."
  fi

  DISCORD_MSG=$(printf '🔔 **Task Complete** (%s)\n\n🖥 Host: `%s`\n📁 Project: `%s`\n⏰ Time: %s' \
    "$AGENT_NAME" "$HOSTNAME_SHORT" "$PROJECT" "$TIMESTAMP")

  if [[ -n "$MESSAGE_TEXT" ]]; then
    DISCORD_MSG="${DISCORD_MSG}"$'\n\n'"**Response:**"$'\n'"${MESSAGE_TEXT}"
  fi

  PAYLOAD=$(json_payload_content <<< "$DISCORD_MSG")
  if [[ ${#PAYLOAD} -gt 1990 && -n "$MESSAGE_TEXT" ]]; then
    SHORT="${MESSAGE_TEXT:0:400}"
    if [[ ${#MESSAGE_TEXT} -gt 400 ]]; then
      SHORT="${SHORT}..."
    fi
    DISCORD_MSG=$(printf '🔔 **Task Complete** (%s)\n\n🖥 Host: `%s`\n📁 Project: `%s`\n⏰ Time: %s\n\n**(truncated) Response:**\n%s' \
      "$AGENT_NAME" "$HOSTNAME_SHORT" "$PROJECT" "$TIMESTAMP" "$SHORT")
    PAYLOAD=$(json_payload_content <<< "$DISCORD_MSG")
  fi

  curl -sf -X POST -H "$AUTH" -H "Content-Type: application/json" \
    -d "$PAYLOAD" \
    "https://discord.com/api/v10/channels/${THREAD_ID}/messages" > /dev/null 2>&1 || true
fi
