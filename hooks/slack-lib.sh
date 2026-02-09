#!/bin/bash
# Shared Slack API functions for all hooks.
# Source this file after setting SLACK_BOT_TOKEN and SLACK_CHANNEL_ID.

_SLACK_API="https://slack.com/api"
_SLACK_AUTH="Authorization: Bearer ${SLACK_BOT_TOKEN}"

slack_find_thread() {
  local thread_name="$1"
  local result
  result=$(curl -sf -H "$_SLACK_AUTH" \
    "${_SLACK_API}/conversations.history?channel=${SLACK_CHANNEL_ID}&limit=200" 2>/dev/null \
    | _THREAD_NAME="$thread_name" python3 -c "
import sys, json, os
try:
    name = os.environ['_THREAD_NAME']
    data = json.load(sys.stdin)
    if data.get('ok'):
        for msg in data.get('messages', []):
            text = msg.get('text', '')
            first_line = text.split('\n')[0].strip()
            if first_line == name or text.startswith(name):
                print(msg['ts'])
                break
except Exception:
    pass
" 2>/dev/null || echo "")
  echo "$result"
}

slack_create_thread() {
  local thread_name="$1"
  local starter_content="${2:-"tmux session: ${thread_name}"}"

  local payload parent_ts
  payload=$(python3 -c "import json,sys; print(json.dumps({'channel': sys.argv[1], 'text': sys.stdin.read().strip()}))" "$SLACK_CHANNEL_ID" <<< "$starter_content")

  parent_ts=$(curl -sf -X POST -H "$_SLACK_AUTH" -H "Content-Type: application/json" \
    -d "$payload" \
    "${_SLACK_API}/chat.postMessage" 2>/dev/null \
    | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('message',{}).get('ts','') if d.get('ok') else '')" 2>/dev/null || echo "")

  if [[ -n "$parent_ts" ]]; then
    # Post welcome message as thread reply
    local session_name="${thread_name#\[agent\] }"
    local welcome
    welcome=$(printf '*Welcome to %s*\n\nType a message here to forward it to the tmux session.\n\n*Commands:*\n`!sessions` — list all sessions\n`!kill %s` — kill this session + close thread' \
      "$thread_name" "$session_name")
    slack_post_to_thread "$parent_ts" "$welcome"
    echo "$parent_ts"
  fi
}

slack_ensure_thread() {
  local thread_name="$1"
  local starter_content="${2:-}"
  local thread_ts
  thread_ts=$(slack_find_thread "$thread_name")

  if [[ -n "$thread_ts" ]]; then
    # Slack threads auto-resurface when replied to; no unarchive needed
    echo "$thread_ts"
  else
    if [[ -n "$starter_content" ]]; then
      slack_create_thread "$thread_name" "$starter_content"
    else
      slack_create_thread "$thread_name"
    fi
  fi
}

slack_archive_thread() {
  local thread_ts="$1"
  # Slack has no thread archive concept. Post a closing message and add :lock: reaction.
  slack_post_to_thread "$thread_ts" ":lock: Thread archived. Session closed."
  # Add :lock: reaction to parent message
  curl -sf -X POST -H "$_SLACK_AUTH" -H "Content-Type: application/json" \
    -d "{\"channel\":\"${SLACK_CHANNEL_ID}\",\"timestamp\":\"${thread_ts}\",\"name\":\"lock\"}" \
    "${_SLACK_API}/reactions.add" > /dev/null 2>&1 || true
}

slack_delete_thread() {
  local thread_ts="$1"
  curl -sf -X POST -H "$_SLACK_AUTH" -H "Content-Type: application/json" \
    -d "{\"channel\":\"${SLACK_CHANNEL_ID}\",\"ts\":\"${thread_ts}\"}" \
    "${_SLACK_API}/chat.delete" > /dev/null 2>&1 || true
}

slack_post_to_thread() {
  local thread_ts="$1"
  local content="$2"
  local payload
  if [[ ${#content} -gt 3800 ]]; then
    content="${content:0:3800}..."
  fi
  payload=$(python3 -c "
import json, sys
print(json.dumps({
    'channel': sys.argv[1],
    'thread_ts': sys.argv[2],
    'text': sys.stdin.read().strip()
}))
" "$SLACK_CHANNEL_ID" "$thread_ts" <<< "$content")
  curl -sf -X POST -H "$_SLACK_AUTH" -H "Content-Type: application/json" \
    -d "$payload" \
    "${_SLACK_API}/chat.postMessage" > /dev/null 2>&1 || true
}
