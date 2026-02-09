#!/bin/bash
# tmux hook: sync Discord threads on session create/close.
# Usage: discord-thread-sync.sh create <session_name>
#        discord-thread-sync.sh delete <session_name>
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
if [[ "$SESSION_NAME" == "agent-bridge" ]]; then
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

  if [[ -z "${DISCORD_BOT_TOKEN:-}" || -z "${DISCORD_CHANNEL_ID:-}" ]]; then
    exit 0
  fi

  # Check if thread sync is enabled (default: true)
  if [[ "${TMUX_THREAD_SYNC:-true}" == "false" ]]; then
    exit 0
  fi

  # shellcheck source=/dev/null
  source "${HOOK_DIR}/discord-lib.sh"

  THREAD_NAME="[agent] ${SESSION_NAME}"
  HOSTNAME_SHORT=$(hostname -s 2>/dev/null || hostname)
  TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')

  case "$MODE" in
    create)
      THREAD_ID=$(discord_ensure_thread "$THREAD_NAME" "🖥 **tmux session: ${THREAD_NAME}** (${HOSTNAME_SHORT})")
      if [[ -n "$THREAD_ID" ]]; then
        discord_post_to_thread "$THREAD_ID" "▶️ Session \`${SESSION_NAME}\` started on \`${HOSTNAME_SHORT}\` · ${TIMESTAMP}"
      fi
      ;;
    delete)
      THREAD_ID=$(discord_find_thread "$THREAD_NAME")
      if [[ -n "$THREAD_ID" ]]; then
        discord_post_to_thread "$THREAD_ID" "⏹️ Session \`${SESSION_NAME}\` closed on \`${HOSTNAME_SHORT}\` · ${TIMESTAMP}"
        discord_archive_thread "$THREAD_ID"
      fi
      ;;
  esac
) 1>/dev/null 2>/dev/null &

disown
exit 0
