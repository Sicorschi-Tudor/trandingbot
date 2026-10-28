# v1 Technical Contract (source of truth for all modules)

Version 1 = fastest working demo. Single Python process (FastAPI) + SQLite + vanilla JS dashboard + MQL5 bridge EA.
All timestamps stored/transported in UTC. UI shows Europe/Chisinau.

## 0. Layout

```
tradingBot/
  .env.example  .env  requirements.txt  run.sh  README.md
  app/
    __init__.py
    main.py              FastAPI app, routers, startup/shutdown of background tasks, serves web/
    config.py            pydantic-settings Settings loaded from .env (see §5)
    db.py                sqlite3 schema + small helpers (thread-safe, one connection + lock)
    state.py             in-memory runtime state (quotes, bars, account, positions, last analysis) + EventBus for SSE
    bridge.py            /bridge/v1/sync endpoint (EA protocol §3) + command queue
    simfeed.py           SIMULATED feed (random walk) producing the same updates as the bridge (DATA_SOURCE=sim)
    indicators.py        ema, rsi, atr, macd, bollinger (pure python / lists of floats)
    strategy.py          deterministic signal engine -> Analysis / Signal
    risk.py              deterministic Risk Engine -> RiskCheck
    executor.py          intents -> bridge commands (DEMO) or paper fills (PAPER); reconciliation of results
    paper.py             paper broker simulator using live bid/ask
    position_manager.py  break-even / trailing / time-stop / session-end close; detects closed positions
    scheduler.py         session window (IANA tz, DST-safe), manual override
    ai_claude.py         Claude review of a computed signal (explain + optional VETO only)
    telegram.py          outbox + sender worker with retry/backoff/idempotency
    engine.py            orchestrator loop (analysis on bar close, risk, intents, notifications)
    api.py               /api/v1/* routes for the UI (§2)
  web/  index.html  app.js  style.css
  mt5/  AIBridgeEA.mq5
  tests/ test_*.py (pytest)
```

## 1. Core rules (from spec — must hold)
- Modes: SIGNAL_ONLY, PAPER, DEMO. LIVE exists as a name but is **refused in v1** (403). Default mode from env `TRADING_MODE` (default PAPER). Mode/arming is NEVER restored to DEMO automatically after restart unless `TRADING_MODE=DEMO` in .env AND `EXECUTION_ENABLED=true`; LIVE never.
- DEMO orders only if bridge connected AND `account.trade_mode == 0` (MT5 ACCOUNT_TRADE_MODE_DEMO). Otherwise block + log.
- Claude never creates orders, never changes direction/levels. It returns AGREE / CAUTION / VETO + explanation. If `CLAUDE_VETO_ENABLED=true` and verdict == VETO → signal becomes NO_TRADE (it can only reduce risk). If Claude fails/times out → proceed without it (status ERROR), unless `CLAUDE_REQUIRED=true` → NO_TRADE.
- Every order has SL and TP (MUST_HAVE_SL_TP). Bot positions identified by magic `MT5_MAGIC` (default 552211). Never touch non-bot positions.
- Kill switch (pause entries) ≠ close-all. Close-all requires body `{"confirm":"CLOSE ALL"}`.
- No duplicate signals: key = symbol + timeframe + bar_open_time + direction.
- Commands to EA are delivered once; never blindly re-sent. If no result in 30 s → state UNKNOWN + alert, reconcile via positions (comment `ai:<intent_id_first8>`).
- Stale data (tick age > `MAX_TICK_AGE_SECONDS`), spread > max, < 100 bars, session closed, existing bot position on the symbol → HOLD with reason.
- Secrets never returned by API, never logged.

## 2. UI API (`/api/v1`, JSON)
Admin endpoints (all POST/PUT) require header `X-Admin-Token: <ADMIN_TOKEN>`. If ADMIN_TOKEN is empty → 403 `{"detail":"Set ADMIN_TOKEN in .env"}`. Wrong token → 401.

