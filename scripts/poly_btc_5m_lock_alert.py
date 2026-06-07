#!/usr/bin/env python3
"""
Polymarket BTC Up/Down 5m — LOCK Strategy Alert Bot
Monitors BTC price and sends Telegram alerts when a 5m window
has spread > $100 at minute 4 (LOCK setup).

ALERT ONLY — no auto-trading. You click manually on Polymarket.

Usage:
    python3 poly_btc_5m_lock_alert.py --telegram-token TOKEN --chat-id ID
    python3 poly_btc_5m_lock_alert.py (console only)
"""

import argparse
import json
import urllib.request
import time
from datetime import datetime, timezone, timedelta

# ============ CONFIG ============
CHECK_INTERVAL = 5          # seconds between price checks
SPREAD_THRESHOLD = 100      # USD spread needed for LOCK alert
ALERT_WINDOW_START = 4 * 60  # seconds: start checking at minute 4
ALERT_WINDOW_END = 4 * 60 + 55  # seconds: stop at minute 4:55
MINUTES_PER_WINDOW = 5

# APIs
BINANCE_URL = "https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT"
COINBASE_URL = "https://api.coinbase.com/v2/exchange-rates?currency=BTC"
YAHOO_URL = "https://query1.finance.yahoo.com/v8/finance/chart/BTC-USD?interval=1m&range=1d"
TELEGRAM_URL = "https://api.telegram.org/bot{token}/sendMessage"

# Fallbacks
PRICE_SOURCES = ["binance", "coinbase", "yahoo"]


