# Local measurements on the bot's own data (coordinator notes, 2026-11-09)

Source: SQLite `~/tradingBot-local/data/bot.db`, tables `bars` + `bars_archive`, symbol XAUUSDm, 104,642 M1 bars from 2026-07-21 22:49 to 2026-11-09 06:48 UTC (≈ 3.5 months, 110 trading days). Spread modelled as a constant 0.24 USD (Exness XAUUSDm typical), fills at the bar close, exits checked bar by bar on M1 highs/lows, one position at a time, results in USD per 0.01 lot (1 USD per 1 USD of price). Scripts: scratchpad `mech.py`, `mech2.py`, `mech5.py`, `mech15.py`, `mc15.py`, `session.py`. These are mechanical reproductions of the bot's rules (no Claude discretion). Evidence quality: independent local backtest, in-sample for parameter choices that were tuned on the first 107 live trades (02–06.11), out-of-sample for Jul–Oct bars which the bot never traded.

## 1. Live record (ground truth)
- 107 live XAUUSDm trades 02–06.11.2026: 69 wins (64%), −94.90 USD at mixed lots 0.01–0.04 (−30.7 USD normalised to 0.01 lot). TP exits: 67, avg +1.73 per 0.01; SL exits: 32, avg −4.13 per 0.01. API cost since 03.11: 69 USD.
- Winners' median MFE 1.43 ATR, losers' median MFE 0.36 ATR (only 2 of 38 losers ever reached +1 ATR) → losers are losers almost immediately; break-even/trailing cannot rescue them. Median holding 4 minutes.

## 2. The bot's M1 rule set run mechanically over the whole history
Rules: trend = M5 EMA9−EMA21 gap ≥ 0.4×ATR14 M1; entry when price is between 0.7×ATR below and 0.3×ATR above EMA20 M1 (in trade direction), last M1 bar or last 10 min against the trade, 0.5–2.0×ATR from EMA50 M1, no 3 same-direction bars; no entries 09–16 UTC; ATR14 M1 < 3.5; exits TP = max(2, 1×ATR) / SL = max(4, 2.5×ATR), 30-min time stop.

