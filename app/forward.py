"""Forward test (PAPER, simulated only): independent runner inside the live engine loop.

Each configured test has its OWN book (forward_positions / forward_trades). It never creates intents, never queues
bridge commands, never touches the paper broker or the live position manager, and its decisions never feed back
into the live strategy (the live context — positions, exposures, signal counts — is not read or changed).

Config: env FORWARD_TESTS (JSON list), overridden by the settings key 'forward_tests' when that is a list. Item:
  {name, strategy, signal_tf='H1', htf_tf='H4', symbols='active'|[...], risk_pct=0.5, equity=200,
   time_stop_minutes=240, started_utc (set on first run), criteria {min_trades, max_weeks, ci_lower_gt, avg_r_gt, pf_gt}}

Strategy 'fade-v1' (pre-registered mirror of trend-v1, NOT tuned): whenever trend-v1 (same live params, the item's
signal/HTF timeframes) emits BUY/SELL after its own market filters (spread / ATR / stops / tick age), fade-v1 opens the
OPPOSITE side on the same quote with SL distance = trend-v1's TP distance and TP distance = trend-v1's SL distance.
No break-even, no trailing, time stop, one position per symbol, max 1 per currency+direction in fade-v1's own book.
NO news blackout (not in the pre-registered definition; the STAGE_C2 backtest has no news filter). Any other registered plugin name runs as-is with the forward book as its context.

Entries only on a newly CLOSED bar of signal_tf (rt.closed_bars), at the live quote (BUY at ask, SELL at bid).
Exits on every live tick with a FRESH quote (tick age <= risk max_tick_age_seconds: a stale quote re-ingested after a
weekend / reconnect never closes a position), conservative order: SL first (BUY: bid <= SL, filled at min(bid, SL); SELL: ask >= SL,
filled at max(ask, SL)), then TP (filled at TP), then the time stop (market: bid/ask).
r = realized R with the live demo spread (entry at ask/bid, exit at bid/ask: the demo spread is already paid once);
r_adj = r - extra cost / SL distance, extra cost = max(0, standard spread (STAGE_C2 table) - demo spread at entry)
+ 1 point slippage (STAGE_C2 STANDARD = spread + 1 point, charged once); symbols missing from the table add no extra
spread. r_adj is the primary metric. usd_adj = r_adj × equity × risk_pct / 100 (fixed equity, min lot ignored).
Verdict: the decision sample is fixed in advance — the first min_trades closed trades, or the trades closed before
started + max_weeks if that comes first — and is frozen in forward_meta.decided_json the first time it is reached; later
trades only update the informational stats, never the verdict (no repeated peeking)."""
from __future__ import annotations

import json
import math
import time
import uuid

from . import strategy as strategy_mod
from .config import TF_SECONDS, iso, parse_iso

FADE = "fade-v1"
FADE_BASE = "trend-v1"
SIGNAL_FRESH_S = 90  # same as the live engine: no entry on a bar that closed long ago (restart / reconnect)
CONFIG_RELOAD_S = 60.0
WEEK_S = 7 * 86400
Z90 = 1.645
# standard-account spreads (points) from docs/research/STAGE_C2_RESULTS.md (cost model STANDARD)
STD_SPREAD_POINTS = {"EURUSD": 10, "GBPUSD": 14, "USDJPY": 12, "AUDUSD": 12, "USDCAD": 16, "EURJPY": 18,
                     "GBPJPY": 28, "NZDUSD": 16, "EURGBP": 14, "EURCHF": 18, "AUDJPY": 18}
STD_SLIPPAGE_POINTS = 1.0  # STAGE_C2 STANDARD slippage (points, once per trade)
DEFAULT_CRITERIA = {"min_trades": 100, "max_weeks": 8, "ci_lower_gt": -0.05, "avg_r_gt": 0.0, "pf_gt": 1.15}
VERDICT_RUNNING, VERDICT_PROMISING, VERDICT_REJECTED = "ÎN CURS", "PROMIȚĂTOR", "RESPINS"
REASON_RO = {"SL": "Stop loss", "TP": "Take profit", "TIME": "Limită de timp"}


# ---- pure helpers (tested directly) -------------------------------------------
def std_spread_points(symbol: str, fallback: float | None = None) -> float | None:
    letters = "".join(ch for ch in str(symbol).upper() if ch.isalpha())[:6]
    v = STD_SPREAD_POINTS.get(letters)
    return float(v) if v is not None else (None if fallback is None else float(fallback))


