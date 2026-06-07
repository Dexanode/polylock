#!/bin/bash
# Run BTC 5m LOCK Alert Bot
# Usage: bash run_poly_lock_alert.sh

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PID_FILE="/tmp/poly_btc_lock_alert.pid"

# Read Telegram config from env or hardcode here
TOKEN="${POLY_TELEGRAM_TOKEN:-YOUR_BOT_TOKEN}"
CHAT_ID="${POLY_CHAT_ID:-YOUR_CHAT_ID}"

if [ -f "$PID_FILE" ]; then
    OLD_PID=$(cat "$PID_FILE")
    if ps -p "$OLD_PID" > /dev/null 2>&1; then
        echo "Bot already running (PID: $OLD_PID)"
        exit 0
    fi
fi

echo "Starting BTC 5m LOCK Alert Bot..."
echo "Logs: /tmp/poly_lock_alert.log"
cd "$SCRIPT_DIR"
nohup python3 -u poly_btc_5m_lock_alert.py \
    --telegram-token "$TOKEN" \
    --chat-id "$CHAT_ID" \
    > /tmp/poly_lock_alert.log 2>&1 &
NEW_PID=$!
echo $NEW_PID > "$PID_FILE"
echo "Started (PID: $NEW_PID)"
