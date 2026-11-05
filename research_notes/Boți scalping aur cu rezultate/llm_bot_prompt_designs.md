# LLM-driven intraday bots: how they build the LLM's input and rules

Research date: 2026-11-04. Scope: open-source bots / public write-ups where an LLM (Claude/GPT/Gemini/DeepSeek/local) decides or filters intraday entries on forex / gold / crypto via MT5 or similar brokers. Focus: (1) what context is computed in code and passed, (2) how the prompt phrases entry rules and output, (3) which mechanical guards stay in code, (4) reported results, labelled VERIFIED-LIVE / PAPER (live data, no real money) / BACKTEST / CLAIM (self-reported, no methodology).

Result labels used below: **LIVE** = real money, author-reported with numbers; **PAPER** = live-feed paper trading with numbers; **BACKTEST**; **CLAIM** = numbers without methodology (vendor or blog); **NONE** = no numbers published.

---

## A. Concrete, copyable prompt / context techniques (with source)

### A1. Three-timeframe role split, each timeframe has ONE job
Source: kojott/LLM-trader-test (DeepSeek, Binance/Hyperliquid) — https://github.com/kojott/LLM-trader-test, prompts at https://raw.githubusercontent.com/kojott/LLM-trader-test/main/prompts/system_prompt.txt
- Code fetches 200×15m, 100×1h, 100×4h candles; computes EMA20/50/200, RSI14, MACD, ATR, volume vs 20-bar avg.
- Prompt assigns roles: **4h = bias only** (EMA alignment scored into trend strength, ATR for stop size), **1h = structure** (swing H/L, pullback to EMA/level, consolidation breakout on above-avg volume), **15m = trigger only** (RSI, MACD cross, EMA20, volume all required).
- Copyable rule: "Execute ONLY when ALL mathematical conditions are satisfied." Entry types with different risk: Type A with-trend 2%, Type B counter-trend 1% (needs 4h RSI extreme + major level + strong reversal), Type C range 1%.
- Results: author BACKTEST 18 Oct–3 Nov 2025 = **-15.2%** (Medium post), later prompt version reported as positive (CLAIM, no numbers in reachable sources). Public dashboard llmtest.coininspector.pro. Reported "+17%" tied to patron-only prompts = CLAIM.

### A2. "Validation gates" listed as a numbered checklist the model must tick, and echoed back in the JSON
Source: kojott system_prompt.txt (same repo)
- Gates in prompt: "Confluence count ≥ 3", "Cooldown elapsed (3h post-loss)", max 3 open positions, total risk ≤ 6%, stop distance ≤ 2%, counter-trend requires RSI_4h extreme or divergence.
- JSON output echoes the gates: `"action": "entry|hold|close|reject"`, `"direction": "long|short|null"`, `"gates_passed": 8`, `"failed_gates": []`, plus `quantitative_metrics`, `position_parameters`, `reasoning`.
- Why it is useful: the code can reject any decision whose `gates_passed` / `failed_gates` disagree with its own computed gates (the prompt cannot "forget" a rule silently).

### A3. Per-coin JSON decision with `invalidation_condition` and a mandatory hold-shape
Source: nof1 Alpha Arena reconstructed prompt (community reverse-engineering, unofficial) — https://gist.github.com/wquguru/7d268099b8c04b7e5b6ad6fae922ae83 and https://gist.github.com/patrickleehua/21ca475012e018824ca3c310546e483b
- Input: per coin, ~10 most recent points per indicator at 3-min (mid price, EMA20, MACD, RSI7, RSI14) + 4h context (EMA20/50, ATR3/14, volume vs avg, MACD, RSI14) + OI and funding; account state (return %, Sharpe, cash, equity); every open position with entry, current, liquidation, unrealised PnL, leverage, notional, risk_usd, confidence and its own `exit_plan` (TP, SL, invalidation).
- Copyable lines: "ALL PRICE AND INDICATOR DATA IS ORDERED: OLDEST → NEWEST. The LAST element in each array is the MOST RECENT data point." (LLMs misread order otherwise.)
- Output fields: `signal` (buy_to_enter | sell_to_enter | hold | close), `quantity`, `leverage`, `profit_target`, `stop_loss`, `invalidation_condition` ("Specific market signal that voids your thesis. Must be objective and observable"), `confidence` 0-1, `risk_usd`, `justification` (max 500 chars).
- Hold rule: "When signal is hold: Set quantity=0, leverage=1, and use placeholder values for risk fields… When in doubt, choose hold over forcing a trade."
- Anti-overtrading text: "Trading Fees ~0.02-0.05% per trade… Excessive trading erodes capital through fees… Fewer high-conviction trades beat many low-conviction trades."
- Results (LIVE, $10k each, 17 Oct–3 Nov 2025, secondary coverage, figures conflict across outlets): Qwen3-Max ≈ +22%, DeepSeek V3.1 ≈ +5% (~41 trades, win rate ~24%), Claude Sonnet 4.5 ≈ -31%, Grok 4 ≈ -45%, Gemini 2.5 Pro ≈ -57%, GPT-5 ≈ -63% (overleverage + overtrading → two margin calls). Same prompt, very different trade counts (Qwen 1,418 orders vs Grok 158 in one round) ⇒ the prompt alone does not control frequency.

