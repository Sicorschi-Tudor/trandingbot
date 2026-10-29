"""disc-rev-htfslope: trend-v1 plus a structural HTF confirmation gate (research only).

Review idea: the live HTF rule (app/strategy.py: EMA50 slope over 3 HTF bars vs 0.05*ATR14) is FLAT on only ~14 % of
H1 gold bars (TRAIN), so the 'H1 confirmation' almost never vetoes an entry. This plugin runs trend-v1 unchanged
(same cfg / risk / context, bit-identical candidate levels) and appends ONE extra blocker when the HTF structure does
not confirm the trade direction:
    slope  = EMA50[-1] - EMA50[-1 - htf_slope_bars]   on the closed HTF bars (same series trend-v1 uses)
    BUY  needs slope >  htf_slope_atr * ATR14(HTF)   [and HTF close > EMA50 when htf_require_close_side]
    SELL needs slope < -htf_slope_atr * ATR14(HTF)   [and HTF close < EMA50 when htf_require_close_side]
htf_slope_atr = 0 and htf_require_close_side = False disables the gate (pure trend-v1 = the matching baseline,
research/results/REVIEW/entry_gold150.json / entry_base_h1.json). invert=True blocks the complement (entries that DO
pass the structural rule) as a diagnostic of which side carries the information.
Research only (discovery package): never offered to the live engine/UI."""
from __future__ import annotations

from ... import indicators as ind
from ... import strategy
from .. import trend_v1

NAME = "disc-rev-htfslope"
VERSION = "0.1"
DESCRIPTION_RO = ("Cercetare (review): trend-v1 identic, plus confirmare structurală HTF — panta EMA50 pe htf_slope_bars "
                  "bare ≥ htf_slope_atr×ATR în direcția tranzacției, opțional închiderea HTF de partea corectă a EMA50.")
PARAMS = {**trend_v1.PARAMS, "htf_slope_bars": 10, "htf_slope_atr": 0.0, "htf_require_close_side": False,
          "invert": False}


def _param(cfg, key):
    v = cfg.get(key)
    return PARAMS[key] if v is None else v


def htf_confirms(htf_bars, side: str, bars: int, k_atr: float, close_side: bool):
    """True/False when the HTF structure confirms `side`; None when the series is too short.
    Returns (confirms, slope_in_atr, close_vs_ema50)."""
    hb = htf_bars or []
    if len(hb) < max(int(bars) + 1, 60):
        return None, None, None
    hc = [b["c"] for b in hb]
    e50 = ind.ema(hc, 50)
    atr = ind.last(ind.atr([b["h"] for b in hb], [b["l"] for b in hb], hc, 14))
    e_now, e_then = ind.last(e50), ind.last(e50, int(bars))
    if e_now is None or e_then is None or not atr:
        return None, None, None
    slope_atr = (e_now - e_then) / atr
    sign = 1 if side == "BUY" else -1
    ok = sign * slope_atr > float(k_atr)
    close_rel = sign * (hc[-1] - e_now)
    if close_side:
        ok = ok and close_rel > 0
    return ok, slope_atr, close_rel


def analyze(symbol, sig_bars, htf_bars, quote, cfg, risk, ctx) -> dict:
    a = strategy.analyze(symbol, sig_bars, htf_bars, quote, cfg, risk, ctx)
    cand = a.get("candidate")
    k_atr = float(_param(cfg, "htf_slope_atr"))
    close_side = bool(_param(cfg, "htf_require_close_side"))
    if not cand or (k_atr <= 0 and not close_side):
        return a
    bars = int(_param(cfg, "htf_slope_bars"))
    invert = bool(_param(cfg, "invert"))
    side = cand["side"]
    ok, slope_atr, close_rel = htf_confirms(htf_bars, side, bars, k_atr, close_side)
    if ok is None:
        return a
    if ok != invert:  # confirmed (or, inverted, unconfirmed): not in the blocked region
        return a
    htf_name = cfg.get("htf_timeframe", "H1")
    blocker = (f"Confirmare structurală {htf_name} {'lipsă' if not invert else 'prezentă (invert)'} pentru {side}: "
               f"pantă EMA50/{bars} bare = {slope_atr:+.2f} ATR (prag {k_atr:g}), închidere vs EMA50 = {close_rel:+.2f}"
               f"{' (necesară)' if close_side else ''} — filtru de structură HTF.")
    cand["blockers"].append(blocker)
    if a["direction"] != "HOLD":
        comps = list(a["reasons"])
        a["direction"] = "HOLD"
        a["entry"] = a["stop_loss"] = a["take_profit"] = a["rr"] = None
        a["reasons"] = [f"Setup {side} detectat, dar blocat de filtre:", blocker, *("(+) " + c for c in comps)]
    else:
        a["reasons"].append(blocker)
    return a
