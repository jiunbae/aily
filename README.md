<div align="center">

<img src="docs/banner.svg" alt="aily — AI notification relay" width="700"/>

<br/>
<br/>

Connect your AI coding agents to Discord and Slack — get notified when tasks complete, see interactive prompts remotely, and send commands back.

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Claude Code](https://img.shields.io/badge/Claude_Code-hook-blueviolet)](https://docs.anthropic.com/en/docs/claude-code)
[![Codex CLI](https://img.shields.io/badge/Codex_CLI-hook-green)](https://github.com/openai/codex)
[![Gemini CLI](https://img.shields.io/badge/Gemini_CLI-hook-orange)](https://github.com/google-gemini/gemini-cli)

</div>

---

## How it works

```
                    aily
  ┌──────────────────────────────────────────────┐
  │                                              │
  │   Agent finishes task                        │
  │       │                                      │
  │       ▼                                      │
  │   ┌──────────────┐   ┌──────────┐           │
  │   │ Notification  │──▶│ post.sh  │──┬──▶  Discord Thread
  │   │ Hook          │   │(dispatch)│  │    [agent] my-session
  │   └──────────────┘   └──────────┘  │
  │                                     └──▶  Slack Thread
  │   Agent asks question                    [agent] my-session
  │       │                                      │
  │       ▼                                      │
  │   ┌──────────────┐   ┌──────────┐           │
  │   │ PreToolUse   │──▶│ format + │──┬────────┘
  │   │ Hook         │   │ post     │  └──▶  (both platforms)
  │   └──────────────┘   └──────────┘
  │                                              │
  └──────────────────────────────────────────────┘

  bridges (optional, bidirectional)
  ┌──────────────────────────────────────────────┐
  │                                              │
  │   Discord / Slack ──▶ SSH ──▶ tmux send-keys│
  │   message in thread      ──▶ agent input    │
  │                                              │
  └──────────────────────────────────────────────┘
```

When an AI agent finishes a task, aily:

1. Extracts the agent's last response
2. Finds or creates a thread `[agent] <tmux-session>` on all configured platforms
3. Posts a formatted summary

Each tmux session gets its own thread — notifications stay organized across projects and machines. Configure one platform or both.

### Notification example

```
🔔 Task Complete (claude)

🖥 Host: dev-server
📁 Project: my-app
⏰ Time: 2026-02-07 22:01:38

Response:
Fixed the bug in auth.ts by updating the token validation logic...
```

### Interactive prompt forwarding

When Claude Code asks you a question (`AskUserQuestion`), aily forwards the choices immediately:

```
❓ Waiting for Input

📋 Approach
Which pattern should we use for the API client?

1️⃣ Singleton
   Single shared instance, simpler but less testable

2️⃣ Factory
   Create instances per-request, more flexible

3️⃣ Dependency injection
   Register in container, best for testing

💬 Reply with option number (1, 2, 3) or type a custom answer
```

## Supported agents

| Agent | Hook type | Extractor |
|-------|-----------|-----------|
| **Claude Code** | `Notification` + `PreToolUse` | JSONL session parser |
| **Codex CLI** | `notify` | stdin message |
| **Gemini CLI** | `AfterAgent` | stdin JSON |

## Quick start

```bash
# Clone and install
git clone https://github.com/your-user/aily.git
cd aily
./install.sh

# Configure credentials
cp .env.example ~/.claude/hooks/.notify-env
chmod 600 ~/.claude/hooks/.notify-env
```

Edit `~/.claude/hooks/.notify-env` with your platform credentials (one or both):

```env
# Discord
DISCORD_BOT_TOKEN="your-bot-token"
DISCORD_CHANNEL_ID="your-channel-id"

# Slack
SLACK_BOT_TOKEN="xoxb-your-slack-bot-token"
SLACK_APP_TOKEN="xapp-your-slack-app-level-token"
SLACK_CHANNEL_ID="C0123456789"
```

Platforms are auto-detected from available tokens. Run `aily status` to verify.

The install script automatically:
- Symlinks all hooks into `~/.claude/hooks/`
- Configures `~/.codex/config.toml` for Codex CLI
- Configures `~/.gemini/settings.json` for Gemini CLI
- Sets tmux hooks for auto thread creation/archival on session start/close

### Configure Claude Code

Add to `~/.claude/settings.json`:

```json
{
  "hooks": {
    "Notification": [
      {
        "hooks": [{
          "type": "command",
          "command": "bash ~/.claude/hooks/notify-claude.sh",
          "statusMessage": "Notifying..."
        }]
      }
    ],
    "PreToolUse": [
      {
        "matcher": "AskUserQuestion",
        "hooks": [{
          "type": "command",
          "command": "bash ~/.claude/hooks/ask-question-notify.sh",
          "statusMessage": "Forwarding question to Discord..."
        }]
      }
    ]
  }
}
```

<details>
<summary><b>Codex CLI configuration</b></summary>

Add to `~/.codex/config.toml`:

```toml
notify = ["python3", "~/.claude/hooks/notify-codex.py"]
```

If you use [oh-my-prompt](https://github.com/nichochar/oh-my-prompt), chain both hooks:

```toml
notify = "bash ~/.claude/hooks/notify-codex-wrapper.sh"
```

</details>

<details>
<summary><b>Gemini CLI configuration</b></summary>

Add to `~/.gemini/settings.json`:

```json
{
  "hooks": {
    "AfterAgent": [
      {
        "hooks": [{
          "type": "command",
          "command": "~/.claude/hooks/notify-gemini.sh",
          "name": "discord-notify",
          "timeout": 10000
        }]
      }
    ]
  }
}
```

</details>

## Session lifecycle

### aily CLI

Manage threads and tmux session sync from the command line:

```bash
aily start [name]     # Create thread for current/named tmux session
aily stop [name]      # Archive thread for current/named tmux session
aily auto [on|off]    # Toggle auto thread sync (or show status)
aily sessions         # List tmux sessions across hosts
aily status           # Show which platforms are configured
aily help             # Show help
```

When run inside tmux without a name argument, `aily start` and `aily stop` auto-detect the current session.

New threads receive a welcome message with available commands:

```
Welcome to [agent] my-session

Type a message here to forward it to the tmux session.

Commands:
  !sessions — list all sessions
  !kill my-session — kill this session + archive thread
```

### Auto-sync (tmux hooks)

When `install.sh` runs, it sets tmux global hooks:
- **Session created** -> Discord thread created + welcome message
- **Session closed** -> "Session closed" message + thread archived

Toggle auto-sync without editing config files:
```bash
aily auto off   # disable auto thread creation/archival
aily auto on    # re-enable
aily auto       # show current status
```

For persistence across tmux restarts, add to `~/.tmux.conf`:
```bash
set-hook -g session-created "run-shell '~/.claude/hooks/thread-sync.sh create #{session_name}'"
set-hook -g session-closed  "run-shell '~/.claude/hooks/thread-sync.sh delete #{hook_session_name}'"
```

### Bridge commands

The agent bridges support `!` commands in the workspace channel or any thread:

| Command | Action |
|---------|--------|
| `!new <name> [host]` | Create tmux session + thread |
| `!kill <name>` | Kill tmux session + archive thread |
| `!sessions` | List all sessions with sync status |

```
> !sessions
  my-project            host-1                   synced
  dev-api               host-1                   synced
  data-pipeline         host-2                   no thread
  old-experiment        ---                      orphan thread
```

## Architecture

```
aily/
├── hooks/
│   ├── post.sh                     # Multi-platform dispatcher
│   ├── discord-lib.sh              # Discord API functions
│   ├── discord-post.sh             # Discord message posting
│   ├── slack-lib.sh                # Slack API functions
│   ├── slack-post.sh               # Slack message posting
│   ├── thread-sync.sh              # tmux session lifecycle (multi-platform)
│   ├── notify-claude.sh            # Claude Code notification hook
│   ├── notify-codex.py             # Codex CLI notification hook
│   ├── notify-gemini.sh            # Gemini CLI notification hook
│   ├── ask-question-notify.sh      # AskUserQuestion prompt forwarder
│   ├── format-question.py          # Formats interactive prompts
│   └── extract-last-message.py     # JSONL session response extractor
├── aily                            # CLI tool (start/stop/auto/sessions/status)
├── agent-bridge.py                 # Discord ↔ tmux bridge + ! commands
├── slack-bridge.py                 # Slack ↔ tmux bridge + ! commands
├── install.sh                      # One-command setup
└── docs/
    └── architecture.md             # Detailed technical docs
```

### Key design decisions

- **Background execution** — All hooks fork to a background subshell (`( ... ) & disown; exit 0`) and return immediately to avoid agent hook timeouts
- **Thread-per-session** — Each tmux session gets a dedicated thread (`[agent] <session-name>`) on each platform
- **Multi-platform dispatch** — `post.sh` auto-detects enabled platforms from tokens and dispatches in parallel
- **Lifecycle coupling** — tmux hooks auto-create/archive threads; `!` commands manage both from messaging platforms
- **Hash-based dedup** — Prevents duplicate notifications when the same response triggers multiple hook events
- **Interactive suppression** — When an `AskUserQuestion` prompt is active, task-complete notifications are suppressed to avoid stale messages

## Multi-machine setup

Each machine needs its own clone and `.notify-env`. The install script handles symlinking and agent config automatically.

```bash
# On a new machine
git clone https://github.com/your-user/aily.git ~/workspace-ext/aily
cd ~/workspace-ext/aily
./install.sh

cp .env.example ~/.claude/hooks/.notify-env
chmod 600 ~/.claude/hooks/.notify-env
# Edit .notify-env with your Discord credentials
```

### Updating

```bash
cd ~/workspace-ext/aily
git pull
./install.sh   # re-symlinks any new hooks
```

## Bridges (optional)

Bridges enable **bidirectional** communication — send messages from Discord/Slack back to your tmux sessions, plus `!` commands for session management.

```
Message in [agent] thread
    → bridge detects it
    → SSH + tmux send-keys to the right session
```

### Discord bridge

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python agent-bridge.py
```

### Slack bridge

Requires a Slack app with Socket Mode enabled and an app-level token (`xapp-...`).

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python slack-bridge.py
```

See [docs/architecture.md](docs/architecture.md) for implementation details.

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| No notification | Check `~/.claude/hooks/.notify-env` exists with valid tokens |
| Thread not found | Verify channel ID matches your channel |
| tmux session not detected | Ensure you're inside tmux (`echo $TMUX`) |
| Codex hook not firing | Check `~/.codex/config.toml` has the `notify` line |
| Gemini hook not firing | Check `~/.gemini/settings.json` has `AfterAgent` hook |
| macOS tmux path | Script tries `/opt/homebrew/bin/tmux` first, falls back to `tmux` |
| Platform not detected | Run `aily status` to check configured platforms |

## Requirements

- macOS or Linux
- Python 3
- `curl`
- `tmux`
- Discord bot with message permissions and/or Slack app with Socket Mode

## License

MIT
