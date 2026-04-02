#!/bin/bash
set -e

case "${BRIDGE_MODE:-dashboard}" in
  discord)
    echo "Starting Discord bridge..."
    exec python3 /app/agent-bridge.py
    ;;
  slack)
    echo "Starting Slack bridge..."
    exec python3 /app/slack-bridge.py
    ;;
  dashboard)
    echo "Starting dashboard..."
    exec python3 -m dashboard
    ;;
  all)
    echo "Starting dashboard + bridges..."

    BRIDGE_PIDS=()
    _restart_backoff() {
      local name="$1" cmd="$2" delay=1
      while true; do
        echo "Starting ${name}..."
        $cmd &
        local pid=$!
        BRIDGE_PIDS+=("$pid")
        wait "$pid" || true
        echo "${name} (pid $pid) exited, restarting in ${delay}s..."
        sleep "$delay"
        delay=$((delay < 30 ? delay * 2 : 30))
      done
    }

    _cleanup() {
      echo "Caught signal, shutting down..."
      for pid in "${BRIDGE_PIDS[@]}" "$DASHBOARD_PID"; do
        kill "$pid" 2>/dev/null || true
      done
      wait
      exit 0
    }
    trap _cleanup SIGTERM SIGINT

    python3 -m dashboard &
    DASHBOARD_PID=$!
    # Wait for dashboard to be healthy before starting bridges
    for i in $(seq 1 30); do
      if python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/healthz')" 2>/dev/null; then
        break
      fi
      sleep 1
    done
    # Start configured bridges with restart + backoff
    if [ -n "$DISCORD_BOT_TOKEN" ]; then
      _restart_backoff "discord-bridge" "python3 /app/agent-bridge.py" &
    fi
    if [ -n "$SLACK_BOT_TOKEN" ]; then
      _restart_backoff "slack-bridge" "python3 /app/slack-bridge.py" &
    fi
    wait $DASHBOARD_PID
    ;;
  *)
    echo "Unknown BRIDGE_MODE: ${BRIDGE_MODE}. Use: discord, slack, dashboard, or all"
    exit 1
    ;;
esac
