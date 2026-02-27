<div align="center">

<img src="docs/banner.svg" alt="aily — AI agent session bridge" width="700"/>

<br/>

**tmux 세션을 Discord & Slack에 동기화. 출력 확인, 입력 전달, CLI 전환까지.**

AI 코딩 에이전트의 tmux 세션과 채팅 플랫폼 간 완전한 I/O 브릿지 — 모니터링, 제어, 터미널 연결을 하나로.

[![npm](https://img.shields.io/npm/v/aily-cli)](https://www.npmjs.com/package/aily-cli)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![GitHub stars](https://img.shields.io/github/stars/jiunbae/aily)](https://github.com/jiunbae/aily)

[English](README.md) | **한국어**

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

> **사전 요구:** `bash`, `curl`, `jq`, `tmux`, `python3` — Node.js는 `npm install` 방식에만 필요합니다.

`aily init`이 모든 것을 안내합니다:

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

사전 요구: macOS 또는 Linux, `bash`, `curl`, `jq`, `tmux`, `python3`, 대상 호스트에 SSH 키 기반 접근. Node.js는 npm 방식에만 필요합니다.

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
| `aily init` | 대화형 설정 위저드 (인증정보, 훅, 에이전트) |
| `aily init --non-interactive` | 헤드리스 모드 — 환경변수에서 읽음 |
| `aily status` | 플랫폼 연결 상태 및 설정 표시 |
| `aily doctor` | 일반적인 문제 진단 |
| `aily sessions` | 활성 세션 목록 |
| `aily sync [name]` | 세션의 메시지 동기화 트리거 |
| `aily logs [name]` | 세션의 최근 메시지 조회 |
| `aily config show` | 현재 설정 표시 (토큰 가려짐) |
| `aily config set KEY VALUE` | 설정 값 변경 |
| `aily start [name]` | tmux 세션에 스레드 생성 |
| `aily stop [name]` | tmux 세션의 스레드 보관/삭제 |
| `aily auto [on\|off]` | 자동 스레드 동기화 토글 (tmux 훅) |
| `aily bridge start\|stop\|restart\|status\|logs` | Discord/Slack 브릿지 봇 관리 |
| `aily dashboard start\|stop\|restart\|status\|logs` | 웹 대시보드 관리 (백그라운드 프로세스) |
| `aily deploy <host>` | 원격 SSH 호스트에 aily 배포 |
| `aily attach <session>` | tmux 세션에 연결 |
| `aily export <session> [json\|markdown]` | 세션 메시지 내보내기 |
| `aily uninstall` | 훅 및 설정 제거 |
| `aily version` | 버전 표시 |
| `--json` | JSON 출력 (글로벌 플래그) |
| `--verbose` | 디버그 출력 (글로벌 플래그) |

</details>

## What Happens After Setup

설치 후 aily는 백그라운드에서 자동으로 동작합니다:

- **에이전트가 작업을 완료하면** — tmux 세션 이름으로 된 Discord/Slack 스레드에 에이전트 응답과 함께 알림을 받습니다.
- **에이전트가 질문하면** — 같은 스레드에 프롬프트가 표시됩니다. 폰으로 답장하면 됩니다.
- **스레드에 답장하면** — 메시지가 SSH를 통해 에이전트의 tmux 세션으로 전달됩니다. 직접 키보드로 치는 것과 같습니다.
- **셸 명령어 출력** — tmux 세션에서 에이전트가 아닌 일반 셸이 실행 중이면, 명령어 출력이 캡처되어 스레드로 자동 전달됩니다.

모든 tmux 세션은 전용 스레드를 갖습니다. 세션을 시작하면 스레드가 생기고, 세션을 닫으면 스레드가 보관(또는 삭제 — [설정 가능](.env.example))됩니다. 수동 연결이 필요 없습니다.

Discord/Slack에서 직접 세션을 관리할 수도 있습니다: `!new <name> [host]`, `!kill <name>`, `!sessions`. 스레드 안에서는 단축키로 키 시퀀스를 보낼 수 있습니다: `!c` (Ctrl+C), `!d` (Ctrl+D), `!z` (Ctrl+Z), `!q`, `!enter`, `!esc`.

단방향 알림 도구와 달리, aily는 **완전한 세션 브릿지**를 제공합니다 — 출력 모니터링, 입력 전달, `aily attach`로 동일한 터미널 화면에 바로 연결할 수 있습니다.

## Bridge

브릿지 봇은 Discord/Slack에 연결되어 양방향 통신을 가능하게 합니다 — 메시지를 tmux 세션으로 전달하고 출력을 다시 릴레이합니다. `aily init`이 자동 시작을 제안합니다.

```bash
aily bridge start     # runs in a tmux session (aily-bridge)
aily bridge status    # check if running
aily bridge logs      # view recent output
aily bridge restart   # restart after config changes
```

**Bridge commands** (Discord/Slack에서):

| Command | Description |
|---------|-------------|
| `!new <name> [host]` | 새 tmux 세션 + 스레드 생성 |
| `!kill <name>` | tmux 세션 종료 및 스레드 보관 |
| `!sessions` | 전체 호스트의 활성 세션 목록 |

**Thread shortcuts** (에이전트 스레드 안에서):

| Shortcut | Sends |
|----------|-------|
| `!c` | Ctrl+C |
| `!d` | Ctrl+D |
| `!z` | Ctrl+Z |
| `!q` | `q` |
| `!enter` | Enter |
| `!esc` | Escape |

스레드에 입력하는 다른 모든 메시지는 `tmux send-keys`를 통해 세션으로 직접 전달됩니다.

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

각 tmux 세션은 플랫폼마다 전용 스레드(`[agent] <session-name>`)를 갖습니다. 작업 완료, 대화형 프롬프트, 오류가 해당 스레드에 게시됩니다. 스레드에 답장하면 에이전트에게 입력이 전달됩니다.

자세한 내용은 [Architecture](docs/architecture.md)를 참고하세요.

## Dashboard

웹 대시보드는 호스트 간 세션을 모니터링하고 관리하는 실시간 UI를 제공합니다. WebSocket을 통한 실시간 세션 상태, 전체 메시지 기록, 입력 전송 컨트롤, 로그인 인증, 다크/라이트 테마, 모바일 친화적 레이아웃을 포함합니다.

```bash
aily dashboard start   # runs as a background process
aily dashboard status  # check if running
aily dashboard logs    # tail recent output
aily dashboard stop    # stop the process
```

API 라우트와 REST 엔드포인트는 [API Reference](docs/api.md)를 참고하세요.

## Supported Agents

| Agent | Hook Type | Extractor |
|-------|-----------|-----------|
| [![Claude Code](https://img.shields.io/badge/Claude_Code-hook-blueviolet)](https://docs.anthropic.com/en/docs/claude-code) | `Notification` + `Stop` | JSONL session parser |
| [![Codex CLI](https://img.shields.io/badge/Codex_CLI-hook-green)](https://github.com/openai/codex) | `notify` | stdin message |
| [![Gemini CLI](https://img.shields.io/badge/Gemini_CLI-hook-orange)](https://github.com/google-gemini/gemini-cli) | `AfterAgent` | stdin JSON |
| [![OpenCode](https://img.shields.io/badge/OpenCode-plugin-teal)](https://github.com/opencode-ai/opencode) | Plugin (`aily-notify.mjs`) | Event hook |

## Configuration

`aily init`이 모든 인증정보를 포함한 `~/.config/aily/env`를 생성합니다. `aily config show`로 확인하세요.

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

플랫폼은 사용 가능한 토큰에서 자동 감지됩니다. `aily status`로 확인하세요.

</details>

<details>
<summary><b>Discord Bot Setup</b></summary>

1. [Discord Developer Portal](https://discord.com/developers/applications)에서 새 애플리케이션 생성
2. **Bot**에서 토큰을 리셋 (이것이 `DISCORD_BOT_TOKEN`)하고 **Message Content Intent** 활성화
3. **OAuth2 > URL Generator**에서 스코프 `bot` 선택, 권한: Send Messages, Create/Send/Manage Threads, Read Message History
4. 생성된 URL로 봇을 초대한 뒤, 대상 채널의 ID를 복사 (`DISCORD_CHANNEL_ID`)

</details>

<details>
<summary><b>Slack App Setup</b></summary>

1. [api.slack.com/apps](https://api.slack.com/apps)에서 새 앱 생성 후 **Socket Mode** 활성화 (`SLACK_APP_TOKEN` 생성됨)
2. 봇 토큰 스코프 추가: `chat:write`, `channels:history`, `channels:read`, `reactions:write`
3. 봇 이벤트 구독: `message.channels`, `message.groups`
4. 워크스페이스에 설치 후 봇 토큰 복사 (`SLACK_BOT_TOKEN`)
5. 봇을 채널에 초대하고 채널 ID 복사 (`SLACK_CHANNEL_ID`)

</details>

## Multi-host Setup

원격 머신의 tmux 세션도 Discord/Slack 채널에 브릿지할 수 있습니다:

```bash
# Deploy aily to a remote host (copies files, config, runs init)
aily deploy my-server

# Or manually add a host
aily config set SSH_HOSTS "localhost,my-server"
```

`aily deploy`는: SSH 연결 테스트 → 원격 호스트에 aily 클론 → 설정 복사 → `aily init --non-interactive` 실행 → `SSH_HOSTS`에 추가. 키 기반 SSH 접근이 필요합니다 (비밀번호 프롬프트 없이). `~/.ssh/config` 호스트 별칭을 사용하세요.

## Docker

Dockerfile은 `BRIDGE_MODE`를 통해 세 가지 모드를 지원합니다:

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

Docker 외부에서 브릿지를 실행하려면 Python 3.10+과 `aiohttp`가 필요합니다.

---

<details>
<summary><b>English</b></summary>

## Quickstart for Agents

```
Install aily — AI agent session bridge for Discord/Slack (https://github.com/jiunbae/aily).
Install: `git clone https://github.com/jiunbae/aily.git && cd aily && ./aily init`
  (or: `npm install -g aily-cli && aily init`)
The wizard asks: platform (Discord/Slack/both) → bot token → channel ID → done.
Dashboard, SSH hosts, and agent auto-launch are optional (shown when "Use defaults?" is declined).
Config is saved to ~/.config/aily/env. Run `aily status` to verify, `aily doctor` to diagnose.
```

## What Happens After Setup

Once installed, aily works automatically in the background:

- **Agent finishes a task** — You get a Discord/Slack notification in a thread named after your tmux session, with the agent's response.
- **Agent asks a question** — The prompt appears in the same thread. Reply from your phone to answer it.
- **You reply in the thread** — Your message is forwarded to the agent's tmux session via SSH, as if you typed it at the keyboard.
- **Shell command output** — When the tmux session is running a plain shell (not an agent), command output is captured and relayed back to the thread automatically.

Every tmux session gets a dedicated thread. Start a session, get a thread. Close the session, the thread archives (or deletes).

You can also manage sessions directly from Discord/Slack using bridge commands: `!new <name> [host]`, `!kill <name>`, `!sessions`. Inside a thread, use shortcuts to send key sequences: `!c` (Ctrl+C), `!d` (Ctrl+D), `!z` (Ctrl+Z), `!q`, `!enter`, `!esc`.

Unlike one-way notification tools, aily provides a **full session bridge** — monitor output, send input, and `aily attach` to jump into the same terminal view from CLI.

## Bridge

The bridge bot connects to Discord/Slack and enables bidirectional communication. `aily init` offers to start it automatically.

```bash
aily bridge start     # runs in a tmux session (aily-bridge)
aily bridge status    # check if running
aily bridge logs      # view recent output
aily bridge restart   # restart after config changes
```

| Command | Description |
|---------|-------------|
| `!new <name> [host]` | Create a new tmux session + thread |
| `!kill <name>` | Kill a tmux session and archive its thread |
| `!sessions` | List active sessions across all hosts |

| Shortcut | Sends |
|----------|-------|
| `!c` | Ctrl+C |
| `!d` | Ctrl+D |
| `!z` | Ctrl+Z |
| `!q` | `q` |
| `!enter` | Enter |
| `!esc` | Escape |

## Dashboard

```bash
aily dashboard start   # runs as a background process
aily dashboard status  # check if running
aily dashboard logs    # tail recent output
aily dashboard stop    # stop the process
```

## Multi-host Setup

```bash
# Deploy aily to a remote host (copies files, config, runs init)
aily deploy my-server

# Or manually add a host
aily config set SSH_HOSTS "localhost,my-server"
```

`aily deploy` will: test SSH connectivity → clone aily to the remote host → copy your config → run `aily init --non-interactive` → add the host to `SSH_HOSTS`. Requires key-based SSH access (no password prompts).

For the full English documentation, see [README.md](README.md).

</details>

## License

[MIT](LICENSE)
