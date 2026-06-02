# Polymarket Scalping Journal

Use this template to track every BTC Up/Down 5m trade and calculate real winrates.

---

## Daily Header

```markdown
## 📅 Date: YYYY-MM-DD

**Starting Bankroll:** $____  
**Session Goal:** ___ trades max | Stop loss at $____  
**HTF Bias (M30):** Bullish / Bearish / Neutral  
**Market Conditions:** Trending / Ranging / Volatile (news)  
```

---

## Per-Trade Entry (copy for each trade)

```markdown
### Trade #___
- **Window:** HH:MM UTC (e.g., 14:30)
- **Strategy:** Last-Minute Lock / Ignored Middle / HTF Trend / Gambling
- **HTF Bias before trade:** UP / DOWN / NEUTRAL
- **Spread at minute 3:** $____ (Current – Price to Beat)
- **Direction bet:** UP / DOWN
- **Polymarket fill price:** ____¢
- **Size:** $____
- **Fee paid:** $____ (typically 2% of notional)

**Result:**
- [ ] WIN → Resolved $1.00
- [ ] LOSS → Resolved $0.00
- [ ] Early Exit → Sold at ____¢

**P/L:**
- Gross: $____
- Net (after fees): $____

**Emotion:** [ ] Calm [ ] FOMO [ ] Ragu [ ] Yakin banget
**Notes:** _____________________________________________
```

---

## Daily Summary

```markdown
### 📊 Daily Summary

| Metric | Value |
|--------|-------|
| Total Trades | ___ |
| Wins | ___ |
| Losses | ___ |
| Skipped (evaluated but no-trade) | ___ |
| Winrate | ___% |
| Gross Profit | +$___ |
| Gross Loss | –$___ |
| Net P/L | $___ |
| Fees Paid | $___ |
| Best Trade | Trade #___ (+$___) |
| Worst Trade | Trade #___ (–$___) |
| Avg Win | $___ |
| Avg Loss | $___ |

**Strategy Breakdown:**
- Last-Minute Lock: ___W / ___L (___%)
- Ignored Middle: ___W / ___L (___%)
- HTF Trend: ___W / ___L (___%)

**Lessons:**
1. ________________________________
2. ________________________________

**Tomorrow's adjustment:**
_______________________________
```

---

## CSV Format (for Excel/Google Sheets)

```csv
Date,Window,Strategy,HTF_Bias,Spread_Min3,Direction,Fill_Price,Size,Fee,Result,Resolved_Price,Gross_PL,Net_PL,Emotion,Notes
2026-05-09,14:30,Ignored Middle,UP,87,UP,0.73,10,0.20,WIN,1.00,2.70,2.50,Calm,Clean momentum
2026-05-09,14:35,Last-Minute Lock,UP,150,UP,0.88,20,0.40,WIN,1.00,2.40,2.00,Calm,Locked at min4
2026-05-09,14:40,Ignored Middle,NEUTRAL,-25,DOWN,0.65,10,0.20,LOSS,0.00,-6.50,-6.70,Ragu,Should have skipped
```

### Spreadsheet Formulas

**Winrate:**
```
=COUNTIF(Result_range,"WIN") / (COUNTIF(Result_range,"WIN") + COUNTIF(Result_range,"LOSS"))
```

**Net P/L:**
```
=SUM(Net_PL_range)
```

**Strategy Winrate (example for "Ignored Middle"):**
```
=COUNTIFS(Strategy_range,"Ignored Middle",Result_range,"WIN") / COUNTIFS(Strategy_range,"Ignored Middle",OR_Result_range)
```

---

## Weekly Review Checklist

Every Sunday, review the past week:

- [ ] Which strategy had the highest winrate?
- [ ] Which time of day (UTC) was most profitable?
- [ ] Did HTF bias correctly predict 5m direction? (%) 
- [ ] What's my actual average fill price vs. assumed price?
- [ ] Am I overtrading? (trades/day trend)
- [ ] What's my biggest emotional leak? (FOMO, revenge, boredom)
- [ ] One thing to improve next week: ________________

---

## Monthly Goals Tracker

| Month | Target Winrate | Actual Winrate | Target Net P/L | Actual Net P/L | Trades |
|-------|---------------|----------------|----------------|----------------|--------|
| May | 65% | ___% | $100 | $___ | ___ |
| Jun | 65% | ___% | $100 | $___ | ___ |

---

## Disclaimer

This journal is for tracking and self-analysis only. Prediction markets involve risk of loss. Never trade more than you can afford to lose. The house edge (fees + spread) means you must win significantly more than 50% to be profitable.
