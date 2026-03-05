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
  MESSAGE_TEXT="${2:-}"
fi

ENV_FILE="${AILY_ENV:-${XDG_CONFIG_HOME:-$HOME/.config}/aily/env}"
if [[ ! -f "$ENV_FILE" ]]; then
  exit 0
fi

# shellcheck source=/dev/null
source "$ENV_FILE"

TMUX_SESSION=$(tmux display-message -p '#{session_name}' 2>/dev/null || echo "")
if [[ -z "$TMUX_SESSION" ]]; then
  exit 0
fi

HOSTNAME_SHORT=$(hostname -s 2>/dev/null || hostname)
PROJECT=$(basename "${PWD:-unknown}")
TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')

_DEFAULT_FMT='[agent] {session} - {host}'
THREAD_NAME="${THREAD_NAME_FORMAT:-$_DEFAULT_FMT}"
THREAD_NAME="${THREAD_NAME//\{session\}/$TMUX_SESSION}"
THREAD_NAME="${THREAD_NAME//\{host\}/$HOSTNAME_SHORT}"
