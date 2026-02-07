# claude-hooks

Claude Code notification hooks that post task completions to Discord, with one thread per tmux session.

## What it does

When Claude Code finishes a task (triggers a Notification event), this hook:

1. Extracts Claude's last response from the session JSONL
2. Finds or creates a Discord thread named `[agent] <tmux-session>` in your channel
3. Posts a summary with host, project, timestamp, and Claude's response

```
🔔 Task Complete

🖥 Host: jiun-mini
📁 Project: my-project
⏰ Time: 2026-02-07 22:01:38

Response:
Fixed the bug in auth.ts by updating the token validation logic...
```

Each tmux session gets its own thread, so notifications stay organized.

## Requirements

- macOS or Linux
- Python 3
- `curl`
- `tmux` (notifications only fire inside tmux sessions)
- A Discord bot token with message permissions

## Installation

```bash
git clone https://github.com/jiunbae/claude-hooks.git
cd claude-hooks
./install.sh
```

The install script symlinks hook files into `~/.claude/hooks/`.

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

### Configure Claude Code

Add the notification hook to `~/.claude/settings.json`:

```json
{
  "hooks": {
    "Notification": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "bash ~/.claude/hooks/notify-clawdia.sh",
            "statusMessage": "Notifying..."
          }
        ]
      }
    ]
  }
}
```

## How it works

### notify-clawdia.sh

The main hook script. Runs in a background subshell (`( ... ) & disown`) to avoid Claude Code's hook timeout.

**Flow:**
1. Detects current tmux session name
2. Waits 5s for Claude Code to flush the response to its JSONL log
3. Extracts Claude's last response via `extract-last-message.py`
4. Looks up the guild's active threads, archived threads, and channel messages to find an existing `[agent] <session>` thread
5. Creates a new thread if none found; unarchives if archived
6. Posts the notification to the thread

**Key design decisions:**
- Background fork avoids hook timeout — the hook returns `exit 0` immediately
- Thread names use `[agent]` prefix for easy identification by bots
- Uses guild-based active threads endpoint (`/guilds/{id}/threads/active`), not the channel-based one (which returns 404)
- Markdown tables in Claude's response are auto-wrapped in code blocks (Discord doesn't render markdown tables)

### extract-last-message.py

Reads the Claude Code session JSONL file backwards to find the last meaningful assistant text response.

- Locates the project JSONL at `~/.claude/projects/<sanitized-cwd>/*.jsonl`
- Strips English Coach header blocks (`--- > ... ---`) if present
- Converts markdown tables to code blocks for Discord compatibility
- Skips responses shorter than 20 characters
- Truncates at 1000 characters

## Multi-machine setup

To sync across machines, clone the repo on each machine and run `./install.sh`. The `.notify-env` file must be created separately on each machine (it contains secrets and is gitignored).

```bash
# On remote machine
git clone https://github.com/jiunbae/claude-hooks.git
cd claude-hooks
./install.sh
cp .env.example ~/.claude/hooks/.notify-env
# Edit .notify-env with your tokens
```

## Thread naming

Threads are named `[agent] <tmux-session-name>`. This prefix allows Discord bots to identify notification threads and implement bidirectional flows (e.g., forwarding Discord replies back to the tmux session's Claude Code instance).

## License

MIT
