#!/bin/bash
# Shared Slack posting logic for all agent hooks.
# Usage: slack-post.sh <agent_name> <message_text>
#    OR: slack-post.sh --raw <pre_formatted_message>
#
# Required env vars: SLACK_BOT_TOKEN, SLACK_CHANNEL_ID
# Requires: running inside a tmux session (TMUX/TMUX_PANE set)
#
# NOTE: This script does NOT fork. Entrypoints should background it to avoid hook timeouts.

set -euo pipefail

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

HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${AILY_ENV:-${XDG_CONFIG_HOME:-$HOME/.config}/aily/env}"
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

if [[ -z "${SLACK_BOT_TOKEN:-}" || -z "${SLACK_CHANNEL_ID:-}" || -z "${TMUX_SESSION}" ]]; then
  exit 0
fi

HOSTNAME_SHORT=$(hostname -s 2>/dev/null || hostname)
PROJECT=$(basename "${PWD:-unknown}")
TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')

THREAD_NAME="[agent] ${TMUX_SESSION}"

# Source shared Slack API functions
# shellcheck source=/dev/null
source "${HOOK_DIR}/slack-lib.sh"

# Find or create thread
THREAD_TS=$(slack_ensure_thread "$THREAD_NAME")

# Post to thread
if [[ -n "$THREAD_TS" ]]; then
  if [[ "$RAW_MODE" == true ]]; then
    SLACK_MSG="$RAW_MESSAGE"
  else
    if [[ -n "$MESSAGE_TEXT" && ${#MESSAGE_TEXT} -gt 1000 ]]; then
      MESSAGE_TEXT="${MESSAGE_TEXT:0:1000}..."
    fi

    SLACK_MSG=$(printf ':bell: *Task Complete* (%s)\n\n:desktop_computer: Host: `%s`\n:file_folder: Project: `%s`\n:clock1: Time: %s' \
      "$AGENT_NAME" "$HOSTNAME_SHORT" "$PROJECT" "$TIMESTAMP")

    if [[ -n "$MESSAGE_TEXT" ]]; then
      SLACK_MSG="${SLACK_MSG}"$'\n\n'"*Response:*"$'\n'"${MESSAGE_TEXT}"
    fi
  fi

  slack_post_to_thread "$THREAD_TS" "$SLACK_MSG"
fi
