"""Shared plumbing of pattern builder #0 research plugins (pat_*.py in this package; not a plugin itself).

Two plugin kinds per pattern of app.patterns.p0:
- standalone 'pat-<ID>': the pattern is the whole strategy (entry at the next open, structural SL, R-multiple TP,
  time stop hold_bars bars through max_hold_minutes, no break-even, no live RESCUE because RUN_DEFAULTS is set);
- filter 'filt-<ID>': the live trend-v1 decision (app.strategy.analyze, bit-identical) with one extra market blocker
  when the pattern disagrees with the candidate side, so app.backtest runs the live exits (incl. RESCUE) on the
  filtered candidate stream. Pattern params come from cfg keys 'pat_<param>' (e.g. pat_K, pat_m, pat_fmode).
Research only: the live engine, the UI and the API never list these plugins."""
from __future__ import annotations

from ... import strategy
from ...patterns import p0
from ...strategy import MIN_HTF_BARS
from .. import _common as C
from .. import _htf as X

RUN_DEFAULTS = {"break_even_at_r": 0, "close_at_session_end": False, "session": None, "max_hold_minutes": 120}
NEED = 220  # closed signal bars before a standalone decision (Ichimoku x2 needs 52*2 + 26*2 + 1)


def pat_params(cfg: dict, defaults: dict) -> dict:
    """Pattern params from cfg 'pat_<k>' keys over the plugin defaults (PARAMS without the prefix)."""
    out = {}
    for k, v in defaults.items():
        if k.startswith("pat_"):
            cv = cfg.get(k)
            out[k[4:]] = v if cv is None else cv
    return out


# ---- filter plugins ----------------------------------------------------------------------------------------------

def filtered(pid: str, defaults: dict, symbol, sig_bars, htf_bars, quote, cfg, risk, ctx) -> dict:
    a = strategy.analyze(symbol, sig_bars, htf_bars, quote, cfg, risk, ctx)
    cand = a.get("candidate")
    if not cand or not sig_bars:
        return a
    prm = pat_params(cfg, defaults)
    if p0.filter_keep(pid, sig_bars, len(sig_bars) - 1, cand["side"], float(cand["entry"]), prm):
        return a
    msg = f"Filtru {pid}: tiparul nu confirmă {cand['side']} ({', '.join(f'{k}={v}' for k, v in prm.items())})."
    cand["blockers"].append(msg)
    if a["direction"] != "HOLD":
        a["direction"] = "HOLD"
        a["entry"] = a["stop_loss"] = a["take_profit"] = a["rr"] = None
        a["reasons"] = [f"Setup {cand['side']} detectat, dar blocat de filtre:", msg]
    else:
        a["reasons"].append(msg)
    return a


# ---- standalone plugins ------------------------------------------------------------------------------------------

def _sl_dist(entry: float, sl: float, side: int, cfg: dict, quote: dict) -> float:
    digits = int(quote.get("digits") or 5)
    point = float(quote.get("point") or 10 ** -digits)
    return max(side * (entry - sl), float(cfg.get("min_sl_points", 60)) * point)