def fetch_btc_price(source: str = "binance") -> float:
    """Fetch current BTC-USD price."""
    try:
        if source == "binance":
            req = urllib.request.Request(BINANCE_URL, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
            return float(data["price"])
        elif source == "coinbase":
            req = urllib.request.Request(COINBASE_URL, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
            return float(data["data"]["rates"]["USD"])
        elif source == "yahoo":
            req = urllib.request.Request(YAHOO_URL, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
            result = data["chart"]["result"][0]
            closes = result["indicators"]["quote"][0]["close"]
            # Get last non-None close
            for c in reversed(closes):
                if c is not None:
                    return float(c)
            return 0.0
    except Exception as e:
        print(f"[WARN] {source} failed: {e}")
        return 0.0


def get_btc_price() -> float:
    """Try multiple sources until one works."""
    for src in PRICE_SOURCES:
        price = fetch_btc_price(src)
        if price > 0:
            return price
    return 0.0


def send_telegram(msg: str, token: str, chat_id: str):
    """Send alert to Telegram."""
    try:
        url = TELEGRAM_URL.format(token=token)
        payload = json.dumps({"chat_id": chat_id, "text": msg, "parse_mode": "Markdown"}).encode()
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=5)
        print("📤 Telegram sent.")
    except Exception as e:
        print(f"[WARN] Telegram failed: {e}")


def get_next_5m_boundary(now: datetime) -> datetime:
    """Get the next 5-minute boundary (e.g., :00, :05, :10)."""
    minute = now.minute
    second = now.second
    microsecond = now.microsecond

    # Round up to next 5m
    next_min = ((minute // 5) + 1) * 5
    if next_min >= 60:
        next_boundary = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    else:
        next_boundary = now.replace(minute=next_min, second=0, microsecond=0)
    return next_boundary


def format_time(dt: datetime) -> str:
    return dt.strftime("%H:%M:%S")


def get_window_number(now: datetime) -> int:
    """Which 5m window within the hour (0-11)."""
    return now.minute // 5


def build_alert_msg(window_start: datetime, ptb: float, current: float,
                    spread: float, direction: str, est_price: float) -> str:
    """Build a sexy Telegram alert message."""
    now = datetime.now(timezone.utc)
    time_left = 300 - ((now - window_start).total_seconds() % 300)
    mins = int(time_left // 60)
    secs = int(time_left % 60)

    # Estimasi profit
    size = 3.0  # Suggest $3 for $10 bankroll
    gross_profit = size * (1.0 - est_price)
    fee = size * 0.02
    net_profit = gross_profit - fee

    arrow = "🟢" if direction == "UP" else "🔴"

    msg = (
        f"🚨 *POLY LOCK ALERT* 🚨\n"
        f"{'=' * 28}\n"
        f"💎 BTC Up/Down 5m\n"
        f"⏰ Window: `{format_time(window_start)}` ET\n"
        f"🎯 Price to Beat: `${ptb:,.2f}`\n"
        f"📊 Current: `${current:,.2f}`\n"
        f"📈 Spread: `{spread:+.2f}` USD\n"
        f"{'=' * 28}\n"
        f"{arrow} *DIRECTION: {direction}*\n"
        f"💰 Est. Entry Price: `{est_price:.2f}`\n"
        f"💵 Suggested Size: `${size:.0f}`\n"
        f"📈 Est. Net Profit: `${net_profit:.2f}`\n"
        f"⏳ Resolve in: `{mins}m {secs}s`\n"
        f"{'=' * 28}\n"
        f"_Go to Polymarket → BTC Up/Down 5m → Buy {direction}_\n"
        f"_LOCK strategy: minute 4+ ✓ → Spread >$100 ✓_"
    )
    return msg


def build_window_msg(window_start: datetime, ptb: float) -> str:
    """Notify that a new window has opened."""
    return (
        f"🌅 *NEW WINDOW OPEN*\n"
        f"Window: `{format_time(window_start)}`\n"
        f"Price to Beat: `${ptb:,.2f}`\n"
        f"Monitoring for LOCK setup (min 4, spread >$100)..."
    )


def build_skip_msg(window_start: datetime, ptb: float, final_spread: float) -> str:
    """Notify that window closed with no LOCK setup."""
    return (
        f"🌙 *WINDOW CLOSED* — NO LOCK\n"
        f"Window: `{format_time(window_start)}`\n"
        f"Price to Beat: `${ptb:,.2f}`\n"
        f"Final Spread: `${final_spread:+.2f}`\n"
        f"_Spread < $100. Skipped._"
    )


def estimate_polymarket_price(spread: float) -> float:
    """Estimate Polymarket share price based on BTC spread."""
    abs_spread = abs(spread)
    if abs_spread < 100:
        return 0.50
    elif abs_spread < 130:
        return 0.78
    elif abs_spread < 160:
        return 0.83
    elif abs_spread < 200:
        return 0.87
    else:
        return 0.91


def main():
    parser = argparse.ArgumentParser(description="BTC 5m LOCK Alert Bot")
    parser.add_argument("--telegram-token", help="Telegram Bot Token")
    parser.add_argument("--chat-id", help="Telegram Chat ID")
    parser.add_argument("--spread", type=int, default=100, help="Spread threshold (default: 100)")
    parser.add_argument("--test", action="store_true", help="Send test alert and exit")
    args = parser.parse_args()

    token = args.telegram_token
    chat_id = args.chat_id
    spread_threshold = args.spread

    def notify(msg: str):
        print(msg)
        if token and chat_id:
            send_telegram(msg, token, chat_id)

    # Test mode
    if args.test:
        test_msg = build_alert_msg(
            datetime.now(timezone.utc), 80421.76, 80550.00,
            128.24, "UP", 0.85
        )
        notify(test_msg)
        return

    print("=" * 50)
    print("🚀 BTC 5m LOCK Alert Bot Starting...")
    print(f"   Spread Threshold: ${spread_threshold}")
    print(f"   Check Interval: {CHECK_INTERVAL}s")
    print(f"   Alert Window: Minute 4:00–4:55")
    if token and chat_id:
        print("   Telegram: ENABLED")
        notify("🚀 *BTC LOCK Bot Active*\nMonitoring BTC for 5m LOCK setups...")
    else:
        print("   Telegram: DISABLED (console only)")
    print("=" * 50)

    # State
    current_window_start: Optional[datetime] = None
    price_to_beat: float = 0.0
    lock_alerted: bool = False
    window_notified: bool = False
    last_window_num: int = -1

    while True:
        now = datetime.now(timezone.utc)
        btc_price = get_btc_price()

        if btc_price == 0:
            print(f"[{format_time(now)}] Failed to fetch BTC price. Retrying...")
            time.sleep(CHECK_INTERVAL)
            continue

        # Determine current window
        window_num = get_window_number(now)
        window_start = now.replace(minute=window_num * 5, second=0, microsecond=0)
        seconds_into_window = (now - window_start).total_seconds()

        # New window started
        if window_num != last_window_num:
            # Close previous window if not alerted
            if current_window_start and not lock_alerted and price_to_beat > 0:
                final_spread = btc_price - price_to_beat
                msg = build_skip_msg(current_window_start, price_to_beat, final_spread)
                print(f"\n{msg}\n")
                if token and chat_id:
                    send_telegram(msg, token, chat_id)

            current_window_start = window_start
            price_to_beat = btc_price
            lock_alerted = False
            window_notified = False
            last_window_num = window_num

            msg = build_window_msg(window_start, price_to_beat)
            print(f"\n{msg}\n")
            if token and chat_id:
                send_telegram(msg, token, chat_id)
            window_notified = True

        # Calculate spread
        spread = btc_price - price_to_beat
        abs_spread = abs(spread)

        # Log every minute
        if int(seconds_into_window) % 60 == 0:
            direction = "UP" if spread > 0 else "DOWN"
            print(f"[{format_time(now)}] BTC: ${btc_price:,.2f} | PTB: ${price_to_beat:,.2f} | "
                  f"Spread: {spread:+.2f} ({direction}) | Min: {int(seconds_into_window // 60)}")

        # LOCK alert condition: minute 4+, spread > threshold, not yet alerted
        if not lock_alerted and ALERT_WINDOW_START <= seconds_into_window <= ALERT_WINDOW_END:
            if abs_spread >= spread_threshold:
                direction = "UP" if spread > 0 else "DOWN"
                est_price = estimate_polymarket_price(spread)

                msg = build_alert_msg(window_start, price_to_beat, btc_price,
                                      spread, direction, est_price)
                print(f"\n{'='*50}")
                print(msg)
                print(f"{'='*50}\n")
                if token and chat_id:
                    send_telegram(msg, token, chat_id)
                lock_alerted = True

        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()
