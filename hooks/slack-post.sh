#!/bin/bash
# Slack posting hook.
# Usage: slack-post.sh <agent_name> <message_text>
#    OR: slack-post.sh --raw <pre_formatted_message>
#
# Required env vars: SLACK_BOT_TOKEN, SLACK_CHANNEL_ID
# Requires: running inside a tmux session (TMUX/TMUX_PANE set)
#
# NOTE: This script does NOT fork. Entrypoints should background it to avoid hook timeouts.

set -euo pipefail

HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HOOK_DIR/log.sh"

# shellcheck source=_post-common.sh
source "$HOOK_DIR/_post-common.sh" "$@"

[[ -z "${SLACK_BOT_TOKEN:-}" || -z "${SLACK_CHANNEL_ID:-}" ]] && exit 0

# shellcheck source=slack-lib.sh
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
