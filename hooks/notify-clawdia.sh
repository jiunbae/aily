#!/bin/bash
# Backward-compatible wrapper: delegates to notify-claude.sh
# Keep this file so existing symlinks at ~/.claude/hooks/notify-clawdia.sh still work.
HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec bash "${HOOK_DIR}/notify-claude.sh"
