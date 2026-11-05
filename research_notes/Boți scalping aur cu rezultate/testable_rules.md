# Testable pullback-in-trend M1/M5 scalping rules: catalogue with evidence labels

Scope: concrete, parameterised entry / filter / exit / sizing rules that a pullback-in-M5-trend scalper (gold or liquid FX, fixed small targets) could test, computed only from OHLC, EMAs, ATR, time of day and the bot's own trade log. Each rule is labelled EVIDENCE-BACKED (data shown in the cited source), PLAUSIBLE (expert/practitioner opinion with a mechanism but no data shown), or FOLKLORE (widely repeated, no data found, or data contradicts it).

Overall honesty note for the report writer: after ~30 searches/fetches, **no source was found that reports a reproducible, out-of-sample backtest of a pullback-to-EMA scalper on XAUUSD M1/M5 with win rate / profit factor / expectancy numbers**. MQL5 marketplace listings claim figures but are vendor self-reports. The data-backed material is almost all daily-bar equities/ETFs (Quantpedia, TradingMarkets, Enlightened Stock Trading), 30-minute index CFD (MQL5 article), or academic intraday-momentum papers (S&P 500 ETF, RUB/USD). Parameter ranges for M1/M5 gold therefore have to be *transferred* from those, and every transferred number below is marked as such. The strongest practical recommendation that follows from the evidence is: the bot's own trade log (MAE/MFE, bars-held, hour-of-day, streak position) is the only dataset that can give evidence-backed numbers for this specific system; the catalogue tells it *what to measure and which thresholds to sweep*.

---

## Key question 1: Pullback definitions — which parameters are reported to work, and with what effect?

### Takeaway
The only data-backed pullback definitions found are daily-bar: "N consecutive down closes while above a long MA" (2–3 days best; ≥4 degrades sharply; 1-day hold best), "RSI(2) < 20 above MA200" (best Calmar), and "first close below MA(5) while above MA(20/50/100/200), buy a limit 2–12% lower" (first-pullback-after-strength). Distance-to-EMA-in-ATR, Keltner/Bollinger-touch and VWAP-reversion pullbacks on M5 gold exist only as vendor products or untested scripts.