- `GET /status` →
```json
{"app_env":"development","version":"0.1.0","data_source":"mt5_bridge|sim","feed_label":"MT5_FEED|SIMULATED",
 "bridge":{"connected":true,"last_sync_utc":"...","age_s":0.4,"terminal_connected":true,"trade_allowed":true,"ea_version":"1.00"},
 "account":null | {"login_masked":"****1234","server":"...","company":"...","is_demo":true,"currency":"USD","balance":0,"equity":0,"margin":0,"margin_free":0,"leverage":100,"profit":0},
 "session":{"enabled":true,"open_now":false,"start":"09:00","end":"13:00","days":["MON",...],"tz":"Europe/Chisinau","next_change_utc":"...","override":null|"start"|"stop"},
 "trading":{"mode":"PAPER","execution_enabled":false,"entries_paused":false,"live_allowed":false},
 "ai":{"enabled":true,"model":"claude-opus-5-5","veto_enabled":true,"key_set":true,"last_ok_utc":null,"last_error":null},
 "telegram":{"enabled":false,"configured":false,"last_ok_utc":null,"last_error":null,"pending":0},
 "counts":{"signals_today":0,"trades_today":0,"open_positions":0,"closed_today":0,"realized_pnl_today":0.0},
 "engine":{"last_loop_utc":"...","last_analysis_utc":"..."}}
```
- `GET /symbols` → `[{"symbol":"EURUSD","enabled":true,"bid":1.1,"ask":1.1,"spread_points":12,"digits":5,"point":0.00001,"tick_age_s":0.5,"quality":"OK|STALE|NO_DATA","max_spread_points":30,"analysis":<Analysis|null>}]`
- `GET /candles?symbol=EURUSD&tf=M5&limit=300` → `{"symbol":"EURUSD","tf":"M5","bars":[{"time":1730000000,"open":..,"high":..,"low":..,"close":..,"tick_volume":..}],"ema_fast":[{"time":..,"value":..}],"ema_slow":[...]}` (time = unix seconds UTC of bar open; last bar may be forming)
- `GET /analysis?symbol=EURUSD` → Analysis:
```json
{"symbol":"EURUSD","timeframe":"M5","time_utc":"...","bar_time":1730000000,"direction":"BUY|SELL|HOLD","score":0.0,
 "probabilities":{"up":0.5,"down":0.3,"neutral":0.2},"probability_method":"heuristic-v1 (NOT calibrated)",
 "reasons":["..."],"indicators":{"ema_fast":..,"ema_slow":..,"rsi":..,"atr":..,"macd_hist":..,"htf_trend":"UP|DOWN|FLAT"},
 "regime":"TREND_UP|TREND_DOWN|RANGE|HIGH_VOL","spread_points":12,"entry":null|1.1,"stop_loss":null|..,"take_profit":null|..,"rr":null|1.67}
```
- `GET /signals?limit=100&symbol=&direction=` → `[Signal]` newest first. Signal:
```json
{"id":"uuid","created_at_utc":"...","symbol":"EURUSD","timeframe":"M5","bar_time":1730000000,"direction":"BUY|SELL","horizon_minutes":60,
 "probabilities":{...},"entry":1.1,"stop_loss":1.09,"take_profit":1.12,"rr":1.67,"spread_points":12,"expires_at_utc":"...",
 "invalidation":"text","reasons":["..."],"indicators":{...},
 "ai":{"status":"PENDING|DONE|ERROR|DISABLED","verdict":"AGREE|CAUTION|VETO|null","explanation":"ro text","risks":["..."],"model":"..."},
 "status":"NEW|NO_TRADE|INTENT_CREATED|EXECUTED|REJECTED|EXPIRED","status_reason":"...","mode":"PAPER","feed_label":"MT5_FEED","strategy_version":"trend-v1","intent_id":null|"uuid"}
```
- `GET /signals/{id}` → Signal + `"events":[{"time_utc","event_type","detail"}]`
- `GET /positions` → `{"open":[{"ticket":123,"symbol":"EURUSD","side":"BUY|SELL","volume":0.01,"price_open":..,"price_current":..,"sl":..,"tp":..,"profit":..,"swap":..,"opened_at_utc":"...","source":"MT5|PAPER","bot_owned":true,"comment":"..."}],"pending_commands":[Intent]}`
- `GET /trades?limit=100` → `[{"id":..,"ticket":..,"symbol":..,"side":..,"volume":..,"price_open":..,"price_close":..,"profit":..,"commission":..,"swap":..,"opened_at_utc":..,"closed_at_utc":..,"close_reason":"SL|TP|MANUAL|BOT|UNKNOWN","mode":"PAPER|DEMO","signal_id":..}]`
- `GET /intents?limit=100` → `[Intent]`; Intent = `{"id":"uuid","created_at_utc":..,"updated_at_utc":..,"kind":"OPEN|CLOSE|MODIFY","signal_id":..,"symbol":..,"side":..,"volume":..,"sl":..,"tp":..,"ticket":null|int,"mode":"PAPER|DEMO","state":"REJECTED_RISK|QUEUED|DELIVERED|FILLED|FAILED|UNKNOWN|PAPER_FILLED","risk_check":{"passed":true,"checks":[{"name":"..","ok":true,"detail":".."}]},"result":null|{"retcode":10009,"retcode_desc":"..","price":..,"volume":..,"position":..,"comment":".."}}`
- `GET /risk` → risk settings object (§5 risk keys, lower_snake_case); `PUT /risk` (admin) partial update, validated, persisted in sqlite `settings` table (override .env).
- `GET /settings` → `{"schedule":{"enabled","start","end","days","tz"},"strategy":{"signal_timeframe","htf_timeframe","symbols":[..],"atr_sl_mult","atr_tp_mult","min_score","signal_expiry_minutes","break_even_at_r","trailing_enabled","max_hold_minutes","close_at_session_end"},"telegram":{"enabled","configured","thread_id_set"},"ai":{"enabled","model","veto_enabled","required","key_set"}}`
- `PUT /schedule`, `PUT /strategy` (admin) partial updates.
- `POST /control/mode` `{"mode":"SIGNAL_ONLY|PAPER|DEMO|LIVE"}` (LIVE → 403 "LIVE disabled in v1"; DEMO → 409 if bridge not connected or account not demo).
- `POST /control/execution` `{"enabled":true}`
- `POST /control/pause-entries`, `POST /control/resume-entries`
- `POST /control/session` `{"action":"start|stop|auto"}`
- `POST /control/close-position` `{"ticket":123,"confirm":true}`
- `POST /control/close-all` `{"confirm":"CLOSE ALL"}` → closes bot-owned only.
- `POST /control/test-order` `{"symbol":"EURUSD","side":"BUY|SELL"}` → PAPER/DEMO only; min volume; SL/TP from ATR; full Risk Engine; returns Intent.
- `POST /control/analyze-now` `{"symbol":"EURUSD"}` → runs analysis immediately (no order unless signal + all gates pass) returns Analysis.
- `POST /telegram/test` → `{"ok":true}` or `{"ok":false,"error":"..."}`
- `GET /logs?limit=200&level=` → `[{"id","time_utc","level":"INFO|WARN|ERROR","component","message"}]`
- `GET /stream` (SSE, no auth for reads). Each message: `id: <seq>\nevent: <type>\ndata: <json>\n\n`. Types: `status` (same as /status, every 2 s), `tick` `{"symbol","bid","ask","spread_points","time_msc"}` (throttled ≤4 Hz per symbol), `bar` `{"symbol","tf","bar":{...}}`, `analysis` (Analysis), `signal` (Signal), `intent` (Intent), `positions` (same as /positions), `trade`, `log`. Keepalive comment every 15 s.

