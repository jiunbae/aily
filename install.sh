#!/bin/bash
# Install notification hooks for Claude Code, Codex CLI, Gemini CLI, and OpenCode.
# Symlinks hook files and configures each agent.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HOOKS_DIR="$HOME/.claude/hooks"

echo "=== Installing notification hooks ==="
echo ""

# --- 1. Symlink hook files to ~/.claude/hooks/ ---
mkdir -p "$HOOKS_DIR"

for f in "$SCRIPT_DIR/hooks/"*; do
  # Only symlink hook files (avoid directories like __pycache__).
  if [[ ! -f "$f" ]]; then
    continue
  fi
  name=$(basename "$f")
  target="$HOOKS_DIR/$name"
  if [[ -e "$target" && ! -L "$target" ]]; then
    echo "  ⚠️  $target exists and is not a symlink, skipping"
    continue
  fi
  ln -sf "$f" "$target"
  echo "  ✓ $name"
done

# Check for config file
AILY_CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/aily"
AILY_ENV_FILE="${AILY_CONFIG_DIR}/env"

if [[ -f "$AILY_ENV_FILE" ]]; then
  chmod 600 "$AILY_ENV_FILE" 2>/dev/null || true
  echo "  ✓ Config: $AILY_ENV_FILE (chmod 600)"
  # shellcheck source=/dev/null
  source "$AILY_ENV_FILE" 2>/dev/null || true
else
  echo ""
  echo "  ⚠️  No config found. Run 'aily init' to set up."
fi

if [[ -n "${DISCORD_BOT_TOKEN:-}" && -n "${DISCORD_CHANNEL_ID:-}" ]]; then
  echo "  ✓ Discord: configured"
else
  echo "  · Discord: not configured (optional)"
fi
if [[ -n "${SLACK_BOT_TOKEN:-}" && -n "${SLACK_CHANNEL_ID:-}" ]]; then
  echo "  ✓ Slack: configured"
else
  echo "  · Slack: not configured (optional)"
fi

# --- 2. Claude Code ---
echo ""
echo "=== Claude Code ==="
CLAUDE_SETTINGS="$HOME/.claude/settings.json"
CLAUDE_HOOK_CMD="bash $HOOKS_DIR/notify-claude.sh"

if python3 - "$CLAUDE_SETTINGS" "$CLAUDE_HOOK_CMD" <<'PY' 2>/dev/null
import json
import sys
from pathlib import Path

settings_path = Path(sys.argv[1]).expanduser()
hook_cmd = sys.argv[2]

desired_hook = {
    "type": "command",
    "command": hook_cmd,
}

settings = {}
try:
    raw = settings_path.read_text(encoding="utf-8")
    settings = json.loads(raw) if raw.strip() else {}
except FileNotFoundError:
    settings = {}
except Exception:
    settings = {}

if not isinstance(settings, dict):
    settings = {}

hooks = settings.get("hooks")
if not isinstance(hooks, dict):
    hooks = {}
    settings["hooks"] = hooks

notification = hooks.get("Notification")
if notification is None:
    notification = []
elif not isinstance(notification, list):
    notification = []
hooks["Notification"] = notification

# Check if our hook already exists
def is_our_hook(h: dict) -> bool:
    cmd = h.get("command", "")
    return "notify-clawdia" in cmd or "notify-claude" in cmd

found = False
for group in notification:
    if not isinstance(group, dict):
        continue
    group_hooks = group.get("hooks")
    if not isinstance(group_hooks, list):
        continue
    for h in group_hooks:
        if not isinstance(h, dict):
            continue
        if is_our_hook(h):
            found = True
            break
    if found:
        break

if not found:
    notification.append({"hooks": [desired_hook]})
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")

PY
then
  echo "  ✓ Notification hook configured"
else
  echo "  ⚠️  Failed to update $CLAUDE_SETTINGS. Add manually:"
  echo "     bash $HOOKS_DIR/notify-claude.sh"
fi

# --- 3. Codex CLI ---
echo ""
echo "=== Codex CLI ==="
CODEX_CONFIG="$HOME/.codex/config.toml"
NOTIFY_PATH="$HOOKS_DIR/notify-codex.py"

