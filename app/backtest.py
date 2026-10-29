"""Walk-forward backtest of the live strategy on stored bars (bars_archive ∪ bars).

Decisions use exactly the live code path: strategy.analyze on CLOSED bars only (last 400 signal-TF bars, last 200 closed
HTF bars at the decision time), quote = decision bar close + modelled spread, then the same context blockers
(strategy.context_blockers) and the same Risk Engine (risk.evaluate) with a simulated book/account.
Execution: fill at the next bar open (+spread for BUY, + slippage), SL/TP/BE/time stop bar by bar (labeler.SimPosition,
SL first when both are touched in one bar), commission per lot round trip.

Phase 1 (per symbol, parallel processes): strategy.analyze with a neutral context -> candidate levels + market blockers.
Phase 2 (sequential, all symbols in time order): context blockers, risk checks, sizing, fills, exits.
The split is exact because analyze() = market blockers + context_blockers(); a test pins the equivalence.

Stage C research: params.strategy_name picks a plugin from app.strategies (default trend-v1 = the live engine),
params.exit = {mode, params} an exit policy from app.exits (default base = live exits), cost.per_symbol_spread_pts
fixed spreads per symbol, record_paths/paths_out per-trade bar paths (JSONL). run_cached() keeps phase 1 (and the
loaded bars) in a caller-owned dict, so a sweep over exit policies only re-runs phase 2.

Stage C2 (higher timeframes, long holds): signal_tf H1/H4/D1, htf_tf a higher TF or 'NONE' (no HTF series is
loaded; plugins may ignore it). A plugin's RUN_DEFAULTS (backtest-only: timeframes, no session, no time stop, no BE,
swap on) apply unless the run sets the key; its default_exit(cfg) is the exit policy when params.exit is absent;
the 'signal_flip' exit asks the plugin's bias() at every closed bar (memoised in the run_cached cache).
exit.params max_hold_minutes/hours/days (≤ 60 days, 0 = none) override the strategy's time stop.
Swap/financing: cost.swap_mode 'off' (default for the intraday strategies: nothing computed, results unchanged),
'table' (cost.swap_pts_per_day {symbol: [long_pts, short_pts]} in points per day, negative = cost; missing symbols
use SWAP_DEFAULT_PTS = -0.5 pip per side) or 'zero' (sensitivity: swap reported as 0). Charged per broker-midnight
rollover (17:00 New York = 21:00/22:00 UTC, the D1 bar boundary) strictly inside the holding interval (Mon–Fri,
Wednesday ×3; an exit at a bar close = broker midnight does not pay that night) on the full volume, converted to USD with the trade's tick value and to R
with its initial risk; trades carry swap_usd/swap_r/rollovers and the summary the totals.

Live-parity pack (same setting keys as the live bot, read from the resolved strategy/risk config). resolve() fills
them from the live Settings defaults / saved settings like every other key (BE_LOCK_R 0.1, RESCUE_ENABLED, cooldown 30,
rollover 20), so a run that wants the old behaviour must switch them off explicitly (strategy rescue_enabled false,
be_lock_r 0; risk cooldown_after_loss_minutes 0, rollover_blackout_minutes 0):
- strategy rescue_enabled + rescue_* -> the live RESCUE exit, exits 'rescue' combined with the run's exit policy
  (exits.with_rescue; base -> rescue, X -> combo [X, rescue]) for the live strategy; research plugins with their own
  RUN_DEFAULTS / default_exit are left as they are; strategy {"rescue_enabled": false} turns it off; strategy be_lock_r -> smart break-even lock in SimPosition / exits._try_be;
- risk cooldown_after_loss_minutes -> no entry on a symbol for N minutes after a trade on it closed at a loss
  (net < 0 and |net| ≥ 0.1R, same neutral band as live; AnalysisContext.last_loss_ts = that trade's exit time); risk rollover_blackout_minutes -> no entry within ±N
  minutes of 17:00 America/New_York — both are strategy.context_blockers (phase 2, before sizing; live: before Claude);
- pre-AI arbitration order: candidates on the same bar close are processed HTF-aligned first, then by the lowest
  spread / SL-distance cost ratio (symbol order on ties); each sees the exposure of the ones admitted before it.

CLI: python -m app.backtest --symbols EURUSD,GBPUSD --days 90
"""
from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import math
import os
import pickle
import random
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, time as dtime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import exits
from . import risk as risk_mod
from . import scheduler, strategy
from .config import ROOT_DIR, TF_SECONDS, get_settings, iso
from .labeler import SimPosition, guess_point

SIG_WINDOW = 400  # same as engine.analyze_symbol
HTF_WINDOW = 200
NEUTRAL_R = 0.1  # same as engine.NEUTRAL_R (|net| below 0.1R is neither win nor loss for the loss streak)
CHUNK = 2000  # decisions per worker job
WORKER_TIMEOUT_S = 1800
BOOTSTRAP_N = 1000
BOOTSTRAP_SEED = 1729
MAX_TRADES_OUT = 300
MAX_CURVE_PTS = 500
DEFAULT_COMMISSION = 7.0
DEFAULT_SLIPPAGE = 1.0
DEFAULT_STRATEGY = "trend-v1"
# USD value of one unit of the quote currency when no loaded pair gives the rate (warned)
SWAP_MODES = ("off", "table", "zero")
SWAP_DEFAULT_PTS = (-5.0, -5.0)  # -0.5 pip/day long and short (5 points on 5-digit and 3-digit JPY pairs)
ROLLOVER_UTC_H = 22  # legacy constant (winter value); rollovers() uses ROLLOVER_TZ / ROLLOVER_LOCAL_H
ROLLOVER_TZ = ZoneInfo("America/New_York")  # New-York-close broker: rollover at broker midnight = 17:00 NY
ROLLOVER_LOCAL_H = 17
INTRABAR_REASONS = ("SL", "TP", "BE", "TRAIL", "RESCUE")  # exits somewhere inside the bar (stamped with the bar open)
PHASE2_STRATEGY_KEYS = ("be_lock_r", "rescue_enabled") + exits.RESCUE_KEYS
PHASE2_RISK_KEYS = ("cooldown_after_loss_minutes", "rollover_blackout_minutes")  # neutral-context blockers in phase 1
APPROX_USD = {"EUR": 1.08, "GBP": 1.27, "JPY": 1 / 150.0, "CHF": 1.12, "CAD": 0.73, "AUD": 0.65, "NZD": 0.59,
              "SEK": 0.095, "NOK": 0.092, "SGD": 0.74, "HKD": 0.128}


# ---- data -----------------------------------------------------------------

class ReadOnlyDB:
    """Minimal read-only view (CLI): never creates tables or writes to the bot database."""

    def __init__(self, path: str):
        self.conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row

    def query(self, sql: str, params: tuple | list = ()) -> list[dict]:
        return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    def close(self) -> None:
        self.conn.close()


