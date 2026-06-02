#!/usr/bin/env python3
"""
Polymarket BTC 5m LOCK Strategy — AUTO TRADER

WARNING: This script can spend REAL MONEY from your wallet.
Default mode is PAPER (dry run). Use --live ONLY after testing.

Requirements:
  - Python 3.9+
  - pip install web3 requests eth-account python-dotenv
  - Polygon wallet with USDC
  - POLYMARKET_PRIVATE_KEY env var (with 0x prefix)

Usage:
  # Paper mode (default) — logs only, no trades
  python3 poly_btc_5m_autotrader.py

  # Live mode — REAL MONEY
  export POLYMARKET_PRIVATE_KEY="0x..."
  export POLY_RPC="https://polygon-rpc.com"
  python3 poly_btc_5m_autotrader.py --live --telegram-token TOKEN --chat-id ID
"""

import argparse
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from typing import Optional, Dict, List, Tuple
from dataclasses import dataclass, field
from enum import Enum

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------

CHECK_INTERVAL = 5          # seconds
SPREAD_THRESHOLD = 100      # USD
ALERT_WINDOW_START = 4 * 60
ALERT_WINDOW_END = 4 * 60 + 55
FEE_RATE = 0.02

# Signal filters (FastLoop-inspired)
MIN_VOLUME_RATIO = 0.5      # Skip if current volume < 0.5x 10-candle avg
MOMENTUM_THRESHOLD = 0.1    # Max opposing momentum % allowed
EV_THRESHOLD = 0.0          # Min expected value per share
KLINES_URL = "https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1m&limit=20"

# Price sources
BINANCE_URL = "https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT"
YAHOO_URL = "https://query1.finance.yahoo.com/v8/finance/chart/BTC-USD?interval=1m&range=1d"

# Polymarket CLOB
CLOB_HOST = "https://clob.polymarket.com"
GAMMA_HOST = "https://gamma-api.polymarket.com"

# ---------------------------------------------------------------------------
# ENV
# ---------------------------------------------------------------------------

PRIVATE_KEY = os.environ.get("POLYMARKET_PRIVATE_KEY", "")
POLY_RPC = os.environ.get("POLY_RPC", "https://polygon-rpc.com")

# ---------------------------------------------------------------------------
# STATE
# ---------------------------------------------------------------------------

class Mode(Enum):
    PAPER = "paper"
    LIVE = "live"


class Direction(Enum):
    UP = "UP"
    DOWN = "DOWN"
    NONE = "NONE"


@dataclass
class WindowState:
    start: datetime
    ptb: float                     # Price to Beat
    alerted: bool = False
    traded: bool = False
    direction: Direction = Direction.NONE
    entry_price: float = 0.0
    size: float = 0.0
    final_spread: float = 0.0
    result: str = ""               # WIN / LOSS / SKIP / PENDING


@dataclass
class DailyStats:
    date: str
    trades: int = 0
    wins: int = 0
    losses: int = 0
    profit: float = 0.0
    max_drawdown: float = 0.0
    peak_bankroll: float = 0.0

# ---------------------------------------------------------------------------
# PRICE FETCHERS
# ---------------------------------------------------------------------------