### Cited Findings
- Multi-asset pullback (SPY, EEM, IEF, FXE, GLD, DBC; daily; Feb 2006–Dec 2025): buy at close if price > SMA200 and the last 2 closes are both down, exit next close. Dynamic equal-weight version: Sharpe 0.95, ~8.96%/yr, max DD −25.1%; 3-down-day version: Sharpe 0.78, −3.6 pp/yr return, trades 2,152 → 1,030, but max DD −13.7% and avg win rate 58.5%; ≥4 down days "degraded sharply"; hold 1 day Sharpe 0.95 vs hold 7 days Sharpe 0.54; MA length 150/200 Sharpe 0.97/0.95 vs MA50 0.85; RSI(2)<20 trigger gave best Calmar 0.63, max DD −11.1%. Individual-ETF (incl. GLD) Sharpe only 0.11–0.22 with win rates >55%. — [Quantpedia, AI-assisted pullback discovery](https://quantpedia.com/testing-an-ai-assisted-research-workflow-for-multi-asset-pullback-strategy-discovery/) — EVIDENCE-BACKED (daily, not intraday).
- "First pullback" (Connors/TradingMarkets; US stocks, daily, 2001–2012): setup = close > MA200, MA100, MA50, MA20 and close < MA5 (first sign of weakness in a strong stock); entry = limit order 2–12% below prior close within 1–5 days; exits = close > MA5, or ConnorsRSI > 50/70. Top-10 variants: avg gain 2.09–2.74%/trade (700–1,800 signals); S&P 500-only universe: 3.00–4.16%/trade (97–319 signals, ~50% improvement, shorter duration, higher win rate — exact win rate/PF not reported). — [TradingMarkets, Learning From The First Pullback Strategy](https://tradingmarkets.com/?p=1584306) — EVIDENCE-BACKED (daily; summary ranges only, no tables).
- Connors RSI(2) variants on US500 M30 (Jan 2024–Mar 2025), MQL5 article: V1 = RSI2<5 & close>SMA200, exit close>SMA5, SL 0.15%; V2 ("pullback") = last three RSI2 readings all <10 & close>SMA200, exit on close above prior bar high, SL 0.15%; V3 = RSI2<5 & close>SMA50, exit RSI2>70, SL 1%. Author reports V3 had the highest win rate and PF but largest floating DD; V1 avg win ≈ avg loss, 1–2 trades/day; exact numbers only in screenshots. — [MQL5 article 17636](https://www.mql5.com/en/articles/17636) — PLAUSIBLE (backtest run but numbers not extractable; 14 months in-sample).
- Community report: Connors RSI-2 on 1-minute bars gave "catastrophic results"; 15-minute called a possible starting point. — [ProRealCode RSI-2 thread](https://www.prorealcode.com/prorealtime-trading-strategies/rsi-2-strategy-larry-connors/?pnum=2) — anecdotal, single user.
- Vendor M5 gold pullback EA ("Ema Trend Bullback"): H4 EMA50 trend, 8/21 EMA cross on M5, entry on pullback to EMA21 with engulfing/pin-bar confirmation; "expect 45–55% win rate → PF 2.4–3.0" is a projection, not a result. — [MQL5 market 170508](https://www.mql5.com/en/market/product/170508) — FOLKLORE (no data).
- Vendor "MTF Pullback Scalper": H4 + M5 EMAs as trend filter, M5 retracement entries; backtest report linked but no figures in listing. — [MQL5 market 187598](https://www.mql5.com/en/market/product/187598) — FOLKLORE.
- Vendor "EMA Breakout Trend Hunter" (breakout, not pullback; 1.5 yrs M5): self-reported PF >1.4, win ~52%, max DD <25%, >80 trades. — [MQL5 market 187009](https://www.mql5.com/zh/market/product/187009) — self-reported, unverified.
- Academic: 1-minute EURUSD shows mean reversion; average deviation from moving mean ≈ 0 (journal paper, not a strategy test). — [Folia Oeconomica Stetinensia 2014](https://doaj.org/article/2ede5372457f45d2bb37ea569a88fa7f) — EVIDENCE-BACKED for *existence* of M1 FX mean reversion, no rule parameters.
- NautilusTrader Bollinger mean-reversion EURUSD 1-min tutorial: notes that without a regime filter the strategy "pays the spread on every cycle". — [NautilusTrader tutorial](https://nautilustrader.io/docs/nightly/tutorials/fx_mean_reversion_ax/) — PLAUSIBLE.
- Bollinger lower-band mean reversion, daily SPY 2006–2025, exit middle band: 75% win (69/23), PF 2.06, max DD 28.9%, CAGR 3.67%; with SMA200 filter and upper-band exit: PF 5.44, 90% win on only 41 trades. Crypto 1H walk-forward: only 7.2% of Bollinger-reversion variants kept PF ≥1.3 out-of-sample. — [Reddit snapshot](https://reddit.sentinel-team.org/posts/1qdoa4g/snapshots/2026-01-16T01%3A20%3A13.051324Z); [edgefinder blog](https://edgefinder-five.vercel.app/blog/why-most-backtests-lie) — small-sample / anecdotal.
- Price distance to moving averages and subsequent returns (US stocks, Alajbeg–Bubaš–Vasić): distance-to-return relation is weaker and less consistent for the 200-day MA than for 20- and 50-day MAs; buying very far below MAs produced the lowest returns. — [CROSBI record](https://www.bib.irb.hr/913079) — EVIDENCE-BACKED (daily equities).

### Inferences
- Transferable rule family (sweep, do not assume): **"N consecutive counter-trend closes (N = 2–3) while price above/below the slow EMA, enter at the next bar, exit within 1–3 bars"**. Evidence says N ≥ 4 destroys the edge and long holds dilute it; on M5 this argues for N = 2–3 M5 bars and a time stop of a few bars rather than 15–20 (see Q3).
- Transferable rule: **RSI(2) on the entry timeframe < 10–20 (longs, above EMA200/EMA50 on M5) with exit on close above prior bar high or RSI(2) > 70** is the best-documented pullback trigger family; the only intraday test (M30) shows the *50-period* trend filter + oscillator exit as the highest win-rate variant.
- "Pullback to EMA within 0.5 ATR", "Keltner/Bollinger touch", "VWAP reversion", "first pullback after breakout" on M5 gold: **FOLKLORE/PLAUSIBLE only** — no numbers found. Suggested sweep grid from the daily analogues: distance to EMA20 ∈ {0.25, 0.5, 0.75, 1.0} ATR(14, M5); EMA ∈ {9, 20, 50}; counter-trend bars ∈ {1, 2, 3}; "failed pullback = price closes beyond EMA50 / beyond 1.5 ATR past the level → cancel".
- Failed-pullback cancellation has no source; the Alajbeg finding (buying *very far* below MAs gives worst returns) is the closest evidence that over-extended pullbacks should be skipped, i.e. cap pullback depth (e.g. ≤ 1.5 ATR beyond EMA20, or price still above EMA50).

### Gaps
- No source with win rate / PF for distance-to-EMA-in-ATR pullbacks on any intraday instrument.
- No VWAP-reversion backtest with numbers (only a vendor "VWAP Reversion Bot" listing).
- No test of "first pullback after breakout" on intraday bars; only daily US stocks.
- No gold-specific pullback numbers at all except vendor claims.

---

## Key question 2: Trend / regime filters — EMA slope, ADX, ATR ratio, HTF alignment, Choppiness, range position

### Takeaway
Long-MA trend filters (MA150–200 on daily) are the only regime filter with shown data (Sharpe ~0.95–0.97 vs 0.85 for MA50). ADX 20 vs 25 vs 30 has no clean comparison; Choppiness Index and ATR-ratio regimes are unbacked; "too aligned = late" is folklore with no data found.

### Cited Findings
- Trend filter length: MA200/MA150 Sharpe 0.95/0.97 vs MA50 0.85 (MA50 better Calmar 0.54, max DD −14.3%) in the multi-asset pullback test. — [Quantpedia](https://quantpedia.com/testing-an-ai-assisted-research-workflow-for-multi-asset-pullback-strategy-discovery/) — EVIDENCE-BACKED (daily).
- Vol-targeting overlay (size = 10% / 21-day realised vol, cap 100%): Sharpe 0.98 vs 0.95, max DD −17.1% vs −25.1%, return 6.8% vs 8.96%. — [Quantpedia](https://quantpedia.com/testing-an-ai-assisted-research-workflow-for-multi-asset-pullback-strategy-discovery/) — EVIDENCE-BACKED (daily).
- ADX threshold: one SPY backtest (unusual rules) reported CAGR ~11.9% at ADX(14) threshold 20 vs ~14.1% at 25; table ambiguous. — [Liberated Stock Trader ADX test](https://www.liberatedstocktrader.com/adx-indicator/) — weak evidence. Opinions: ">20 lets in too many weak signals, test 30 too" vs ">25 is lagging, prefer >20". — [Algotest](https://algotest.in/blog/how-to-automate-adx-indicator/), [Alpha Algo Trading](https://algotr.substack.com/p/stop-trading-in-chop-start-using) — PLAUSIBLE. A vendor claim of "+5–10 pp win rate from ADX>20" has no methodology. — [crosstrade.io](https://crosstrade.io/learn/technical-indicators/adx) — FOLKLORE.
- Choppiness Index: used as a regime gate only (CHOP < 38.2 = trending; > 61.8 = consolidation; one script uses 50); Quantified Strategies ran a SPY CHOP test behind paywall; TrendSpider CHOP strategy shows −43.7% DD with no universe disclosed. — [LuxAlgo CHOP](https://www.luxalgo.com/library/concept/choppiness-index/), [FMZ ADX+CI strategy](https://www.fmz.com/lang/en/strategy/483023), [Quantified Strategies CHOP](https://quantifiedstrategies.substack.com/p/choppiness-index-trading-strategy) — PLAUSIBLE, no accessible numbers.
- ATR regime: open-source script gates on ATR > rolling median (P50) for mean reversion and ATR > P70 for breakouts; no backtest. — [TradingView ATR regime script](https://www.tradingview.com/script/2QcaKbTR) — FOLKLORE/PLAUSIBLE.
- ATR-of-day exhaustion filter (anecdotal): skip new trades when the day has already used ~85% of ATR(14,daily) by 14:00 UTC; only trade when daily range < 70% of ATR14. — [tradersunion gold timing](https://tradersunion.com/interesting-articles/gold-trading-time-analysis/) — PLAUSIBLE (untested).
- Intraday momentum predictability increases with volatility and volume (S&P 500 ETF, half-hour data). — [Alpha Architect summary of Gao–Han–Li–Zhou](https://alphaarchitect.com/2014/08/attention-prop-traders-the-first-half-hour-of-trading-predicts-the-last-half-hour/) — EVIDENCE-BACKED (equity index).

### Inferences
- Transferable: **slow trend filter on the entry timeframe should be long (EMA/SMA 150–200 on M5 ≈ 12–17 hours) rather than EMA50**; shorter filters trade more and lose Sharpe but can reduce drawdown.
- **ADX: sweep {20, 25, 30} and no-filter** — expected trade-off (fewer trades, later entries at 25–30). No evidence supports a specific threshold for gold M5.
- **"Too aligned = late"**: no data. The only related evidence is the Quantpedia result that *more* counter-trend bars (≥4) kill the pullback edge, and Alajbeg's finding that *very* far from the MA yields the worst returns — i.e. extension, not alignment, is what the data flags.
- **ATR-ratio regime (ATR(14,M5) / median ATR of the day or of 20 days)**: untested in sources; the daily "range used ≥ 85% of ATR14" exhaustion rule is a cheap, OHLC-only candidate.

### Gaps
- No EMA-slope threshold study found.
- No measured ADX effect on gold/FX intraday.
- No test of HTF-alignment counting (e.g. M5/M15/H1 all aligned) vs performance.
- No range-position (position within the day's range / Donchian) filter backtest found.

---

## Key question 3: Exits — fixed TP/SL in ATR, time stops, break-even, partial exits, trailing, MAE/MFE

### Takeaway
MAE/MFE research (Sweeney 1985/1997; modern vendor write-ups) supports placing the stop just beyond the MAE that most *winners* survive (75th–90th percentile + 20–30% buffer), and treating a losing trade's MFE as the key statistic for "losers rarely recover" — but no source actually published the losers'-MFE numbers. Partial exits/profit targets on trend trades reduced return in every tested combination and only one combination improved MAR. Time stops: one blog reports +41–101% expectancy from a time threshold; otherwise opinion only.

### Cited Findings
- Sweeney (Technical Analysis of Stocks & Commodities, 1985; book 1997): every method has its own pattern of adverse moves; stops are set "to cut off large losers while leaving room for winners" using the MAE distribution of winners; MAE and MFE plotted against final outcome. — [Traders.com V.5:4](https://store.traders.com/-v05-c04-usingma-pdf.html), [Traders.com V.10:1](https://store.traders.com/-v10-c01-testing-pdf.html), [NexusFi MAE wiki](https://nexusfi.com/wiki/trading-wiki/Maximum-Adverse-Excursion) — EVIDENCE-BACKED method; numbers in paywalled book.
- Backtrex workflow: (1) backtest with very wide stop to get true MAE, (2) take the *winners'* MAE distribution, (3) stop = 90th percentile + 20–30% buffer (75th = "standard", 50th = aggressive, >95th too wide), (4) re-run and compare early-exit count and PF; needs ≥ 50–100 winning trades, < 30 is "too sensitive to outliers"; illustrative example: median winner MAE 1.2% with stop at 0.8% would cut ~50% of winners and drop win rate from 55% to ~35%; break-even suggestion: if winners typically reach +2% MFE with ~0.8% MAE, move stop to BE at +1.5%. No real data shown. — [Backtrex MAE blog](https://backtrex.com/en/blog/maximum-adverse-excursion-mae-backtest-trading) — PLAUSIBLE (vendor, illustrative).
- MAE distributions: plot winners' and losers' MAE separately; if they separate, stop near the boundary; if they overlap heavily, MAE gives no clean stop. — [LuxAlgo MAE/MFE distributions](https://www.luxalgo.com/library/concept/mae-mfe-distributions/) — PLAUSIBLE.
- Excursion caveats: optimising a stop on 30 trades is overfitting; rules fitted to a calm period get stopped excessively when conditions change; a backtest without precise OHLC/tick data understates real MAE. — [NexusFi MAE](https://nexusfi.com/wiki/trading-wiki/MAE), [Backtrex](https://backtrex.com/en/blog/maximum-adverse-excursion-mae-backtest-trading) — PLAUSIBLE.
- Partial exits / profit targets on a trend system (ASX stocks, 200-day-high entry, 25% trailing stop, 1,432 trades, base return 21.6%, DD 41.7%, MAR 0.52): profit targets 25–150% with 0–100% position reduction — **every combination lowered return**, every combination lowered drawdown, and only *one* (75% target, 100% exit) beat base MAR. — [Enlightened Stock Trading](https://enlightenedstocktrading.com/trend-following-profit-targets-do-they-work) — EVIDENCE-BACKED (daily, trend-following; not scalping).
- Time stops: one blog claims a time threshold raised expectancy per dollar risked by 48% (IBD basket), 101% (Nasdaq), 41% (S&P 500) — methodology not shown. — [IBD index blog](https://ibdindex.blogspot.com/2008/03/oneils-exception-to-20-profit-target.html?m=1) — weak evidence. Method advice: compute expectancy vs bars-held and set the time stop where expectancy turns negative; "if momentum-based, a time exit may add value". — [EliteTrader time-based stops](https://www.elitetrader.com/et/threads/time-based-stops.219254) — PLAUSIBLE.
- Hold length in the daily pullback test: 1-day hold Sharpe 0.95 vs 7-day 0.54 — the edge decays fast after entry. — [Quantpedia](https://quantpedia.com/testing-an-ai-assisted-research-workflow-for-multi-asset-pullback-strategy-discovery/) — EVIDENCE-BACKED (daily).
- Break-even / trailing: on a mean-reversion strategy a trailing stop "turns a winning trade into a break-even or small-loss trade by exiting on the oscillation"; opposite opinion: BE stops remove "winner turned loser" outcomes. Neither shows data. Vendor default: BE trigger at 2.0 ATR for swing trading. — [primefoldtools](https://primefoldtools.beehiiv.com/p/the-exit-rule-that-survived), [MQL5 forum 497723](https://www.mql5.com/en/forum/497723), [Banana EA docs](https://itradeaims.gitbook.io/banana-ea/complete-user-guide/trade-management-system/trade-management-features.md) — FOLKLORE/PLAUSIBLE.
- Partial stop-loss exit on FX: a blog reports higher profit and lower drawdown on 4 of the pairs tested, smaller profit in others; small sample, unclear method. — [jagadevcare blog](https://jagadevcare.blogspot.com/2013/11/how-to-reduce-your-maximum-drawdown.html) — anecdotal.
- Mechanical Trading Systems (Wiley) 5-minute RSI-extremes system with 16.67-hour MA filter (Jun 2003–Jan 2004): shorter timeframes make mean reversion more likely to hit a tight stop. — [O'Reilly chapter listing](https://www.safaribooksonline.com/library/view/mechanical-trading-systems/9780471730972/chapter05-sec018.html) — PLAUSIBLE (results not visible).

### Inferences
- **Stop rule to test on the bot's log**: SL = P75–P90 of winners' MAE (in ATR(14,M5)) × 1.2–1.3. Requires ≥ 50–100 winners; with fewer, use P75 and widen rather than narrow.
- **"Losers never recover" test (the key question)**: compute, for each closed loser, MFE_loser / SL_distance and MFE_loser / TP_distance. If, e.g., < 20% of losers ever reached +0.5 × TP, an early exit at "no +0.5 TP within N bars" is justified; if losers' MFE overlaps winners' MFE heavily, no early-exit rule exists (LuxAlgo/Backtrex overlap logic). **No source published these numbers for any market — it must be measured.**
- **Time stop**: no published N for scalps. Evidence that pullback edges decay within 1–3 bars on daily data, and the EliteTrader method, suggest: tabulate expectancy by bars-held and cut where it crosses zero; sweep N ∈ {5, 10, 15, 20, 30} M5 bars (or 10–60 M1 bars). For a fixed small target, expect the optimum to be *short* (edge is in the first few bars).
- **Partial exits / BE**: the one real test (trend system) says they lower return and DD together and rarely improve MAR — on a scalper with fixed small TP, a BE move at +0.5 TP will most likely cut winners (the ProRealCode/primefold mean-reversion warning). Test BE at {0.5, 0.75} × TP vs none; expect "none" to win on expectancy, BE to win on DD.
- **Trailing by ATR** on scalps with fixed small TP: no evidence; the only trend-system evidence favours wide trailing (25%) with no target. Treat as FOLKLORE for this system.
- ATR vs USD units: no comparative study found; ATR units are the standard in MAE literature (excursions normalised by volatility).

### Gaps
- No source gives percentile-of-MFE-of-losers data for any instrument (the central "losers rarely reach +X" claim is unverified in the literature).
- No published time-stop N for intraday scalps.
- No gold-specific ATR multiple for TP/SL with reported PF.

---

## Key question 4: Exhaustion / overextension — consecutive bars, distance from EMA, RSI extremes on M1, win-streak cooldowns

### Takeaway
Overextension is evidenced only indirectly: ≥4 consecutive counter-trend days kills the pullback edge, buying very far from the MA yields the worst returns, and RSI(2) extremes work on daily/M30 but reportedly fail on M1. Streaks of quick wins as an "extended move" signal and consecutive-win cooldowns have no supporting data; the only quantitative pushback found is that pauses after losses reduced profit in one trader's tests.

### Cited Findings
- ≥ 4 consecutive down days "degraded sharply"; 2–3 best. — [Quantpedia](https://quantpedia.com/testing-an-ai-assisted-research-workflow-for-multi-asset-pullback-strategy-discovery/) — EVIDENCE-BACKED (daily).
- Buying very far below almost all MAs on almost all horizons produced the *lowest* returns (US stocks). — [CROSBI 245648](https://www.bib.irb.hr/913079) — EVIDENCE-BACKED (daily).
- RSI(2) < 5 / > 95 works on US500 M30 (author report); RSI-2 on 1-minute bars "catastrophic" (forum). — [MQL5 17636](https://www.mql5.com/en/articles/17636); [ProRealCode](https://www.prorealcode.com/prorealtime-trading-strategies/rsi-2-strategy-larry-connors/?pnum=2) — PLAUSIBLE / anecdotal.
- Losing-streak arithmetic: in a 500-trade sample the expected max losing streak is ~14 at 35% win rate and ~6 at 65%; size risk with a ×1.5 buffer on the backtest's longest streak; an 11-EA sample showed 22 losses in a row at a 74% win rate. — [MQL5 blog 776451](https://www.mql5.com/en/blogs/post/776451), [MQL5 blog 777422](https://www.mql5.com/en/blogs/post/777422) — EVIDENCE-BACKED (simulation/backtest statistics).
- One trader: all drawdown-reduction methods tested, including halting trades after losses, "also reduced profit". — [MQL5 forum 442476](https://www.mql5.com/en/forum/442476/45240359) — anecdotal, points *against* pauses.
- Practitioner rule (not tested): cut size after 4–5 losses, stop for a session or days after 6–10. — [thenexttrade](https://thenexttrade.vercel.app/articles/how-to-handle-a-losing-streak) — FOLKLORE.
- Defining "consecutive" at portfolio level is non-trivial (overlapping trades; days without closes). — [Wealth-Lab discussion 12359](https://wealth-lab.com/Discussion/Building-Block-to-pause-trading-12359) — PLAUSIBLE.

### Inferences
- **Consecutive same-direction bars as an exhaustion veto**: test "skip entries in the trend direction after ≥ 4–5 consecutive trend-direction M5 closes" and "skip pullback entries after ≥ 4 counter-trend closes" (the daily analogue of the 4-day breakdown). Label PLAUSIBLE.
- **Distance from EMA in ATR as an exhaustion veto**: "skip longs if close − EMA20 > k × ATR(14)", sweep k ∈ {1.5, 2, 2.5, 3}. Supported only by daily equity evidence that extremes underperform; PLAUSIBLE.
- **RSI extremes on M1**: evidence is negative for M1 and positive for M30/daily; if used, use RSI(2) on M5 not M1. PLAUSIBLE at best.
- **Consecutive-win cooldown**: FOLKLORE. Win streaks of the expected length at the system's win rate are pure chance (streak arithmetic sources); no data shows that a run of quick wins predicts an extended move. The correct test is: expectancy of trade k+1 conditioned on the length of the preceding win streak, from the bot's own log; if flat, drop the rule.
- **Consecutive-loss cooldown**: also unbacked, with one anecdote saying it costs profit; treat as a risk/psychology rule, not an edge.

### Gaps
- No study of win-streak-conditional expectancy for any intraday system.
- No consecutive-bar exhaustion backtest on intraday gold/FX.

---

## Key question 5: Time-of-day and news filters — effect sizes

### Takeaway
Intraday momentum (first-half-hour → last-half-hour) is robust in equity-index and RUB/USD research but small after costs; for gold the only sources are broker/blog claims that the London–NY overlap (≈12:00–17:00 UTC, core 13:00–16:00) produces most of the daily range, and that the 22:00 GMT rollover prints the widest spreads. News filters: no effect-size study; one small FX backtest was *more* profitable including NFP days, and spread widening (EURUSD 0.8 → 3 pips; gold $0.50–1.00 in thin sessions) is the concrete cost.

### Cited Findings
- Gao, Han, Li, Zhou (JFE 2018), S&P 500 ETF: first-half-hour return predicts last-half-hour return; R² 1.6% (2.6% adding the 12th half-hour); simple long/short-last-half-hour rule earns 6.34%/yr gross vs −0.5% for holding all last half-hours; predictability rises with volatility and volume; QuantConnect replication found lower returns; CXO notes gross figures and that magnitudes are too small after frictions for most. — [Alpha Architect](https://alphaarchitect.com/2014/08/attention-prop-traders-the-first-half-hour-of-trading-predicts-the-last-half-hour/), [CXO Advisory](https://cxoadvisory.com/calendar-effects/first-and-last-half-hours-of-trading-linked), [QuantConnect replication](https://www.quantconnect.com/research/15348/intraday-etf-momentum/), [Quantpedia update](https://vvv.quantpedia.com/?p=561) — EVIDENCE-BACKED (equity index).
- Elaut, Frömmel, Lampaert (JFM 2018), RUB/USD transaction data 2005–2014: intraday momentum (first vs last half-hour) exists, attributed to liquidity providers' aversion to overnight holdings; stronger in crises; magnitude and after-cost profitability not in abstract. — [IDEAS/RePEc](https://ideas.repec.org/a/eee/finmar/v37y2018icp35-51.html) — EVIDENCE-BACKED (FX, but single exotic pair).
- Breedon & Ranaldo (JMCB 2013): currencies depreciate during their local trading hours (EBS data 1997–2007). — [QMUL working paper 694](https://ideas.repec.org/p/qmw/qmwecw/694.html) — EVIDENCE-BACKED (FX, daily-cycle effect).
- Gold intraday seasonality (student thesis): hat-shaped pattern, gold appreciates during eastern (Asian) hours. — [CBS student project](https://research.cbs.dk/en/studentProjects/gold-price-dynamics-around-the-clock/) — weak evidence.
- Gold sessions (broker/blog claims): London–NY overlap ≈ 12:00–17:00 UTC is highest volatility; most of the daily move forms 13:00–16:00 GMT; Asia often range-bound; 22:00 GMT rollover hour frequently has the widest spread; survey of 1,050 retail traders: 62% best results in the overlap. — [tradersunion](https://tradersunion.com/interesting-articles/gold-trading-time-analysis/), [dev.to XAUUSD sessions](https://dev.to/xauusdrobot/when-does-gold-actually-move-xauusd-trading-sessions-explained-i4a), [Fusion Markets](https://fusionmarkets.com/posts/normal-trading-day-in-gold) — PLAUSIBLE/FOLKLORE (no measured ATR-by-hour table).
- NFP filter backtest (EURCHF, AUDNZD, GBPNZD scalper, MT5): profit *higher* including NFP days (EURCHF 2089.81 vs 1671.10; AUDNZD 1015.88 vs 815.01), poster distrusts tester precision around news. — [MQL5 forum 375320](https://www.mql5.com/en/forum/375320) — anecdotal, contradicts the folklore.
- Spread widening around releases: EURUSD 0.8 → 3 pips just before release; a news filter that blocks entries does not protect an open stop from widening; spreads take minutes to normalise. — [MQL5 blog 764095](https://www.mql5.com/en/blogs/post/764095) — PLAUSIBLE.
- Gold vendor warning: gold can move $30 in seconds, slippage turns a $1.50 SL into much more; spreads $0.50–$1.00 in thin sessions. — [TradingView Gold Smart Scalper script](https://www.tradingview.com/script/smYrqoA4-Gold-Smart-Scalper-AI-V2) — FOLKLORE (vendor).
- Gold NFP reaction (FXStreet, few dozen releases): +$8.33 avg 15 min after a downside NFP miss, −$4.68 after an upside surprise. — [FXStreet NFP preview](https://www.fxstreet.com/analysis/us-may-nonfarm-payrolls-preview-analyzing-gold-price-reaction-to-nfp-surprises-202506051000) — EVIDENCE-BACKED (small sample).
- Testing advice: test quiet, directional and news-driven regimes separately; favourable fixed spreads misrepresent results. — [MQL5 blog 776595](https://www.mql5.com/en/blogs/post/776595) — PLAUSIBLE.

### Inferences
- **Session filter to test**: allow entries only 07:00–17:00 UTC (London + overlap), with a sub-test of 12:00–17:00 only; block 21:30–23:00 UTC (rollover spreads) and the first 15 min of Asia. Supported by consistent but unmeasured practitioner claims — PLAUSIBLE. The bot log can give the evidence: expectancy and avg spread by UTC hour.
- **News filter**: the standard ±15–30 min block around high-impact USD releases is PLAUSIBLE for *spread/slippage* reasons, but the only backtest found shows it *cost* profit on FX; the effect on gold is unmeasured. Test both, and separately measure SL slippage during release windows from the log.
- **Intraday momentum use**: for a pullback-with-trend scalper the equity/FX evidence suggests the *direction of the first 30 minutes of the main session* (e.g. 13:30–14:00 UTC for NY) is a weak but real directional tilt for the session's last hour; magnitude is too small to be a standalone entry but is a cheap trend-direction confirmation.

### Gaps
- No measured ATR-by-hour or expectancy-by-hour table for XAUUSD found (must be computed from own data).
- No controlled study of news-window exclusion on gold scalping P&L.

---

## Key question 6: Position sizing by confidence / setup quality — Kelly fraction, tiered sizing

### Takeaway
No head-to-head backtest of fractional Kelly vs fixed-fractional vs tiered sizing was found. Consensus (opinion) is that quarter-to-half Kelly keeps most growth with far smaller drawdown and is far more robust to estimation error; the only sizing rule with shown data is vol-targeting (Sharpe 0.95 → 0.98, DD −25% → −17%, return −2.2 pp).

### Cited Findings
- Vol targeting (size = 10% / 21-day realised vol, capped 100%) in the pullback test: Sharpe 0.98 vs 0.95, max DD −17.1% vs −25.1%, return 6.8% vs 8.96%. — [Quantpedia](https://quantpedia.com/testing-an-ai-assisted-research-workflow-for-multi-asset-pullback-strategy-discovery/) — EVIDENCE-BACKED (daily).
- Fractional Kelly: growth scales ~linearly with Kelly fraction while drawdown severity scales faster; quarter-to-half Kelly keeps the majority of growth. — [NexusFi Kelly](https://nexusfi.com/a/risk-management/kelly-criterion) — PLAUSIBLE (derivation, no backtest). "Half Kelly ≈ 75% of max growth with ~50% less drawdown" — [damnpropfirms](https://damnpropfirms.com/trading-guides/adjust-risk-using-backtesting-data/) — FOLKLORE (uncited).
- Fixed fractional avoids the parameter-estimation burden that makes full Kelly fragile; Kelly without reliable data is worse than fixed fractional. — [LuxAlgo Kelly](https://www.luxalgo.com/library/concept/kelly-criterion/), [quantest blog](https://quantest-web.pages.dev/en/blog/position-sizing/) — PLAUSIBLE.
- Live drawdowns can be 1.5–2× backtest (20% → 30–40%). — [damnpropfirms](https://damnpropfirms.com/trading-guides/adjust-risk-using-backtesting-data/) — FOLKLORE (rule of thumb).
- Streak sizing: size so that the backtest's longest losing streak × 1.5 is survivable. — [MQL5 blog 776451](https://www.mql5.com/en/blogs/post/776451) — EVIDENCE-BACKED (statistics), PLAUSIBLE as a rule.

### Inferences
- **Tiered sizing by setup quality has no published evidence.** The testable version is: tag each trade in the log with a quality score (e.g. number of filters passed: trend length, ADX, session, pullback depth), compute expectancy per tier, and only then weight size ∝ expectancy (capped at ¼–½ Kelly computed per tier). If per-tier expectancy does not differ significantly (needs ~100+ trades per tier), tiered sizing is noise.
- **Vol-scaled lot (lot ∝ 1/ATR)** is the only sizing change with shown data (daily) and is cheap on M5: lot = risk_USD / (SL_ATR_multiple × ATR(14,M5) × contract value). EVIDENCE-BACKED on daily, transferred.
- Fractional Kelly for a scalper with ~50–60% win rate and ~1:1 payoff gives tiny fractions; the practical use is as a *cap*, not a target.

### Gaps
- No data on Kelly/tiered sizing effect for intraday FX/gold.
- No study of confidence-based sizing (e.g. LLM/score-based) in any trading context found.

---

## Consolidated rule catalogue (parameters to sweep, with labels)

| # | Family | Rule (OHLC/EMA/ATR/time/log only) | Parameter grid | Label | Basis |
|---|--------|-----------------------------------|----------------|-------|-------|
| 1 | Pullback | Enter with trend after N consecutive counter-trend M5 closes, price still on trend side of slow EMA | N ∈ {2,3}; veto N ≥ 4 | PLAUSIBLE (daily analogue EVIDENCE-BACKED) | Quantpedia |
| 2 | Pullback | RSI(2) on M5 < 10 (long) / > 90 (short) with close above/below EMA200; exit close > prior high or RSI(2) > 70 | thresholds {5,10,20}; exits {prior-high, RSI70, EMA5} | PLAUSIBLE | MQL5 17636, Quantpedia |
| 3 | Pullback | Pullback depth to EMA20 within k × ATR(14,M5); cancel if close beyond EMA50 or > 1.5 ATR past EMA20 | k ∈ {0.25,0.5,0.75,1.0} | FOLKLORE (no data) | — |
| 4 | Pullback | First pullback after new 50/100-bar high: first close < EMA5 while > EMA20/50/200; limit entry below prior close | limit offset {0.25,0.5,1.0} ATR | PLAUSIBLE (daily EVIDENCE-BACKED) | TradingMarkets |
| 5 | Regime | Trend filter length on M5 | EMA ∈ {50,100,150,200}; expect 150–200 best Sharpe, 50 best DD | PLAUSIBLE (daily EVIDENCE-BACKED) | Quantpedia |
| 6 | Regime | ADX(14) ≥ T on M5 or M15 | T ∈ {none,20,25,30} | PLAUSIBLE | Algotest / Alpha Algo |
| 7 | Regime | Choppiness(14) < 38.2 (trend) / skip > 61.8 | thresholds {38.2, 50, 61.8} | FOLKLORE | LuxAlgo |
| 8 | Regime | ATR(14,M5) vs rolling median; day-range-used ≥ 85% of ATR(14,D1) → skip | median/P70; 70%/85% of daily ATR | PLAUSIBLE | TradingView script, tradersunion |
| 9 | Exhaustion | Skip trend-direction entry if close − EMA20 > k × ATR | k ∈ {1.5,2,2.5,3} | PLAUSIBLE | Alajbeg et al. (daily) |
| 10 | Exhaustion | Skip after ≥ 4–5 consecutive same-direction M5 closes | {4,5,6} | PLAUSIBLE | Quantpedia (inverse) |
| 11 | Exhaustion | Consecutive-win cooldown (pause after W quick wins) | W ∈ {2,3,4} | FOLKLORE — test conditional expectancy, expect null | streak arithmetic |
| 12 | Exhaustion | Consecutive-loss pause | L ∈ {3,5} | FOLKLORE; one anecdote: reduces profit | MQL5 forum |
| 13 | Exit | SL = P75–P90 of winners' MAE × 1.2–1.3 (ATR units); needs ≥ 50–100 winners | P75, P90 | EVIDENCE-BACKED method (Sweeney); numbers must be measured | Sweeney, Backtrex |
| 14 | Exit | Early exit if MFE < m × TP after B bars (only if losers' MFE distribution separates from winners') | m ∈ {0.3,0.5}; B ∈ {3,5,10} | PLAUSIBLE — no published losers'-MFE numbers | LuxAlgo, Backtrex |
| 15 | Exit | Time stop after N bars | N ∈ {5,10,15,20,30} M5 bars; cut where expectancy-by-bars-held < 0 | PLAUSIBLE (one unverified +41–101% claim) | EliteTrader, IBD blog |
| 16 | Exit | Break-even at b × TP | b ∈ {none,0.5,0.75} | PLAUSIBLE; expect lower return, lower DD | Enlightened ST (trend), opinions |
| 17 | Exit | Partial exit p at q × TP | p ∈ {0,0.5}; q ∈ {0.5,0.75} | EVIDENCE-BACKED on trend system: every combo lowered return; rarely better MAR | Enlightened ST |
| 18 | Exit | ATR trailing | multiple {1.5,2,3} | FOLKLORE for scalps | — |
| 19 | Time | Entries only 07:00–17:00 UTC; sub-test 12:00–17:00; block 21:30–23:00 UTC | windows | PLAUSIBLE (consistent but unmeasured) | tradersunion, Fusion |
| 20 | Time | Block ±15–30 min around high-impact USD news | ±15, ±30 | PLAUSIBLE for slippage; one FX backtest says it costs profit | MQL5 375320 / 764095 |
| 21 | Time | Session-open momentum tilt: only take trades in the direction of the first 30 min of London/NY open during the last hour of that session | first-30-min sign | EVIDENCE-BACKED (S&P 500 ETF, RUB/USD); gross, small | Gao et al., Elaut et al. |
| 22 | Sizing | Lot ∝ 1/ATR (vol targeting) | target vol 10% annualised analogue | EVIDENCE-BACKED (daily); transferred | Quantpedia |
| 23 | Sizing | Tiered size by setup-quality tier, ≤ ¼–½ Kelly per tier, only if per-tier expectancy differs with ≥ 100 trades/tier | tiers 2–3 | FOLKLORE until measured | NexusFi, LuxAlgo |
| 24 | Sizing | Survive 1.5 × backtest's longest losing streak at chosen risk | ×1.5 | EVIDENCE-BACKED (statistics) | MQL5 776451 |

## Sources not reachable / excluded
- Quantified Strategies "When to sell — best trade exits" (paid), CHOP backtest (paid) — not readable. — [QS exits](https://quantifiedstrategies.substack.com/p/when-to-sell-unveiling-the-best-trade)
- Sweeney's book full text, Elaut et al. full text (paywalled) — magnitudes unavailable.
- Robot Wealth / Alvarez Quant Trading: no relevant pages surfaced in searches for intraday FX/gold pullbacks or EMA distance.
- MQL5 marketplace listings (Xauusd Scalper Gold, Gold Pullback Sniper, Simple Pullback EA) — vendor claims only, "optimised on successful backtests" (overfitting flag). — [MQL5 184529](https://www.mql5.com/en/market/product/184529), [164574](https://www.mql5.com/zh/market/product/164574), [169461](https://www.mql5.com/en/market/product/169461)
