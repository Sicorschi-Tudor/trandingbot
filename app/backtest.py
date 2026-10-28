"""Walk-forward backtest of the live strategy on stored bars (bars_archive ∪ bars).

Decisions use exactly the live code path: strategy.analyze on CLOSED bars only (last 400 signal-TF bars, last 200 closed
HTF bars at the decision time), quote = decision bar close + modelled spread, then the same context blockers
(strategy.context_blockers) and the same Risk Engine (risk.evaluate) with a simulated book/account.
Execution: fill at the next bar open (+spread for BUY, + slippage), SL/TP/BE/time stop bar by bar (labeler.SimPosition,
SL first when both are touched in one bar), commission per lot round trip.

Phase 1 (per symbol, parallel processes): strategy.analyze with a neutral context -> candidate levels + market blockers.
Phase 2 (sequential, all symbols in time order): context blockers, risk checks, sizing, fills, exits.
The split is exact because analyze() = market blockers + context_blockers(); a test pins the equivalence.

CLI: python -m app.backtest --symbols EURUSD,GBPUSD --days 90
"""
from __future__ import annotations

import argparse
import bisect
import json
import math
import os
import pickle
import random
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone

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
# USD value of one unit of the quote currency when no loaded pair gives the rate (warned)
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
    strat.update(params.get("strategy") or {})
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
    elif not sched.get("enabled", True):
        sched = None
    for tf in (strat["signal_timeframe"], strat["htf_timeframe"]):
        if tf not in TF_SECONDS:
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
    cost.update({k: v for k, v in (params.get("cost") or {}).items() if v is not None})
    if cost["mode"] not in ("stats", "bar", "fixed"):
        raise ValueError(f"cost.mode invalid: {cost['mode']}")
    if cost["mode"] == "fixed" and cost.get("fixed_spread_pts") is None:
        raise ValueError("cost.fixed_spread_pts lipsește pentru mode=fixed")
    return {"strategy": strat, "risk": rsk, "schedule": sched, "symbols": symbols, "start_ts": start, "end_ts": end,
            "now_ts": now, "cost": cost, "initial_equity": float(params.get("initial_equity") or 200.0),
            "size_mode": params.get("size_mode") or "live", "halt_on_drawdown": bool(params.get("halt_on_drawdown")),
            "specs": params.get("specs") or {}, "tz": (sched or {}).get("tz") or s.DEFAULT_TIMEZONE,
            "workers": params.get("workers")}


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
    Returns {"symbol", "decisions": {i: (side, entry, sl, tp, score, market_blockers)}, "n": analyses run}."""
    symbol, cfg, rsk = job["symbol"], job["strategy"], job["risk"]
    sec, hsec = TF_SECONDS[cfg["signal_timeframe"]], TF_SECONDS[cfg["htf_timeframe"]]
    point, digits, stops_level = job["point"], job["digits"], job["stops_level"]
    sig = [{"t": t, "o": o, "h": h, "l": l, "c": c} for t, o, h, l, c in job["sig"]]
    htf = [{"t": t, "o": o, "h": h, "l": l, "c": c} for t, o, h, l, c in job["htf"]]
    htf_close = [b["t"] + hsec for b in htf]
    spreads = job["spreads"]
    corr = int(rsk.get("max_same_currency_direction", 1))
    out: dict[int, tuple] = {}
    n = 0
    for i in job["idx"]:
        b = sig[i]
        T = b["t"] + sec
        k = bisect.bisect_right(htf_close, T)
        sp_pts = spreads[i]
        quote = {"bid": b["c"], "ask": round(b["c"] + sp_pts * point, digits + 3), "spread_points": sp_pts,
                 "digits": digits, "point": point, "stops_level": stops_level}
        ctx = strategy.AnalysisContext(session_open=True, has_bot_position=False, signals_this_session=0,
                                       tick_age=0.0, now_ts=T, open_exposures=[], corr_limit=corr)
        a = strategy.analyze(symbol, sig[max(0, i - SIG_WINDOW + 1): i + 1], htf[max(0, k - HTF_WINDOW): k],
                             quote, cfg, rsk, ctx)
        n += 1
        cand = a.get("candidate")
        if not cand:
            continue
        neutral = set(strategy.context_blockers(symbol, cand["side"], ctx, rsk))
        market = tuple(x for x in cand["blockers"] if x not in neutral)
        out[job.get("offset", 0) + i] = (cand["side"], cand["entry"], cand["stop_loss"], cand["take_profit"],
                                          cand["score"], market)
    return {"symbol": symbol, "decisions": out, "n": n}


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
    t0 = time.time()
    cfg = resolve(db, params or {})
    strat, rsk, cost = cfg["strategy"], cfg["risk"], cfg["cost"]
    tf, htf_tf = strat["signal_timeframe"], strat["htf_timeframe"]
    sec, hsec = TF_SECONDS[tf], TF_SECONDS[htf_tf]
    start, end, now = cfg["start_ts"], cfg["end_ts"], cfg["now_ts"]
    warnings: list[str] = []
    sess = Session(cfg["schedule"], cfg["tz"], start - 2 * 86400, end + 2 * 86400)
    _progress(progress, 0.02, "încarc barele")

    # data
    sig_from = start - SIG_WINDOW * sec * 2 - 4 * 86400
    htf_from = start - int(HTF_WINDOW * hsec * 1.5) - 4 * 86400
    series: dict[str, list[dict]] = {}
    htfs: dict[str, list[dict]] = {}
    for sym in cfg["symbols"]:
        series[sym] = load_bars(db, sym, tf, sig_from, end, now)
        htfs[sym] = load_bars(db, sym, htf_tf, htf_from, end, now)
        n_in = sum(1 for b in series[sym] if b["t"] + sec >= start)
        if n_in == 0:
            warnings.append(f"{sym}: nicio bară {tf} în interval — simbol ignorat.")
        elif len(series[sym]) - n_in < strategy.MIN_SIG_BARS:
            warnings.append(f"{sym}: istoric insuficient înainte de început ({len(series[sym]) - n_in} bare {tf}); "
                            f"primele analize sunt HOLD până la {strategy.MIN_SIG_BARS} bare.")
        if len(htfs[sym]) < strategy.MIN_HTF_BARS:
            warnings.append(f"{sym}: doar {len(htfs[sym])} bare {htf_tf} (minim {strategy.MIN_HTF_BARS}).")

    # spreads (points) per bar
    stats = load_spread_stats(db) if cost["mode"] == "stats" else {}
    fallback_n: dict[str, int] = {}
    spreads: dict[str, list[float]] = {}
    for sym, bars in series.items():
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
    if cost["mode"] == "bar":
        warnings.append("Cost mode 'bar': spread-ul barei (sp) este minimul din bară — costurile reale sunt mai mari.")

    specs = {}
    for sym, bars in series.items():
        specs[sym] = symbol_spec(sym, bars[-1]["c"] if bars else None, cfg["specs"])
    if any(sym not in cfg["specs"] for sym in series):
        warnings.append("Specificații simbol estimate (point/contract/lot 0.01) — trimite `specs` pentru valori exacte.")

    # phase 1: chunks of decisions (with their own 400/200-bar lookback) spread over worker processes
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
            lo, hi = max(0, part[0] - SIG_WINDOW + 1), part[-1] + 1
            k_lo = max(0, bisect.bisect_right(htf_close, bars[part[0]]["t"] + sec) - HTF_WINDOW)
            k_hi = bisect.bisect_right(htf_close, bars[part[-1]]["t"] + sec)
            jobs.append({"symbol": sym, "strategy": strat, "risk": rsk, "point": sp["point"], "digits": sp["digits"],
                         "stops_level": sp["stops_level"], "offset": lo, "spreads": spreads[sym][lo:hi],
                         "idx": [i - lo for i in part],
                         "sig": [(b["t"], b["o"], b["h"], b["l"], b["c"]) for b in bars[lo:hi]],
                         "htf": [(b["t"], b["o"], b["h"], b["l"], b["c"]) for b in htfs[sym][k_lo:k_hi]]})
    workers = cfg["workers"]
    if workers is None:
        workers = min(len(jobs), max(1, (os.cpu_count() or 2) - 1))
    _progress(progress, 0.05, f"analizez {n_dec} bare")
    t1 = time.time()
    scans: dict[str, dict] = {}
    for r in _run_scans(jobs, int(workers), warnings, progress):
        cur = scans.setdefault(r["symbol"], {"symbol": r["symbol"], "decisions": {}, "n": 0})
        cur["decisions"].update(r["decisions"])
        cur["n"] += r["n"]
    t_scan = time.time() - t1
    _progress(progress, 0.85, "simulez execuția")

    # phase 2
    prices = _Prices(series, sec)
    res = _simulate(cfg, series, spreads, specs, scans, sess, prices, warnings)
    trades = res["trades"]
    if prices.approx_used:
        warnings.append("Curs USD aproximativ pentru " + ", ".join(sorted(prices.approx_used))
                        + " (pereche de conversie neîncărcată) — valorile USD sunt estimate.")
    if strat.get("trailing_enabled"):
        warnings.append("Trailing stop nu este simulat (doar break-even și time stop).")
    warnings.append("Fără calendar economic în backtest: blocările pe știri nu sunt simulate.")
    if len(trades) < 30:
        warnings.append(f"Doar {len(trades)} tranzacții — rezultatele nu sunt semnificative statistic.")

    summary = summarize(trades, ci=True)
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
    return {
        "summary": summary, "by_symbol": by_symbol, "by_hour": by_hour, "equity_r": curve,
        "trades": ordered[-MAX_TRADES_OUT:], "warnings": warnings, "counts": counts,
        "final_equity": round(res["equity"], 2),
        "params": {"symbols": cfg["symbols"], "start_utc": iso(start), "end_utc": iso(end), "signal_tf": tf,
                   "htf_tf": htf_tf, "cost": cost, "session": cfg["schedule"], "initial_equity": cfg["initial_equity"],
                   "risk_pct": rsk.get("max_risk_per_trade_pct"), "size_mode": cfg["size_mode"],
                   "strategy": {k: v for k, v in strat.items() if k != "symbols"},
                   "risk": rsk},
        "timing": {"total_s": round(time.time() - t0, 2), "analyze_s": round(t_scan, 2), "workers": int(workers)},
    }


def _simulate(cfg: dict, series: dict, spreads: dict, specs: dict, scans: dict, sess: Session, prices: _Prices,
              warnings: list[str]) -> dict:
    strat, rsk, cost = cfg["strategy"], cfg["risk"], cfg["cost"]
    sec = TF_SECONDS[strat["signal_timeframe"]]
    start, end = cfg["start_ts"], cfg["end_ts"]
    corr = int(rsk.get("max_same_currency_direction", 1))
    max_hold = int(strat.get("max_hold_minutes") or 0) * 60
    be_r = float(strat.get("break_even_at_r") or 0)
    expiry = int(strat.get("signal_expiry_minutes") or 15) * 60
    session_end_close = bool(strat.get("close_at_session_end"))
    comm_lot = float(cost.get("commission_per_lot_usd") or 0)
    slip_pts = float(cost.get("slippage_pts") or 0)
    max_cl = int(rsk.get("max_consecutive_losses", 3))
    pause_s = int(rsk.get("loss_streak_pause_minutes", 90) or 0) * 60
    r_only = cfg["size_mode"] == "r_only"

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
    counts = {"blocked_market": 0, "blocked_context": {}, "signals": 0, "risk_rejected": {}, "filled": 0,
              "skipped_gap": 0, "expired_unfilled": 0, "closed_at_end": 0}

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
        gross = (px - sp.entry) * sp.sign / spec["tick_size"] * tv * vol
        comm = comm_lot * vol
        risk_usd = sp.r_dist / spec["tick_size"] * tv * vol
        net = gross - comm
        r = net / risk_usd if risk_usd > 0 else 0.0
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
            "gross_r": round(sp.r_of(px), 4), "usd": round(net, 2), "commission_usd": round(comm, 2),
            "volume": round(vol, 4), "spread_pts": pos["spread_pts"], "mfe_r": round(sp.mfe / sp.r_dist, 3),
            "mae_r": round(sp.mae / sp.r_dist, 3), "score": pos["score"],
            "entry_utc": iso(sp.opened_t), "exit_utc": iso(t),
        })
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
                                  slip_px=slip, stops_px=float(spec.get("stops_level") or 0) * spec["point"])
                if not sim.valid or (sim.tp - entry) * sim.sign <= 0:
                    counts["skipped_gap"] += 1  # broker would reject stops on the wrong side of the price
                    del book[sym]
                    continue
                pos["sim"], pos["state"] = sim, "OPEN"
                counts["filled"] += 1
            sim = pos["sim"]
            if session_end_close and not sess.open(b["t"]):
                close(sym, pos, "SESSION_END", sim.close_price(b["o"], sp_px), b["t"])
                continue
            hit = sim.step(b, sp_px)
            if hit:
                close(sym, pos, hit[0], hit[1], b["t"])
            elif max_hold > 0 and b["t"] + sec - sim.opened_t >= max_hold:
                close(sym, pos, "TIME", sim.close_price(b["c"], sp_px), b["t"] + sec)
        # 2) decisions at the close of bar t
        T = t + sec
        if T < start or T > end:
            continue
        for _, s_idx, i in group:
            sym = cfg["symbols"][s_idx]
            scan = scans.get(sym)
            rec = scan["decisions"].get(i) if scan else None
            if rec is None:
                continue
            side, entry, sl, tp, score, market = rec
            if market:
                counts["blocked_market"] += 1
                continue
            s_start = sess.start(T)
            ctx = strategy.AnalysisContext(
                session_open=True, has_bot_position=sym in book,
                signals_this_session=sig_count.get((sym, s_start), 0), tick_age=0.0, now_ts=T,
                open_exposures=[(s, p["side"]) for s, p in book.items()], corr_limit=corr)
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
                    open_risk += abs(p["sim"].entry - p["sim"].sl) / specs[s]["tick_size"] * p["tick_value"] * p["volume"]
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
    return {"trades": trades, "counts": counts, "equity": equity}


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
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    path = a.db or str(get_settings().db_path)
    db = ReadOnlyDB(path)
    params = {"days": a.days, "signal_tf": a.signal_tf, "htf_tf": a.htf_tf, "initial_equity": a.equity,
              "risk_pct": a.risk_pct, "workers": a.workers, "size_mode": "r_only" if a.r_only else "live",
              "cost": {"mode": a.cost, "fixed_spread_pts": a.spread, "commission_per_lot_usd": a.commission,
                       "slippage_pts": a.slippage}}
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