if python3 - "$CODEX_CONFIG" "$NOTIFY_PATH" <<'PY' 2>/dev/null
import re
import sys
from pathlib import Path

config_path = Path(sys.argv[1]).expanduser()
notify_path = sys.argv[2]

desired = f'notify = ["python3", "{notify_path}"]\n'

text = ""
try:
    text = config_path.read_text(encoding="utf-8")
except FileNotFoundError:
    text = ""
except Exception:
    # Fall back to empty rather than failing install.
    text = ""

lines = text.splitlines(True)
out = []
replaced = False
pat = re.compile(r"^\s*notify\s*=")

for line in lines:
    if pat.match(line):
        if not replaced:
            out.append(desired)
            replaced = True
        continue
    out.append(line)

if not replaced:
    if out and not out[-1].endswith("\n"):
        out[-1] += "\n"
    if out and out[-1].strip() != "":
        out.append("\n")
    out.append(desired)

config_path.parent.mkdir(parents=True, exist_ok=True)
config_path.write_text("".join(out), encoding="utf-8")
PY
then
  echo "  ✓ Set notify hook in $CODEX_CONFIG"
else
  echo "  ⚠️  Failed to update $CODEX_CONFIG. Add manually:"
  echo "     notify = [\"python3\", \"$NOTIFY_PATH\"]"
fi

# --- 4. Gemini CLI ---
echo ""
echo "=== Gemini CLI ==="
GEMINI_SETTINGS="$HOME/.gemini/settings.json"
GEMINI_HOOK_CMD="$HOOKS_DIR/notify-gemini.sh"

if python3 - "$GEMINI_SETTINGS" "$GEMINI_HOOK_CMD" <<'PY' 2>/dev/null
import json
import sys
from pathlib import Path

settings_path = Path(sys.argv[1]).expanduser()
hook_cmd = sys.argv[2]

desired_hook = {
    "type": "command",
    "command": hook_cmd,
    "name": "discord-notify",
    "timeout": 10000,
}

settings = {}
try:
    raw = settings_path.read_text(encoding="utf-8")
    settings = json.loads(raw) if raw.strip() else {}
except FileNotFoundError:
    settings = {}
except Exception:
    settings = {}

if not isinstance(settings, dict):
    settings = {}

hooks = settings.get("hooks")
if not isinstance(hooks, dict):
    hooks = {}
    settings["hooks"] = hooks

after_agent = hooks.get("AfterAgent")
if after_agent is None:
    after_agent = []
elif isinstance(after_agent, dict):
    after_agent = [after_agent]
elif not isinstance(after_agent, list):
    after_agent = []
hooks["AfterAgent"] = after_agent

def is_our_hook(h: dict) -> bool:
    if h.get("name") == "discord-notify":
        return True
    cmd = h.get("command")
    return isinstance(cmd, str) and cmd.endswith("notify-gemini.sh")

found = False
for group in after_agent:
    if not isinstance(group, dict):
        continue
    group_hooks = group.get("hooks")
    if not isinstance(group_hooks, list):
        continue
    for h in group_hooks:
        if not isinstance(h, dict):
            continue
        if is_our_hook(h):
            h.clear()
            h.update(desired_hook)
            found = True
            break
    if found:
        break

if not found:
    after_agent.append({"hooks": [desired_hook]})

settings_path.parent.mkdir(parents=True, exist_ok=True)
settings_path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
PY
then
  echo "  ✓ Ensured AfterAgent hook in $GEMINI_SETTINGS"
else
  echo "  ⚠️  Failed to update $GEMINI_SETTINGS. Add manually:"
  echo "     \"command\": \"$GEMINI_HOOK_CMD\""
fi

# --- 5. OpenCode ---
echo ""
echo "=== OpenCode ==="
OPENCODE_PLUGINS="$HOME/.config/opencode/plugins"
OPENCODE_PLUGIN_SRC="$HOOKS_DIR/notify-opencode.mjs"
OPENCODE_PLUGIN_LINK="$OPENCODE_PLUGINS/aily-notify.mjs"

if [[ -f "$OPENCODE_PLUGIN_SRC" ]]; then
  mkdir -p "$OPENCODE_PLUGINS"
  ln -sf "$OPENCODE_PLUGIN_SRC" "$OPENCODE_PLUGIN_LINK"
  echo "  ✓ Linked plugin: $OPENCODE_PLUGIN_LINK"
