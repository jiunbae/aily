#!/bin/bash
# Claude Code hook: notify via Discord (#workspace thread per tmux session)
# Runs in background to avoid hook timeout

set -euo pipefail

ENV_FILE="$(dirname "$0")/.notify-env"
if [[ ! -f "$ENV_FILE" ]]; then
  exit 0
fi

HOOK_DIR="$(dirname "$0")"
TMUX_SESSION=""
if [[ -n "${TMUX:-}" ]]; then
  TMUX_SESSION=$(/opt/homebrew/bin/tmux display-message -p '#S' 2>/dev/null || echo "")
fi

# Fork to background so hook returns immediately
(
  source "$ENV_FILE"

  # Wait for Claude Code to flush response to JSONL
  sleep 5

  HOSTNAME=$(hostname -s)
  SESSION_CWD="${PWD:-unknown}"
  PROJECT=$(basename "$SESSION_CWD")
  TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')

  LAST_MESSAGE=$(python3 "${HOOK_DIR}/extract-last-message.py" 2>/dev/null || echo "")

  escape_json() {
    python3 -c "import sys,json; print(json.dumps(sys.stdin.read()))" <<< "$1"
  }

  # === Discord notification ===
  if [[ -z "${DISCORD_BOT_TOKEN:-}" || -z "${DISCORD_CHANNEL_ID:-}" || -z "${TMUX_SESSION}" ]]; then
    exit 0
  fi

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

  # Check channel messages
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
      -d "{\"content\": \"🖥 **tmux session: ${THREAD_NAME}** (${HOSTNAME})\"}" \
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
    SUMMARY_BLOCK=""
    if [[ -n "$LAST_MESSAGE" ]]; then
      if [[ ${#LAST_MESSAGE} -gt 1000 ]]; then
        LAST_MESSAGE="${LAST_MESSAGE:0:1000}..."
      fi
      ESCAPED=$(escape_json "$LAST_MESSAGE")
      ESCAPED="${ESCAPED:1:${#ESCAPED}-2}"
      SUMMARY_BLOCK="\\n\\n**Response:**\\n${ESCAPED}"
    fi

    DISCORD_MSG="🔔 **Task Complete**\\n\\n🖥 Host: \`${HOSTNAME}\`\\n📁 Project: \`${PROJECT}\`\\n⏰ Time: ${TIMESTAMP}${SUMMARY_BLOCK}"

    PAYLOAD="{\"content\": \"${DISCORD_MSG}\"}"
    if [[ ${#PAYLOAD} -gt 1990 ]]; then
      SHORT="${LAST_MESSAGE:0:400}..."
      SHORT_ESC=$(escape_json "$SHORT")
      SHORT_ESC="${SHORT_ESC:1:${#SHORT_ESC}-2}"
      DISCORD_MSG="🔔 **Task Complete**\\n\\n🖥 Host: \`${HOSTNAME}\`\\n📁 Project: \`${PROJECT}\`\\n⏰ Time: ${TIMESTAMP}\\n\\n**(truncated) Response:**\\n${SHORT_ESC}"
      PAYLOAD="{\"content\": \"${DISCORD_MSG}\"}"
    fi

    curl -sf -X POST -H "$AUTH" -H "Content-Type: application/json" \
      -d "$PAYLOAD" \
      "https://discord.com/api/v10/channels/${THREAD_ID}/messages" > /dev/null 2>&1 || true
  fi
) &

# Return immediately - background process handles the notification
disown
exit 0
