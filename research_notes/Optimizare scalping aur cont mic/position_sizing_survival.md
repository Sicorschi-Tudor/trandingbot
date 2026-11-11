# Position sizing and survival mathematics for a ~100 USD gold-scalping account

Scope: fixed-fractional vs Kelly sizing, risk-of-ruin / drawdown probabilities for a +1 / −2.5 payoff at 55–80% win rates, the real "0.02–0.04 lot on 105–130 USD" case, margin at 1:200 on gold ≈ 4000 USD, fixed API costs of 5–8 USD/day, and practitioner minimum-capital guidance.

Assumptions used throughout (from the assignment): 0.01 lot XAUUSD = 1 oz = 1 USD P/L per 1 USD move; average win ≈ +1 ATR ≈ +2 USD per 0.01 lot; average loss ≈ −2.5 ATR ≈ −4 to −5 USD per 0.01 lot; 5–15 trades/day; broker minimum 0.01 lot; leverage 1:200; independent, identically distributed trades (no serial correlation, no slippage beyond what is already in the averages).

Convention: 1 "unit" = the average loss (1R in Van Tharp's language). Win = +0.4R, loss = −1R, i.e. payoff b = 0.4. Equivalently, in "win units": win +1, loss −2.5.

All numbers labelled **[own computation]** come from the script saved alongside these notes (`position_sizing_sim.py`, pure Python, 5,000 Monte Carlo paths per cell, seed 7; sampling error at 5,000 paths is roughly ±1.4 percentage points at p = 0.5). The formulas they implement are cited from Thorp and Whelan below so they can be re-derived.

---

## Key Question 1 — Expectancy, Kelly fraction, and the probability of ruin / 50% drawdown within 200 trades at 2%, 5%, 10% risk per trade (payoff b = 0.4)

### Takeaway
At a +1 / −2.5 payoff the break-even win rate is 71.4%; every win rate in the 55–70% band therefore has **negative** expectancy and a **negative** Kelly fraction (the mathematically correct size is zero), and Monte Carlo confirms that at 60% win rate even 2% risk per trade gives a ~50% chance of a 50% drawdown within 200 trades. Only above ~72–75% win rate does the system have a positive edge, and even then risk above ~5% per trade makes a 50% drawdown likely within 200–500 trades.

### Cited Findings

**Formulas (sourced)**

- Kelly, even-money case: the growth rate is g(f) = p·log(1+f) + q·log(1−f); setting g′(f) = 0 gives f* = p − q; g(f) has a unique maximum at f* and a unique break-even fraction f_c > f* where g(f_c) = 0, beyond which "eventual downward fluctuations will inexorably drive the values of X_n toward zero" — [Thorp 2006, "The Kelly Criterion in Blackjack, Sports Betting, and the Stock Market", pp. 388–391](https://www.gwern.net/doc/statistics/decision/2006-thorp.pdf)
- Kelly, uneven payoff (win b per unit wagered): g(f) = p·log(1+bf) + q·log(1−f); "Arguments using calculus yield f* = (bp − q)/b". Generalisation when a unit wager wins b with probability p and loses a with probability q: with expectation m ≡ bp − aq > 0, f* = m/(ab). Thorp explicitly defends this generalisation for leveraged instruments where one "can lose much more than the amount bet" (futures, short sales) — [Thorp 2006, p. 391](https://www.gwern.net/doc/statistics/decision/2006-thorp.pdf)
- Same formula in Wikipedia's notation: f* = p/l − q/g where l is the fraction lost on a loss and g the fraction gained on a win; derived by maximising p·log(1+fg) + q·log(1−fl); expected geometric growth rate per bet r = (1+fg)^p · (1−fl)^q — [Wikipedia, Kelly criterion](https://en.wikipedia.org/wiki/Kelly_criterion)
- Ralph Vince's "optimal f" for a two-outcome game is quoted as f = ((B+1)·P − 1)/B with B = payoff ratio and P = win probability, which is algebraically identical to Kelly's (bp − q)/b — [Wealth-Lab forum "Risk of Ruin"](https://wl6.wealth-lab.com/Forum/Posts/Risk-of-Ruin-36671)
- Break-even win rate: WR_be = 1/(1 + R) where R = reward/risk ratio; for R = 1:0.5 (i.e. win half the loss) the break-even is ≈ 66.7%; costs push the real break-even higher — [MQL5, "Risk/Reward vs Win Rate: The Breakeven Line"](https://www.mql5.com/en/blogs/post/779062); [TradeZella, Risk-Reward Ratio](https://www.tradezella.com/blog/risk-reward-ratio); [DayTradingToolkit](https://daytradingtoolkit.com/beginners-guide/win-rate-vs-risk-reward-expectancy)
- Expectancy in R-multiples (Van Tharp): expectancy = win rate × average win (R) − loss rate × average loss (R); R = initial risk defined by the stop — [NexusFi, R-multiples](https://nexusfi.com/a/risk-management/r-multiples)
- Fractional Kelly drawdown probabilities (continuous/diffusion approximation): Prob(V(t, c·f*) reaches y·V0 before x·V0) = [1 − x^a] / [1 − (x/y)^a], where a = 2M/V, M and V being the drift and variance per unit time of log-wealth (Thorp eq. 7.12, derived from the classical gambler's-ruin formula in Cox & Miller 1965 by passing to the diffusion limit). Letting y → ∞: Prob(V(t, c·f*)/V0 ≤ x for some t) = x^(2/c − 1) (eq. 7.13). For full Kelly (c = 1) the chance of ever losing half the capital is 1/2; for half Kelly (c = 1/2) it is x³ = 1/8; half Kelly "has 3/4 the growth rate but much less chance of a big loss"; Prob(doubling before halving) = 2/3 at full Kelly and 8/9 at half Kelly — [Thorp 2006, §7, pp. 414–415](https://www.gwern.net/doc/statistics/decision/2006-thorp.pdf)
- Thorp's "case for fractional Kelly": because the edge estimate m_e is uncertain, "it is wise to assume m_t < m_e and to choose f < f_e* by enough to prevent g ≤ 0"; choosing f = f_e* when the true edge is half the estimate gives g = 0 ("increasingly wild oscillations"), and f = 1.5·f_e* with a 2× overestimated edge gives g < 0 ("we will be ruined") — [Thorp 2006, §7.3, p. 411](https://www.gwern.net/doc/statistics/decision/2006-thorp.pdf)
- Exact (discrete) gambler's ruin with asymmetric payoffs: for a walk that gains K units with probability p and loses 1 unit with probability 1−p, the difference equation p·P_{n+K} − P_n + (1−p)·P_{n−1} = 0 has characteristic equation p·r^(K+1) − r + (1−p) = 0 with K+1 roots, one of which is 1; ruin/target probabilities are P_n = Σ A_i r_i^n with coefficients fixed by boundary conditions (Harper & Ross 2005 matrix method) — [Whelan 2025, "Ruin Probabilities for Strategies with Asymmetric Risk", §3.1, eqs. 20–24](https://www.karlwhelan.com/Papers/Ruin.pdf)
- Whelan's headline result (positive-EV case): an investor risking 1% of capital per trade with 1% expected return per trade, targeting a tripling of wealth, has a 13% ruin probability with symmetric payoffs; with a 2:1 payoff ruin rises to 34%; with 20:1 it is 64%. "Positive expected value" is not the same as "safe"; variance of the per-trade outcome is what drives ruin — [Whelan 2025, Introduction](https://www.karlwhelan.com/Papers/Ruin.pdf); [summary page](https://www.karlwhelan.com/sports-betting-gamblers-ruin-asymmetric-odds/)
- Whelan on stake size: when p > 0.5 "increasing s [stake] reduces the probability of success and raises the probability of ruin. To minimize the chance of ruin when the game has a positive expected profit, the best strategy is to repeatedly make small bets"; the reverse holds for negative-EV games (bold play) — [Whelan 2025, §5](https://www.karlwhelan.com/Papers/Ruin.pdf)
- Kaufman's even-payoff shortcut: Risk of ruin = ((1 − Edge)/(1 + Edge))^CapitalUnits, valid only for 1:1 payoffs, fixed bet size and independent trades; sources disagree on whether "Edge" means p or p − q (only p − q reproduces the textbook (q/p)^n result) — [NexusFi, Risk of Ruin](https://nexusfi.com/a/risk-management/risk-of-ruin); [JournalPlus](https://journalplus.co/learn/glossary/risk-of-ruin). I did not find a reliable online copy of Vince's uneven-payoff ruin formula; one forum commenter reports it "can give misleading results at extreme payoff ratios" — [Wealth-Lab forum](https://wl6.wealth-lab.com/Forum/Posts/Risk-of-Ruin-36671)

**Worked numbers for b = 0.4 (win +1, lose −2.5)** — [own computation] applying the Thorp formulas above:

| p | q | Expectancy (win units) = p − 2.5q | Expectancy in R | Kelly f* = p − q/0.4 = p − 2.5q | half Kelly |
|---|---|---|---|---|---|
| 0.55 | 0.45 | −0.575 | −0.23R | −0.575 (do not trade) | — |
| 0.60 | 0.40 | −0.400 | −0.16R | −0.400 | — |
| 0.65 | 0.35 | −0.225 | −0.09R | −0.225 | — |
| 0.70 | 0.30 | −0.050 | −0.02R | −0.050 | — |
| **0.7143** | 0.2857 | **0 (break-even)** | 0 | 0 | 0 |
| 0.72 | 0.28 | +0.020 | +0.008R | +0.020 (2.0% of equity at risk) | 1.0% |
| 0.75 | 0.25 | +0.125 | +0.05R | +0.125 (12.5%) | 6.25% |
| 0.80 | 0.20 | +0.300 | +0.12R | +0.300 (30%) | 15% |

Derivation check: Kelly with a = 1 (lose 1R), b = 0.4 (win 0.4R): f* = (bp − aq)/(ab) = (0.4p − q)/0.4 = p − 2.5q. Break-even: p = 2.5/(1 + 2.5) = 0.7143. If the realised loss is 4 USD vs 2 USD win (payoff 0.5), break-even is 4/6 = 0.667 and f* = p − 2q.

**Monte Carlo, fixed-fractional risk r of current equity per trade (loss = r, win = 0.4r), 200 trades, 5,000 paths** — [own computation]:

| p | r | P(max DD ≥ 30%) | P(max DD ≥ 50%) | P(equity ≤ 20% of start) | P(final < start) | median final / start |
|---|---|---|---|---|---|---|
| 0.55 | 2% | 0.999 | 0.924 | 0.000 | 1.000 | 0.39 |
| 0.55 | 5% | 1.000 | 1.000 | 0.962 | 1.000 | 0.09 |
| 0.55 | 10% | 1.000 | 1.000 | 1.000 | 1.000 | 0.01 |
| 0.60 | 2% | 0.977 | 0.508 | 0.000 | 1.000 | 0.52 |
| 0.60 | 5% | 1.000 | 0.996 | 0.632 | 1.000 | 0.18 |
| 0.60 | 10% | 1.000 | 1.000 | 0.990 | 1.000 | 0.02 |
| 0.65 | 2% | 0.722 | 0.087 | 0.000 | 0.979 | 0.69 |
| 0.65 | 5% | 0.995 | 0.897 | 0.128 | 0.986 | 0.36 |
| 0.65 | 10% | 1.000 | 0.996 | 0.816 | 0.987 | 0.10 |
| 0.70 | 2% | 0.197 | 0.001 | 0.000 | 0.710 | 0.91 |
| 0.70 | 5% | 0.888 | 0.429 | 0.005 | 0.751 | 0.74 |
| 0.70 | 10% | 0.999 | 0.935 | 0.278 | 0.806 | 0.44 |
| 0.75 | 2% | 0.012 | 0.000 | 0.000 | 0.143 | 1.20 |
| 0.75 | 5% | 0.500 | 0.062 | 0.000 | 0.182 | 1.50 |
| 0.75 | 10% | 0.985 | 0.611 | 0.020 | 0.232 | 1.85 |
| 0.75 | 17% | 1.000 | 0.979 | 0.196 | 0.342 | 1.74 |
| 0.80 | 2% | 0.000 | 0.000 | 0.000 | 0.002 | 1.59 |
| 0.80 | 5% | 0.121 | 0.002 | 0.000 | 0.005 | 3.05 |
| 0.80 | 10% | 0.851 | 0.180 | 0.000 | 0.007 | 7.85 |
| 0.80 | 17% | 1.000 | 0.789 | 0.012 | 0.018 | 21.6 |

500 trades (same model): p = 0.70, r = 2% → P(DD ≥ 50%) = 0.134, median final 0.79; p = 0.75, r = 5% → P(DD ≥ 50%) = 0.213; p = 0.75, r = 10% → 0.926; p = 0.80, r = 5% → 0.006; p = 0.80, r = 10% → 0.408 — [own computation].

**Closed-form check (Thorp eq. 7.13 diffusion approximation, infinite horizon)** with M = p·ln(1+0.4r) + q·ln(1−r), V = p·q·[ln(1+0.4r) − ln(1−r)]², a = 2M/V, P(ever fall to x·V0) = x^a — [own computation]:

| p | r | M (drift/trade) | a | P(ever −30%) | P(ever −50%) | P(ever −80%) |
|---|---|---|---|---|---|---|
| 0.72 | 2% | +0.00008 | 1.00 | 0.70 | 0.50 | 0.20 |
| 0.72 | ≥5% | ≤ 0 | — | ruin certain (g ≤ 0; above f_c) | | |
| 0.75 | 2% | +0.00093 | 12.4 | 0.012 | 0.000 | 0.000 |
| 0.75 | 5% | +0.00203 | 4.28 | 0.217 | 0.051 | 0.001 |
| 0.75 | 10% | +0.00308 | 1.57 | 0.571 | 0.337 | 0.080 |
| 0.75 | 17% | +0.00276 | 0.46 | 0.848 | 0.726 | 0.475 |
| 0.80 | 5% | +0.00558 | 13.8 | 0.007 | 0.000 | 0.000 |
| 0.80 | 10% | +0.01030 | 6.16 | 0.111 | 0.014 | 0.000 |
| 0.80 | 17% | +0.01536 | 3.02 | 0.340 | 0.123 | 0.008 |

Note the closed form is for an unlimited horizon so it is an upper bound on the 200-trade Monte Carlo numbers above (e.g. p = 0.75, r = 10%: 0.34 lifetime vs 0.61 in 200 trades — the discrepancy is because the diffusion approximation understates jump risk when single steps are large relative to equity; Thorp notes the approximation is for small step sizes, [Thorp 2006 p. 414](https://www.gwern.net/doc/statistics/decision/2006-thorp.pdf)).

**Independent Monte Carlo evidence on risk-per-trade bands** (different payoff, same lesson): 20,000 simulated accounts, 45% win rate, 2:1 reward:risk (+0.35R expectancy), 200 trades, full Kelly = 17.5%: P(drawdown ≥ 50%) = 0% at 1% and 2% risk, 19% at 5%, 94% at 10%, 100% at 15% and above; median max drawdown 9% / 18% / 41% / 67% / 84% for 1 / 2 / 5 / 10 / 15%; the author concludes "1–2% isn't timid" and that 25% is past the point where over-betting lowers even the median — [dev.to, "I ran 20,000 Monte Carlo simulations…"](https://dev.to/nomad_paolo/i-ran-20000-monte-carlo-simulations-to-find-the-best-position-size-the-average-result-is-a-trap-5ed4)
- Bryant's method (Technical Analysis of Stocks & Commodities, 2001): shuffle the trade sequence many times, recompute max drawdown at each fixed-fractional risk level; e.g. 940 of 1,000 shuffled sequences at 5% risk staying under 25% drawdown implies a 94% probability of staying under that drawdown — [Traders.com, Bryant abstract](https://www.traders.com/Documentation/FEEDbk_docs/2001/02/Abstracts_new/Bryant/Bryant.html) (page returned HTTP 403 on direct fetch; wording taken from the search summary)

### Inferences
- For the stated system (55–65% win rate, +1/−2.5 payoff) the Kelly answer is "bet zero": f* < 0. Position sizing cannot rescue a negative-expectancy system; the Monte Carlo "P(final < start)" column is 98–100% at p ≤ 0.65 regardless of r. Survival time is extended by smaller size but the terminal outcome is a loss.
- At p = 0.70 the system is essentially break-even (−0.02R); 2% risk gives a 20% chance of a 30% drawdown in 200 trades and a 71% chance of ending below start.
- Even when the edge is real (p = 0.75), full Kelly (12.5%) gives ≈ 60–70% chance of a 50% drawdown in 200 trades, half Kelly (6.25%) ≈ 10%, 2% ≈ 0%. Thorp's rule of thumb that half Kelly has 1/8 lifetime chance of halving (vs 1/2 at full Kelly) is consistent with these numbers.
- Because the difference between 70% and 75% win rate is the difference between "slow bleed" and "healthy edge", the win rate estimate must be measured on a large sample (hundreds of trades) before any sizing decision is meaningful; Thorp's §7.3 says to assume the true edge is below the estimate.

### Gaps
- I could not access Ralph Vince's uneven-payoff risk-of-ruin formula in primary form (Portfolio Management Formulas / Mathematics of Money Management are not freely online); the values above use Thorp's diffusion formula and direct Monte Carlo instead.
- Kaufman's risk-of-ruin tables (Trading Systems and Methods) are not reproduced in any reliable online source I found; the FXStreet "tables" returned by search are garbled.
- The Monte Carlo assumes independent trades; scalping systems typically show serial correlation of losses around news/volatility, which would worsen every drawdown figure above.

---

## Key Question 2 — Minimum win rate for break-even at payoff 1:2.5 and practitioners' margin of safety

### Takeaway
Break-even is WR = L/(W+L) = 2.5/3.5 = **71.4%** gross (66.7% if the loss is 2× the win); practitioners (Thorp, Elder, Monte Carlo authors) do not quote a fixed win-rate "margin", but they size at ½ Kelly or less and cap risk at 1–2% precisely because the measured edge is uncertain — Thorp's criterion is to choose f small enough that even if the true edge is half the estimate, growth stays positive.

### Cited Findings
- Break-even win rate = 1/(1+R) with R = average win / average loss; a 1:0.5 ratio (win half the loss) needs ≈ 66.7%; the formula ignores fees, so real break-even is higher — [MQL5, Breakeven Line](https://www.mql5.com/en/blogs/post/779062); [TradeZella](https://www.tradezella.com/blog/risk-reward-ratio)
- For R = 0.4 (our case): 1/(1 + 0.4) = 0.7143 — [own computation from the cited formula]
- Thorp on margin of safety: "To the extent m_e is an uncertain estimate of m_t, it is wise to assume m_t < m_e and to choose f < f_e* by enough to prevent g ≤ 0"; half Kelly is what "most people strongly prefer" in exchange for giving up 1/4 of growth — [Thorp 2006 §7.3, §7, pp. 411–415](https://www.gwern.net/doc/statistics/decision/2006-thorp.pdf)
- Thorp on the Kelly game at p = 0.53 (even money): f* = 6%, but any f above f_c ≈ 12% leads to eventual ruin, and the expected time to double at f* is ≈ 385 bets — [Thorp 2006, Example 2.1, p. 391](https://www.gwern.net/doc/statistics/decision/2006-thorp.pdf)
- Elder's 2% rule caps the loss on any single trade at 2% of equity; the 6% rule stops trading for the month once realised monthly losses plus open risk reach 6% of equity ("the 2% Rule will save your account from shark bites and the 6% Rule from piranhas") — [IncredibleCharts, Elder's 6 Percent Rule](https://www.incrediblecharts.com/trading/6_percent_rule.php); [TD Ameritrade Ticker Tape](https://tickertape.tdameritrade.com/trading/rules-to-manage-risk-15908)
- Van Tharp's framework: define 1R as the initial stop-loss risk, size so that 1R equals a chosen % of equity (commonly 1%), and evaluate the system as expectancy in R — [NexusFi, R-multiples](https://nexusfi.com/a/risk-management/r-multiples)
- Monte Carlo practitioners choose the largest risk whose 95th-percentile drawdown stays within tolerance, running 0.25%–2% per trade — [NexusFi, Monte Carlo simulation](https://nexusfi.com/a/risk-management/monte-carlo-simulation)
- Whelan: with positive EV, "smaller stakes reduce ruin risk… echoing the logic of the Kelly criterion"; the required safety margin increases with payoff variance — [Whelan 2025](https://www.karlwhelan.com/Papers/Ruin.pdf)

### Inferences
- Applying Thorp's "assume the true edge is half the estimate" rule: if the measured win rate is 75% (edge 0.125 win-units), you should size as if the edge were 0.0625, i.e. as if p ≈ 73.2% — an f of about 6% at most, and lower still after costs (see Q3).
- A practical safety margin consistent with the cited sizing rules is: do not trade the system live until the measured win rate exceeds the cost-adjusted break-even (≈ 75–80% at 0.01 lot, see Q3) by a margin that is statistically significant on the sample. With n = 200 trades the standard error of a win-rate estimate near 0.72 is √(0.72·0.28/200) ≈ 3.2 percentage points, so a measured 75% is less than one standard error above break-even [own computation, binomial standard error].

### Gaps
- No source gives an explicit "required margin above break-even win rate" number for scalpers; the field expresses safety as fractional Kelly or % risk, not as win-rate margin.

---

## Key Question 3 — Effect of fixed daily costs (API ≈ 5–8 USD/day) on the required edge for a 100 USD account

### Takeaway
5–8 USD/day is 5–8% of the account per day and 110–176% per month; at 10 trades/day it is 0.50–0.80 USD per trade, which at 0.01 lot raises the break-even win rate from 71.4% (gross) to ≈ 79–83%, and at 0.02 lot to ≈ 75–77%. No published sizing source contemplates fixed costs of this magnitude relative to capital; the only rule of thumb found caps total trading costs at 5% of capital per year.

### Cited Findings
- Fixed per-trade costs are "disproportionately expensive" for small tickets (example: a 5 USD fee on a 100 USD trade is 5% of capital immediately) — [TradersPost, Commission Impact](https://blog.traderspost.io/article/commission-impact-trading-strategies)
- Fees raise the break-even win rate: in one worked example a 0.5% round-trip fee shifts break-even from a 50% to a 56% win rate at even payoff — [TradingView, "Beating the rake"](https://br.tradingview.com/chart/BTCPERP/D3CuGUK2-Beating-the-rake-Know-your-trading-fees)
- Rule of thumb: commission rate × trades per year "should not exceed 5% of your capital" — [The Chartist](https://www.thechartist.com.au/?p=5762)
- Break-even formula excludes fees; "costs effectively raise the breakeven win rate" — [MQL5, Breakeven Line](https://www.mql5.com/en/blogs/post/779062)
- Thorp: a fixed cost or over-estimated edge can push g(f) ≤ 0, at which point ruin is certain regardless of size — [Thorp 2006 §7.3](https://www.gwern.net/doc/statistics/decision/2006-thorp.pdf)

**Worked numbers** — [own computation]:

- Cost per trade c = C/k: C = 5 USD/day → 1.00 / 0.50 / 0.33 USD per trade at 5 / 10 / 15 trades/day; C = 8 USD/day → 1.60 / 0.80 / 0.53 USD per trade.
- Monthly (22 trading days): 110 USD (C=5) to 176 USD (C=8) = 110–176% of a 100 USD account per month. The account must earn 5–8% per day just to stand still.
- Required win rate for zero net after cost: p_req = (L + c)/(W + L), from p·W − (1−p)·L − c = 0.

| lot | W (USD) | L (USD) | c = 0.50 | c = 0.80 | c = 1.60 |
|---|---|---|---|---|---|
| 0.01 | 2 | 4 | 0.750 | 0.800 | 0.933 |
| 0.01 | 2 | 5 | 0.786 | 0.829 | 0.943 |
| 0.02 | 4 | 8 | 0.708 | 0.733 | 0.800 |
| 0.02 | 4 | 10 | 0.750 | 0.771 | 0.829 |
| 0.04 | 8 | 16 | 0.688 | 0.700 | 0.733 |
| 0.04 | 8 | 20 | 0.732 | 0.743 | 0.771 |

- Required net edge per trade in USD: to cover 5–8 USD/day with 10 trades/day the system must net ≥ 0.50–0.80 USD/trade after spread. At 0.01 lot, with W = 2 and L = 5, the gross expectancy at p = 0.75 is 0.75·2 − 0.25·5 = +0.25 USD/trade, i.e. below the 0.50–0.80 cost per trade; at p = 0.80 it is +0.60 USD/trade, roughly equal to cost. At 0.02 lot the gross expectancy doubles (+0.50 at p = 0.75, +1.20 at p = 0.80) while the cost per trade is unchanged, which is the arithmetic reason a fixed cost "pushes" toward larger lots — at the price of the ruin probabilities in Q5.
- The Chartist's 5%-of-capital-per-year cap, applied to a 100 USD account, allows 5 USD per **year** of total cost; the system's 5–8 USD per **day** exceeds it by a factor of ~250–400.

### Inferences
- Fixed costs of this size turn a marginally positive gross edge into a negative net edge at every win rate up to ≈ 80%; the system needs either a capital base where 5–8 USD/day is ≤ 0.1–0.3%/day (≈ 2,000–8,000 USD) or a cost structure that scales with trades/capital.
- Since Kelly f* = (net edge)/(a·b), subtracting c from every trade lowers f* substantially: at p = 0.75, 0.01 lot, W = 2, L = 5, c = 0.5, the net outcomes are +1.5 / −5.5 USD → b_net = 0.273, f*_net = p − q/b_net = 0.75 − 0.25/0.273 = −0.17 (do not trade); at c = 0 it was +0.125 [own computation].

### Gaps
- No source treats fixed non-trading (API/compute) costs as a fraction of a micro account; all cost literature is about commissions/spreads. The computations above are straightforward arithmetic but have no external validation.

---

## Key Question 4 — Minimum capital for gold scalping at 0.01 lot with 1:200–1:500 leverage (reputable sources), plus margin constraints at 1:200 near 4,000 USD

### Takeaway
Primary sources agree that 0.01 lot = 1 oz (100 oz per lot) and that margin at 1:200 for 0.01 lot near 4,000 USD is ≈ 20 USD, but they stress that leverage changes only the margin, not the dollar risk: a 0.01-lot position now moves ≈ 100–120 USD on an average 2026 day, and an evidence-based practitioner (MQL5) recommends ≥ 1,000 USD (or a cent account) for a single 0.01-lot gold strategy and ≈ 3,800 USD to keep a stressed drawdown under 15%.

### Cited Findings

**Contract and margin facts**
- Exness gold: 1 lot = 100 troy oz, pip size 0.01, minimum lot 0.01, max 200 lots; Standard-account gold margin requirement listed as "1:200 – 1:1000 (0.1%–0.5%)"; hedged margin 0%; available on Standard Cent accounts — [Exness Help Center, Commodities](https://get.exness.help/hc/en-us/articles/17854173039388-Commodities) (page blocked direct fetch with HTTP 403; figures from search summary — verify in terminal)
- Exness Higher Margin Requirements: around news releases and market closures gold leverage drops to "1:100 – 1:1000 (0.1% – 1%)", i.e. margin can double — [Exness Help Center, HMR](https://get.exness.help/hc/en-us/articles/20618327917596-Higher-Margin-Requirements-HMR)
- Exness marketing cites "up to 1:2000" for gold on MT4, which conflicts with the help-centre range above; treat help-centre as authoritative — [Exness XAUUSD page](https://www.exness.com/commodities/xauusd/)
- Exness Standard account: stop-out level 0% (positions closed from the most losing when margin level hits 0%), margin call commonly reported at 60% — [BrokerXplorer](https://www.brokerxplorer.com/article/comparing-standard-and-professional-accounts-in-exness-3500); multiple issuu promotional pages (low quality) e.g. [issuu](https://issuu.com/articles/90340657). Verify on the official account-spec page.
- Margin formula: Margin = Lots × Contract Size × Price / Leverage (e.g. 1 lot gold at 2,650 USD, 1:200 → 1,325 USD) — [MiddleEastFX calculator page](https://middleeastfx-com.pages.dev/artigos/gold-trading-calculator); Margin % = (1/leverage) × 100 = 0.5% at 1:200 — [Exness-branded article (issuu)](https://issuu.com/exness_blog/docs/exness.docx/s/98506981)
- Independent confirmation of 0.01 lot = 1 oz, 1 USD per 1 USD move; margin for 0.01 lot at 4,024 USD: 201 USD at 1:20, 40 USD at 1:100, 8 USD at 1:500 — [MQL5, "What 0.01 Lot of Gold Means Now"](https://www.mql5.com/en/blogs/post/779060)
- Daily high–low range of gold in 2026 (through 22 Oct 2026), expressed on a 0.01-lot position: average day ≈ 120 USD (12.05% of a 1,000 USD account), median ≈ 100 USD; in 2024 the average was ≈ 33 USD; "Higher leverage only lowers the margin, which leaves more free margin to absorb a move"; if the stop-based size cannot be reduced below 0.01 lot, "the account is then too small for that stop distance" — [MQL5, 779060](https://www.mql5.com/en/blogs/post/779060)

**Margin at 1:200, gold 4,000 USD, 100 oz contract, 100 USD account** — [own computation from the cited formula]:

| lot | notional (USD) | margin 1:200 | margin 1:100 (HMR/news) | margin 1:500 | free margin on 100 USD | adverse move to equity 0 | loss that triggers 60% margin call |
|---|---|---|---|---|---|---|---|
| 0.01 | 4,000 | 20 | 40 | 8 | 80 | 100 USD | 88 USD |
| 0.02 | 8,000 | 40 | 80 | 16 | 60 | 50 USD | 76 USD |
| 0.03 | 12,000 | 60 | 120 | 24 | 40 | 33 USD | 64 USD |
| 0.04 | 16,000 | 80 | 160 | 32 | 20 | 25 USD | 52 USD |

Notes: at 0.04 lot the margin (80 USD) is 80% of the account; during an HMR window the required margin (160 USD) exceeds the account, so new 0.04-lot positions cannot be opened and existing ones sit at margin level ≈ 60–80%. The average 2026 daily range (~100–120 USD per 0.01 lot) is 4–5× the entire account's loss capacity at 0.04 lot (25 USD move).

**Minimum-capital guidance**
- Sizing rule: "Account size = the drawdown in dollars you should plan for ÷ the percentage you can live with"; plan for 1.4–1.7× the historical drawdown. Single gold strategy at 0.01 lot: average stop ≈ 18 USD, worst historical losing streak ≈ 138 USD (≈ 14% of 1,000 USD); advice: "$1,000 or more, or on a cent account". Eight-strategy portfolio at 0.01 lot: 95th-percentile stressed drawdown ≈ 574 USD → 95.6% of a 600 USD account, 57.4% of 1,000, 28.7% of 2,000, 14.3% of 4,000; ≈ 3,800 USD needed to keep it under 15%. "With several strategies at 0.01 lots each, a $500 account can't carry a realistic drawdown." — [MQL5, "How Much Money Do You Need to Trade Gold with an EA?"](https://www.mql5.com/en/blogs/post/776723)
- EA vendors list minimum deposits from 100 USD ("recommended $500+") to 1,000 USD for gold scalping EAs — marketing claims, not independent — [Gumroad XAUUSD M5 EA](https://fxhunter.gumroad.com/l/XAUUSDM5SUPER); [MQL5 Market, Gold Pro Scalping EA](https://www.mql5.com/en/market/product/175878)
- A gold position-sizing guide notes that even the minimum 0.01 lot with a 28 USD stop is 2.8% of a 1,000 USD account, i.e. above a 1–2% rule — [BabyPips forum, Gold lot sizes and 1% risk](https://forums.babypips.com/t/gold-lot-sizes-and-1-risk/383662)
- Brokers advertise deposits "as little as $10" — what is possible, not what is prudent — [Exness Standard accounts](https://www.exness.com/standard-accounts/)

### Inferences
- Applying the 1–2% rule to a 4–5 USD average loss at 0.01 lot implies a minimum account of 200–500 USD (loss / 0.02 … loss / 0.01); applying the MQL5 drawdown rule (stressed worst streak ≈ 1.5 × historical, kept under 15–25% of equity) with a plausible 10-loss streak at 5 USD = 50 USD × 1.5 = 75 USD → 300–500 USD. Both put the floor for *one* 0.01-lot gold scalping stream at roughly 300–1,000 USD; 100 USD is 3–10× below it.
- On a 100 USD account at 1:200, 0.01 lot is the only size that leaves meaningful free margin (80 USD) and survives an HMR doubling of margin (40 USD). Anything above 0.02 lot is margin-constrained before it is risk-constrained.

### Gaps
- Exness's own XAUUSDm (Standard-account suffix) specification page could not be fetched (HTTP 403); contract size 100 oz and the 1:200–1:1000 margin range come from the help-centre search summary and should be checked in the MT5 "Specification" dialog. The Exness stop-out (0%) and margin-call (60%) levels come from secondary, partly promotional sources.
- No peer-reviewed or regulator source gives a minimum capital for gold scalping; the best available is a practitioner MQL5 article with explicit stress-test numbers.

---

## Key Question 5 — Risk per trade of 8–17% (0.02–0.04 lot on 105–130 USD): drawdown / ruin probabilities

### Takeaway
At 8–17% risk per trade the literature's answer is unanimous: this is above full Kelly even for a genuinely profitable system (full Kelly for a 45%/2:1 system is 17.5% and gives ≥ 94% chance of a 50% drawdown in 200 trades), and for our payoff it exceeds the ruin threshold f_c at any win rate below ≈ 75%; direct simulation of the actual lots shows 0.02 lot on 105 USD has 60–95% probability of hitting margin stop-out within 200 trades at 65–70% win rate, and 0.04 lot on 130 USD is stopped out within 200 trades with 85–99% probability at 60–70% win rate and still 46% at 75%.

### Cited Findings
- Fixed-fractional Monte Carlo (45% WR, 2:1, 200 trades, 20,000 runs): P(DD ≥ 50%) = 19% at 5% risk, 94% at 10%, 100% at 15% and 20%; median max drawdown 41% / 67% / 84% / 93%; full Kelly = 17.5% — [dev.to Monte Carlo article](https://dev.to/nomad_paolo/i-ran-20000-monte-carlo-simulations-to-find-the-best-position-size-the-average-result-is-a-trap-5ed4)
- Thorp: betting above f* up to f_c still grows but with wilder swings; above f_c "eventual downward fluctuations will inexorably drive the values of X_n toward zero"; lifetime P(losing half) = 1/2 at full Kelly, 1/8 at half Kelly; betting 1.5× Kelly on a 2× overestimated edge → "we will be ruined" — [Thorp 2006, pp. 391, 411, 415](https://www.gwern.net/doc/statistics/decision/2006-thorp.pdf)
- Whelan: for positive-EV games, larger stakes raise ruin probability; with high payoff variance ruin probability approaches that of a zero-EV game even when EV > 0 — [Whelan 2025, §4.2, §5](https://www.karlwhelan.com/Papers/Ruin.pdf)
- Brazilian futures study (eight sizing methods, Monte Carlo): "profitable trading systems with high hit rates can become losers when position sizing is not done correctly" — [Revista FAE](https://revistafae.fae.edu/revistafae/article/view/626) (abstract only)
- Risk of ruin rises steeply with capital units: a 5% risk per trade is tied to ≈ 13% ruin probability vs much lower at 1% in one even-payoff example; capital units = account / risk per trade — [CompleteTradersEdge](https://completetradersedge.com/risk-of-ruin-mathematics-every-trader-must-understand/) (secondary source, even-payoff formula)

**Fixed-lot Monte Carlo of the actual configuration** (per 0.01 lot: win +2 USD, loss −4 or −5 USD; 200 trades; 5,000 paths; "ruin" = equity falls to or below the margin required to hold the position, 20 USD per 0.01 lot at 4,000 USD and 1:200; lots are not reduced as equity shrinks, which is what a fixed-lot bot does) — [own computation]:

| account | lot | loss/0.01 | risk/trade | p | P(ruin = margin stop) | P(DD ≥ 50%) | P(DD ≥ 30%) | median final (USD) |
|---|---|---|---|---|---|---|---|---|
| 100 | 0.01 | 4 | 4.0% | 0.60 | 0.60 | 0.89 | 0.98 | 20 |
| 100 | 0.01 | 4 | 4.0% | 0.65 | 0.12 | 0.44 | 0.78 | 80 |
| 100 | 0.01 | 4 | 4.0% | 0.70 | 0.005 | 0.065 | 0.31 | 140 |
| 100 | 0.01 | 4 | 4.0% | 0.75 | 0.000 | 0.001 | 0.044 | 200 |
| 100 | 0.01 | 5 | 5.0% | 0.60 | 0.97 | 1.00 | 1.00 | 19 |
| 100 | 0.01 | 5 | 5.0% | 0.65 | 0.67 | 0.91 | 0.99 | 20 |
| 100 | 0.01 | 5 | 5.0% | 0.70 | 0.16 | 0.49 | 0.82 | 80 |
| 100 | 0.01 | 5 | 5.0% | 0.75 | 0.005 | 0.072 | 0.33 | 150 |
| 100 | 0.01 | 5 | 5.0% | 0.80 | 0.000 | 0.002 | 0.042 | 220 |
| 105 | 0.02 | 4 | 7.6% | 0.60 | 0.93 | 0.98 | 1.00 | 37 |
| 105 | 0.02 | 4 | 7.6% | 0.65 | 0.57 | 0.77 | 0.95 | 37 |
| 105 | 0.02 | 4 | 7.6% | 0.70 | 0.12 | 0.30 | 0.70 | 185 |
| 105 | 0.02 | 4 | 7.6% | 0.75 | 0.008 | 0.040 | 0.27 | 305 |
| 105 | 0.02 | 5 | 9.5% | 0.60 | 1.00 | 1.00 | 1.00 | 37 |
| 105 | 0.02 | 5 | 9.5% | 0.65 | 0.95 | 0.99 | 1.00 | 37 |
| 105 | 0.02 | 5 | 9.5% | 0.70 | 0.60 | 0.81 | 0.96 | 39 |
| 105 | 0.02 | 5 | 9.5% | 0.75 | 0.13 | 0.31 | 0.69 | 205 |
| 105 | 0.02 | 5 | 9.5% | 0.80 | 0.011 | 0.048 | 0.27 | 345 |
| 130 | 0.03 | 5 | 11.5% | 0.70 | 0.71 | 0.85 | 0.98 | 58 |
| 130 | 0.03 | 5 | 11.5% | 0.75 | 0.24 | 0.42 | 0.78 | 259 |
| 130 | 0.04 | 4 | 12.3% | 0.65 | 0.84 | — | 0.99 | 74 |
| 130 | 0.04 | 4 | 12.3% | 0.70 | 0.44 | — | 0.87 | 218 |
| 130 | 0.04 | 4 | 12.3% | 0.75 | 0.14 | — | 0.52 | 506 |
| 130 | 0.04 | 5 | 15.4% | 0.60 | 1.00 | — | 1.00 | 74 |
| 130 | 0.04 | 5 | 15.4% | 0.65 | 0.99 | — | 1.00 | 74 |
| 130 | 0.04 | 5 | 15.4% | 0.70 | 0.85 | — | 0.99 | 74 |
| 130 | 0.04 | 5 | 15.4% | 0.75 | 0.46 | — | 0.87 | 218 |
| 130 | 0.04 | 5 | 15.4% | 0.80 | 0.15 | — | 0.52 | 582 |

("—" for 0.04 lot: the margin stop at 80 USD equity is reached at a 38% drawdown, so the position is force-closed before a 50% drawdown can be recorded; the ruin column is the relevant one.) Median final equity for ruined configurations is the frozen margin level (≈ 20 / 37 / 58 / 74 USD), i.e. the account ends with only the locked margin.

500 trades, 0.02 lot on 105 USD: P(ruin) = 0.81 (p = 0.70, loss 5), 0.17 (p = 0.75, loss 5), 0.011 (p = 0.80, loss 5); with loss 4: 0.16 / 0.010 / 0.001 — [own computation].

**Infinite-horizon gambler's ruin for the fixed-lot case** (steps +2 / −5 USD per 0.01 lot; following Whelan's method the characteristic equation is p·r⁷ − r⁵ + q = 0; the root r in (0,1) gives P(ruin from n units of 2 USD) ≈ rⁿ, ignoring margin, so this is the probability of equity reaching 0) — [own computation]:

| p | r | 100 USD at 0.01 (n = 50) | 105 USD at 0.02 (n = 26) | 130 USD at 0.04 (n = 16) | 1,000 USD at 0.01 (n = 500) |
|---|---|---|---|---|---|
| 0.72 | 0.9920 | 0.67 | 0.81 | 0.88 | 0.018 |
| 0.75 | 0.9499 | 0.076 | 0.26 | 0.44 | ~10⁻¹¹ |
| 0.80 | 0.8780 | 0.001 | 0.034 | 0.125 | ~10⁻²⁹ |
| 0.85 | 0.8012 | 0.000 | 0.003 | 0.029 | ~10⁻⁴⁹ |
| ≤ 0.7143 | — | ruin certain (zero or negative drift) | | | |

### Inferences
- The 8–17% band is 1.3–2.7× the full Kelly fraction of a system with p = 0.75 (12.5%) and far above the ruin threshold for p ≤ 0.72; in Thorp's taxonomy it is "overbetting", where growth is lower *and* ruin is likely.
- Doubling the lot from 0.01 to 0.02 on the same ~100 USD account roughly halves the number of "capital units" (50 → 26) and squares-root-of-two-ishly worsens the exponent: lifetime ruin at p = 0.75 goes from 7.6% to 26%; at 0.04 lot (16 units) to 44%. The MQL5 article's observation that the 2026 daily range is ≈ 100–120 USD per 0.01 lot means a single bad day at 0.04 lot (≈ 400–480 USD swing) is 3–4× the whole 130 USD account.
- The cost argument in Q3 (bigger lots dilute fixed cost per trade) and the ruin argument here pull in opposite directions; at 100–130 USD there is no lot size that satisfies both. Increasing capital (so that 0.01 lot ≈ 1–2% risk, i.e. 250–500 USD minimum, and so that 5–8 USD/day ≤ 0.3%/day, i.e. ≈ 2,000–3,000 USD) is the only configuration the cited sizing literature supports.

### Gaps
- No published source models exactly a fixed-lot (non-compounding) bot on a 100 USD account with a margin-based stop-out; the fixed-lot table is my simulation using the cited contract parameters (100 oz, 1:200, 0% stop-out). It ignores spread/slippage, swap, and HMR margin doubling, all of which make it optimistic.
- Serial correlation of losses (volatility clustering around news) is not modelled; Whelan's and Thorp's results assume i.i.d. outcomes.

---

## Source quality notes
- Primary/academic: Thorp 2006 (book chapter, PDF, text extracted locally with pdftotext); Whelan 2025 (UCD working paper, PDF, text extracted locally). Both fully read for the cited equations.
- Practitioner with explicit math and data: MQL5 blog posts 779060 and 776723 (one author; gives data sources and stress-test numbers); dev.to Monte Carlo article (20,000 runs, parameters disclosed).
- Broker facts: Exness help centre (summarised via search; direct fetch 403). Stop-out/margin-call levels from secondary promotional pages — lowest confidence item in these notes.
- Not found / not accessible: Ralph Vince's uneven-payoff ruin formula in primary form; Kaufman's ruin tables; Bryant's full TASC article (403).
