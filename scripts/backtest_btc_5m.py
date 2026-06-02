#!/usr/bin/env python3
"""
BTC Up/Down 5m Strategy Backtester
Simulates the decision-tree strategy against historical BTC price data.

Usage:
    python3 backtest_btc_5m.py --days 30 --strategy trend
    python3 backtest_btc_5m.py --days 7 --strategy lock
    python3 backtest_btc_5m.py --days 14 --strategy all --csv journal.csv

Strategies:
    trend   = HTF Trend Follower (beli sesuai M15 bias, entry menit 2-3)
    lock    = Last-Minute Lock (entry menit 4+, arah locked)
    middle  = The Ignored Middle (skip flat, trade momentum)
    all     = Run all strategies and compare
"""

import argparse
import json
import urllib.request
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import List, Optional, Dict
from enum import Enum


class StrategyType(Enum):
    TREND = "trend"
    LOCK = "lock"
    MIDDLE = "middle"
    ALL = "all"


class Direction(Enum):
    UP = "UP"
    DOWN = "DOWN"
    NEUTRAL = "NEUTRAL"


class TradeResult(Enum):
    WIN = "WIN"
    LOSS = "LOSS"
    SKIP = "SKIP"


@dataclass
class Trade:
    window_start: datetime
    strategy: str
    htf_bias: Direction
    spread_at_min3: float          # USD difference from price to beat
    entry_direction: Optional[Direction]
    entry_price: float             # e.g. 0.62 means bought at 62 cents
    size: float
    outcome: TradeResult
    gross_pl: float
    net_pl: float
    reason: str = ""


@dataclass
class BacktestResult:
    strategy: str
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    skips: int = 0
    gross_profit: float = 0.0
    gross_loss: float = 0.0
    net_pl: float = 0.0
    trades: List[Trade] = field(default_factory=list)

    @property
    def winrate(self) -> float:
        resolved = self.wins + self.losses
        return (self.wins / resolved * 100) if resolved > 0 else 0.0

    @property
    def avg_win(self) -> float:
        wins = [t.net_pl for t in self.trades if t.outcome == TradeResult.WIN]
        return sum(wins) / len(wins) if wins else 0.0

    @property
    def avg_loss(self) -> float:
        losses = [t.net_pl for t in self.trades if t.outcome == TradeResult.LOSS]
        return sum(losses) / len(losses) if losses else 0.0


def fetch_btc_ohlc(days: int = 30, interval: str = "1m") -> List[dict]:
    """Fetch BTC-USD price data from Yahoo Finance."""
    # Yahoo symbol for BTC-USD
    symbol = "BTC-USD"
    range_param = f"{days}d" if days <= 60 else "1y"

    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
        f"?interval={interval}&range={range_param}"
    )

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())

        result = data["chart"]["result"][0]
        timestamps = result["timestamp"]
        quote = result["indicators"]["quote"][0]
        opens = quote["open"]
        highs = quote["high"]
        lows = quote["low"]
        closes = quote["close"]
        volumes = quote.get("volume", [0] * len(timestamps))

        candles = []
        for i in range(len(closes)):
            if closes[i] is None:
                continue
            candles.append({
                "time": datetime.fromtimestamp(timestamps[i], tz=timezone.utc),
                "open": float(opens[i]),
                "high": float(highs[i]),
                "low": float(lows[i]),
                "close": float(closes[i]),
                "volume": int(volumes[i]) if volumes[i] else 0,
            })
        return candles
    except Exception as e:
        print(f"[ERROR] Failed to fetch BTC data: {e}")
        return []


def calculate_ema(data: List[float], period: int) -> Optional[float]:
    if len(data) < period:
        return None
    multiplier = 2 / (period + 1)
    ema = sum(data[:period]) / period
    for price in data[period:]:
        ema = (price - ema) * multiplier + ema
    return ema


def calculate_vwap(candles: List[dict]) -> float:
    """Simple VWAP for a list of candles."""
    total_pv = sum(c["close"] * c["volume"] for c in candles)
    total_v = sum(c["volume"] for c in candles)
    return total_pv / total_v if total_v > 0 else candles[-1]["close"]


