"""Datasets for the ML layer, built from the bar archive through the existing backtest/labeler code (no new trade
simulation).

build(db, params) runs ONE backtest (app.backtest.run_cached, size_mode r_only, record_paths) of a base strategy and
reuses what it computed:
- ENTRY dataset (schema fs-v1): every phase-1 candidate of the strategy without MARKET blockers — i.e. every setup
  the live engine would take or block only by its context (position already open, correlation, signal limit, risk).
  Outcome = labeler.simulate of the candidate (fill at the next bar open, SL first, break-even, time stop) on the
  signal-TF bars with the backtest's spread model, minus entry slippage and commission (in R per lot) = R net of
  realistic costs. `taken` = the backtest really traded it (book/risk rules).
- EXIT dataset (schema xs-v1): one row per closed signal bar of every simulated trade (record_paths JSONL) where the
  trade is still open after that bar (the exit bar is dropped: nothing left to decide). The current SL/TP come from
  replaying labeler.SimPosition with the trade's levels (cross-checked against the recorded path).
  target = final_R_net - close_now_R_net, close_now_R_net = r_now - (gross_R - net_R) - exit_cost_r
  = gross_R - r_now + exit_cost_r: the R still to be won (+) or lost (-) by holding instead of closing at this
  bar's close. Commission is paid either way, so it cancels.

No lookahead: features use closed bars up to the decision bar only; labels use the bars after it. Every row carries
`t` (decision time) and `t_end` (label end time) so the splits in train.py can purge overlapping labels.
Session-end closes and swap are not part of the entry label (trend-v1 base holds ≤ max_hold_minutes).
"""
from __future__ import annotations

import bisect
import hashlib
import json
import math
import os
import tempfile
import time

import numpy as np

from .. import backtest, exits
from ..config import TF_SECONDS
from ..labeler import LABEL_HORIZON_S, SimPosition, simulate
from . import features as F

HTF_FOR = {"M1": "M15", "M5": "H1", "M15": "H1", "M30": "H4", "H1": "H4", "H4": "D1", "D1": "NONE"}
PATH_TOL_R = 0.02  # replay vs recorded path: max |Δr_now| before a trade is dropped from the exit dataset


def _cache_get(cache: dict, tag: str):
    hits = [(k, v) for k, v in cache.items() if isinstance(k, tuple) and k and k[0] == tag]
    if not hits:
        raise RuntimeError(f"backtest cache: lipsește intrarea '{tag}'")
    return hits[-1][1]


