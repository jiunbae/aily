#!/bin/bash
# Claude Code hook: forward AskUserQuestion prompts to enabled platforms
# Triggered by PreToolUse event with AskUserQuestion matcher
# Reads tool input from stdin, posts formatted choices via discord-post.sh

set -euo pipefail

# Read stdin BEFORE forking (not available in background)
TOOL_INPUT=$(cat)

ENV_FILE="$(dirname "$0")/.notify-env"
if [[ ! -f "$ENV_FILE" ]]; then
  exit 0
fi

HOOK_DIR="$(dirname "$0")"

# Fork to background so hook returns immediately
(
  HOSTNAME=$(hostname -s)
  PROJECT=$(basename "${PWD:-unknown}")
  TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')

  # Format the question
  FORMATTED=$(echo "$TOOL_INPUT" | \
    HOSTNAME="$HOSTNAME" PROJECT="$PROJECT" TIMESTAMP="$TIMESTAMP" \
    python3 "${HOOK_DIR}/format-question.py" 2>/dev/null || echo "")

  if [[ -z "$FORMATTED" ]]; then
    exit 0
  fi

  exec bash "${HOOK_DIR}/post.sh" --raw "$FORMATTED"
) &

# Return immediately - background process handles the notification
disown
exit 0