def get_htf_bias(candles_before: List[dict]) -> Direction:
    """
    Determine HTF bias using M15-like logic.
    We use the last 15 candles (1m each) as proxy for M15.
    """
    if len(candles_before) < 15:
        return Direction.NEUTRAL

    recent = candles_before[-15:]
    closes = [c["close"] for c in recent]
    ema9 = calculate_ema(closes, 9)
    ema21 = calculate_ema(closes, 21)
    vwap = calculate_vwap(recent)
    current_price = recent[-1]["close"]

    if ema9 is None or ema21 is None:
        return Direction.NEUTRAL

    bullish = (current_price > vwap) and (ema9 > ema21)
    bearish = (current_price < vwap) and (ema9 < ema21)

    if bullish:
        return Direction.UP
    elif bearish:
        return Direction.DOWN
    return Direction.NEUTRAL


def simulate_5m_window(
    candles_5m: List[dict],
    candles_before: List[dict],
    strategy: StrategyType,
    fee_rate: float = 0.02,
) -> Optional[Trade]:
    """
    Simulate ONE 5-minute window.
    candles_5m: exactly 5 candles (minute 0-4)
    candles_before: candles before this window for HTF bias
    """
    if len(candles_5m) < 5:
        return None

    price_to_beat = candles_5m[0]["open"]  # Window opens at this price
    htf_bias = get_htf_bias(candles_before)

    # ---- PHASE 1: Minute 0-2 (noise, no entry) ----
    # We analyze but don't trade

    # ---- PHASE 2: Minute 3 assessment ----
    # Use candle index 3 (0-indexed, so 4th minute)
    minute3_price = candles_5m[3]["close"]
    spread_at_min3 = minute3_price - price_to_beat

    # Determine direction at minute 3
    if spread_at_min3 > 40:
        dir_min3 = Direction.UP
    elif spread_at_min3 < -40:
        dir_min3 = Direction.DOWN
    else:
        dir_min3 = Direction.NEUTRAL

    # ---- PHASE 3: Apply strategy rules ----
    entry_direction = None
    entry_price = 0.0
    size = 10.0
    reason = ""

    if strategy == StrategyType.TREND:
        # HTF Trend Follower
        # Entry minute 2-3, price 0.55-0.65, must align with HTF bias
        if htf_bias == Direction.NEUTRAL:
            return Trade(
                window_start=candles_5m[0]["time"],
                strategy="trend",
                htf_bias=htf_bias,
                spread_at_min3=spread_at_min3,
                entry_direction=None,
                entry_price=0.0,
                size=0.0,
                outcome=TradeResult.SKIP,
                gross_pl=0.0,
                net_pl=0.0,
                reason="HTF neutral",
            )

        # Check momentum from minute 1 to 3
        price_min1 = candles_5m[1]["close"]
        momentum = abs(minute3_price - price_min1)

        if momentum < 30:
            return Trade(
                window_start=candles_5m[0]["time"],
                strategy="trend",
                htf_bias=htf_bias,
                spread_at_min3=spread_at_min3,
                entry_direction=None,
                entry_price=0.0,
                size=0.0,
                outcome=TradeResult.SKIP,
                gross_pl=0.0,
                net_pl=0.0,
                reason="Momentum too low",
            )

        if dir_min3 == Direction.UP and htf_bias == Direction.UP:
            entry_direction = Direction.UP
            entry_price = 0.62
            reason = "HTF UP + momentum UP"
        elif dir_min3 == Direction.DOWN and htf_bias == Direction.DOWN:
            entry_direction = Direction.DOWN
            entry_price = 0.62
            reason = "HTF DOWN + momentum DOWN"
        else:
            return Trade(
                window_start=candles_5m[0]["time"],
                strategy="trend",
                htf_bias=htf_bias,
                spread_at_min3=spread_at_min3,
                entry_direction=None,
                entry_price=0.0,
                size=0.0,
                outcome=TradeResult.SKIP,
                gross_pl=0.0,
                net_pl=0.0,
                reason="Direction mismatch HTF",
            )

    elif strategy == StrategyType.LOCK:
        # Last-Minute Lock
        # Entry minute 4+, price 0.85+, spread > $120
        minute4_price = candles_5m[4]["close"]
        spread_at_min4 = minute4_price - price_to_beat

        if abs(spread_at_min4) < 120:
            return Trade(
                window_start=candles_5m[0]["time"],
                strategy="lock",
                htf_bias=htf_bias,
                spread_at_min3=spread_at_min3,
                entry_direction=None,
                entry_price=0.0,
                size=0.0,
                outcome=TradeResult.SKIP,
                gross_pl=0.0,
                net_pl=0.0,
                reason=f"Spread min4 only {spread_at_min4:.0f} (<120)",
            )

        if spread_at_min4 >= 120:
            entry_direction = Direction.UP
            entry_price = 0.88
            reason = f"Min4 spread +{spread_at_min4:.0f}, locked UP"
        elif spread_at_min4 <= -120:
            entry_direction = Direction.DOWN
            entry_price = 0.88
            reason = f"Min4 spread {spread_at_min4:.0f}, locked DOWN"

        size = 20.0  # Bigger size karena higher confidence

    elif strategy == StrategyType.MIDDLE:
        # Ignored Middle: only trade clear momentum, skip flat
        # Entry 0.55-0.70 if spread > $60 and consistent
        if dir_min3 == Direction.NEUTRAL:
            return Trade(
                window_start=candles_5m[0]["time"],
                strategy="middle",
                htf_bias=htf_bias,
                spread_at_min3=spread_at_min3,
                entry_direction=None,
                entry_price=0.0,
                size=0.0,
                outcome=TradeResult.SKIP,
                gross_pl=0.0,
                net_pl=0.0,
                reason="Flat market (±$40)",
            )

        # Check consistency: all candles from min1-min3 same direction
        c0, c1, c2, c3 = candles_5m[0], candles_5m[1], candles_5m[2], candles_5m[3]
        hh_ll = (c1["close"] < c2["close"] < c3["close"])  # Higher highs
        lh_ll = (c1["close"] > c2["close"] > c3["close"])  # Lower lows

        if not (hh_ll or lh_ll):
            return Trade(
                window_start=candles_5m[0]["time"],
                strategy="middle",
                htf_bias=htf_bias,
                spread_at_min3=spread_at_min3,
                entry_direction=None,
                entry_price=0.0,
                size=0.0,
                outcome=TradeResult.SKIP,
                gross_pl=0.0,
                net_pl=0.0,
                reason="Zigzag, not consistent",
            )

        if dir_min3 == Direction.UP:
            entry_direction = Direction.UP
            entry_price = 0.65
            reason = "Momentum UP, consistent"
        else:
            entry_direction = Direction.DOWN
            entry_price = 0.65
            reason = "Momentum DOWN, consistent"

    # ---- RESOLUTION ----
    # Window resolves at end of minute 4 (candle 4 close)
    final_price = candles_5m[4]["close"]

    if entry_direction is None:
        return None  # Should not happen

    # Determine win/loss
    if entry_direction == Direction.UP:
        won = final_price > price_to_beat
    else:
        won = final_price < price_to_beat

    # Exact same price = tie (rare). Treat as loss for conservatism.
    if abs(final_price - price_to_beat) < 0.01:
        won = False
        reason += " | TIE (treated as loss)"

    if won:
        gross_pl = size * (1.0 - entry_price)
        net_pl = gross_pl - (size * fee_rate)
        outcome = TradeResult.WIN
    else:
        gross_pl = -size * entry_price
        net_pl = gross_pl - (size * fee_rate)
        outcome = TradeResult.LOSS

    return Trade(
        window_start=candles_5m[0]["time"],
        strategy=strategy.value,
        htf_bias=htf_bias,
        spread_at_min3=spread_at_min3,
        entry_direction=entry_direction,
        entry_price=entry_price,
        size=size,
        outcome=outcome,
        gross_pl=gross_pl,
        net_pl=net_pl,
        reason=reason,
    )


