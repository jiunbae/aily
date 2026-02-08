# claude-hooks

Notification hooks for Claude Code, Codex CLI, and Gemini CLI that post task completions to Discord, with one thread per tmux session.

## What it does

When an agent finishes a task (Claude Code Notification hook, Codex notify hook, or Gemini AfterAgent hook), it:

1. Extracts the agent's last response
2. Finds or creates a Discord thread named `[agent] <tmux-session>` in your channel
3. Posts a summary with host, project, timestamp, and the response

```
🔔 **Task Complete** (claude)

🖥 Host: jiun-mini
📁 Project: my-project
⏰ Time: 2026-02-07 22:01:38

**Response:**
Fixed the bug in auth.ts by updating the token validation logic...
```

Each tmux session gets its own thread, so notifications stay organized.

## Requirements

- macOS or Linux
- Python 3
- `curl`
- `tmux` (thread naming is based on the tmux session)
- A Discord bot token with message permissions

## Installation

```bash
git clone https://github.com/jiunbae/claude-hooks.git
cd claude-hooks
./install.sh
```

The install script:

- Symlinks hook files into `~/.claude/hooks/`
- Updates `~/.codex/config.toml` for Codex CLI
- Updates `~/.gemini/settings.json` for Gemini CLI

### Configure secrets

```bash
cp .env.example ~/.claude/hooks/.notify-env
chmod 600 ~/.claude/hooks/.notify-env
```

Edit `~/.claude/hooks/.notify-env` with your Discord bot token and channel ID:

```
DISCORD_BOT_TOKEN="your-bot-token"
DISCORD_CHANNEL_ID="your-channel-id"
```

## Agent Configuration

### Claude Code (Notification hook)

Add the notification hook to `~/.claude/settings.json`:

```json
{
  "hooks": {
    "Notification": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "bash ~/.claude/hooks/notify-claude.sh",
            "statusMessage": "Notifying..."
          }
        ]
      }
    ]
  }
}
```

Backward compatibility: `notify-clawdia.sh` still exists as a wrapper and can be used if you already have it configured.

### Codex CLI (`notify` hook)

Add (or ensure) this in `~/.codex/config.toml`:

```toml
notify = ["python3", "/Users/<you>/.claude/hooks/notify-codex.py"]
```

### Gemini CLI (`AfterAgent` hook)

Merge this into `~/.gemini/settings.json`:

```json
{
  "hooks": {
    "AfterAgent": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "/Users/<you>/.claude/hooks/notify-gemini.sh",
            "name": "discord-notify",
            "timeout": 10000
          }
        ]
      }
    ]
  }
}
```

## How it works

### Unified flow

Each agent-specific hook extracts the last assistant response, then calls the shared Discord poster:

- `hooks/notify-claude.sh` (Claude Code)
- `hooks/notify-codex.py` (Codex CLI)
- `hooks/notify-gemini.sh` (Gemini CLI)
- `hooks/discord-post.sh` (shared Discord thread discovery + posting)

`discord-post.sh`:

- Detects current tmux session name
- Looks up the guild's active threads (via `/guilds/{id}/threads/active`) and matches threads where `parent_id == DISCORD_CHANNEL_ID`
- Creates a thread if none found; unarchives if archived
- Posts a formatted message to the thread

All hooks are designed to return quickly and do network work in the background (to avoid hook timeouts).

### extract-last-message.py

Reads the Claude Code session JSONL file backwards to find the last meaningful assistant text response.

- Locates the project JSONL at `~/.claude/projects/<sanitized-cwd>/*.jsonl`
- Strips English Coach header blocks (`--- > ... ---`) if present
- Converts markdown tables to code blocks for Discord compatibility
- Skips responses shorter than 20 characters
- Truncates at 1000 characters

## Multi-machine setup

Each machine needs its own clone and `.notify-env`. The install script handles symlinking and agent config automatically.

### Quick setup (new machine)

```bash
# 1. Clone
git clone https://github.com/jiunbae/claude-hooks.git ~/workspace-ext/claude-hooks
cd ~/workspace-ext/claude-hooks

# 2. Install (symlinks + agent configs)
./install.sh

# 3. Create secrets
cp .env.example ~/.claude/hooks/.notify-env
chmod 600 ~/.claude/hooks/.notify-env
nano ~/.claude/hooks/.notify-env   # fill in DISCORD_BOT_TOKEN and DISCORD_CHANNEL_ID
```

### Step-by-step walkthrough

#### Prerequisites

```bash
# Verify these are available
python3 --version   # Python 3.x
curl --version
tmux -V             # tmux must be installed
```

#### 1. Clone and install

```bash
git clone https://github.com/jiunbae/claude-hooks.git ~/workspace-ext/claude-hooks
cd ~/workspace-ext/claude-hooks
./install.sh
```

The install script will:
- Symlink all hooks to `~/.claude/hooks/`
- Add `notify` to `~/.codex/config.toml` (creates if needed)
- Add `AfterAgent` hook to `~/.gemini/settings.json` (creates if needed)
- Check if Claude Code `Notification` hook is configured

#### 2. Configure secrets

```bash
cp .env.example ~/.claude/hooks/.notify-env
chmod 600 ~/.claude/hooks/.notify-env
```

Edit `.notify-env`:

```
DISCORD_BOT_TOKEN="your-raw-bot-token"
DISCORD_CHANNEL_ID="your-channel-id"
```

#### 3. Configure Claude Code (if not already done)

Add to `~/.claude/settings.json`:

```json
{
  "hooks": {
    "Notification": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "bash ~/.claude/hooks/notify-claude.sh",
            "statusMessage": "Notifying..."
          }
        ]
      }
    ]
  }
}
```

#### 4. Verify installation

```bash
# Check symlinks
ls -la ~/.claude/hooks/notify-*.sh ~/.claude/hooks/notify-*.py ~/.claude/hooks/discord-post.sh

# Check agent configs
grep notify ~/.codex/config.toml
python3 -c "import json; print(json.dumps(json.load(open('$HOME/.gemini/settings.json')).get('hooks',{}), indent=2))"
```

### Codex + oh-my-prompt chaining

If the machine already has an oh-my-prompt Codex notify hook, use the wrapper to chain both:

```bash
# Edit ~/.codex/config.toml
notify = "bash /path/to/.claude/hooks/notify-codex-wrapper.sh"
```

The wrapper (`notify-codex-wrapper.sh`) calls both oh-my-prompt's `notify.js` and `notify-codex.py` in parallel.

### Updating on all machines

```bash
# On each machine
cd ~/workspace-ext/claude-hooks
git pull
./install.sh   # re-run to pick up any new hooks
```

### Troubleshooting

| Issue | Fix |
|-------|-----|
| No Discord notification | Check `~/.claude/hooks/.notify-env` exists with valid tokens |
| Thread not found | Verify `DISCORD_CHANNEL_ID` matches your #workspace channel |
| tmux session not detected | Ensure you're running inside `tmux` (check `echo $TMUX`) |
| Codex hook not firing | Check `~/.codex/config.toml` has the `notify` line |
| Gemini hook not firing | Check `~/.gemini/settings.json` has `AfterAgent` hook |
| macOS tmux path issue | Script tries `/opt/homebrew/bin/tmux` first, falls back to `tmux` |

## Thread naming

Threads are named `[agent] <tmux-session-name>`. This prefix allows Discord bots to identify notification threads and implement bidirectional flows (e.g., forwarding Discord replies back to the tmux session's Claude Code instance).

## License

MIT
