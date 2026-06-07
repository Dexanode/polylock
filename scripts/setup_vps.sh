#!/bin/bash
# Setup script for BTC 5m LOCK Alert Bot on VPS
# Tested on Ubuntu 22.04/24.04

set -e

echo "=========================================="
echo "  VPS Setup for Poly BTC 5m LOCK Bot"
echo "=========================================="

# Update system
echo "[1/5] Updating system..."
apt-get update -qq

# Install Python & pip if not exists
if ! command -v python3 &> /dev/null; then
    echo "[2/5] Installing Python3..."
    apt-get install -y -qq python3 python3-pip
else
    echo "[2/5] Python3 already installed."
fi

# Create bot directory
BOT_DIR="/opt/poly-btc-bot"
mkdir -p $BOT_DIR
echo "[3/5] Created directory: $BOT_DIR"

# Copy bot script (run this from the scripts directory)
SCRIPT_SOURCE="$(cd "$(dirname "$0")" && pwd)/poly_btc_5m_lock_alert.py"
if [ -f "$SCRIPT_SOURCE" ]; then
    cp "$SCRIPT_SOURCE" $BOT_DIR/
    echo "[4/5] Copied bot script."
else
    echo "[ERROR] poly_btc_5m_lock_alert.py not found!"
    echo "        Make sure to run this script from the same directory."
    exit 1
fi

# Create systemd service
cat > /etc/systemd/system/poly-btc-bot.service << 'EOF'
[Unit]
Description=Polymarket BTC 5m LOCK Alert Bot
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/poly-btc-bot
ExecStart=/usr/bin/python3 -u /opt/poly-btc-bot/poly_btc_5m_lock_alert.py --telegram-token ${POLY_TOKEN} --chat-id ${POLY_CHAT_ID}
Restart=always
RestartSec=10
Environment="PYTHONUNBUFFERED=1"
StandardOutput=append:/var/log/poly-btc-bot.log
StandardError=append:/var/log/poly-btc-bot.log

[Install]
WantedBy=multi-user.target
EOF

echo "[5/5] Created systemd service."

echo ""
echo "=========================================="
echo "  Setup Complete!"
echo "=========================================="
echo ""
echo "Next steps:"
echo "  1. Set Telegram credentials:"
echo "     export POLY_TOKEN='your_bot_token'"
echo "     export POLY_CHAT_ID='your_chat_id'"
echo ""
echo "  2. Start the bot:"
echo "     systemctl daemon-reload"
echo "     systemctl start poly-btc-bot"
echo "     systemctl enable poly-btc-bot"
echo ""
echo "  3. Check logs:"
echo "     journalctl -u poly-btc-bot -f"
echo "     or"
echo "     tail -f /var/log/poly-btc-bot.log"
echo ""
echo "  4. Check status:"
echo "     systemctl status poly-btc-bot"
echo ""