def run_backtest(candles: List[dict], strategy: StrategyType) -> BacktestResult:
    """Run backtest over all 5-minute windows in the data."""
    result = BacktestResult(strategy=strategy.value)

    # Need at least 20 candles (15 for HTF + 5 for window)
    if len(candles) < 20:
        print("[ERROR] Not enough data for backtest.")
        return result

    i = 15  # Start after HTF lookback
    while i + 5 <= len(candles):
        window = candles[i:i+5]
        before = candles[:i]

        trade = simulate_5m_window(window, before, strategy)
        if trade:
            result.trades.append(trade)
            if trade.outcome == TradeResult.WIN:
                result.wins += 1
                result.total_trades += 1
                result.gross_profit += trade.gross_pl
                result.net_pl += trade.net_pl
            elif trade.outcome == TradeResult.LOSS:
                result.losses += 1
                result.total_trades += 1
                result.gross_loss += trade.gross_pl
                result.net_pl += trade.net_pl
            else:
                result.skips += 1

        i += 5  # Move to next 5m window (non-overlapping)

    return result


def print_results(results: Dict[str, BacktestResult], days: int):
    print("\n" + "=" * 70)
    print(f"📊 BTC UP/DOWN 5m BACKTEST RESULTS")
    print(f"   Period: Last {days} days of 1-minute BTC data")
    print(f"   Windows tested: ~{days * 24 * 12} possible")
    print("=" * 70)

    for name, r in results.items():
        resolved = r.wins + r.losses
        print(f"\n🎯 Strategy: {name.upper()}")
        print(f"   Total Trades Taken: {resolved}")
        print(f"   Skipped:            {r.skips}")
        print(f"   Wins:               {r.wins}")
        print(f"   Losses:             {r.losses}")
        print(f"   Winrate:            {r.winrate:.1f}%")
        print(f"   Gross Profit:       +${r.gross_profit:,.2f}")
        print(f"   Gross Loss:         ${r.gross_loss:,.2f}")
        print(f"   NET P/L:            ${r.net_pl:,.2f}")
        print(f"   Avg Win:            ${r.avg_win:,.2f}")
        print(f"   Avg Loss:           ${r.avg_loss:,.2f}")

        if resolved > 0:
            pl_per_trade = r.net_pl / resolved
            print(f"   Avg per Trade:      ${pl_per_trade:,.2f}")

    print("\n" + "=" * 70)
    print("⚠️  NOTE: This uses BTC spot price as proxy.")
    print("   Real Polymarket prices may differ slightly due to:")
    print("   - Spread and slippage")
    print("   - Different price feed (Polymarket vs Yahoo)")
    print("   - Orderbook depth affecting fill prices")
    print("=" * 70)


