#!/usr/bin/env python3
"""
BTC Up/Down 5m Strategy Backtester V2
Realistic dynamic pricing + $10 bankroll simulation

Usage:
    python3 backtest_btc_5m_v2.py --days 7 --bankroll 10
    python3 backtest_btc_5m_v2.py --days 5 --strategy middle
"""

import argparse
import json
import urllib.request
import random
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Tuple
from enum import Enum

random.seed(42)  # Reproducible


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
    spread_at_entry: float
    entry_direction: Optional[Direction]
    entry_price: float
    size: float
    outcome: TradeResult
    gross_pl: float
    net_pl: float
    bankroll_before: float
    bankroll_after: float
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
    max_bankroll: float = 10.0
    min_bankroll: float = 10.0
    max_drawdown_pct: float = 0.0
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
        losses = [abs(t.net_pl) for t in self.trades if t.outcome == TradeResult.LOSS]
        return sum(losses) / len(losses) if losses else 0.0

    @property
    def profit_factor(self) -> float:
        return abs(self.gross_profit / self.gross_loss) if self.gross_loss != 0 else float('inf')

    @property
    def expectancy(self) -> float:
        """Expected profit per trade (in $)."""
        resolved = self.wins + self.losses
        if resolved == 0:
            return 0.0
        return self.net_pl / resolved


def fetch_btc_ohlc(days: int = 7) -> List[dict]:
    symbol = "BTC-USD"
    range_param = f"{days}d" if days <= 60 else "1y"
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1m&range={range_param}"

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
        result = data["chart"]["result"][0]
        timestamps = result["timestamp"]
        quote = result["indicators"]["quote"][0]
        opens, highs, lows, closes = quote["open"], quote["high"], quote["low"], quote["close"]
        volumes = quote.get("volume", [0] * len(timestamps))

        candles = []
        for i in range(len(closes)):
            if closes[i] is None:
                continue
            candles.append({
                "time": datetime.fromtimestamp(timestamps[i], tz=timezone.utc),
                "open": float(opens[i]), "high": float(highs[i]),
                "low": float(lows[i]), "close": float(closes[i]),
                "volume": int(volumes[i]) if volumes[i] else 0,
            })
        return candles
    except Exception as e:
        print(f"[ERROR] Fetch failed: {e}")
        return []


def calculate_ema(data: List[float], period: int) -> Optional[float]:
    if len(data) < period:
        return None
    mult = 2 / (period + 1)
    ema = sum(data[:period]) / period
    for p in data[period:]:
        ema = (p - ema) * mult + ema
    return ema


def get_htf_bias(candles_before: List[dict]) -> Direction:
    """Use last 30 candles (30m) for HTF bias. More reliable than 15m."""
    if len(candles_before) < 30:
        return Direction.NEUTRAL
    recent = candles_before[-30:]
    closes = [c["close"] for c in recent]
    ema9 = calculate_ema(closes, 9)
    ema21 = calculate_ema(closes, 21)
    if ema9 is None or ema21 is None:
        return Direction.NEUTRAL
    if ema9 > ema21 * 1.001:  # Small buffer
        return Direction.UP
    elif ema9 < ema21 * 0.999:
        return Direction.DOWN
    return Direction.NEUTRAL


def get_polymarket_price(spread: float, entry_minute: float, direction: Direction) -> float:
    """
    REALISTIC dynamic pricing.
    Price reflects probability based on BTC spread from Price to Beat.
    """
    abs_spread = abs(spread)
    
    # Direct price mapping (more conservative than v1)
    if abs_spread < 20:
        base = 0.53
    elif abs_spread < 40:
        base = 0.58
    elif abs_spread < 60:
        base = 0.63
    elif abs_spread < 80:
        base = 0.68
    elif abs_spread < 100:
        base = 0.73
    elif abs_spread < 130:
        base = 0.78
    elif abs_spread < 160:
        base = 0.83
    elif abs_spread < 200:
        base = 0.87
    else:
        base = 0.91
    
    # Direction check
    if (direction == Direction.UP and spread > 0) or (direction == Direction.DOWN and spread < 0):
        price = base
    else:
        price = 1.0 - base
    
    # Lock premium for minute 4+ with large spread
    if entry_minute >= 4.0 and abs_spread >= 100:
        price = min(0.94, price + 0.03)
    
    # Micro noise
    noise = random.uniform(-0.015, 0.015)
    price = round(price + noise, 2)
    return max(0.05, min(0.95, price))


