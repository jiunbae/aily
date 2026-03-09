#!/bin/bash
# Detect and wrap terminal multiplexer commands for aily hooks.
# Source this file — do not execute it directly.
#
# Sets MUX_TYPE ("tmux" or "zellij") and provides wrapper functions:
#   mux_session_name      — get the current session name
#   mux_has_session       — check if a session exists
#   mux_list_sessions     — list all session names
#   mux_show_env          — get an environment variable from a session
#
# Detection priority:
#   1. AILY_MULTIPLEXER env var (explicit override)
#   2. ZELLIJ env var set -> zellij
#   3. TMUX env var set -> tmux
#   4. Default: tmux

if [[ -n "${AILY_MULTIPLEXER:-}" ]]; then
  MUX_TYPE="${AILY_MULTIPLEXER,,}"  # lowercase
elif [[ -n "${ZELLIJ:-}" ]]; then
  MUX_TYPE="zellij"
elif [[ -n "${TMUX:-}" ]]; then
  MUX_TYPE="tmux"
else
  MUX_TYPE="tmux"
fi

mux_session_name() {
  # Get the current session name (only works when attached)
  case "$MUX_TYPE" in
    zellij)
      # ZELLIJ_SESSION_NAME is set by zellij when attached
      echo "${ZELLIJ_SESSION_NAME:-}"
      ;;
    *)
      tmux display-message -p '#{session_name}' 2>/dev/null || echo ""
      ;;
  esac
}

mux_has_session() {
  # Check if a session exists. Returns 0 if yes, 1 if no.
  local name="$1"
  case "$MUX_TYPE" in
    zellij)
      zellij list-sessions 2>/dev/null | grep -q "^${name}"
      ;;
    *)
      tmux has-session -t "$name" 2>/dev/null
      ;;
  esac
}

mux_list_sessions() {
  # List session names, one per line.
  case "$MUX_TYPE" in
    zellij)
      zellij list-sessions 2>/dev/null | sed 's/ .*//'
      ;;
    *)
      tmux list-sessions -F '#{session_name}' 2>/dev/null
      ;;
  esac
}

mux_show_env() {
  # Get an environment variable from a session.
  # Args: session_name var_name
  # Outputs: value (or empty if not supported/not found)
  local session="$1"
  local var="$2"
  case "$MUX_TYPE" in
    zellij)
      # Zellij doesn't support session environment variables
      echo ""
      ;;
    *)
      tmux show-environment -t "$session" "$var" 2>/dev/null | grep -v '^-' | cut -d= -f2-
      ;;
  esac
}
