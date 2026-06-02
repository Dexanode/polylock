#!/usr/bin/env python3
"""
Polymarket BTC 5m LOCK Strategy — $50 Threshold Bot

Usage:
  python3 poly_btc_5m_lock_50.py [--live] [--telegram-token TOKEN --chat-id ID]
  python3 poly_btc_5m_lock_50.py --bankroll 10 --spread 50 --daily-stop 5
"""

import argparse
import json
import os
import sys
import time
import urllib.request
from collections import deque
from datetime import datetime, timezone
from typing import Optional, Dict, List, Tuple
from dataclasses import dataclass, field
from enum import Enum

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------

CHECK_INTERVAL       = 5          # seconds between price fetches
SPREAD_THRESHOLD     = 50         # USD minimum spread to consider a trade
ALERT_WINDOW_START   = 4 * 60     # 240s — start of LOCK zone
ALERT_WINDOW_END     = 4 * 60 + 55 # 295s — end of LOCK zone
FEE_RATE             = 0.02       # Polymarket taker fee

# Signal filters
MIN_VOLUME_RATIO     = 0.25       # skip if volume < 0.25x 10-candle avg (diturunkan dari 0.5x — spike inflasi avg)
MOMENTUM_THRESHOLD   = 0.12       # max opposing 3-candle slope % allowed
EV_THRESHOLD         = 0.0        # min expected value per share
MAX_FILTER_ATTEMPTS  = 3          # max re-checks per window before giving up

# Slippage buffer added to estimated entry (realistic for live)
SLIPPAGE_BUFFER      = 0.02

# Price history: keep last N samples for boundary-accurate resolution
PRICE_HISTORY_SIZE   = 60         # 60 × 5s = 5 minutes coverage

# URLs
BINANCE_URL  = "https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT"
KLINES_URL   = "https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1m&limit=20"
YAHOO_URL    = "https://query1.finance.yahoo.com/v8/finance/chart/BTC-USD?interval=1m&range=1d"

# Chainlink BTC/USD on Polygon — same feed Polymarket uses to resolve markets
CHAINLINK_CONTRACT = "0xc907E116054Ad103354f2D350FD2514433D57F6f"
CHAINLINK_SELECTOR = "0x50d25bcd"   # latestAnswer()
POLYGON_RPCS = [
    "https://1rpc.io/matic",
    "https://polygon.drpc.org",
    "https://rpc-mainnet.matic.quiknode.pro",
]

# Binance 5m kline — untuk ambil open price window yang akurat
KLINES_5M_URL = "https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=5m&limit=2"

# Persistent log file — dashboard reads ini, survive restart
LOG_DIR      = os.path.join(os.path.dirname(__file__), "..", "logs")
WINDOWS_LOG  = os.path.join(LOG_DIR, "windows.jsonl")
STATS_LOG    = os.path.join(LOG_DIR, "stats.json")

# ---------------------------------------------------------------------------
# PERSISTENT LOGGING — dashboard reads from these files
# ---------------------------------------------------------------------------

def _ensure_log_dir():
    os.makedirs(LOG_DIR, exist_ok=True)

def log_window(window) -> None:
    """Append/update window result ke windows.jsonl (1 JSON object per line)."""
    _ensure_log_dir()
    record = {
        "start":       window.start.isoformat(),
        "ptb":         window.ptb,
        "direction":   window.direction.value,
        "traded":      window.traded,
        "entry_price": window.entry_price,
        "size":        window.size,
        "final_spread": window.final_spread,
        "result":      window.result,
        "ts":          datetime.now(timezone.utc).isoformat(),
    }
    with open(WINDOWS_LOG, "a") as f:
        f.write(json.dumps(record) + "\n")