else
  echo "  ⚠️  $OPENCODE_PLUGIN_SRC not found"
fi

# --- 6. tmux session hooks ---
echo ""
echo "=== tmux session hooks ==="
SYNC_SCRIPT="$HOOKS_DIR/thread-sync.sh"
if [[ -x "$SYNC_SCRIPT" ]]; then
  if command -v tmux >/dev/null 2>&1 && tmux list-sessions >/dev/null 2>&1; then
    session_count=$(tmux list-sessions 2>/dev/null | wc -l | tr -d ' ')

    # Ask before enabling auto-sync
    _reply="y"
    if [[ -e /dev/tty ]]; then
      printf "  Enable auto thread sync for tmux sessions? [Y/n]: " > /dev/tty
      read -r _reply < /dev/tty 2>/dev/null || _reply="y"
    fi

    if [[ "${_reply:-y}" =~ ^[Yy]$ ]]; then
      tmux set-hook -g session-created \
        "run-shell '${SYNC_SCRIPT} create #{session_name}'" 2>/dev/null && \
        echo "  ✓ session-created hook set" || \
        echo "  ⚠️  Failed to set session-created hook"

      tmux set-hook -g session-closed \
        "run-shell '${SYNC_SCRIPT} delete #{hook_session_name}'" 2>/dev/null && \
        echo "  ✓ session-closed hook set" || \
        echo "  ⚠️  Failed to set session-closed hook"

      # Offer to sync existing sessions
      if [[ "$session_count" -gt 0 ]]; then
        printf "  Sync %d existing tmux session(s)? [y/N]: " "$session_count" > /dev/tty
        read -r _reply2 < /dev/tty 2>/dev/null || _reply2="n"
        if [[ "${_reply2:-n}" =~ ^[Yy]$ ]]; then
          while IFS= read -r _sess; do
            "$SYNC_SCRIPT" create "$_sess" 2>/dev/null &
            echo "  ✓ Synced: $_sess"
          done < <(tmux list-sessions -F '#{session_name}' 2>/dev/null)
          wait
        else
          echo "  · Existing sessions skipped (threads created on next session)"
        fi
      fi
    else
      echo "  · Skipped (use 'aily auto on' to enable later)"
    fi
  else
    echo "  ⚠️  tmux not running. Start tmux first, then re-run install.sh"
  fi

  echo ""
  echo "  For persistence across tmux restarts, add to ~/.tmux.conf:"
  echo "    set-hook -g session-created \"run-shell '${SYNC_SCRIPT} create #{session_name}'\""
  echo "    set-hook -g session-closed \"run-shell '${SYNC_SCRIPT} delete #{hook_session_name}'\""
else
  echo "  ⚠️  thread-sync.sh not found or not executable"
fi

# --- 7. aily CLI ---
echo ""
echo "=== aily CLI ==="
AILY_BIN="$SCRIPT_DIR/aily"
AILY_LINK="$HOME/.local/bin/aily"
if [[ -x "$AILY_BIN" ]]; then
  mkdir -p "$HOME/.local/bin"
  ln -sf "$AILY_BIN" "$AILY_LINK"
  echo "  ✓ aily → $AILY_LINK"
  if ! echo "$PATH" | grep -q "$HOME/.local/bin"; then
    echo "  ⚠️  Add ~/.local/bin to your PATH if not already"
  fi
else
  echo "  ⚠️  aily script not found"
fi

# --- Summary ---
echo ""
echo "=== Done ==="
echo "  Claude Code: notify-claude.sh (via ~/.claude/settings.json)"
echo "  Codex CLI:   notify-codex.py  (via ~/.codex/config.toml)"
echo "  Gemini CLI:  notify-gemini.sh (via ~/.gemini/settings.json)"
echo "  OpenCode:    aily-notify.mjs  (via ~/.config/opencode/plugins)"
echo "  tmux:        thread-sync.sh (via tmux set-hook)"
echo "  CLI:         aily (start/stop/auto/sessions/status)"
echo ""
echo "Hooks post to [agent] <tmux-session-name> threads on all configured platforms."
echo "Run 'aily status' to see which platforms are enabled."
