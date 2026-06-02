# BTC Up/Down 5m Scalping Guide

Complete playbook for scalping Polymarket's 5-minute BTC binary markets.

---

## Market Structure

- **Resolution**: Every 5 minutes
- **Outcome**: UP if BTC spot > Price to Beat at close; DOWN if below
- **Price to Beat**: Fixed at window open (first candle open)
- **Feed**: Coinbase/CryptoCompare spot BTC/USD
- **Liquidity**: Thin ($5K–50K per window typical)

---

## Pre-Market Setup (Before Window Opens)

### HTF Bias Check (M30 / H1)

Use TradingView or your broker:
- [ ] BTC M30: Price above VWAP + EMA 9 > EMA 21 = **Bullish bias**
- [ ] BTC M30: Price below VWAP + EMA 9 < EMA 21 = **Bearish bias**
- [ ] BTC M30: Mixed signals = **Neutral bias** → skip or reduce size

Funding rate (Binance/Bybit):
- Positive = longs pay shorts = bullish
- Negative = shorts pay longs = bearish

Record bias: ___________

---

## The 5-Minute Window

### Phase 1: Noise (0:00–1:30)
**Action: NO ENTRY**
- Watch price action only
- Mark Price to Beat
- Note opening direction and volatility

### Phase 2: Momentum Assessment (1:30–3:00)
**Action: EVALUATE**

Check at minute 3 (candle 3 close):
1. **Spread** = Current price – Price to Beat
2. **Direction** = UP if spread > +$40 / DOWN if spread < –$40 / NEUTRAL otherwise
3. **Consistency** = Are candles 1–3 forming higher highs + higher lows (UP) or lower lows + lower highs (DOWN)?

**Go / No-Go Rules:**
| Spread | Consistency | Decision |
|--------|-------------|----------|
| >+$60 | HH+HL | ✅ Continue (UP setup) |
| <–$60 | LH+LL | ✅ Continue (DOWN setup) |
| ±$20–40 | Zigzag | ❌ SKIP |
| >+$60 but zigzag | — | ❌ SKIP (unreliable) |

### Phase 3: Entry Timing (2:30–3:30)

**If continuing:**
- Check Polymarket orderbook for fill price
- **Do NOT use fixed 0.65¢** — price adjusts to spread

**Dynamic Pricing Guide:**
| Spread at min 3 | Realistic fill | Gross profit if win | Net (after 2% fee) |
|-----------------|---------------|---------------------|-------------------|
| $50–80 | 0.70–0.75¢ | $0.25–0.30 | $0.21–0.26 |
| $80–120 | 0.75–0.85¢ | $0.15–0.25 | $0.11–0.21 |
| $120–200 | 0.85–0.92¢ | $0.08–0.15 | $0.04–0.11 |
| $200+ | 0.92–0.97¢ | $0.03–0.08 | ≈0–0.04 |

> **Rule**: Only enter if net profit > $0.10 per share. Otherwise skip.

### Phase 4: Last-Minute Lock (4:00–4:55)

**Only if you missed Phase 3 or want higher certainty:**
- Spread must be >$120
- No reversal candle in minute 4
- Fill price will be 0.85–0.92¢
- Profit small but probability high (~90%+ directional)

---

## Position Sizing

**Base size per trade:**
- Conservative: $5–10
- Normal: $10–20
- Aggressive (lock only): $20–50

**Bankroll rules:**
- Max 1 trade per 5m window
- Max 5 trades per hour
- Max daily loss: 5% of bankroll
- Stop after 3 consecutive losses

---

## Exit Rules

### Early Exit (before resolution)
- Target: Sell at 0.80–0.90¢ if momentum stalls
- Stop loss mental: If spread shrinks to <$30 against your position, consider cutting

### Hold to Resolution
- Only if spread >$100 and no reversal signal at minute 4:30
- Accept binary outcome: $1.00 or $0.00

---

## No-Trade Conditions (Auto-Skip)

- [ ] Spread at minute 3 is <$40
- [ ] HTF bias is NEUTRAL and no clear momentum
- [ ] BTC just had a massive spike/rejection (>2% in 10 min)
- [ ] Major news event within 30 minutes (CPI, FOMC, etc.)
- [ ] You already lost 3 trades today
- [ ] You feel FOMO, revenge urge, or uncertainty
- [ ] Orderbook on Polymarket shows < $500 liquidity on your side

---

## Sample Trade Log Entry

```
Window: 2026-05-09 14:30 UTC
HTF Bias: Bullish (M30 EMA9>EMA21, above VWAP)
Spread min3: +$87
Direction: UP
Consistency: HH+HL ✅
Polymarket fill: 0.73¢
Size: $10
Result: WIN → resolved $1.00
Gross: +$2.70
Net: +$2.50 (after 2% fee)
Time held: 2.5 minutes
Emotion: Calm
Note: Clean momentum, no reversal in min4
```

---

## Backtest Interpretation

The included backtest (`scripts/backtest_btc_5m.py`) uses BTC spot as proxy and **assumes fixed entry prices**. It reliably shows directional edge (momentum continuation in 5m windows), but **dollar P/L is inflated**.

**How to use backtest results:**
1. Validate that your chosen strategy has >70% directional accuracy
2. Do NOT trust the dollar P/L numbers
3. Paper trade 20+ windows to discover real fill prices
4. Adjust expected returns using the Dynamic Pricing table above

---

## Key Differences from Forex/Gold Scalping

| Aspect | Forex/Gold | Polymarket 5m |
|--------|-----------|---------------|
| Leverage | Yes (up to 1:500) | No (full collateral) |
| Spread/fee | Broker spread | Taker fee ~2% + orderbook spread |
| Chart tools | MT5/TradingView | None (use external BTC chart) |
| Execution | Instant market order | Orderbook match (can slip) |
| Settlement | T+0 | Binary resolve (5 min or tied) |
| Edge source | Technical analysis | Informational + momentum |