def _has_table(db, name: str) -> bool:
    try:
        return bool(db.query("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)))
    except Exception:
        return False


def _setting(db, key: str):
    try:
        rows = db.query("SELECT value_json FROM settings WHERE key=?", (key,))
        return json.loads(rows[0]["value_json"]) if rows else None
    except Exception:
        return None


def load_bars(db, symbol: str, tf: str, t_from: int, t_to: int, now_ts: float | None = None) -> list[dict]:
    """Closed bars with t in [t_from, t_to), archive first (final CopyRates values), live `bars` fills the rest."""
    sec = TF_SECONDS[tf]
    now_ts = time.time() if now_ts is None else now_ts
    out: dict[int, dict] = {}
    for table in ("bars_archive", "bars"):
        if not _has_table(db, table):
            continue
        for r in db.query(f"SELECT t,o,h,l,c,sp FROM {table} WHERE symbol=? AND tf=? AND t>=? AND t<? ORDER BY t",
                          (symbol, tf, int(t_from), int(t_to))):
            t = int(r["t"])
            if t in out or t + sec > now_ts or r["o"] is None:
                continue
            out[t] = {"t": t, "o": float(r["o"]), "h": float(r["h"]), "l": float(r["l"]), "c": float(r["c"]),
                      "sp": float(r["sp"] or 0)}
    return [out[t] for t in sorted(out)]


def load_spread_stats(db) -> dict[tuple[str, int], float]:
    if not _has_table(db, "spread_stats"):
        return {}
    out = {}
    for r in db.query("SELECT symbol, hour_utc, n, sum_pts FROM spread_stats WHERE n > 0"):
        out[(r["symbol"], int(r["hour_utc"]))] = float(r["sum_pts"]) / float(r["n"])
    return out


# ---- strategy plugins -----------------------------------------------------------

def _registry():
    try:
        from . import strategies as reg  # lazy: the package is optional for trend-v1
    except ModuleNotFoundError as e:
        if e.name not in ("app.strategies", __package__ + ".strategies"):
            raise
        return None
    return reg


def get_plugin(name: str | None):
    """Plugin module for a strategy name (None when the registry is missing and name is trend-v1)."""
    name = name or DEFAULT_STRATEGY
    reg = _registry()
    if reg is None:
        if name == DEFAULT_STRATEGY:
            return None
        raise ValueError(f"Strategie necunoscută: {name} (registrul app.strategies lipsește)")
    try:
        return reg.get(name)
    except KeyError as e:
        raise ValueError(str(e.args[0]) if e.args else f"Strategie necunoscută: {name}") from None


def get_analyzer(name: str | None):
    """analyze(symbol, sig_bars, htf_bars, quote, cfg, risk, ctx) of a strategy (looked up at call time)."""
    mod = get_plugin(name)
    return strategy.analyze if mod is None else mod.analyze


# ---- config ---------------------------------------------------------------

def resolve(db, params: dict) -> dict:
    """Live defaults (.env + settings saved in the DB, like Runtime.load_runtime_config) + params overrides."""
    s = get_settings()
    strat, rsk, sched = s.default_strategy(), s.default_risk(), s.default_schedule()
    for key, cur in (("strategy", strat), ("risk", rsk), ("schedule", sched)):
        saved = _setting(db, key)
        if isinstance(saved, dict):
            for k, v in saved.items():
                if k in cur:
                    cur[k] = v
    strategy_name = str(params.get("strategy_name") or DEFAULT_STRATEGY)
    plugin = get_plugin(strategy_name)
    reg = _registry()
    if plugin is not None and reg is not None and hasattr(reg, "merged_cfg"):
        strat = reg.merged_cfg(strategy_name, strat, params.get("strategy") or {})
    else:
        if plugin is not None:
            for k, v in (getattr(plugin, "PARAMS", None) or {}).items():
                strat.setdefault(k, v)
        strat.update(params.get("strategy") or {})
    run_def = dict(getattr(plugin, "RUN_DEFAULTS", None) or {}) if plugin is not None else {}
    own = params.get("strategy") or {}
    for k, v in run_def.items():
        if k not in ("session", "swap_mode") and k not in own:
            strat[k] = v
    if params.get("signal_tf"):
        strat["signal_timeframe"] = str(params["signal_tf"]).upper()
    if params.get("htf_tf"):
        strat["htf_timeframe"] = str(params["htf_tf"]).upper()
    rsk.update(params.get("risk") or {})
    if params.get("risk_pct") is not None:
        rsk["max_risk_per_trade_pct"] = float(params["risk_pct"])
    if "session" in params:
        sess = params["session"]
        sched = None if sess is None else {**sched, "enabled": True, **sess}
    elif "session" in run_def:
        sess = run_def["session"]
        sched = None if sess is None else {**sched, "enabled": True, **sess}
    elif not sched.get("enabled", True):
        sched = None
    if str(strat["htf_timeframe"]).upper() == "NONE":
        strat["htf_timeframe"] = "NONE"  # no HTF series (plugins that ignore the HTF)
    for tf in (strat["signal_timeframe"], strat["htf_timeframe"]):
        if tf not in TF_SECONDS and (tf != "NONE" or tf == strat["signal_timeframe"]):
            raise ValueError(f"timeframe necunoscut: {tf}")
    symbols = [str(x).upper() for x in (params.get("symbols") or strat.get("symbols") or [])]
    if not symbols:
        raise ValueError("niciun simbol pentru backtest")
    now = float(params.get("now_ts") or time.time())
    if params.get("start_ts") is not None:
        start = int(params["start_ts"])
        end = int(params.get("end_ts") or now)
    else:
        end = int(params.get("end_ts") or now)
        start = end - int(params.get("days") or 30) * 86400
    if end <= start:
        raise ValueError("interval invalid (end_ts <= start_ts)")
    cost = {"mode": "stats", "fixed_spread_pts": None, "commission_per_lot_usd": DEFAULT_COMMISSION,
            "slippage_pts": DEFAULT_SLIPPAGE}
    raw_cost = params.get("cost") or {}
    cost.update({k: v for k, v in raw_cost.items() if v is not None and k not in ("swap_mode", "swap_pts_per_day")})
    if cost["mode"] not in ("stats", "bar", "fixed"):
        raise ValueError(f"cost.mode invalid: {cost['mode']}")
    per_sym = {}
    for k, v in (cost.get("per_symbol_spread_pts") or {}).items():
        try:
            fv = float(v)
        except (TypeError, ValueError):
            raise ValueError(f"cost.per_symbol_spread_pts.{k}: număr invalid") from None
        if not 0 <= fv <= 1000:
            raise ValueError(f"cost.per_symbol_spread_pts.{k}: între 0 și 1000")
        per_sym[str(k).upper()] = fv
    cost["per_symbol_spread_pts"] = per_sym
    _resolve_swap(cost, raw_cost, run_def)
    if cost["mode"] == "fixed" and cost.get("fixed_spread_pts") is None and any(s_ not in per_sym for s_ in symbols):
        raise ValueError("cost.fixed_spread_pts lipsește pentru mode=fixed")
    if params.get("exit") is None and callable(getattr(plugin, "default_exit", None)):
        exit_policy = exits.ExitPolicy.from_spec(plugin.default_exit(strat))
    else:
        exit_policy = exits.ExitPolicy.from_spec(params.get("exit"))
    # live RESCUE exit (strategy rescue_enabled): an overlay on the run's exit policy for the live strategy (trend-v1 and
    # plugins without backtest-only RUN_DEFAULTS / their own default_exit); cfg["exit"] stays the policy as given
    live_rescue = None
    if strat.get("rescue_enabled") and not run_def and not callable(getattr(plugin, "default_exit", None)):
        live_rescue = {k: float(strat[k] if strat.get(k) is not None else exits.LIVE_RESCUE_DEFAULTS[k])
                       for k in exits.RESCUE_KEYS}
    # live PROFIT_LOCK trailing (strategy profit_lock_enabled): same overlay rule as the RESCUE exit
    live_profit_lock = None
    if strat.get("profit_lock_enabled") and not run_def and not callable(getattr(plugin, "default_exit", None)):
        live_profit_lock = {k: float(strat[k] if strat.get(k) is not None else exits.DEFAULT_PARAMS["profit_lock"][k])
                            for k in exits.PROFIT_LOCK_KEYS}
    if "signal_flip" in exit_policy.modes and not callable(getattr(plugin, "bias", None)):
        raise ValueError(f"exit signal_flip: strategia {strategy_name} nu oferă bias() (direcția pe fiecare bară)")
    max_hold_s = None  # None -> strategy max_hold_minutes, exactly as before
    if exit_policy.hold_minutes is not None:
        max_hold_s = int(round(exit_policy.hold_minutes * 60))
    elif not 0 <= float(strat.get("max_hold_minutes") or 0) <= exits.MAX_HOLD_MINUTES:
        raise ValueError("max_hold_minutes: între 0 și 60 de zile (0 = fără limită)")
    if params.get("record_paths") and not params.get("paths_out"):
        raise ValueError("record_paths necesită paths_out (cale fișier JSONL)")
    return {"strategy": strat, "risk": rsk, "schedule": sched, "symbols": symbols, "start_ts": start, "end_ts": end,
            "now_ts": now, "cost": cost, "initial_equity": float(params.get("initial_equity") or 200.0),
            "size_mode": params.get("size_mode") or "live", "halt_on_drawdown": bool(params.get("halt_on_drawdown")),
            "specs": params.get("specs") or {}, "tz": (sched or {}).get("tz") or s.DEFAULT_TIMEZONE,
            "workers": params.get("workers"), "strategy_name": strategy_name,
            "strategy_version": getattr(plugin, "VERSION", None) if plugin is not None else strategy.STRATEGY_VERSION,
            "exit": exit_policy, "live_rescue": live_rescue, "live_profit_lock": live_profit_lock,
            "record_paths": bool(params.get("record_paths")),
            "all_trades": bool(params.get("all_trades")),
            "paths_out": params.get("paths_out"), "max_hold_s": max_hold_s}


def _resolve_swap(cost: dict, raw_cost: dict, run_def: dict) -> None:
    """cost.swap_* in place; nothing is added when swap is off (results identical to the pre-swap backtest)."""
    mode = raw_cost.get("swap_mode")
    if mode is None:
        mode = "table" if raw_cost.get("swap_pts_per_day") else run_def.get("swap_mode", "off")
    mode = str(mode).lower()
    if mode not in SWAP_MODES:
        raise ValueError(f"cost.swap_mode invalid: {mode} (unul din {', '.join(SWAP_MODES)})")
    if mode == "off":
        return
    table = {}
    for k, v in (raw_cost.get("swap_pts_per_day") or {}).items():
        try:
            lo, sh = (float(x) for x in v)
        except (TypeError, ValueError):
            raise ValueError(f"cost.swap_pts_per_day.{k}: [puncte long, puncte short]") from None
        if not (abs(lo) <= 10000 and abs(sh) <= 10000):
            raise ValueError(f"cost.swap_pts_per_day.{k}: valori prea mari")
        table[str(k).upper()] = [lo, sh]
    cost["swap_mode"] = mode
    cost["swap_pts_per_day"] = table
    cost["swap_default_pts"] = list(SWAP_DEFAULT_PTS)


def rollovers(t_open: float, t_end: float) -> int:
    """Swap days charged for a position open over the open interval (t_open, t_end): one per broker-midnight rollover
    (17:00 New York, i.e. 21:00 UTC in summer / 22:00 UTC in winter) from Monday to Friday, three on Wednesday (the
    weekend), none on Saturday/Sunday. A rollover exactly at the fill or at the exit instant is not charged (a D1
    bar opens and closes at broker midnight)."""
    if t_end <= t_open:
        return 0
    d = datetime.fromtimestamp(t_open, ROLLOVER_TZ).date()
    n = 0
    while True:
        r = datetime.combine(d, dtime(ROLLOVER_LOCAL_H), tzinfo=ROLLOVER_TZ).timestamp()
        if r >= t_end:
            break
        if r > t_open and d.weekday() < 5:
            n += 3 if d.weekday() == 2 else 1
        d += timedelta(days=1)
    return n


def swap_points(cost: dict, symbol: str, side: str) -> float:
    """Swap per day in points for one side (0 in mode 'zero' or 'off')."""
    if cost.get("swap_mode") != "table":
        return 0.0
    lo, sh = (cost.get("swap_pts_per_day") or {}).get(symbol.upper()) or cost.get("swap_default_pts") or SWAP_DEFAULT_PTS
    return float(lo if side == "BUY" else sh)


class Session:
    """Session window lookups over a whole date range (precomputed, DST-safe through app.scheduler)."""

    def __init__(self, schedule: dict | None, tz: str, t_from: float, t_to: float):
        self.schedule = schedule
        self.tz = tz
        self.wins: list[tuple[float, float]] = []
        if schedule:
            around = datetime.fromtimestamp(t_from, timezone.utc)
            days = int((t_to - t_from) // 86400) + 3
            self.wins = [(a.timestamp(), b.timestamp()) for a, b in scheduler.windows_utc(schedule, around, 1, days)]
            self.starts = [a for a, _ in self.wins]
        self._day_cache: dict[int, float] = {}

    def _win(self, ts: float) -> tuple[float, float] | None:
        if not self.wins:
            return None
        k = bisect.bisect_right(self.starts, ts) - 1
        if k >= 0 and self.wins[k][0] <= ts < self.wins[k][1]:
            return self.wins[k]
        return None

    def open(self, ts: float) -> bool:
        return True if self.schedule is None else self._win(ts) is not None

    def day_start(self, ts: float) -> float:
        h = int(ts // 3600)
        v = self._day_cache.get(h)
        if v is None:
            v = scheduler.local_day_start_utc(datetime.fromtimestamp(ts, timezone.utc), self.tz).timestamp()
            self._day_cache[h] = v
        return v

    def start(self, ts: float) -> float:
        """Like scheduler.session_start_utc: current window start, else local midnight."""
        w = self._win(ts)
        return w[0] if w else self.day_start(ts)


def symbol_spec(symbol: str, price: float, specs: dict) -> dict:
    sp = dict(specs.get(symbol) or {})
    point = float(sp.get("point") or guess_point(symbol, price))
    digits = int(sp.get("digits") if sp.get("digits") is not None else max(0, round(-math.log10(point))))
    letters = "".join(ch for ch in symbol.upper() if ch.isalpha())
    contract = float(sp.get("contract_size") or (100 if letters.startswith("XAU") else 5000 if letters.startswith("XAG") else 100000))
    return {"symbol": symbol, "point": point, "digits": digits, "tick_size": float(sp.get("tick_size") or point),
            "contract_size": contract, "volume_min": float(sp.get("volume_min") or 0.01),
            "volume_step": float(sp.get("volume_step") or 0.01), "volume_max": float(sp.get("volume_max") or 100.0),
            "stops_level": float(sp.get("stops_level") or 0), "tick_value": sp.get("tick_value")}


# ---- phase 1: candidates per symbol ----------------------------------------

def scan_symbol(job: dict) -> dict:
    """Run strategy.analyze on every requested closed bar with a neutral context (process-pool safe).
    Returns {"symbol", "decisions": {i: (side, entry, sl, tp, score, market_blockers)}, "n": analyses run,
    "aligned": {i: 1 for HTF-aligned decisions}}."""
    symbol, cfg, rsk = job["symbol"], job["strategy"], job["risk"]
    sec, hsec = TF_SECONDS[cfg["signal_timeframe"]], TF_SECONDS.get(cfg["htf_timeframe"], 0)
    point, digits, stops_level = job["point"], job["digits"], job["stops_level"]
    sig = [{"t": t, "o": o, "h": h, "l": l, "c": c} for t, o, h, l, c in job["sig"]]
    htf = [{"t": t, "o": o, "h": h, "l": l, "c": c} for t, o, h, l, c in job["htf"]]
    htf_close = [b["t"] + hsec for b in htf]
    spreads = job["spreads"]
    corr = int(rsk.get("max_same_currency_direction", 1))
    analyze = get_analyzer(job.get("strategy_name"))
    out: dict[int, tuple] = {}
    aligned: dict[int, int] = {}  # decisions whose side agrees with the HTF trend (pre-AI arbitration order)
    n = 0
    for i in job["idx"]:
        b = sig[i]
        T = b["t"] + sec
        k = bisect.bisect_right(htf_close, T)
        # decision at the bar CLOSE: the live bot reads the spread of that instant = the hour the NEXT bar opens in;
        # spreads[i] is the decision bar's own (opening-hour / in-bar) spread — stale by one bar on stats/fixed series
        sp_pts = spreads[i + 1] if job.get("decision_spread_next") and i + 1 < len(spreads) else spreads[i]
        quote = {"bid": b["c"], "ask": round(b["c"] + sp_pts * point, digits + 3), "spread_points": sp_pts,
                 "digits": digits, "point": point, "stops_level": stops_level}
        ctx = strategy.AnalysisContext(session_open=True, has_bot_position=False, signals_this_session=0,
                                       tick_age=0.0, now_ts=T, open_exposures=[], corr_limit=corr)
        a = analyze(symbol, sig[max(0, i - SIG_WINDOW + 1): i + 1], htf[max(0, k - HTF_WINDOW): k],
                    quote, cfg, rsk, ctx)
        n += 1
        cand = a.get("candidate")
        if not cand:
            continue
        neutral = set(strategy.context_blockers(symbol, cand["side"], ctx, rsk))
        market = tuple(x for x in cand["blockers"] if x not in neutral)
        out[job.get("offset", 0) + i] = (cand["side"], cand["entry"], cand["stop_loss"], cand["take_profit"],
                                          cand["score"], market)
        if htf_aligned(cand["side"], a):
            aligned[job.get("offset", 0) + i] = 1
    return {"symbol": symbol, "decisions": out, "n": n, "aligned": aligned}


def htf_aligned(side: str, a: dict) -> int:
    """1 when the higher-timeframe trend of the analysis agrees with the side (BUY+UP / SELL+DOWN), else 0."""
    htf = str(((a or {}).get("indicators") or {}).get("htf_trend") or "").upper()
    return 1 if (side == "BUY" and htf == "UP") or (side == "SELL" and htf == "DOWN") else 0


def arbitration_key(aligned: int, spread_px: float, entry: float, sl: float) -> tuple[int, float]:
    """Pre-AI arbitration order of candidates on the same bar close (live engine rule): HTF-aligned first, then the
    lowest cost ratio spread / SL distance. Sort ascending; ties keep the incoming (symbol) order."""
    dist = abs(float(entry) - float(sl))
    return (0 if aligned else 1, spread_px / dist if dist > 0 else math.inf)


def _scan_in_subprocess(batch: list[dict]) -> list[dict]:
    """One worker = `python -m app.backtest --scan-worker` (pickled jobs on stdin, results on stdout).
    A plain subprocess never re-imports the caller's __main__ (multiprocessing spawn would)."""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT_DIR) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    p = subprocess.run([sys.executable, "-m", "app.backtest", "--scan-worker"], input=pickle.dumps(batch),
                       capture_output=True, cwd=str(ROOT_DIR), env=env, timeout=WORKER_TIMEOUT_S)
    if p.returncode != 0:
        tail = p.stderr.decode("utf-8", "replace").strip().splitlines()[-1:] or ["?"]
        raise RuntimeError(f"worker exit {p.returncode}: {tail[0]}")
    return pickle.loads(p.stdout)


def _worker_main() -> int:
    out = sys.stdout.buffer
    sys.stdout = sys.stderr  # stray prints must not corrupt the pickled result
    try:
        os.nice(10)  # the live bot keeps priority over a backtest
    except (AttributeError, OSError):
        pass
    jobs = pickle.load(sys.stdin.buffer)
    pickle.dump([scan_symbol(j) for j in jobs], out)
    out.flush()
    return 0


def _run_scans(jobs: list[dict], workers: int, warnings: list[str], progress=None) -> list[dict]:
    total = sum(len(j["idx"]) for j in jobs) or 1
    out: list[dict] = []

    def tick(rs: list[dict]) -> None:
        out.extend(rs)
        done = sum(x["n"] for x in out)
        _progress(progress, 0.05 + 0.8 * done / total, f"analizate {done} bare")

    if workers > 1 and len(jobs) > 1 and total > 2 * CHUNK:
        try:
            from concurrent.futures import ThreadPoolExecutor
            n_b = min(len(jobs), workers * 2)
            buckets = [jobs[k::n_b] for k in range(n_b)]
            with ThreadPoolExecutor(max_workers=workers) as ex:
                for rs in ex.map(_scan_in_subprocess, buckets):
                    tick(rs)
            return out
        except Exception as e:  # no subprocesses allowed / worker crash: one process
            warnings.append(f"Rulare paralelă indisponibilă ({e!r}) — rulez secvențial.")
            out.clear()
    for j in jobs:
        tick([scan_symbol(j)])
    return out


# ---- phase 2 helpers ---------------------------------------------------------

def _letters(sym: str) -> str:
    return "".join(ch for ch in sym.upper() if ch.isalpha())


class _Prices:
    """Close of the last closed signal-TF bar at a time, for USD conversion of tick values."""

    def __init__(self, series: dict[str, list[dict]], sec: int):
        self.sec = sec
        self.by_letters = {}
        for sym, bars in series.items():
            self.by_letters[_letters(sym)[:6]] = ([b["t"] + sec for b in bars], [b["c"] for b in bars])
        self.approx_used: set[str] = set()

    def close_at(self, letters: str, ts: float) -> float | None:
        s = self.by_letters.get(letters)
        if not s:
            return None
        k = bisect.bisect_right(s[0], ts) - 1
        return s[1][k] if k >= 0 else None

    def usd_per(self, ccy: str, ts: float) -> float | None:
        if ccy == "USD":
            return 1.0
        px = self.close_at("USD" + ccy, ts)
        if px:
            return 1.0 / px
        px = self.close_at(ccy + "USD", ts)
        if px:
            return px
        if ccy in APPROX_USD:
            self.approx_used.add(ccy)
            return APPROX_USD[ccy]
        return None


def _tick_value(spec: dict, prices: _Prices, ts: float, price: float) -> float | None:
    if spec.get("tick_value"):
        return float(spec["tick_value"])
    q = _letters(spec["symbol"])[3:6]
    base = _letters(spec["symbol"])[:3]
    if q == "USD":
        rate = 1.0
    elif base == "USD":
        rate = 1.0 / price if price else None
    else:
        rate = prices.usd_per(q, ts)
    return None if rate is None else spec["tick_size"] * spec["contract_size"] * rate


def bootstrap_ci(values: list[float], n: int = BOOTSTRAP_N, seed: int = BOOTSTRAP_SEED, level: float = 0.90) -> list:
    if len(values) < 2:
        return [None, None]
    rng = random.Random(seed)
    m = len(values)
    means = sorted(math.fsum(rng.choices(values, k=m)) / m for _ in range(n))
    lo = means[int((1 - level) / 2 * n)]
    hi = means[min(n - 1, int((1 + level) / 2 * n) - 1)]
    return [round(lo, 4), round(hi, 4)]


def summarize(trades: list[dict], ci: bool = False) -> dict:
    n = len(trades)
    if not n:
        return {"trades": 0, "win_rate": None, "avg_r": None, "expectancy_r": None, "profit_factor": None,
                "max_dd_r": 0.0, "max_consec_losses": 0, "net_r": 0.0, "net_usd": 0.0,
                **({"ci90_avg_r": [None, None]} if ci else {})}
    rs = [t["r"] for t in trades]
    gp = math.fsum(r for r in rs if r > 0)
    gl = -math.fsum(r for r in rs if r < 0)
    cum = peak = dd = 0.0
    streak = mcl = 0
    for t in sorted(trades, key=lambda x: x["exit_t"]):
        cum += t["r"]
        peak = max(peak, cum)
        dd = max(dd, peak - cum)
        streak = streak + 1 if t["r"] < 0 else 0
        mcl = max(mcl, streak)
    wins = sum(1 for r in rs if r > 0)
    avg = math.fsum(rs) / n
    out = {"trades": n, "win_rate": round(wins / n, 4), "avg_r": round(avg, 4), "expectancy_r": round(avg, 4),
           "profit_factor": round(gp / gl, 3) if gl > 0 else None, "max_dd_r": round(dd, 3),
           "max_consec_losses": mcl, "net_r": round(math.fsum(rs), 3),
           "net_usd": round(math.fsum(t.get("usd") or 0.0 for t in trades), 2)}
    if ci:
        out["ci90_avg_r"] = bootstrap_ci(rs)
    return out


def _progress(cb, frac: float, msg: str) -> None:
    if cb is None:
        return
    try:
        cb(round(frac, 3), msg)
    except TypeError:
        cb(round(frac, 3))
    except Exception:
        pass


# ---- main -----------------------------------------------------------------------

def run(db, params: dict, progress=None) -> dict:
    return run_cached(db, params, None, progress)


def _digest(obj) -> str:
    return hashlib.sha1(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()


def run_cached(db, params: dict, cache: dict | None, progress=None) -> dict:
    """run() with phase 1 (strategy analyses) and the loaded bars kept in `cache` (a dict owned by the caller, one
    per database). Phase-1 key: strategy name + settings, risk settings, symbols, period, timeframes, session, specs
    and the modelled spreads — so changing the exit policy, costs other than spread, sizing or equity re-runs only
    phase 2. Without now_ts/end_ts in params the first call pins `now` in the cache (same bars on every call)."""
    t0 = time.time()
    params = dict(params or {})
    if cache is not None and params.get("now_ts") is None:
        params["now_ts"] = cache.setdefault("_now", time.time())
    cfg = resolve(db, params)
    strat, rsk, cost = cfg["strategy"], cfg["risk"], cfg["cost"]
    tf, htf_tf = strat["signal_timeframe"], strat["htf_timeframe"]
    sec, hsec = TF_SECONDS[tf], TF_SECONDS.get(htf_tf, 0)  # htf 'NONE' -> no HTF series
    start, end, now = cfg["start_ts"], cfg["end_ts"], cfg["now_ts"]
    warnings: list[str] = []
    sess = Session(cfg["schedule"], cfg["tz"], start - 2 * 86400, end + 2 * 86400)
    _progress(progress, 0.02, "încarc barele")

    # data
    sig_from = start - SIG_WINDOW * sec * 2 - 4 * 86400
    htf_from = start - int(HTF_WINDOW * hsec * 1.5) - 4 * 86400
    data_key = ("data", tuple(cfg["symbols"]), tf, htf_tf, sig_from, htf_from, end, now)
    data = cache.get(data_key) if cache is not None else None
    if data is None:
        series: dict[str, list[dict]] = {}
        htfs: dict[str, list[dict]] = {}
        for sym in cfg["symbols"]:
            series[sym] = load_bars(db, sym, tf, sig_from, end, now)
            htfs[sym] = load_bars(db, sym, htf_tf, htf_from, end, now) if hsec else []
        data = (series, htfs)
        if cache is not None:
            cache[data_key] = data
    series, htfs = data
    for sym in cfg["symbols"]:
        n_in = sum(1 for b in series[sym] if b["t"] + sec >= start)
        if n_in == 0:
            warnings.append(f"{sym}: nicio bară {tf} în interval — simbol ignorat.")
        elif len(series[sym]) - n_in < strategy.MIN_SIG_BARS:
            warnings.append(f"{sym}: istoric insuficient înainte de început ({len(series[sym]) - n_in} bare {tf}); "
                            f"primele analize sunt HOLD până la {strategy.MIN_SIG_BARS} bare.")
        if hsec and len(htfs[sym]) < strategy.MIN_HTF_BARS:
            warnings.append(f"{sym}: doar {len(htfs[sym])} bare {htf_tf} (minim {strategy.MIN_HTF_BARS}).")

    # spreads (points) per bar
    stats = load_spread_stats(db) if cost["mode"] == "stats" else {}
    per_sym = cost.get("per_symbol_spread_pts") or {}
    fallback_n: dict[str, int] = {}
    spreads: dict[str, list[float]] = {}
    for sym, bars in series.items():
        if sym in per_sym:
            spreads[sym] = [round(float(per_sym[sym]), 2)] * len(bars)
            continue
        sp = []
        for b in bars:
            if cost["mode"] == "fixed":
                v = float(cost["fixed_spread_pts"])
            elif cost["mode"] == "stats" and (sym, int(b["t"] % 86400 // 3600)) in stats:
                v = stats[(sym, int(b["t"] % 86400 // 3600))]
            else:
                if cost["mode"] == "stats":
                    fallback_n[sym] = fallback_n.get(sym, 0) + 1
                v = max(float(b["sp"] or 0), 1.0)
            sp.append(round(v, 2))
        spreads[sym] = sp
    if fallback_n:
        miss = ", ".join(sorted(fallback_n))
        warnings.append(f"Statistici de spread lipsă pentru {miss} (unele ore) — folosesc spread-ul barei max(sp, 1), "
                        f"care subestimează costul (sp din MT5 este minimul barei).")
    if cost["mode"] == "bar" and any(s_ not in per_sym for s_ in series):
        warnings.append("Cost mode 'bar': spread-ul barei (sp) este minimul din bară — costurile reale sunt mai mari.")

    specs = {}
    for sym, bars in series.items():
        specs[sym] = symbol_spec(sym, bars[-1]["c"] if bars else None, cfg["specs"])
    if any(sym not in cfg["specs"] for sym in series):
        warnings.append("Specificații simbol estimate (point/contract/lot 0.01) — trimite `specs` pentru valori exacte.")

    # phase 1: chunks of decisions (with their own 400/200-bar lookback) spread over worker processes
    scan_key = None
    if cache is not None:
        # phase-2-only keys (exits, BE lock, context blockers) do not change the analyses: sweeps over them reuse phase 1
        p1_strat = {k: v for k, v in strat.items() if k not in PHASE2_STRATEGY_KEYS}
        p1_rsk = {k: v for k, v in rsk.items() if k not in PHASE2_RISK_KEYS}
        scan_key = ("scan", cfg["strategy_name"], _digest([p1_strat, p1_rsk, cfg["symbols"], start, end, tf, htf_tf,
                                                           cfg["schedule"], cfg["tz"], specs, spreads,
                                                           cost["mode"] != "bar"]), data_key)
    hit = cache.get(scan_key) if scan_key is not None else None
    workers = cfg["workers"]
    if hit is not None:
        scans, scan_warnings, n_dec = hit
        warnings.extend(scan_warnings)
        t_scan = 0.0
        if workers is None:
            workers = 0
        _progress(progress, 0.85, "analize din cache — simulez execuția")
    else:
        jobs = []
        n_dec = 0
        for sym, bars in series.items():
            idx = [i for i, b in enumerate(bars)
                   if start <= b["t"] + sec <= end and i + 1 >= strategy.MIN_SIG_BARS and sess.open(b["t"] + sec)]
            if not idx:
                continue
            n_dec += len(idx)
            sp = specs[sym]
            htf_close = [b["t"] + hsec for b in htfs[sym]]
            for c0 in range(0, len(idx), CHUNK):
                part = idx[c0:c0 + CHUNK]
                lo, hi = max(0, part[0] - SIG_WINDOW + 1), min(len(bars), part[-1] + 2)  # +1 bar: decision-time spread
                k_lo = max(0, bisect.bisect_right(htf_close, bars[part[0]]["t"] + sec) - HTF_WINDOW)
                k_hi = bisect.bisect_right(htf_close, bars[part[-1]]["t"] + sec)
                jobs.append({"symbol": sym, "strategy": strat, "risk": rsk, "point": sp["point"],
                             "digits": sp["digits"], "stops_level": sp["stops_level"], "offset": lo,
                             "spreads": spreads[sym][lo:hi], "idx": [i - lo for i in part],
                             "decision_spread_next": cost["mode"] != "bar",
                             "strategy_name": cfg["strategy_name"],
                             "sig": [(b["t"], b["o"], b["h"], b["l"], b["c"]) for b in bars[lo:hi]],
                             "htf": [(b["t"], b["o"], b["h"], b["l"], b["c"]) for b in htfs[sym][k_lo:k_hi]]})
        if workers is None:
            workers = min(len(jobs), max(1, (os.cpu_count() or 2) - 1))
        _progress(progress, 0.05, f"analizez {n_dec} bare")
        t1 = time.time()
        scans: dict[str, dict] = {}
        w0 = len(warnings)
        for r in _run_scans(jobs, int(workers), warnings, progress):
            cur = scans.setdefault(r["symbol"], {"symbol": r["symbol"], "decisions": {}, "n": 0, "aligned": {}})
            cur["decisions"].update(r["decisions"])
            cur["aligned"].update(r.get("aligned") or {})
            cur["n"] += r["n"]
        t_scan = time.time() - t1
        if scan_key is not None:
            cache[scan_key] = (scans, list(warnings[w0:]), n_dec)
        _progress(progress, 0.85, "simulez execuția")

    # phase 2
    t2 = time.time()
    prices = _Prices(series, sec)
    bias = None
    if "signal_flip" in cfg["exit"].modes:
        bias = _bias_lookup(cfg, series, htfs, sec, hsec, cache, data_key)
    res = _simulate(cfg, series, spreads, specs, scans, sess, prices, warnings, bias=bias)
    trades = res["trades"]
    if prices.approx_used:
        warnings.append("Curs USD aproximativ pentru " + ", ".join(sorted(prices.approx_used))
                        + " (pereche de conversie neîncărcată) — valorile USD sunt estimate.")
    if strat.get("trailing_enabled"):
        warnings.append("Trailing stop nu este simulat (doar break-even și time stop).")
    if (cfg.get("live_rescue") or "rescue" in cfg["exit"].modes) and sec > 300:
        warnings.append(f"Ieșirea RESCUE e simulată pe închiderile barelor {strat['signal_timeframe']} (live: eșantioane "
                        "de 1 minut) — modelul e valid doar pentru M5 sau mai mic; rezultatele RESCUE nu sunt concludente.")
    warnings.append("Fără calendar economic în backtest: blocările pe știri nu sunt simulate.")
    if len(trades) < 30:
        warnings.append(f"Doar {len(trades)} tranzacții — rezultatele nu sunt semnificative statistic.")
    if cfg["record_paths"]:
        _write_paths(cfg["paths_out"], res.get("paths") or [])

    summary = summarize(trades, ci=True)
    if cost.get("swap_mode"):
        summary["swap_r"] = round(math.fsum(t["swap_r"] for t in trades), 3)
        summary["swap_usd"] = round(math.fsum(t["swap_usd"] for t in trades), 2)
        summary["swap_days"] = sum(t["rollovers"] for t in trades)
        dflt = sorted(s_ for s_ in cfg["symbols"] if s_ not in cost["swap_pts_per_day"])
        if cost["swap_mode"] == "table" and dflt:
            warnings.append(f"Swap estimat (tabel implicit {SWAP_DEFAULT_PTS[0]:g}/{SWAP_DEFAULT_PTS[1]:g} puncte/zi "
                            f"long/short) pentru {', '.join(dflt)} — trimite cost.swap_pts_per_day pentru valorile brokerului.")
        elif cost["swap_mode"] == "zero":
            warnings.append("Swap setat la zero (swap_mode='zero', test de sensibilitate).")
    first = min((b["t"] + sec for bars in series.values() for b in bars if b["t"] + sec >= start), default=start)
    last = max((b["t"] + sec for bars in series.values() for b in bars), default=end)
    summary["sample_days"] = round(max(0.0, min(last, end) - first) / 86400, 1)
    by_symbol = {}
    for sym in cfg["symbols"]:
        st = summarize([t for t in trades if t["symbol"] == sym])
        if st["trades"]:
            by_symbol[sym] = st
    by_hour = {}
    for h in range(24):
        st = summarize([t for t in trades if int(t["entry_t"] % 86400 // 3600) == h])
        if st["trades"]:
            by_hour[str(h)] = st
    ordered = sorted(trades, key=lambda x: x["exit_t"])
    curve, cum = [], 0.0
    for t in ordered:
        cum += t["r"]
        curve.append([int(t["exit_t"]), round(cum, 3)])
    if len(curve) > MAX_CURVE_PTS:
        step = len(curve) / MAX_CURVE_PTS
        curve = [curve[int(k * step)] for k in range(MAX_CURVE_PTS - 1)] + [curve[-1]]
    counts = res["counts"]
    counts["analyses"] = sum(r["n"] for r in scans.values())
    counts["candidates"] = sum(len(r["decisions"]) for r in scans.values())
    _progress(progress, 1.0, "gata")
    by_exit = {}
    for t in trades:
        by_exit[t["outcome"]] = by_exit.get(t["outcome"], 0) + 1
    return {
        "summary": summary, "by_symbol": by_symbol, "by_hour": by_hour, "equity_r": curve,
        "trades": ordered if cfg.get("all_trades") else ordered[-MAX_TRADES_OUT:], "warnings": warnings, "counts": counts,
        "final_equity": round(res["equity"], 2), "by_outcome": by_exit,
        "params": {"symbols": cfg["symbols"], "start_utc": iso(start), "end_utc": iso(end), "signal_tf": tf,
                   "htf_tf": htf_tf, "cost": cost, "session": cfg["schedule"], "initial_equity": cfg["initial_equity"],
                   "risk_pct": rsk.get("max_risk_per_trade_pct"), "size_mode": cfg["size_mode"],
                   "strategy": {k: v for k, v in strat.items() if k != "symbols"},
                   "risk": rsk, "strategy_name": cfg["strategy_name"], "strategy_version": cfg["strategy_version"],
                   "exit": cfg["exit"].to_dict(), "live_rescue": cfg.get("live_rescue"),
                   "live_profit_lock": cfg.get("live_profit_lock")},
        "timing": {"total_s": round(time.time() - t0, 2), "analyze_s": round(t_scan, 2), "workers": int(workers),
                   "simulate_s": round(time.time() - t2, 2), "phase1_cached": hit is not None},
    }


def _bias_lookup(cfg: dict, series: dict, htfs: dict, sec: int, hsec: int, cache: dict | None, data_key):
    """bias(sym, i): the plugin's direction at the close of signal bar i, from the same closed-bar windows as phase 1
    (memoised; kept in the run_cached cache so an exit sweep computes it once)."""
    plugin = get_plugin(cfg["strategy_name"])
    strat = cfg["strategy"]
    key = ("bias", cfg["strategy_name"], _digest(strat), data_key)
    memo = cache.setdefault(key, {}) if cache is not None else {}
    htf_close = {s: [b["t"] + hsec for b in hb] for s, hb in htfs.items()}

    def bias(sym: str, i: int) -> int:
        v = memo.get((sym, i))
        if v is None:
            bars = series[sym]
            k = bisect.bisect_right(htf_close[sym], bars[i]["t"] + sec)
            v = int(plugin.bias(sym, bars[max(0, i - SIG_WINDOW + 1): i + 1], htfs[sym][max(0, k - HTF_WINDOW): k],
                                strat) or 0)
            memo[(sym, i)] = v
        return v
    return bias


def _write_paths(path: str, rows: list[dict]) -> None:
    d = os.path.dirname(os.path.abspath(path))
    os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def _simulate(cfg: dict, series: dict, spreads: dict, specs: dict, scans: dict, sess: Session, prices: _Prices,
              warnings: list[str], bias=None) -> dict:
    strat, rsk, cost = cfg["strategy"], cfg["risk"], cfg["cost"]
    sec = TF_SECONDS[strat["signal_timeframe"]]
    start, end = cfg["start_ts"], cfg["end_ts"]
    corr = int(rsk.get("max_same_currency_direction", 1))
    max_hold = int(strat.get("max_hold_minutes") or 0) * 60 if cfg.get("max_hold_s") is None else int(cfg["max_hold_s"])
    swap_on = bool(cost.get("swap_mode"))
    t_index: dict[str, dict[int, int]] = {}
    be_r = float(strat.get("break_even_at_r") or 0)
    be_lock_r = float(strat.get("be_lock_r") or 0)  # smart BE lock (0 = entry ± 2 points)
    expiry = int(strat.get("signal_expiry_minutes") or 15) * 60
    session_end_close = bool(strat.get("close_at_session_end"))
    comm_lot = float(cost.get("commission_per_lot_usd") or 0)
    slip_pts = float(cost.get("slippage_pts") or 0)
    max_cl = int(rsk.get("max_consecutive_losses", 3))
    pause_s = int(rsk.get("loss_streak_pause_minutes", 90) or 0) * 60
    r_only = cfg["size_mode"] == "r_only"
    policy: exits.ExitPolicy = cfg.get("exit") or exits.ExitPolicy()
    if cfg.get("live_rescue"):
        policy = exits.with_rescue(policy, {"rescue_enabled": True, **cfg["live_rescue"]})
    if cfg.get("live_profit_lock"):
        policy = exits.with_profit_lock(policy, {"profit_lock_enabled": True, **cfg["live_profit_lock"]})
    record = bool(cfg.get("record_paths"))
    paths: list[dict] = []

    equity = cfg["initial_equity"]
    peak = equity
    dd_lim = float(rsk.get("max_equity_drawdown_pct", 3.0))
    dd_warned = False
    book: dict[str, dict] = {}  # symbol -> pending order or open position
    trades: list[dict] = []
    sig_count: dict[tuple[str, float], int] = {}
    day_opens: dict[float, int] = {}
    day_start_eq: dict[float, float] = {}
    streak, last_loss = 0, None
    last_loss_sym: dict[str, float] = {}  # symbol -> exit time of its last loss ≥ 0.1R (cooldown)
    counts = {"blocked_market": 0, "blocked_context": {}, "signals": 0, "risk_rejected": {}, "filled": 0,
              "skipped_gap": 0, "expired_unfilled": 0, "closed_at_end": 0}
    if "partial" in policy.modes:
        counts["partial_disabled_min_lot"] = 0

    events = []
    for s_idx, sym in enumerate(cfg["symbols"]):
        for i, b in enumerate(series.get(sym) or []):
            if b["t"] + sec >= start and b["t"] < end:
                events.append((b["t"], s_idx, i))
    events.sort()

    def close(sym: str, pos: dict, reason: str, px: float, t: int) -> None:
        nonlocal equity, peak, streak, last_loss
        sp: SimPosition = pos["sim"]
        spec = specs[sym]
        vol, tv = pos["volume"], pos["tick_value"]
        risk_usd = sp.r_dist / spec["tick_size"] * tv * vol
        if sp.closed_frac:
            gross = sp.total_r(px) * risk_usd  # partial close: R-weighted pieces of the same volume
        else:
            gross = (px - sp.entry) * sp.sign / spec["tick_size"] * tv * vol
        comm = comm_lot * vol
        net = gross - comm
        if swap_on:
            # an exit inside a bar may come after a rollover strictly inside that bar: charged (conservative); a
            # rollover at the bar close (broker midnight) is after the exit and is not
            n_roll = rollovers(sp.opened_t, t + sec if reason in INTRABAR_REASONS else t)
            swap_usd = swap_points(cost, sym, sp.side) * spec["point"] / spec["tick_size"] * tv * vol * n_roll
            net += swap_usd
        r = net / risk_usd if risk_usd > 0 else 0.0
        if net < 0 and abs(net) >= NEUTRAL_R * risk_usd:
            # loss cooldown (live rule: |net| < 0.1R is neutral, no cooldown) from the real close time: an intrabar exit (stamped with the bar open) happened by the bar
            # end at the latest, which on a bar grid gives exactly the live first allowed decision
            last_loss_sym[sym] = t + sec if reason in INTRABAR_REASONS else t
        equity += net
        peak = max(peak, equity)
        if pause_s > 0 and streak >= max_cl and last_loss is not None and t >= last_loss + pause_s:
            streak = 0
        if abs(net) >= NEUTRAL_R * risk_usd:
            if net < 0:
                streak += 1
                last_loss = t
            else:
                streak = 0
        trades.append({
            "symbol": sym, "side": sp.side, "signal_t": pos["signal_t"], "entry_t": sp.opened_t, "exit_t": int(t),
            "entry": round(sp.entry, spec["digits"] + 1), "sl": pos["sl0"], "tp": sp.tp,
            "exit": round(px, spec["digits"] + 1), "outcome": reason, "r": round(r, 4),
            "gross_r": round(sp.total_r(px), 4), "usd": round(net, 2), "commission_usd": round(comm, 2),
            "volume": round(vol, 4), "spread_pts": pos["spread_pts"], "mfe_r": round(sp.mfe / sp.r_dist, 3),
            "mae_r": round(sp.mae / sp.r_dist, 3), "score": pos["score"],
            "entry_utc": iso(sp.opened_t), "exit_utc": iso(t),
        })
        if sp.closed_frac:
            trades[-1]["partial_frac"] = round(sp.closed_frac, 4)
        if swap_on:
            trades[-1].update(swap_usd=round(swap_usd, 2), swap_r=round(swap_usd / risk_usd, 4) if risk_usd > 0 else 0.0,
                              rollovers=n_roll)
        if record:
            paths.append({"source": "backtest", "trade_id": f"{sym}-{sp.opened_t}", "symbol": sym, "side": sp.side,
                          "entry_t": sp.opened_t, "exit_t": int(t), "outcome": reason, "r": round(r, 4),
                          "gross_r": round(sp.total_r(px), 4), "path": pos.get("path") or []})
        del book[sym]

    def current_streak(now_t: float) -> tuple[int, float | None]:
        if streak >= max_cl:
            if pause_s <= 0:
                # live: the streak only counts trades since local midnight
                return (streak, None) if last_loss is not None and last_loss >= sess.day_start(now_t) else (0, None)
            until = (last_loss or now_t) + pause_s
            return (0, None) if now_t >= until else (streak, until)
        return streak, None

    k = 0
    n_ev = len(events)
    while k < n_ev:
        t = events[k][0]
        j = k
        while j < n_ev and events[j][0] == t:
            j += 1
        group = events[k:j]
        k = j
        # 1) fills and position management on bar t
        for _, s_idx, i in group:
            sym = cfg["symbols"][s_idx]
            pos = book.get(sym)
            if not pos:
                continue
            b = series[sym][i]
            spec = specs[sym]
            sp_px = spreads[sym][i] * spec["point"]
            if pos["state"] == "PENDING":
                if b["t"] - pos["signal_t"] > expiry:
                    counts["expired_unfilled"] += 1
                    del book[sym]
                    continue
                slip = slip_pts * spec["point"]
                side = pos["side"]
                entry = b["o"] + (sp_px + slip if side == "BUY" else -slip)
                sim = SimPosition(side, entry, pos["sl0"], pos["tp0"], spec["point"], int(b["t"]), be_at_r=be_r,
                                  slip_px=slip, stops_px=float(spec.get("stops_level") or 0) * spec["point"],
                                  be_lock_r=be_lock_r)
                if not sim.valid or (sim.tp - entry) * sim.sign <= 0:
                    counts["skipped_gap"] += 1  # broker would reject stops on the wrong side of the price
                    del book[sym]
                    continue
                if not policy.is_base:
                    frac = None
                    if "partial" in policy.modes and not r_only:
                        frac = _partial_frac(pos["volume"], policy.p["partial_frac"], spec)
                        if frac == 0.0:
                            counts["partial_disabled_min_lot"] += 1
                    bias_fn = None
                    if bias is not None and "signal_flip" in policy.modes:
                        if sym not in t_index:
                            t_index[sym] = {b_["t"]: n_ for n_, b_ in enumerate(series[sym])}
                        bias_fn = (lambda bar_, s_=sym: bias(s_, t_index[s_][bar_["t"]]))
                    exits.attach(sim, policy, history=series[sym][max(0, i - exits.WARMUP_BARS):i], bar_sec=sec,
                                 partial_frac=frac, bias_fn=bias_fn)
                if record:
                    if sim.ind is None:
                        sim.ind = exits.IncIndicators().seed(series[sym][max(0, i - exits.WARMUP_BARS):i])
                    pos["path"] = []
                pos["sim"], pos["state"] = sim, "OPEN"
                counts["filled"] += 1
            sim = pos["sim"]
            if session_end_close and not sess.open(b["t"]):
                close(sym, pos, "SESSION_END", sim.close_price(b["o"], sp_px), b["t"])
                continue
            hit = sim.step(b, sp_px)
            if record:
                _path_point(pos, sim, b, sec, sp_px, spreads[sym][i])
            if hit:
                close(sym, pos, hit[0], hit[1], b["t"] + sec if hit[0] in exits.AT_CLOSE_REASONS else b["t"])
            elif max_hold > 0 and b["t"] + sec - sim.opened_t >= max_hold:
                close(sym, pos, "TIME", sim.close_price(b["c"], sp_px), b["t"] + sec)
        # 2) decisions at the close of bar t
        T = t + sec
        if T < start or T > end:
            continue
        cands = []
        for _, s_idx, i in group:
            sym = cfg["symbols"][s_idx]
            scan = scans.get(sym)
            rec = scan["decisions"].get(i) if scan else None
            if rec is None:
                continue
            if rec[5]:
                counts["blocked_market"] += 1
                continue
            cands.append((sym, i, rec, (scan.get("aligned") or {}).get(i, 0)))
        if len(cands) > 1:  # pre-AI arbitration order (live): HTF-aligned first, then the cheapest spread/SL distance
            cands.sort(key=lambda c: arbitration_key(c[3], spreads[c[0]][c[1]] * specs[c[0]]["point"], c[2][1], c[2][2]))
        for sym, i, rec, _al in cands:
            side, entry, sl, tp, score = rec[:5]
            s_start = sess.start(T)
            ctx = strategy.AnalysisContext(
                session_open=True, has_bot_position=sym in book,
                signals_this_session=sig_count.get((sym, s_start), 0), tick_age=0.0, now_ts=T,
                open_exposures=[(s, p["side"]) for s, p in book.items()], corr_limit=corr,
                last_loss_ts=last_loss_sym.get(sym))
            # live rules incl. risk cooldown_after_loss_minutes and rollover_blackout_minutes (17:00 New York)
            cb = strategy.context_blockers(sym, side, ctx, rsk)
            if cb:
                key = cb[0].split(" ")[0].strip(":.")
                counts["blocked_context"][key] = counts["blocked_context"].get(key, 0) + 1
                continue
            # a real signal (live counts it toward the session limit whatever happens next)
            sig_count[(sym, s_start)] = sig_count.get((sym, s_start), 0) + 1
            counts["signals"] += 1
            spec = specs[sym]
            sp_pts = spreads[sym][i]
            bar = series[sym][i]
            tv = _tick_value(spec, prices, T, bar["c"])
            if not tv:
                counts["risk_rejected"]["tick_value"] = counts["risk_rejected"].get("tick_value", 0) + 1
                continue
            day = sess.day_start(T)
            if day not in day_start_eq:
                day_start_eq[day] = equity
            if not dd_warned and peak > 0 and (peak - equity) / peak * 100 >= dd_lim:
                dd_warned = True
                warnings.append(f"Drawdown {dd_lim:g}% atins la {iso(T)} — în live intrările s-ar fi oprit "
                                + ("(simulat)." if cfg["halt_on_drawdown"] else "(backtestul continuă; halt_on_drawdown=true pentru oprire)."))
            streak_now, pause_until = current_streak(T)
            open_risk = 0.0
            for s, p in book.items():
                if p["state"] == "PENDING":
                    open_risk += p["risk_usd"]
                elif (p["sim"].sl - p["sim"].entry) * p["sim"].sign < 0:  # SL at/after BE risks nothing (live rule)
                    o_r = abs(p["sim"].entry - p["sim"].sl) / specs[s]["tick_size"] * p["tick_value"] * p["volume"]
                    open_risk += o_r * (1.0 - p["sim"].closed_frac) if p["sim"].closed_frac else o_r
            info = {**spec, "bid": bar["c"], "ask": entry if side == "BUY" else bar["c"] + sp_pts * spec["point"],
                    "spread_points": sp_pts, "tick_value": tv}
            rc = risk_mod.evaluate(risk_mod.RiskContext(
                mode="PAPER", execution_enabled=True, entries_paused=False, side=side, entry=entry, sl=sl, tp=tp,
                symbol_info=info, account={"equity": equity, "balance": equity, "margin_free": equity}, risk=rsk,
                tick_age=0.0, open_bot_positions=len(book), has_bot_position_on_symbol=sym in book, symbol=sym,
                open_exposures=[(s, p["side"]) for s, p in book.items()], trades_today=day_opens.get(day, 0),
                day_start_equity=day_start_eq[day], peak_equity=peak if cfg["halt_on_drawdown"] else None,
                consecutive_losses=streak_now, loss_pause_until=pause_until, open_risk_money=open_risk))
            failed = [c["name"] for c in rc["checks"] if not c["ok"]]
            volume = float(rc.get("volume") or 0)
            sl_dist = abs(entry - sl)
            if r_only and failed in (["volume_ok"], ["volume_ok", "total_open_risk_ok"]):
                volume = equity * float(rsk.get("max_risk_per_trade_pct", 0.25)) / 100 / (sl_dist / spec["tick_size"] * tv)
                failed = []
            if failed:
                for f in failed:
                    counts["risk_rejected"][f] = counts["risk_rejected"].get(f, 0) + 1
                continue
            day_opens[day] = day_opens.get(day, 0) + 1
            book[sym] = {"state": "PENDING", "side": side, "sl0": sl, "tp0": tp, "signal_t": T, "volume": volume,
                         "tick_value": tv, "spread_pts": sp_pts, "score": score,
                         "risk_usd": sl_dist / spec["tick_size"] * tv * volume}
    for sym in list(book):
        pos = book[sym]
        if pos["state"] != "OPEN":
            del book[sym]
            continue
        bars = series[sym]
        b = bars[-1]
        close(sym, pos, "END", pos["sim"].close_price(b["c"], spreads[sym][-1] * specs[sym]["point"]), b["t"] + sec)
        counts["closed_at_end"] += 1
    if counts["closed_at_end"]:
        warnings.append(f"{counts['closed_at_end']} poziții închise forțat la sfârșitul datelor (END).")
    if counts["risk_rejected"].get("volume_ok"):
        warnings.append(f"{counts['risk_rejected']['volume_ok']} semnale respinse: risc prea mic pentru lotul minim "
                        f"(ca în live). size_mode='r_only' le simulează în R.")
    return {"trades": trades, "counts": counts, "equity": equity, "paths": paths}


def _partial_frac(volume: float, frac: float, spec: dict) -> float:
    """Fraction really closable after lot rounding (0.0 when either piece would be below volume_min)."""
    step, vmin = spec["volume_step"] or 0.01, spec["volume_min"]
    part = math.floor(volume * frac / step + 1e-9) * step
    if part < vmin - 1e-9 or volume - part < vmin - 1e-9 or volume <= 0:
        return 0.0
    return part / volume


def _path_point(pos: dict, sim: SimPosition, b: dict, sec: int, sp_px: float, sp_pts: float) -> None:
    """Trade path row at the close of bar b (same fields as the live trade_paths table)."""
    ind = sim.ind
    ts = int(b["t"] + sec)
    atr = ind.atr if ind is not None else None
    ema = ind.ema20 if ind is not None else None
    pos["path"].append({
        "ts": ts, "minutes": round((ts - sim.opened_t) / 60, 2),
        "r_now": round(sim.r_of(sim.close_price(b["c"], sp_px)), 4),
        "mfe_r": round(sim.mfe / sim.r_dist, 4), "mae_r": round(sim.mae / sim.r_dist, 4), "price": b["c"],
        "atr": None if atr is None else round(atr, 8),
        "ema_dist_atr": round((b["c"] - ema) * sim.sign / atr, 4) if atr and ema is not None else None,
        "spread_pts": sp_pts})


# ---- CLI ------------------------------------------------------------------------

def _print_report(r: dict) -> None:
    s = r["summary"]
    print(f"Tranzacții {s['trades']}  win {s['win_rate']}  avgR {s['avg_r']}  CI90 {s['ci90_avg_r']}  "
          f"PF {s['profit_factor']}  maxDD {s['max_dd_r']}R  serie pierderi {s['max_consec_losses']}  "
          f"net {s['net_usd']} USD  zile {s['sample_days']}")
    for sym, st in r["by_symbol"].items():
        print(f"  {sym:<8} n={st['trades']:<4} win={st['win_rate']}  avgR={st['avg_r']}  net={st['net_usd']} USD")
    print("Contoare:", json.dumps(r["counts"], ensure_ascii=False))
    print("Timp:", r["timing"])
    for w in r["warnings"]:
        print("! " + w)


def main(argv: list[str] | None = None) -> int:
    if (sys.argv[1:] if argv is None else argv) == ["--scan-worker"]:
        return _worker_main()
    ap = argparse.ArgumentParser(prog="python -m app.backtest", description="Backtest trend-v1 pe barele stocate")
    ap.add_argument("--symbols", default="")
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--db", default=None, help="cale SQLite (implicit DATABASE_PATH; deschisă doar pentru citire)")
    ap.add_argument("--signal-tf", default=None)
    ap.add_argument("--htf-tf", default=None)
    ap.add_argument("--cost", default="stats", choices=["stats", "bar", "fixed"])
    ap.add_argument("--spread", type=float, default=None, help="spread fix în puncte (cu --cost fixed)")
    ap.add_argument("--commission", type=float, default=DEFAULT_COMMISSION)
    ap.add_argument("--slippage", type=float, default=DEFAULT_SLIPPAGE)
    ap.add_argument("--risk-pct", type=float, default=None)
    ap.add_argument("--equity", type=float, default=200.0)
    ap.add_argument("--no-session", action="store_true")
    ap.add_argument("--r-only", action="store_true", help="ignoră lotul minim (măsoară doar R)")
    ap.add_argument("--workers", type=int, default=None)
    ap.add_argument("--strategy", default=DEFAULT_STRATEGY, help="numele strategiei (app.strategies)")
    ap.add_argument("--exit", default="plugin", help="politica de ieșire (app.exits.MODES; 'plugin' = cea a strategiei)")
    ap.add_argument("--exit-params", default="{}", help='parametri JSON, ex. {"trail_start_r": 1.0}')
    ap.add_argument("--swap-mode", default=None, choices=list(SWAP_MODES),
                    help="swap/finanțare: off | table (implicit -0.5 pip/zi) | zero; implicit după strategie")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    path = a.db or str(get_settings().db_path)
    db = ReadOnlyDB(path)
    params = {"days": a.days, "signal_tf": a.signal_tf, "htf_tf": a.htf_tf, "initial_equity": a.equity,
              "risk_pct": a.risk_pct, "workers": a.workers, "size_mode": "r_only" if a.r_only else "live",
              "cost": {"mode": a.cost, "fixed_spread_pts": a.spread, "commission_per_lot_usd": a.commission,
                       "slippage_pts": a.slippage, "swap_mode": a.swap_mode},
              "strategy_name": a.strategy,
              "exit": {"mode": a.exit, "params": json.loads(a.exit_params)} if a.exit != "plugin" else None}
    if a.symbols:
        params["symbols"] = [x.strip().upper() for x in a.symbols.split(",") if x.strip()]
    if a.no_session:
        params["session"] = None
    try:
        r = run(db, params)
    finally:
        db.close()
    if a.json:
        print(json.dumps(r, ensure_ascii=False))
    else:
        _print_report(r)
    return 0


if __name__ == "__main__":
    sys.exit(main())
