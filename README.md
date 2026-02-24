<div align="center">

<img src="docs/banner.svg" alt="aily — AI agent notification relay" width="700"/>

<br/>

**Know when your agent finishes. Reply from your phone.**

Built for developers running AI coding agents in tmux sessions across SSH hosts.

[![npm](https://img.shields.io/npm/v/aily-cli)](https://www.npmjs.com/package/aily-cli)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![GitHub stars](https://img.shields.io/github/stars/jiunbae/aily)](https://github.com/jiunbae/aily)

</div>

## Quickstart for Agents

<div><img src="https://quickstart-for-agents.vercel.app/api/header.svg?theme=claude-code&mascot=wave&title=Install+aily&lang=Agents" width="100%" /></div>

```
Install aily — AI agent notification relay for Discord/Slack (https://github.com/jiunbae/aily).
Run `npm install -g aily-cli` then `aily init` to start the interactive setup wizard.
The wizard will ask for: dashboard URL, auth token, Discord/Slack credentials, and SSH hosts.
After setup, run `aily status` to verify connectivity and `aily doctor` to check for issues.
```

<div><img src="https://quickstart-for-agents.vercel.app/api/footer.svg?theme=claude-code" width="100%" /></div>

## Quick Start

> **Prerequisite:** [Node.js](https://nodejs.org/) >= 14

`aily init` walks you through everything:

<details>
<summary><code>$ aily init</code> — wizard output</summary>

```
$ aily init

=== aily setup wizard ===

  1) Dashboard URL
     Dashboard URL [https://aily.jiun.dev]: ... OK

  2) Auth token ... OK

  3) Platform setup
     Enable Discord? ... OK (channel: ai-notifications)
     Enable Slack? ... skipped

  4) SSH hosts
     SSH hosts [localhost]: myhost.ts.net ... OK

  5) Thread cleanup
     Choice [1: archive]: ... OK

  6) Writing configuration
     ✓ Saved to ~/.claude/hooks/.notify-env (chmod 600)

  7) Installing hooks
     ✓ Claude Code, Codex CLI, Gemini CLI configured

  8) Shell completions ... installed

=== Setup complete ===
```

</details>

<details>
<summary><b>Manual Install</b></summary>

Prerequisites: macOS or Linux, `curl`, `jq`, `tmux`, Node.js >= 14, SSH key-based access to target hosts.

```bash
# npm (recommended)
npm install -g aily-cli && aily init

# npx (no install)
npx aily-cli init

# curl from dashboard
curl -sSL https://aily.jiun.dev/api/install.sh | bash

# git clone
git clone https://github.com/jiunbae/aily.git && cd aily && ./install.sh
```

</details>

<details>
<summary><b>CLI Reference</b></summary>

| Command | Description |
|---------|-------------|
| `aily init` | Interactive setup wizard (credentials, hooks, agents) |
| `aily init --non-interactive` | Headless mode — reads from env vars |
| `aily status` | Show platform connectivity and configuration |
| `aily doctor` | Diagnose common issues |
| `aily sessions` | List active sessions from dashboard |
| `aily sync [name]` | Trigger message sync for a session |
| `aily logs [name]` | Fetch recent messages for a session |
| `aily config show` | Show current config (tokens redacted) |
| `aily config set KEY VALUE` | Update a config key |
| `aily start [name]` | Create thread for tmux session |
| `aily stop [name]` | Archive/delete thread for tmux session |
| `aily auto [on\|off]` | Toggle auto thread sync (tmux hooks) |
| `aily uninstall` | Remove hooks and configuration |
| `--json` | JSON output (global flag) |
| `--verbose` | Debug output (global flag) |

</details>

## What Happens After Setup

Once installed, aily works automatically in the background:

- **Agent finishes a task** — You get a Discord/Slack notification in a thread named after your tmux session, with the agent's response.
- **Agent asks a question** — The prompt appears in the same thread. Reply from your phone to answer it.
- **You reply in the thread** — Your message is forwarded to the agent's tmux session via SSH, as if you typed it at the keyboard.
- **Shell command output** — When the tmux session is running a plain shell (not an agent), command output is captured and relayed back to the thread automatically.

Every tmux session gets a dedicated thread. Start a session, get a thread. Close the session, the thread archives (or deletes — [configurable](.env.example)). No manual wiring.

You can also manage sessions directly from Discord/Slack using bridge commands: `!new <name> [host]`, `!kill <name>`, `!sessions`.

Unlike one-way notification tools, aily provides **bidirectional chat** — you can monitor and control agents entirely from your phone.

<!-- TODO: Add screenshot/GIF showing Discord notification + reply flow -->

## How It Works

```mermaid
flowchart LR
    A["Agent\n(Claude / Codex / Gemini)"] --> B["Hook\n(post.sh)"]
    B --> C["Discord thread"]
    B --> D["Slack thread"]
    B --> E["Dashboard API"]

    C --> F["Bridge"]
    D --> F
    F -->|"SSH + tmux send-keys"| A
    A -.->|"capture-pane"| F
```

Each tmux session gets a dedicated thread (`[agent] <session-name>`) on each platform. Task completions, interactive prompts, and errors are posted to the matching thread. Reply in the thread to send input back to the agent.

For a deeper look, see [Architecture](docs/architecture.md).

## Dashboard

The web dashboard provides a real-time UI for monitoring and managing sessions across hosts. Features include live session status via WebSocket, full message history, send-input controls, login authentication, dark/light theme, and mobile-friendly layout.

<!-- TODO: Add screenshot of dashboard sessions page -->

See [API Reference](docs/api.md) for dashboard routes and REST endpoints.

## Supported Agents

| Agent | Hook Type | Extractor |
|-------|-----------|-----------|
| [![Claude Code](https://img.shields.io/badge/Claude_Code-hook-blueviolet)](https://docs.anthropic.com/en/docs/claude-code) | `Notification` + `PreToolUse` | JSONL session parser |
| [![Codex CLI](https://img.shields.io/badge/Codex_CLI-hook-green)](https://github.com/openai/codex) | `notify` | stdin message |
| [![Gemini CLI](https://img.shields.io/badge/Gemini_CLI-hook-orange)](https://github.com/google-gemini/gemini-cli) | `AfterAgent` | stdin JSON |

## Configuration

`aily init` creates `~/.claude/hooks/.notify-env` with all credentials. Run `aily config show` to inspect.

<details>
<summary><b>.notify-env reference</b></summary>

```env
# Discord (optional)
DISCORD_BOT_TOKEN="your-bot-token"
DISCORD_CHANNEL_ID="your-channel-id"

# Slack (optional)
SLACK_BOT_TOKEN="xoxb-your-slack-bot-token"
SLACK_APP_TOKEN="xapp-your-slack-app-level-token"
SLACK_CHANNEL_ID="C0123456789"

# Dashboard
AILY_DASHBOARD_URL="https://aily.jiun.dev"
AILY_AUTH_TOKEN="your-auth-token"

# Multi-host (comma-separated SSH targets)
SSH_HOSTS="host1,host2"

# Thread cleanup on session kill: "archive" (default) or "delete"
THREAD_CLEANUP="archive"

# Auto-create/archive threads on tmux session start/close (default: true)
# TMUX_THREAD_SYNC="true"

# Force specific platforms (default: auto-detect from tokens)
# NOTIFY_PLATFORMS="discord,slack"
```

Platforms are auto-detected from available tokens. Run `aily status` to verify.

</details>

<details>
<summary><b>Discord Bot Setup</b></summary>

1. Go to [Discord Developer Portal](https://discord.com/developers/applications) and create a new application
2. Under **Bot**, reset the token (this is your `DISCORD_BOT_TOKEN`) and enable **Message Content Intent**
3. Under **OAuth2 > URL Generator**, select scope `bot` with permissions: Send Messages, Create/Send/Manage Threads, Read Message History
4. Use the generated URL to invite the bot, then copy the target channel's ID (`DISCORD_CHANNEL_ID`)

</details>

<details>
<summary><b>Slack App Setup</b></summary>

1. Create a new app at [api.slack.com/apps](https://api.slack.com/apps) and enable **Socket Mode** (generates `SLACK_APP_TOKEN`)
2. Add bot token scopes: `chat:write`, `channels:history`, `channels:read`, `reactions:write`
3. Subscribe to bot events: `message.channels`, `message.groups`
4. Install to workspace and copy the bot token (`SLACK_BOT_TOKEN`)
5. Invite the bot to your channel and copy the channel ID (`SLACK_CHANNEL_ID`)

</details>

## Docker / Kubernetes

The Dockerfile supports three modes via `BRIDGE_MODE`:

```bash
# Discord bridge
docker run -e BRIDGE_MODE=discord \
  -e DISCORD_BOT_TOKEN=... -e DISCORD_CHANNEL_ID=... \
  -e SSH_HOSTS=... aily

# Slack bridge
docker run -e BRIDGE_MODE=slack \
  -e SLACK_BOT_TOKEN=... -e SLACK_APP_TOKEN=... -e SLACK_CHANNEL_ID=... \
  -e SSH_HOSTS=... aily

# Dashboard
docker run -e BRIDGE_MODE=dashboard -p 8080:8080 aily
```

Bridges require Python 3.10+ when running outside Docker. For Kubernetes, deploy via ArgoCD with the included kustomize overlays. The CI pipeline (Gitea Actions) builds multi-arch images and updates the IaC repo automatically.

## License

[MIT](LICENSE)
