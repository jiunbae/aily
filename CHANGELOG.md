# Changelog

All notable changes to this project will be documented in this file.

## 2026.3.12 - 2026-03-12

### Security
- **Mandatory authentication** — Dashboard now blocks all requests (503) when `DASHBOARD_TOKEN` is not set, eliminating the dev mode bypass that allowed unauthenticated access
- Hook endpoints require Bearer token when `HOOK_SECRET` is not configured
- Auto-generated random token on startup when no token is configured (with console warning)
- Config file now reads `DASHBOARD_TOKEN` in addition to `AILY_AUTH_TOKEN`

### Infrastructure
- Server binds to `0.0.0.0` by default (was `127.0.0.1`) — fixes K8s liveness probe failures
- SSH control socket directory moved to `/tmp/aily-ssh-ctl` — fixes PermissionError with read-only `~/.ssh` in containers
- Added `tests/**` and `requirements-dev.txt` to CI trigger paths
- Restored Gitea Actions deploy workflow
- Added GitHub webhook for immediate Gitea mirror sync

### Tests
- Test suite updated for mandatory auth model
- `AuthenticatedClient` wrapper auto-injects Bearer token in test fixtures
- Separate `noauth_client` and `auth_client` fixtures for auth-specific tests

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
