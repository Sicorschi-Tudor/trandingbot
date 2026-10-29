"""Shared plumbing of pattern builder #1 research plugins (pat_*.py of app.patterns.p1; not a plugin itself).

Two plugin kinds per pattern:
- standalone 'pat-<ID>': the pattern is the whole strategy (market entry at the next open, structural SL, TP from the
  pattern (price, capped at cap_r R) or an R multiple, time stop max_hold_minutes, no break-even; no live RESCUE because
  RUN_DEFAULTS is set);
- filter 'filt-<ID>': the live trend-v1 decision (app.strategy.analyze, bit-identical) with one extra market blocker
  when the pattern does not confirm the candidate side, so app.backtest runs the live exits (incl. RESCUE) on the
  filtered candidate stream. Pattern params come from cfg keys 'pat_<param>' (e.g. pat_K, pat_w, pat_fmode).
Research only: the live engine, the UI and the API never list these plugins."""
from __future__ import annotations

from ... import strategy
from ...patterns import p1
from .. import _common as C
from .. import _htf as X

RUN_DEFAULTS = {"break_even_at_r": 0, "close_at_session_end": False, "session": None, "max_hold_minutes": 240}
NEED = 200  # closed signal bars before a standalone decision (largest p1 tail + margin)


def pat_params(cfg: dict, defaults: dict) -> dict:
    out = {}
    for k, v in defaults.items():
        if k.startswith("pat_"):
            cv = cfg.get(k)
            out[k[4:]] = v if cv is None else cv
    return out


def _htf(pid: str, htf_bars):
    return list(htf_bars or []) if pid in p1.NEEDS_HTF else None


def filtered(pid: str, defaults: dict, symbol, sig_bars, htf_bars, quote, cfg, risk, ctx) -> dict:
    a = strategy.analyze(symbol, sig_bars, htf_bars, quote, cfg, risk, ctx)
    cand = a.get("candidate")
    if not cand or not sig_bars:
        return a
    prm = pat_params(cfg, defaults)
    keep = p1.PATTERNS[pid][2](sig_bars, len(sig_bars) - 1, cand["side"], float(cand["entry"]), prm,
                               htf=_htf(pid, htf_bars))
    if keep:
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


def standalone(pid: str, defaults: dict, symbol, sig_bars, htf_bars, quote, cfg, risk, ctx) -> dict:
    a, bars = X.start(symbol, sig_bars, htf_bars, quote, cfg, ctx, NEED)
    if bars is None:
        return a
    prm = pat_params(cfg, defaults)
    i = len(bars) - 1
    digits = int(quote.get("digits") or 5)
    point = float(quote.get("point") or 10 ** -digits)
    s = p1.PATTERNS[pid][1](bars, i, prm, htf=_htf(pid, htf_bars))
    a["indicators"] = X.indicators(digits, s["atr"] if s else p1.atr_at(bars, i), "FLAT")
    if not s:
        return C.hold(a, f"Niciun semnal {pid} pe bara închisă.")
    d, side = s["dir"], s["side"]
    entry = float(quote["ask"]) if d > 0 else float(quote["bid"])
    if d * (entry - s["sl"]) <= 0:
        return C.hold(a, "Prețul a depășit deja SL-ul structural.")
    sl_dist = max(d * (entry - s["sl"]), float(cfg.get("min_sl_points", 60)) * point)
    if s.get("tp") is not None:
        tp_r = d * (s["tp"] - entry) / sl_dist
        if s.get("cap_r"):
            tp_r = min(tp_r, float(s["cap_r"]))
    else:
        tp_r = float(s["tp_r"])
    a["regime"] = "TREND_UP" if d > 0 else "TREND_DOWN"
    comps = [f"Tipar {pid} {side} pe bara închisă ({s.get('note', '')})", f"TP {tp_r:.2f}R"]
    if tp_r <= 0:
        return C.hold(a, f"Setup {side} fără spațiu până la TP.", *("(+) " + c for c in comps))
    return C.finish(a, symbol, side, s["sl"], None, quote, cfg, risk, ctx, comps, len(comps), tp_r=tp_r,
                    note=f"time stop {int(cfg.get('max_hold_minutes') or 0)} min")
