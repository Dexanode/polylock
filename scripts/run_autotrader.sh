#!/bin/bash
# Run Polymarket BTC 5m Auto Trader
# Usage: bash run_autotrader.sh [--bot 100|50] [--live] [--screen] [--token TOKEN] [--chat-id ID]
#
# Bots:
#   100  → poly_btc_5m_autotrader.py   ($100 spread threshold, entry ~0.78)
#   50   → poly_btc_5m_lock_50.py      ($50 spread threshold, entry ~0.65, user's preferred BOT B)
#
# Examples:
#   bash run_autotrader.sh --bot 50 --screen --token <TOKEN> --chat-id <ID>
#   bash run_autotrader.sh --bot 100 --live

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
MODE="paper"
BOT="100"
USE_SCREEN=false
TOKEN=""
CHAT_ID=""

# Parse args
while [[ $# -gt 0 ]]; do
    case "$1" in
        --bot) BOT="$2"; shift 2 ;;
        --live) MODE="live"; shift ;;
        --screen) USE_SCREEN=true; shift ;;
        --token) TOKEN="$2"; shift 2 ;;
        --chat-id) CHAT_ID="$2"; shift 2 ;;
        *) echo "Unknown arg: $1"; exit 1 ;;
    esac
done

# Load credentials from .env if exists
if [ -f "$SCRIPT_DIR/.env" ]; then
    export $(grep -v '^#' "$SCRIPT_DIR/.env" | xargs)
fi

# Resolve script and session name
if [ "$BOT" == "50" ]; then
    SCRIPT="poly_btc_5m_lock_50.py"
    SESSION="polybot50"
    LOG="/tmp/polybot50.log"
    LABEL="🧪 BOT B ($50 threshold)"
elif [ "$BOT" == "100" ]; then
    SCRIPT="poly_btc_5m_autotrader.py"
    SESSION="polybot100"
    LOG="/tmp/polybot100.log"
    LABEL="📊 BOT A ($100 threshold)"
else
    echo "❌ Unknown bot: $BOT (use 100 or 50)"
    exit 1
fi

# Build python command
PY_ARGS=""
if [ -n "$TOKEN" ]; then
    PY_ARGS="$PY_ARGS --telegram-token $TOKEN"
fi
if [ -n "$CHAT_ID" ]; then
    PY_ARGS="$PY_ARGS --chat-id $CHAT_ID"
fi
if [ "$MODE" == "live" ] && [ "$BOT" == "100" ]; then
    if [ -z "$POLYMARKET_PRIVATE_KEY" ]; then
        echo "❌ FATAL: POLYMARKET_PRIVATE_KEY not set!"
        exit 1
    fi
    PY_ARGS="$PY_ARGS --live"
fi

# Run
if [ "$USE_SCREEN" == true ]; then
    # Kill existing session if present
    screen -S "$SESSION" -X quit 2>/dev/null
    sleep 1
    echo "$LABEL starting in screen session '$SESSION'..."
    echo "   Log: $LOG"
    screen -dmS "$SESSION" bash -c "cd '$SCRIPT_DIR' && python3 -u '$SCRIPT' $PY_ARGS 2>&1 | tee -a '$LOG'"
    sleep 1
    screen -ls | grep "$SESSION"
else
    echo "$LABEL — $MODE mode"
    python3 "$SCRIPT_DIR/$SCRIPT" $PY_ARGS
fi
