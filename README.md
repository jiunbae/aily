<div align="center">

# aily

**AI agent notification relay for Discord**

Connect your AI coding agents to Discord — get notified when tasks complete, see interactive prompts remotely, and send commands back.

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Claude Code](https://img.shields.io/badge/Claude_Code-hook-blueviolet)](https://docs.anthropic.com/en/docs/claude-code)
[![Codex CLI](https://img.shields.io/badge/Codex_CLI-hook-green)](https://github.com/openai/codex)
[![Gemini CLI](https://img.shields.io/badge/Gemini_CLI-hook-orange)](https://github.com/google-gemini/gemini-cli)

</div>

---

## How it works

```
                    aily
  ┌─────────────────────────────────────────┐
  │                                         │
  │   Agent finishes task                   │
  │       │                                 │
  │       ▼                                 │
  │   ┌──────────────┐   ┌──────────────┐  │
  │   │ Notification  │──▶│ discord-post │──┼──▶  Discord Thread
  │   │ Hook          │   │ .sh          │  │    [agent] my-session
  │   └──────────────┘   └──────────────┘  │
  │                                         │        ▲
  │   Agent asks question                   │        │
  │       │                                 │        │
  │       ▼                                 │        │
  │   ┌──────────────┐   ┌──────────────┐  │        │
  │   │ PreToolUse   │──▶│ format +     │──┼────────┘
  │   │ Hook         │   │ post         │  │
  │   └──────────────┘   └──────────────┘  │
  │                                         │
  └─────────────────────────────────────────┘

  agent-bridge (optional, bidirectional)
  ┌─────────────────────────────────────────┐
  │                                         │
  │   Discord ──▶ SSH ──▶ tmux send-keys   │
  │   message        ──▶ Claude Code input │
  │                                         │
  └─────────────────────────────────────────┘
```

When an AI agent finishes a task, aily:

1. Extracts the agent's last response
2. Finds or creates a Discord thread `[agent] <tmux-session>`
3. Posts a formatted summary

Each tmux session gets its own thread — notifications stay organized across projects and machines.

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

When Claude Code asks you a question (`AskUserQuestion`), aily forwards the choices to Discord immediately:

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
git clone https://github.com/jiunbae/aily.git
cd aily
./install.sh

# Configure Discord credentials
cp .env.example ~/.claude/hooks/.notify-env
chmod 600 ~/.claude/hooks/.notify-env
```

Edit `~/.claude/hooks/.notify-env`:

```env
DISCORD_BOT_TOKEN="your-bot-token"
DISCORD_CHANNEL_ID="your-channel-id"
```

The install script automatically:
- Symlinks all hooks into `~/.claude/hooks/`
- Configures `~/.codex/config.toml` for Codex CLI
- Configures `~/.gemini/settings.json` for Gemini CLI

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

## Architecture

```
aily/
├── hooks/
│   ├── notify-claude.sh            # Claude Code notification hook
│   ├── notify-codex.py             # Codex CLI notification hook
│   ├── notify-gemini.sh            # Gemini CLI notification hook
│   ├── ask-question-notify.sh      # AskUserQuestion prompt forwarder
│   ├── format-question.py          # Formats interactive prompts for Discord
│   ├── extract-last-message.py     # JSONL session response extractor
│   └── discord-post.sh             # Shared Discord thread discovery + posting
├── agent-bridge.py                 # Bidirectional Discord ↔ tmux bridge
├── install.sh                      # One-command setup
└── docs/
    └── architecture.md             # Detailed technical docs
```

### Key design decisions

- **Background execution** — All hooks fork to a background subshell (`( ... ) & disown; exit 0`) and return immediately to avoid agent hook timeouts
- **Thread-per-session** — Each tmux session gets a dedicated Discord thread (`[agent] <session-name>`), keeping multi-project notifications organized
- **Hash-based dedup** — Prevents duplicate notifications when the same response triggers multiple hook events
- **Interactive suppression** — When an `AskUserQuestion` prompt is active, task-complete notifications are suppressed to avoid stale messages

## Multi-machine setup

Each machine needs its own clone and `.notify-env`. The install script handles symlinking and agent config automatically.

```bash
# On a new machine
git clone https://github.com/jiunbae/aily.git ~/workspace-ext/aily
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

## Agent Bridge (optional)

`agent-bridge.py` enables **bidirectional** communication — send messages from Discord back to your tmux sessions:

```
Discord message in [agent] thread
    → agent-bridge detects it
    → SSH + tmux send-keys to the right session
    → captures terminal output
    → posts it back to the thread
```

Requires `aiohttp`:

```bash
python3 -m venv .venv
.venv/bin/pip install aiohttp
.venv/bin/python agent-bridge.py
```

See [docs/architecture.md](docs/architecture.md) for implementation details.

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| No Discord notification | Check `~/.claude/hooks/.notify-env` exists with valid tokens |
| Thread not found | Verify `DISCORD_CHANNEL_ID` matches your channel |
| tmux session not detected | Ensure you're inside tmux (`echo $TMUX`) |
| Codex hook not firing | Check `~/.codex/config.toml` has the `notify` line |
| Gemini hook not firing | Check `~/.gemini/settings.json` has `AfterAgent` hook |
| macOS tmux path | Script tries `/opt/homebrew/bin/tmux` first, falls back to `tmux` |

## Requirements

- macOS or Linux
- Python 3
- `curl`
- `tmux`
- Discord bot with message permissions

## License

MIT
