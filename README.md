# PolyLock — BTC 5m LOCK Strategy Bot

> Automated paper/live trading bot for Polymarket's **BTC Up or Down 5m** market,
> using the **LOCK strategy** — enter at minute 4 when BTC has a significant spread
> from the Price to Beat (PTB), locked in the direction likely to hold.

![Dashboard Preview](dashboard/violet.jpg)

---

## Table of Contents

- [Strategy Overview](#strategy-overview)
- [Architecture](#architecture)
- [Requirements](#requirements)
- [Installation](#installation)
- [Configuration](#configuration)
- [Running the Bot](#running-the-bot)
- [Dashboard](#dashboard)
- [Backtesting](#backtesting)
- [File Structure](#file-structure)
- [Signal Filters](#signal-filters)
- [Risk Management](#risk-management)
- [Going Live](#going-live)
- [FAQ](#faq)

---

## Strategy Overview

Polymarket's **BTC Up or Down 5m** market resolves every 5 minutes via Chainlink oracle.
The market asks: *"Will BTC be higher or lower than [Price to Beat] at window close?"*

### The LOCK Setup

```
Window timeline:
  :00  :01  :02  :03  [  :04 ──────── :04:55  ]  :05
  ─────────────────────────────────────────────────
  PTB set    Monitoring...     LOCK ZONE    Resolve
```

| Minute | Action |
|--------|--------|
| 0:00 | Price to Beat (PTB) is set = current BTC price |
| 0:00–3:59 | Bot monitors, no trade |
| 4:00–4:55 | **LOCK ZONE** — if spread ≥ $50 & filters pass → execute trade |
| 5:00 | Window resolves via Chainlink oracle |

**Win condition:**
- Bet **UP** → BTC must close **above** PTB
- Bet **DOWN** → BTC must close **below** PTB

### Why It Works

At minute 4 with a $50+ spread, BTC has already moved significantly. Reversing
$50+ in the final 45 seconds is statistically uncommon, giving an edge.

| Spread at Min 4 | Est. Entry | Est. Win Rate | EV/share |
|-----------------|------------|---------------|----------|
| $50–$70 | 0.65 | ~72% | -$0.013 |
| $70–$110 | 0.70–0.75 | ~78–82% | +$0.058 |
| $110–$160 | 0.78–0.83 | ~86–90% | +$0.064 |
| $160+ | 0.87–0.91 | ~93–95% | +$0.042 |

> Break-even win rate at entry 0.65 = **~72%** | at entry 0.78 = **~82%**

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                      Price Sources                       │
│   Chainlink (Polygon) ──primary──┐                       │
│   Binance spot ────────fallback──┤──→ Bot Python         │
│   Yahoo Finance ───────fallback──┘                       │
└─────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────┐    writes     ┌──────────────────────┐
│  poly_btc_5m_   │ ──────────── │  logs/windows.jsonl  │
│  lock_50.py     │              │  logs/stats.json      │
│  (Main Bot)     │              └────────────┬─────────┘
└─────────────────┘                           │ reads
                                              ▼
┌─────────────────────────────────────────────────────────┐
│              dashboard/server.js (Node.js)               │
│  /api/btc-price  — Chainlink proxy (no CORS)             │
│  /api/windows    — trade history                         │
│  /api/stats      — daily stats                           │
│  /terminal/*     — ttyd proxy (web terminal)             │
│  static files    — index.html dashboard                  │
└──────────────────────────────┬──────────────────────────┘
                               │ port 3456
                               ▼
                    Browser Dashboard (http://IP:3456)
                    Web Terminal  (http://IP:3456/terminal/?token=...)
```

### PM2 Process Map

| ID | Name | Description | Port |
|----|------|-------------|------|
| `polylock-bot-50` | Main trading bot | $50 threshold, paper mode | — |
| `polylock-dashboard` | Node.js server | Serves dashboard + API | 3456 |
| `polylock-terminal` | ttyd | Web terminal (bash) | localhost:3457 |

---

## Requirements

### System
- **OS**: Ubuntu 20.04+ / Debian 11+
- **Python**: 3.9+
- **Node.js**: 18+
- **PM2**: `npm install -g pm2`
- **ttyd**: `apt-get install ttyd`

### Python packages (paper mode — stdlib only)
Paper trading mode requires **zero external packages** — uses only Python stdlib
(`json`, `urllib`, `datetime`, `collections`, `dataclasses`).

### Python packages (live mode only)
```
eth-account>=0.8.0   # EIP-712 wallet signing
web3>=6.0.0          # Polygon RPC interaction
```

---

## Installation

### 1. Clone repo

```bash
git clone https://github.com/YOUR_USERNAME/polylock.git
cd polylock
```

### 2. Install Node dependencies (dashboard)

```bash
# PM2
npm install -g pm2

# ttyd (web terminal)
apt-get install -y ttyd
```

### 3. Install Python dependencies (live mode only)

```bash
pip install eth-account web3
```

### 4. Configure environment

```bash
cp scripts/.env.example scripts/.env
nano scripts/.env
```

```env
# Wallet (LIVE mode only)
POLYMARKET_PRIVATE_KEY=0xYOUR_PRIVATE_KEY

# Telegram alerts (optional)
TELEGRAM_TOKEN=YOUR_BOT_TOKEN
TELEGRAM_CHAT_ID=YOUR_CHAT_ID

# Proxy (optional — if Polymarket is geo-blocked)
# PROXY_URL=http://user:pass@gate.proxies.fo:7777
```

### 5. Create log directory

```bash
mkdir -p logs
```

---

## Running the Bot

### Option A — PM2 (recommended, runs in background)

```bash
# Start all services (bot + dashboard + terminal)
pm2 start ecosystem.config.cjs

# Save process list (auto-restart on server reboot)
pm2 save
pm2 startup
```

### Option B — Manual

```bash
# Paper mode
python3 scripts/poly_btc_5m_lock_50.py

# With Telegram alerts
python3 scripts/poly_btc_5m_lock_50.py \
  --telegram-token YOUR_TOKEN \
  --chat-id YOUR_CHAT_ID

# Custom parameters
python3 scripts/poly_btc_5m_lock_50.py \
  --bankroll 10 \
  --spread 50 \
  --daily-stop 5
```

### CLI Arguments

| Argument | Default | Description |
|----------|---------|-------------|
| `--bankroll` | `10.0` | Starting bankroll in USD |
| `--spread` | `50` | Minimum spread to trigger trade ($) |
| `--daily-stop` | `5.0` | Stop trading if daily loss exceeds this |
| `--telegram-token` | — | Telegram bot token for alerts |
| `--chat-id` | — | Telegram chat/user ID |
| `--live` | off | Enable live trading (requires private key) |

### PM2 Commands

```bash
pm2 status                        # show all processes
pm2 logs polylock-bot-50          # live bot logs
pm2 logs polylock-bot-50 --lines 50  # last 50 lines
pm2 restart polylock-bot-50       # restart bot
pm2 stop polylock-bot-50          # pause bot
```

---

## Dashboard

Access at `http://YOUR_SERVER_IP:3456`

```
┌─────────────────┬─────────────────┬──────────────────┐
│  Bot Stats      │   Hero / BTC    │  Window Live     │
│  Win/Loss/P&L   │   Chainlink     │  Timer + Spread  │
│  Bankroll       │   Price Feed    │  Win Prob Meter  │
├─────────────────┼─────────────────┤  Lock Badge      │
│  Activity Log   │  Signal & EV    ├──────────────────┤
│  (live feed)    │  Table          │  Trade History   │
└─────────────────┴─────────────────┴──────────────────┘
```

### Web Terminal

No SSH needed. Access from browser:

```
http://YOUR_SERVER_IP:3456/terminal/?token=polylock2025
```

Or click the **⌘** icon in the dock at bottom of dashboard.

> **Change the token**: Edit `ecosystem.config.cjs` → `TERMINAL_TOKEN`, then `pm2 restart polylock-dashboard`

### API Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /api/btc-price` | Live BTC price from Chainlink |
| `GET /api/windows` | All trade windows (newest first) |
| `GET /api/stats` | Today's stats (trades, W/L, P&L, bankroll) |

---

## Backtesting

```bash
# Run backtest on last 7 days
python3 scripts/backtest_btc_5m.py --days 7 --strategy lock

# All strategies, export to CSV
python3 scripts/backtest_btc_5m.py --days 30 --strategy all --csv results.csv

# V2 — realistic dynamic pricing with bankroll simulation
python3 scripts/backtest_btc_5m_v2.py --days 7 --bankroll 10

# Available strategies: trend | lock | middle | all
```

---

## File Structure

```
polylock/
├── scripts/
│   ├── poly_btc_5m_lock_50.py    # Main bot — $50 threshold LOCK strategy
│   ├── poly_btc_5m_autotrader.py # Bot A — $100 threshold (archived)
│   ├── poly_btc_5m_lock_alert.py # Alert-only bot (no auto-trade)
│   ├── polymarket.py             # CLI helper — query Polymarket API
│   ├── backtest_btc_5m.py        # Backtester V1
│   ├── backtest_btc_5m_v2.py     # Backtester V2 (dynamic pricing)
│   ├── poly_test_signing.py      # Test CLOB signing (live prep)
│   ├── test_proxy.py             # Test proxy + API connectivity
│   ├── run_autotrader.sh         # Shell runner script
│   ├── requirements.txt          # Python deps (live mode)
│   └── .env.example              # Config template
│
├── dashboard/
│   ├── server.js                 # Node.js server + API + ttyd proxy
│   ├── index.html                # Dashboard UI (single file)
│   ├── terminal-login.html       # Terminal auth page
│   └── violet.jpg                # Hero background image
│
├── logs/                         # Auto-created by bot
│   ├── windows.jsonl             # All trade windows (persistent)
│   └── stats.json                # Latest daily stats
│
├── references/
│   ├── api-endpoints.md          # Polymarket API reference
│   ├── bot-operations.md         # Bot operation guide
│   └── btc-5m-scalping-guide.md  # Strategy deep-dive
│
├── ecosystem.config.cjs          # PM2 process config
└── README.md
```

---

## Signal Filters

Before executing a trade, the bot runs 3 filters:

### 1. Volume Filter (`MIN_VOLUME_RATIO = 0.25`)
Compares average of last 3 **complete** 1m candles vs 10-candle baseline.
> Uses complete candles only — the current (incomplete) candle is always low volume.

### 2. Momentum Filter (`MOMENTUM_THRESHOLD = 0.12%`)
Uses 3-candle slope (avg of last 3 vs avg of 3 before) to detect counter-trend.
Blocks trade if momentum opposes the intended direction.

### 3. EV Filter (`EV_THRESHOLD = 0.0`)
```
EV = (win_probability × $1.00) − (entry_price × 1.02)
```
Skips trade if expected value per share is negative.

### Filter result codes

| Code | Meaning |
|------|---------|
| `SKIP` | Spread < $50 throughout LOCK zone |
| `SKIP_LOW_VOLUME` | Volume ratio < 0.25x after 3 attempts |
| `SKIP_COUNTER_MOMENTUM` | Opposing momentum detected |
| `SKIP_NEGATIVE_EV` | EV calculation negative |
| `WIN` | Trade resolved in correct direction |
| `LOSS` | Trade resolved in wrong direction |
| `PENDING` | Trade open, window not yet resolved |

---

## Risk Management

| Parameter | Default | Description |
|-----------|---------|-------------|
| Position size | 20% bankroll, max $3 | Per trade allocation |
| Daily stop loss | $5 | Bot halts if daily P&L ≤ -$5 |
| Min bankroll | $2 | Bot halts below this |
| Fee rate | 2% | Polymarket taker fee |
| Slippage buffer | +0.02 | Added to entry price in live mode |

### Position Sizing Formula
```python
size = min(bankroll × 0.20, $3.00)
# Capped so cost never exceeds 85% of bankroll
```

---

## Going Live

> ⚠️ **Paper trade for at least 5 days and 100+ trades before going live.**

### Prerequisites
1. Polygon wallet with USDC
2. `POLYMARKET_PRIVATE_KEY` set in `.env`
3. Install live dependencies: `pip install eth-account web3`
4. Test signing: `python3 scripts/poly_test_signing.py`
5. Test proxy (if needed): `python3 scripts/test_proxy.py`

### Enable live mode

Edit `ecosystem.config.cjs`:

```js
args: '-u scripts/poly_btc_5m_lock_50.py --live --bankroll 10 --spread 50 --daily-stop 5',
```

Then reload:
```bash
pm2 reload ecosystem.config.cjs --only polylock-bot-50 --update-env
```

### Geo-restriction

Polymarket blocks certain countries (US, UK). If needed, configure a residential proxy:

```env
# scripts/.env
PROXY_URL=http://username:password@gate.proxies.fo:7777
```

PM2 will automatically inject `HTTP_PROXY` / `HTTPS_PROXY` into all bot processes.

Test connectivity:
```bash
PROXY_URL="http://user:pass@host:port" python3 scripts/test_proxy.py
```

---

## FAQ

**Q: Why Chainlink price instead of Binance?**
Polymarket resolves BTC Up/Down markets using the Chainlink BTC/USD oracle on Polygon.
Using Chainlink as the price source minimizes discrepancy between bot PTB and market PTB.

**Q: Why does spread show $0 sometimes?**
The bot joined mid-window and PTB was reset. Fixed by fetching the 5m candle open price from Binance to get the accurate PTB even after restarts.

**Q: What does 100% win rate mean in paper?**
Paper results can look great in trending markets. Win rate will likely be 70–85% in live conditions across different market states (sideways, choppy, reversal days).

**Q: Can I run two bots simultaneously?**
Yes — `poly_btc_5m_autotrader.py` (A/B $100 threshold) and `poly_btc_5m_lock_50.py` ($50 threshold) can run in parallel for comparison.

**Q: How do I access the server without SSH?**
Open `http://YOUR_IP:3456/terminal/?token=polylock2025` in browser. Full bash terminal runs in the browser — no SSH client needed.

---

## Disclaimer

This software is for **educational purposes only**. Prediction market trading
involves significant financial risk. Past paper trading results do not guarantee
future live performance. Never risk money you cannot afford to lose.

---

*Built with Python 3 · Node.js · PM2 · Chainlink · Polymarket CLOB API*
