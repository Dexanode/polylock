# PolyLock — BTC 5m LOCK Strategy Bot for Polymarket CLOB V2

> Automated paper/live trading bot for Polymarket's **BTC Up or Down 5m** market,
> using the **LOCK strategy** — enter when BTC has significant spread from PTB
> at minute 3:30–4:20 of the 5-minute window.

![Status](https://img.shields.io/badge/status-LIVE%20TRADING-brightgreen)
![CLOB](https://img.shields.io/badge/CLOB-V2-blue)
![Python](https://img.shields.io/badge/python-3.12-blue)
![Chain](https://img.shields.io/badge/chain-Polygon-8247E5)

---

## ⚡ Quick Start

```bash
# 1. Clone
git clone https://github.com/Dexanode/polylock.git && cd polylock

# 2. Setup venv
python3 -m venv venv && source venv/bin/activate
pip install py-clob-client-v2 web3 eth-account py-builder-relayer-client

# 3. Configure (edit with your keys)
cp scripts/.env.example scripts/.env
nano scripts/.env

# 4. Paper test first
python3 scripts/poly_btc_5m_lock_50.py --bankroll 10 --spread 50

# 5. Go live
python3 scripts/poly_btc_5m_lock_50.py --live --bankroll 15 --spread 50 --daily-stop 5
```

---

## Strategy Overview

Polymarket's **BTC Up or Down 5m** market resolves every 5 minutes via Chainlink oracle.
Bot enters in the LOCK zone (3:30–4:20) when spread ≥ $50.

```
Window timeline:
  :00     :01     :02     :03    [:03:30 ─ :04:20]    :05
  ──────────────────────────────────────────────────────────
  PTB set     Monitoring...        LOCK ZONE          Resolve
```

### Win Conditions

| Direction | Condition | Profit |
|-----------|-----------|--------|
| **UP** (BUY YES) | BTC closes **above** PTB | `$(1.00 - entry) × shares` |
| **DOWN** (BUY NO) | BTC closes **below** PTB | `$(1.00 - entry) × shares` |

### Why It Works

At minute 3:30+ with $50+ spread, BTC has already moved significantly.
Reversing $50+ in the final 90 seconds is statistically uncommon.

---

## CLOB V2 — Deposit Wallet Flow

> ⚠️ **Polymarket migrated to CLOB V2 on April 28, 2026.** All new API accounts
> must use the **deposit wallet flow** with `POLY_1271` signature type.

### Architecture

```
┌──────────────┐     ┌──────────────────┐     ┌──────────────┐
│   Your EOA   │────→│  Deposit Wallet   │────→│  CLOB Order  │
│  (signer)    │     │  (UUPS proxy)     │     │  POLY_1271   │
│ 0x94A6...    │     │ 0x75c8...         │     │  signature   │
└──────────────┘     └──────────────────┘     └──────────────┘
```

| Component | Value |
|-----------|-------|
| **EOA** | Your wallet (holds nothing) |
| **Deposit Wallet** | UUPS proxy (holds pUSD + conditional tokens) |
| **Signature Type** | `POLY_1271` (type 3) |
| **Funder** | Deposit wallet address |
| **Collateral** | pUSD (6 decimals, backed by USDC) |
| **Order Type** | GTC (Good-Til-Cancelled) |
| **Min Shares** | 5 shares per trade |

### Deposit Steps

1. Fund your EOA with MATIC + USDC on Polygon
2. Deposit USDC on [polymarket.com](https://polymarket.com) — this creates your deposit wallet automatically
3. USDC converts to pUSD and lands in your deposit wallet
4. Bot auto-detects deposit wallet address (deterministic CREATE2)
5. Orders signed by EOA, validated via ERC-1271 on deposit wallet

---

## CLI Arguments

| Argument | Default | Description |
|----------|---------|-------------|
| `--bankroll` | `10.0` | Starting bankroll in USD |
| `--spread` | `50` | Minimum spread to trigger trade ($) |
| `--daily-stop` | `5.0` | Stop trading if daily loss exceeds this |
| `--max-trades` | `20` | Max trades per day |
| `--telegram-token` | — | Telegram bot token for alerts |
| `--chat-id` | — | Telegram chat/user ID |
| `--live` | off | Enable LIVE trading (requires `POLYMARKET_PRIVATE_KEY`) |
| `--signal` | off | Signal-only mode (Telegram alerts, no orders) |

---

## Signal Filters

Before executing, bot runs 3 quality filters:

| Filter | Threshold | Description |
|--------|-----------|-------------|
| **Volume** | ≥ 0.25× avg | Recent volume vs 10-candle baseline |
| **Momentum** | ≤ 0.12% opposing | 3-candle slope must not counter trade direction |
| **EV** | ≥ $0.00 | Expected value per share must be positive |

---

## Configuration

```env
# scripts/.env

# Wallet (LIVE mode)
POLYMARKET_PRIVATE_KEY=0xYOUR_KEY

# Telegram Alerts
TELEGRAM_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id

# Builder code (for order attribution)
POLYMARKET_BUILDER_CODE=0x2111a204350f2c552401b7d34b7cb61021e32b68a17a15ef712b978fd991f55d

# Proxy (optional)
PROXY_URL=http://user:pass@gate.proxies.fo:7777
```

---

## Risk Management

| Parameter | Value | Description |
|-----------|-------|-------------|
| Position size | 20% bankroll, max 5 shares | Per trade allocation |
| Daily stop loss | $5.00 | Bot halts on daily P&L ≤ -$5 |
| Min bankroll | $1.00 | Bot halts below this |
| Fee rate | 2% | Polymarket taker fee (applied at match) |
| Slippage | +$0.03 | Price buffer for better fill rate |
| Entry window | 3:30–4:20 | LOCK zone (seconds into window) |

---

## Dashboard

Access at `http://YOUR_SERVER_IP:3456`

| Section | Content |
|---------|---------|
| Bot Stats | Win/Loss, P&L, Bankroll |
| Live Price | Chainlink BTC/USD feed |
| Window Timer | Current 5m window countdown |
| Signal | Spread, direction, entry price |
| Trade History | All resolved windows |

---

## Backtesting

```bash
python3 scripts/backtest_btc_5m.py --days 7 --strategy lock
python3 scripts/backtest_btc_5m_v2.py --days 7 --bankroll 10
```

---

## File Structure

```
polylock/
├── scripts/
│   ├── poly_btc_5m_lock_50.py       # Main bot ($50 threshold, CLOB V2)
│   ├── poly_btc_5m_autotrader.py    # Bot A ($100 threshold, archived)
│   ├── poly_btc_5m_lock_alert.py    # Alert-only bot
│   ├── backtest_btc_5m.py           # Backtester V1
│   ├── backtest_btc_5m_v2.py        # Backtester V2 (dynamic pricing)
│   ├── polymarket.py                # CLI helper — query Polymarket API
│   ├── poly_test_signing.py         # Test CLOB signing
│   ├── debug_deposit.py             # Debug deposit wallet
│   ├── debug_keys.py                # Debug API keys
│   ├── debug_signer.py              # Debug signer addresses
│   ├── debug_wallet.py              # Debug wallet setup
│   ├── find_proxy.py                # Find proxy/safe wallets
│   ├── requirements.txt             # Python deps
│   ├── run_autotrader.sh            # Shell runner
│   └── .env.example                 # Config template
│
├── dashboard/
│   ├── server.js                    # Node.js API server
│   ├── index.html                   # Dashboard UI
│   └── terminal-login.html         # Terminal auth
│
├── logs/                            # Auto-created
│   ├── windows.jsonl                # Trade history
│   ├── stats.json                   # Daily stats
│   └── clob_creds.json              # CLOB API credentials
│
├── references/
│   ├── api-endpoints.md
│   ├── bot-operations.md
│   └── btc-5m-scalping-guide.md
│
├── ecosystem.config.cjs             # PM2 config
└── README.md
```

---

## Debugging Live Mode

```bash
# Check deposit wallet address
python3 scripts/debug_deposit.py

# Verify API keys
python3 scripts/debug_keys.py

# Test order signing
python3 scripts/poly_test_signing.py

# Check wallet setup
python3 scripts/debug_wallet.py
```

**Common errors & fixes:**

| Error | Cause | Fix |
|-------|-------|-----|
| `maker address not allowed` | EOA mode deprecated | Use POLY_1271 + deposit wallet |
| `invalid amounts` | Wrong decimal precision | Pass `tick_size="0.01"` |
| `couldn't be fully filled` | FOK too strict | Use GTC order type |
| `order signer address mismatch` | Wrong signature type | Use POLY_1271, not EOA |
| `Incorrect padding` | URL-safe base64 secret | Replace `_`→`/`, `-`→`+` |

---

## Disclaimer

This software is for **educational purposes only**. Prediction market trading
involves significant financial risk. Never risk money you cannot afford to lose.

---

*Built with Python 3 · Polymarket CLOB V2 · Chainlink · Polygon*