def fetch_btc_binance() -> float:
    try:
        req = urllib.request.Request(BINANCE_URL, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            return float(json.loads(resp.read())["price"])
    except Exception as e:
        print(f"[WARN] Binance: {e}")
        return 0.0


def fetch_btc_yahoo() -> float:
    try:
        req = urllib.request.Request(YAHOO_URL, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        closes = data["chart"]["result"][0]["indicators"]["quote"][0]["close"]
        for c in reversed(closes):
            if c is not None:
                return float(c)
        return 0.0
    except Exception as e:
        print(f"[WARN] Yahoo: {e}")
        return 0.0


def fetch_btc_binance_signal() -> Dict:
    """
    Fetch BTC price, volume ratio, and 1m momentum from Binance klines.
    Returns empty dict on failure (bot falls back to simple price).
    """
    try:
        req = urllib.request.Request(KLINES_URL, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        if not data or len(data) < 5:
            return {}
        closes = [float(c[4]) for c in data]
        volumes = [float(c[5]) for c in data]
        current_price = closes[-1]
        prev_price = closes[-2]
        momentum_pct = ((current_price - prev_price) / prev_price) * 100 if prev_price > 0 else 0.0
        current_vol = volumes[-1]
        avg_vol = sum(volumes[-11:-1]) / 10 if len(volumes) >= 11 else sum(volumes[:-1]) / max(1, len(volumes) - 1)
        volume_ratio = current_vol / avg_vol if avg_vol > 0 else 1.0
        return {
            "price": current_price,
            "momentum_pct": momentum_pct,
            "volume_ratio": volume_ratio,
            "current_volume": current_vol,
            "avg_volume": avg_vol,
        }
    except Exception as e:
        print(f"[WARN] Binance signal: {e}")
        return {}


def get_btc_price() -> float:
    p = fetch_btc_binance()
    if p > 0:
        return p
    return fetch_btc_yahoo()


# ---------------------------------------------------------------------------
# TELEGRAM
# ---------------------------------------------------------------------------

def send_telegram(msg: str, token: str, chat_id: str):
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = json.dumps({"chat_id": chat_id, "text": msg, "parse_mode": "Markdown"}).encode()
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=5)
    except Exception as e:
        print(f"[WARN] Telegram: {e}")


# ---------------------------------------------------------------------------
# CLOB AUTH & ORDER (Simplified — see full integration notes)
# ---------------------------------------------------------------------------

def generate_api_key(private_key: str) -> Optional[Dict]:
    """
    Generate Polymarket CLOB API key from wallet.
    Returns {'apiKey': str, 'secret': str, 'passphrase': str}
    """
    try:
        from eth_account import Account
        from eth_account.messages import encode_defunct

        acct = Account.from_key(private_key)
        address = acct.address

        # Get nonce from CLOB
        nonce_url = f"{CLOB_HOST}/auth/api-key/nonce"
        req = urllib.request.Request(nonce_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            nonce_data = json.loads(resp.read())
        nonce = nonce_data.get("nonce", 0)

        # Sign nonce
        msg = f"Sign this message to access the Polymarket CLOB API: {nonce}"
        signed = acct.sign_message(encode_defunct(text=msg))
        signature = signed.signature.hex()

        # Create API key
        create_url = f"{CLOB_HOST}/auth/api-key"
        body = json.dumps({
            "address": address,
            "signature": signature,
            "nonce": nonce,
        }).encode()
        req = urllib.request.Request(
            create_url, data=body,
            headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"}
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except Exception as e:
        print(f"[ERROR] CLOB auth failed: {e}")
        return None


def get_market_token_ids() -> Tuple[Optional[str], Optional[str]]:
    """
    Fetch BTC Up/Down 5m market token IDs from Gamma API.
    Returns (yes_token_id, no_token_id).
    """
    try:
        url = f"{GAMMA_HOST}/events?active=true&closed=false&limit=20"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            events = json.loads(resp.read())

        for ev in events:
            title = ev.get("title", "").lower()
            if "btc" in title and ("up or down" in title or "updown" in title or "5m" in title):
                markets = ev.get("markets", [])
                for m in markets:
                    clob_raw = m.get("clobTokenIds", "[]")
                    try:
                        clob_ids = json.loads(clob_raw)
                        if len(clob_ids) >= 2:
                            return clob_ids[0], clob_ids[1]
                    except:
                        continue
        return None, None
    except Exception as e:
        print(f"[WARN] Market fetch failed: {e}")
        return None, None


def place_order(
    api_key: Dict,
    token_id: str,
    side: str,          # BUY or SELL
    size: float,        # Number of shares
    price: float,       # Price per share (0.01 - 0.99)
    mode: Mode,
) -> bool:
    """
    Place an order on Polymarket CLOB.
    In PAPER mode, logs only and returns True.
    """
    if mode == Mode.PAPER:
        print(f"\n{'='*50}")
        print(f"[PAPER TRADE] {side} {size} shares @ {price:.2f}")
        print(f"              Token: {token_id[:20]}...")
        print(f"{'='*50}\n")
        return True

    # LIVE mode — requires full CLOB integration
    # This is a simplified structure. Real implementation needs:
    # 1. EIP-712 order signing
    # 2. /order endpoint POST
    # 3. Proper order format matching CLOB spec

    print(f"\n{'='*50}")
    print(f"[LIVE ORDER] {side} {size} shares @ {price:.2f}")
    print(f"[WARNING] Full CLOB integration requires EIP-712 signing.")
    print(f"          See: https://docs.polymarket.com/")
    print(f"{'='*50}\n")

    # TODO: Implement full CLOB order signing + POST
    # For now, return False to prevent accidental execution
    return False


# ---------------------------------------------------------------------------
# STRATEGY
# ---------------------------------------------------------------------------

def estimate_entry_price(abs_spread: float) -> float:
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


def estimate_win_probability(abs_spread: float) -> float:
    """
    Estimate win probability for LOCK based on spread magnitude.
    Derived from backtest: larger spread = higher certainty.
    """
    if abs_spread < 100:
        return 0.50
    elif abs_spread < 130:
        return 0.95
    elif abs_spread < 160:
        return 0.97
    elif abs_spread < 200:
        return 0.98
    else:
        return 0.99


def calculate_ev(entry_price: float, win_prob: float) -> float:
    """Expected value per share after fees."""
    cost = entry_price
    fee = cost * FEE_RATE
    total_cost = cost + fee
    return (win_prob * 1.0) - total_cost


def get_position_size(bankroll: float, entry_price: float) -> float:
    """Risk 20-30% of bankroll per trade."""
    size = min(bankroll * 0.25, 5.0)
    max_cost = size * entry_price
    if max_cost > bankroll * 0.90:
        size = (bankroll * 0.90) / entry_price
    return round(size, 2)


# ---------------------------------------------------------------------------
# MAIN BOT
# ---------------------------------------------------------------------------

def get_window_number(now: datetime) -> int:
    return now.minute // 5


def get_window_start(now: datetime) -> datetime:
    wn = get_window_number(now)
    return now.replace(minute=wn * 5, second=0, microsecond=0)


def format_time(dt: datetime) -> str:
    return dt.strftime("%H:%M:%S")


class AutoTrader:
    def __init__(self, args):
        self.mode = Mode.LIVE if args.live else Mode.PAPER
        self.telegram_token = args.telegram_token or ""
        self.chat_id = args.chat_id or ""
        self.spread_threshold = args.spread
        self.bankroll = args.bankroll
        self.initial_bankroll = args.bankroll
        self.daily_stop = args.daily_stop
        self.max_trades_per_day = args.max_trades

        self.current_window: Optional[WindowState] = None
        self.daily_stats = DailyStats(
            date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            peak_bankroll=self.bankroll,
        )
        self.all_time_trades: List[WindowState] = []
        self.api_key: Optional[Dict] = None
        self.yes_token: Optional[str] = None
        self.no_token: Optional[str] = None

        # Initialize CLOB if live
        if self.mode == Mode.LIVE:
            if not PRIVATE_KEY:
                print("[FATAL] POLYMARKET_PRIVATE_KEY not set!")
                print("        Set it: export POLYMARKET_PRIVATE_KEY='0x...'")
                sys.exit(1)

            print("🔐 Authenticating with Polymarket CLOB...")
            self.api_key = generate_api_key(PRIVATE_KEY)
            if self.api_key:
                print("✅ CLOB auth success.")
            else:
                print("⚠️ CLOB auth failed. Running in FALLBACK mode (orders won't execute).")
                self.mode = Mode.PAPER

            print("🔍 Fetching market token IDs...")
            self.yes_token, self.no_token = get_market_token_ids()
            if self.yes_token:
                print(f"✅ Market loaded. YES token: {self.yes_token[:20]}...")
            else:
                print("⚠️ Market token IDs not found. Orders won't execute.")
                self.mode = Mode.PAPER

        self._print_banner()

    def _print_banner(self):
        banner = f"""
{'='*55}
  🚀 POLYMARKET BTC 5m LOCK AUTO TRADER
{'='*55}
  Mode:           {self.mode.value.upper()}
  Bankroll:       ${self.bankroll:.2f}
  Spread Target:  ${self.spread_threshold}
  Daily Stop:     ${self.daily_stop}
  Max Trades/Day: {self.max_trades_per_day}
  Telegram:       {'ENABLED' if self.telegram_token else 'OFF'}
{'='*55}
  ⚠️  {('PAPER MODE — no real money' if self.mode == Mode.PAPER else 'LIVE MODE — REAL MONEY')}
{'='*55}
"""
        self._notify(banner)

    def _notify(self, msg: str):
        print(msg)
        if self.telegram_token and self.chat_id:
            send_telegram(msg, self.telegram_token, self.chat_id)

    def _check_new_day(self):
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if today != self.daily_stats.date:
            # Save yesterday's stats
            print(f"\n📅 Day summary: {self.daily_stats.date}")
            print(f"   Trades: {self.daily_stats.trades}, Wins: {self.daily_stats.wins}, Losses: {self.daily_stats.losses}")
            print(f"   P/L: ${self.daily_stats.profit:+.2f}, Peak: ${self.daily_stats.peak_bankroll:.2f}")

            self.daily_stats = DailyStats(
                date=today,
                peak_bankroll=self.bankroll,
            )

    def _should_stop_trading(self) -> bool:
        """Check daily limits."""
        if self.daily_stats.trades >= self.max_trades_per_day:
            return True
        if self.daily_stats.profit <= -self.daily_stop:
            return True
        if self.bankroll < 2.0:
            return True
        return False

    def _check_signal_filters(self, direction: Direction, abs_spread: float, entry_price: float) -> Tuple[bool, str]:
        """Check volume, momentum, and EV filters. Returns (passed, reason)."""
        signal = fetch_btc_binance_signal()
        if not signal:
            print("[SIGNAL] No signal data, allowing trade (fallback)")
            return True, "fallback"
        price = signal.get("price", 0.0)
        vol_ratio = signal.get("volume_ratio", 1.0)
        momentum = signal.get("momentum_pct", 0.0)
        print(f"[SIGNAL] Price: ${price:,.2f} | Vol: {vol_ratio:.2f}x | Mom: {momentum:+.3f}%")
        # Volume filter
        if vol_ratio < MIN_VOLUME_RATIO:
            return False, f"LOW_VOLUME ({vol_ratio:.2f}x < {MIN_VOLUME_RATIO}x)"
        # Momentum confirmation
        if direction == Direction.DOWN and momentum > MOMENTUM_THRESHOLD:
            return False, f"MOMENTUM_UP (+{momentum:.3f}%)"
        if direction == Direction.UP and momentum < -MOMENTUM_THRESHOLD:
            return False, f"MOMENTUM_DOWN ({momentum:.3f}%)"
        # EV check
        win_prob = estimate_win_probability(abs_spread)
        ev = calculate_ev(entry_price, win_prob)
        print(f"[SIGNAL] Win prob: {win_prob:.0%} | EV: ${ev:.4f}/share")
        if ev < EV_THRESHOLD:
            return False, f"NEGATIVE_EV (${ev:.4f})"
        return True, "ok"

    def _execute_trade(self, direction: Direction, window: WindowState, entry_price: float, size: float):
        """Execute or paper-trade."""
        if self.mode == Mode.LIVE and not self.api_key:
            print("[ERROR] No API key. Skipping trade.")
            return False

        # Determine token and side
        if direction == Direction.UP:
            token = self.yes_token
            side = "BUY"
        else:
            token = self.no_token
            side = "BUY"

        cost = size * entry_price
        fee = cost * FEE_RATE
        total_cost = cost + fee

        if self.mode == Mode.LIVE and total_cost > self.bankroll:
            print(f"[WARN] Insufficient funds: need ${total_cost:.2f}, have ${self.bankroll:.2f}")
            return False

        success = place_order(
            self.api_key or {},
            token or "",
            side,
            size,
            entry_price,
            self.mode,
        )

        if success:
            window.traded = True
            window.direction = direction
            window.entry_price = entry_price
            window.size = size
            self.daily_stats.trades += 1
            # Deduct from bankroll (will be resolved later)
            self.bankroll -= total_cost
            print(f"[TRADE] Bankroll now: ${self.bankroll:.2f}")
            return True
        return False

    def _resolve_window(self, window: WindowState, final_price: float):
        """Resolve completed window."""
        window.final_spread = final_price - window.ptb

        if not window.traded:
            if not window.result:
                window.result = "SKIP"
            return

        # Determine WIN or LOSS based on direction vs final price
        # DOWN wins if final_price < ptb (negative spread)
        # UP wins if final_price > ptb (positive spread)
        won = False
        if window.direction == Direction.DOWN and window.final_spread < 0:
            won = True
        elif window.direction == Direction.UP and window.final_spread > 0:
            won = True

        cost = window.size * window.entry_price
        fee = cost * FEE_RATE
        total_cost = cost + fee

        if won:
            payout = window.size * 1.0  # Binary pays $1 per share
            profit = payout - total_cost
            self.daily_stats.wins += 1
            self.daily_stats.profit += profit
            self.bankroll += payout  # Bankroll already deducted by total_cost
            window.result = "WIN"

            msg = (
                f"✅ *WIN!* 🎉\n"
                f"Window: `{window.start.strftime('%H:%M:%S')}` | Direction: *{window.direction.value}*\n"
                f"Entry: `{window.entry_price:.2f}` | Size: `{window.size}`\n"
                f"Profit: `+${profit:.2f}` | Bankroll: `${self.bankroll:.2f}`"
            )
        else:
            loss = total_cost
            self.daily_stats.losses += 1
            self.daily_stats.profit -= loss
            window.result = "LOSS"

            msg = (
                f"❌ *LOSS* 😢\n"
                f"Window: `{window.start.strftime('%H:%M:%S')}` | Direction: *{window.direction.value}*\n"
                f"Entry: `{window.entry_price:.2f}` | Size: `{window.size}`\n"
                f"Lost: `-${loss:.2f}` | Bankroll: `${self.bankroll:.2f}`"
            )

        self.daily_stats.peak_bankroll = max(self.daily_stats.peak_bankroll, self.bankroll)
        print(f"\n{'='*50}")
        print(msg.replace('*', '').replace('`', ''))
        print(f"{'='*50}\n")
        self._notify(msg)

    def run(self):
        print("💡 Bot running. Press Ctrl+C to stop.\n")

        while True:
            now = datetime.now(timezone.utc)
            self._check_new_day()

            if self._should_stop_trading():
                if not hasattr(self, '_stopped_notified'):
                    self._notify(f"🚫 *STOPPED* \u2014 Daily limit hit.\nP/L: ${self.daily_stats.profit:+.2f}")
                    self._stopped_notified = True
                time.sleep(60)
                continue
            else:
                self._stopped_notified = False

            btc_price = get_btc_price()
            if btc_price == 0:
                print(f"[{format_time(now)}] Price fetch failed. Retrying...")
                time.sleep(CHECK_INTERVAL)
                continue

            window_start = get_window_start(now)
            seconds_into = (now - window_start).total_seconds()

            # New window
            if self.current_window is None or self.current_window.start != window_start:
                # Resolve previous
                if self.current_window:
                    self._resolve_window(self.current_window, btc_price)
                    self.all_time_trades.append(self.current_window)

                self.current_window = WindowState(
                    start=window_start,
                    ptb=btc_price,
                )
                print(f"\n🌅 New window: {format_time(window_start)} | PTB: ${btc_price:,.2f}")

            if not self.current_window:
                time.sleep(CHECK_INTERVAL)
                continue

            w = self.current_window
            spread = btc_price - w.ptb
            abs_spread = abs(spread)
            direction = Direction.UP if spread > 0 else Direction.DOWN

            # Log every minute
            if int(seconds_into) % 60 == 0:
                print(f"[{format_time(now)}] BTC: ${btc_price:,.2f} | Spread: {spread:+7.2f} | Min: {int(seconds_into//60)}")

            # LOCK alert condition
            if not w.alerted and ALERT_WINDOW_START <= seconds_into <= ALERT_WINDOW_END:
                if abs_spread >= self.spread_threshold:
                    entry_price = estimate_entry_price(abs_spread)
                    size = get_position_size(self.bankroll, entry_price)

                    # --- NEW: FastLoop-inspired filters ---
                    passed, reason = self._check_signal_filters(direction, abs_spread, entry_price)
                    if not passed:
                        skip_msg = f"⏸️ LOCK FILTERED: {reason} — skip window {format_time(w.start)}"
                        print(f"\n{skip_msg}\n")
                        self._notify(skip_msg)
                        w.alerted = True
                        w.result = f"SKIP_{reason.split()[0]}"
                    else:
                        # All filters passed — execute
                        msg = (
                            f"🚨 *LOCK SETUP* 🚨\n"
                            f"Window: `{format_time(w.start)}`\n"
                            f"PTB: `${w.ptb:,.2f}` | Current: `${btc_price:,.2f}`\n"
                            f"Spread: `{spread:+.2f}` USD\n"
                            f"Direction: *{direction.value}*\n"
                            f"Est. Entry: `{entry_price:.2f}` | Size: `${size:.2f}`\n"
                            f"Mode: *{self.mode.value.upper()}*"
                        )
                        self._notify(msg)
                        w.alerted = True

                        # Execute trade
                        if not w.traded and size > 0:
                            print(f"🚀 Executing {self.mode.value} trade: {direction.value} {size} @ {entry_price:.2f}")
                            self._execute_trade(direction, w, entry_price, size)

            time.sleep(CHECK_INTERVAL)


def main():
    parser = argparse.ArgumentParser(description="Polymarket BTC 5m LOCK Auto Trader")
    parser.add_argument("--live", action="store_true", help="LIVE trading mode (default: paper)")
    parser.add_argument("--telegram-token", default="", help="Telegram Bot Token")
    parser.add_argument("--chat-id", default="", help="Telegram Chat ID")
    parser.add_argument("--spread", type=int, default=100, help="Spread threshold USD")
    parser.add_argument("--bankroll", type=float, default=10.0, help="Starting bankroll USD")
    parser.add_argument("--daily-stop", type=float, default=5.0, help="Daily stop loss USD")
    parser.add_argument("--max-trades", type=int, default=20, help="Max trades per day")
    args = parser.parse_args()

    if args.live and not PRIVATE_KEY:
        print("")
        print("❌ FATAL: --live requires POLYMARKET_PRIVATE_KEY environment variable.")
        print("   Set it: export POLYMARKET_PRIVATE_KEY='0xYOUR_PRIVATE_KEY'")
        print("   Or run in paper mode: python3 poly_btc_5m_autotrader.py")
        print("")
        sys.exit(1)

    try:
        bot = AutoTrader(args)
        bot.run()
    except KeyboardInterrupt:
        print("\n\n👋 Bot stopped by user.")
        sys.exit(0)


if __name__ == "__main__":
    main()
