#!/bin/bash
# Discord posting hook.
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

# shellcheck source=_post-common.sh
source "$HOOK_DIR/_post-common.sh" "$@"

[[ -z "${DISCORD_BOT_TOKEN:-}" || -z "${DISCORD_CHANNEL_ID:-}" ]] && exit 0

# shellcheck source=discord-lib.sh
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