def get_position_size(bankroll: float, entry_price: float, confidence: str = "normal") -> float:
    """
    Position sizing for $10 bankroll.
    Never risk more than we can afford to lose.
    """
    if bankroll < 2.0:
        return 0.0  # Too broke to trade
    
    # Risk 80-100% of bankroll per trade (aggressive but needed for $10)
    if confidence == "high":
        size = min(bankroll * 0.90, 10.0)
    elif confidence == "normal":
        size = min(bankroll * 0.80, 8.0)
    else:
        size = min(bankroll * 0.60, 5.0)
    
    # Must have enough to cover the share cost
    max_cost = size * entry_price
    if max_cost > bankroll * 0.95:
        size = (bankroll * 0.95) / entry_price
    
    return round(size, 2)


def calculate_fees(gross_pl: float, trade_size: float, fee_rate: float = 0.02) -> float:
    """
    Polymarket fee model (simplified):
    - Taker fee: 2% on trade value
    - Winner pays fee on profit + principal? 
    For simplicity: deduct 2% from gross P/L.
    More accurate: if you win $3 on $10 trade, you pay 2% of $10 = $0.20 fee.
    Net = $3.00 - $0.20 = $2.80
    """
    fee = trade_size * fee_rate
    if gross_pl > 0:
        return gross_pl - fee
    else:
        return gross_pl - fee


