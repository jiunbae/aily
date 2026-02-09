#!/bin/bash
# Platform dispatcher: posts to all enabled notification platforms.
# Same interface as discord-post.sh / slack-post.sh.
# Usage: post.sh <agent_name> <message_text>
#    OR: post.sh --raw <pre_formatted_message>
#
# Auto-detects enabled platforms from available tokens in .notify-env.
# Override with NOTIFY_PLATFORMS="discord,slack" in .notify-env.

set -euo pipefail

HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${HOOK_DIR}/.notify-env"
if [[ ! -f "$ENV_FILE" ]]; then
  exit 0
fi

# shellcheck source=/dev/null
source "$ENV_FILE"

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

# Dispatch to each enabled platform (in parallel)
IFS=',' read -ra PLATFORM_LIST <<< "$PLATFORMS"
for platform in "${PLATFORM_LIST[@]}"; do
  platform=$(echo "$platform" | tr -d ' ')
  case "$platform" in
    discord)
      bash "${HOOK_DIR}/discord-post.sh" "$@" &
      ;;
    slack)
      bash "${HOOK_DIR}/slack-post.sh" "$@" &
      ;;
  esac
done

wait
