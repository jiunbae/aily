# API Reference

## Authentication

All endpoints except `/healthz` and `/api/hooks/event` require a `Bearer` token in the `Authorization` header when `DASHBOARD_TOKEN` is set.

## Endpoints

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

## Dashboard Routes

| Page | Path | Description |
|------|------|-------------|
| Home | `/` | Overview with stats and recent activity |
| Sessions | `/sessions` | Live session list with status, messages, send input |
| Session Detail | `/sessions/{name}` | Full message history and controls for a session |
| Settings | `/settings` | Platform status, SSH hosts, feature toggles |

## WebSocket

Connect to `/ws` with a `token` query parameter or session cookie. The server pushes real-time events for session updates, new messages, and status changes.
