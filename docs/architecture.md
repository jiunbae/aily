# Discord ↔ Claude Code Bidirectional Bridge

A system that connects Discord to Claude Code sessions running in tmux, enabling remote task management and automatic completion notifications.

## Overview

```
┌──────────┐     ┌───────────────┐     ┌─────────┐     ┌──────────────────┐
│  User on  │────▶│ agent-bridge  │────▶│  SSH    │────▶│ tmux sessions    │
│  Discord  │◀────│ (Python/WS)   │◀────│         │◀────│ (Claude Code)    │
└──────────┘     └───────────────┘     └─────────┘     └──────────────────┘
                                                        │
                                                        │ Notification Hook
                                                        ▼
                                                  ┌──────────────┐
                                                  │ Discord API  │
                                                  │ (Direct Post)│
                                                  └──────────────┘
```

There are two independent data flows:

1. **User → Claude Code** (via agent-bridge): User sends a message in a Discord `[agent]` thread → `agent-bridge.py` deterministically forwards it to the corresponding tmux session's Claude Code via SSH + `tmux send-keys`
2. **Claude Code → User** (via hook): Claude Code finishes a task → notification hook posts the response summary to the Discord `[agent]` thread

## Components

### 1. Notification Hook (`notify-clawdia.sh`)

**Trigger:** Claude Code's `Notification` event (fires when the AI finishes and the user appears idle)

**What it does:**
- Runs entirely in a background subshell to avoid Claude Code's hook timeout
- Detects the current tmux session via `TMUX_PANE` env var (not `tmux display-message -p '#S'` which returns the attached client's session, not the hook's session)
- Waits 5 seconds for Claude Code to flush its response to the session JSONL
- Extracts Claude's last response via `extract-last-message.py`
- Finds or creates a Discord thread named `[agent] <tmux-session>` in the configured channel
- Posts a formatted notification with host, project, timestamp, and Claude's response

