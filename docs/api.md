# API Reference

## Authentication

All endpoints except `/healthz` and `/api/install.sh` require authentication when `DASHBOARD_TOKEN` is set.

- **API endpoints:** `Authorization: Bearer <token>` header
- **WebSocket:** `?token=<token>` query parameter
- **Browser:** Session cookie (set after login at `/login`)
- **Hook endpoint** (`/api/hooks/event`): Accepts either `X-Hook-Signature` HMAC (when `HOOK_SECRET` is set) or `Bearer` token

If `DASHBOARD_TOKEN` is not configured, the server returns `503 Service Unavailable` — all requests are blocked until a token is set.

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
