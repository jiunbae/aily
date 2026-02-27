#!/bin/bash
# Shared logging helper for aily hooks.
# Source this file in any hook: source "$HOOK_DIR/log.sh"

_AILY_LOG_FILE="${XDG_DATA_HOME:-$HOME/.local/share}/aily/logs/hooks.log"

_aily_log() {
  local level="$1"; shift
  local log="$_AILY_LOG_FILE"
  mkdir -p "$(dirname "$log")"

  # Auto-rotate: truncate to last 500 lines when > 1000
  if [[ -f "$log" ]]; then
    local lines
    lines=$(wc -l < "$log" 2>/dev/null || echo 0)
    if (( lines > 1000 )); then
      local tmp="${log}.tmp"
      tail -500 "$log" > "$tmp" 2>/dev/null && mv "$tmp" "$log" 2>/dev/null || rm -f "$tmp"
    fi
  fi

  printf '%s [%s] %s\n' "$(date '+%H:%M:%S')" "$level" "$*" >> "$log"
}
