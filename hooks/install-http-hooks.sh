#!/bin/bash
# Install aily HTTP hooks into ~/.claude/settings.json.
#
# Usage:
#   ./hooks/install-http-hooks.sh                    # localhost:8080
#   ./hooks/install-http-hooks.sh --url https://aily.example.com
#   ./hooks/install-http-hooks.sh --url https://aily.example.com --token AILY_TOKEN
#
# Requires: jq

set -euo pipefail

BASE_URL="http://localhost:8080"
TOKEN_VAR=""
HOOK_SECRET_VAR=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --url)  BASE_URL="${2%/}"; shift 2 ;;
    --token) TOKEN_VAR="$2"; shift 2 ;;
    --hook-secret) HOOK_SECRET_VAR="$2"; shift 2 ;;
    -h|--help)
      echo "Usage: $0 [--url BASE_URL] [--token ENV_VAR_NAME] [--hook-secret ENV_VAR_NAME]"
      echo "  --url          Dashboard URL (default: http://localhost:8080)"
      echo "  --token        Env var name for auth token (e.g. AILY_TOKEN)"
      echo "  --hook-secret  Env var name for HMAC hook secret (e.g. HOOK_SECRET)"
      exit 0 ;;
    *) echo "Unknown option: $1"; exit 1 ;;
  esac
done

if ! command -v jq &>/dev/null; then
  echo "Error: jq is required. Install with: brew install jq" >&2
  exit 1
fi

SETTINGS="$HOME/.claude/settings.json"
mkdir -p "$(dirname "$SETTINGS")"

# Build hook object
_hook_obj() {
  local url="$1" timeout="$2"
  local env_vars=()
  local headers='{}'

  if [[ -n "$TOKEN_VAR" ]]; then
    headers=$(jq -n --arg token "\$$TOKEN_VAR" '{"Authorization":("Bearer "+$token)}')
    env_vars+=("$TOKEN_VAR")
  fi
  if [[ -n "$HOOK_SECRET_VAR" ]]; then
    env_vars+=("$HOOK_SECRET_VAR")
  fi

  local env_arr
  env_arr=$(printf '%s\n' "${env_vars[@]}" | jq -R . | jq -s .)

  if [[ -n "$HOOK_SECRET_VAR" ]]; then
    jq -n --arg url "$url" --argjson timeout "$timeout" \
      --argjson headers "$headers" --argjson envVars "$env_arr" \
      --arg secret "\$$HOOK_SECRET_VAR" \
      '{type:"http", url:$url, timeout:$timeout, headers:($headers + {"X-Hook-Secret":$secret}), allowedEnvVars:$envVars}'
  elif [[ ${#env_vars[@]} -gt 0 ]]; then
    jq -n --arg url "$url" --argjson timeout "$timeout" \
      --argjson headers "$headers" --argjson envVars "$env_arr" \
      '{type:"http", url:$url, timeout:$timeout, headers:$headers, allowedEnvVars:$envVars}'
  else
    jq -n --arg url "$url" --argjson timeout "$timeout" \
      '{type:"http", url:$url, timeout:$timeout}'
  fi
}

# Build the hooks config
HOOKS=$(jq -n \
  --argjson stop "$(_hook_obj "$BASE_URL/api/hooks/stop" 10)" \
  --argjson session "$(_hook_obj "$BASE_URL/api/hooks/session" 5)" \
  --argjson tool "$(_hook_obj "$BASE_URL/api/hooks/tool-activity" 5)" \
  '{
    Stop: [{hooks: [$stop]}],
    SessionStart: [{hooks: [$session]}],
    SessionEnd: [{hooks: [$session]}],
    PostToolUse: [{hooks: [$tool]}]
  }')

# Read existing settings or start fresh
if [[ -f "$SETTINGS" ]]; then
  EXISTING=$(cat "$SETTINGS")
else
  EXISTING='{}'
fi

# Merge: add aily hooks without clobbering existing hooks
MERGED=$(echo "$EXISTING" | jq --argjson new "$HOOKS" '
  .hooks = ((.hooks // {}) * $new)
')

# Show diff
echo "=== Changes to $SETTINGS ==="
diff <(echo "$EXISTING" | jq . 2>/dev/null || echo "$EXISTING") <(echo "$MERGED" | jq .) || true
echo ""

read -rp "Apply these changes? [y/N] " confirm
if [[ "$confirm" != [yY]* ]]; then
  echo "Aborted."
  exit 0
fi

echo "$MERGED" | jq . > "$SETTINGS"
echo "Done. HTTP hooks installed to $SETTINGS"
echo ""
echo "Events configured:"
echo "  Stop         → $BASE_URL/api/hooks/stop"
echo "  SessionStart → $BASE_URL/api/hooks/session"
echo "  SessionEnd   → $BASE_URL/api/hooks/session"
echo "  PostToolUse  → $BASE_URL/api/hooks/tool-activity"
