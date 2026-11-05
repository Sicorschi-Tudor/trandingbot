# Documented intraday behaviour of gold (XAUUSD) relevant to M1/M5 scalping

Scope: sessions and hours, volatility regimes, scheduled US news, spread/commission costs, and what a +2 USD / −4 USD scalp (price distance; 0.01 lot = 1 oz so 2 and 4 USD of P&L) implies. All prices are USD per troy ounce of gold price; the year/period of each number is stated. "Measured" = a dataset with sample size or period is given; "folklore" = trader/vendor claim with no data behind it.

Unit convention used by most gold-trading sources: 1 point = $0.01, 1 pip = $0.10, so "100 pips" = $10.00 of gold price. (Source: [FXNX NFP guide](https://fxnx.com/en/blog/how-far-gold-moves-nfp-release-release-pip-log)). Some broker review sites use a different pip definition; conversions below are flagged where that matters.

Research budget note: ~21 tool calls; Myfxbook and SSRN pages returned HTTP 403 and could not be read.

---

## Key question 1 — Typical 1-minute and 5-minute ATR of gold by session/hour (USD, 2024–2026), and the change on news days

### Takeaway
No source publishes a measured M1/M5 ATR table for gold; what exists are measured **hourly** range profiles (two independent datasets, 2010–2026 and a recent ~108-day sample at ~$4,000 gold) plus one vendor claim of an M1 ATR of $1.50–$3.00 in active hours. Scaling the measured hourly ranges by √time gives M1 ranges of roughly $1.3 (Asia) → $3 (13:00 UTC) → $0.4 (21:00 UTC rollover) at late-2026 price levels, and M5 ranges roughly 3.5× those; the 13:00 UTC hour moves about 3× the quietest hour, and news days skew hourly means up by roughly 18–28% (vendor estimate) with single 1-minute NFP candles of $8–$22.

### Cited Findings

**Measured hourly profile A — GoldHunts, 2,599 hourly bars (~108 days per hour, recent sample at ~$4,000 gold, Twelve Data feed, timestamps corrected to true UTC)** — [GoldHunts XAUUSD Volatility Study](https://goldhunts.com/gold-research/xauusd-volatility-study)
- Average high–low range per UTC hour (avg / median, USD): 00:00 17.31/15.64; 01:00 20.59/20.60; 02:00 14.93/15.08; 03:00 11.30/11.26; 04:00 10.29/9.80; 05:00 15.28/14.20; 06:00 14.12/14.64; 07:00 12.36/12.66; 08:00 13.55/12.99; 09:00 11.44/10.97; 10:00 11.86/11.06; 11:00 14.14/13.37; 12:00 21.91/19.57; **13:00 24.09/23.22 (highest)**; 14:00 21.61/20.16; 15:00 17.16/16.85; 16:00 13.28/12.52; 17:00 12.99/11.88; 18:00 13.02/10.87; 19:00 12.49/11.33; 20:00 8.69/7.16; **21:00 2.80/1.82 (lowest, rollover)**; 22:00 15.34/10.55; 23:00 12.07/10.74.
- Maximum hourly ranges in the sample: 13:00 UTC $103.00; 18:00 UTC $110.98; 22:00 UTC $103.98 (the 22:00 hour, right after rollover, has a low median but a very fat tail).
- Daily range: $29.43 average over 3,178 daily bars since Jan 2015 vs **$90.28 average over the trailing 250 sessions** (≈3.1×) — dollar volatility has scaled with the price level, so any dollar-denominated stop/target must be re-derived from current data.
- Session share of daily range (108 days): Asia 45.0% (avg $40.72), London 43.4% ($40.45), New York 52.6% ($49.74), rollover + late NY 31.0% ($20.67); windows overlap so shares do not sum to 100%. The page does not give its session boundaries explicitly.

**Measured hourly profile B — EVTradeLabs, Dukascopy ticks → M1, 5 May 2003–12 Oct 2026, 7,959,799 M1 candles; hourly/session stats on 2010–2026** — [EVTradeLabs XAUUSD seasonality](https://evtradelabs.com/blog/xauusd-seasonality-day-month-session)
- Mean hourly range (% of price, with the article's USD equivalent at the period-average price): 13:00 UTC 0.479% ($9.1), 14:00 0.472% ($9.0), 12:00 0.414% ($7.8), 15:00 0.400% ($7.7), 16:00 0.311% ($6.0), 01:00 0.297% ($6.1); quietest 21:00 UTC 0.155%. Busiest hour moves 3.1× the quietest; the article states the hourly pattern is stable year to year.
- Mean session range (% of price, 2010–2026): Asia 00–07 UTC 0.618%; London 07–12 0.598%; New York 12–17 **0.977%**; NY late 17–22 0.510%; Rollover 22–24 0.231%. NY range ≈ 4.2× rollover range.
- Session drift (mean return): Asia +0.0190% (p=0.007), London −0.0120% (p=0.067), NY −0.0033% (p=0.747), NY late +0.0053% (p=0.354), Rollover +0.0202% (p<0.001). The authors call these drifts "small relative to spread and costs".
- Friday is the only weekday with a Bonferroni-surviving positive mean daily return (+0.138%, win rate 57.1%, p<0.001, holds in both halves 2006–2016 and 2016–2026).
- Median measured spread since 2003: $0.37. Median spread as share of mean hourly range: 13:00 UTC 4.7%, 14:00 4.7%, 15:00 5.5%, 04:00 13.3%, 22:00 13.2%, **21:00 UTC 19.8%**.

**Semi-measured hourly profile C — FXNX, "12 consecutive months of raw tick data", no dates, no sample size, table values missing from the page text** — [FXNX hourly ATR table](https://fxnx.com/en/blog/xauusd-hourly-atr-table-12-months-tick-data)
- Median hourly true range: Asia 00–07 UTC $3.00–$5.50/h (03:00–04:00 $3.10; 05:00 $2.90); London open + NY overlap 08–16 UTC $7–$11/h rising to $14–$25+ in 12:30–16:00 UTC (13:30 UTC median $21.80); after-hours 17–23 UTC $4–$6/h; 20:00 UTC (FOMC hour) median $8.20, mean $14.90.
- Day-of-week ATR at 13:30 UTC: Mon $15.20, Tue $17.80, Wed $19.90, Thu $24.40, Fri $26.80.
- Claims the mean hourly ATR is skewed upward 18–28% by news tail events (NFP, CPI, FOMC) — no separate news vs non-news table. The page is internally inconsistent (e.g., overlap peak quoted as $14–22+, $18.5–22+ and $14–25+ in different places), so treat as indicative only.
- Assumed raw spread $0.31; spread/ATR "efficiency" 10.7% at 05:00 UTC vs 1.4% at 13:30 UTC.

**Session ATR ranges from a second FXNX article (no dataset stated, Aug 2026)** — [FXNX trading hours & cost windows](https://fxnx.com/en/blog/xauusd-trading-hours-session-range-spread-cost-windows)
- Asian 00:00–07/08:00 GMT ATR $8–$14 (range-bound); London morning 07/08:00–12:30 GMT $18–$28 (trend initiation); London/NY overlap 12:30–16:00/16:30 GMT $30–$55+ (max volatility); late NY after 19:00 $6–$10; 21:00–22:00 "zero trading zone". These appear to be session-level (multi-hour) ranges and conflict in scale with profile C from the same site.

**Direct M1 ATR claims (vendor, unverified)**
- "Gold's 1-minute ATR routinely runs $1.50–$3.00 during active sessions, against a typical spread of $0.20–$0.35" — [ForTraders](https://fortraders.com/blog/scalp-trading-ai) (2026, no data shown).
- M1 ATR stop sizing guidance of 0.8–1.2 × ATR(14) on M1; skip pullback candles > 1.5 × M1 ATR — [AppTrading.ai](https://apptrading.ai/en/blog/xauusd-scalping-strategy-for-1-and-5-minute-charts/) (no data).
- Single 1-minute NFP candle "can carve out a $22 range" — [FXNX 15-minute rule](https://fxnx.com/en/blog/mastering-xauusd-news-15-minute-rule-cpi-nfp); initial 1-minute NFP spike 80–180 pips ($8–$18) — [FXNX NFP guide](https://fxnx.com/en/blog/how-far-gold-moves-nfp-release-release-pip-log).

### Inferences
- **Estimated M1/M5 ranges at ~$4,000 gold (late 2026), derived from GoldHunts hourly averages by √time scaling** (a Brownian-motion range scales with √T; M1 ≈ hourly/√60 ≈ hourly/7.7; M5 ≈ hourly/√12 ≈ hourly/3.5). This is an approximation, not a measurement:
  - Asia 00–07 UTC (hourly avg ≈ $10–$20): M1 ≈ $1.3–$2.7, M5 ≈ $3–$6. Quietest Asia hours 03–04 UTC: M1 ≈ $1.3–$1.5, M5 ≈ $3.
  - London 07–12 UTC (hourly avg ≈ $11–$14): M1 ≈ $1.5–$1.8, M5 ≈ $3.3–$4.1. Note: the 09–11 UTC hours are the quietest of the European session (≈ $11.5/h) — less than Asia's 01:00 hour.
  - Overlap 12–16 UTC (hourly avg ≈ $17–$24): M1 ≈ $2.2–$3.1, M5 ≈ $5–$7.
  - NY afternoon 16–21 UTC (hourly avg ≈ $9–$13): M1 ≈ $1.1–$1.7, M5 ≈ $2.5–$3.8; 20:00 UTC ≈ M1 $1.1.
  - Rollover 21:00 UTC (hourly avg $2.80, median $1.82): M1 ≈ $0.25–$0.36, M5 ≈ $0.5–$0.8.
  - These derived values agree with the ForTraders claim of M1 ATR $1.50–$3.00 in active hours, which is the only direct M1 figure found.
- The two independent measured profiles (GoldHunts recent sample; EVTradeLabs 2010–2026 in %) agree on shape: peak at 13:00–14:00 UTC, trough at 21:00 UTC, secondary bump at 01:00 UTC (Asia open / Shanghai). EVTradeLabs' 0.479% at 13:00 UTC × $4,000 = $19.2, consistent with GoldHunts' $24 recent average (2026 was a higher-vol year).
- A **+2 USD target is ~0.65–1.5 × one M1 range** in active hours and ~5–8 × an M1 range in the rollover hour; the **−4 USD stop is ~1.3–3 × M1 range** in active hours. On M1 both barriers sit inside the noise of a single 5-minute bar during the overlap ($5–$7 M5 range), so entries there are effectively coin-flips on direction unless the entry has real edge; in Asia/late NY the stop sits ~1–1.5 M5 ranges away.
- "News days vs normal days": no source quantifies a news/non-news ATR split; the best available is FXNX's 18–28% mean skew and the day-of-week table (Thu/Fri 13:30 UTC ATR 60–76% above Monday), plus the 1-minute NFP spike of $8–$22, i.e., one NFP minute ≈ 3–7 normal overlap minutes.

### Gaps
- No published, dated M1 or M5 ATR table for XAUUSD by hour exists in the sources found; the values above are derived. Recomputing from the project's own M1 data (Dukascopy/Twelve Data) is the only way to get sample-sized numbers.
- Myfxbook's XAUUSD volatility page (hourly, in pips) returned HTTP 403 and could not be read.
- GoldHunts does not give the date range of its 108-day sample (only "a few months", at ~$4,000 gold, i.e., 2026).
- No source separates news-day from non-news-day hourly ranges with a sample size.

---

## Key question 2 — Which hours are documented as mean-reverting vs trending; the 09–12 UTC window, NY afternoon, rollover/thin period; session-open and fix behaviours

### Takeaway
There is **no rigorous study** classifying gold's hours as mean-reverting vs trending; the only measured evidence (an SSRN paper on session autocorrelation) points to positive lag-1 autocorrelation in Asia, i.e., momentum rather than reversion, contradicting the widespread trader claim that Asia "ranges". Measured facts are: 09–11 UTC are the quietest European hours (~$11/h in 2026), the 21:00 UTC rollover hour has ~1/8 the range of 13:00 UTC with spreads at ~20% of hourly range, and the London PM fix (15:00 London) showed abnormal volume/volatility and downward bias in 2001–2013 data, which is contested and predates the 2015 electronic auction.

### Cited Findings

**Measured / academic**
- Caminschi & Heaney (2014, Journal of Futures Markets 34(11):1003–1039, "Fixing a Leaky Fixing"): trading volume and volatility in GC futures and GLD rose during the PM fix window before the result was published; volume surged 47.8% above average in the 20-minute period preceding the fix start; orders placed during the fix were most accurate on days the price moved most afterwards. — [RePEc entry](https://econpapers.repec.org/RePEc:wly:jfutmk:v:34:y:2014:i:11:p:1003-1039); [UWA summary](https://www.news.uwa.edu.au/201410067021/research/uwa-research-finds-gold-and-silver-price-setting-open-exploitation).
- Abrantes-Metz & Metz (NYU, 2001–2013 intraday data): on days with large moves during the 3 pm London fix, moves were downward at least two-thirds of the time in six different years 2004–2013; contested by Peter Fertig (LBMA Alchemist) who argues ordinary explanations (equity reversals, USD, oil). — [LBMA Alchemist issue 73](https://www.lbma.org.uk/alchemist/issue-73/has-there-been-a-decade-of-london-pm-gold-fixing-manipulation).
- Since 2014/2015 the fix is an electronic LBMA auction at 10:30 and 15:00 London time; spot trading does not pause around it. — [Traders Mastermind](https://tradersmastermind.com/gold-trading-hours-comex-london-fix/); [Forexr](https://forexr.com/learn/lbma-gold-price-and-london-fix).
- Copenhagen Business School thesis (2019): "hat-shaped" intraday pattern in gold linked to Eastern demand and London fix effects, with downward moves around fixing times; does not test autocorrelation. — [CBS thesis PDF](https://research.cbs.dk/files/59803241/651553_Thesis_Contract_13383.pdf).
- Wenling Wei (SSRN working paper): gold's lag-1 return autocorrelation is **positive in the Asian session** (short-horizon continuation, not reversion); abstract only, full text returned 403. — [SSRN 7257240](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=7257240).
- Watkins (2017 conference slides): gold's informational efficiency varies by session, with the London session showing weaker return correlations. — [Watkins slides](https://www.clintonwatkins.com/talks/2017-abfc/intraday_seasonality_20171129.pdf).
- EVTradeLabs (2010–2026): session drifts are statistically tiny (Asia +0.019%/session, London −0.012%, Rollover +0.020%) — no evidence of a tradable directional bias per session. Most volatile hours 12–15 UTC; quietest 21:00 UTC. — [EVTradeLabs](https://evtradelabs.com/blog/xauusd-seasonality-day-month-session)
- GoldHunts (2026 sample): 09:00 UTC avg range $11.44, 10:00 $11.86, 11:00 $14.14 vs 13:00 $24.09; 20:00 $8.69; 21:00 $2.80; 22:00 median $10.55 but max $103.98. — [GoldHunts](https://goldhunts.com/gold-research/xauusd-volatility-study)

**Trader/vendor claims (no data = folklore)**
- GoldHunts strategy page: gold "reverts most reliably in the Asian range (23:00–07:00 UTC)" and in the "London midday drift (10:00–12:15 UTC)"; reversion "fails" at the 07:00–09:00 UTC London open and the 13:30 UTC data window; 15:30–19:00 UTC NY afternoon "good" for reversion; exclude 21:00–23:00 UTC rollover and 18:45–20:30 UTC on FOMC days; fades of 1.5–2 × hourly ATR "fade reliably" in Asia. **No hit rates, samples or periods given; the three linked models report "No closed gold trades recorded".** — [GoldHunts mean reversion sessions](https://goldhunts.com/gold-strategies/gold-mean-reversion/best-sessions)
- FXNX: Asia = range containment, "breakouts often fail"; London morning = trend initiation; overlap = momentum/trend continuation; 21:00–22:00 GMT = "zero trading zone"; CME maintenance 21:00–22:00 (22:00–23:00 in DST); US macro releases typically 12:30–18:00 GMT. — [FXNX trading hours](https://fxnx.com/en/blog/xauusd-trading-hours-session-range-spread-cost-windows)
- "Pre-fix volatility build-up" with mean reversion after 11:00 EST — unsourced Substack post. — [William GWA](https://william1234567.substack.com/p/how-the-daily-pre-fix-volatility)
- Asian session "10–30 pips typical range" ($1–$3) — EA vendor page, inconsistent with the measured $10–$20/h Asia ranges in 2026; likely a stale or mis-unit figure. — [Golden Viper EA](https://www.goldenviperea.com/blog/gold-trading/gold-trading-sessions/)
- Broker/education guides agree Asia = quieter/range-friendly, London open = liquidity jump, overlap = fastest tape, but give no measurements. — [QuantVPS](https://www.quantvps.com/blog/when-to-trade-gold); [NordFX](https://nordfx.com/traders-guide/best-time-to-trade-gold-xauusd-sessions-volatility-news)

### Inferences
- The "Asia ranges / London trends" taxonomy is folklore: the one academic autocorrelation result found (Wei) shows **positive** short-horizon autocorrelation in Asia. The safest measured statement is only about *magnitude*: 09–11 UTC and 17–21 UTC are low-range, 12–15 UTC is high-range, 21:00 UTC is dead.
- For a 2/4 scalp, the 09–12 UTC window is attractive on cost grounds (spread is a small share of hourly range, ~5–7% by EVTradeLabs' ratio, vs 20% at 21:00 UTC) but the M1 range there (~$1.5–1.8 estimated) means the +2 target is more than one M1 range away and takes several minutes to reach, raising exposure to the 12:30 UTC data releases.
- The 21:00 UTC hour is unusable for a $2 target: median hourly range $1.82 is smaller than the target, and spread ≈ 20% of hourly range. 22:00 UTC is a trap: modest median but the fattest tail in the sample (max $104) — the new-day reopen gaps.
- London fix behaviour: the documented effect was on volume/volatility inside the fix window (now 10:30/15:00 London = 10:30/15:00 UTC in winter, 09:30/14:00 UTC in summer) and a downward bias in old data; nothing measured since the 2015 auction. Note the 15:00 London PM fix coincides with/just follows the 13:30 UTC US data and sits inside the overlap's peak-range hour, so it is not separable from the macro flow in current data.

### Gaps
- No post-2015 study of price behaviour around the LBMA auction times was found.
- COMEX open (13:20 UTC)/close (18:30 UTC) behaviour: no measured study found; the 13:00–14:00 UTC peak is attributed by all sources to US data at 13:30 UTC rather than the COMEX pit open, and GoldHunts' 18:00 UTC hour shows the sample's single largest hourly range ($110.98) but no explanation.
- The Wei SSRN paper's sample period and values could not be read (403).

---

## Key question 3 — Trading gold around US data releases on 1–5 minute charts: move sizes, spread widening, slippage, how long to stay out

### Takeaway
Vendor/education sources converge on: NFP/CPI/FOMC move gold $6–$35 within the first 15–60 minutes (1-minute NFP spike $8–$18, outliers $22+), spreads widen from ~$0.02–$0.35 to **$1–$5 for 10–180 seconds** and normalise within 3–5 minutes, market-order slippage can exceed $1.50, and 40–60% of initial spikes reverse within 5–15 minutes; none of these figures carry a sample size, so they are consistent folklore rather than measured statistics.

### Cited Findings
- **FXNX NFP guide** (2024–2026 prices, no release log — its examples are hypothetical): 1-minute spike 80–180 pips ($8–$18); 60-minute range typically 150–350 pips ($15–$35), outliers >450 pips ($45); minor surprises 120–180 pips; ~40% of releases show a significant fade; normal spread ~0.2 pips ($0.02) widening to 10–40 pips ($1–$4) at 13:30:00 GMT, normalising within 3–5 minutes; avoid market orders and tight pending stops 13:29:58–13:31:00 GMT; avoid execution in first 180 s; straddles trigger both sides and lose ~25–50 pips ($2.50–$5) of friction. — [FXNX NFP](https://fxnx.com/en/blog/how-far-gold-moves-nfp-release-release-pip-log)
- **FXNX 15-minute rule**: gold typically travels 150–350 pips ($15–$35) in the first 60 minutes of CPI/NFP; a single 1-minute candle can have a $22 range; spread-widening and noise subside in ~15 minutes. — [FXNX 15-min rule](https://fxnx.com/en/blog/mastering-xauusd-news-15-minute-rule-cpi-nfp)
- **Pro-Scalper** (pips; no sample sizes; "historical data" measured pre-news to 60-min high/low): NFP 80–200 pips ($8–$20), low-surprise 40–80, opening spike 50–100; CPI 60–150 ($6–$15); FOMC decision 100–300 ($10–$30); FOMC minutes 30–80; Fed chair speech 40–100; PPI 30–70; GDP 30–80; retail sales 20–50. Spread 0.2–0.5 pips normally → 30–50 pips ($3–$5) within 10–30 s of release, wider on FOMC; market-order slippage can exceed 15 pips ($1.50); pending orders fill 5–15 pips ($0.50–$1.50) beyond trigger. Initial spike direction wrong >40% of the time against the surprise; reversals recover 50–100% of the spike within 5–15 minutes; final direction resolves in 5–30 minutes. Recommended: pause EAs without news filters 15–30 min before NFP/CPI/FOMC; avoid first 60 s; wait 2–5 min for the spike to resolve; close NFP trades within 60 min. ADP, ISM and jobless claims are **not** covered. — [Pro-Scalper news trading](https://www.pro-scalper.com/xauusd-strategies/news-trading-gold)
- **Pro-Scalper NFP page**: gold can move 80–100 pips ($8–$10) within minutes after a major beat. — [Pro-Scalper NFP](https://www.pro-scalper.com/gold-market/nfp-effect-on-gold)
- **FXNX trading hours**: normal spread 15–20 cents; during major US news "from 15 cents to over $1.50, briefly"; CME maintenance blackout spread $2.00–$5.00; rollover example $2.50 wide (ask 2,350.25 / bid 2,347.75, i.e., 2024 price level). — [FXNX trading hours](https://fxnx.com/en/blog/xauusd-trading-hours-session-range-spread-cost-windows)
- **FXNX hourly ATR**: 20:00 UTC (FOMC hour) median hourly ATR $8.20 vs mean $14.90 — FOMC days roughly double the hour's typical range (8 days/year). — [FXNX hourly ATR](https://fxnx.com/en/blog/xauusd-hourly-atr-table-12-months-tick-data)
- **Vantage Markets** academy pages describe the same spike-then-reassess pattern without figures. — [Vantage XAUUSD news trading](https://www.vantagemarkets.com/en-za/academy/xauusd-news-trading/)
- **GoldHunts strategy page** (unsourced): $25–$60 moves on data releases; spreads widen to $0.50+ around rollover and data. — [GoldHunts](https://goldhunts.com/gold-strategies/gold-mean-reversion/best-sessions)
- GoldHunts measured maxima at the 13:00 UTC hour ($103 range) and 18:00 UTC hour ($111 range) in the 2026 sample give the realistic upper bound of a data/FOMC hour at $4,000 gold. — [GoldHunts](https://goldhunts.com/gold-research/xauusd-volatility-study)

### Inferences
- For a 2/4 scalp the relevant number is not the 60-minute move but the **first-minute spread**: $1–$5 of spread equals 50–250% of the +2 target, so any position open at 13:30:00 UTC on NFP/CPI effectively has its stop widened by the spread and its target made unreachable for 1–5 minutes. A −4 stop is smaller than a typical 1-minute NFP candle ($8–$18), so an open position at the print is a near-certain full loss or worse (slippage > $1.50 reported).
- The common vendor advice (flat 15–30 min before, re-enter 3–15 min after once spread is back to normal) is consistent across sources; the quantitative justification is that spreads normalise in 3–5 minutes and 40–60% of spikes retrace within 5–15 minutes. A rule of "no new entries from T−15 min, and from T+0 until spread ≤ 2× normal" is the sourced version.
- Lesser releases (ADP, ISM, jobless claims) are not quantified anywhere found; by the pro-scalper table they sit in the 20–80 pip ($2–$8) class with retail sales/GDP, i.e., one such release is 1–4 targets of movement — still larger than the stop.

### Gaps
- No source gives a measured, dated release-by-release log of 1-, 5- or 15-minute ranges with a sample size; all ranges are vendor summaries.
- ADP, ISM and jobless-claims impacts on gold were not found quantified.
- No broker spread log through a release was found (IC Markets' guide explicitly declines to quote one).

---

## Key question 4 — Spread and commission per 0.01 lot on common MT5 brokers, and share of a 2 USD target

### Takeaway
On raw/ECN accounts (IC Markets Raw, Pepperstone Razor, Exness Raw) the commission is ~$3.50 per side per lot = **$0.07 round trip per 0.01 lot**, and typical quiet-hours gold spreads are reported at $0.04–$0.20, so an all-in cost of **≈$0.11–$0.27 per 0.01 lot = 5–14% of a $2 target** (before slippage); standard/no-commission accounts run $0.16–$0.35 spread (8–18% of target); rollover and news spreads of $1–$5 are 50–250% of the target. All spread figures come from review/affiliate sites, not broker pages, and conflict with each other.

### Cited Findings
- IC Markets MetaTrader Raw: $3.50 per side per lot ($7 round turn) on metals; cTrader Raw $3 per side per $100,000 notional (≈$12.38/side at $4,125 gold — article says verify); Standard: no commission. Illustrative per-0.01-lot (1 oz) round trip: Raw spread $0.10 + commission $0.07 = **$0.17** ($0.32 with $0.15 slippage); Standard spread $0.22 = $0.22 ($0.37 with slippage). The article refuses to quote a measured spread ("variable"); snapshot 29 Sep 2026 at ~$4,125/oz. — [ForexTradeLab IC Markets gold](https://forextradelab.com/blog/ic-markets-gold-spread-xauusd-guide-2026/)
- IC Markets Raw average XAU/USD spread $0.05–$0.10 plus $3.50/lot/side — [GoldSniper IC Markets](https://www.goldsniper.io/brokers/ic-markets-gold-trading); 30-day test review — [LowSpreadBroker](https://lowspreadbroker.com/artigos/ic-markets-spreads-review); fees page — [CompareForexBrokers](https://www.compareforexbrokers.com/reviews/ic-markets-review/spreads-fees/)
- Pepperstone Razor: raw spreads from 0.0 plus commission; one test reports ~10 cents average gold spread on Razor and ~7 cents on Exness Raw (test method not shown). — [TradingSkillLab comparison](https://tradingskilllab.com/comparison/low-spread-brokers-compared/)
- Exness: Raw Spread commission $3.50 per lot per side ($7 round trip); Zero account $5.50 per side ($11 round trip); Raw Spread average gold spread reported variously as $0.037 (+$3.50/side), "0.0–15 points typical" ($0.00–$0.15), "0.0 to 5.0 pips" ($0–$0.50); Standard account average $0.16 with no commission (another site says 2.45 pips 24-h average ≈ $0.245; another says 20–35 pips ≈ $2–$3.50, which is implausible for normal hours and likely a unit error). — [fxfinds Exness](https://fxfinds.com/exness-spread-fees/); [TradersUnion Exness XAUUSD](https://tradersunion.com/brokers/forex/view/exness/xauusd-spread/); [fxrecap Exness gold](https://fxrecap.com/top-broker/exness-review/trade-gold); [GoldPriceActionTrading Exness spread](https://goldpriceactiontrading.com/2026/05/exness-xauusd-average-spread-2026.html)
- EVTradeLabs measured median Dukascopy spread since 2003: $0.37 (older data at lower prices inflates this relative to 2026 raw accounts). — [EVTradeLabs](https://evtradelabs.com/blog/xauusd-seasonality-day-month-session)
- FXNX illustrative average spreads: Asia $0.35, overlap $0.15; slippage buffer Asia $0.15, overlap $0.05; total friction Asia $0.60 vs overlap $0.30 per oz; "$0.60 against a $3 target is 20% of gross profit". — [FXNX trading hours](https://fxnx.com/en/blog/xauusd-trading-hours-session-range-spread-cost-windows)
- FXNX hourly ATR article assumes raw spread $0.31 (3.1 pips). — [FXNX hourly ATR](https://fxnx.com/en/blog/xauusd-hourly-atr-table-12-months-tick-data)
- ForTraders: typical spread $0.20–$0.35 "at a reputable feed". — [ForTraders](https://fortraders.com/blog/scalp-trading-ai)
- A review of a gold EA attributes its live-vs-backtest gap to "real gold spreads 10–30× higher, commissions add up, and execution slippage". — [ForexCracked Gold Strike review](https://www.forexcracked.com/forex-ea/forexcracked-gold-strike-ea-free-download-xauusd-breakout-scalper-review/)
- Broker spread league tables (no measurement method): [FXLeaders](https://www.fxleaders.com/forex-brokers/forex-brokers-by-spreads/); [ChiefIdea lowest XAUUSD spread](https://www.chiefidea.com/forex-brokers/latest/forex-broker-with-the-lowest-xauusd-spread/)

### Inferences
- Cost per 0.01 lot (1 oz) round trip, best to worst, 2026: Exness/IC/Pepperstone raw in the overlap ≈ $0.04–$0.10 spread + $0.07 commission = **$0.11–$0.17 (5.5–8.5% of a $2 target)**; raw in Asia/late NY ≈ $0.15–$0.35 + $0.07 = $0.22–$0.42 (11–21%); standard accounts $0.16–$0.35 (8–18%); adding the FXNX slippage buffers ($0.05 overlap / $0.15 Asia) gives $0.16–$0.57 (8–29%). Rollover/news: $1–$5 (50–250%).
- Because the stop is 2× the target, the cost is also 2.5–7% of the stop; the asymmetry means costs hurt the win side proportionally more.
- The project's own bridge should log the actual MT5 spread per fill: every source refuses or fails to provide a measured series, and the Exness figures alone span a 10× range.

### Gaps
- No broker-published, time-stamped XAUUSD spread series for 2025–2026 was found; all figures are reviewer snapshots or illustrations.
- Pepperstone's gold commission on Razor was not confirmed from a primary page (sources only say "identical" to IC/Exness, i.e., $3.50/side/lot).
- No source measured the spread inside the 21:00–23:00 UTC window on these specific brokers; only FXNX's generic $2–$5 blackout figure exists.

---

## Key question 5 — Sustainable win rates for TP < SL on gold (e.g., +2/−4), break-even including costs, and evidence on time stops

### Takeaway
No credible live track record for a small-TP/large-SL gold scalper was found; the arithmetic is unambiguous: a +2/−4 scalp breaks even at **66.7% win rate before costs** (which is exactly the hit probability of a drift-free random walk, so random entries have zero edge) and at **≈69–73% after realistic raw-account costs of $0.17–$0.35**, with each 1 point of win rate worth only ≈$0.06 per trade per 0.01 lot. The one documented backtest-vs-live comparison for a gold scalping EA fell from 83.8% win / PF 6.75 (backtest) to PF 1.66 with 21% drawdown live. No evidence on time stops for scalps exists in the sources found.

### Cited Findings
- Break-even formula including costs (fixed TP/SL, equal cost on wins and losses): win rate = (stop + round-trip cost) / (target + stop). Example: 0.4% target / 0.3% stop goes from 43% break-even with no fees to 71% with 0.20% round-trip fees; a 0.30% target with 0.10% round-trip cost needs 66.7%. — [ChartMini scalping guide](https://chartmini.com/blog/scalping-strategies-guide); [BitDelta scalping guide](https://bitdelta.pro/blog/scalping-strategy-guide/)
- Pre-cost baselines: 1:1 needs 50%, 1:2 (reward:risk) ~33%, 1:3 25%, 1:4 20%; at 1:1 after costs ~59% in forex. — [NIFM spread math](https://www.nifmacademy.com/blog/post/333/forex-trading/forex-scalping-strategy-the-spread-math-that-decides-it); [Backtrex expectancy](https://backtrex.com/en/blog/risk-reward-ratio-trading-expectancy-relationship)
- A 0.1-pip spread on a 10-pip stop is 0.01R per trade and "can erode a +0.05R expectancy to break-even" in high-frequency scalping. — [ChartMini R-multiples](https://chartmini.com/blog/risk-reward-ratio-explained)
- Moving the stop to break-even on a 5-pip scalp "gives up 14% of a risk unit every single time" (cost of early break-even stops on tight scalps). — [ProPlynq break-even](https://proplynq.com/blog/moving-stop-to-breakeven)
- "Most scalpers operate with 1:1 or even negative risk-reward ratios, relying on high win rates to overcome the mathematical disadvantage"; "even small spread differences significantly impact results with tight stops" (Happy Gold EA review: $2.40 SL / $10 TP). — [AlgoTradingSpace Happy Gold review](https://algotradingspace.com/happy-gold-ea-review)
- Gold Strike EA: backtest 83.84% win rate, profit factor 6.75; live Myfxbook signal PF 1.66, 21.39% drawdown; gap attributed to real spreads 10–30× backtest, commissions, slippage. — [ForexCracked Gold Strike](https://www.forexcracked.com/forex-ea/forexcracked-gold-strike-ea-free-download-xauusd-breakout-scalper-review/)
- A scalping-EA comparison lists at least one gold scalper with a "Real Myfxbook · Exness" account "currently in loss". — [Trade2Sync best scalping EAs](https://trade2sync.com/learn/best-scalping-eas)
- MQL5 blog on gold EA choice (multi-level/grid vs scalp): vendor commentary, no figures. — [MQL5 blog](https://www.mql5.com/en/blogs/post/777116)
- Time-of-day cutoffs: one blog suggests using the journal's time-of-day win-rate report to set a hard session stop (e.g., no trades after 11:00 local) — heuristic, no test data. — [TradeZella scalping](https://www.tradezella.com/blog/scalping-strategies)
- Expectancy definition and the point that a high win rate with negative expectancy still loses. — [TheTradingTerminal](https://thetradingterminal.com/blog/win-rate-vs-profitability); [OneTradeJournal](https://onetradejournal.com/glossary/win-rate)

### Inferences (arithmetic for +2 / −4 per 0.01 lot = 1 oz; c = round-trip cost in USD)
- Break-even win rate p* = (4 + c) / 6: c = 0 → **66.7%**; c = 0.11 → 68.5%; c = 0.17 → **69.5%**; c = 0.27 → 71.2%; c = 0.35 → 72.5%; c = 0.60 (Asia with slippage, FXNX) → **76.7%**; c = 1.00 (news/rollover spread) → 83.3%.
- Expectancy per trade E = p(2 − c) − (1 − p)(4 + c). At c = 0.25: p = 70% → **−$0.05**; 72% → +$0.07; 75% → +$0.25; 80% → +$0.55. Sensitivity ≈ $0.06 per percentage point of win rate — a strategy needs a win rate measured on hundreds of trades before its sign is known (the standard error of a 72% win rate on n = 200 trades is ±3.2 points, i.e., the 95% interval spans break-even).
- Gambler's-ruin baseline: for a drift-free, cost-free price the probability of touching +2 before −4 is 4/(2+4) = 66.7% — identical to the pre-cost break-even. Therefore **any entry rule must beat a coin flip by at least the cost term**; a 70–75% win rate on a 2/4 scalp is not evidence of edge unless costs are included and the sample is large. This also explains why the EVTradeLabs session drifts (≤0.02%/session ≈ $0.8 at $4,000) cannot by themselves lift the hit rate: they are smaller than the cost.
- Expected time to touch either barrier for a drift-free random walk ≈ (2 × 4) / σ²_1min: with the derived M1 σ ≈ $2.5 (overlap) → ~1.3 min; σ ≈ $1.5 (London/Asia active) → ~3.6 min; σ ≈ $1.0 (late NY, quiet Asia) → 8 min; σ ≈ $0.35 (21:00 UTC) → ~65 min. A trade still open far beyond these horizons is, by construction, in a lower-volatility regime than its sizing assumed; this is the only quantitative basis found for a time stop, and it is derived, not sourced.
- The only live-vs-backtest datapoint for a high-win-rate gold scalper (PF 6.75 → 1.66) suggests dividing backtested win rates by a wide margin; the cited causes (spread 10–30× backtest, slippage) are exactly the cost term in the formulas above.

### Gaps
- No verified Myfxbook/FXBlue track record for a gold scalper with TP ≈ ½ SL, with trade count and costs, was found; vendor pages were the only sources and none disclosed sample sizes.
- No study on time stops (close after N minutes) for intraday gold or FX scalps was found; the barrier-hitting-time estimate above is a derivation.
- No source gives the realised distribution of "max favourable excursion" for gold M1 entries, which would be the direct way to size a 2/4 target/stop empirically.
