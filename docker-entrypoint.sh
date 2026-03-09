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
    python3 -m dashboard &
    DASHBOARD_PID=$!
    # Wait for dashboard to be healthy before starting bridges
    for i in $(seq 1 30); do
      if python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/healthz')" 2>/dev/null; then
        break
      fi
      sleep 1
    done
    # Start configured bridges
    if [ -n "$DISCORD_BOT_TOKEN" ]; then
      python3 /app/agent-bridge.py &
    fi
    if [ -n "$SLACK_BOT_TOKEN" ]; then
      python3 /app/slack-bridge.py &
    fi
    wait $DASHBOARD_PID
    ;;
  *)
    echo "Unknown BRIDGE_MODE: ${BRIDGE_MODE}. Use: discord, slack, dashboard, or all"
    exit 1
    ;;
esac