def standalone(pid: str, defaults: dict, symbol, sig_bars, htf_bars, quote, cfg, risk, ctx) -> dict:
    a, bars = X.start(symbol, sig_bars, htf_bars, quote, cfg, ctx, NEED)
    if bars is None:
        return a
    prm = pat_params(cfg, defaults)
    i = len(bars) - 1
    digits = int(quote.get("digits") or 5)
    htf = X.htf_trend(htf_bars) if pid == "L2_ROUND" else "FLAT"  # only the round-level fade reads the HTF trend
    side = p0.DETECTORS[pid](bars, i, prm)  # detector first: most bars stop here, before the ATR
    atr = p0.atr_at(bars, i) if side else None
    a["indicators"] = X.indicators(digits, atr, htf)
    if side and not atr:
        return C.hold(a, "ATR indisponibil.")
    if pid == "L2_ROUND" and side:
        if len(htf_bars or []) < MIN_HTF_BARS:
            return C.hold(a, "Trend HTF indisponibil.")
        if (side > 0 and htf != "UP") or (side < 0 and htf != "DOWN"):
            return C.hold(a, f"Respingere la nivel rotund contra trendului HTF ({htf}).")
    if not side:
        return C.hold(a, f"Niciun semnal {pid} pe bara închisă.")
    s_txt = "BUY" if side > 0 else "SELL"
    entry = float(quote["ask"]) if side > 0 else float(quote["bid"])
    tp_r = float(prm.get("R", 2.0))
    if pid == "C1_ENGULF":
        sl = p0.c1_levels(bars, i, side)["sl"]
    elif pid == "C4_3SOLDIERS":
        sl = p0.c4_levels(bars, i, side, entry)["sl"]
    elif pid == "P3_FLAG":
        lv = p0.p3_levels(bars, i, prm)
        sl = lv["sl"]
        tp_r = min(lv["height"] / _sl_dist(entry, sl, side, cfg, quote), 3.0)
    elif pid == "L2_ROUND":
        b = bars[i]
        sl = b["l"] - 0.5 * atr if side > 0 else b["h"] + 0.5 * atr
        tp_r = 2.0
    elif pid == "S1_SWEEP":
        lv = p0.s1_levels(bars, i, prm)
        sl = lv["sl"]
        d = _sl_dist(entry, sl, side, cfg, quote)
        tgt = lv["target"]
        tgt_r = None if tgt is None else side * (tgt - entry) / d
        tp_r = 2.0 if tgt_r is None or tgt_r < 1.2 else min(tgt_r, 4.0)
    elif pid == "I1_ICHI_CLOUD":
        ic = p0.ichimoku_at(bars, i, int(prm.get("scale", 1)))
        if side > 0:
            cands = [ic["kijun"] - 0.2 * atr, ic["bottom"]]
            cands = [x for x in cands if x < entry]
            sl = max(cands) if cands else entry - atr
            sl = min(sl, entry - atr)
        else:
            cands = [ic["kijun"] + 0.2 * atr, ic["top"]]
            cands = [x for x in cands if x > entry]
            sl = min(cands) if cands else entry + atr
            sl = max(sl, entry + atr)
        tp_r = 2.5
    elif pid == "I4_ADX":
        sl = entry - side * 1.5 * atr
        tp_r = 2.5
    else:
        raise KeyError(pid)
    if side * (entry - sl) <= 0:
        return C.hold(a, "Prețul a depășit deja SL-ul structural.")
    a["regime"] = "TREND_UP" if side > 0 else "TREND_DOWN"
    comps = [f"Tipar {pid} {s_txt} pe bara închisă", f"TP {tp_r:.2f}R"]
    return C.finish(a, symbol, s_txt, sl, None, quote, cfg, risk, ctx, comps, len(comps), tp_r=tp_r,
                    note=f"time stop {int(cfg.get('max_hold_minutes') or 0)} min")


def bias(pid: str, defaults: dict, sig_bars, cfg) -> int:
    """Direction on the last closed bar for the signal_flip exit: I1 = close vs Kijun, I4 = +DI vs -DI."""
    prm = pat_params(cfg, defaults)
    i = len(sig_bars) - 1
    if pid == "I1_ICHI_CLOUD":
        ic = p0.ichimoku_at(sig_bars, i, int(prm.get("scale", 1)))
        if ic is None:
            return 0
        c = sig_bars[i]["c"]
        return 1 if c > ic["kijun"] else -1 if c < ic["kijun"] else 0
    if pid == "I4_ADX":
        d = p0.dmi_at(sig_bars, i, int(prm.get("n", 14)))
        if d is None:
            return 0
        return 1 if d["pdi"] > d["mdi"] else -1 if d["mdi"] > d["pdi"] else 0
    return 0