def simulate_window(
    candles_5m: List[dict],
    candles_before: List[dict],
    strategy: str,
    bankroll: float,
    fee_rate: float = 0.02,
) -> Tuple[Optional[Trade], float]:
    """
    Simulate one 5m window. Returns (Trade, new_bankroll).
    """
    if len(candles_5m) < 5 or bankroll < 2.0:
        return None, bankroll
    
    ptb = candles_5m[0]["open"]
    htf_bias = get_htf_bias(candles_before)
    
    # --- Minute 3 data ---
    min3_price = candles_5m[3]["close"]
    spread_min3 = min3_price - ptb
    
    # --- Minute 4 data ---
    min4_price = candles_5m[4]["close"]
    spread_min4 = min4_price - ptb
    
    entry_direction = None
    entry_price = 0.0
    entry_minute = 0.0
    size = 0.0
    reason = ""
    
    if strategy == "lock":
        # Last-Minute Lock: entry at minute 4, spread > $100
        if abs(spread_min4) < 100:
            return Trade(
                window_start=candles_5m[0]["time"], strategy="lock",
                htf_bias=htf_bias, spread_at_entry=spread_min4,
                entry_direction=None, entry_price=0.0, size=0.0,
                outcome=TradeResult.SKIP, gross_pl=0.0, net_pl=0.0,
                bankroll_before=bankroll, bankroll_after=bankroll,
                reason=f"Min4 spread {spread_min4:.0f} < 100"
            ), bankroll
        
        entry_minute = 4.0
        if spread_min4 > 100:
            entry_direction = Direction.UP
        else:
            entry_direction = Direction.DOWN
        entry_price = get_polymarket_price(spread_min4, 4.0, entry_direction)
        size = get_position_size(bankroll, entry_price, "high")
        reason = f"Lock min4 spread {spread_min4:+.0f}"
    
    elif strategy == "middle":
        # Ignored Middle: entry min3, consistent momentum, spread > $40
        if abs(spread_min3) < 40:
            return Trade(
                window_start=candles_5m[0]["time"], strategy="middle",
                htf_bias=htf_bias, spread_at_entry=spread_min3,
                entry_direction=None, entry_price=0.0, size=0.0,
                outcome=TradeResult.SKIP, gross_pl=0.0, net_pl=0.0,
                bankroll_before=bankroll, bankroll_after=bankroll,
                reason=f"Spread {spread_min3:.0f} < 40"
            ), bankroll
        
        # Check consistency: min1, min2, min3 all same direction
        c1, c2, c3 = candles_5m[1]["close"], candles_5m[2]["close"], candles_5m[3]["close"]
        up_trend = c1 < c2 < c3
        down_trend = c1 > c2 > c3
        
        if not (up_trend or down_trend):
            return Trade(
                window_start=candles_5m[0]["time"], strategy="middle",
                htf_bias=htf_bias, spread_at_entry=spread_min3,
                entry_direction=None, entry_price=0.0, size=0.0,
                outcome=TradeResult.SKIP, gross_pl=0.0, net_pl=0.0,
                bankroll_before=bankroll, bankroll_after=bankroll,
                reason="Zigzag, not consistent"
            ), bankroll
        
        entry_minute = 3.0
        if spread_min3 > 0:
            entry_direction = Direction.UP
        else:
            entry_direction = Direction.DOWN
        entry_price = get_polymarket_price(spread_min3, 3.0, entry_direction)
        size = get_position_size(bankroll, entry_price, "normal")
        reason = f"Momentum {entry_direction.value}, spread {spread_min3:+.0f}"
    
    elif strategy == "trend":
        # HTF Trend: entry min2-3, direction aligns with HTF, momentum present
        if htf_bias == Direction.NEUTRAL:
            return Trade(
                window_start=candles_5m[0]["time"], strategy="trend",
                htf_bias=htf_bias, spread_at_entry=spread_min3,
                entry_direction=None, entry_price=0.0, size=0.0,
                outcome=TradeResult.SKIP, gross_pl=0.0, net_pl=0.0,
                bankroll_before=bankroll, bankroll_after=bankroll,
                reason="HTF neutral"
            ), bankroll
        
        # Need some momentum
        price_min1 = candles_5m[1]["close"]
        momentum = abs(min3_price - price_min1)
        if momentum < 25:
            return Trade(
                window_start=candles_5m[0]["time"], strategy="trend",
                htf_bias=htf_bias, spread_at_entry=spread_min3,
                entry_direction=None, entry_price=0.0, size=0.0,
                outcome=TradeResult.SKIP, gross_pl=0.0, net_pl=0.0,
                bankroll_before=bankroll, bankroll_after=bankroll,
                reason="Momentum too low"
            ), bankroll
        
        # Direction must align with HTF
        if spread_min3 > 40 and htf_bias == Direction.UP:
            entry_direction = Direction.UP
        elif spread_min3 < -40 and htf_bias == Direction.DOWN:
            entry_direction = Direction.DOWN
        else:
            return Trade(
                window_start=candles_5m[0]["time"], strategy="trend",
                htf_bias=htf_bias, spread_at_entry=spread_min3,
                entry_direction=None, entry_price=0.0, size=0.0,
                outcome=TradeResult.SKIP, gross_pl=0.0, net_pl=0.0,
                bankroll_before=bankroll, bankroll_after=bankroll,
                reason="Direction mismatch HTF"
            ), bankroll
        
        entry_minute = 3.0
        entry_price = get_polymarket_price(spread_min3, 3.0, entry_direction)
        size = get_position_size(bankroll, entry_price, "normal")
        reason = f"HTF {htf_bias.value} + momentum {entry_direction.value}"
    
    else:
        return None, bankroll
    
    # No entry
    if entry_direction is None or size <= 0:
        return None, bankroll
    
    # --- RESOLUTION ---
    final_price = candles_5m[4]["close"]
    
    if entry_direction == Direction.UP:
        won = final_price > ptb
    else:
        won = final_price < ptb
    
    # Tie = loss (conservative)
    if abs(final_price - ptb) < 0.01:
        won = False
        reason += " | TIE"
    
    # Calculate P/L
    if won:
        gross_pl = size * (1.0 - entry_price)
        outcome = TradeResult.WIN
    else:
        gross_pl = -size * entry_price
        outcome = TradeResult.LOSS
    
    net_pl = calculate_fees(gross_pl, size, fee_rate)
    new_bankroll = bankroll + net_pl
    
    return Trade(
        window_start=candles_5m[0]["time"], strategy=strategy,
        htf_bias=htf_bias, spread_at_entry=spread_min3 if entry_minute == 3.0 else spread_min4,
        entry_direction=entry_direction, entry_price=entry_price, size=size,
        outcome=outcome, gross_pl=gross_pl, net_pl=net_pl,
        bankroll_before=bankroll, bankroll_after=new_bankroll,
        reason=reason
    ), new_bankroll