def log_stats(stats, bankroll: float) -> None:
    """Overwrite stats.json dengan daily stats terkini."""
    _ensure_log_dir()
    record = {
        "date":     stats.date,
        "trades":   stats.trades,
        "wins":     stats.wins,
        "losses":   stats.losses,
        "profit":   round(stats.profit, 4),
        "bankroll": round(bankroll, 4),
        "peak_bankroll": round(stats.peak_bankroll, 4),
        "ts":       datetime.now(timezone.utc).isoformat(),
    }
    with open(STATS_LOG, "w") as f:
        json.dump(record, f)

# ---------------------------------------------------------------------------
# ENUMS & DATACLASSES
# ---------------------------------------------------------------------------

class Mode(Enum):
    PAPER = "paper"
    LIVE  = "live"

class Direction(Enum):
    UP   = "UP"
    DOWN = "DOWN"
    NONE = "NONE"

@dataclass
class WindowState:
    start:           datetime
    ptb:             float
    alerted:         bool      = False   # True only after successful trade
    traded:          bool      = False
    direction:       Direction = Direction.NONE
    entry_price:     float     = 0.0
    size:            float     = 0.0
    final_spread:    float     = 0.0
    result:          str       = ""
    filter_attempts: int       = 0       # how many times filters were checked

@dataclass
class DailyStats:
    date:           str
    trades:         int   = 0
    wins:           int   = 0
    losses:         int   = 0
    profit:         float = 0.0
    max_drawdown:   float = 0.0
    peak_bankroll:  float = 0.0

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


def fetch_window_open_price() -> Tuple[float, datetime]:
    """
    Ambil open price dari 5m candle yang sedang berjalan di Binance.
    Ini adalah PTB yang benar — harga tepat saat window buka.
    Dipakai saat bot start/restart di tengah window supaya PTB tidak meleset.
    Returns (open_price, candle_open_time) atau (0.0, None) jika gagal.
    """
    try:
        req = urllib.request.Request(KLINES_5M_URL, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read())
        if not data:
            return 0.0, None
        # data[-1] = candle 5m yang sedang berjalan
        # data[-1][0] = open time (ms), data[-1][1] = open price
        candle = data[-1]
        open_time  = datetime.fromtimestamp(candle[0] / 1000, tz=timezone.utc)
        open_price = float(candle[1])
        return open_price, open_time
    except Exception as e:
        print(f"[WARN] 5m candle open: {e}")
        return 0.0, None


def fetch_btc_chainlink() -> float:
    """
    Baca BTC/USD langsung dari Chainlink aggregator di Polygon via raw JSON-RPC.
    Ini feed yang sama yang Polymarket pakai untuk resolve market — paling akurat.
    Tidak butuh library tambahan, cukup urllib.
    """
    payload = json.dumps({
        "jsonrpc": "2.0", "method": "eth_call",
        "params": [{"to": CHAINLINK_CONTRACT, "data": CHAINLINK_SELECTOR}, "latest"],
        "id": 1,
    }).encode()
    for rpc in POLYGON_RPCS:
        try:
            req = urllib.request.Request(
                rpc, data=payload,
                headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"},
            )
            with urllib.request.urlopen(req, timeout=8) as resp:
                result = json.loads(resp.read())
            hex_val = result.get("result", "0x0")
            if not hex_val or hex_val == "0x":
                continue
            price = int(hex_val, 16) / 1e8   # Chainlink 8 decimal places
            if price > 1000:                  # sanity check
                return price
        except Exception as e:
            print(f"[WARN] Chainlink ({rpc}): {e}")
    return 0.0


def get_btc_price() -> float:
    """Chainlink (Polymarket feed) → Binance → Yahoo sebagai fallback."""
    p = fetch_btc_chainlink()
    if p > 0:
        return p
    print("[WARN] Chainlink failed, fallback to Binance")
    p = fetch_btc_binance()
    return p if p > 0 else fetch_btc_yahoo()


