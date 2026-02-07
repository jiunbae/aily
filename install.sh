#!/bin/bash
# Install Claude Code hooks by symlinking into ~/.claude/hooks/
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HOOKS_DIR="$HOME/.claude/hooks"

mkdir -p "$HOOKS_DIR"

# Symlink hook files
for f in "$SCRIPT_DIR/hooks/"*; do
  name=$(basename "$f")
  target="$HOOKS_DIR/$name"
  if [[ -e "$target" && ! -L "$target" ]]; then
    echo "⚠️  $target exists and is not a symlink, skipping (backup first if needed)"
    continue
  fi
  ln -sf "$f" "$target"
  echo "✓ $name → $target"
done

# Check for .notify-env
if [[ ! -f "$HOOKS_DIR/.notify-env" ]]; then
  echo ""
  echo "⚠️  No .notify-env found. Copy the example and fill in your tokens:"
  echo "  cp $SCRIPT_DIR/.env.example $HOOKS_DIR/.notify-env"
  echo "  chmod 600 $HOOKS_DIR/.notify-env"
else
  echo "✓ .notify-env already exists"
fi

# Verify Claude Code settings
SETTINGS="$HOME/.claude/settings.json"
if [[ -f "$SETTINGS" ]]; then
  if grep -q "notify-clawdia" "$SETTINGS" 2>/dev/null; then
    echo "✓ Notification hook already configured in settings.json"
  else
    echo ""
    echo "⚠️  Add this to $SETTINGS under hooks.Notification:"
    echo '  {"hooks": [{"type": "command", "command": "bash ~/.claude/hooks/notify-clawdia.sh", "statusMessage": "Notifying..."}]}'
  fi
fi

echo ""
echo "Done!"