def run_backtest(candles: List[dict], strategy: str, bankroll_start: float = 10.0) -> BacktestResult:
    result = BacktestResult(strategy=strategy)
    bankroll = bankroll_start
    result.max_bankroll = bankroll
    result.min_bankroll = bankroll
    
    if len(candles) < 35:
        print("[ERROR] Not enough data.")
        return result
    
    i = 30  # HTF lookback = 30 candles
    while i + 5 <= len(candles):
        window = candles[i:i+5]
        before = candles[:i]
        
        trade, new_bankroll = simulate_window(window, before, strategy, bankroll)
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
            
            bankroll = new_bankroll
            result.max_bankroll = max(result.max_bankroll, bankroll)
            result.min_bankroll = min(result.min_bankroll, bankroll)
            
            dd = (result.max_bankroll - bankroll) / result.max_bankroll * 100
            result.max_drawdown_pct = max(result.max_drawdown_pct, dd)
        
        i += 5
    
    return result


def kelly_criterion(winrate: float, avg_win: float, avg_loss: float) -> float:
    """Fraction of bankroll to bet per Kelly."""
    if avg_loss == 0:
        return 0.0
    b = avg_win / avg_loss  # Odds
    p = winrate / 100.0
    q = 1 - p
    kelly = (b * p - q) / b
    return max(0.0, min(kelly, 0.5))  # Cap at 50%


def risk_of_ruin(winrate: float, avg_win: float, avg_loss: float, bankroll: float, bet_size: float) -> float:
    """Probability of busting (reaching $0)."""
    if avg_loss == 0 or bet_size == 0:
        return 0.0
    p = winrate / 100.0
    q = 1 - p
    if p <= q:
        return 1.0  # Certain ruin eventually
    
    # Simplified: R = (q/p)^n where n = bankroll / avg_loss
    n = bankroll / bet_size
    try:
        r = (q / p) ** n
    except OverflowError:
        r = 0.0
    return min(1.0, r)


def print_results(results: Dict[str, BacktestResult], days: int, bankroll: float):
    print("\n" + "=" * 75)
    print(f"  📊 BTC 5m BACKTEST V2 — REALISTIC DYNAMIC PRICING")
    print(f"  Period: {days} days | Start Bankroll: ${bankroll:.2f}")
    print("=" * 75)
    
    for name, r in results.items():
        resolved = r.wins + r.losses
        final_br = r.trades[-1].bankroll_after if r.trades else bankroll
        
        print(f"\n🎯 Strategy: {name.upper()}")
        print(f"   Trades Taken:     {resolved}")
        print(f"   Skipped:          {r.skips}")
        print(f"   Win / Loss:       {r.wins} / {r.losses}")
        print(f"   Winrate:          {r.winrate:.1f}%")
        print(f"   Avg Win:          ${r.avg_win:.2f}")
        print(f"   Avg Loss:         ${r.avg_loss:.2f}")
        print(f"   Gross Profit:     +${r.gross_profit:.2f}")
        print(f"   Gross Loss:       ${r.gross_loss:.2f}")
        print(f"   NET P/L:          ${r.net_pl:+.2f}")
        print(f"   Final Bankroll:   ${final_br:.2f}")
        print(f"   Max Drawdown:     {r.max_drawdown_pct:.1f}%")
        print(f"   Profit Factor:    {r.profit_factor:.2f}")
        print(f"   Expectancy:       ${r.expectancy:.2f}/trade")
        
        if resolved > 0 and r.avg_loss > 0:
            kelly = kelly_criterion(r.winrate, r.avg_win, r.avg_loss)
            print(f"   Kelly %:          {kelly*100:.1f}% (optimal bet per trade)")
            
            avg_bet = sum(t.size for t in r.trades if t.outcome != TradeResult.SKIP) / resolved
            ruin = risk_of_ruin(r.winrate, r.avg_win, r.avg_loss, bankroll, avg_bet)
            print(f"   Risk of Ruin:     {ruin*100:.1f}% (with avg bet ${avg_bet:.2f})")
        
        # ROI
        roi = (final_br - bankroll) / bankroll * 100
        print(f"   ROI:              {roi:+.1f}%")
        
        # Recommendation
        if r.expectancy > 0.5 and r.winrate >= 60:
            print(f"   ✅ VERDICT: VIABLE for $10 bankroll")
        elif r.expectancy > 0 and r.winrate >= 55:
            print(f"   ⚠️  VERDICT: MARGINAL — needs larger sample")
        else:
            print(f"   ❌ VERDICT: NOT VIABLE — negative expectancy")
    
    print("\n" + "=" * 75)
    print("⚠️  DISCLAIMER:")
    print("   • Uses BTC spot as proxy. Polymarket may use different feed.")
    print("   • Dynamic pricing is estimated — real prices vary.")
    print("   • Does NOT account for network/gas fees.")
    print("   • Past performance does not guarantee future results.")
    print("=" * 75)


