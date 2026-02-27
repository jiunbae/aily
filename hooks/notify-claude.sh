#!/bin/bash
# Claude Code Notification hook entry point.
# Extracts last response from Claude Code JSONL, then posts to enabled platforms.
# Registered in ~/.claude/settings.json -> hooks.Notification

set -euo pipefail

HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HOOK_DIR/log.sh"

ENV_FILE="${AILY_ENV:-${XDG_CONFIG_HOME:-$HOME/.config}/aily/env}"
if [[ ! -f "$ENV_FILE" ]]; then
  exit 0
fi

# Require tmux: check env first, fall back to tmux query
if [[ -z "${TMUX:-}" ]] && ! tmux display-message -p '' >/dev/null 2>&1; then
  exit 0
fi

# Fork to background so hook returns immediately
(
  # Find the JSONL file and wait for it to be updated (up to 10s)
  jsonl=$(python3 -c "
import os, glob
cwd = os.environ.get('PWD', os.getcwd())
sanitized = cwd.replace('/', '-')
project_dir = os.path.expanduser(f'~/.claude/projects/{sanitized}')
pattern = os.path.join(project_dir, '*.jsonl')
files = glob.glob(pattern)
if files:
    print(max(files, key=os.path.getmtime))
" 2>/dev/null || echo "")

  if [[ -n "$jsonl" ]]; then
    mtime_before=$(stat -f%m "$jsonl" 2>/dev/null || echo 0)
    for i in $(seq 1 10); do
      sleep 1
      mtime_now=$(stat -f%m "$jsonl" 2>/dev/null || echo 0)
      if [[ "$mtime_now" != "$mtime_before" ]]; then
        _aily_log "DBG" "notify-claude: JSONL updated after ${i}s"
        break
      fi
    done
  else
    _aily_log "WARN" "notify-claude: no JSONL found, sleeping 5s fallback"
    sleep 5
  fi

  LAST_MESSAGE=$(python3 "${HOOK_DIR}/extract-last-message.py" 2>&1) || {
    _aily_log "ERR" "extract failed: $LAST_MESSAGE"
    LAST_MESSAGE=""
  }

  if [[ -n "$LAST_MESSAGE" ]]; then
    exec bash "${HOOK_DIR}/post.sh" "claude" "$LAST_MESSAGE"
  fi
) 1>/dev/null &

disown
exit 0