def export_csv(trades: List[Trade], filename: str):
    """Export trades to CSV for analysis in Excel/Sheets."""
    with open(filename, "w") as f:
        f.write("Window,Strategy,HTF_Bias,Spread_Min3,Direction,Entry_Price,Size,Result,Gross_PL,Net_PL,Reason\n")
        for t in trades:
            f.write(
                f"{t.window_start.isoformat()},{t.strategy},"
                f"{t.htf_bias.value},{t.spread_at_min3:.2f},"
                f"{t.entry_direction.value if t.entry_direction else 'SKIP'},"
                f"{t.entry_price:.2f},{t.size:.2f},"
                f"{t.outcome.value},${t.gross_pl:.2f},${t.net_pl:.2f},"
                f'"{t.reason}"\n'
            )
    print(f"\n💾 Exported to: {filename}")


def main():
    parser = argparse.ArgumentParser(description="BTC 5m Strategy Backtester")
    parser.add_argument("--days", type=int, default=7, help="Days of historical data (default: 7)")
    parser.add_argument("--strategy", type=str, default="all", choices=["trend", "lock", "middle", "all"])
    parser.add_argument("--csv", type=str, help="Export trades to CSV file")
    parser.add_argument("--fee", type=float, default=0.02, help="Fee rate (default: 0.02 = 2%)")
    args = parser.parse_args()

    print(f"📥 Fetching {args.days} days of BTC 1m data from Yahoo Finance...")
    candles = fetch_btc_ohlc(days=args.days, interval="1m")

    if not candles:
        print("[ERROR] No data fetched. Try again later.")
        return

    print(f"   Got {len(candles)} 1-minute candles.")

    strategies = []
    if args.strategy == "all":
        strategies = [StrategyType.TREND, StrategyType.LOCK, StrategyType.MIDDLE]
    else:
        strategies = [StrategyType(args.strategy)]

    results = {}
    all_trades = []

    for strat in strategies:
        print(f"\n🔬 Running backtest: {strat.value}...")
        result = run_backtest(candles, strat)
        results[strat.value] = result
        all_trades.extend(result.trades)

    print_results(results, args.days)

    if args.csv:
        export_csv(all_trades, args.csv)

    # Print some example trades
    print("\n📝 Sample Trades (first 5):")
    for t in all_trades[:5]:
        if t.outcome != TradeResult.SKIP:
            print(f"   {t.window_start.strftime('%m-%d %H:%M')} | {t.strategy:6} | {t.entry_direction.value if t.entry_direction else 'SKIP':4} | "
                  f"Spread: {t.spread_at_min3:+7.2f} | {t.outcome.value:4} | Net: ${t.net_pl:+.2f} | {t.reason[:50]}")


if __name__ == "__main__":
    main()
