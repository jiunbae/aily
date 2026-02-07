# Discord ↔ Claude Code Bidirectional Bridge

A system that connects Discord to Claude Code sessions running in tmux, enabling remote task management and automatic completion notifications.

## Overview

```
┌──────────┐     ┌──────────────┐     ┌─────────┐     ┌──────────────────┐
│  User on  │────▶│  Clawdia Bot  │────▶│  SSH    │────▶│ tmux sessions    │
│  Discord  │◀────│  (K8s Pod)    │◀────│         │◀────│ (Claude Code)    │
└──────────┘     └──────────────┘     └─────────┘     └──────────────────┘
                                                        │
                                                        │ Notification Hook
                                                        ▼
                                                  ┌──────────────┐
                                                  │ Discord API  │
                                                  │ (Direct Post)│
                                                  └──────────────┘
```

There are two independent data flows:

1. **User → Claude Code** (via bot): User sends a message in a Discord `[agent]` thread → Clawdia bot forwards it to the corresponding tmux session's Claude Code via SSH + `tmux send-keys`
2. **Claude Code → User** (via hook): Claude Code finishes a task → notification hook posts the response summary to the Discord `[agent]` thread

## Components

### 1. Notification Hook (`notify-clawdia.sh`)

**Trigger:** Claude Code's `Notification` event (fires when the AI finishes and the user appears idle)

**What it does:**
- Runs entirely in a background subshell to avoid Claude Code's hook timeout
- Extracts the current tmux session name
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

### 3. Clawdia Bot (OpenClaw on K8s)

**Role:** Receives Discord messages in `[agent]` threads and forwards them to the corresponding tmux session.

**Configuration:**
- AGENTS.md contains the `[agent] Threads: tmux Session Bridge` rule
- TOOLS.md contains SSH host details and tmux command patterns
- Bot has SSH access to `jiun-mini` (via `host.internal`) and `jiun-mbp` (via Tailscale `100.88.17.8`)

**Forwarding flow:**
1. Bot receives a message in a thread starting with `[agent]`
2. Extracts tmux session name from the thread name
3. Checks which host has the session: `ssh <host> 'tmux has-session -t <name>'`
4. Sends the message as keystrokes (two separate commands — critical for Claude Code):
   ```bash
   ssh <host> 'tmux send-keys -t <session> "<message>"'
   ssh <host> 'sleep 0.3 && tmux send-keys -t <session> Enter'
   ```
5. Waits, captures output: `ssh <host> 'tmux capture-pane -t <session> -p | tail -40'`
6. Posts captured output back to the Discord thread

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
├── docs/
│   └── architecture.md    # This document
├── .env.example
├── .gitignore
├── install.sh
└── README.md
```

## Setup Checklist

- [ ] Clone repo: `git clone https://github.com/jiunbae/claude-hooks.git`
- [ ] Run `./install.sh` to symlink hooks
- [ ] Create `~/.claude/hooks/.notify-env` with Discord bot token and channel ID
- [ ] Add `Notification` hook to `~/.claude/settings.json`
- [ ] Ensure tmux is running (hook only fires inside tmux sessions)
- [ ] For bidirectional flow: configure bot's AGENTS.md with `[agent]` thread bridge rules
- [ ] For bidirectional flow: ensure SSH access from bot to tmux hosts