## 3. EA bridge protocol (`mt5/AIBridgeEA.mq5` ↔ `app/bridge.py`)
EA attached to ONE chart, uses `OnTimer` every `InpTimerMs` (default 500 ms). Per timer: build JSON, `WebRequest("POST", InpServerUrl + "/bridge/v1/sync", headers "Content-Type: application/json\r\nX-Bridge-Key: <InpBridgeKey>\r\n", timeout 3000)`.
EA inputs: `InpServerUrl="http://127.0.0.1:8787"`, `InpBridgeKey=""`, `InpMagic=552211`, `InpTimerMs=500`, `InpDeviationPoints=20`, `InpHistoryBars=300`.

Request JSON (EA → server):
```json
{"ea_version":"1.00","magic":552211,"time_utc":1730000000,
 "terminal":{"connected":true,"trade_allowed":true,"build":4000},
 "account":{"login":12345678,"server":"Broker-Demo","company":"Broker","trade_mode":0,"currency":"USD","balance":10000.0,"equity":10000.0,"margin":0.0,"margin_free":10000.0,"leverage":100,"profit":0.0,"trade_allowed":true,"trade_expert":true,"margin_mode":2},
 "symbols":[{"symbol":"EURUSD","bid":1.1,"ask":1.10012,"time_msc":1730000000123,"spread_points":12,"point":0.00001,"digits":5,
   "volume_min":0.01,"volume_max":100,"volume_step":0.01,"stops_level":0,"freeze_level":0,"tick_value":1.0,"tick_size":0.00001,"contract_size":100000,"trade_mode":4,"filling_mode":3}],
 "bars":[{"symbol":"EURUSD","tf":"M5","t":1730000000,"o":1.1,"h":1.1,"l":1.1,"c":1.1,"tv":120,"sp":12}],
 "positions":[{"ticket":123,"symbol":"EURUSD","type":0,"volume":0.01,"price_open":1.1,"sl":1.09,"tp":1.12,"price_current":1.1,"profit":0.5,"swap":0.0,"magic":552211,"comment":"ai:1a2b3c4d","time":1730000000}],
 "deals":[{"ticket":456,"order":789,"position_id":123,"symbol":"EURUSD","type":1,"entry":1,"volume":0.01,"price":1.11,"profit":1.0,"commission":-0.07,"swap":0.0,"magic":552211,"reason":4,"time":1730000500,"comment":"tp 1.11"}],
 "results":[{"cmd_id":"1a2b3c4d-...","ok":true,"retcode":10009,"retcode_desc":"done","order":789,"deal":456,"position":123,"price":1.1,"volume":0.01,"comment":""}]}
```
- `positions`: ALL positions (server filters by magic; non-bot shown with bot_owned=false).
- `deals`: deals with this magic from the last 24 h (`HistorySelect(now-86400, now+60)`), max 50 newest. `entry`: 0=IN,1=OUT,2=INOUT,3=OUT_BY. `reason`: DEAL_REASON (3=SL? use MT5 values: 4=SL, 5=TP per ENUM_DEAL_REASON: CLIENT=0,MOBILE=1,WEB=2,EXPERT=3,SL=4,TP=5,SO=6).
- `bars`: for each requested symbol × timeframe: the last 3 bars (index 0 = forming, 1 = last closed, 2) every sync; PLUS full `InpHistoryBars` history for any pair listed in the server's `HIST` lines of the previous response. Times are bar open in UTC seconds — EA must convert server time to UTC: `utc = bar_time - (TimeTradeServer() - TimeGMT())` rounded to nearest 60 s offset.
- `time_msc` of ticks: convert to UTC the same way (subtract offset*1000).
- `results`: results of commands executed since the last successful sync (EA keeps them in a buffer until the POST returns HTTP 200).
- EA ≥ 1.10 also sends:
  - `instance_id`: random string generated in `OnInit`. The server remembers ids seen in the last 30 s; more than one → WARN `Mai multe instanțe AIBridgeEA active` (max once per 5 min).
  - `calendar` + `calendar_ok` — ONLY on the first successful sync after a calendar refresh (on init, every 10 min, or when the server sends `CALENDAR`): `"calendar":[{"t":1730000000,"ccy":"USD","imp":3,"name":"Fed Interest Rate Decision"}],"calendar_ok":true`. Source: `CalendarValueHistory(values, TimeTradeServer()-3600, TimeTradeServer()+86400)` (no country/currency filter) + `CalendarEventById` + `CalendarCountryById`; only importance ≥ MODERATE (`imp`: 1 LOW, 2 MODERATE, 3 HIGH), events with `time_mode` DATE/NOTIME skipped, `t` converted to UTC like bars, max 200 items. If the calendar functions fail: `"calendar":[],"calendar_ok":false` (logged once by the EA).
  - Server storage: `rt.calendar` (list, sorted by `t`), `rt.calendar_ts` (time.time() of the last good update), `rt.bridge["calendar_ok"]`. When `calendar_ok` is false the previous list and `calendar_ts` are kept.