### A4. Falsification requirement: no signal is accepted without a specific "proven wrong if…"
Source: qrak/LLM_trader (Gemini vision, CCXT, BTC/USDC 4h) — https://github.com/qrak/LLM_trader , write-up https://dev.to/qrak/-stop-trading-like-its-1999-an-llm-crypto-bot-that-sees-charts-remembers-trades-and-learns-1cd1
- Template: "This signal would be proven wrong if [specific price level or indicator condition] occurs." Vague conditions ("if the market turns bearish") are rejected in code and the model is re-asked. If the named condition triggers before the next cycle, code closes the position early.
- Bull case → bear case → decision inside one prompt (saves a second call).
- Result: PAPER 20 Sep–31 Oct 2026, 10,000 USDC: +0.16% vs B&H -4.64%, 12 trades, WR 42.9%, PF 1.40, Sharpe 0.21, inference cost $6.44 per 83 cycles. Author: "twelve trades are not evidence of an edge". Fees not modelled.

### A5. Code overrides the LLM's own market read when they disagree (TrendValidator / PatternQualityScorer / EV gate)
Source: qrak/LLM_trader (same)
- `TrendValidator`: LLM's stated trend strength compared with computed ADX; if they differ by >15 points the computed value wins.
- Pattern quality is a deterministic 0–100 score from the pattern engine, never the LLM's self-report.
- EV gate: expected P&L = WR×avgWin − (1−WR)×avgLoss computed from the bot's own closed trades; "LLM says strong buy but EV is negative ⇒ HOLD". Kelly fraction sizing, min R:R, wrong-side SL/TP detection, cooldown window, symbol whitelist, max position size — all in code.

### A6. Inject the bot's OWN similar past trades + win rate of nearest neighbours into every prompt
Source: qrak/LLM_trader; also Quant Flow review system — https://web3spreads.github.io/quant-flow/docs/features/review-system ; igorganapolsky "self-healing" bot — https://dev.to/igorganapolsky/i-built-a-self-healing-ai-trading-bot-that-learns-from-every-failure-g94
- qrak: ChromaDB with three collections (experiences, rules, blocked trades). Each prompt gets the 3 nearest past trades (by embedding of ADX/RSI/pattern/sentiment metadata), the win rate over the 20 nearest, up to 3 matching rules, up to 5 post-mortems. Reflection every 5 closed trades writes new rules. Time decay: last-week trade weighted 3× one from two months ago.
- Quant Flow: instant rule-based update after each closed trade (matched lesson confidence ×1.05 if profitable, ×0.95 if not; no LLM call), weekly LLM reflection writes a report only — prompt changes need human review. Lessons are tagged by regime (trending/ranging); regime mismatch multiplies similarity by 0.4; negative lessons prefixed `[Avoid]`, capped positive ratio 0.7.
- igorganapolsky: structured lesson records `LL-NNN` (trigger, impact, root cause, fix, prevention); before each decision a top-5 RAG query on "trade type + underlying + IV rank"; entry needs ≥2/3 agent votes. Results: PAPER $100k→$95.4k, 1 closed trade, LIVE -$20 ⇒ NONE meaningful.

