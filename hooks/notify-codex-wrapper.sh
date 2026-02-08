#!/bin/bash
# Codex CLI notify wrapper: chains oh-my-prompt + discord notification.
# Receives JSON as $1 (passed by Codex CLI).
# Registered in ~/.codex/config.toml:
#   notify = "bash /path/to/notify-codex-wrapper.sh"

JSON_ARG="${1:-}"
HOOK_DIR="$(dirname "$0")"

# 1. Call oh-my-prompt notify (existing)
OMP_NOTIFY="$HOME/.config/oh-my-prompt/hooks/codex/notify.js"
if [[ -f "$OMP_NOTIFY" ]]; then
  node "$OMP_NOTIFY" "$JSON_ARG" 2>/dev/null &
fi

# 2. Call discord notify
python3 "${HOOK_DIR}/notify-codex.py" "$JSON_ARG" 2>/dev/null &

wait
