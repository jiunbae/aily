#!/bin/bash
# Platform dispatcher: posts to all enabled notification platforms.
# Same interface as discord-post.sh / slack-post.sh.
# Usage: post.sh <agent_name> <message_text>
#    OR: post.sh --raw <pre_formatted_message>
#
# Auto-detects enabled platforms from available tokens in .notify-env.
# Override with NOTIFY_PLATFORMS="discord,slack" in .notify-env.
# Retries failed posts with exponential backoff.

set -euo pipefail

HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${AILY_ENV:-${XDG_CONFIG_HOME:-$HOME/.config}/aily/env}"
[[ -f "$ENV_FILE" ]] || ENV_FILE="${HOOK_DIR}/.notify-env"
if [[ ! -f "$ENV_FILE" ]]; then
  exit 0
fi

# shellcheck source=/dev/null
source "$ENV_FILE"

MAX_RETRIES="${NOTIFY_MAX_RETRIES:-2}"

# Determine enabled platforms
PLATFORMS="${NOTIFY_PLATFORMS:-}"
if [[ -z "$PLATFORMS" ]]; then
  # Auto-detect from available tokens
  PLATFORMS=""
  if [[ -n "${DISCORD_BOT_TOKEN:-}" && -n "${DISCORD_CHANNEL_ID:-}" ]]; then
    PLATFORMS="discord"
  fi
  if [[ -n "${SLACK_BOT_TOKEN:-}" && -n "${SLACK_CHANNEL_ID:-}" ]]; then
    PLATFORMS="${PLATFORMS:+${PLATFORMS},}slack"
  fi
fi

if [[ -z "$PLATFORMS" ]]; then
  exit 0
fi

# Post with retry
_post_with_retry() {
  local platform="$1"
  shift
  local attempt=0
  local delay=1

  while (( attempt <= MAX_RETRIES )); do
    if bash "${HOOK_DIR}/${platform}-post.sh" "$@" 2>/dev/null; then
      return 0
    fi
    attempt=$((attempt + 1))
    if (( attempt <= MAX_RETRIES )); then
      sleep "$delay"
      delay=$((delay * 2))
    fi
  done
  # Log failure (stderr only, non-blocking)
  echo "[aily] Failed to post to ${platform} after $((MAX_RETRIES + 1)) attempts" >&2
  return 1
}

# Dispatch to each enabled platform (in parallel, with retry)
IFS=',' read -ra PLATFORM_LIST <<< "$PLATFORMS"
for platform in "${PLATFORM_LIST[@]}"; do
  platform=$(echo "$platform" | tr -d ' ')
  case "$platform" in
    discord)
      _post_with_retry discord "$@" &
      ;;
    slack)
      _post_with_retry slack "$@" &
      ;;
  esac
done

wait