| variant | trades | win | PnL/0.01 | PF | max DD |
|---|---|---|---|---|---|
| current rules | 1451 | 65% | −254.8 | 0.89 | −300 |
| by month Jul / Aug / Sep / Oct / Nov | 150/403/430/421/47 | 65/68/66/62/68% | −21 / −4 / −65 / −170 / +5 | 0.91/0.99/0.91/0.76/1.07 | |
| no hour block | 2028 | 64% | −525 | 0.85 | |
| SL 1.5× / 2.0× / 3.0× ATR | | 61/63/67% | −312 / −282 / −245 | 0.86/0.88/0.90 | |
| TP 0.75× / 1.25× / 1.5× ATR | | 66/63/59% | −297 / −196 / −193 | 0.87/0.92/0.92 | |
| time stop 15 / 45 / 60 min | | | −282 / −267 / −280 | | |
| M5 gap ≥ 0.2 / 0.6 / 1.0 | | | −319 / −253 / −149 | 0.87/0.88/0.91 | |
| ADX14 M5 ≥ 15 / 20 / 25 / 30 | 1189/935/697/513 | 66–68% | −135 / −94 / −69 / **+19** | 0.93/0.94/0.94/**1.02** | −93 |
| ATR below 50/70/85th pct of 24 h | | | −271 / −255 / −238 | | |
| BUY only / SELL only | 691/760 | 64/66% | −203 / −52 | 0.83/0.95 | |

Breakeven win rate for avg win +2.18 / avg loss −4.58 is 68%; the strategy delivers 62–68% → structurally negative. Hours with the worst mechanical results (no block): 14 UTC −111, 22 UTC −71, 16 UTC −59, 19 UTC −59, 04 UTC −57, 11 UTC −48; best: 03 UTC +45, 05 +38, 07 +38, 18 +21, 00 +12 — but even the best hours are small relative to noise.

## 3. Other M1 families (same history, same spread)
- Pullback entries with other exits: 2a/1a PF 0.84; 2a/1.5a PF 0.91; 3a/1.5a PF 0.97 (−82); 1.5a/1.5a PF 0.91; trailing (tp 4a, sl 1.5a, lock after 1a) PF 0.92; trailing (6a/2a/1.5a) PF 0.96; break-even at +1a PF 0.90. Pullback + ADX14 M5 ≥ 30: PF 0.96 / 0.91 / 0.86 depending on exits.
- Mean reversion (fade 1.5–2.5 ATR from EMA20 M1 with a reversal bar): PF 0.67–0.80 everywhere, every month negative, Asian-hours-only PF 0.68.
- 20-bar breakout (with or without M5 trend, with or without 09–16 block, three exit shapes): PF 0.79–0.92, every month negative.
- M5 pullback in the M15 trend (exits in M5 ATR, 6 exit shapes, ADX/hour filters): PF 0.83–0.91, negative every month except isolated ones.
Conclusion: at a 0.24 USD spread no M1/M5 family tested has an edge on this history; the live result matches the mechanical baseline, so the LLM gate did not add edge on M1.

## 4. M15 pullback in the H1 trend — the only robust positive family
Rules: trend = H1 EMA9−EMA21 gap ≥ 0.4×ATR14 M15; entry at an M15 close between 0.7×ATR15 below and 0.3×ATR15 above EMA20 M15, 0.3–2.5×ATR15 from EMA50 M15, last M15 bar or last 3 bars against the trade; exits TP 1.5×ATR15 / SL 1.5×ATR15, time stop 12 M15 bars (3 h); no hour block.

| variant | trades | win | PnL/0.01 | PF | max DD | months Jul/Aug/Sep/Oct/Nov |
|---|---|---|---|---|---|---|
| baseline tp1.5/sl1.5 ts12 | 297 (2.7/day) | 54% | **+261.5** | 1.19 | −110 | +41/+84/+118/+8/+10 |
| tp1.0/sl1.5 | 341 | 62% | +257 | 1.19 | −98 | +56/+22/+120/+56/+3 |
| tp2.0/sl1.5 | 273 | 49% | +277 | 1.20 | −134 | +49/+128/+133/−28/−5 |
| tp1.0/sl1.0 | 397 | 51% | +106 | 1.07 | −95 | |
| tp1.5/sl2.0 | 289 | 56% | +69 | 1.04 | −175 | |
| gap ≥ 0.2 / 0.6 / 1.0 | 318/265/216 | 54–55% | +302 / +279 / +197 | 1.20/1.23/1.20 | | |
| time stop 6 / 8 / 16 / 24 bars | | | +158 / +172 / +261 / +198 | 1.11–1.18 | | |
| geometry d50 ≥ 0.5 & stretch ≤ 2.0 | 250 | 56% | +371 | 1.33 | −112 | +33/+120/+140/+31/+47 |
| block 12–16 UTC | 239 | 51% | +64 | 1.06 | −158 | — blocking HURTS on M15 |
| block 09–16 UTC | 209 | 51% | +57 | 1.06 | | |
| ADX14 H1 ≥ 20 / 25 | 191/156 | 53/57% | +100 / +139 | 1.10/1.18 | | |
| trend from M15 itself / from H4 | 189/189 | 50/51% | +124 / −84 | 1.13/0.92 | | |
| spread 0.40 instead of 0.24 | 299 | 52% | +171 | 1.12 | −140 | +15/+53/+99/−4/+9 |
| M1-timed entry inside the M15 setup | 282 | 52% | +149 | 1.10 | −132 | |
| M30 pullback (H2 trend) / H1 pullback (H4 trend) | 131/60 | 50/48% | −63 / +33 | 0.94/1.05 | | |

Per-trade numbers (baseline): mean +0.87, median win +10.4, median loss −10.3 USD per 0.01 lot; max 7 consecutive losses. By entry hour the 12–15 UTC window is the most profitable on M15 (+22, +86, +54, +56) while 18 UTC (−78) and 21 UTC (−49) are the worst — the opposite of the M1 hour pattern.

Monte Carlo (5000 runs of 300 bootstrapped trades ≈ 3.5 months, 0.01 lot, ruin = balance below the margin for the next trade):

| start balance | P(ruin) 0.01 lot | P(50% drawdown) | P(ruin) 0.02 lot |
|---|---|---|---|
| 105 USD | 27.6% | 64.8% | 58.8% |
| 200 USD | 6.1% | 34.4% | 30.0% |
| 300 USD | 1.2% | 17.2% | 14.7% |
| 500 USD | 0.1% | 3.6% | 2.6% |
| 1000 USD | 0.0% | 0.0% | 0.0% |

## 5. Session strategies on the same history
- Asian range → London breakout (OCO at range end, SL = range capped at 0.6% of price, TP 2.5R, break-even at 1.5R, flat 21:00 UTC, skip range < 0.30% or > 1.60%): range 00–05 UTC PF 1.69 (+526, 70 trades, 47% wins, avg loss −21, max DD −126); 01–06 UTC PF 1.27 (+249, DD −240); 02–07 PF 1.10; 03–08 PF 0.97. On 01–06: no range filter PF 1.40, flat 16:00 PF 1.36, TP 1.5R PF 1.00, break-even off PF 1.24. Window choice matters a lot (selection risk); avg loss ≈ 20 USD per 0.01 lot → needs ≥ 500 USD.
- Previous-day high/low fade (reversal M1 bar at the level, TP 0.3 × previous day range, SL 0.15 × range, 07–20 UTC): 46 trades, 48% wins, +157, PF 1.49, DD −110, avg loss −13; TP 0.2/SL 0.1 PF 1.49; TP 0.5/SL 0.25 PF 0.89; all hours PF 1.18.

## 6. Cost facts from our own log
- Claude API: 69 USD over 03–06.11 (≈ 17 USD/day at the old cadence, 5–8 USD/day after the 06.11 throttles) against a 105 USD balance. At M15 decision cadence (≤ 96 calls/day, realistically 10–20 when pre-filtered) the cost would be ≈ 1–2 USD/day on Sonnet.
- Lot: owner traded 0.03–0.04 on 105–143 USD → 9–17% of the account per stop; the same trades at 0.01 would have lost 15.6 USD instead of 54.25 on 06.11.
