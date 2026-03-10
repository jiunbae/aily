#!/bin/bash
# aily demo recording script
# Records a split-screen demo: tmux (left) + Discord (right)
#
# Prerequisites:
#   brew install asciinema
#   brew install --cask obs     (for full screen recording)
#   pip install asciinema-agg   (for GIF conversion)
#
# Usage:
#   ./demo/record.sh            # Record terminal demo
#   ./demo/record.sh --render   # Convert to GIF

set -euo pipefail

DEMO_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(dirname "$DEMO_DIR")"
CAST_FILE="${DEMO_DIR}/demo.cast"
GIF_FILE="${REPO_DIR}/docs/demo.gif"
SVG_FILE="${REPO_DIR}/docs/demo.svg"

# Colors
GREEN='\033[0;32m'
CYAN='\033[0;36m'
DIM='\033[2m'
BOLD='\033[1m'
RESET='\033[0m'

# Simulated typing speed
TYPE_DELAY=0.04
LINE_DELAY=1.5

_type() {
  local text="$1"
  local delay="${2:-$TYPE_DELAY}"
  for ((i = 0; i < ${#text}; i++)); do
    printf '%s' "${text:$i:1}"
    sleep "$delay"
  done
}

_run() {
  local cmd="$1"
  local pause="${2:-$LINE_DELAY}"
  printf "${CYAN}\$ ${RESET}"
  _type "$cmd"
  sleep 0.3
  printf '\n'
  eval "$cmd" 2>/dev/null || true
  sleep "$pause"
}

_comment() {
  printf "\n${DIM}# %s${RESET}\n" "$1"
  sleep 0.8
}

_header() {
  printf "\n${BOLD}${GREEN}%s${RESET}\n\n" "$1"
  sleep 1
}

_discord_sim() {
  # Simulate Discord message arrival
  local from="$1"
  local msg="$2"
  printf "${DIM}  Discord > ${RESET}${BOLD}[%s]${RESET} %s\n" "$from" "$msg"
  sleep 0.5
}

# ─── Render mode ───
if [[ "${1:-}" == "--render" ]]; then
  echo "Converting recording to GIF..."
  if command -v agg >/dev/null 2>&1; then
    agg --cols 100 --rows 30 --speed 1.5 \
        --font-size 14 --theme monokai \
        "$CAST_FILE" "$GIF_FILE"
    echo "GIF saved to: $GIF_FILE"
  elif command -v svg-term >/dev/null 2>&1; then
    svg-term --in "$CAST_FILE" --out "$SVG_FILE" \
             --window --width 100 --height 30 --padding 10
    echo "SVG saved to: $SVG_FILE"
  else
    echo "Install agg (pip install asciinema-agg) or svg-term (npm i -g svg-term-cli)"
    exit 1
  fi
  exit 0
fi

# ─── Record mode ───
echo "Recording aily demo..."
echo "Press Ctrl+C to stop recording."
echo ""

# Check if asciinema is available
if command -v asciinema >/dev/null 2>&1; then
  asciinema rec "$CAST_FILE" \
    --cols 100 --rows 30 \
    --title "aily — AI agent session bridge" \
    --command "bash ${DEMO_DIR}/scenario.sh"
  echo ""
  echo "Recording saved to: $CAST_FILE"
  echo "Run './demo/record.sh --render' to convert to GIF"
else
  echo "asciinema not found. Running scenario directly..."
  bash "${DEMO_DIR}/scenario.sh"
fi
