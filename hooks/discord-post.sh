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

escape_json() {
  python3 -c "import sys,json; print(json.dumps(sys.stdin.read()))" <<< "$1"
}

THREAD_NAME="[agent] ${TMUX_SESSION}"
AUTH="Authorization: Bot ${DISCORD_BOT_TOKEN}"

# Get guild ID from channel
GUILD_ID=$(curl -sf -H "$AUTH" \
  "https://discord.com/api/v10/channels/${DISCORD_CHANNEL_ID}" 2>/dev/null \
  | python3 -c "import sys,json; print(json.load(sys.stdin).get('guild_id',''))" 2>/dev/null || echo "")

# Find existing thread (active) - must use guild endpoint
THREAD_ID=""
if [[ -n "$GUILD_ID" ]]; then
  THREAD_ID=$(curl -sf -H "$AUTH" \
    "https://discord.com/api/v10/guilds/${GUILD_ID}/threads/active" 2>/dev/null \
    | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    for t in data.get('threads', []):
        if t.get('name') == '${THREAD_NAME}' and t.get('parent_id') == '${DISCORD_CHANNEL_ID}':
            print(t['id'])
            break
except: pass
" 2>/dev/null || echo "")
fi

# Check archived
if [[ -z "$THREAD_ID" ]]; then
  THREAD_ID=$(curl -sf -H "$AUTH" \
    "https://discord.com/api/v10/channels/${DISCORD_CHANNEL_ID}/threads/archived/public" 2>/dev/null \
    | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    for t in data.get('threads', []):
        if t.get('name') == '${THREAD_NAME}':
            print(t['id'])
            break
except: pass
" 2>/dev/null || echo "")
fi

# Check channel messages for thread metadata
if [[ -z "$THREAD_ID" ]]; then
  THREAD_ID=$(curl -sf -H "$AUTH" \
    "https://discord.com/api/v10/channels/${DISCORD_CHANNEL_ID}/messages?limit=50" 2>/dev/null \
    | python3 -c "
import sys, json
try:
    msgs = json.load(sys.stdin)
    for m in msgs:
        t = m.get('thread', {})
        if t.get('name') == '${THREAD_NAME}':
            print(t['id'])
            break
except: pass
" 2>/dev/null || echo "")
fi

# Create thread if not found
if [[ -z "$THREAD_ID" ]]; then
  STARTER_MSG_ID=$(curl -sf -X POST \
    -H "$AUTH" -H "Content-Type: application/json" \
    -d "{\"content\": \"🖥 **tmux session: ${THREAD_NAME}** (${HOSTNAME_SHORT})\"}" \
    "https://discord.com/api/v10/channels/${DISCORD_CHANNEL_ID}/messages" 2>/dev/null \
    | python3 -c "import sys,json; print(json.load(sys.stdin).get('id',''))" 2>/dev/null || echo "")

  if [[ -n "$STARTER_MSG_ID" ]]; then
    THREAD_ID=$(curl -sf -X POST \
      -H "$AUTH" -H "Content-Type: application/json" \
      -d "{\"name\": \"${THREAD_NAME}\"}" \
      "https://discord.com/api/v10/channels/${DISCORD_CHANNEL_ID}/messages/${STARTER_MSG_ID}/threads" 2>/dev/null \
      | python3 -c "import sys,json; print(json.load(sys.stdin).get('id',''))" 2>/dev/null || echo "")
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

  SUMMARY_BLOCK=""
  if [[ -n "$MESSAGE_TEXT" ]]; then
    ESCAPED=$(escape_json "$MESSAGE_TEXT")
    ESCAPED="${ESCAPED:1:${#ESCAPED}-2}"
    SUMMARY_BLOCK="\\n\\n**Response:**\\n${ESCAPED}"
  fi

  DISCORD_MSG="🔔 **Task Complete** (${AGENT_NAME})\\n\\n🖥 Host: \`${HOSTNAME_SHORT}\`\\n📁 Project: \`${PROJECT}\`\\n⏰ Time: ${TIMESTAMP}${SUMMARY_BLOCK}"

  PAYLOAD="{\"content\": \"${DISCORD_MSG}\"}"
  if [[ ${#PAYLOAD} -gt 1990 ]]; then
    SHORT="${MESSAGE_TEXT:0:400}..."
    SHORT_ESC=$(escape_json "$SHORT")
    SHORT_ESC="${SHORT_ESC:1:${#SHORT_ESC}-2}"
    DISCORD_MSG="🔔 **Task Complete** (${AGENT_NAME})\\n\\n🖥 Host: \`${HOSTNAME_SHORT}\`\\n📁 Project: \`${PROJECT}\`\\n⏰ Time: ${TIMESTAMP}\\n\\n**(truncated) Response:**\\n${SHORT_ESC}"
    PAYLOAD="{\"content\": \"${DISCORD_MSG}\"}"
  fi

  curl -sf -X POST -H "$AUTH" -H "Content-Type: application/json" \
    -d "$PAYLOAD" \
    "https://discord.com/api/v10/channels/${THREAD_ID}/messages" > /dev/null 2>&1 || true
fi
