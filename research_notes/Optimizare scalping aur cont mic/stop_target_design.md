# Stop-loss / take-profit design for ATR-based intraday scalping (gold XAUUSD, liquid FX, M1–M5)

Scope note for the report writer: rigorous, peer-reviewed evidence on stops exists almost only for daily/monthly equity data. For M1–M5 gold/FX scalping the available material is (a) one broker-published 20,000-trade intraday study with undisclosed methodology, (b) vendor EA backtests (marketing, unverified), (c) a handful of independent practitioner backtests (daily gold, H1 gold, ASX stocks, SPY constituents), and (d) math that can be derived exactly. Every item below is tagged with instrument / timeframe / period and with a reliability label: **[peer-reviewed]**, **[independent backtest]**, **[vendor/broker claim]**, **[anecdotal]**, **[derived math]**.

---

## KQ1. What do backtests / papers say about SL at 1, 1.5, 2, 2.5, 3× ATR(14) on M1–M5 for gold/FX scalping (win rate, expectancy, drawdown)?

### Takeaway
No independent study sweeps 1–3× ATR stops on M1–M5 gold with expectancy and drawdown per level. The best available intraday data (broker-published, 20,000 trades, EURUSD/GBPJPY/XAUUSD, mostly M15) shows win rate rising steeply from ~39% at 1.0× ATR to ~51% at 1.5× and ~58% at 2.0× ATR with a fixed 1.5R target, i.e. stops ≤1× ATR are hit mostly by noise; the academic literature (daily/monthly equities) consistently finds tight stops underperform because of excess trading/transaction costs, while ATR-scaled stops are the "balanced" choice.

