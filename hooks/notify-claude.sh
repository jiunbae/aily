#!/bin/bash
# Claude Code Notification hook entry point.
# Extracts last response from Claude Code JSONL, then posts to enabled platforms.
# Registered in ~/.claude/settings.json → hooks.Notification

set -euo pipefail

HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

ENV_FILE="${HOOK_DIR}/.notify-env"
if [[ ! -f "$ENV_FILE" ]]; then
  exit 0
fi
if [[ -z "${TMUX:-}" && -z "${TMUX_PANE:-}" ]]; then
  exit 0
fi

# Fork to background so hook returns immediately
(
  # Wait for Claude Code to flush response to JSONL
  sleep 5

  LAST_MESSAGE=$(python3 "${HOOK_DIR}/extract-last-message.py" 2>/dev/null || echo "")
  exec bash "${HOOK_DIR}/post.sh" "claude" "$LAST_MESSAGE"
) 1>/dev/null &

disown
exit 0
