#!/bin/bash
# Shared Discord API functions for all hooks.
# Source this file after setting DISCORD_BOT_TOKEN and DISCORD_CHANNEL_ID.

_DISCORD_API="https://discord.com/api/v10"
_DISCORD_AUTH="Authorization: Bot ${DISCORD_BOT_TOKEN}"
_DISCORD_GUILD_ID=""

discord_get_guild_id() {
  if [[ -n "$_DISCORD_GUILD_ID" ]]; then
    echo "$_DISCORD_GUILD_ID"
    return
  fi
  _DISCORD_GUILD_ID=$(curl -sf -H "$_DISCORD_AUTH" \
    "${_DISCORD_API}/channels/${DISCORD_CHANNEL_ID}" 2>/dev/null \
    | python3 -c "import sys,json; print(json.load(sys.stdin).get('guild_id',''))" 2>/dev/null || echo "")
  echo "$_DISCORD_GUILD_ID"
}

discord_find_thread() {
  local thread_name="$1"
  local guild_id thread_id=""
  guild_id=$(discord_get_guild_id)

  # 1. Active threads (must use guild endpoint)
  if [[ -n "$guild_id" ]]; then
    thread_id=$(curl -sf -H "$_DISCORD_AUTH" \
      "${_DISCORD_API}/guilds/${guild_id}/threads/active" 2>/dev/null \
      | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    for t in data.get('threads', []):
        if t.get('name') == '${thread_name}' and t.get('parent_id') == '${DISCORD_CHANNEL_ID}':
            print(t['id']); break
except: pass
" 2>/dev/null || echo "")
  fi

  # 2. Archived threads
  if [[ -z "$thread_id" ]]; then
    thread_id=$(curl -sf -H "$_DISCORD_AUTH" \
      "${_DISCORD_API}/channels/${DISCORD_CHANNEL_ID}/threads/archived/public" 2>/dev/null \
      | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    for t in data.get('threads', []):
        if t.get('name') == '${thread_name}': print(t['id']); break
except: pass
" 2>/dev/null || echo "")
  fi

  # 3. Channel messages (thread metadata)
  if [[ -z "$thread_id" ]]; then
    thread_id=$(curl -sf -H "$_DISCORD_AUTH" \
      "${_DISCORD_API}/channels/${DISCORD_CHANNEL_ID}/messages?limit=50" 2>/dev/null \
      | python3 -c "
import sys, json
try:
    msgs = json.load(sys.stdin)
    for m in msgs:
        t = m.get('thread', {})
        if t.get('name') == '${thread_name}': print(t['id']); break
except: pass
" 2>/dev/null || echo "")
  fi

  echo "$thread_id"
}

discord_create_thread() {
  local thread_name="$1"
  local starter_content="${2:-"tmux session: ${thread_name}"}"

  local payload msg_id thread_id
  payload=$(python3 -c "import json,sys; print(json.dumps({'content': sys.stdin.read().strip()}))" <<< "$starter_content")

  msg_id=$(curl -sf -X POST -H "$_DISCORD_AUTH" -H "Content-Type: application/json" \
    -d "$payload" \
    "${_DISCORD_API}/channels/${DISCORD_CHANNEL_ID}/messages" 2>/dev/null \
    | python3 -c "import sys,json; print(json.load(sys.stdin).get('id',''))" 2>/dev/null || echo "")

  if [[ -n "$msg_id" ]]; then
    local name_payload
    name_payload=$(python3 -c "import json; print(json.dumps({'name': '$thread_name'}))")
    thread_id=$(curl -sf -X POST -H "$_DISCORD_AUTH" -H "Content-Type: application/json" \
      -d "$name_payload" \
      "${_DISCORD_API}/channels/${DISCORD_CHANNEL_ID}/messages/${msg_id}/threads" 2>/dev/null \
      | python3 -c "import sys,json; print(json.load(sys.stdin).get('id',''))" 2>/dev/null || echo "")

    # Post welcome message with usage guide
    if [[ -n "$thread_id" ]]; then
      local welcome
      welcome=$(printf '**Welcome to %s** 👋\n\nType a message here to forward it to the tmux session.\n\n**Commands:**\n`!sessions` — list all sessions\n`!kill %s` — kill this session + archive thread' \
        "$thread_name" "${thread_name#\[agent\] }")
      discord_post_to_thread "$thread_id" "$welcome"
      echo "$thread_id"
    fi
  fi
}

discord_ensure_thread() {
  local thread_name="$1"
  local starter_content="${2:-}"
  local thread_id
  thread_id=$(discord_find_thread "$thread_name")

  if [[ -n "$thread_id" ]]; then
    # Unarchive if needed
    curl -sf -X PATCH -H "$_DISCORD_AUTH" -H "Content-Type: application/json" \
      -d '{"archived": false}' \
      "${_DISCORD_API}/channels/${thread_id}" > /dev/null 2>&1 || true
    echo "$thread_id"
  else
    if [[ -n "$starter_content" ]]; then
      discord_create_thread "$thread_name" "$starter_content"
    else
      discord_create_thread "$thread_name"
    fi
  fi
}

discord_archive_thread() {
  local thread_id="$1"
  curl -sf -X PATCH -H "$_DISCORD_AUTH" -H "Content-Type: application/json" \
    -d '{"archived": true}' \
    "${_DISCORD_API}/channels/${thread_id}" > /dev/null 2>&1 || true
}

discord_delete_thread() {
  local thread_id="$1"
  curl -sf -X DELETE -H "$_DISCORD_AUTH" \
    "${_DISCORD_API}/channels/${thread_id}" > /dev/null 2>&1 || true
}

discord_post_to_thread() {
  local thread_id="$1"
  local content="$2"
  local payload
  payload=$(python3 -c "import json,sys; print(json.dumps({'content': sys.stdin.read().strip()}))" <<< "$content")
  if [[ ${#payload} -gt 1990 ]]; then
    local short="${content:0:1800}..."
    payload=$(python3 -c "import json,sys; print(json.dumps({'content': sys.stdin.read().strip()}))" <<< "$short")
  fi
  curl -sf -X POST -H "$_DISCORD_AUTH" -H "Content-Type: application/json" \
    -d "$payload" \
    "${_DISCORD_API}/channels/${thread_id}/messages" > /dev/null 2>&1 || true
}
