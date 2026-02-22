<div align="center">

<img src="docs/banner.svg" alt="aily — AI notification relay" width="700"/>

<br/>
<br/>

AI agent notification relay + web dashboard for Discord and Slack.
Get notified when tasks complete, forward interactive prompts, send commands back — all from your phone.

[![npm](https://img.shields.io/npm/v/aily-cli)](https://www.npmjs.com/package/aily-cli)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Claude Code](https://img.shields.io/badge/Claude_Code-hook-blueviolet)](https://docs.anthropic.com/en/docs/claude-code)
[![Codex CLI](https://img.shields.io/badge/Codex_CLI-hook-green)](https://github.com/openai/codex)
[![Gemini CLI](https://img.shields.io/badge/Gemini_CLI-hook-orange)](https://github.com/google-gemini/gemini-cli)

</div>

---

## Quick Start

Paste this into your AI agent (Claude Code, Codex, Gemini):

> Install aily — AI agent notification relay for Discord/Slack (https://github.com/jiunbae/aily).
> Run `npm install -g aily-cli` then `aily init` to start the interactive setup wizard.
> The wizard will ask for: dashboard URL, auth token, Discord/Slack credentials, and SSH hosts.
> After setup, run `aily status` to verify connectivity and `aily doctor` to check for issues.

`aily init` guides you through each step interactively:

```
$ aily init

=== aily setup wizard ===

  1) Dashboard URL
     Dashboard URL [https://aily.jiun.dev]:
     Testing connectivity... OK

  2) Auth token
     Auth token [****]:
     Validating token... OK

  3) Platform setup
     Enable Discord? [Y/n]: Y
     Discord bot token [****]:
     Discord channel ID [1234567890]:
     Validating Discord... OK (channel: ai-notifications)

     Enable Slack? [y/N]: N

  4) SSH hosts
     SSH hosts (comma-separated) [localhost]: myhost.ts.net
     Testing SSH to myhost.ts.net... OK

  5) Writing configuration
     ✓ Saved to ~/.claude/hooks/.notify-env (chmod 600)

  6) Installing hooks
     ✓ Claude Code, Codex CLI, Gemini CLI, OpenCode configured

  7) Shell completions
     ✓ Zsh completions installed

=== Done ===
```

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
<summary><b>CLI Options</b></summary>

| Option | Description |
|--------|-------------|
| `aily init` | Interactive setup wizard (recommended) |
| `aily init --non-interactive` | Headless mode — reads from env vars |
| `aily status` | Show platform connectivity |
| `aily doctor` | Diagnose common issues |
| `aily config show` | Show current config (tokens redacted) |
| `aily config set KEY VALUE` | Update a config key |
| `aily uninstall` | Remove hooks and configuration |
| `--json` | JSON output (global flag) |
| `--verbose` | Debug output (global flag) |

</details>

## How It Works

```
  Agent (Claude/Codex/Gemini)
      |
      v
  Hook triggers  --->  post.sh (dispatcher)  --+--->  Discord thread
                                                |
                                                +--->  Slack thread
                                                |
                                                +--->  Dashboard API

  Discord/Slack message in thread
      |
      v
  Bridge  --->  SSH  --->  tmux send-keys  --->  Agent input
```

Each tmux session gets a dedicated thread (`[agent] <session-name>`) on each platform. Task completions, interactive prompts, and errors are posted to the matching thread. Reply in the thread to send input back to the agent.

## Dashboard

The web dashboard provides a real-time UI for monitoring and managing sessions across hosts.

| Page | Path | Description |
|------|------|-------------|
| Home | `/` | Overview with stats and recent activity |
| Sessions | `/sessions` | Live session list with status, messages, send input |
| Session Detail | `/sessions/{name}` | Full message history and controls for a session |
| Settings | `/settings` | Platform status, SSH hosts, feature toggles |

Features: real-time updates via WebSocket, dark/light theme, token-based auth, mobile-friendly.

## CLI Commands

| Command | Description |
|---------|-------------|
| `aily init` | Interactive setup wizard (credentials, hooks, agents) |
| `aily status` | Show platform connectivity and configuration |
| `aily sessions` | List active sessions from dashboard |
| `aily sync [name]` | Trigger message sync for a session |
| `aily logs [name]` | Fetch recent messages for a session |
| `aily config ...` | Show or edit configuration |
| `aily doctor` | Diagnose common issues |
| `aily start [name]` | Create thread for tmux session |
| `aily stop [name]` | Archive thread for tmux session |
| `aily auto [on\|off]` | Toggle auto thread sync (tmux hooks) |
| `aily uninstall` | Remove hooks and configuration |

## Supported Agents

| Agent | Hook Type | Extractor |
|-------|-----------|-----------|
| **Claude Code** | `Notification` + `PreToolUse` | JSONL session parser |
| **Codex CLI** | `notify` | stdin message |
| **Gemini CLI** | `AfterAgent` | stdin JSON |

## Architecture

```
aily/
├── aily                        # CLI tool
├── agent-bridge.py             # Discord <-> tmux bridge
├── slack-bridge.py             # Slack <-> tmux bridge
├── dashboard/
│   ├── app.py                  # aiohttp app factory
│   ├── config.py               # Configuration from env / .notify-env
│   ├── api/                    # REST + WebSocket endpoints
│   ├── services/               # Session, message, platform services
│   ├── workers/                # Background pollers and sync
│   ├── templates/              # Jinja2 HTML templates
│   └── static/                 # CSS, JS, assets
├── hooks/
│   ├── post.sh                 # Multi-platform dispatcher
│   ├── discord-lib.sh          # Discord API functions
│   ├── slack-lib.sh            # Slack API functions
│   ├── thread-sync.sh          # tmux session lifecycle
│   ├── notify-claude.sh        # Claude Code hook
│   ├── notify-codex.py         # Codex CLI hook
│   ├── notify-gemini.sh        # Gemini CLI hook
│   ├── ask-question-notify.sh  # Interactive prompt forwarder
│   └── extract-last-message.py # JSONL response extractor
├── Dockerfile                  # Multi-mode container (discord/slack/dashboard)
└── install.sh                  # One-command local setup
```

**Bridges** run as long-lived processes (Discord bot or Slack Socket Mode) that relay messages bidirectionally between platform threads and tmux sessions via SSH.

**Dashboard** is an aiohttp web app that polls SSH hosts for tmux sessions, syncs messages from Discord/Slack, and serves a real-time UI. Background workers handle session polling, message sync, and optional JSONL ingestion.

**Hooks** are lightweight shell/Python scripts that fire on agent events, format the output, and dispatch to all configured platforms in parallel. They fork to background to avoid blocking agent execution.

## Configuration

Credentials are stored in `~/.claude/hooks/.notify-env`:

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
```

Platforms are auto-detected from available tokens. Run `aily status` to verify.

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

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/sessions` | List all sessions |
| `GET` | `/api/sessions/{name}` | Get session details |
| `POST` | `/api/sessions/{name}/send` | Send input to a session |
| `GET` | `/api/sessions/{name}/messages` | Get message history |
| `POST` | `/api/sessions/{name}/sync` | Trigger message sync |
| `GET` | `/api/stats` | Dashboard statistics |
| `GET` | `/ws` | WebSocket for real-time updates |
| `GET` | `/api/settings` | System settings |
| `PUT` | `/api/settings` | Update settings |
| `POST` | `/api/settings/test` | Test platform/SSH connectivity |
| `GET` | `/api/install.sh` | Downloadable installer script |
| `POST` | `/api/hooks/event` | Bridge webhook (internal) |
| `GET` | `/healthz` | Health check (no auth) |

All endpoints except `/healthz` and `/api/hooks/event` require a `Bearer` token when `DASHBOARD_TOKEN` is set.

## Docker / Kubernetes

The Dockerfile supports three modes via `BRIDGE_MODE`:

```bash
# Discord bridge
docker run -e BRIDGE_MODE=discord -e DISCORD_BOT_TOKEN=... aily

# Slack bridge
docker run -e BRIDGE_MODE=slack -e SLACK_BOT_TOKEN=... aily

# Dashboard
docker run -e BRIDGE_MODE=dashboard -p 8080:8080 aily
```

For Kubernetes, deploy via ArgoCD with the included kustomize overlays. The CI pipeline (Gitea Actions) builds multi-arch images and updates the IaC repo automatically.

## License

[MIT](LICENSE)