def realized_r(side: str, entry: float, sl: float, exit_price: float) -> float:
    risk = abs(entry - sl)
    if risk <= 0:
        return 0.0
    return ((exit_price - entry) if side == "BUY" else (entry - exit_price)) / risk


def extra_cost_points(std_spread_pts: float | None, spread_pts_at_entry: float | None) -> float:
    """Cost (points) on top of the demo spread already inside r: the standard spread not yet paid + slippage."""
    extra = 0.0
    if std_spread_pts is not None and spread_pts_at_entry is not None:
        extra = max(0.0, float(std_spread_pts) - float(spread_pts_at_entry))
    return extra + STD_SLIPPAGE_POINTS


def cost_adjusted_r(r: float, entry: float, sl: float, point: float, spread_points: float | None) -> float:
    """r minus a cost in points converted to R by the position's initial SL distance."""
    risk = abs(entry - sl)
    if risk <= 0 or not spread_points:
        return r
    return r - float(spread_points) * float(point) / risk


def mirror_levels(base_side: str, base_entry: float, base_sl: float, base_tp: float, bid: float, ask: float) -> dict:
    """fade-v1: opposite side on the same quote, SL distance = base TP distance, TP distance = base SL distance."""
    sl_d, tp_d = abs(base_entry - base_sl), abs(base_tp - base_entry)
    side = "SELL" if base_side == "BUY" else "BUY"
    entry = ask if side == "BUY" else bid
    sign = 1 if side == "BUY" else -1
    return {"side": side, "entry": entry, "sl": entry - sign * tp_d, "tp": entry + sign * sl_d}


def tick_exit(pos: dict, bid: float, ask: float, now: float, time_stop_s: float) -> tuple[str, float] | None:
    """(reason, exit price) for this tick or None. Conservative order: SL, then TP, then time stop."""
    sl, tp = float(pos["sl"]), float(pos["tp"])
    if pos["side"] == "BUY":
        if bid <= sl:
            return "SL", min(bid, sl)
        if bid >= tp:
            return "TP", tp
        px = bid
    else:
        if ask >= sl:
            return "SL", max(ask, sl)
        if ask <= tp:
            return "TP", tp
        px = ask
    if time_stop_s > 0 and now - float(pos["opened_ts"]) >= time_stop_s:
        return "TIME", px
    return None


def summarize(rs: list[float]) -> dict:
    n = len(rs)
    if not n:
        return {"trades_count": 0, "win_rate": None, "avg_r_adj": None, "ci90_adj": None, "pf_adj": None,
                "sum_r_adj": 0.0}
    m = sum(rs) / n
    ci = None
    if n >= 2:
        sd = math.sqrt(sum((x - m) ** 2 for x in rs) / (n - 1))
        h = Z90 * sd / math.sqrt(n)
        ci = [round(m - h, 4), round(m + h, 4)]
    gw = sum(x for x in rs if x > 0)
    gl = -sum(x for x in rs if x < 0)
    return {"trades_count": n, "win_rate": round(sum(1 for x in rs if x > 0) / n, 4), "avg_r_adj": round(m, 4),
            "ci90_adj": ci, "pf_adj": round(gw / gl, 3) if gl > 0 else None, "sum_r_adj": round(sum(rs), 3)}


def criteria_check(stats: dict, weeks: float, crit: dict) -> tuple[str, list[dict]]:
    """Verdict is decided only when min_trades is reached or max_weeks elapsed; then ALL criteria must pass."""
    n = stats["trades_count"]
    ci = stats.get("ci90_adj")
    lo = ci[0] if ci else None
    avg, pf = stats.get("avg_r_adj"), stats.get("pf_adj")
    if pf is None and n and avg is not None and avg > 0:
        pf = math.inf  # no losing trade
    checks = [
        {"key": "ci_lower_gt", "label": "Limita inferioară IC90% a R mediu ajustat >", "threshold": crit["ci_lower_gt"],
         "value": lo, "ok": lo is not None and lo > crit["ci_lower_gt"]},
        {"key": "avg_r_gt", "label": "R mediu ajustat >", "threshold": crit["avg_r_gt"], "value": avg,
         "ok": avg is not None and avg > crit["avg_r_gt"]},
        {"key": "pf_gt", "label": "Profit factor ajustat >", "threshold": crit["pf_gt"],
         "value": None if pf is None or pf == math.inf else pf, "ok": pf is not None and pf > crit["pf_gt"]},
    ]
    gate = [
        {"key": "min_trades", "label": "Tranzacții (decizie la minim)", "threshold": crit["min_trades"], "value": n,
         "ok": n >= crit["min_trades"]},
        {"key": "max_weeks", "label": "Săptămâni scurse (decizie la maxim)", "threshold": crit["max_weeks"],
         "value": round(weeks, 2), "ok": weeks >= crit["max_weeks"]},
    ]
    decided = gate[0]["ok"] or gate[1]["ok"]
    if not decided:
        verdict = VERDICT_RUNNING
    else:
        verdict = VERDICT_PROMISING if all(c["ok"] for c in checks) else VERDICT_REJECTED
    return verdict, gate + checks