def bar_spreads(db, cost: dict, series: dict) -> dict[str, list[float]]:
    """Spread in points per bar = backtest.run_cached's cost model (stats per UTC hour / fixed / per symbol / bar sp)."""
    stats = backtest.load_spread_stats(db) if cost["mode"] == "stats" else {}
    per_sym = cost.get("per_symbol_spread_pts") or {}
    out = {}
    for sym, bars in series.items():
        if sym in per_sym:
            out[sym] = [round(float(per_sym[sym]), 2)] * len(bars)
            continue
        sp = []
        for b in bars:
            key = (sym, int(b["t"] % 86400 // 3600))
            if cost["mode"] == "fixed":
                v = float(cost["fixed_spread_pts"])
            elif cost["mode"] == "stats" and key in stats:
                v = stats[key]
            else:
                v = max(float(b["sp"] or 0), 1.0)
            sp.append(round(v, 2))
        out[sym] = sp
    return out


def default_params(base_strategy: str = "trend-v1", tf: str = "M5", **kw) -> dict:
    tf = tf.upper()
    p = {"strategy_name": base_strategy, "signal_tf": tf, "htf_tf": HTF_FOR.get(tf, "H1"), "size_mode": "r_only",
         "workers": 3, "days": 120}
    p.update({k: v for k, v in kw.items() if v is not None})
    return p


def build(db, params: dict, progress=None, exit_cost_r: float = 0.0) -> dict:
    """Entry + exit datasets for a backtest params dict (see backtest.resolve). Returns
    {"entry": {...arrays...}, "exit": {...}, "meta": {...}}."""
    params = dict(params)
    params["size_mode"] = "r_only"
    params["record_paths"] = True
    params["workers"] = min(3, int(params.get("workers") or 3))
    tmp = tempfile.mkdtemp(prefix="ml_paths_")
    params["paths_out"] = os.path.join(tmp, "paths.jsonl")
    if params.get("now_ts") is None:
        params["now_ts"] = time.time()  # pinned: resolve() below sees the same period as the backtest
    cache: dict = {}
    res = backtest.run_cached(db, params, cache, progress)
    cfg = backtest.resolve(db, params)
    series, htfs = _cache_get(cache, "data")
    scans = _cache_get(cache, "scan")[0]
    strat, cost = cfg["strategy"], cfg["cost"]
    sec, hsec = TF_SECONDS[strat["signal_timeframe"]], TF_SECONDS.get(strat["htf_timeframe"], 0)
    spreads = bar_spreads(db, cost, series)
    specs = {s: backtest.symbol_spec(s, b[-1]["c"] if b else None, cfg["specs"]) for s, b in series.items()}
    prices = backtest._Prices(series, sec)
    paths = []
    with open(params["paths_out"], encoding="utf-8") as f:
        for line in f:
            if line.strip():
                paths.append(json.loads(line))
    try:
        os.remove(params["paths_out"])
        os.rmdir(tmp)
    except OSError:
        pass
    taken = {(p["symbol"], int(p["entry_t"])) for p in paths}
    entry = _entry_rows(cfg, series, htfs, spreads, specs, prices, scans, sec, hsec, taken)
    exit_ = _exit_rows(cfg, series, spreads, specs, scans, sec, paths, exit_cost_r)
    meta = {"params": {k: v for k, v in params.items() if k not in ("paths_out", "record_paths")},
            "strategy_name": cfg["strategy_name"], "strategy_version": cfg["strategy_version"],
            "signal_tf": strat["signal_timeframe"], "htf_tf": strat["htf_timeframe"],
            "start_ts": cfg["start_ts"], "end_ts": cfg["end_ts"], "now_ts": cfg["now_ts"],
            "cost": cost, "exit_policy": cfg["exit"].to_dict(), "exit_cost_r": exit_cost_r,
            "backtest_summary": res["summary"], "backtest_counts": res["counts"],
            "n_entry": len(entry["t"]), "n_exit": len(exit_["t"]), "n_trades": len(paths),
            "exit_dropped": exit_.pop("_dropped"), "symbols": cfg["symbols"],
            "max_bar_t": max((b[-1]["t"] for b in series.values() if b), default=None)}
    meta["dataset_version"] = dataset_version(meta)
    return {"entry": entry, "exit": exit_, "meta": meta}


def dataset_version(meta: dict) -> str:
    key = {k: meta.get(k) for k in ("params", "strategy_name", "strategy_version", "start_ts", "end_ts", "cost",
                                    "exit_policy", "n_entry", "n_exit", "max_bar_t", "exit_cost_r")}
    return "ds-" + hashlib.sha1(json.dumps(key, sort_keys=True, default=str).encode()).hexdigest()[:12]


def _entry_rows(cfg, series, htfs, spreads, specs, prices, scans, sec, hsec, taken) -> dict:
    strat, cost = cfg["strategy"], cfg["cost"]
    hold_s = int(cfg["max_hold_s"]) if cfg.get("max_hold_s") is not None else int(strat.get("max_hold_minutes") or 0) * 60
    policy: exits.ExitPolicy = cfg["exit"]
    use_policy = not policy.is_base and "signal_flip" not in policy.modes
    lcfg = {"max_hold_minutes": hold_s / 60.0, "break_even_at_r": strat.get("break_even_at_r"), "_bar_sec": sec,
            "_horizon_s": LABEL_HORIZON_S if hold_s <= 0 else 0}
    horizon = (hold_s if hold_s > 0 else LABEL_HORIZON_S) + 4 * 86400  # + weekend gaps
    comm_lot = float(cost.get("commission_per_lot_usd") or 0)
    slip_pts = float(cost.get("slippage_pts") or 0)
    cols = {k: [] for k in ("t", "t_end", "symbol", "side", "r", "r_gross", "outcome", "taken", "score")}
    X = []
    for sym in cfg["symbols"]:
        scan = scans.get(sym)
        bars = series.get(sym) or []
        if not scan or not bars:
            continue
        times = [b["t"] for b in bars]
        htf = htfs.get(sym) or []
        htf_close = [b["t"] + hsec for b in htf]
        spec = specs[sym]
        point, digits = spec["point"], spec["digits"]
        sp_by_t = {b["t"]: spreads[sym][k] * point for k, b in enumerate(bars)}
        spread_fn = (lambda b, m=sp_by_t, pt=point: m.get(b["t"], max(float(b.get("sp") or 0), 1.0) * pt))
        lcfg_s = {**lcfg, "_stops_px": float(spec.get("stops_level") or 0) * point}
        for i in sorted(scan["decisions"]):
            side, c_entry, sl, tp, score, market = scan["decisions"][i]
            if market:
                continue
            b = bars[i]
            T = b["t"] + sec
            j_end = bisect.bisect_left(times, T + horizon)
            lo = max(0, i - exits.WARMUP_BARS + 1) if use_policy else i + 1
            lab = simulate(side, sl, tp, bars[lo:j_end], point, T, lcfg_s, cfg["now_ts"], slip_px=slip_pts * point,
                           spread_fn=spread_fn, exit_policy=policy if use_policy else None)
            if lab is None or lab.get("gap"):
                continue  # unresolved at the end of the data / not tradeable at these levels
            r_dist = abs(lab["entry"] - sl)
            tv = backtest._tick_value(spec, prices, T, b["c"])
            if not tv or r_dist <= 0:
                continue
            risk_lot = r_dist / spec["tick_size"] * tv
            comm_r = comm_lot / risk_lot if risk_lot > 0 else 0.0
            slip_r = slip_pts * point / r_dist  # labeler fills at the open (+spread): backtest also slips the entry
            r_net = lab["outcome_r"] - comm_r - slip_r
            k = bisect.bisect_right(htf_close, T)
            sp_pts = spreads[sym][i]
            quote = {"bid": b["c"], "ask": round(b["c"] + sp_pts * point, digits + 3), "spread_points": sp_pts,
                     "point": point, "digits": digits}
            feats = F.entry_features(bars[max(0, i - backtest.SIG_WINDOW + 1): i + 1],
                                     htf[max(0, k - backtest.HTF_WINDOW): k], quote,
                                     {"side": side, "entry": c_entry, "sl": sl, "tp": tp, "score": score}, bar_sec=sec)
            X.append(F.vector(feats, F.ENTRY_FEATURE_NAMES))
            end_t = lab["exit_t"] + sec  # labeler stamps exits with the bar OPEN: the label ends by its close
            cols["t"].append(int(T))
            cols["t_end"].append(int(max(end_t, T)))
            cols["symbol"].append(sym)
            cols["side"].append(side)
            cols["r"].append(round(r_net, 5))
            cols["r_gross"].append(lab["outcome_r"])
            cols["outcome"].append(lab["outcome"])
            cols["taken"].append((sym, int(T)) in taken or (sym, int(bars[i + 1]["t"]) if i + 1 < len(bars) else None) in taken)
            cols["score"].append(score)
    return _finish(cols, X, F.ENTRY_FEATURE_NAMES, F.ENTRY_SCHEMA)


def _finish(cols: dict, X: list, names: list[str], schema: str) -> dict:
    order = np.argsort(np.asarray(cols["t"], dtype=np.int64), kind="stable") if cols["t"] else np.zeros(0, dtype=int)
    out = {k: np.asarray(v)[order] if len(v) else np.asarray(v) for k, v in cols.items()}
    out["X"] = np.asarray(X, dtype=float).reshape(len(X), len(names))[order] if X else np.zeros((0, len(names)))
    out["feature_names"] = list(names)
    out["schema"] = schema
    return out


def _decision_for(scan: dict, bars: list[dict], i_fill: int, sec: int, side: str, expiry: int):
    j = i_fill - 1
    entry_t = bars[i_fill]["t"]
    while j >= 0 and bars[j]["t"] + sec >= entry_t - expiry:
        rec = scan["decisions"].get(j)
        if rec is not None and not rec[5] and rec[0] == side:
            return j, rec
        j -= 1
    return None, None


def _exit_rows(cfg, series, spreads, specs, scans, sec, paths, exit_cost_r: float) -> dict:
    strat, cost = cfg["strategy"], cfg["cost"]
    be_r = float(strat.get("break_even_at_r") or 0)
    slip_pts = float(cost.get("slippage_pts") or 0)
    expiry = int(strat.get("signal_expiry_minutes") or 15) * 60
    policy: exits.ExitPolicy = cfg["exit"]
    cols = {k: [] for k in ("t", "t_entry", "t_end", "symbol", "trade_id", "r_now", "final_r", "target")}
    X = []
    dropped = {"no_decision": 0, "path_mismatch": 0, "no_bar": 0}
    t_index = {s: {b["t"]: k for k, b in enumerate(bs)} for s, bs in series.items()}
    for p in paths:
        sym, side, entry_t = p["symbol"], p["side"], int(p["entry_t"])
        bars = series.get(sym) or []
        i_fill = t_index.get(sym, {}).get(entry_t)
        if i_fill is None:
            dropped["no_bar"] += 1
            continue
        _, rec = _decision_for(scans.get(sym) or {"decisions": {}}, bars, i_fill, sec, side, expiry)
        if rec is None:
            dropped["no_decision"] += 1
            continue
        _, _, sl0, tp, _, _ = rec
        spec = specs[sym]
        point = spec["point"]
        slip = slip_pts * point
        bf = bars[i_fill]
        sp_px = spreads[sym][i_fill] * point
        entry = bf["o"] + (sp_px + slip if side == "BUY" else -slip)
        sim = SimPosition(side, entry, sl0, tp, point, entry_t, be_at_r=be_r, slip_px=slip,
                          stops_px=float(spec.get("stops_level") or 0) * point)
        if not policy.is_base:
            exits.attach(sim, policy, history=bars[max(0, i_fill - exits.WARMUP_BARS):i_fill], bar_sec=sec)
        rows = p.get("path") or []
        cur = i_fill
        ok = True
        out_rows = []
        for n, row in enumerate(rows):
            i = t_index[sym].get(int(row["ts"]) - sec)
            if i is None or i < cur:
                ok = False
                break
            while cur <= i:
                hit = sim.step(bars[cur], spreads[sym][cur] * point)
                cur += 1
                if hit:
                    break
            if hit and (cur - 1 != i or n < len(rows) - 1):
                ok = False  # the replay closed before the recorded path did
                break
            px = sim.close_price(bars[i]["c"], spreads[sym][i] * point)
            if abs(sim.r_of(px) - float(row["r_now"])) > PATH_TOL_R:
                ok = False
                break
            if n == len(rows) - 1 or hit:
                break  # exit bar: the trade closed inside it (or at its close)
            feats = F.exit_features(bars[max(0, i - F.XS_WINDOW + 1): i + 1],
                                    {"side": side, "entry": entry, "sl0": sl0, "sl": sim.sl, "tp": sim.tp,
                                     "opened_ts": entry_t, "mfe_r": row.get("mfe_r"), "mae_r": row.get("mae_r"),
                                     "price_now": px, "spread_points": spreads[sym][i], "point": point}, bar_sec=sec)
            out_rows.append((int(row["ts"]), feats))
        if not ok:
            dropped["path_mismatch"] += 1
            continue
        tid = f"{sym}-{entry_t}"
        for ts, feats in out_rows:
            X.append(F.vector(feats, F.EXIT_FEATURE_NAMES))
            r_now = feats["r_now"]
            cols["t"].append(ts)
            cols["t_entry"].append(entry_t)
            # intrabar exits are stamped with the bar open: the label is known only at that bar's close
            cols["t_end"].append(int(p["exit_t"]) + (sec if p.get("outcome") in backtest.INTRABAR_REASONS else 0))
            cols["symbol"].append(sym)
            cols["trade_id"].append(tid)
            cols["r_now"].append(r_now)
            cols["final_r"].append(float(p["r"]))
            cols["target"].append(exit_target(float(p["r"]), float(p["gross_r"]), r_now, exit_cost_r))
    out = _finish(cols, X, F.EXIT_FEATURE_NAMES, F.EXIT_SCHEMA)
    out["_dropped"] = dropped
    return out


def exit_target(final_r_net: float, final_r_gross: float, r_now: float, exit_cost_r: float = 0.0) -> float:
    """Remaining R if held: final_R_net - close_now_R_net, close_now_R_net = r_now - (gross - net) - exit_cost_r."""
    cost_r = final_r_gross - final_r_net
    return round(final_r_net - (r_now - cost_r - exit_cost_r), 6)


def save(ds: dict, path: str) -> str:
    import joblib
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    joblib.dump(ds, path, compress=3)
    return path


def load(path: str) -> dict:
    import joblib
    return joblib.load(path)


def finite_frac(X: np.ndarray) -> float:
    return float(np.isfinite(X).mean()) if X.size else math.nan
