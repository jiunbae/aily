#!/bin/bash
# Backward-compat wrapper: delegates to thread-sync.sh (multi-platform).
# Existing tmux hook configurations continue to work.
exec "$(dirname "${BASH_SOURCE[0]}")/thread-sync.sh" "$@"