- EA ≥ 1.11 also sends, ONLY on the sync right after a response that contained `HISTX` (kept and resent until a POST returns HTTP 200):
  `"archive":[{"symbol":"EURUSD","tf":"M5","end_utc":1730000000,"bars":[{"t":..,"o":..,"h":..,"l":..,"c":..,"tv":..,"sp":..}],"err":4401}]`
  - `bars`: up to `count` CLOSED bars strictly older than `end_utc`, oldest → newest, from `CopyRates(sym, tf, server_time(end_utc) - 1 s, count, rates)` (UTC→server with the EA offset; bar `t` converted back to UTC like live bars; the server applies the same clock-skew correction). `sp` = MqlRates.spread (minimum spread of the bar, points).
  - `err` is an MT5 error code, or (EA ≥ 1.13) the string `"UNKNOWN_SYMBOL"` when `SymbolSelect` fails for the requested name (EA 1.11/1.12 sent `4301`). The EA `SymbolSelect`s the symbol before `CopyRates` (all versions ≥ 1.11).
  - The EA runs the CopyRates AFTER handling the response that carried `HISTX` (commands already executed), so a slow history download never delays quotes, positions or results; the batch travels in the next request. 5000 bars ≈ 0.5 MB (< 1.5 MB).
  - `err` (only when non-zero): series not ready (`CopyRates` = -1, or 0 bars while the broker has older data / `SERIES_SYNCHRONIZED` false) → `bars: []` + `err`. The server does NOT mark the pair done; it retries after 30 s, 60 s, … (max 10 min).
  - `bars: []` without `err` = the broker has nothing older (or the terminal's *Max bars in chart* limit was reached) → the pair is done.
- EA ≥ 1.14 also sends, ONLY on the sync right after a response that contained `CALHIST` (kept and resent until a POST returns HTTP 200):
  `"calendar_hist":{"from_utc":<echo>,"to_utc":<echo>,"srv_off":<TimeTradeServer()-TimeGMT()>,"ok":true,"err":0,"total":<values matched>,"truncated":false,"items":[{"id":<value.id>,"eid":<event_id>,"t":<ToUtc(value.time)>,"ccy":"USD","cc":"US","imp":3,"name":"Nonfarm Payrolls","tm":<time_mode>,"unit":<unit>,"mult":<multiplier>,"dig":<digits>,"a":<actual|null>,"f":<forecast|null>,"p":<prev|null>,"r":<revised_prev|null>,"imx":<impact_type>,"rev":<revision>,"per":<period, server time>}]}`
  - Source: `CalendarValueHistory(values, from_utc + offset, to_utc + offset)` (no country/currency filter, local terminal database), filtered to importance ≥ `min_imp`. `a`/`f`/`p`/`r` are the raw `MqlCalendarValue` longs (value × 10^6); `LONG_MIN` (not set) → `null`. Items oldest → newest, at most 4000 (`CALHIST_MAX_ITEMS`); when more matched, `"truncated":true` and `total` = the full match count. `CalendarValueHistory` failure → `"ok":false,"err":<GetLastError()>,"items":[]` (server backs off 30 s, doubling to 10 min).
  - The EA reads the calendar AFTER handling the response that carried `CALHIST` (commands already executed) and sends it with the next request, so it never delays quotes, positions or results. ≈ 230 bytes/item (4000 items ≈ 0.9 MB).
  - `calendar_hist` is NOT touched by `bridge.normalize_clock`: `app/calendar_hist.py` removes the clock skew itself and redoes the broker DST conversion per value from `srv_off`. Stored in `calendar_history` (PK `(event_id, t)`), progress in `calendar_history_windows`; research data only, nothing live reads it.

Response (server → EA), `text/plain`, one instruction per line, fields separated by `|`:
```
OK|<server_time_utc>
SYMBOLS|EURUSD,GBPUSD
TF|M1,M5,H1
HIST|EURUSD|M5
OPEN|<cmd_id>|<symbol>|BUY|<volume>|<sl>|<tp>|<deviation_points>|<comment>[|<ref_price>|<max_slip_points>|<expires_utc>]
MODIFY|<cmd_id>|<ticket>|<sl>|<tp>
CLOSE|<cmd_id>|<ticket>|<volume>
CALENDAR
HISTX|<symbol>|<tf>|<end_utc>|<count>
CALHIST|<from_utc>|<to_utc>|<min_imp>
```
- The 3 trailing OPEN fields are sent only when the EA reported `ea_version` ≥ 1.10 (v1.00 parses them fine but ignores them; existing tests pin the 9-field form for 1.00). `ref_price` = ask (BUY) / bid (SELL) when the intent was queued; `max_slip_points` = max(5, round(0.25 × SL distance in points)); `expires_utc` = queue time + 20 s, shifted by the EA's measured `TimeGMT()` error (`rt.bridge["ea_gmt_skew_s"]`). `0` disables a check.
- EA ≥ 1.10 on OPEN: if `TimeGMT() > expires_utc` → result ok=false, retcode 10012, retcode_desc `TIMEOUT (expired)`; if |current ask/bid − ref_price| > max_slip_points × point → ok=false, retcode 10020, retcode_desc `PRICE_CHANGED (ref drift)`. Nothing is sent to the broker in both cases.
- `CALENDAR` (EA ≥ 1.10 only): refresh and resend the calendar on the next sync. Sent when the server has no calendar or it is older than 20 min (max once per 60 s).
- `HISTX` (EA ≥ 1.11 only, `app/archive.py`): archive backfill request, `count` ≤ 5000, `end_utc` in the EA's UTC frame (real UTC + `rt.bridge["clock_skew_s"]`). At most ONE per response, and only when: `ARCHIVE_ENABLED` (default true), the bridge is connected, the response carries no OPEN/MODIFY/CLOSE, no DEMO intent is QUEUED/DELIVERED/UNKNOWN (`executor.PENDING_STATES`), no `HIST` refill is pending, and no earlier HISTX is still unanswered (60 s timeout → retried with backoff). Pairs, in order: for each active symbol, `[signal_tf, htf_tf, "M15"]`. Each pair walks backwards from its oldest archived bar (first request: the open time of the current bar) until `oldest_ts ≤ now − ARCHIVE_DAYS·86400` (default 120) or the EA answers with no bars. `count` = min(`ARCHIVE_BATCH_BARS` (5000), bars left to the target). Rows are written by a single background thread (`INSERT OR IGNORE` into `bars_archive`, 500-row transactions); live bars flushed to `bars` are mirrored into `bars_archive` too, so the archive has no gap between the backfill and now.
- Extended targets (Stage C2): `ARCHIVE_EXTRA` (default `off` — live unchanged; the C2 research setup sets e.g. `H1:1095,H4:1825,D1:3650` in `.env`; `TF:days`, comma-separated; unknown TFs / bad items ignored; `off`/`none`/`0` disables). Per (symbol, tf) the wanted history is `days_target = max(ARCHIVE_DAYS if tf ∈ [signal_tf, htf_tf, M15], ARCHIVE_EXTRA[tf])`, `target_ts = now − days_target·86400`. Phase 1 (base, unchanged order, not throttled): every active symbol × `[signal_tf, htf_tf, "M15"]` walks to `now − ARCHIVE_DAYS·86400`. Phase 2 (extended) starts when no base pair is below its ARCHIVE_DAYS target except pairs waiting in error backoff (those keep priority as soon as their retry is due): TFs of `ARCHIVE_EXTRA` ordered by TF length descending (D1, H4, H1 — cheapest first), each TF for all active symbols before the next TF, walking to the full `target_ts`. Extended HISTX: `count` = min(`ARCHIVE_BATCH_BARS`, 3000, bars left); at most ONE every `ARCHIVE_EXTRA_INTERVAL_S` (default 60 s, measured from the previous extended REPLY — or its 60 s timeout — so a slow `CopyRates` download still leaves the EA a full pause, `rt.archive_plan["extra_last_ts"]`), on top of all the HISTX gating rules above (never while OPEN/MODIFY/CLOSE are in the response, a DEMO intent is pending or the bot holds an MT5 position with its magic — `CopyRates` can block the EA while the terminal downloads multi-year history). `rt.archive_plan["pending"]["extended"]` tells which phase a request belongs to. A pair is `done` when `oldest_ts ≤ target_ts` (full target) or the history is exhausted: an empty reply without `err`, or a reply with no older bar than `oldest_ts`; the pair is added to `rt.archive_plan["exhausted"]` and its real oldest bar is recorded in `rt.archive_plan["history_start"][(symbol, tf)]` (None if the broker has no bars at all). An exhausted base pair is not walked again in phase 2. Exhaustion is in-memory: after a restart the walk resumes once from `oldest_ts` and stops again on the first empty reply.
- `CALHIST` (EA ≥ 1.14 only, `app/calendar_hist.py`, called from `bridge.process_sync` via `calendar_hist.on_sync` after the HISTX line): economic-calendar history with numbers. `from_utc`/`to_utc` in the EA's UTC frame (like HISTX `end_utc`), `min_imp` 0..3 (default `CALHIST_MIN_IMP=1`). At most ONE per response and at most one unanswered (90 s timeout → backoff), and only when: `CALHIST_ENABLED` (default true), not sim, bridge connected, no OPEN/MODIFY/CLOSE in the response nor a DEMO intent pending, no `HIST` refill pending, no `HISTX` in the response nor outstanding, no bot MT5 position (unless `ARCHIVE_EXTRA_WITH_POSITIONS`), and `CALHIST_INTERVAL_S` (default 30 s) since the previous reply. Covers 2015-01-01 → now + 7 days walking back year by year (adaptive window, shrinks on `truncated`); the last 3 days are re-fetched every 6 h. A reply counts only when `from_utc`/`to_utc` echo the outstanding request; anything else is written (upsert) but never touches the plan. Older EAs never receive it.
- Bar time convention (all TFs, M1..D1): `t` in `bars` / `bars_archive` is the UTC instant of the bar OPEN, i.e. the broker's server-time bar open converted with the DST rule above (EA ≥ 1.12) or today's offset (`fixed`, EA 1.11). H4/D1 therefore keep the broker's boundaries and are NOT aligned to UTC multiples of the TF: with a New-York-close broker (GMT+2/+3) a D1 bar opens at broker 00:00 = 21:00 UTC (summer) / 22:00 UTC (winter) of the previous UTC day, H4 bars at 21/01/05/09/13/17 UTC (summer). A bar is closed at `t + TF_SECONDS[tf]` (`backtest.load_bars`, the simulator and the live engine all use open time + TF length); around a broker DST switch the true D1/H4 close differs from that by ±1 h twice a year (accepted). HISTX `end_utc` for the first request of a pair is the UTC-aligned `now // sec · sec`; every bar with `t < end_utc` that is already closed is kept, so no closed broker bar is skipped.
- Reply matching: an `archive` item answers the outstanding HISTX only when its `symbol`, `tf` AND `end_utc` echo that request (`rt.archive_plan["pending"]["ea_end"]`). Anything else (a batch resent after a lost HTTP 200, a late reply after the 60 s timeout) is only written (`INSERT OR IGNORE`); it never marks the pair done/exhausted, never sets an error and never clears the pending request.
- Broker DST (EA ≥ 1.12): the EA stamps every bar with TODAY's server offset. It now also sends `"srv_offset"` (top level, every sync) and `"srv_off"` (per archive item) = `TimeTradeServer()-TimeGMT()` in seconds. The server recovers the broker's server time of each archive bar and converts it with the rule from `MT5_SERVER_TZ` (default `auto`; `EET` = Europe/Athens, EU switch dates; `NY` = America/New_York + 7 h, "New York close" GMT+2/+3; any IANA zone; `fixed` = today's offset for every bar, the pre-1.12 behaviour). `auto` picks the rule whose offset today equals the broker's (`srv_offset` + clock skew); when only one rule matches (the weeks between the EU and US switches) it is remembered per account server in `settings.archive_server_tz`; when both match and nothing is remembered, `NY` is assumed; when none matches, `fixed`. The chosen rule is logged (INFO `archive`). HISTX `end_utc` is shifted the same way so the EA's `end_utc + offset` lands on the broker's server time of that UTC instant. A rule whose offset today differs from the broker's is ignored (`fixed`). Bars archived by EA 1.11 (or with `fixed`) older than the broker's last DST switch can be 1 h off.
- EA ≥ 1.12 copies archive batches from local terminal history only (`CopyRates` by range from `max(SERIES_TERMINAL_FIRSTDATE, end − 2·count·period − 4 days)`; partial batches are normal); the count form that starts (and waits for) a broker download is used only when nothing local is older than `end_utc`.
- Research symbols (`RESEARCH_SYMBOLS`, default `XAUUSD,XAGUSD,US500,US30,NAS100,UK100,DE40,JP225,XTIUSD,XBRUSD,BTCUSD,ETHUSD,USDCHF,USDMXN,USDZAR,USDTRY`; comma list, upper-cased; `off` disables; active symbols are removed from it; `archive.research_list(rt)`): ARCHIVE-ONLY. They are never in `rt.active_symbols()`, the `SYMBOLS|` / `HIST|` lines, `needed_tfs` pairs, analysis, quotes or orders; `bridge.process_sync` drops any quote/bar the EA might send for them (`archive.research_only_names`). Phase 3 of the planner, only when ARCHIVE_EXTRA is on and the EA is ≥ 1.13 (`archive.RESEARCH_EA_VERSION`), and only once no active (base or extended) pair can be requested (pairs in error backoff do not hold it back; they keep priority when their retry is due): TFs D1 → H4 → H1 (`archive.research_targets`: days = `ARCHIVE_EXTRA[tf]`, else D1 3650 / H4 1825 / H1 1095), each TF for all research symbols before the next, same batch (≤ 3000), same `ARCHIVE_EXTRA_INTERVAL_S` throttle (shared `extra_last_ts`, `pending.extended = true`, `pending.research = true`) and the same position gating (`ARCHIVE_EXTRA_WITH_POSITIONS`). Broker names: the HISTX carries the broker name — the resolved one, or the next untried candidate of `archive.RESEARCH_ALIASES` (canonical first: US500 → US500, SPX500, US500.cash, SP500; NAS100 → NAS100, USTEC, NDX100; US30 → US30, DJ30; DE40 → DE40, GER40, DAX40; UK100 → UK100, FTSE100; JP225 → JP225, JPN225, NI225; XTIUSD → XTIUSD, USOIL, WTI; XBRUSD → XBRUSD, UKOIL, BRENT; names of active symbols are skipped). An empty reply with `err` = `"UNKNOWN_SYMBOL"` (EA 1.13: `SymbolSelect` failed; `4301` = ERR_MARKET_UNKNOWN_SYMBOL is treated the same) moves to the next candidate; after the last one the symbol is `available: false` and never asked again. The first reply with bars or a plain empty reply sets `broker` (resolved name) and `available: true`. Bars are archived in `bars_archive` under the CANONICAL name (replies are mapped back via `rt.archive_plan["research_names"]` {broker: canonical}). Resolution state `rt.archive_plan["research"][canonical] = {"idx", "broker", "available", "tried"}` is in-memory (re-probed after a restart).
- Archive status: `rt.archive_status = {symbol: {tf: {"bars": int, "oldest_ts": int|None, "target_ts": int, "done": bool}}}` — rebuilt from `bars_archive` at startup (`COUNT(*)`, `MIN(t)` per pair; also lists inactive symbols already archived), updated on every archive payload. `target_ts` / `done` refer to the full per-TF target (`days_target` above); extended-only TFs get an entry once their walk starts. `archive.status_view(rt)` is the full status payload: `{"enabled", "days_target": ARCHIVE_DAYS, "extra": {tf: days}, "extra_interval_s", "phase": "base"|"extra"|"done", "ea_version", "symbols": {symbol: {tf: {bars, oldest_ts, target_ts, done, days_target, days_loaded, extended, exhausted, history_start_ts}}}}`, listing every extended TF of every active symbol even before its walk starts (TFs ordered longest first). `GET /api/v1/archive/status` returns exactly `archive.status_view(rt)` (`Engine.archive_status()`). Research symbols are NOT in `symbols`; they are listed separately: `"research_enabled"` (research list non-empty and ARCHIVE_EXTRA on), `"research_ea_ok"` (EA ≥ 1.13), `"research_targets": {tf: days}`, `"research": {canonical: {"broker_symbol": str|None, "available": true|false|null (not probed yet), "trying": next candidate|None, "candidates": [...], "tried": [...unknown names], "tfs": {tf: {same fields as symbols}}}}`. `"phase"` is `"base"|"extra"|"research"|"done"`.
- Backtest API and the Stage C2 research plugins (`donchian-v1`, `ma-trend-v1`, `tsmom-v1`): `POST /api/v1/backtest/run` accepts them in `strategy_name` (they stay out of `names()` / `PUT /strategy`, never live). For these: `signal_tf` / `htf_tf` left out → the plugin's `RUN_DEFAULTS` (D1, no HTF) instead of the live timeframes; `htf_tf: "NONE"` allowed; `days` up to 3650 (730 for the live strategies). `cost.swap_mode`: `off|table|zero` for every strategy (default by strategy: off for the intraday set, table for C2).
- Live spread statistics: every sync, each symbol's quote `spread_points` is sampled at most once per 10 s (skipped when the tick is older than 60 s) into an in-memory aggregate per (symbol, UTC hour of day), flushed every 60 s on the background thread into `spread_stats` (n, sum, sum of squares, max are added up).

- EA must `SymbolSelect(sym,true)` for every symbol in SYMBOLS. Unknown TF names ignored. TF names: M1,M5,M15,M30,H1,H4,D1.
- EA keeps an in-memory set of executed cmd_ids (last 500) and **never executes the same cmd_id twice**.
- OPEN: uses CTrade with `SetExpertMagicNumber(InpMagic)`, `SetDeviationInPoints`, filling type chosen from symbol filling_mode (FOK if allowed, else IOC, else RETURN); `PositionOpen(sym, type, vol, price(ask/bid), sl, tp, comment)`. Normalize prices to digits. Before sending: `OrderCheck`; if it fails report ok=false with retcode/comment.
- MODIFY: `PositionModify(ticket, sl, tp)` only if position magic == InpMagic.
- CLOSE: `PositionClosePartial(ticket, vol)` if vol < position volume else `PositionClose(ticket)`; only if magic == InpMagic.
- result.ok = retcode in {10008 PLACED, 10009 DONE, 10010 DONE_PARTIAL}.
- If WebRequest returns -1 → Print error 4014 hint: "Add URL to Tools > Options > Expert Advisors > Allow WebRequest".
- Response HTTP 401 → Print "Bad bridge key".
- EA never trades on its own; it only executes server commands.

Server side bridge rules: `X-Bridge-Key` must equal `MT5_BRIDGE_KEY` (if empty → 403 always). Max body 5 MB. On first sync per (symbol, tf) where server has < 100 bars, include `HIST` line. Commands in state QUEUED are put in the response and marked DELIVERED (delivered_at). Bridge `connected` = last sync < 5 s ago.
Rejected requests (401/403) are logged once per minute with a count: `Cereri EA respinse (cheie greșită): N în ultimul minut`.

Account binding: every DEMO intent (OPEN/MODIFY/CLOSE) stores `account_login` (= `rt.mt5_account.login` at creation) in its data_json. `take_commands` delivers a QUEUED intent only if `account_login` equals the current login, otherwise FAILED with retcode_desc `ACCOUNT_CHANGED`. An OPEN queued more than 20 s ago is never delivered (FAILED `EXPIRED_BEFORE_DELIVERY`). When the login changes inside `process_sync` (where `rt.bridge["account_changed_ts"]` is set), every QUEUED/DELIVERED/UNKNOWN DEMO intent not bound to the new login is FAILED (`ACCOUNT_CHANGED`) and `engine.known_mt5_positions`, `engine.pm.last_action`, `engine.pm.gave_up` are cleared. Reconcile never resolves an UNKNOWN intent bound to another login (it is FAILED `ACCOUNT_CHANGED`).
Retry window: `Executor.consecutive_failures(ticket, kind)` counts only consecutive FAILED intents updated in the last 60 min.

## 4. Strategy v1 (`trend-v1`), deterministic
On every new CLOSED bar of `SIGNAL_TIMEFRAME` (default M5) for each enabled symbol, during the session window:
- Need ≥ 100 closed bars on signal TF and ≥ 60 on HTF (default H1). Only closed bars used.
- Indicators: EMA(20), EMA(50), RSI(14), ATR(14), MACD(12,26,9) hist, HTF EMA(50) and its slope over 3 bars → htf_trend UP/DOWN/FLAT.
- Long score components (each +1): ema20>ema50; close>ema20; macd_hist>0 and rising vs previous; 50<rsi<70; htf_trend==UP. Short symmetric (rsi 30..50, below, DOWN). score = (long-short)/5 in [-1,1].
- BUY if long_score ≥ `MIN_SCORE_COMPONENTS` (default 4) and htf_trend != DOWN; SELL symmetric. Else HOLD.
- Heuristic probabilities (label "heuristic-v1 (NOT calibrated)"): p_dir = 0.5 + 0.25*|score|, p_opp = (1-p_dir)*0.6, neutral = rest.
- entry = ask (BUY) / bid (SELL); SL = entry ∓ `ATR_SL_MULT`(1.5)*ATR; TP = entry ± `ATR_TP_MULT`(2.5)*ATR. Respect stops_level: distance ≥ (stops_level+5)*point else HOLD.
- Filters → HOLD: spread_points > max_spread_points; spread price > 0.2*SL distance; tick age > MAX_TICK_AGE_SECONDS; ATR==0; session closed; bot position already open on symbol; signal count for symbol this session ≥ MAX_SIGNALS_PER_SYMBOL_PER_SESSION.
- Expiry: `SIGNAL_EXPIRY_MINUTES` (default 15). Horizon: 60 min.
- Regime: HIGH_VOL if ATR > 2× median ATR(100); else TREND_UP/DOWN if |ema20-ema50| > 0.5 ATR; else RANGE.

## 5. Environment variables (.env) — names are authoritative
```
APP_ENV=development
HOST=127.0.0.1
PORT=8787
ADMIN_TOKEN=
DATA_SOURCE=mt5_bridge          # mt5_bridge | sim
MT5_BRIDGE_KEY=
MT5_MAGIC=552211
DATABASE_PATH=data/bot.db
DEFAULT_TIMEZONE=Europe/Chisinau
SESSION_ENABLED=true
SESSION_START=09:00
SESSION_END=13:00
SESSION_DAYS=MON,TUE,WED,THU,FRI
ACTIVE_SYMBOLS=EURUSD
SIGNAL_TIMEFRAME=M5
HTF_TIMEFRAME=H1
CHART_TIMEFRAMES=M1,M5,M15,H1
TRADING_MODE=PAPER              # SIGNAL_ONLY | PAPER | DEMO   (LIVE refused in v1)
EXECUTION_ENABLED=false
LIVE_TRADING_ALLOWED=false
MUST_HAVE_SL_TP=true
MAX_RISK_PER_TRADE_PCT=0.25
MAX_DAILY_LOSS_PCT=1.0
MAX_EQUITY_DRAWDOWN_PCT=3.0
MAX_OPEN_POSITIONS=1
MAX_TRADES_PER_DAY=5
MAX_CONSECUTIVE_LOSSES=3
MAX_SPREAD_POINTS=30
MAX_SLIPPAGE_POINTS=20
MIN_FREE_MARGIN_PCT=50
MAX_TICK_AGE_SECONDS=10
MAX_SIGNALS_PER_SYMBOL_PER_SESSION=5
ATR_SL_MULT=1.5
ATR_TP_MULT=2.5
MIN_SCORE_COMPONENTS=4
SIGNAL_EXPIRY_MINUTES=15
BREAK_EVEN_AT_R=1.0
TRAILING_ENABLED=false
MAX_HOLD_MINUTES=240
CLOSE_AT_SESSION_END=false
PAPER_START_BALANCE=10000
ANTHROPIC_API_KEY=
CLAUDE_ENABLED=true
CLAUDE_MODEL=claude-opus-5-5
CLAUDE_VETO_ENABLED=true
CLAUDE_REQUIRED=false
CLAUDE_TIMEOUT_SECONDS=45
TELEGRAM_ENABLED=false
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
TELEGRAM_MESSAGE_THREAD_ID=
PUBLIC_APP_URL=http://127.0.0.1:8787
```

## 6. Risk Engine checks (each produces {"name","ok","detail"}; ALL must pass)
mode_allows_execution (PAPER/DEMO and EXECUTION_ENABLED, not SIGNAL_ONLY, not LIVE) · entries_not_paused · bridge_connected (DEMO) · account_is_demo (DEMO) · terminal_trade_allowed (DEMO: terminal.trade_allowed && account.trade_expert) · quote_fresh · spread_ok · sl_tp_present · sl_distance_ok (≥ stops_level) · volume_ok (volume = floor_to_step(equity*risk_pct/100 / (sl_dist/tick_size*tick_value)); if < volume_min → fail "risk too small for min lot" except test-order uses volume_min and checks its risk ≤ 2×max_risk) · max_open_positions · max_trades_per_day · daily_loss_ok (equity vs day-start equity, day boundary in DEFAULT_TIMEZONE) · drawdown_ok (equity vs peak equity) · consecutive_losses_ok · free_margin_ok (margin_free/equity*100 ≥ MIN_FREE_MARGIN_PCT) · no_existing_bot_position_on_symbol.

## 7. Storage additions (stage B; additive migrations on the existing `bot.db`)
`app/db.py` creates these with `CREATE TABLE IF NOT EXISTS` and adds missing columns with `ALTER TABLE … ADD COLUMN` guarded by `PRAGMA table_info` (`DB.migrated` lists what was added). Nothing is dropped or rewritten.
```
bars_archive(symbol TEXT, tf TEXT, t INTEGER, o REAL, h REAL, l REAL, c REAL, tv REAL, sp REAL, PRIMARY KEY(symbol,tf,t))
spread_stats(symbol TEXT, hour_utc INTEGER, n INTEGER, sum_pts REAL, sum_sq REAL, max_pts REAL, PRIMARY KEY(symbol,hour_utc))
analyses(id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT, tf TEXT, bar_time INTEGER, created_ts REAL, account_key TEXT,
         direction TEXT, candidate_side TEXT, entry REAL, sl REAL, tp REAL, score REAL, blockers_json TEXT, signal_id TEXT,
         ai_verdict TEXT, outcome TEXT, outcome_r REAL, mfe_r REAL, mae_r REAL, labeled_ts REAL, UNIQUE(symbol,tf,bar_time))
equity_snapshots(account_key TEXT, ts INTEGER, balance REAL, equity REAL, PRIMARY KEY(account_key,ts))
claude_usage(id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, signal_id TEXT, model TEXT, input_tokens INTEGER,
             output_tokens INTEGER, cache_read_tokens INTEGER, cost_usd REAL)
backtest_runs(id TEXT PRIMARY KEY, created_ts REAL, status TEXT, params_json TEXT, metrics_json TEXT, error TEXT)
trades  + account_key TEXT, signal_entry REAL, initial_sl REAL, risk_money REAL, r_multiple REAL
signals + account_key TEXT        intents + account_key TEXT
```
Indexes: `ix_analyses_bar(bar_time)`, `ix_analyses_signal(signal_id)`, `ix_claude_usage_ts(ts)`.
`spread_stats.hour_utc` is the UTC hour of day (0–23); mean = sum_pts/n, sd = sqrt(sum_sq/n − mean²). `bars.sp` / `bars_archive.sp` are the MT5 bar spread (minimum within the bar), not the average.
Env: `ARCHIVE_ENABLED=true`, `ARCHIVE_DAYS=120`, `ARCHIVE_BATCH_BARS=5000`, `ARCHIVE_EXTRA=off` (research: `H1:1095,H4:1825,D1:3650`), `ARCHIVE_EXTRA_INTERVAL_S=60`, `MT5_SERVER_TZ=auto`, `RESEARCH_SYMBOLS=XAUUSD,…,USDTRY` (archive only, see §3 HISTX). Calendar history (EA ≥ 1.14, see §3 CALHIST): `CALHIST_ENABLED=true`, `CALHIST_INTERVAL_S=30`, `CALHIST_MIN_IMP=1`.
Shadow labels (`app/labeler.py`): BE moves the stop to entry ± 2 points like `position_manager.plan`, only when the bar's favourable price leaves spread/stops level + 1 point; entries and ask-side exits use the `spread_stats` mean of that UTC hour (fallback max(bar sp, 1)), stop exits 1 point slippage; `outcome_r` is GROSS of commission. With `max_hold_minutes=0` (no live time stop) open setups are closed at a 24 h horizon with outcome `HORIZON`. Backtests read bars through their own read-only SQLite connection (`backtest.ReadOnlyDB`), never the shared locked one.