# FIX 3: momentum pakai 3-candle slope, bukan 1-candle diff
def fetch_btc_binance_signal() -> Dict:
    """
    Fetch BTC klines dan hitung:
    - volume_ratio  : avg 3 candle COMPLETE terbaru vs 10-candle avg
                      FIX: pakai volumes[-4:-1] bukan volumes[-1]
                      karena candle terakhir masih incomplete (selalu rendah)
    - momentum_pct  : slope 3-candle terbaru vs 3-candle sebelumnya
    """
    try:
        req = urllib.request.Request(KLINES_URL, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        if not data or len(data) < 8:
            return {}

        closes  = [float(c[4]) for c in data]
        volumes = [float(c[5]) for c in data]

        # FIX: gunakan 3 candle complete terakhir (bukan candle[-1] yang masih jalan)
        # volumes[-1] = candle sekarang (incomplete, bisa 0.04x karena baru mulai)
        # volumes[-4:-1] = 3 candle 1m yang sudah tutup sempurna
        recent_complete_vols = volumes[-4:-1]
        current_vol  = sum(recent_complete_vols) / len(recent_complete_vols)
        avg_vol      = sum(volumes[-11:-1]) / 10 if len(volumes) >= 11 else sum(volumes[:-1]) / max(1, len(volumes) - 1)
        volume_ratio = current_vol / avg_vol if avg_vol > 0 else 1.0

        # 3-candle slope pada candle complete (exclude candle sekarang)
        complete_closes = closes[:-1]  # buang candle terakhir yang masih jalan
        avg_recent = sum(complete_closes[-3:])  / 3
        avg_before = sum(complete_closes[-6:-3]) / 3
        momentum_pct = ((avg_recent - avg_before) / avg_before) * 100 if avg_before > 0 else 0.0

        return {
            "price":          closes[-1],
            "momentum_pct":   momentum_pct,
            "volume_ratio":   volume_ratio,
            "current_volume": current_vol,
            "avg_volume":     avg_vol,
        }
    except Exception as e:
        print(f"[WARN] Binance signal: {e}")
        return {}


# ---------------------------------------------------------------------------
# TELEGRAM
# ---------------------------------------------------------------------------

def send_telegram(msg: str, token: str, chat_id: str):
    try:
        url     = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = json.dumps({"chat_id": chat_id, "text": msg, "parse_mode": "Markdown"}).encode()
        req     = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=5)
    except Exception as e:
        print(f"[WARN] Telegram: {e}")


# ---------------------------------------------------------------------------
# STRATEGY
# ---------------------------------------------------------------------------

def estimate_entry_price(abs_spread: float, mode: Mode = Mode.PAPER) -> float:
    """
    Estimasi harga entry dari tabel spread.
    FIX 2: tambah SLIPPAGE_BUFFER di live mode — harga real selalu
    lebih mahal dari estimasi karena orderbook tipis saat spread besar.
    """
    if   abs_spread < 50:  base = 0.50
    elif abs_spread < 70:  base = 0.65
    elif abs_spread < 90:  base = 0.70
    elif abs_spread < 110: base = 0.75
    elif abs_spread < 130: base = 0.78
    elif abs_spread < 160: base = 0.83
    elif abs_spread < 200: base = 0.87
    else:                  base = 0.91

    # Di live mode tambah slippage — jangan optimis
    if mode == Mode.LIVE:
        base = min(0.95, base + SLIPPAGE_BUFFER)
    return base


def estimate_win_probability(abs_spread: float) -> float:
    if   abs_spread < 50:  return 0.50
    elif abs_spread < 70:  return 0.72
    elif abs_spread < 90:  return 0.78
    elif abs_spread < 110: return 0.82
    elif abs_spread < 130: return 0.86
    elif abs_spread < 160: return 0.90
    elif abs_spread < 200: return 0.93
    else:                  return 0.95


def calculate_ev(entry_price: float, win_prob: float) -> float:
    return (win_prob * 1.0) - (entry_price * (1 + FEE_RATE))


