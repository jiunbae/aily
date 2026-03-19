#!/bin/bash
# Shared Discord API functions for all hooks.
# Source this file after setting DISCORD_BOT_TOKEN and DISCORD_CHANNEL_ID.

# Expects log.sh to be sourced by the caller (discord-post.sh)

_DISCORD_API="https://discord.com/api/v10"
_DISCORD_AUTH="Authorization: Bot ${DISCORD_BOT_TOKEN}"
_DISCORD_GUILD_ID=""

discord_get_guild_id() {
  if [[ -n "$_DISCORD_GUILD_ID" ]]; then
    echo "$_DISCORD_GUILD_ID"
    return
  fi
  local resp
  resp=$(curl -sf -H "$_DISCORD_AUTH" \
    "${_DISCORD_API}/channels/${DISCORD_CHANNEL_ID}" 2>&1) || {
    _aily_log "ERR" "discord: get guild_id failed: $resp"; resp=""
  }
  if [[ -z "$resp" || "$resp" != "{"* ]]; then
    _aily_log "ERR" "discord: guild_id response is not valid JSON: ${resp:0:120}"
    _DISCORD_GUILD_ID=""
  else
    _DISCORD_GUILD_ID=$(echo "$resp" | python3 -c "import sys,json; print(json.load(sys.stdin).get('guild_id',''))" 2>/dev/null || echo "")
  fi
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
      | _THREAD_NAME="$thread_name" _PARENT_ID="$DISCORD_CHANNEL_ID" python3 -c "
import sys, json, os
try:
    name = os.environ['_THREAD_NAME']
    parent = os.environ['_PARENT_ID']
    data = json.load(sys.stdin)
    for t in data.get('threads', []):
        if t.get('name') == name and t.get('parent_id') == parent:
            print(t['id']); break
except Exception: pass
" 2>/dev/null || echo "")
  fi

  # 2. Archived threads
  if [[ -z "$thread_id" ]]; then
    thread_id=$(curl -sf -H "$_DISCORD_AUTH" \
      "${_DISCORD_API}/channels/${DISCORD_CHANNEL_ID}/threads/archived/public" 2>/dev/null \
      | _THREAD_NAME="$thread_name" python3 -c "
import sys, json, os
try:
    name = os.environ['_THREAD_NAME']
    data = json.load(sys.stdin)
    for t in data.get('threads', []):
        if t.get('name') == name: print(t['id']); break
except Exception: pass
" 2>/dev/null || echo "")
  fi

  # 3. Channel messages (thread metadata)
  if [[ -z "$thread_id" ]]; then
    thread_id=$(curl -sf -H "$_DISCORD_AUTH" \
      "${_DISCORD_API}/channels/${DISCORD_CHANNEL_ID}/messages?limit=50" 2>/dev/null \
      | _THREAD_NAME="$thread_name" python3 -c "
import sys, json, os
try:
    name = os.environ['_THREAD_NAME']
    msgs = json.load(sys.stdin)
    for m in msgs:
        t = m.get('thread', {})
        if t.get('name') == name: print(t['id']); break
except Exception: pass
" 2>/dev/null || echo "")
  fi

  echo "$thread_id"
}

discord_create_thread() {
  local thread_name="$1"
  local starter_content="${2:-"tmux session: ${thread_name}"}"

  local payload msg_id thread_id
  payload=$(python3 -c "import json,sys; print(json.dumps({'content': sys.stdin.read().strip()}))" <<< "$starter_content")
  if [[ -z "$payload" || "$payload" != "{"* ]]; then
    _aily_log "ERR" "discord_create_thread: failed to build JSON payload for starter message"
    return 1
  fi

  local msg_resp
  msg_resp=$(curl -sf -X POST -H "$_DISCORD_AUTH" -H "Content-Type: application/json" \
    -d "$payload" \
    "${_DISCORD_API}/channels/${DISCORD_CHANNEL_ID}/messages" 2>&1) || {
    _aily_log "ERR" "discord: create starter message failed: $msg_resp"; msg_resp=""
  }
  if [[ -z "$msg_resp" || "$msg_resp" != "{"* ]]; then
    _aily_log "ERR" "discord: starter message response is not valid JSON: ${msg_resp:0:120}"
    msg_id=""
  else
    msg_id=$(echo "$msg_resp" | python3 -c "import sys,json; print(json.load(sys.stdin).get('id',''))" 2>/dev/null || echo "")
  fi

  if [[ -n "$msg_id" ]]; then
    local name_payload
    name_payload=$(python3 -c "import json,sys; print(json.dumps({'name': sys.stdin.read().strip()}))" <<< "$thread_name")
    if [[ -z "$name_payload" || "$name_payload" != "{"* ]]; then
      _aily_log "ERR" "discord_create_thread: failed to build JSON payload for thread name"
      return 1
    fi
    local thread_resp
    thread_resp=$(curl -sf -X POST -H "$_DISCORD_AUTH" -H "Content-Type: application/json" \
      -d "$name_payload" \
      "${_DISCORD_API}/channels/${DISCORD_CHANNEL_ID}/messages/${msg_id}/threads" 2>&1) || {
      _aily_log "ERR" "discord: create thread failed: $thread_resp"; thread_resp=""
    }
    if [[ -z "$thread_resp" || "$thread_resp" != "{"* ]]; then
      _aily_log "ERR" "discord: create thread response is not valid JSON: ${thread_resp:0:120}"
      thread_id=""
    else
      thread_id=$(echo "$thread_resp" | python3 -c "import sys,json; print(json.load(sys.stdin).get('id',''))" 2>/dev/null || echo "")
    fi

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
    local unarch_resp
    unarch_resp=$(curl -sf -X PATCH -H "$_DISCORD_AUTH" -H "Content-Type: application/json" \
      -d '{"archived": false}' \
      "${_DISCORD_API}/channels/${thread_id}" 2>&1) || _aily_log "ERR" "discord: unarchive thread failed: $unarch_resp"
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
  if [[ -z "$payload" || "$payload" != "{"* ]]; then
    _aily_log "ERR" "discord_post_to_thread: failed to build JSON payload"
    return 1
  fi
  if [[ ${#payload} -gt 1990 ]]; then
    local short="${content:0:1800}..."
    payload=$(python3 -c "import json,sys; print(json.dumps({'content': sys.stdin.read().strip()}))" <<< "$short")
    if [[ -z "$payload" || "$payload" != "{"* ]]; then
      _aily_log "ERR" "discord_post_to_thread: failed to build truncated JSON payload"
      return 1
    fi
  fi
  local post_resp
  post_resp=$(curl -sf -X POST -H "$_DISCORD_AUTH" -H "Content-Type: application/json" \
    -d "$payload" \
    "${_DISCORD_API}/channels/${thread_id}/messages" 2>&1) || _aily_log "ERR" "discord: post to thread failed: $post_resp"
}
