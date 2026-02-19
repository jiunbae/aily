# Changelog

All notable changes to this project will be documented in this file.

## 2026.2.18 - 2026-02-18

### Features
- OpenCode agent support (notify-opencode.mjs plugin with session.idle hook)
- Full-text search (FTS5) across messages with snippet highlights
- Session export (JSON and Markdown formats)
- Shell completions for bash and zsh with session name autocomplete
- CLI commands: attach, export, version, --json flag
- Keyboard shortcuts overlay (? key) and g+key navigation
- Message role filter tabs (All/User/Assistant)

### Performance
- Multi-stage Dockerfile (smaller image, faster builds)
- WebSocket permessage-deflate compression
- Database composite indexes on hot query paths
- Hook delivery retry with exponential backoff (NOTIFY_MAX_RETRIES)

### Security
- Token bucket rate limiting per client IP (429 with Retry-After)
- Access logging middleware (method, path, status, duration)
- Structured JSON logging (LOG_FORMAT=json)
- Max WebSocket connection limit (50 clients)

### Infrastructure
- CI test gate (pytest runs before build/publish)
- Docker HEALTHCHECK directive
- SQLite DB backup CronJob (every 6h, gzip, 7-day retention)
- Graceful shutdown with WebSocket drain (shutdown_timeout=10s)

## 2026.2.17 - 2026-02-17

### Features
- Dashboard web UI (real-time sessions, messages, WebSocket)
- Discord + Slack message sync
- JSONL file ingestion for Claude Code sessions
- Dark/light theme with responsive design
- CLI tool (aily init/status/sessions/sync/logs/config/doctor)
- npm package distribution (aily-cli)
- curl one-liner installer
- Settings page with connectivity testing
- Bridge webhook integration (real-time events from Discord/Slack)
- Bulk session management (delete, update)
- Typing indicators
- Infinite scroll message history

### Performance
- SSH ControlMaster (persistent connections)
- Parallel host scanning via asyncio.gather
- Shared aiohttp.ClientSession for platform APIs

### Security
- Timing-safe token comparison (hmac.compare_digest)
- WebSocket authentication via query param
- Configurable DASHBOARD_URL/GITHUB_REPO (no hardcoded values)

### Infrastructure
- GitHub Actions npm publish on version tags
- K8s healthcheck CronJob (5-min interval)
- DASHBOARD_URL and GITHUB_REPO env vars in K8s
