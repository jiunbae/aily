#!/bin/bash
# Shared Discord posting logic for all agent hooks.
# Usage: discord-post.sh <agent_name> <message_text>
#    OR: discord-post.sh --raw <pre_formatted_message>
#
# Required env vars: DISCORD_BOT_TOKEN, DISCORD_CHANNEL_ID
# Requires: running inside a tmux session (TMUX/TMUX_PANE set)
#
# NOTE: This script does NOT fork. Entrypoints should background it to avoid hook timeouts.

set -euo pipefail

HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HOOK_DIR/log.sh"

RAW_MODE=false
if [[ "${1:-}" == "--raw" ]]; then
  RAW_MODE=true
  RAW_MESSAGE="${2:-}"
  AGENT_NAME="raw"
  MESSAGE_TEXT=""
else
  AGENT_NAME="${1:-unknown}"
  MESSAGE_TEXT="${2:-}"
fi

ENV_FILE="${AILY_ENV:-${XDG_CONFIG_HOME:-$HOME/.config}/aily/env}"
if [[ ! -f "$ENV_FILE" ]]; then
  exit 0
fi

# shellcheck source=/dev/null
source "$ENV_FILE"

# Detect tmux session via direct query (works even when env vars are missing)
TMUX_SESSION=$(tmux display-message -p '#{session_name}' 2>/dev/null || echo "")

if [[ -z "${DISCORD_BOT_TOKEN:-}" || -z "${DISCORD_CHANNEL_ID:-}" || -z "${TMUX_SESSION}" ]]; then
  exit 0
fi

HOSTNAME_SHORT=$(hostname -s 2>/dev/null || hostname)
PROJECT=$(basename "${PWD:-unknown}")
TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')

_DEFAULT_FMT='[agent] {session} - {host}'
THREAD_NAME="${THREAD_NAME_FORMAT:-$_DEFAULT_FMT}"
THREAD_NAME="${THREAD_NAME//\{session\}/$TMUX_SESSION}"
THREAD_NAME="${THREAD_NAME//\{host\}/$HOSTNAME_SHORT}"

# Source shared Discord API functions
# shellcheck source=/dev/null
source "${HOOK_DIR}/discord-lib.sh"

# Find or create thread
THREAD_ID=$(discord_ensure_thread "$THREAD_NAME")

# Post to thread
if [[ -n "$THREAD_ID" ]]; then
  if [[ "$RAW_MODE" == true ]]; then
    # Raw mode: post pre-formatted message as-is
    DISCORD_MSG="$RAW_MESSAGE"
  else
    # Standard mode: wrap in Task Complete format
    if [[ -n "$MESSAGE_TEXT" && ${#MESSAGE_TEXT} -gt 1000 ]]; then
      MESSAGE_TEXT="${MESSAGE_TEXT:0:1000}..."
    fi

    DISCORD_MSG=$(printf '🔔 **Task Complete** (%s)\n\n🖥 Host: `%s`\n📁 Project: `%s`\n⏰ Time: %s' \
      "$AGENT_NAME" "$HOSTNAME_SHORT" "$PROJECT" "$TIMESTAMP")

    if [[ -n "$MESSAGE_TEXT" ]]; then
      DISCORD_MSG="${DISCORD_MSG}"$'\n\n'"**Response:**"$'\n'"${MESSAGE_TEXT}"
    fi
  fi

  discord_post_to_thread "$THREAD_ID" "$DISCORD_MSG"
fi
