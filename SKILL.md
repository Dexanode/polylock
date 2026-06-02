---
name: polymarket
description: "Query Polymarket: markets, prices, orderbooks, history."
version: 1.0.0
author: Hermes Agent + Teknium
tags: [polymarket, prediction-markets, market-data, trading]
---

# Polymarket — Prediction Market Data

Query prediction market data from Polymarket using their public REST APIs.
All endpoints are read-only and require zero authentication.

See `references/api-endpoints.md` for the full endpoint reference with curl examples.

## When to Use

- User asks about prediction markets, betting odds, or event probabilities
- User wants to know "what are the odds of X happening?"
- User asks about Polymarket specifically
- User wants market prices, orderbook data, or price history
- User asks to monitor or track prediction market movements

## Key Concepts

- **Events** contain one or more **Markets** (1:many relationship)
- **Markets** are binary outcomes with Yes/No prices between 0.00 and 1.00
- Prices ARE probabilities: price 0.65 means the market thinks 65% likely
- `outcomePrices` field: JSON-encoded array like `["0.80", "0.20"]`
- `clobTokenIds` field: JSON-encoded array of two token IDs [Yes, No] for price/book queries
- `conditionId` field: hex string used for price history queries
- Volume is in USDC (US dollars)

## Three Public APIs

1. **Gamma API** at `gamma-api.polymarket.com` — Discovery, search, browsing
2. **CLOB API** at `clob.polymarket.com` — Real-time prices, orderbooks, history
3. **Data API** at `data-api.polymarket.com` — Trades, open interest

## Typical Workflow

When a user asks about prediction market odds:

1. **Search** using the Gamma API public-search endpoint with their query
2. **Parse** the response — extract events and their nested markets
3. **Present** market question, current prices as percentages, and volume
4. **Deep dive** if asked — use clobTokenIds for orderbook, conditionId for history

## Presenting Results

Format prices as percentages for readability:
- outcomePrices `["0.652", "0.348"]` becomes "Yes: 65.2%, No: 34.8%"
- Always show the market question and probability
- Include volume when available

Example: `"Will X happen?" — 65.2% Yes ($1.2M volume)`

## Parsing Double-Encoded Fields

The Gamma API returns `outcomePrices`, `outcomes`, and `clobTokenIds` as JSON strings
inside JSON responses (double-encoded). When processing with Python, parse them with
`json.loads(market['outcomePrices'])` to get the actual array.

## Rate Limits

Generous — unlikely to hit for normal usage:
- Gamma: 4,000 requests per 10 seconds (general)
- CLOB: 9,000 requests per 10 seconds (general)
- Data: 1,000 requests per 10 seconds (general)

## Limitations

- This skill is read-only — it does not support placing trades
- Trading requires wallet-based crypto authentication (EIP-712 signatures)
- Some new markets may have empty price history
- Geographic restrictions apply to trading but read-only data is globally accessible

---

## 🪙 Crypto Binary Scalping (BTC Up/Down 5m)

Polymarket now offers rapid-resolution crypto markets (e.g., "BTC Up or Down 5m"). These resolve every 5 minutes based on whether BTC spot is above or below a "Price to Beat" at the interval close.

**Key differences from event-based prediction markets:**
- Resolution: Every 5 minutes (not days/weeks)
- Structure: Binary outcome (UP/DOWN)
- Data feed: BTC spot price (Coinbase/CryptoCompare)
- Liquidity: Much lower than forex — orderbook can be thin ($5K–$50K per window)

### Scalping Strategies

Three strategies have been backtested against BTC 1m spot data. See `references/btc-5m-scalping-guide.md` for the full decision tree and backtest methodology.

| Strategy | When to use | Entry timing | Typical price | Backtested winrate* |
|----------|------------|--------------|---------------|---------------------|
| **Last-Minute Lock** | BTC spread >$120 at minute 4 | Minute 4:00–4:55 | 0.85¢–0.92¢ | ~100% directional |
| **Ignored Middle** | Clear momentum, consistent candles | Minute 2:30–3:30 | 0.60¢–0.75¢ | ~97% directional |
| **HTF Trend** | Strong M30/H1 trend alignment | Minute 2:00–3:00 | 0.55¢–0.65¢ | Requires real HTF bias (not 15m) |

*Directional winrate using BTC spot as proxy. **Actual Polymarket P/L is lower** due to:
- Dynamic pricing (spread >$80 means "UP" already costs 0.75¢+, not 0.65¢)
- Taker fees (~2%)
- Slippage on thin orderbooks
- Different price feed vs. your broker

### Critical Pitfalls

| Pitfall | Why it hurts | Fix |
|---------|-------------|-----|
| **Buying at fixed 0.65¢** in simulation | Real market price adjusts to spread instantly | Use dynamic pricing model: spread $50→0.70¢, $80→0.80¢, $120→0.88¢ |
| **Ignoring fees in P/L calc** | 2% taker fee eats 4% round-trip | Subtract fees from gross on every trade |
| **Overtrading flat windows** | ±$20 noise = 50/50 coin flip | Skip if spread at minute 3 is <$40 |
| **Liquidity illusion** | Backtest assumes fill at desired price | Real orderbook may have $50–$200 gaps |
| **No leverage** | Full collateral per trade | Size accordingly; max 1–2% bankroll per window |
| **Settlement delay** | Winnings not instant | Plan cash flow, don't compound immediately |

### Quick Decision Tree

```
1. HTF bias? (M30/H1 trend + VWAP)
   NEUTRAL → NO TRADE

2. Minute 0–2: WATCH ONLY (noise)

3. Minute 3: Spread vs Price to Beat?
   <$40 or zigzag → SKIP
   >$60 + consistent direction → Continue

4. Entry price realistic?
   Spread $60-100 → expect 0.70-0.80¢
   Spread $100+ → expect 0.80-0.90¢
   Profit still > fee + slippage? → ENTRY

5. Minute 4: Spread >$120 + no reversal?
   → LOCK entry (0.85¢+, high confidence, small profit)

6. NEVER trade:
   - Flat market
   - News pending (CPI, FOMC) within 30 min
   - After 3 losses same day
   - When emotional / FOMO
```

### Tools in this skill

| File | Purpose |
|------|---------|
| `scripts/backtest_btc_5m.py` | Backtest strategies against BTC 1m Yahoo data |
| `scripts/poly_btc_5m_lock_50.py` | **BOT B** — $50 threshold autotrader (user's preferred) |
| `scripts/poly_btc_5m_autotrader.py` | **BOT A** — $100 threshold autotrader (legacy) |
| `scripts/run_autotrader.sh` | Unified launcher for both bots (supports `--screen`, `--bot 50|100`) |
| `references/btc-5m-scalping-guide.md` | Full decision tree, position sizing, exit rules |
| `references/bot-operations.md` | **Running & troubleshooting live bots** — health checks, logs, restart |
| `templates/polymarket-journal.md` | Trading journal template (Markdown + CSV) |

**Running the backtest:**
```bash
python3 scripts/backtest_btc_5m.py --days 7 --strategy all --csv results.csv
```

**Running BOT B ($50) persistently:**
```bash
bash scripts/run_autotrader.sh --bot 50 --screen --token <TOKEN> --chat-id <ID>
```

> ⚠️ **Backtest P/L is inflated.** Use it to validate directional edge only, not dollar returns. Always paper-trade 20+ windows before sizing up.
