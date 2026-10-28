"""meanrev-v1: fade Bollinger extremes in a flat HTF.

BUY when the closed bar closes below the lower band with RSI < rsi_low, SELL above the upper band with RSI > rsi_high,
only when |HTF EMA50 slope over 3 bars| <= flat_slope_atr × HTF ATR (range market).
Own stop logic: SL = (min(lower band, bar low) − sl_atr_k × ATR) for BUY (mirror for SELL), floored at min_sl_points;
TP = middle band (SMA) of the signal bar. Setups with RR < min_rr are kept as candidates but blocked."""
from __future__ import annotations

from .. import indicators as ind
from . import _common as C

NAME = "meanrev-v1"
VERSION = "1.0"
DESCRIPTION_RO = ("Revenire la medie: vinde/cumpără închiderile în afara benzilor Bollinger(20,2) cu RSI(14) "
                  "peste 70 / sub 30, doar când trendul H1 este plat. TP la banda mijlocie, SL dincolo de bandă "
                  "cu k×ATR.")
PARAMS = {"bb_period": 20, "bb_k": 2.0, "rsi_low": 30.0, "rsi_high": 70.0, "flat_slope_atr": 0.05,
          "sl_atr_k": 1.0, "min_rr": 0.5}


def analyze(symbol, sig_bars, htf_bars, quote, cfg, risk, ctx) -> dict:
    a = C.skeleton(symbol, sig_bars, quote, cfg, ctx)
    if not C.data_ok(a, quote, sig_bars, htf_bars, cfg):
        return a
    g = lambda k: C.p(cfg, PARAMS, k)  # noqa: E731
    digits = int(quote.get("digits") or 5)
    s = C.sig_state(sig_bars)
    h = C.htf_state(htf_bars)
    period, k = int(g("bb_period")), float(g("bb_k"))
    mid_s, up_s, lo_s = ind.bollinger(s["closes"][-period:], period, k)
    mid, up, lo = ind.last(mid_s), ind.last(up_s), ind.last(lo_s)
    a["indicators"] = C.base_indicators(s, h, digits)
    a["indicators"].update({"bb_mid": C.r_(mid, digits + 1), "bb_up": C.r_(up, digits + 1),
                            "bb_lo": C.r_(lo, digits + 1)})
    if None in (s["ema_fast"], s["ema_slow"], s["rsi_v"], s["atr_v"], s["h_now"], mid, h["slope"]):
        return C.hold(a, "Indicatori incompleți (date insuficiente).")
    a["regime"] = C.regime(s)
    close, rsi_v, atr_v = s["close"], s["rsi_v"], s["atr_v"]
    htf = cfg.get("htf_timeframe", "H1")
    flat = abs(h["slope"]) <= float(g("flat_slope_atr")) * h["atr"]
    side = None
    if close < lo and rsi_v < float(g("rsi_low")):
        side = "BUY"
        comps = [f"Închiderea sub banda Bollinger inferioară ({lo:.{digits}f})",
                 f"RSI {rsi_v:.1f} sub {float(g('rsi_low')):g} (supravânzare)"]
    elif close > up and rsi_v > float(g("rsi_high")):
        side = "SELL"
        comps = [f"Închiderea peste banda Bollinger superioară ({up:.{digits}f})",
                 f"RSI {rsi_v:.1f} peste {float(g('rsi_high')):g} (supracumpărare)"]
    if side is None:
        return C.hold(a, f"Fără extremă Bollinger+RSI (închidere {close:.{digits}f}, benzi "
                         f"{lo:.{digits}f}–{up:.{digits}f}, RSI {rsi_v:.1f}).")
    if not flat:
        return C.hold(a, f"Setup {side} de revenire ignorat: trendul pe {htf} nu este plat ({h['trend']}).",
                      *("(+) " + c for c in comps))
    comps.append(f"Trend plat pe {htf} (pantă EMA50 mică față de ATR)")
    kk = float(g("sl_atr_k"))
    bar = s["bars"][-1]
    sl_price = (min(lo, bar["l"]) - kk * atr_v) if side == "BUY" else (max(up, bar["h"]) + kk * atr_v)
    return C.finish(a, symbol, side, sl_price, mid, quote, cfg, risk, ctx, comps, len(comps),
                    min_rr=float(g("min_rr")), note="TP la banda mijlocie")