**Key technical details:**
- Uses `( ... ) & disown; exit 0` pattern to fork to background
- Discord active threads must be queried via `/guilds/{guild_id}/threads/active` (the `/channels/{channel_id}/threads/active` endpoint returns 404)
- Thread search checks: active threads → archived threads → channel messages (thread metadata)
- Markdown tables in responses are auto-wrapped in code blocks (Discord doesn't render markdown tables natively)

### 2. Message Extractor (`extract-last-message.py`)

**Purpose:** Read Claude Code's session JSONL backwards to find the last meaningful assistant text response.

**How it works:**
- Locates the JSONL file at `~/.claude/projects/<sanitized-cwd>/*.jsonl`
- Reads the last 200 lines in reverse
- Finds `type: "assistant"` entries with text content blocks
- Strips English Coach header blocks (`--- > ... ---`)
- Converts markdown tables to code blocks for Discord
- Skips responses shorter than 20 characters
- Truncates at 1000 characters

### 3. Agent Bridge (`agent-bridge.py`)

**Role:** Deterministic forwarder — no AI involved. Connects to Discord gateway via WebSocket and forwards ALL messages in `[agent]` threads to the corresponding tmux session.

**Why deterministic?** Previously, the Clawdia bot (AI) was responsible for forwarding but it was unreliable — sometimes it would forward correctly, other times it would respond as a chatbot. Replacing with a deterministic script eliminated this inconsistency.

**How it works:**
- Connects to Discord gateway WebSocket with guild + message + message-content intents
- Listens for `MESSAGE_CREATE` events
- Checks if the message is in a thread with the `[agent]` prefix
- Extracts the tmux session name from the thread name
- Forwards via SSH + tmux send-keys (two-step — see Critical Implementation Notes)
- Posts captured terminal output back to the Discord thread

**Forwarding flow:**
1. Receives Discord message in an `[agent] <session>` thread
2. Finds which SSH host has the session: `ssh <host> 'tmux has-session -t <name>'`
3. Sends the message as keystrokes (two separate commands — critical for Claude Code):
   ```bash
   ssh <host> 'tmux send-keys -t <session> "<message>"'
   sleep 0.3
   ssh <host> 'tmux send-keys -t <session> Enter'
   ```
4. Waits 8 seconds, captures output: `ssh <host> 'tmux capture-pane -t <session> -p | tail -40'`
5. Posts captured output back to the Discord thread

**Runtime:** Runs as a persistent process (typically in a tmux session `agent-bridge` on jiun-mini). Uses aiohttp (not urllib, which gets 403 from Discord API).

**Configuration:**
- `#workspace` channel is set to `allow: false` in Clawdia bot's Discord config so the bot ignores it
- Agent bridge exclusively handles `[agent]` threads in that channel
- SSH hosts: `jiun-mini`, `jiun-mbp`

### 4. SSH Infrastructure

**K8s Pod → Local Machines:**

| Host | Access | Purpose |
|------|--------|---------|
| jiun-mini | `ssh jiun-mini` (host.internal) | OrbStack host, primary dev machine |
| jiun-mbp | `ssh jiun-mbp` (100.88.17.8 via Tailscale) | MacBook Pro, secondary dev |

- SSH key stored as K8s Secret `clawdia-ssh-key`
- Mounted at `/home/node/.ssh-keys/` in the pod
- SSH config created at startup in the container entrypoint
- `~/.zshenv` on both hosts ensures `/opt/homebrew/bin` is in PATH for non-interactive SSH

## Thread Naming Convention

Threads use the `[agent]` prefix to distinguish notification/bridge threads from regular conversation threads:

```
[agent] clawdbot     → tmux session "clawdbot"
[agent] vibe         → tmux session "vibe"
[agent] prompt-mgr   → tmux session "prompt-mgr"
```

The `[agent]` prefix serves two purposes:
1. The Clawdia bot recognizes it and activates bridge mode (forward messages, don't chat)
2. Users can visually distinguish agent threads from regular bot conversations

## Critical Implementation Notes

### tmux Session Detection (TMUX_PANE)

`tmux display-message -p '#S'` returns the session name of the **attached client**, not the session the hook is running in. If you have sessions `clawdbot` and `iac` and your terminal is attached to `clawdbot`, a hook running in `iac` would incorrectly report `clawdbot`.

**Fix:** Use the `TMUX_PANE` environment variable (set by tmux for every pane) to target the correct session:

```bash
# Correct: uses TMUX_PANE to find the session this pane belongs to
if [[ -n "${TMUX_PANE:-}" ]]; then
  TMUX_SESSION=$(tmux display-message -t "${TMUX_PANE}" -p '#{session_name}')
fi
```

The `-t "${TMUX_PANE}"` flag tells tmux to query the specific pane's session, not the attached client's session. Falls back to `display-message -p '#S'` when `TMUX_PANE` is not set.

### tmux send-keys and Claude Code

Claude Code's terminal input widget does NOT accept `Enter` when combined with text in a single `tmux send-keys` command. The Enter key gets interpreted as a newline (like Shift+Enter) instead of submit.

**Broken (Enter treated as newline):**
```bash
tmux send-keys -t session "message" Enter
```

**Working (two separate commands with delay):**
```bash
tmux send-keys -t session "message"
sleep 0.3 && tmux send-keys -t session Enter
```

### Discord API Gotchas

- **Active threads:** Use `/guilds/{guild_id}/threads/active`, NOT `/channels/{channel_id}/threads/active` (returns 404)
- **Guild ID:** Fetch from channel: `GET /channels/{channel_id}` → `guild_id` field
- **Thread filtering:** Filter by `parent_id` to match the specific channel when multiple channels have same-named threads
- **Thread creation:** Post a message first, then create a thread on that message
- **Unarchive:** `PATCH /channels/{thread_id}` with `{"archived": false}` before posting to an archived thread

### Hook Timeout

Claude Code hooks have a timeout. Long-running hooks get killed. Solution:

```bash
(
  # All work happens in this subshell
  sleep 5
  # ... API calls, message extraction, Discord posting
) &
disown
exit 0  # Hook returns immediately
```

### macOS Bash

macOS ships with Bash 3.x which doesn't support negative indices in substring expansion:
```bash
# Fails on macOS
"${var:1:-1}"

# Works on macOS
"${var:1:${#var}-2}"
```

## File Layout

```
~/.claude/
├── settings.json          # Hook configuration (Notification event)
└── hooks/
    ├── notify-clawdia.sh  # Symlink → claude-hooks repo
    ├── extract-last-message.py  # Symlink → claude-hooks repo
    └── .notify-env        # Secrets (not in repo)

claude-hooks/              # GitHub repo
├── hooks/
│   ├── notify-clawdia.sh
│   └── extract-last-message.py
├── agent-bridge.py        # Deterministic Discord ↔ tmux forwarder
├── docs/
│   └── architecture.md    # This document
├── .env.example
├── .gitignore
├── .venv/                 # Python venv (aiohttp dep for agent-bridge)
├── install.sh
└── README.md
```

## Setup Checklist

### Notification Hook (Claude Code → Discord)
- [ ] Clone repo: `git clone https://github.com/jiunbae/claude-hooks.git`
- [ ] Run `./install.sh` to symlink hooks
- [ ] Create `~/.claude/hooks/.notify-env` with Discord bot token and channel ID
- [ ] Add `Notification` hook to `~/.claude/settings.json`
- [ ] Ensure tmux is running (hook only fires inside tmux sessions)

### Agent Bridge (Discord → Claude Code)
- [ ] Set up Python venv: `python3 -m venv .venv && .venv/bin/pip install aiohttp`
- [ ] Configure `.notify-env` with `DISCORD_BOT_TOKEN` and `DISCORD_CHANNEL_ID`
- [ ] Ensure SSH access from bridge host to tmux hosts (`jiun-mini`, `jiun-mbp`)
- [ ] Run: `.venv/bin/python agent-bridge.py` (typically in a dedicated tmux session)
- [ ] Disable the `#workspace` channel in the Clawdia bot's Discord config (`allow: false`) to avoid conflicts