### A7. Default-to-HOLD on every failure path and a stateless 30-second "consultant" call
Source: CrossTrade NT8 guide (Claude as ES signal filter) — https://crosstrade.io/blog/llm-assisted-futures-trading-using-ai-signal-generation-on-nt8
- Context per call: instrument, current price, last 20 ticks listed explicitly, change over window, avg tick-to-tick move (volatility proxy), session P&L, current position (dir, qty, entry, unrealised) or FLAT.
- System prompt rules: valid signals only BUY/SELL/HOLD; one contract max; no reversal in one step; HOLD once daily loss limit hit; "default to HOLD when uncertain".
- Output: `{"signal": "...", "reason": "..."}` only, max 200 tokens. Code strips fences; any parse error, invalid value or API error ⇒ HOLD. Code cross-checks the signal against real position state because the model "can misread position state even when it is stated in the snapshot".
- Result: NONE; author says the LLM "won't beat a well-tuned quantitative model", use it as a filter.

### A8. Hard "no-trade" conditions for gold scalping written as explicit numeric gates (ATR caps, rollover window, session, news ±15 min, loss streak stop)
Source: huynguyen03dev/xauusd-trading Claude skill — https://github.com/huynguyen03dev/xauusd-trading (SKILL.md)
- Data: live price + current candle + RSI, MACD, Stoch, ADX, CCI, EMA, BB, ATR on 5m/15m/1h; last N candles.
- Priority stack stated in prompt: (1) price structure, (2) candle patterns, (3) indicator confluence, (4) external rating "a secondary opinion, not gospel". "Never take a trade purely because an indicator says so."
- Do-not-trade list: "Price in middle of range", "Choppy/whipsaw action (alternating direction candles)", "Wide spread candles", "Near session open/close"; "If 5m ATR > 8 points → SKIP (your 5-point SL is noise)"; "If 15m ATR > 20 points → SKIP"; "DO NOT TRADE during 21:00-22:00 UTC (rollover)"; "Do NOT trade during or 15 minutes before/after [red news]"; best window London–NY overlap 13:00–17:00 UTC; Asian "warn but allow".
- Cost realism in prompt: "Effective SL = 5 (your SL) + 2-3 (spread) + 1 (slippage) = 8-9 points".
- Output: BUY / SELL / NO TRADE + confidence "Cap at 70% max for any single outcome"; "If SL probability > TP1 probability by a wide margin → SKIP"; verdict WORTH TAKING / MARGINAL / SKIP. Checklist "Majority are Yes and #2 + #6 pass" (#2 rejection wick/engulfing at level, #6 logical SL behind structure).
- Management: BE at +2.5–3 pts; "After 2 consecutive losses (-10 pts total), STOP trading for the session"; "most of the time the correct signal is NO TRADE". Results: NONE (paper/learning only).

### A9. "NO SIGNAL – MARKET IS CHOPPY" as the only allowed non-trade output, plus a bounded confidence range
Source: MQL5 article 20185 (local Ollama model on EURUSD H1, Python+MT5) — https://www.mql5.com/en/articles/20185
- Context sent as ONE comma-separated line computed from 500 H1 bars: price, EMA 9/21/50/200, RSI14, MACD state (bullish/bearish), BB position (above/below/inside), volume vs 20-bar avg %, structure label (HH/HL, LH/LL, consolidation), daily open, weekly open, prev-day high/low, flags for bullish/bearish FVG and liquidity grab.
- Prompt rules: "Never open positions without minimum 1:2 risk-reward ratio"; "Conservatism is more important than profit"; "Risk per trade never exceeds 2% of account equity"; if no setup, output exactly "NO SIGNAL – MARKET IS CHOPPY" and nothing else.
- Fixed template: SIGNAL, ENTRY, STOP-LOSS, TP1 (50%), TP2 (50%), CONFIDENCE (50–95 only), TIMEFRAME, EXPIRATION, RISK (0.5–2.0%), REASONING (max two sentences). Temperature 0.2.
- Guards in code: none (volume hard-coded 0.01, only checks the string "BUY"). Results: CLAIM only (expected "30–40% fewer false signals", no data).

### A10. "JSON-only analyst" wrapper + temperature 0 + text fallback parser
Source: MQL5 article 20588 (ChatGPT EA, Part 7) — https://www.mql5.com/en/articles/20588
- Prompt opens: "You are a JSON-only trading analyst. Respond with EXACTLY one valid JSON object matching this schema. NO OTHER TEXT:" and includes one example response; reminds "Bar 1 is the most recent complete bar".
- Fields: `signal` BUY/SELL/NONE, `reason` (exact pattern name from a list), `trigger_bar` 1–10, `entry`, `sl`, `tp`.
- Data: last MaxChartBars completed bars (time, OHLC normalised to _Digits, tick volume) from shift 1.
- Code: `GetChatGPTResponse(prompt, 0.0)` for signals; extracts `{…}`; if JSON parse fails, line-based fallback on `SIGNAL:`/`SL:`/`TP:`; non-NONE with zero entry/SL/TP ⇒ alert and abort; trigger_bar outside 1–10 reset to 1. Does NOT check SL side or existing positions. Results: NONE (GIF only).

### A11. Validation service that rejects by vocabulary and by confidence floor, with a 5-tier fallback ending in "no trade"
Source: MQL5 blog 764232 (vendor write-up, MT5 EA + Python + Redis) — https://www.mql5.com/en/blogs/post/764232
- Reply format: action BUY/SELL/WAIT, confidence 1–10, one-sentence reason, risk level. Temperature 0.3.
- Validation layer: position size must fit risk params; SL 10–100 pips (configurable); TP realistic; "Confidence scores below 6/10 = no trade"; responses containing "definitely" or "guaranteed" are discarded; on failure fall back to conservative defaults.
- Fallback chain: GPT-4 → Claude 3 → GPT-3.5 → local rules → no trade; 30 s timeout. Background analysis every 30 s with cached scenarios so real-time calls only for exceptions.
- Results: CLAIM (500+ h forward test, "99.7% hallucination catch rate", "15% better accuracy with ensemble at 3× cost") — promotional, unverified.

### A12. Analyst/executor separation: the LLM proposes, a deterministic risk engine and human approve, live tools off by default
Source: hyuwowo/OpenTrading (MT5, MCP for Claude/GPT/Cursor) — https://github.com/hyuwowo/opentrading
- "The AI acts as analyst, not executor." Every live order must pass Signal Engine (rule-based: SMA, RSI, ATR, volatility breakout, ensemble voting, optional LightGBM/XGBoost) → Risk Engine (deterministic final gate) → human approval → kill switch.
- Risk config examples: 5% max risk per trade, 10% max daily loss, spread caps, `min signal confidence 0.42`, `min reward:risk 0.7`, edge floor, kill switch. Docs list `no_trade_conditions.md` and `risk_rules.md` (not reachable at time of writing).
- Env: `MCP_DATA_ONLY_MODE=true`, `ENABLE_LIVE_ORDER=false` default; live tools only with `MCP_ENABLE_LIVE_TOOLS=true`.
- Tools exposed to the LLM include `analysis_packet`, `htf_context`, `market_research`, `propose_trade`, `request_live_order`. Results: NONE.

### A13. Supervised mode: Claude generates signals into a `signals/` folder, MT5 EA executes only after per-trade human consent; strategy rules live in `rules.json`
Source: DonCoyoteS/claude-mt5-trader — https://github.com/DonCoyoteS/claude-mt5-trader ; also ClaudeTrading skill (analysis → account check → lot from risk % → risk summary → ask confirmation → emit CLI command)
- Autonomous mode requires "multiple deliberate steps" plus risk limits and a kill switch; walk-forward backtest with 70/30 split is part of onboarding. Results: NONE.

### A14. Pydantic structured output with `should_trade: bool` as the first field
Source: mcp-metatrader5-server pydantic-ai docs — https://mcp-metatrader5-server.readthedocs.io/en/latest/pydantic_ai_integration/
- `TradingDecision(should_trade: bool, action: "BUY"|"SELL"|None, symbol, volume, stop_loss, take_profit, reason)`; separate `MarketAnalysis(trend, support_level, resistance_level, recommendation, risk_level)` agent.
- Prompt: "You are a conservative trading bot focused on capital preservation. Rules: 1. Only trade when you have high confidence (>80%) 2. Always use stop loss and take profit 3. Maximum risk per trade: 2% … 5. Never trade during high-impact news events 6. Always check existing positions before opening new ones. Decision criteria: strong trend confirmation, clear support/resistance levels, R:R ≥ 1:2."
- Caveat: nothing in the example code enforces the 80% or the 2% (placeholders). Results: NONE.

### A15. Ground every level in the technical report, absolute prices only, "conflict alone is not a Hold"
Source: TauricResearch/TradingAgents trader agent — https://github.com/TauricResearch/TradingAgents (tradingagents/agents/trader/trader.py)
- "Ground concrete price levels (entry, stop-loss, position sizing) in the technical market report's price structure -- current price, support/resistance, ATR, and volatility -- and use the research plan for direction."
- "State entry price and stop-loss as absolute price levels … never a percentage or a range; convert a percentage distance to the price level it implies, or omit the field if you cannot state a number."
- Output ordering: Action (Buy/Hold/Sell) on its own line first, then Reasoning, Entry, Stop, Sizing. "A research recommendation of Overweight is a Buy and Underweight is a Sell, sized by how strong the case is; conflict alone is not a Hold." Daily stocks, not intraday. Results: BACKTEST in paper (arXiv 2412.20138), not intraday-relevant.

### A16. Strength label instead of numeric confidence, and trade only on STRONG with zero open trades
Source: sameer-dev-stack/Trade-bot-AI-Scalper (Gemini, MT5, EURUSD+XAUUSD) — https://github.com/sameer-dev-stack/Trade-bot-AI-Scalper
- Sends 100 H1 bars, RSI14, SMA20, BB(20,2); asks BUY/SELL/HOLD + reason + strength STRONG/MODERATE/WEAK; code opens only if STRONG and no open trade; fixed 0.01 lot, SL 10 / TP 20 pips. Results: NONE (0 stars; README leaks an API key — do not reuse).

### A17. Portfolio-level memory window + news abstracts + fixed allocation vector (not per-trade signals)
Source: LiveTradeBench (ulab-uiuc/live-trade-bench, arXiv 2511.03628) — https://github.com/ulab-uiuc/live-trade-bench
- Each step: positions incl. cash, prices, Google-News titles+abstracts by asset keyword, a fixed-length window of past observations and action outcomes; ReAct loop; output is an allocation vector summing to 1.
- Result: LIVE-feed PAPER, 18 Aug–24 Oct 2025, 21 LLMs. US stocks: GPT-4.1 +6.25% (Sharpe 2.64), Claude-Opus-4.1 lowest DD 1.84%. Polymarket: Claude-Sonnet-3.7 +20.5%, several models −50%+. Failure modes: "overreactive allocation changes" under volatility, reasoning models over-deliberate, chasing headlines (13 Oct everyone flipped No→Yes on non-moving news). Delay k days cost ~2% on Polymarket, ~0 on stocks.

### A18. Tool-calling agent with an explicit human-supplied strategy and a token-bucket rate limiter
Source: dev.to "lessons from 2,296 trades" (Claude Desktop + MCP, crypto) — https://dev.to/hopkins_jesse_cdb68cfa22c/building-an-ai-trading-agent-that-actually-works-lessons-from-2296-trades-1n9p
- Context: tickers, order book, candles, RSI/MACD/BB (+AHR999 for BTC), balance, positions; 107 tools. The author supplied the strategy; the LLM executed it.
- Result (implied LIVE, ~3 months): 2,296 trades, WR 53.7% vs 51.02% break-even after fees, Sharpe 3.82 annualised, avg +$1.05/day (≈ $380/yr). Author: "not a money printer"; agent never notices abnormal data or regime change.

### A19. Pure price-action prompt with S/R defined mechanically inside the prompt ("local max/min of last 50 bars"), stateless each call
Source: MQL5 article 20354 (Aleh Daikou, Python+MT5+LLM, EURUSD M5) — https://www.mql5.com/en/articles/20354
- Sends last 100 bars as CSV; prompt: "You are an intraday trader working both on support/resistance levels and Price Action patterns. Define the key support/resistance levels (local max/min for the last 50 bars). Look for Price Action candle patterns on levels. If bearish signal… SELL… Otherwise - HOLD. SL beyond the extremum, TP with 1:2."
- Output ACTION/ENTRY_PRICE/SL/TP/REASON parsed by regex; defaults to HOLD. Only guard: zero price ⇒ HOLD. Author: "DO NOT use this script for trading on a live account"; readers saw "no trade" for long stretches and 12–13 s per call. Results: NONE.

### A20. Post-mortem driven guard tuning (non-LLM bot, but the guard numbers are reusable)
Source: dev.to "70 live trades post-mortem v8.04" — https://dev.to/rocketsquirreldev/analyzing-70-live-trades-of-my-python-crypto-bot-v804-post-mortem-5acl ; "26 days of AI trading" — https://dev.to/xqliu/26-days-of-ai-trading-14-trades-3-system-rewrites-and-one-expensive-lesson-4ai9
- LIVE 70 trades: WR 48.6%, realised R:R 1.14 vs 3:1 target because trailing exit "too aggressive"; BTC entered 4× in one day in chop ⇒ 1 h post-loss cooldown "simply too short" → 2 h; daily circuit breaker halting new entries 24 h; ADX ≥ 25 gate per symbol; "volume climax filter" to block FOMO entries at pump tops.
- LIVE $100 Hyperliquid, 14 trades: trading P&L −$7.71; 1-hour direction check (exit if not +0.8% in the right direction within an hour); TP 7%→1% turned backtest −4.9% into +35.6%; "Any backtest without real transaction costs is fiction."

---

## B. Five prompt rules that recur across projects

1. **HOLD / NO TRADE is the explicit default and must be a legal, well-formed output.** Every working design gives the model a cheap no-action answer ("hold" with quantity 0; "NO TRADE"; "NO SIGNAL – MARKET IS CHOPPY"; `should_trade: false`) and tells it "when in doubt, hold" / "most of the time the correct signal is NO TRADE". Any parse or API failure also maps to HOLD in code (A3, A7, A8, A9, A14, A19).
2. **Rules are numbered checklists / gates with numeric thresholds, and the model must say which gates passed.** Confluence ≥ 3, R:R ≥ 1:2 (or 2:1), risk ≤ 1–2 %, ATR caps, cooldown elapsed, max N positions — written as gates, not prose; echoing `gates_passed` / `failed_gates` or a verdict label (WORTH TAKING / MARGINAL / SKIP) lets code audit the decision (A2, A8, A9, A11).
3. **Context is pre-digested, labelled and ordered, not raw.** Trend labels per timeframe (4h bias / 1h structure / 15m trigger), structure tags (HH/HL, LH/LL, consolidation), BB position words, volume vs average %, key levels (daily/weekly open, prev-day H/L, "local max/min of last 50 bars"), plus the bot's own state: open positions with entry / unrealised PnL / exit plan, session P&L, account equity, and (in better designs) its own recent similar trades and their outcomes. Always state the ordering of arrays ("OLDEST → NEWEST") (A1, A3, A6, A7, A9).
4. **Strict JSON-only output with a small fixed schema, temperature ≈ 0–0.3, one example included; levels as absolute prices, with `reason` capped in length.** Common fields: action, entry, sl, tp, confidence (bounded, e.g. 50–95 or 0–1, with a cap like 70 %), reason/justification (≤ 2 sentences or ≤ 500 chars), and — in the two best designs — `invalidation_condition` that must be "objective and observable" (A3, A4, A9, A10, A15).
5. **Anti-chasing is written into the prompt AND duplicated in code.** Prompt side: fees spelled out ("fees materially erode profits on small positions", effective SL = SL + spread + slippage), "fewer high-conviction trades beat many", no pyramiding, no hedging, no averaging down, no reversal in one step, cooldown after loss (3 h), stop after 2 consecutive losses, no trade ±15 min around red news or in rollover 21:00–22:00 UTC, trade only London/NY. Code side: the same cooldown, max positions, daily loss circuit breaker, session gate, confidence floor (A1, A3, A8, A11, A20).

---

## C. Mechanical guards kept in code regardless of the LLM (union across projects)

- Parse/validate: extract `{…}`, schema check, defaults to HOLD; reject zero or wrong-side SL/TP; SL distance band (e.g. 10–100 pips); R:R floor; discard replies containing "definitely"/"guaranteed" (A10, A11, qrak).
- State cross-check: signal vs real open position (no BUY if long, no reversal in one step); "always check existing positions" (A7).
- Confidence floor (6/10, 0.42, STRONG only) and confidence cap (70 %) — but see D (A11, A12, A16, A8).
- Cooldown after loss (1–3 h), max open positions (1–3), total open risk cap (6 %), daily loss circuit breaker (10 % or fixed $) that halts new entries for 24 h, kill switch (A1, A12, A20).
- Session/time gates (London–NY overlap, no rollover hour), red-news window, spread cap, ATR-too-high skip, ATR-too-low skip for scalps (A8, A12).
- Position size computed in code from risk % and SL distance; LLM's entry price ignored, market price used; fixed max contract/lot (A7, A10, A19).
- Independent trend check (ADX) and EV gate from own trade history override the LLM (A5).
- Falsification check: close early if the model's stated invalidation triggers (A4).
- Timeout + fallback chain ending in no trade; token-bucket rate limiting; atomic multi-leg orders (A11, A18, A6).
- Backtests must include real fees; parameter changes accepted only if they validate on held-out data (A20, caelyn_moss Hyperliquid write-up: users who hand-tweaked after one bad week made agents worse).

---

## D. What they report NOT working

- **LLM as free-running autonomous trader on a short loop (3-min cycles, leverage allowed):** 4 of 6 frontier models lost 30–63 % in 17 days (Alpha Arena, LIVE); organiser: "not yet feasible to directly hand over money to an LLM". Same prompt gave 10× differences in trade count between models.
- **Raw numbers / raw bars without pre-digestion:** "RSI is 28, oversold. BUY" followed by −4.2 % (qrak). Bars-only prompts (A10, A19) produce long no-trade stretches or confident wrong calls; 12–13 s latency per call on local models.
- **Trusting the model's stated confidence as a probability:** multiple write-ups and papers find LLM self-confidence overconfident (80 % ≈ 60 % realised; Brier drifting 0.18 → 0.25 over 3 months). Use it as an unlabelled feature, gate on code-computed metrics instead.
- **Trusting the model to enforce prompt-stated risk rules:** A9, A14 show prompts saying "2 % risk, 1:2 R:R, >80 % confidence" with zero enforcement in code; A10 EA ignores SL side. Everyone who ran live later moved the rule into code.
- **Prompt-only anti-overtrading:** fee warnings in the prompt did not stop Qwen's 1,418 orders; cooldown of 1 h was "too short" in chop (4 BTC entries in one day); only code-side cooldowns/circuit breakers worked.
- **Reflection/memory as an edge:** qrak (+0.16 % PAPER, 12 trades), igorganapolsky (1 closed trade), FinMem (daily stocks, backtest, risk profile chosen on test set) — none shows a live intraday edge; Quant Flow explicitly keeps prompt self-modification human-reviewed.
- **Exit discipline is the bottleneck, not entries:** Japan FX Bot Lab (LIVE, MT5, LLMBridgeTrader EURUSD 11W/1L one day; GateGrid "small wins, big losses") concludes "selection power and loss control are two entirely different skills"; 70-trade post-mortem realised R:R 1.14 vs 3:1 target due to aggressive trailing.
- **Many strategies / many signals in one prompt:** "diluted failure" (243 trades, 14 % WR, 4 days, killed); Reddit practitioners: overloading the model with signals increases overtrading.
- **Backtests without fees / live-vs-backtest gap:** +94 % → +35 % once 8.6 bps round-trip added; 67 % WR / PF 4.1 backtest became 62 % / 2.8 live.
- **Latency with cloud LLMs for scalping:** 200–500 ms API round trips move price several pips; vendors mitigate with 30 s background analysis + cached scenarios (CLAIM).
- **Hand-coded chart pattern detection (~900 lines) was worse than a vision model's read** (qrak) — but the vision bot itself shows no edge yet.

---

## E. Sources checked but with no extractable code/prompt
- codebytemirza/xauusd_trading_asistent_ai (Groq, MT5, D1→M5, "65 % accuracy on real account" = CLAIM; code removed from repo).
- LuckyOne7777/LLM-Trading-Lab (ChatGPT micro-cap, daily, LIVE $100, 6 months; results PDF not reachable; not intraday).
- kojott Medium posts (403); DoIt Alpha Pulse AI MQL5 vendor posts (CLAIM only); MQL5 blog 761113 (argues LLM operational decisions infeasible).
- ariadng/metatrader-mcp-server, agentpedia MetaTrader MCP: tool bridges only, no decision prompt.