### Cited Findings
- **Win rate vs ATR stop multiple, 20,000 trades, EURUSD/GBPJPY/XAUUSD, intraday (EURUSD example on M15 with ATR(14)), data period not stated, fixed 1.5R target** — [broker/vendor claim]: 0.5× ATR → 16.1% win rate; 1.0× → 38.6%; 1.5× → 51.2%; 2.0× → 58.4%. A separate configuration with 3.5× ATR stop and 1.0× ATR target gave 78.2% win rate. The article's "equilibrium band" is 1.3–1.8× ATR with 1.5–2.0R targets (48–54% win rate). 2.5× and 3.0× are not reported. Caveats: published by broker FXNX, no data period/broker feed/signal definitions, the 3.5× result uses a different target so is not comparable, and the article contains internal inconsistencies (e.g. gold 20 pips described as 8% of daily range while 20/2400 = 0.8%). — [FXNX 20,000-trade study](https://fxnx.com/en/blog/stop-loss-distance-vs-hit-rate-20-000-trade-study)
- Same source, streak risk by stop width (methodology undisclosed): probability of ≥8 consecutive losses within 100 trades: 0.5× ATR 99.4%; 1.0× 44.8%; 1.5× 6.2%; 2.0× 1.1%. Eight consecutive losses at 0.75% risk = 6.0% drawdown vs typical prop-firm 5% daily / 10% trailing limits. — [FXNX](https://fxnx.com/en/blog/stop-loss-distance-vs-hit-rate-20-000-trade-study)
- Same source, cost friction: with 1.5 pips round-trip friction (0.5 spread + 0.4 commission + 0.6 slippage) the friction is 30% of a 5-pip stop, 15% of 10 pips, 7.5% of 20 pips, 5% of 30 pips; a nominal 1:3 R:R degrades to 1:2.07 on a 5-pip stop vs 1:2.81 on a 30-pip stop. — [FXNX](https://fxnx.com/en/blog/stop-loss-distance-vs-hit-rate-20-000-trade-study)
- **Gold, daily bars, ~10 years, independent system developer** — [independent backtest, anecdotal detail]: tightening the stop from 3× ATR to 1× ATR cut the win rate from ~67% to 45% but allowed ~3× position size for the same dollar risk; with dynamic sizing the author reports net return rising from ~300% to ~6000% over 10 years, profit factor "dropped slightly" from >2.5, and drawdown "more pronounced" (figures only in charts). Author states an optimisation found 1× ATR most profitable for that (swing, ~2-week hold, not scalping) system. — [ProRealCode thread](https://www.prorealcode.com/topic/thoughts-observations-on-stop-losses-and-dynamic-positions-sizing/)
- **XAUUSD M1 EA, Exness MT5 ticks Jan 2020–Apr 2026 (78% tick quality), SL = 1.2× ATR(14), TP = 2.0× SL** — [vendor claim]: 13,491 trades, 87.11% win rate, profit factor 4.72, max equity DD 7.84%, avg trade duration 41 s, max consecutive losses 6. Backtest used a 100-point max-spread filter while recommending 30 points live; includes break-even at +20 points and trailing from +30 points. Seller-reported, unverified; an 87% win rate with a 2:1 reward ratio is implausibly far above break-even (33%) and probably reflects the break-even/trailing exits recording tiny "wins". — [Xauusd Gold 1M Scalper Pro, MQL5](https://www.mql5.com/en/market/product/174156)
- **XAUUSD H1 EA, Jan 2025–May 2026, SL 1.2× ATR, TP 6.0× ATR, trail 3.0× ATR** — [vendor claim]: 295 trades, 31.5% win rate, 36% max DD. — [XAU Aurum Imperial Scalper, MQL5](https://www.mql5.com/fr/market/product/177893)
- **XAUUSD M5 EA, 2019–2023, SL 1.5× ATR, TP 2.5× ATR, 0.75% risk** — [vendor claim]: claimed +232%/year, "fixed spread demo only". Unverified. — [Scalp Gold Sniper EA](https://leotaby.github.io/scalpgold-sniper-ea/ScalpGold_3D_Viz.html)
- **XAUUSD H1, 485 trades, Jan 2023–Aug 2026 (tuned 2023-24, checked 2025-26)** — [independent backtest]: experiments with very wide stops and tiny targets: 6× ATR stop / 0.2R target → 87.1% win rate, profit factor 1.00; 5× ATR / 0.3R → 81% win rate, PF 1.10. The shipped version used 4R targets (3.5–5.0R all improved on 2R), PF 1.21 → 1.68 after changes; in-sample 2023-24 PF was only 0.92. — [MQL5 blog, "I tested four trading ideas"](https://www.mql5.com/en/blogs/post/773874)
- **Multiple assets, 2% fixed to ATR-adaptive stops, MANOVA + OLS, 2024 master's thesis** — [academic, abstract only]: no stop setting produced statistically significant improvement in Sharpe, Sortino or annualised return; more aggressive (tighter) stops reduced volatility and max drawdown but lowered win rate and average profit per trade; ATR-based stops described as the "balanced option with consistent effectiveness across metrics". — [UCLouvain thesis (André 2024)](https://thesis.dial.uclouvain.be/entities/masterthesis/61e583f3-a6f1-4765-8174-dbb333b07590)
- **US individual stocks, closed-form model with serial correlation and transaction costs (MIT thesis 2016)** — [academic]: "tight stop-loss strategies tend to underperform the buy-and-hold policy due to excessive trading costs"; outperformance only for stocks with sufficiently high serial correlation in returns. — [Remorov, MIT DSpace](https://dspace.mit.edu/handle/1721.1/107017?show=full)
- **Kaminski & Lo, "When do stop-loss rules stop losses?", J. Financial Markets 2014, US monthly data 1950–2004** — [peer-reviewed]: under a random walk, simple stop-loss rules always reduce expected return; with momentum (positive serial correlation) they can add value; certain rules added 50–100 bp/month during stopped-out periods. — [MIT DSpace record](https://dspace.mit.edu/entities/publication/bb69ca4b-0cdc-487f-831d-63b2e84fafee); [RePEc](https://econpapers.repec.org/RePEc:eee:finmar:v:18:y:2014:i:c:p:234-254); summary with 10% rule detail (stocks beat bonds 70% of time when invested) at [Quant-Investing](https://www.quant-investing.com/blog/truths-about-stop-losses-that-nobody-wants-to-believe)
- **Van Tharp (secondary summary of his book)** — [practitioner, secondary]: recommends stop = 10-day average true range × a constant between 2.7 and 3.4 ("a 3 ATR stop") to sit beyond daily noise; tight stops "lose less per trade but produce more losers and more trades, raising transaction costs"; MAE analysis of winners may justify ~2 ATR but risks curve-fitting. — [The 7 Circles, Van Tharp 7 – Stops and Exits](https://the7circles.uk/?p=32777)
- **Lei & Li, "The value of stop loss strategies", Financial Services Review 2009, US stocks** — [peer-reviewed]: returns with fixed or trailing stops statistically indistinguishable from buy-and-hold; value comes from risk reduction, not return. — [CXO Advisory summary](https://www.cxoadvisory.com/technical-trading/using-trailing-stop-losses-to-reduce-risk); [journal](https://openjournals.libs.uga.edu/fsr/article/view/4933)
- **Long mean-reversion system, S&P 500 constituents, 2000–2026 (IS 2000-12, OOS 2013-26)** — [independent backtest]: adding a 5% stop cut CAGR 8.22% → 1.05%, worsened max DD -18.6% → -41.8%, MAR 0.44 → 0.03, win rate 66.7% → 49.1%, PF 2.04 → 1.08; 911 of 1,957 trades were cut short. Relevant because scalping entries are usually mean-reversion-like. — [SetupAlpha, stop-loss mean reversion backtest](https://setup4alpha.substack.com/p/stop-loss-mean-reversion-backtest)

### Inferences
- The one intraday dataset available implies ≈60% of 1× ATR stops are noise hits (38.6% win rate with a 1.5R target vs 58.4% at 2× ATR). Moving from 1× to 2× ATR roughly halves the loss frequency but also halves the R-multiple of a fixed-distance target, so expectancy change is not automatic — the FXNX article itself only gives hypothetical expectancy, not measured.
- The academic consensus (Kaminski & Lo; Remorov; Lei & Li; UCLouvain) is that stops add return only when the price process has positive autocorrelation at the holding horizon; on M1–M5 gold, the process after a scalping entry is closer to mean-reverting/random, so a stop's job is drawdown control, not expectancy enhancement. Tight stops (≤1× ATR) convert noise into realised losses plus spread cost.
- For gold at ATR(14, M1) ≈ 0.8–2 USD (typical 2025–26 regime, inferred from the FXNX daily ATR of ~24 USD), a 2.5× ATR stop is ~2–5 USD — the vendor and independent gold tests that report high win rates all use stops in this range or wider (1.2× ATR + break-even; 5–6× ATR).

### Gaps
- No independent, cost-inclusive backtest of 1 / 1.5 / 2 / 2.5 / 3× ATR(14) stops on XAUUSD M1 or M5 with win rate, expectancy and max DD per level was found. The 2.5× and 3.0× points are missing even from the FXNX sweep.
- Kaufman (Trading Systems and Methods) and Pardo stop-loss results could not be retrieved (search returned only book listings and chapter titles).
- Cesar Alvarez's 2015 stop-loss study (Better System Trader ep. 37 / IFTA 2016) is referenced but its numbers were not retrievable (IFTA page 404; podcast page has no figures).

---

## KQ2. Is a small target (1× ATR) with a wide stop (2.5× ATR) sound? Required win rate; what real scalping systems achieve

### Takeaway
A +1R / −2.5R structure needs ≥71.4% win rate before costs and ~75–78% after realistic gold costs; real, independently verifiable intraday systems do not demonstrate sustained win rates that high — the only >80% figures found are vendor EA backtests, a 6× ATR/0.2R gold H1 experiment with PF 1.00, or grid/mean-reversion systems with 36–43% drawdowns. Independent evidence shows the "wide stop / small target" edge over break-even is typically thin (single-digit percentage points).

### Cited Findings
- Break-even win rate formula: BE = 1 / (1 + payoff ratio), payoff = avg win / avg loss. Across 11 profitable EA backtests (FX, gold, Nasdaq, BTC) 7 of 11 had actual win rate less than 10 percentage points above break-even (margins 6.7–9.2 pts). The high-win-rate examples: "Gold King Breakout Grid" 83.06% win, payoff 0.95, BE 51.2%, PF 4.67 but 42.75% max equity DD; "Audcad Peace Mean Reversion" 74.11% win, payoff 0.54, BE 65.1%, margin 9.0 pts, DD 36.2%. — [MQL5 blog, "In 7 of 11 backtests…"](https://www.mql5.com/en/blogs/post/777458) [vendor's own EAs, survivor set]
- Gold H1 experiments (485-trade log, 2023–2026): 6× ATR stop / 0.2R target → 87.1% win, PF 1.00 (zero edge); 5× ATR / 0.3R → 81% win, PF 1.10. The author abandoned the approach for 4R targets. — [MQL5 blog 773874](https://www.mql5.com/en/blogs/post/773874) [independent backtest]
- FXNX worked example: 3.5× ATR stop (−350 USD) / 1.0× ATR target (+100 USD) at 78% win rate → expectancy +1 USD/trade; an 80% win-rate illustration with 8 × +100 and 2 × −950 nets −110 USD/trade. — [FXNX](https://fxnx.com/en/blog/stop-loss-distance-vs-hit-rate-20-000-trade-study) [hypothetical]
- Van Tharp framework: expectancy = mean R-multiple; "small R makes large R-multiple wins possible, but small stops lower the chance of winning each trade"; commissions and slippage must be included in R. — [The 7 Circles](https://the7circles.uk/?p=32777) [secondary]
- Win rate and return move in opposite directions in a 105,708-trade Russell 1000 exit study (26 years, daily): the exit with the highest win rate (47%) ranked last on return per trade. — [SetupAlpha, trailing vs fixed stop](https://setup4alpha.substack.com/p/trailing-stop-vs-fixed-stop-tested) [independent backtest, equities daily]
- Vendor M1 gold EA with 1.2× ATR stop and 2:1 TP claims 87% win rate (see KQ1) — only achievable in reporting terms because break-even/trailing exits at +20–30 points count as wins. — [MQL5 174156](https://www.mql5.com/en/market/product/174156) [vendor claim]
- "Gold Hunter IA" EA uses 0.25× ATR(7) stop and 0.5× ATR target and advertises "75%+ target win rate" — unverified marketing. — [MQL5 product 173870](https://www.mql5.com/fr/market/product/173870) [vendor claim]

### Inferences
- [derived math] Break-even win rate before costs: target 1R / stop 1R → 50%; 1R/1.5R → 60%; 1R/2R → 66.7%; 1R/2.5R → 71.4%; 1R/3R → 75%. Reverse structures: 2R/1R → 33.3%; 1.5R/1R → 40%.
- [derived math] Gold example (+2.00 USD target, −5.00 USD stop = 1:2.5): with 0.25 USD round-trip cost the net payoff is +1.75 / −5.25 = 1:3.0 → BE win rate 75.0%; with 0.30 USD → 75.7%; 0.35 → 76.4%; 0.45 (spread + slippage) → 77.9%. Expectancy per oz at 0.30 USD cost: 70% win → −0.40 USD; 75% → −0.05; 78% → +0.16; 80% → +0.30; 85% → +0.65. So a 1:2.5 scalp on gold is loss-making below ~76% win rate, and every 1 pt of win rate is worth ~0.07 USD/oz of expectancy.
- The independent evidence (MQL5 777458 margins; gold H1 6×ATR experiment PF 1.00) suggests wide-stop/small-target systems sit within 0–10 pts of break-even, so a 75%+ requirement leaves almost no cushion against the typical ~10 pt live-vs-backtest win-rate haircut suggested in forums ([EliteTrader](https://www.elitetrader.com/et/threads/for-those-experienced-in-system-development.17838/post-258982), anecdotal).
- Structural argument: a 2.5× ATR stop on a 1-minute scalp is usually never hit by a 1-minute move; it is hit after a prolonged adverse drift, i.e. the loss arrives late and large, which is also what turns the ~39% "flat/scratch" population (see KQ4) into the dominant outcome class.

### Gaps
- No independent live or audited track record of a 1×ATR/2.5×ATR M1 gold scalper with its realised win rate was found; all 75–87% figures are vendor backtests.
- No source quantifies win-rate decay from backtest to live for gold M1 EAs.

---

## KQ3. Time stops (10/20/30/60 minutes): do winners come early, does cutting stale trades improve expectancy?

### Takeaway
Rigorous evidence is thin. The only controlled result found (daily equities, 105,708 trades) shows a pure time exit beating price-based trailing/channel exits; a gold H1 test found a 24-bar timeout made results worse; practitioner guidance is to set the time stop just beyond the cluster of winners' time-to-MFE. Claims like "68% of profitable EURUSD scalps finish within 3 minutes" exist but are unsourced.

### Cited Findings
- Russell 1000, 200-day reclaim entry, 26 years, 105,708 trades, daily bars: time-based exit at 126 bars = +0.52%/trade (12-month version +1.03%), beating give-back trailing stop (−0.02%, tight setting −0.14%, t = −3.6) and close-only channel (+0.17%; 5-day version −0.17%, t = −2.5). Author uses the time exit as the control. — [SetupAlpha trailing vs fixed stop](https://setup4alpha.substack.com/p/trailing-stop-vs-fixed-stop-tested) [independent backtest, not intraday]
- XAUUSD H1, 485 trades, 2023–2026: "A 24-bar position timeout made results worse and ships disabled." — [MQL5 blog 773874](https://www.mql5.com/en/blogs/post/773874) [independent backtest]
- Van Tharp (secondary): time stops ("exit after a fixed period without a profit") are "best for short-term trading". — [The 7 Circles](https://the7circles.uk/?p=32777)
- TradesViz method: locate where most winning trades reach MFE (time-to-MFE cluster), set the time stop slightly beyond it; "time-stop + trailing stop is often the optimal combination"; needs ≥50–100 trades of data. — [TradesViz glossary](https://www.tradesviz.com/glossary/time-stop/) [practitioner method, no data]
- Unverified claim: "68% of profitable scalping trades occurred within the first three minutes" on EURUSD in 2024, and 5–10 minute time exits "outperformed price-only stops by 18%" — no data, sample or method disclosed. — [Investortrip](https://www.investortrip.com/articles/forex-brokers/scalping-strategies) [anecdotal/unverified]
- Triple-barrier (López de Prado) implementations: one blog reports on real data 25% stop touches and 40% vertical (time) barrier touches, no expectancy; TradingView tool notes in-sample, no costs, overlapping trades not independent. — [alm0stsurely blog](https://alm0stsurely.github.io/2026/03/05/triple-barrier-labeling/); [TradingView triple-barrier script](https://in.tradingview.com/scripts/management/) [anecdotal]
- Vendor M1 gold EA reports average trade duration 41 seconds for 13,491 trades (break-even/trailing driven). — [MQL5 174156](https://www.mql5.com/en/market/product/174156) [vendor claim]
- EliteTrader (anecdotal): a 1-minute EA that "closed trades within minutes" was exposed as a failure when the backtest window was widened. — [EliteTrader forex scalping thread](https://www.elitetrader.com/et/threads/forex-scalping.167438/post-2474968)

### Inferences
- The two independent tests point in opposite directions (time exit best on daily trend-reclaim equities; timeout harmful on gold H1 with 4R targets). The common thread: a time stop helps when the edge decays quickly after entry (short-horizon mean-reversion) and hurts when the target is far (trend-following with 4R targets). An M1 scalp with a 1× ATR target belongs to the first group, so a time stop is plausible but must be calibrated from own time-to-MFE data, not from a fixed 10/20/30/60-minute convention.
- A time stop also truncates the "late, large" losses implied by a 2.5× ATR stop (KQ2), converting them into smaller scratches; its effect on expectancy therefore depends on the sign of the mean P&L of trades still open at minute N — measurable from the bot's own log.

### Gaps
- No study was found that tests 10 vs 20 vs 30 vs 60-minute time stops on gold or FX M1/M5 with expectancy per variant.
- No source gives a measured time-to-MFE distribution for gold scalps.

---

## KQ4. Trailing stops, move-to-break-even, partial profit taking in scalping — when do they help or destroy expectancy?

### Takeaway
Evidence consistently shows: (i) trailing stops that start at entry or are tight cut winners short (PF 0.98 → 1.06 on removal in one FX test; tight give-back trail −0.14%/trade, t = −3.6, in a 105k-trade equity test); (ii) trailing beats fixed stops only with momentum/trend (Swedish OMX study: 15–20% trailing best; Han–Zhou–Zhu momentum); (iii) break-even stops produced 39% flat exits on gold H1 and no backtest showing benefit was found; (iv) partial profit taking lowered return in every one of ~30 variants on an ASX trend system and Van Tharp calls it "large losses and small profits".

### Cited Findings
**Trailing stops**
- Russell 1000, 105,708 trades, daily, 26 y: give-back-of-profit trailing stop ranked 8th of exits; middle setting −0.02%/trade, tight setting −0.14%/trade (t = −3.6); intraday-low channel +0.24%; shorter lookbacks → more exits, less return; using lows beat closes by ~0.1%/trade. — [SetupAlpha](https://setup4alpha.substack.com/p/trailing-stop-vs-fixed-stop-tested) [independent backtest]
- FX EA backtest (MQL5 blog, instrument unspecified): "The trailing stop was cutting winners short: removing it moved the result from profit factor 0.98 to 1.06." — [MQL5 blog 779410](https://www.mql5.com/en/blogs/post/779410) [independent backtest, small]
- Snorrason & Yusupov 2009, OMX Stockholm 30, Jan 1998–Apr 2009, quarterly entries: trailing stop beat fixed-from-entry stop at every level above 10%; best trailing 15% (cumulative 73.9%) / 20% (mean quarterly 1.71%) vs buy-and-hold 1.29% cumulative; 5% trailing was the only variant below buy-and-hold (−8.14%). — [Quant-Investing summary](https://www.quant-investing.com/blog/truths-about-stop-losses-that-nobody-wants-to-believe); [Lund University record](https://www.lunduniversity.lu.se/publication/1474565) [academic thesis]
- Han, Zhou & Zhu, "Taming Momentum Crashes", US stocks 1926–2011, monthly momentum with 10% stop: mean monthly return 1.01% → 1.73%, monthly SD 6.07% → 4.67%, Sharpe 0.166 → 0.371, worst month −49.8% → −11.3% (EW). — [Quant-Investing summary](https://www.quant-investing.com/blog/truths-about-stop-losses-that-nobody-wants-to-believe); [Quantpedia #249](https://vvv.quantpedia.com/?p=558) [peer-reviewed, monthly equities]
- Lei & Li 2009: trailing stop is the one mechanism that reduces risk, but returns indistinguishable from buy-and-hold. — [CXO Advisory](https://www.cxoadvisory.com/technical-trading/using-trailing-stop-losses-to-reduce-risk) [peer-reviewed]
- Practitioner guidance: start trailing only after +1R to +2R; trailing from entry is "a leading cause of premature exits"; trailing beats fixed targets mainly in trending regimes (Hurst > ~0.55, unsourced). — [IQ Option blog](https://blog.iqoption.com/en/how-to-use-trailing-stops-without-killing-your-winners/) [anecdotal]
- Van Tharp (secondary): profit-retracement trail starting at 30% retracement after 2R, tightening to 25% at 3R, 20% at 4R, 5% at 7R; "too tight a trailing stop can reduce the chance of an eventual profit". — [The 7 Circles](https://the7circles.uk/?p=32777)
- Vendor M1 gold EA: trailing starts at +30 points (0.30 USD) in 10-point steps, break-even at +20 points — produces 41-second average trades and 87% "win rate" (see KQ1/KQ2 caveats). — [MQL5 174156](https://www.mql5.com/en/market/product/174156) [vendor claim]

**Break-even stops**
- XAUUSD H1, 485 trades, 2023–2026: BE stop moved to entry at ~+0.6R; outcome distribution: 39.0% break-even scratches (within ±0.05R), 25.2% full stop (≤ −0.9R), 11.7% partial loss, 21.2% reached ≥ +1R. Author identifies the BE stop as "a major source of flat exits" on gold, which retraces after +0.6R. PF with vs without BE not isolated. — [MQL5 blog 773874](https://www.mql5.com/en/blogs/post/773874) [independent backtest]
- NexusFi poster: "I have not yet seen a backtest, where the breakeven stop did do any good" — calls it a psychological tool. — [NexusFi](https://nexusfi.com/showthread.php?p=355842) [anecdotal]
- Podcast (Line Your Own Pockets): moving to break-even "is more about psychological comfort" and "probably a mistake"; even a simple BE rule makes backtests considerably more complicated. — [Transistor.fm episode](https://share.transistor.fm/s/674a967a) [anecdotal]
- ATAS: lists pros (protection from flash spikes) and cons (lower effective win rate) without data. — [ATAS blog](https://atas.net/blog/break-even-in-trading/) [anecdotal]
- Spread-widening mechanics on gold: a BE/tight stop on a sell can be hit by the Ask alone during spread blow-outs; measured XAUUSD spread: time-weighted mean 40.1 pts, median 40, P90 41, max 405 pts (~62 min after rollover), 4.83 M ticks over 20 days, one broker. — [MQL5 blog 779382](https://www.mql5.com/en/blogs/post/779382) [independent measurement]

**Partial exits / scaling out**
- ASX liquid stocks, trend-following, 1,432 trades, daily: base 21.6% return, 41.7% max DD, MAR 0.52; every partial-profit variant (6 target widths × 5 reduction levels) had lower return and lower drawdown; MAR equal or worse in all but one (75% target, 100% reduction). — [Enlightened Stock Trading](https://enlightenedstocktrading.com/trend-following-profit-targets-do-they-work) [independent backtest, author has a stated bias]
- Van Tharp (secondary): scaling out "produces large losses and small profits" — full size on the worst trades, minimal size on the best. — [The 7 Circles](https://the7circles.uk/?p=32777); forum confirmation that Tharp is "adamantly against scaling out" — [NexusFi](https://nexusfi.com/showthread.php?p=181754) [secondary]
- Counter-example (walk-forward, 6 windows, instrument/timeframe not retrieved): fixed stop/target lost in 3 of 6 windows; partial exit at 2R + trail on MA21 passed all, attributed to lower return variance; both passed under same-day fill assumptions. — [MOEasymmetry](https://moeasymmetry.netlify.app/articles/the-sell-rule-that-turned-a-failing-system-into-a-passing-one) [anecdotal; page 404 on fetch, details from search snippet only]
- LuxAlgo: net expectancy of scaling out "can go either way; it depends on how often price extends beyond your first target". — [LuxAlgo scaling out](https://www.luxalgo.com/library/concept/scaling-out/) [practitioner]
- Gold H1 485-trade study kept a partial exit at a multiple of R (fraction not stated) alongside 4R targets. — [MQL5 773874](https://www.mql5.com/en/blogs/post/773874)

### Inferences
- For an M1 scalp with a 1× ATR target, there is almost no room for a trail or partial: the whole target is ~1 ATR, so any trail that activates before target is effectively a tighter stop, and the evidence says tighter stops lower expectancy. The exits that "helped" in the literature (15–20% trailing on quarterly stocks, 2R-activated trails) all activate at multiples of risk that a 1R-target scalp never reaches.
- Break-even on gold is specifically dangerous because gold routinely retraces +0.6R and because the spread (median 0.40 USD at the broker measured above, 0.20–0.30 on raw accounts) alone can touch a stop sitting at entry. Expect ~30–40% of trades to become scratches that still pay full spread.
- Partial exits reduce drawdown but trade away return; on a 1R target they also halve the only payoff that makes a 75% win-rate structure viable (KQ2), so they are net negative unless the second half has a documented positive expectancy beyond 1R.

### Gaps
- No controlled A/B backtest of break-even vs no break-even on gold M1/M5 with PF/expectancy for both arms was found.
- No study isolating partial exits on intraday FX/gold was found; the only quantified partial-exit test is ASX daily trend following.

---

## KQ5. Spread + slippage erosion of a 2 USD gold target at 0.25 USD spread; minimum target/spread ratio

### Takeaway
A 0.25 USD spread is 12.5% of a 2 USD target before slippage; realistic all-in cost (spread 0.20–0.40 + slippage) is 15–22% of the target and pushes the break-even win rate of a 1:2.5 structure from 71.4% to 75–78%. No authoritative minimum ratio exists; practitioner examples treat ≥10:1 target/cost (≤10–12% erosion) as acceptable and ≤3:1 (35–60% erosion) as unworkable.

### Cited Findings
- Spread share of target worked example (XAUUSD): 35¢ spread vs 1.00 USD target = 35% of edge; vs 3.00 USD = ~12%; vs 10 USD = ~3%. Typical raw-account spread 10–35¢, standard account 30–50¢; rollover (≈21:00–23:00 GMT) spreads jump 3–10×; NFP/CPI/FOMC "several dollars" for minutes; at 0.10 lots a 35¢ spread costs ~3.50 USD/trade, 4 trades/day ≈ 70 USD/week. — [dev.to XAUUSD spread](https://dev.to/xauusdrobot/the-xauusd-spread-the-hidden-cost-that-quietly-decides-your-trading-results-574d) [practitioner]
- FX analogue: 3-pip spread is 60% of a 5-pip target but manageable on 18 pips (17%). — [ztrade University](https://ztradeuniversity.pages.dev/best-gold-scalping-strategy-2025) [practitioner]
- Measured XAUUSD spread, one broker, 4.83 M ticks, 20 days (Oct–Nov 2026): time-weighted mean 40.1 points, median 40, P90 41, max 405 points ~62 min after rollover; "a wider spread does not automatically trigger a stop loss" but the Ask can reach a sell's SL while Bid is stable. — [MQL5 blog 779382](https://www.mql5.com/en/blogs/post/779382) [independent measurement]
- Typical ECN XAUUSD spreads: 0.10–0.30 USD in high-liquidity hours ([MQL5 forum](https://www.mql5.com/en/forum/504525)); Exness ECN 0.20–0.40 USD typical in a Q1 2026 sample ([GCTSI report](https://gctsi.pages.dev/reports/q1-2026)); "average spread 10:00–16:00 UTC up to 20 cents", broker StopLevel up to 30 cents ([MQL5 blog 776956](https://www.mql5.com/en/blogs/post/776956)). [broker/practitioner data; units inconsistent across sources]
- Slippage: no measured averages found. One worked example assumes 4 points round-trip; anecdotal stop-entry fills 30–50 pips worse than requested on an MT5 report; news-time example of 0.80 USD negative slippage on a 15-point stop (unsourced). — [MQL5 search summary sources](https://www.mql5.com/en/blogs/post/776956) [anecdotal]
- Brokers quote minimum/average, almost never maximum spread; max can reach 100+ points. — [Spread Gold Radar, MQL5](https://www.mql5.com/tr/market/product/182354) [vendor]
- Commission: 7 USD round-turn per standard lot ≈ 0.7 pips on gold. — [Capital Crossing blog](https://capitalcrossing.blogspot.com/2026/06/mt4-broker-with-lowest-spread-for.html) [practitioner]
- FXNX friction table (FX): 1.5 pips friction = 30% of a 5-pip stop, 5% of a 30-pip stop; R:R 1:3 → 1:2.07 on 5-pip stop. — [FXNX](https://fxnx.com/en/blog/stop-loss-distance-vs-hit-rate-20-000-trade-study) [broker]
- Vendor gold M1 EA backtested with a 100-point max spread filter but recommends 30 points live — a 3.3× gap between tested and advised conditions. — [MQL5 174156](https://www.mql5.com/en/market/product/174156)
- "Quick Gold Scalper" testing note: spread expands, fills deteriorate and an ATR exit widens together in volatile periods, so ATR stops must be re-checked under expanded spread. — [MQL5 blog 776595](https://www.mql5.com/en/blogs/post/776595) [practitioner]

### Inferences
- [derived math] On a +2.00 / −5.00 USD gold scalp: cost 0.25 → 12% of target, net R:R 1:3.00, BE 75.0%; cost 0.30 → 15%, BE 75.7%; cost 0.35 → 18%, BE 76.4%; cost 0.45 → 22%, BE 77.9%. Costs raise the break-even win rate by ~4–6.5 pts versus the frictionless 71.4%.
- [derived] With the measured median spread of 0.40 USD (that broker) a 2 USD target has 20% erosion before slippage; on a 0.20–0.30 raw spread it is 10–15%. A 1 USD target is 25–40% eroded and should be treated as unviable; a 3 USD target brings erosion to ~8–12%.
- Practitioner thresholds that appear implicitly across sources: ≤10–12% cost/target ("intraday" examples) acceptable, 30%+ ("scalping" examples) unacceptable; the synthesis "cost ≤ 10–20% of target" is this researcher's reading, not a published rule.
- Because gold spread has a hard floor (~0.20 USD raw) while ATR(M1) varies 0.5–3 USD intraday, the cost/target ratio of a 1× ATR target swings from ~7% in volatile hours to ~40%+ in quiet Asian hours — the ATR-minimum entry filters used by vendor EAs (skip if ATR(14) < 0.40 USD) are a response to this.

### Gaps
- No published measurement of average gold slippage on market/stop orders by hour was found; the figures above are assumptions or anecdotes.
- No authoritative "minimum target-to-spread ratio" standard exists; all thresholds are practitioner heuristics.
- Spread figures across sources mix "points", "pips" and "cents" with 2- vs 3-decimal quotes; the 40-point measurement (MQL5 779382) is most likely 0.40 USD on a 2-decimal feed but the article does not state the point value.
