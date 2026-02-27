<div align="center">

<img src="docs/banner.svg" alt="aily — AI agent session bridge" width="700"/>

<br/>

**Sync your tmux sessions to Discord & Slack. See everything, reply from anywhere.**

Full I/O bridge between AI coding agents in tmux and your chat platform — monitor output, send input, switch to CLI. All in sync.

[![npm](https://img.shields.io/npm/v/aily-cli)](https://www.npmjs.com/package/aily-cli)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![GitHub stars](https://img.shields.io/github/stars/jiunbae/aily)](https://github.com/jiunbae/aily)

**English** | [한국어](README.ko.md)

</div>

## Quickstart for Agents

<div><img src="https://quickstart-for-agents.vercel.app/api/header.svg?theme=claude-code&mascot=wave&title=Install+aily&lang=Agents" width="100%" /></div>

```
Install aily — AI agent session bridge for Discord/Slack (https://github.com/jiunbae/aily).
Install: `git clone https://github.com/jiunbae/aily.git && cd aily && ./aily init`
  (or: `npm install -g aily-cli && aily init`)
The wizard asks: platform (Discord/Slack/both) → bot token → channel ID → done.
Dashboard, SSH hosts, and agent auto-launch are optional (shown when "Use defaults?" is declined).
Config is saved to ~/.config/aily/env. Run `aily status` to verify, `aily doctor` to diagnose.
```

<div><img src="https://quickstart-for-agents.vercel.app/api/footer.svg?theme=claude-code" width="100%" /></div>

## Quick Start

> **Prerequisites:** `bash`, `curl`, `jq`, `tmux`, `python3` — Node.js only needed for `npm install` method.

`aily init` walks you through everything:

<details>
<summary><code>$ aily init</code> — wizard output</summary>

```
$ aily init

=== aily setup wizard ===

  Notification platform
  > discord   Discord bot
    slack     Slack bot
    both      Discord + Slack

  Discord bot token [****]: ✓
  Discord channel ID [1234...]: ✓
  ✓ Discord: #ai-notifications
    ✓ Send Messages
    ✓ Manage Threads

  Defaults: SSH=localhost, cleanup=archive, no agent, no dashboard
  Use defaults? [Y/n]: y

  ✓ Saved to ~/.config/aily/env (chmod 600)

  === Installing notification hooks ===
    ✓ notify-claude.sh
    ✓ notify-codex.py
    ✓ notify-gemini.sh
    ✓ notify-opencode.mjs

  Start Discord bridge bot? [Y/n]: y
  ✓ Bridge started (tmux session: aily-bridge)

=== Setup complete ===
```

</details>

<details>
<summary><b>Manual Install</b></summary>

Prerequisites: macOS or Linux, `bash`, `curl`, `jq`, `tmux`, `python3`, SSH key-based access to target hosts. Node.js only needed for npm method.

```bash
# npm
npm install -g aily-cli && aily init

# npx (no install)
npx aily-cli init

# git clone (recommended)
git clone https://github.com/jiunbae/aily.git && cd aily && ./aily init
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
| `aily bridge start\|stop\|restart\|status\|logs` | Manage Discord/Slack bridge bot |
| `aily dashboard start\|stop\|restart\|status\|logs` | Manage web dashboard (background process) |
| `aily deploy <host>` | Deploy aily to a remote SSH host |
| `aily attach <session>` | Attach to a tmux session |
| `aily export <session> [json\|markdown]` | Export session messages |
| `aily uninstall` | Remove hooks and configuration |
| `aily version` | Show version |
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

You can also manage sessions directly from Discord/Slack using bridge commands: `!new <name> [host]`, `!kill <name>`, `!sessions`. Inside a thread, use shortcuts to send key sequences: `!c` (Ctrl+C), `!d` (Ctrl+D), `!z` (Ctrl+Z), `!q`, `!enter`, `!esc`.

Unlike one-way notification tools, aily provides a **full session bridge** — monitor output, send input, and `aily attach` to jump into the same terminal view from CLI.

<!-- TODO: Add screenshot/GIF showing Discord notification + reply flow -->

## Bridge

The bridge bot connects to Discord/Slack and enables bidirectional communication — forwarding your messages to tmux sessions and relaying output back. `aily init` offers to start it automatically.

```bash
aily bridge start     # runs in a tmux session (aily-bridge)
aily bridge status    # check if running
aily bridge logs      # view recent output
aily bridge restart   # restart after config changes
```

**Bridge commands** (from Discord/Slack):

| Command | Description |
|---------|-------------|
| `!new <name> [host]` | Create a new tmux session + thread |
| `!kill <name>` | Kill a tmux session and archive its thread |
| `!sessions` | List active sessions across all hosts |

**Thread shortcuts** (inside an agent thread):

| Shortcut | Sends |
|----------|-------|
| `!c` | Ctrl+C |
| `!d` | Ctrl+D |
| `!z` | Ctrl+Z |
| `!q` | `q` |
| `!enter` | Enter |
| `!esc` | Escape |

Any other message typed in a thread is sent directly to the session via `tmux send-keys`.

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

```bash
aily dashboard start   # runs as a background process
aily dashboard status  # check if running
aily dashboard logs    # tail recent output
aily dashboard stop    # stop the process
```

<!-- TODO: Add screenshot of dashboard sessions page -->

See [API Reference](docs/api.md) for dashboard routes and REST endpoints.

## Supported Agents

| Agent | Hook Type | Extractor |
|-------|-----------|-----------|
| [![Claude Code](https://img.shields.io/badge/Claude_Code-hook-blueviolet)](https://docs.anthropic.com/en/docs/claude-code) | `Notification` + `Stop` | JSONL session parser |
| [![Codex CLI](https://img.shields.io/badge/Codex_CLI-hook-green)](https://github.com/openai/codex) | `notify` | stdin message |
| [![Gemini CLI](https://img.shields.io/badge/Gemini_CLI-hook-orange)](https://github.com/google-gemini/gemini-cli) | `AfterAgent` | stdin JSON |
| [![OpenCode](https://img.shields.io/badge/OpenCode-plugin-teal)](https://github.com/opencode-ai/opencode) | Plugin (`aily-notify.mjs`) | Event hook |

## Configuration

`aily init` creates `~/.config/aily/env` with all credentials. Run `aily config show` to inspect.

<details>
<summary><b>Config reference (~/.config/aily/env)</b></summary>

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

## Multi-host Setup

Bridge tmux sessions on remote machines to your Discord/Slack channels:

```bash
# Deploy aily to a remote host (copies files, config, runs init)
aily deploy my-server

# Or manually add a host
aily config set SSH_HOSTS "localhost,my-server"
```

`aily deploy` will: test SSH connectivity → clone aily to the remote host → copy your config → run `aily init --non-interactive` → add the host to `SSH_HOSTS`. Requires key-based SSH access (no password prompts). Use `~/.ssh/config` host aliases for convenience.

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

Bridges require Python 3.10+ and `aiohttp` when running outside Docker.

---

<details>
<summary><b>한국어</b></summary>

## 에이전트 빠른 시작

```
aily 설치 — Discord/Slack용 AI 에이전트 세션 브릿지 (https://github.com/jiunbae/aily).
설치: `git clone https://github.com/jiunbae/aily.git && cd aily && ./aily init`
  (또는: `npm install -g aily-cli && aily init`)
위저드가 물어봅니다: 플랫폼 (Discord/Slack/both) → 봇 토큰 → 채널 ID → 완료.
대시보드, SSH 호스트, 에이전트 자동 실행은 선택사항입니다 ("기본값 사용?" 거부 시 표시).
설정은 ~/.config/aily/env에 저장됩니다. `aily status`로 확인, `aily doctor`로 진단.
```

## 설정 후 동작

설치 후 aily는 백그라운드에서 자동으로 동작합니다:

- **에이전트가 작업을 완료하면** — tmux 세션 이름으로 된 Discord/Slack 스레드에 에이전트 응답과 함께 알림을 받습니다.
- **에이전트가 질문하면** — 같은 스레드에 프롬프트가 표시됩니다. 폰으로 답장하면 됩니다.
- **스레드에 답장하면** — 메시지가 SSH를 통해 에이전트의 tmux 세션으로 전달됩니다. 직접 키보드로 치는 것과 같습니다.
- **셸 명령어 출력** — tmux 세션에서 에이전트가 아닌 일반 셸이 실행 중이면, 명령어 출력이 캡처되어 스레드로 자동 전달됩니다.

모든 tmux 세션은 전용 스레드를 갖습니다. 세션을 시작하면 스레드가 생기고, 세션을 닫으면 스레드가 보관(또는 삭제)됩니다.

Discord/Slack에서 직접 세션을 관리할 수도 있습니다: `!new <name> [host]`, `!kill <name>`, `!sessions`. 스레드 안에서는 단축키로 키 시퀀스를 보낼 수 있습니다: `!c` (Ctrl+C), `!d` (Ctrl+D), `!z` (Ctrl+Z), `!q`, `!enter`, `!esc`.

단방향 알림 도구와 달리, aily는 **완전한 세션 브릿지**를 제공합니다 — 출력 모니터링, 입력 전달, `aily attach`로 동일한 터미널에 바로 연결.

## 브릿지

브릿지 봇은 Discord/Slack에 연결되어 양방향 통신을 가능하게 합니다. `aily init`이 자동 시작을 제안합니다.

```bash
aily bridge start     # tmux 세션에서 실행 (aily-bridge)
aily bridge status    # 실행 상태 확인
aily bridge logs      # 최근 출력 보기
aily bridge restart   # 설정 변경 후 재시작
```

| 명령어 | 설명 |
|--------|------|
| `!new <name> [host]` | 새 tmux 세션 + 스레드 생성 |
| `!kill <name>` | tmux 세션 종료 및 스레드 보관 |
| `!sessions` | 전체 호스트의 활성 세션 목록 |

| 단축키 | 전송 |
|--------|------|
| `!c` | Ctrl+C |
| `!d` | Ctrl+D |
| `!z` | Ctrl+Z |
| `!q` | `q` |
| `!enter` | Enter |
| `!esc` | Escape |

## 대시보드

```bash
aily dashboard start   # 백그라운드 프로세스로 실행
aily dashboard status  # 실행 상태 확인
aily dashboard logs    # 최근 출력 보기
aily dashboard stop    # 프로세스 중지
```

## 멀티호스트 설정

```bash
# 원격 호스트에 aily 배포 (파일 복사, 설정 복사, init 실행)
aily deploy my-server

# 또는 수동으로 호스트 추가
aily config set SSH_HOSTS "localhost,my-server"
```

`aily deploy`는: SSH 연결 테스트 → 원격 호스트에 aily 클론 → 설정 복사 → `aily init --non-interactive` 실행 → `SSH_HOSTS`에 추가. 키 기반 SSH 접근이 필요합니다.

전체 한국어 문서는 [README.ko.md](README.ko.md)를 참고하세요.

</details>

## License

[MIT](LICENSE)
