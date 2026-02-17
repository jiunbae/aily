# Changelog

All notable changes to this project will be documented in this file.

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
