# Changelog

All notable changes to this project will be documented in this file.

## 2026.7.11 - 2026-07-11

### Security
- **Command execution locked to the configured channel** — previously any member of any channel the bot could read could run `!new ... -- <shell>` and execute commands on the SSH hosts; commands and relays are now accepted only from the configured channel and its threads
- SSH host names are now validated, rejecting a leading `-` that could smuggle extra options into the SSH command
- Settings connectivity test now validates the user-supplied dashboard URL (scheme check, link-local addresses rejected) to prevent server-side request forgery
- Login rate limiting is now keyed on the real client IP (honoring `TRUST_PROXY`) so a single proxy address can no longer lock out every user at once
- `/healthz` no longer leaks database exception text to unauthenticated clients

### Bug Fixes
- Slack `!new` threads now work — replies are forwarded, `!kill` can find them, and duplicate parent messages are no longer created
- Custom `THREAD_NAME_FORMAT` values now route correctly through both the Discord and Slack bridges (a custom format previously broke all routing)
- Fixed a session-limit false positive: a message merely mentioning "rate limit" or "429" no longer self-triggers an endless resend loop
- Fixed command-queue double-execution that could inject the same command into a live session twice
- JSONL ingestion no longer skips lines on busy sessions and no longer produces bogus 1970 timestamps
- Session commands are no longer misrouted to a similarly named session (e.g. `web` → `webserver`)
- macOS (bash 3.2) hook compatibility: hooks no longer fail with "bad substitution", notifications keep their body and post from the correct directory, log rotation now runs, and oversized notifications are no longer dropped
- Dashboard API input validation: a malformed JSON body now returns 400 instead of crashing to 500, and a negative `limit` no longer returns the entire table
- Dashboard database writes are now atomic, preventing partial writes and broken rollbacks on the shared connection
- Fixed a WebSocket task leak that occurred when a connection errored or was cancelled
- Distinct messages that share a 200-character prefix are no longer silently dropped
- Discord background posts keep working across gateway reconnects (no more "Session is closed"); Slack event processing no longer blocks on a single slow event

### Infrastructure
- Docker image now bundles the modules required by every bridge mode (previously missing `bridge_core`, `multiplexer`, `session_limit`)
- Container shutdown now stops all bridge processes cleanly
- `aily status`/`attach` no longer crash when run outside a multiplexer, and config values containing backslashes no longer crash env parsing
- Release safety: publishing now verifies the git tag matches `package.json`, the deploy token is passed via `GIT_ASKPASS` instead of being embedded in the clone URL, and the npm tarball no longer ships `__pycache__`

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