def main():
    parser = argparse.ArgumentParser(description="BTC 5m Backtest V2")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--strategy", type=str, default="all", choices=["trend", "lock", "middle", "all"])
    parser.add_argument("--bankroll", type=float, default=10.0, help="Starting bankroll (default $10)")
    parser.add_argument("--fee", type=float, default=0.02, help="Fee rate")
    parser.add_argument("--csv", type=str, help="Export trades to CSV")
    args = parser.parse_args()

    print(f"📥 Fetching {args.days} days BTC 1m data...")
    candles = fetch_btc_ohlc(days=args.days)
    if not candles:
        print("[ERROR] No data.")
        return
    print(f"   Got {len(candles)} candles.")

    strategies = ["trend", "lock", "middle"] if args.strategy == "all" else [args.strategy]
    results = {}
    all_trades = []

    for strat in strategies:
        print(f"🔬 Backtesting: {strat}...")
        r = run_backtest(candles, strat, args.bankroll)
        results[strat] = r
        all_trades.extend(r.trades)

    print_results(results, args.days, args.bankroll)

    if args.csv:
        with open(args.csv, "w") as f:
            f.write("Window,Strategy,HTF,Spread,Direction,EntryPrice,Size,Result,Gross,Net,BR_Before,BR_After,Reason\n")
            for t in all_trades:
                f.write(f"{t.window_start.isoformat()},{t.strategy},{t.htf_bias.value},"
                        f"{t.spread_at_entry:.2f},{t.entry_direction.value if t.entry_direction else 'SKIP'},"
                        f"{t.entry_price:.2f},{t.size:.2f},{t.outcome.value},"
                        f"${t.gross_pl:.2f},${t.net_pl:.2f},${t.bankroll_before:.2f},${t.bankroll_after:.2f},"
                        f'"{t.reason}"\n')
        print(f"\n💾 Exported: {args.csv}")

    # Print sample winning trades
    print("\n📝 Sample Winning Trades:")
    wins = [t for t in all_trades if t.outcome == TradeResult.WIN][:5]
    for t in wins:
        print(f"   {t.window_start.strftime('%m-%d %H:%M')} | {t.strategy:6} | {t.entry_direction.value:4} | "
              f"Price: {t.entry_price:.2f} | Net: ${t.net_pl:+.2f} | BR: ${t.bankroll_before:.2f}→${t.bankroll_after:.2f} | {t.reason[:40]}")

    # Print sample losing trades
    print("\n📝 Sample Losing Trades:")
    losses = [t for t in all_trades if t.outcome == TradeResult.LOSS][:3]
    for t in losses:
        print(f"   {t.window_start.strftime('%m-%d %H:%M')} | {t.strategy:6} | {t.entry_direction.value:4} | "
              f"Price: {t.entry_price:.2f} | Net: ${t.net_pl:+.2f} | BR: ${t.bankroll_before:.2f}→${t.bankroll_after:.2f} | {t.reason[:40]}")


if __name__ == "__main__":
    main()
