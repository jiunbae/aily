#!/bin/bash
# tmux hook: sync threads on session create/close across all enabled platforms.
# Usage: thread-sync.sh create <session_name>
#        thread-sync.sh delete <session_name>
#
# Called by tmux set-hook -g session-created/session-closed.
# Forks to background immediately so tmux is not blocked.

set -euo pipefail

MODE="${1:-}"
SESSION_NAME="${2:-}"

if [[ -z "$MODE" || -z "$SESSION_NAME" ]]; then
  exit 0
fi

# Skip infrastructure sessions
if [[ "$SESSION_NAME" == "agent-bridge" || "$SESSION_NAME" == "slack-bridge" ]]; then
  exit 0
fi

HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${HOOK_DIR}/.notify-env"
if [[ ! -f "$ENV_FILE" ]]; then
  exit 0
fi

# Fork to background so tmux hook returns immediately
(
  # shellcheck source=/dev/null
  source "$ENV_FILE"

  # Check if thread sync is enabled (default: true)
  if [[ "${TMUX_THREAD_SYNC:-true}" == "false" ]]; then
    exit 0
  fi

  THREAD_NAME="[agent] ${SESSION_NAME}"
  HOSTNAME_SHORT=$(hostname -s 2>/dev/null || hostname)
  TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')

  # --- Discord ---
  if [[ -n "${DISCORD_BOT_TOKEN:-}" && -n "${DISCORD_CHANNEL_ID:-}" ]]; then
    # shellcheck source=/dev/null
    source "${HOOK_DIR}/discord-lib.sh"

    case "$MODE" in
      create)
        THREAD_ID=$(discord_ensure_thread "$THREAD_NAME" "tmux session: ${THREAD_NAME} (${HOSTNAME_SHORT})")
        if [[ -n "$THREAD_ID" ]]; then
          discord_post_to_thread "$THREAD_ID" "Session \`${SESSION_NAME}\` started on \`${HOSTNAME_SHORT}\` · ${TIMESTAMP}"
        fi
        ;;
      delete)
        THREAD_ID=$(discord_find_thread "$THREAD_NAME")
        if [[ -n "$THREAD_ID" ]]; then
          discord_post_to_thread "$THREAD_ID" "Session \`${SESSION_NAME}\` closed on \`${HOSTNAME_SHORT}\` · ${TIMESTAMP}"
          discord_archive_thread "$THREAD_ID"
        fi
        ;;
    esac
  fi

  # --- Slack ---
  if [[ -n "${SLACK_BOT_TOKEN:-}" && -n "${SLACK_CHANNEL_ID:-}" ]]; then
    # shellcheck source=/dev/null
    source "${HOOK_DIR}/slack-lib.sh"

    case "$MODE" in
      create)
        THREAD_TS=$(slack_ensure_thread "$THREAD_NAME" "tmux session: ${THREAD_NAME} (${HOSTNAME_SHORT})")
        if [[ -n "$THREAD_TS" ]]; then
          slack_post_to_thread "$THREAD_TS" "Session \`${SESSION_NAME}\` started on \`${HOSTNAME_SHORT}\` · ${TIMESTAMP}"
        fi
        ;;
      delete)
        THREAD_TS=$(slack_find_thread "$THREAD_NAME")
        if [[ -n "$THREAD_TS" ]]; then
          slack_post_to_thread "$THREAD_TS" "Session \`${SESSION_NAME}\` closed on \`${HOSTNAME_SHORT}\` · ${TIMESTAMP}"
          slack_archive_thread "$THREAD_TS"
        fi
        ;;
    esac
  fi
) 1>/dev/null 2>/dev/null &

disown
exit 0