def get_position_size(bankroll: float, entry_price: float) -> float:
    size     = min(bankroll * 0.20, 3.0)
    max_cost = size * entry_price
    if max_cost > bankroll * 0.85:
        size = (bankroll * 0.85) / entry_price
    return round(size, 2)


# ---------------------------------------------------------------------------
# WINDOW HELPERS
# ---------------------------------------------------------------------------

def get_window_start(now: datetime) -> datetime:
    wn = now.minute // 5
    return now.replace(minute=wn * 5, second=0, microsecond=0)


def format_time(dt: datetime) -> str:
    return dt.strftime("%H:%M:%S")


# ---------------------------------------------------------------------------
# AUTO TRADER
# ---------------------------------------------------------------------------

class AutoTrader:
    def __init__(self, args):
        self.mode               = Mode.LIVE if getattr(args, 'live', False) else Mode.PAPER
        self.telegram_token     = args.telegram_token or ""
        self.chat_id            = args.chat_id or ""
        self.spread_threshold   = args.spread
        self.bankroll           = args.bankroll
        self.initial_bankroll   = args.bankroll
        self.daily_stop         = args.daily_stop
        self.max_trades_per_day = args.max_trades

        self.current_window: Optional[WindowState] = None
        self.daily_stats = DailyStats(
            date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            peak_bankroll=self.bankroll,
        )
        self.all_time_trades: List[WindowState] = []

        # FIX 1: price history untuk resolve akurat di boundary window
        # deque of (datetime, float) — rolling 5 menit
        self.price_history: deque = deque(maxlen=PRICE_HISTORY_SIZE)

        self._print_banner()

    # ── BANNER ─────────────────────────────────────────────────────────────

    def _print_banner(self):
        self._notify(f"""
{'='*55}
  🔐 POLYMARKET BTC 5m LOCK BOT — $50 THRESHOLD
{'='*55}
  Mode:           {self.mode.value.upper()}
  Bankroll:       ${self.bankroll:.2f}
  Spread Target:  ${self.spread_threshold}
  Daily Stop:     ${self.daily_stop}
  Max Trades/Day: {self.max_trades_per_day}
  Telegram:       {'ENABLED' if self.telegram_token else 'OFF'}
{'='*55}
  Fixes: boundary-resolve · slippage · 3c-momentum · re-entry
{'='*55}
""")

    # ── NOTIFY ─────────────────────────────────────────────────────────────

    def _notify(self, msg: str):
        print(msg)
        if self.telegram_token and self.chat_id:
            send_telegram(msg, self.telegram_token, self.chat_id)

    # ── DAILY RESET ────────────────────────────────────────────────────────

    def _check_new_day(self):
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if today != self.daily_stats.date:
            summary = (
                f"📅 Day summary: {self.daily_stats.date}\n"
                f"   Trades: {self.daily_stats.trades} | W/L: {self.daily_stats.wins}/{self.daily_stats.losses}\n"
                f"   P/L: ${self.daily_stats.profit:+.2f} | Peak: ${self.daily_stats.peak_bankroll:.2f}"
            )
            self._notify(summary)
            self.daily_stats = DailyStats(date=today, peak_bankroll=self.bankroll)

    # ── RISK CONTROLS ──────────────────────────────────────────────────────

    def _should_stop_trading(self) -> bool:
        if self.daily_stats.profit <= -self.daily_stop:
            return True
        if self.bankroll < 2.0:
            return True
        return False

    # ── FIX 1: BOUNDARY RESOLVE ────────────────────────────────────────────

    def _get_boundary_price(self, window_end: datetime) -> Optional[float]:
        """
        Cari harga terdekat dengan waktu window_end dari history.
        Ini menghindari error resolve karena harga fetch yang telat 5 detik.
        """
        if not self.price_history:
            return None
        best_price = None
        best_delta = float("inf")
        for ts, price in self.price_history:
            delta = abs((ts - window_end).total_seconds())
            if delta < best_delta:
                best_delta = delta
                best_price = price
        return best_price

    # ── FIX 3: SIGNAL FILTERS (3-CANDLE MOMENTUM) ──────────────────────────

    def _check_signal_filters(self, direction: Direction, abs_spread: float, entry_price: float) -> Tuple[bool, str]:
        signal = fetch_btc_binance_signal()
        if not signal:
            print("[SIGNAL] No data — allowing trade (fallback)")
            return True, "fallback"

        price     = signal.get("price", 0.0)
        vol_ratio = signal.get("volume_ratio", 1.0)
        momentum  = signal.get("momentum_pct", 0.0)  # now 3-candle slope

        print(f"[SIGNAL] ${price:,.2f} | Vol: {vol_ratio:.2f}x | Mom(3c): {momentum:+.4f}%")

        if vol_ratio < MIN_VOLUME_RATIO:
            return False, f"LOW_VOLUME ({vol_ratio:.2f}x)"

        if direction == Direction.DOWN and momentum > MOMENTUM_THRESHOLD:
            return False, f"COUNTER_MOMENTUM_UP (+{momentum:.4f}%)"

        if direction == Direction.UP and momentum < -MOMENTUM_THRESHOLD:
            return False, f"COUNTER_MOMENTUM_DOWN ({momentum:.4f}%)"

        win_prob = estimate_win_probability(abs_spread)
        ev       = calculate_ev(entry_price, win_prob)
        print(f"[SIGNAL] WinProb: {win_prob:.0%} | EV: ${ev:.4f}/share")

        if ev < EV_THRESHOLD:
            return False, f"NEGATIVE_EV (${ev:.4f})"

        return True, "ok"

    # ── EXECUTE ────────────────────────────────────────────────────────────

    def _execute_trade(self, direction: Direction, window: WindowState, entry_price: float, size: float) -> bool:
        cost       = size * entry_price
        fee        = cost * FEE_RATE
        total_cost = cost + fee

        if total_cost > self.bankroll:
            print(f"[WARN] Insufficient funds: need ${total_cost:.2f}, have ${self.bankroll:.2f}")
            return False

        print(f"\n{'='*50}")
        print(f"[{'LIVE' if self.mode == Mode.LIVE else 'PAPER'} TRADE] BUY {size} shares @ {entry_price:.2f}")
        print(f"  Direction: {direction.value} | Cost: ${total_cost:.2f} | Fee: ${fee:.2f}")
        print(f"{'='*50}\n")

        window.traded      = True
        window.direction   = direction
        window.entry_price = entry_price
        window.size        = size
        self.daily_stats.trades += 1
        self.bankroll -= total_cost
        print(f"[TRADE] Bankroll: ${self.bankroll:.2f}")
        # Log window sebagai PENDING dulu, akan di-update saat resolve
        window.result = "PENDING"
        log_window(window)
        log_stats(self.daily_stats, self.bankroll)
        return True

    # ── FIX 1: RESOLVE WITH BOUNDARY PRICE ─────────────────────────────────

    def _resolve_window(self, window: WindowState, fallback_price: float):
        # Cari harga closest ke detik :00 dari window berikutnya
        window_end   = window.start.replace(
            minute=(window.start.minute // 5) * 5,
            second=0, microsecond=0
        )
        # window_end = start + 5 menit
        from datetime import timedelta
        window_end = window.start + timedelta(minutes=5)
        boundary_price = self._get_boundary_price(window_end) or fallback_price

        window.final_spread = boundary_price - window.ptb

        if not window.traded:
            if not window.result:
                window.result = "SKIP"
            log_window(window)
            return

        won = (
            (window.direction == Direction.DOWN and window.final_spread < 0) or
            (window.direction == Direction.UP   and window.final_spread > 0)
        )

        cost       = window.size * window.entry_price
        fee        = cost * FEE_RATE
        total_cost = cost + fee

        if won:
            payout = window.size * 1.0
            profit = payout - total_cost
            self.daily_stats.wins   += 1
            self.daily_stats.profit += profit
            self.bankroll           += payout
            window.result = "WIN"
            msg = (
                f"✅ *LOCK WIN* 🎉\n"
                f"Window: `{window.start.strftime('%H:%M:%S')}` | Dir: *{window.direction.value}*\n"
                f"PTB: `${window.ptb:,.2f}` → Final: `${boundary_price:,.2f}`\n"
                f"Entry: `{window.entry_price:.2f}` | Size: `{window.size}` shares\n"
                f"Profit: `+${profit:.2f}` | Bankroll: `${self.bankroll:.2f}`"
            )
        else:
            loss = total_cost
            self.daily_stats.losses += 1
            self.daily_stats.profit -= loss
            window.result = "LOSS"
            msg = (
                f"❌ *LOCK LOSS*\n"
                f"Window: `{window.start.strftime('%H:%M:%S')}` | Dir: *{window.direction.value}*\n"
                f"PTB: `${window.ptb:,.2f}` → Final: `${boundary_price:,.2f}`\n"
                f"Entry: `{window.entry_price:.2f}` | Size: `{window.size}` shares\n"
                f"Lost: `-${loss:.2f}` | Bankroll: `${self.bankroll:.2f}`"
            )

        self.daily_stats.peak_bankroll = max(self.daily_stats.peak_bankroll, self.bankroll)
        print(f"\n{'='*50}")
        print(msg.replace('*', '').replace('`', ''))
        print(f"{'='*50}\n")
        self._notify(msg)
        # Persist hasil final ke file — overwrite baris PENDING
        log_window(window)
        log_stats(self.daily_stats, self.bankroll)

    # ── MAIN LOOP ──────────────────────────────────────────────────────────

    def run(self):
        print("💡 PolyLock Bot running. Press Ctrl+C to stop.\n")

        while True:
            now = datetime.now(timezone.utc)
            self._check_new_day()

            if self._should_stop_trading():
                if not getattr(self, '_stopped_notified', False):
                    self._notify(
                        f"🚫 *STOPPED* — Daily limit hit.\n"
                        f"Trades: {self.daily_stats.trades} | P/L: ${self.daily_stats.profit:+.2f}"
                    )
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

            # FIX 1: simpan setiap sample ke history
            self.price_history.append((now, btc_price))

            window_start  = get_window_start(now)
            seconds_into  = (now - window_start).total_seconds()

            # ── New window ─────────────────────────────────────────────────
            if self.current_window is None or self.current_window.start != window_start:
                if self.current_window:
                    self._resolve_window(self.current_window, btc_price)
                    self.all_time_trades.append(self.current_window)

                # FIX PTB: kalau bot start/restart di tengah window (seconds_into > 5),
                # ambil open price 5m candle dari Binance agar PTB = harga saat :00
                # bukan harga saat bot nyala (yang bisa beda $50+)
                ptb = btc_price
                if seconds_into > 5:
                    open_price, open_time = fetch_window_open_price()
                    if open_price > 0 and open_time and abs((open_time - window_start).total_seconds()) < 30:
                        ptb = open_price
                        print(f"\n🌅 New window: {format_time(window_start)} | PTB: ${ptb:,.2f} (from 5m open, bot joined +{int(seconds_into)}s late)")
                    else:
                        print(f"\n🌅 New window: {format_time(window_start)} | PTB: ${ptb:,.2f} (current price, 5m open unavailable)")
                else:
                    print(f"\n🌅 New window: {format_time(window_start)} | PTB: ${ptb:,.2f}")

                self.current_window = WindowState(start=window_start, ptb=ptb)

            w          = self.current_window
            spread     = btc_price - w.ptb
            abs_spread = abs(spread)
            direction  = Direction.UP if spread > 0 else Direction.DOWN
            # FIX: selalu update direction di window state — tampil di log meski SKIP
            w.direction = direction

            # Log every minute
            if int(seconds_into) % 60 == 0:
                print(f"[{format_time(now)}] BTC: ${btc_price:,.2f} | Spread: {spread:+7.2f} | Min: {int(seconds_into//60)}")

            # ── LOCK ZONE ──────────────────────────────────────────────────
            in_lock_zone = ALERT_WINDOW_START <= seconds_into <= ALERT_WINDOW_END

            # FIX 4: re-entry — jangan blokir dengan alerted kecuali sudah trade
            # Cukup cek: belum traded, masih di lock zone, filter_attempts < MAX
            can_trade = (
                in_lock_zone
                and not w.traded
                and not w.alerted              # alerted = True hanya setelah trade berhasil
                and w.filter_attempts < MAX_FILTER_ATTEMPTS
            )

            if can_trade and abs_spread >= self.spread_threshold:
                entry_price = estimate_entry_price(abs_spread, self.mode)
                size        = get_position_size(self.bankroll, entry_price)

                passed, reason = self._check_signal_filters(direction, abs_spread, entry_price)

                if not passed:
                    w.filter_attempts += 1
                    attempts_left = MAX_FILTER_ATTEMPTS - w.filter_attempts
                    skip_msg = (
                        f"⏸️ FILTERED ({w.filter_attempts}/{MAX_FILTER_ATTEMPTS}): {reason} "
                        f"| Spread: {spread:+.0f} | Rechecking in {CHECK_INTERVAL}s "
                        f"({'done' if attempts_left == 0 else f'{attempts_left} left'})"
                    )
                    print(f"\n{skip_msg}\n")
                    if w.filter_attempts >= MAX_FILTER_ATTEMPTS:
                        w.result = f"SKIP_{reason.split('(')[0].strip()}"
                        self._notify(f"⏸️ *LOCK SKIPPED* — {reason}\nWindow: `{format_time(w.start)}`")
                else:
                    # All filters passed
                    ev = calculate_ev(entry_price, estimate_win_probability(abs_spread))
                    alert_msg = (
                        f"🚨 *LOCK SETUP* 🚨\n"
                        f"Window: `{format_time(w.start)}`\n"
                        f"PTB: `${w.ptb:,.2f}` | BTC: `${btc_price:,.2f}`\n"
                        f"Spread: `{spread:+.2f}` | Dir: *{direction.value}*\n"
                        f"Entry: `{entry_price:.2f}` | Size: `{size}` | EV: `${ev:.4f}`\n"
                        f"Mode: *{self.mode.value.upper()}*"
                    )
                    self._notify(alert_msg)

                    if size > 0 and self._execute_trade(direction, w, entry_price, size):
                        w.alerted = True  # FIX 4: set alerted hanya setelah trade berhasil

            time.sleep(CHECK_INTERVAL)


# ---------------------------------------------------------------------------
# ENTRY POINT
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Polymarket BTC 5m LOCK Bot")
    parser.add_argument("--live",          action="store_true", help="LIVE mode (default: paper)")
    parser.add_argument("--telegram-token", default="",         help="Telegram Bot Token")
    parser.add_argument("--chat-id",        default="",         help="Telegram Chat ID")
    parser.add_argument("--spread",         type=int,   default=50,   help="Spread threshold USD")
    parser.add_argument("--bankroll",       type=float, default=10.0, help="Starting bankroll USD")
    parser.add_argument("--daily-stop",     type=float, default=5.0,  help="Daily stop loss USD")
    parser.add_argument("--max-trades",     type=int,   default=20,   help="Max trades per day")
    args = parser.parse_args()

    if args.live and not os.environ.get("POLYMARKET_PRIVATE_KEY"):
        print("❌ --live requires POLYMARKET_PRIVATE_KEY env var.")
        sys.exit(1)

    try:
        AutoTrader(args).run()
    except KeyboardInterrupt:
        print("\n\n👋 Bot stopped.")
        sys.exit(0)


if __name__ == "__main__":
    main()
