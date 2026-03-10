#!/bin/bash
# aily demo scenario — automated typing simulation
# This script is recorded by asciinema or run standalone

set -euo pipefail

# ─── Helpers ───

TYPE_DELAY=0.04

_type() {
  local text="$1"
  for ((i = 0; i < ${#text}; i++)); do
    printf '%s' "${text:$i:1}"
    sleep "$TYPE_DELAY"
  done
}

_prompt() {
  printf '\033[0;36m$ \033[0m'
}

_run() {
  _prompt
  _type "$1"
  sleep 0.3
  printf '\n'
}

_output() {
  printf '%s\n' "$1"
  sleep 0.3
}

_pause() {
  sleep "${1:-1.5}"
}

_header() {
  printf '\n\033[1;35m  %s\033[0m\n\n' "$1"
  sleep 1.2
}

_discord() {
  # Simulated Discord thread message
  printf '\033[2m  ╭─ Discord #ai-agents ─────────────────────────╮\033[0m\n'
  printf '\033[2m  │\033[0m %-47s \033[2m│\033[0m\n' "$1"
  if [[ -n "${2:-}" ]]; then
    printf '\033[2m  │\033[0m %-47s \033[2m│\033[0m\n' "$2"
  fi
  if [[ -n "${3:-}" ]]; then
    printf '\033[2m  │\033[0m %-47s \033[2m│\033[0m\n' "$3"
  fi
  printf '\033[2m  ╰────────────────────────────────────────────────╯\033[0m\n'
  sleep 0.8
}

_tmux_pane() {
  printf '\033[2m  ┌─ tmux: %s ──────────────────────────────────┐\033[0m\n' "$1"
  shift
  for line in "$@"; do
    printf '\033[2m  │\033[0m %-47s \033[2m│\033[0m\n' "$line"
  done
  printf '\033[2m  └────────────────────────────────────────────────┘\033[0m\n'
  sleep 0.8
}

# ─── Title ───

clear
printf '\n'
printf '\033[1;34m   ╔══════════════════════════════════════════════════╗\033[0m\n'
printf '\033[1;34m   ║\033[0m  \033[1maily\033[0m — AI agent session bridge               \033[1;34m║\033[0m\n'
printf '\033[1;34m   ║\033[0m  Your agents work in tmux.                      \033[1;34m║\033[0m\n'
printf '\033[1;34m   ║\033[0m  You work in Discord.                           \033[1;34m║\033[0m\n'
printf '\033[1;34m   ╚══════════════════════════════════════════════════╝\033[0m\n'
_pause 2

# ─── Scene 1: Create session from Discord ───

_header "Scene 1: Create a session from Discord"

_discord \
  "🧑 You:  !new my-agent" \
  "🤖 aily: ✓ Created tmux session 'my-agent'" \
  "         ✓ Thread created: [agent] my-agent"

_pause 1

_tmux_pane "my-agent" \
  "$ claude" \
  "╭─────────────────────────────────╮" \
  "│ Claude Code                     │" \
  "│ What would you like to work on? │" \
  "╰─────────────────────────────────╯"

_pause 1.5

# ─── Scene 2: Agent works, output relayed ───

_header "Scene 2: Agent works → output flows to Discord"

_tmux_pane "my-agent" \
  "> Analyzing codebase..." \
  "> Reading src/api/handlers.ts" \
  "> Found 3 issues to fix" \
  "> Writing fix for input validation..."

_pause 0.5

_discord \
  "🤖 Claude: Found 3 issues in handlers.ts." \
  "   Fixed input validation on /api/users" \
  "   endpoint. Ready for review."

_pause 1.5

# ─── Scene 3: Agent asks a question → Reply from Discord ───

_header "Scene 3: Agent asks → Reply from your phone"

_tmux_pane "my-agent" \
  "Claude: Should I also add rate limiting" \
  "        to the /api/users endpoint?" \
  "        [yes/no]" \
  ""

_pause 0.5

_discord \
  "🤖 Claude: Should I also add rate" \
  "   limiting to /api/users? [yes/no]" \
  ""

_pause 1

printf '\033[0;33m  📱 You reply from your phone:\033[0m\n'
_pause 0.5

_discord \
  "🧑 You:  yes, use 60 req/min" \
  "" \
  ""

_pause 0.5

printf '\033[0;32m  → Forwarded to tmux via SSH\033[0m\n'
_pause 0.5

_tmux_pane "my-agent" \
  "> yes, use 60 req/min" \
  "Claude: Got it. Adding rate limiter..." \
  "> Writing src/middleware/rate-limit.ts" \
  "> Done. 4 files changed."

_pause 1.5

# ─── Scene 4: Manage sessions ───

_header "Scene 4: Manage sessions"

_run "aily sessions"
_output "  HOST        SESSION      STATUS    THREAD"
_output "  localhost   my-agent     active    [agent] my-agent"
_output "  localhost   backend      active    [agent] backend"
_output "  my-server   deploy       idle      [agent] deploy"

_pause 1

_run "aily attach my-agent"
_output "  Attaching to tmux session 'my-agent'..."
_output "  (you're now in the same terminal the agent sees)"

_pause 1.5

# ─── Scene 5: Dashboard ───

_header "Scene 5: Web Dashboard"

_run "aily dashboard start"
_output "  ✓ Dashboard started on port 8080 (pid: 42891)"
_output "  URL: http://localhost:8080"

_pause 0.5

printf '\033[2m  ┌─ Dashboard ─────────────────────────────────────┐\033[0m\n'
printf '\033[2m  │\033[0m  Sessions (3)       \033[0;32m● active\033[0m                  \033[2m│\033[0m\n'
printf '\033[2m  │\033[0m  ┌──────────────────────────────────────┐   \033[2m│\033[0m\n'
printf '\033[2m  │\033[0m  │ my-agent  \033[0;32m●\033[0m  localhost   2m ago    │   \033[2m│\033[0m\n'
printf '\033[2m  │\033[0m  │ backend   \033[0;32m●\033[0m  localhost   5m ago    │   \033[2m│\033[0m\n'
printf '\033[2m  │\033[0m  │ deploy    \033[0;33m○\033[0m  my-server   1h ago    │   \033[2m│\033[0m\n'
printf '\033[2m  │\033[0m  └──────────────────────────────────────┘   \033[2m│\033[0m\n'
printf '\033[2m  │\033[0m  Real-time messages • Session control       \033[2m│\033[0m\n'
printf '\033[2m  └────────────────────────────────────────────────────┘\033[0m\n'

_pause 2

# ─── Ending ───

printf '\n'
printf '\033[1;34m   ╔══════════════════════════════════════════════════╗\033[0m\n'
printf '\033[1;34m   ║\033[0m                                                  \033[1;34m║\033[0m\n'
printf '\033[1;34m   ║\033[0m  \033[1mgit clone https://github.com/jiunbae/aily\033[0m      \033[1;34m║\033[0m\n'
printf '\033[1;34m   ║\033[0m  \033[1mcd aily && ./aily init\033[0m                          \033[1;34m║\033[0m\n'
printf '\033[1;34m   ║\033[0m                                                  \033[1;34m║\033[0m\n'
printf '\033[1;34m   ╚══════════════════════════════════════════════════╝\033[0m\n'
printf '\n'

_pause 3