def decision_sample(rows: list[dict], started_ts: float, crit: dict, now: float) -> tuple[list[dict] | None, float | None]:
    """(trades of the pre-registered decision sample, decision ts), or (None, None) while the gate is not reached.
    rows sorted by closed_ts. Gate: the min_trades-th closed trade, or started + max_weeks, whichever comes first.
    The sample depends only on the trades and the clock, never on when or how often the page is viewed."""
    deadline = started_ts + float(crit["max_weeks"]) * WEEK_S
    k = max(1, int(crit["min_trades"]))
    if len(rows) >= k and float(rows[k - 1]["closed_ts"]) <= deadline:
        return rows[:k], float(rows[k - 1]["closed_ts"])
    if now >= deadline:
        return [r for r in rows if float(r["closed_ts"]) <= deadline], deadline
    return None, None


def normalize_item(raw) -> dict:
    """Validated test config (raises ValueError with a Romanian message)."""
    if not isinstance(raw, dict):
        raise ValueError("fiecare test forward trebuie să fie un obiect JSON")
    name = str(raw.get("name") or "").strip()
    if not name or len(name) > 60:
        raise ValueError("testul forward are nevoie de un nume (max 60 caractere)")
    strat = str(raw.get("strategy") or "").strip()
    if not strat:
        raise ValueError(f"testul {name}: lipsește strategia")
    sig, htf = str(raw.get("signal_tf") or "H1").upper(), str(raw.get("htf_tf") or "H4").upper()
    if sig not in TF_SECONDS or htf not in TF_SECONDS:
        raise ValueError(f"testul {name}: timeframe necunoscut ({sig}/{htf})")
    syms = raw.get("symbols", "active")
    if isinstance(syms, str):
        syms = "active" if syms.strip().lower() in ("", "active") else [s.strip().upper() for s in syms.split(",") if s.strip()]
    elif isinstance(syms, list):
        syms = [str(s).strip().upper() for s in syms if str(s).strip()]
    else:
        raise ValueError(f"testul {name}: symbols trebuie să fie 'active' sau o listă")
    crit = dict(DEFAULT_CRITERIA)
    for k, v in (raw.get("criteria") or {}).items():
        if k in crit:
            crit[k] = float(v)
    crit["min_trades"] = int(crit["min_trades"])
    out = {"name": name, "strategy": strat, "signal_tf": sig, "htf_tf": htf, "symbols": syms,
           "risk_pct": float(raw.get("risk_pct", 0.5)), "equity": float(raw.get("equity", 200)),
           "time_stop_minutes": float(raw.get("time_stop_minutes", 240)), "criteria": crit,
           "started_utc": raw.get("started_utc")}
    if out["risk_pct"] <= 0 or out["equity"] <= 0:
        raise ValueError(f"testul {name}: risk_pct și equity trebuie să fie > 0")
    return out


