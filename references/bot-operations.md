# Polymarket BTC 5m Bot Operations

Reference for running, monitoring, and troubleshooting the BTC Up/Down 5m autotrader bots.

## Two-Bot Setup

| Bot | Script | Threshold | Entry | Use Case |
|-----|--------|-----------|-------|----------|
| **BOT A** | `poly_btc_5m_autotrader.py` | $100 | ~0.78¢ | Higher confidence, fewer trades |
| **BOT B** | `poly_btc_5m_lock_50.py` | $50 | ~0.65¢ | **User's preferred bot** — more trades, better compounding |

The user primarily runs **BOT B** (`$50` threshold) in PAPER mode.

## Quick Start

```bash
cd /root/.hermes/skills/research/polymarket/scripts

# Start BOT B in persistent screen with logging
bash run_autotrader.sh --bot 50 --screen --token <TOKEN> --chat-id <ID>

# Start BOT A (legacy $100 bot)
bash run_autotrader.sh --bot 100 --screen --token <TOKEN> --chat-id <ID>
```

## Health Checks

```bash
# List running screen sessions
screen -ls

# Check bot processes
ps aux | grep "poly_btc_5m" | grep -v grep

# Tail live logs
tail -f /tmp/polybot50.log      # BOT B
tail -f /tmp/polybot100.log     # BOT A
tail -f /tmp/poly_lock_alert.log # Alert-only bot
```

## Reattach to a Running Bot

```bash
screen -r polybot50     # BOT B
screen -r polybot100    # BOT A

# Detach without killing: Ctrl+A then D
```

## Common Failure: "Stuck" with No Result

**Symptom:** Telegram alert shows `LOCK SETUP` but never shows `WIN` or `LOSS`.

**Root cause:** The bot process died (VPS reboot, session timeout, crash) **after** sending the alert but **before** the 5-minute window closed and resolved.

**Fix:**
1. Check if process is alive: `ps aux | grep poly_btc_5m_lock_50`
2. If dead, restart in screen with logging:
   ```bash
   bash run_autotrader.sh --bot 50 --screen --token <TOKEN> --chat-id <ID>
   ```
3. Verify: `screen -ls` should show `polybot50`

**Prevention:** Always use `--screen` flag; never run bots in a bare SSH session.

## Log File Locations

| Bot | Log File |
|-----|----------|
| BOT B ($50) | `/tmp/polybot50.log` |
| BOT A ($100) | `/tmp/polybot100.log` |
| Alert-only | `/tmp/poly_lock_alert.log` |

If logs are missing, the bot was likely never started with the `--screen` launcher or the log path was overridden.
