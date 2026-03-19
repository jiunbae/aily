#!/bin/bash
# Common boilerplate for discord-post.sh and slack-post.sh
# Source this, don't execute it.

RAW_MODE=false
if [[ "${1:-}" == "--raw" ]]; then
  RAW_MODE=true
  RAW_MESSAGE="${2:-}"
  AGENT_NAME="raw"
  MESSAGE_TEXT=""
else
  AGENT_NAME="${1:-unknown}"
  [[ "$AGENT_NAME" =~ ^[a-zA-Z0-9_.-]+$ ]] || AGENT_NAME="unknown"
  MESSAGE_TEXT="${2:-}"
fi

ENV_FILE="${AILY_ENV:-${XDG_CONFIG_HOME:-$HOME/.config}/aily/env}"
if [[ ! -f "$ENV_FILE" ]]; then
  exit 0
fi

# shellcheck source=/dev/null
source "$ENV_FILE"

# Source multiplexer detection helper
HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=mux-detect.sh
source "${HOOK_DIR}/mux-detect.sh"

MUX_SESSION=$(mux_session_name)
if [[ -z "$MUX_SESSION" ]]; then
  echo "[aily] no multiplexer session detected, skipping notification" >&2
  exit 0
fi

# Backward-compatible alias
TMUX_SESSION="$MUX_SESSION"

HOSTNAME_SHORT=$(hostname -s 2>/dev/null || hostname)
PROJECT=$(basename "${PWD:-unknown}")
TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')

_DEFAULT_FMT='[agent] {session} - {host}'
THREAD_NAME="${THREAD_NAME_FORMAT:-$_DEFAULT_FMT}"
THREAD_NAME="${THREAD_NAME//\{session\}/$MUX_SESSION}"
THREAD_NAME="${THREAD_NAME//\{host\}/$HOSTNAME_SHORT}"