class ForwardRunner:
    """Owned by the Engine; driven by Engine.loop_once (bars, time stops) and a quote hook (tick exits)."""

    def __init__(self, engine):
        self.engine = engine
        self.rt = engine.rt
        self.db = engine.db
        self.tests: list[dict] = []
        self.positions: dict[str, dict[str, dict]] = {}  # name -> symbol -> position
        self.last_bar: dict[tuple[str, str], int] = {}
        self.started: dict[str, float] = {}
        self._cfg_ts = 0.0
        self._cfg_sig: str | None = None
        self._warned: set[str] = set()
        self._orig_needed_tfs = None
        self.clock = time.time  # wall clock for tick exits / time stops (tests replace it)
        self.rt.quote_hooks.append(self.on_quote)

    # ---- config / lifecycle ----------------------------------------------
    def _warn_once(self, key: str, msg: str) -> None:
        if key not in self._warned:
            self._warned.add(key)
            self.rt.log("WARN", "forward", msg)

    def raw_config(self) -> list:
        saved = self.db.get_setting("forward_tests")
        if isinstance(saved, list):
            return saved
        try:
            v = json.loads(getattr(self.engine.settings, "FORWARD_TESTS", "[]") or "[]")
        except (TypeError, ValueError):
            self._warn_once("cfg_json", "FORWARD_TESTS nu este JSON valid — testele forward sunt dezactivate")
            return []
        return v if isinstance(v, list) else []

    def registered(self, name: str) -> bool:
        reg = self.engine.strategy_registry()
        if reg is None:
            return name == FADE_BASE
        try:
            reg.get(name)
            return True
        except Exception:
            return False

    def strategy_known(self, name: str) -> bool:
        return name == FADE or self.registered(name)

    def reload_config(self, now: float | None = None) -> None:
        now = now or time.time()
        self._cfg_ts = now
        raw = self.raw_config()
        sig = json.dumps(raw, sort_keys=True, default=str)
        if sig == self._cfg_sig:
            return
        self._cfg_sig = sig
        tests, seen = [], set()
        for item in raw:
            try:
                t = normalize_item(item)
            except (ValueError, TypeError) as e:
                self._warn_once(f"cfg:{e}", f"Test forward ignorat: {e}")
                continue
            if t["name"] in seen:
                self._warn_once(f"dup:{t['name']}", f"Test forward duplicat ignorat: {t['name']}")
                continue
            if not self.strategy_known(t["strategy"]):
                self._warn_once(f"strat:{t['strategy']}", f"Test forward {t['name']}: strategie necunoscută {t['strategy']}")
                continue
            seen.add(t["name"])
            tests.append(t)
        self.tests = tests
        for t in tests:
            self._ensure_meta(t, now)
            self.positions.setdefault(t["name"], {})

    def _ensure_meta(self, t: dict, now: float) -> None:
        name = t["name"]
        row = self.db.query_one("SELECT started_ts FROM forward_meta WHERE name=?", (name,))
        if row is None:
            ts = now
            if t.get("started_utc"):
                try:
                    ts = parse_iso(str(t["started_utc"])).timestamp()
                except (TypeError, ValueError, AttributeError):
                    ts = now
            self.db.execute("INSERT OR IGNORE INTO forward_meta(name, started_ts, config_json) VALUES(?,?,?)",
                            (name, ts, json.dumps(t)))
            self.started[name] = ts
            self.rt.log("INFO", "forward", f"Test forward {name} ({t['strategy']} {t['signal_tf']}/{t['htf_tf']}) "
                                           f"pornit — SIMULAT, fără ordine reale")
        else:
            self.started[name] = float(row["started_ts"])
            self.db.execute("UPDATE forward_meta SET config_json=? WHERE name=?", (json.dumps(t), name))

    def startup(self) -> None:
        self.reload_config()
        self.load_positions()
        if self._orig_needed_tfs is None:
            self._orig_needed_tfs = self.rt.needed_tfs
            self.rt.needed_tfs = self.needed_tfs  # the EA streams the forward timeframes too

    def needed_tfs(self) -> list[str]:
        tfs = list(self._orig_needed_tfs()) if self._orig_needed_tfs else []
        for t in self.tests:
            for tf in (t["signal_tf"], t["htf_tf"]):
                if tf in TF_SECONDS and tf not in tfs:
                    tfs.append(tf)
        return sorted(tfs, key=lambda x: TF_SECONDS[x])

    def load_positions(self) -> None:
        """Restart-safe: open forward positions come back from the DB."""
        self.positions = {t["name"]: {} for t in self.tests}
        for r in self.db.query("SELECT * FROM forward_positions"):
            pos = self._pos_from_row(r)
            self.positions.setdefault(pos["name"], {})[pos["symbol"]] = pos
        for r in self.db.query("SELECT name, symbol, MAX(bar_time) AS bt FROM ("
                               "SELECT name, symbol, bar_time FROM forward_positions UNION ALL "
                               "SELECT name, symbol, bar_time FROM forward_trades) GROUP BY name, symbol"):
            if r["bt"] is not None:
                self.last_bar[(r["name"], r["symbol"])] = int(r["bt"])

    @staticmethod
    def _pos_from_row(r: dict) -> dict:
        extra = {}
        try:
            extra = json.loads(r.get("data_json") or "{}")
        except ValueError:
            pass
        return {"id": r["id"], "name": r["name"], "symbol": r["symbol"], "side": r["side"], "entry": r["entry"],
                "sl": r["sl"], "tp": r["tp"], "opened_ts": r["opened_ts"], "bar_time": r["bar_time"],
                "point": r["point"], "digits": r["digits"], "spread_pts": r["spread_pts"],
                "std_spread_pts": r["std_spread_pts"], **extra}

    def test(self, name: str) -> dict | None:
        return next((t for t in self.tests if t["name"] == name), None)

    def symbols(self, t: dict) -> list[str]:
        return self.rt.active_symbols() if t["symbols"] == "active" else list(t["symbols"])

    # ---- engine loop hook -------------------------------------------------
    def tick(self, now: float | None = None) -> None:
        now = now or self.clock()
        if now - self._cfg_ts >= CONFIG_RELOAD_S:
            self.reload_config(now)
        for t in self.tests:
            for sym in self.symbols(t):
                try:
                    self._maybe_analyze(t, sym, now)
                except Exception as e:
                    self._warn_once(f"an:{t['name']}:{sym}:{type(e).__name__}", f"Test forward {t['name']} {sym}: analiză eșuată: {e!r}")
            # time stops without new ticks (fresh quote only)
            for sym in list(self.positions.get(t["name"], {})):
                q = self.rt.quotes.get(sym)
                if q and self._fresh(sym, now):
                    self._check_exit(t, sym, q, now)

    def _fresh(self, sym: str, now: float) -> bool:
        """Same guard as the live engine: exits only on a quote no older than risk max_tick_age_seconds."""
        age = self.rt.tick_age(sym, now)
        return age is not None and age <= float(self.rt.risk.get("max_tick_age_seconds", 10))

    def _last_closed_t(self, sym: str, tf: str, now: float) -> int | None:
        series = self.rt.bars.get((sym, tf)) or []
        if not series:
            return None
        sec = TF_SECONDS[tf]
        if series[-1]["t"] + sec <= now:
            return int(series[-1]["t"])
        return int(series[-2]["t"]) if len(series) >= 2 else None

    def _maybe_analyze(self, t: dict, sym: str, now: float) -> None:
        rt = self.rt
        tf, htf = t["signal_tf"], t["htf_tf"]
        if (sym, tf) in rt.hist_force or (sym, htf) in rt.hist_force:
            return
        last_t = self._last_closed_t(sym, tf, now)
        key = (t["name"], sym)
        if last_t is None or self.last_bar.get(key) == last_t:
            return
        if self.engine.settings.data_source == "mt5_bridge" and int(rt.bridge.get("sync_count") or 0) < 1:
            return  # wait for the first EA sync (the bar is retried; freshness below still applies)
        self.last_bar[key] = last_t
        if now - (last_t + TF_SECONDS[tf]) >= SIGNAL_FRESH_S:
            return  # bar closed long ago (restart / reconnect): no entry
        self.analyze(t, sym, now)

    # ---- decision -----------------------------------------------------------
    def _ctx(self, t: dict, sym: str, now: float) -> strategy_mod.AnalysisContext:
        book = self.positions.get(t["name"], {})
        news_block = None  # fade-v1: no news blackout (not in the pre-registered definition / STAGE_C2 evidence)
        if t["strategy"] != FADE:
            news_block, _ = self.engine.news_check(sym, now)
        return strategy_mod.AnalysisContext(
            session_open=True, has_bot_position=sym in book, signals_this_session=0,
            tick_age=self.rt.tick_age(sym, now), now_ts=now,
            open_exposures=[(p["symbol"], p["side"]) for p in book.values()], corr_limit=1, news_block=news_block)

    def _cfg(self, t: dict, plugin: str) -> dict:
        reg = self.engine.strategy_registry()
        over = {"signal_timeframe": t["signal_tf"], "htf_timeframe": t["htf_tf"]}
        if reg is not None:
            return reg.merged_cfg(plugin, self.rt.strategy, over)
        return {**self.rt.strategy, **over}

    def _plugin(self, name: str):
        reg = self.engine.strategy_registry()
        if reg is None:
            return strategy_mod.analyze
        return reg.get(name).analyze

    def analyze(self, t: dict, sym: str, now: float) -> dict | None:
        """Run the test's strategy on closed bars; open a simulated position on BUY/SELL. Returns the decision."""
        rt = self.rt
        q = rt.quotes.get(sym)
        if not q or not q.get("bid") or not q.get("ask"):
            return None
        sig_bars = rt.closed_bars(sym, t["signal_tf"], now)[-400:]
        htf_bars = rt.closed_bars(sym, t["htf_tf"], now)[-200:]
        bid, ask = float(q["bid"]), float(q["ask"])
        if t["strategy"] == FADE and not self.registered(FADE):
            # built-in mirror (fallback when the fade-v1 plugin is not registered): trend-v1 on its own market filters
            # only (no book/session/news/correlation context), then fade-v1's own context on the opposite side
            base_ctx = strategy_mod.AnalysisContext(session_open=True, has_bot_position=False, signals_this_session=0,
                                                    tick_age=rt.tick_age(sym, now), now_ts=now, open_exposures=[],
                                                    corr_limit=0, news_block=None)
            a = self._plugin(FADE_BASE)(sym, sig_bars, htf_bars, q, self._cfg(t, FADE_BASE), rt.risk, base_ctx)
            if a.get("direction") not in ("BUY", "SELL"):
                return None
            m = mirror_levels(a["direction"], float(a["entry"]), float(a["stop_loss"]), float(a["take_profit"]), bid, ask)
            blockers = strategy_mod.context_blockers(sym, m["side"], self._ctx(t, sym, now), rt.risk)
            if blockers:
                return {"side": m["side"], "blocked": blockers}
            side, entry, sl, tp = m["side"], m["entry"], m["sl"], m["tp"]
            base = {"base_side": a["direction"], "base_entry": a["entry"], "base_sl": a["stop_loss"],
                    "base_tp": a["take_profit"]}
        else:
            a = self._plugin(t["strategy"])(sym, sig_bars, htf_bars, q, self._cfg(t, t["strategy"]), rt.risk,
                                            self._ctx(t, sym, now))
            if a.get("direction") not in ("BUY", "SELL"):
                return None
            side = a["direction"]
            entry = ask if side == "BUY" else bid
            shift = entry - float(a["entry"] if a.get("entry") is not None else entry)  # keep the plugin's distances
            sl, tp = float(a["stop_loss"]) + shift, float(a["take_profit"]) + shift
            base = {"plugin_entry": a.get("entry")}
        return self.open(t, sym, side, entry, sl, tp, q, now, int(sig_bars[-1]["t"]) if sig_bars else None, base)

    # ---- book ----------------------------------------------------------------
    def open(self, t: dict, sym: str, side: str, entry: float, sl: float, tp: float, q: dict, now: float,
             bar_time: int | None, extra: dict | None = None) -> dict:
        digits = int(q.get("digits") or 5)
        point = float(q.get("point") or 10 ** -digits)
        spread = q.get("spread_points")
        if spread is None:
            spread = round((float(q["ask"]) - float(q["bid"])) / point)
        pos = {"id": uuid.uuid4().hex, "name": t["name"], "symbol": sym, "side": side, "entry": float(entry),
               "sl": round(float(sl), digits), "tp": round(float(tp), digits), "opened_ts": float(now),
               "bar_time": bar_time, "point": point, "digits": digits, "spread_pts": float(spread),
               "std_spread_pts": std_spread_points(sym, fallback=spread), **(extra or {})}
        data = {k: v for k, v in pos.items() if k not in _POS_COLS}
        self.db.execute(
            "INSERT INTO forward_positions(id, name, symbol, side, entry, sl, tp, opened_ts, bar_time, point, digits, "
            "spread_pts, std_spread_pts, data_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (pos["id"], pos["name"], sym, side, pos["entry"], pos["sl"], pos["tp"], pos["opened_ts"], bar_time, point,
             digits, pos["spread_pts"], pos["std_spread_pts"], json.dumps(data)))
        self.positions.setdefault(t["name"], {})[sym] = pos
        self.rt.log("INFO", "forward", f"[SIMULAT] {t['name']}: {side} {sym} @ {pos['entry']} SL {pos['sl']} TP {pos['tp']}")
        return pos

    def on_quote(self, symbol: str, now: float | None = None) -> None:
        """Quote hook (every live tick): SL/TP/time-stop exits for the forward books only."""
        if not any(symbol in b for b in self.positions.values()):
            return
        q = self.rt.quotes.get(symbol)
        if not q:
            return
        now = now or self.clock()
        if not self._fresh(symbol, now):
            return  # stale quote re-ingested (market closed / reconnect): nobody could trade at that price
        for t in self.tests:
            if symbol in self.positions.get(t["name"], {}):
                try:
                    self._check_exit(t, symbol, q, now)
                except Exception as e:
                    self._warn_once(f"exit:{t['name']}:{type(e).__name__}", f"Test forward {t['name']} {symbol}: ieșire eșuată: {e!r}")

    def _check_exit(self, t: dict, sym: str, q: dict, now: float) -> dict | None:
        pos = self.positions.get(t["name"], {}).get(sym)
        bid, ask = q.get("bid"), q.get("ask")
        if not pos or not bid or not ask:
            return None
        hit = tick_exit(pos, float(bid), float(ask), now, t["time_stop_minutes"] * 60)
        if not hit:
            return None
        return self.close(t, pos, hit[0], hit[1], now)

    def close(self, t: dict, pos: dict, reason: str, price: float, now: float) -> dict:
        r = realized_r(pos["side"], pos["entry"], pos["sl"], price)
        cost_pts = extra_cost_points(pos.get("std_spread_pts"), pos.get("spread_pts"))
        r_adj = cost_adjusted_r(r, pos["entry"], pos["sl"], pos["point"], cost_pts)
        usd_adj = r_adj * t["equity"] * t["risk_pct"] / 100.0
        tr = {"id": pos["id"], "name": pos["name"], "symbol": pos["symbol"], "side": pos["side"], "entry": pos["entry"],
              "sl": pos["sl"], "tp": pos["tp"], "opened_ts": pos["opened_ts"], "closed_ts": float(now),
              "exit_price": round(float(price), int(pos.get("digits") or 5)), "reason": reason, "r": round(r, 4),
              "r_adj": round(r_adj, 4), "usd_adj": round(usd_adj, 4), "bar_time": pos.get("bar_time")}
        data = {k: v for k, v in pos.items() if k not in _POS_COLS}
        data.update({"point": pos["point"], "spread_pts": pos["spread_pts"], "std_spread_pts": pos.get("std_spread_pts"),
                     "extra_cost_pts": cost_pts})
        with self.db.lock:
            c = self.db.conn
            c.execute("BEGIN")
            try:
                c.execute("INSERT OR IGNORE INTO forward_trades(id, name, symbol, side, entry, sl, tp, opened_ts, closed_ts, "
                          "exit_price, reason, r, r_adj, usd_adj, bar_time, data_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                          (tr["id"], tr["name"], tr["symbol"], tr["side"], tr["entry"], tr["sl"], tr["tp"], tr["opened_ts"],
                           tr["closed_ts"], tr["exit_price"], reason, tr["r"], tr["r_adj"], tr["usd_adj"], tr["bar_time"],
                           json.dumps(data)))
                c.execute("DELETE FROM forward_positions WHERE id=?", (pos["id"],))
                c.execute("COMMIT")
            except Exception:
                c.execute("ROLLBACK")
                raise
        self.positions.get(pos["name"], {}).pop(pos["symbol"], None)
        self.rt.log("INFO", "forward", f"[SIMULAT] {pos['name']}: închis {pos['side']} {pos['symbol']} "
                                       f"({REASON_RO.get(reason, reason)}) {tr['r']:+.2f}R, ajustat {tr['r_adj']:+.2f}R")
        return tr

    def reset(self, name: str, now: float | None = None) -> dict:
        now = now or time.time()
        with self.db.lock:
            c = self.db.conn
            c.execute("BEGIN")
            try:
                n_tr = c.execute("DELETE FROM forward_trades WHERE name=?", (name,)).rowcount
                n_pos = c.execute("DELETE FROM forward_positions WHERE name=?", (name,)).rowcount
                c.execute("DELETE FROM forward_meta WHERE name=?", (name,))
                c.execute("COMMIT")
            except Exception:
                c.execute("ROLLBACK")
                raise
        self.positions[name] = {}
        for k in [k for k in self.last_bar if k[0] == name]:
            self.last_bar.pop(k, None)
        self.started.pop(name, None)
        t = self.test(name)
        if t:
            self._ensure_meta({**t, "started_utc": None}, now)
        self.rt.log("WARN", "forward", f"Test forward {name} resetat ({n_tr} tranzacții, {n_pos} poziții șterse)")
        return {"name": name, "deleted_trades": n_tr, "deleted_positions": n_pos, "started_utc": iso(self.started.get(name))}

    # ---- views -----------------------------------------------------------------
    def payload(self, now: float | None = None) -> list[dict]:
        now = now or time.time()
        return [self.test_payload(t, now) for t in self.tests]

    def test_payload(self, t: dict, now: float) -> dict:
        name = t["name"]
        rows = self.db.query("SELECT * FROM forward_trades WHERE name=? ORDER BY closed_ts, id", (name,))
        rs = [float(r["r_adj"]) for r in rows if r["r_adj"] is not None]
        stats = summarize(rs)
        started = self.started.get(name, now)
        weeks = max(0.0, (now - started) / WEEK_S)
        decision = self._decision(t, rows, started, now)
        if decision is None:
            verdict, checks = criteria_check(stats, weeks, t["criteria"])
        else:
            verdict, checks = decision["verdict"], decision["checks"]
        cum, curve = 0.0, []
        for r in rows:
            cum += float(r["r_adj"] or 0)
            curve.append([int(r["closed_ts"]), round(cum, 3)])
        open_list = []
        for p in self.positions.get(name, {}).values():
            q = self.rt.quotes.get(p["symbol"]) or {}
            px = q.get("bid") if p["side"] == "BUY" else q.get("ask")
            open_list.append({
                "id": p["id"], "symbol": p["symbol"], "side": p["side"], "entry": p["entry"], "sl": p["sl"],
                "tp": p["tp"], "opened_utc": iso(p["opened_ts"]), "opened_ts": p["opened_ts"], "price": px,
                "r_now": round(realized_r(p["side"], p["entry"], p["sl"], float(px)), 3) if px else None,
                "minutes": round((now - p["opened_ts"]) / 60, 1),
            })
        last = [{"symbol": r["symbol"], "side": r["side"], "entry": r["entry"], "sl": r["sl"], "tp": r["tp"],
                 "opened_utc": iso(r["opened_ts"]), "closed_utc": iso(r["closed_ts"]), "exit_price": r["exit_price"],
                 "reason": r["reason"], "r": r["r"], "r_adj": r["r_adj"], "usd_adj": r["usd_adj"]}
                for r in reversed(rows[-20:])]
        cfg = {k: v for k, v in t.items() if k != "started_utc"}
        return {"name": name, "strategy": t["strategy"], "config": cfg, "simulated": True,
                "started_utc": iso(started), "weeks_elapsed": round(weeks, 2), "open": open_list,
                **stats, "usd_adj": round(sum(float(r["usd_adj"] or 0) for r in rows), 2),
                "equity_curve_adj": curve, "verdict": verdict, "criteria": t["criteria"], "checks": checks,
                "decision": decision, "last_trades": last}

    def _decision(self, t: dict, rows: list[dict], started: float, now: float) -> dict | None:
        """Frozen verdict: computed once on the pre-registered decision sample, then read back from forward_meta."""
        name = t["name"]
        row = self.db.query_one("SELECT decided_json FROM forward_meta WHERE name=?", (name,))
        if row and row.get("decided_json"):
            try:
                return json.loads(row["decided_json"])
            except ValueError:
                pass
        sample, ts = decision_sample(rows, started, t["criteria"], now)
        if sample is None:
            return None
        st = summarize([float(r["r_adj"]) for r in sample if r["r_adj"] is not None])
        by_deadline = len(sample) < max(1, int(t["criteria"]["min_trades"]))
        weeks = float(t["criteria"]["max_weeks"]) if by_deadline else max(0.0, (ts - started) / WEEK_S)
        verdict, checks = criteria_check(st, weeks, t["criteria"])
        d = {"verdict": verdict, "checks": checks, "stats": st, "trades_count": len(sample), "decided_ts": ts,
             "decided_utc": iso(ts), "frozen_utc": iso(now),
             "note": (f"Verdict înghețat pe primele {len(sample)} tranzacții (eșantion pre-înregistrat); "
                      f"tranzacțiile de după sunt doar informative.")}
        self.db.execute("UPDATE forward_meta SET decided_json=? WHERE name=?", (json.dumps(d), name))
        return d


_POS_COLS = {"id", "name", "symbol", "side", "entry", "sl", "tp", "opened_ts", "bar_time", "point", "digits",
             "spread_pts", "std_spread_pts"}

__all__ = ["ForwardRunner", "FADE", "STD_SPREAD_POINTS", "mirror_levels", "tick_exit", "realized_r", "cost_adjusted_r",
           "summarize", "criteria_check", "normalize_item", "std_spread_points", "extra_cost_points", "decision_sample"]
