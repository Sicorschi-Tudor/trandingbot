"""pullback-v1: buy pullbacks in a strong HTF trend.

Strong HTF trend: HTF EMA50 slope over 3 bars > strong_slope_atr × HTF ATR and the last HTF close on the trend side
of EMA50 (mirror for DOWN). Signal TF: one of the last pb_lookback bars touched EMA20 (low <= EMA20 for BUY,
high >= EMA20 for SELL), RSI(14) crosses back through 50 on the signal bar (prev <= 50 < now for BUY) and the close is
back on the trend side of EMA20.
Own stop logic: SL = swing low of the last swing_n bars − sl_buffer_atr × ATR (mirror for SELL), floored at
min_sl_points; TP = tp_r × final SL distance (2R). A swing stop wider than sl_max_atr × ATR keeps the candidate
but blocks it."""
from __future__ import annotations

from . import _common as C

NAME = "pullback-v1"
VERSION = "1.0"
DESCRIPTION_RO = ("Pullback în trend puternic pe H1 (pantă EMA50 mare, preț de partea trendului): după atingerea "
                  "EMA20 pe M5, intră când RSI trece înapoi peste/sub 50. SL sub minimul ultimelor N bare, TP 2R.")
PARAMS = {"strong_slope_atr": 0.10, "pb_lookback": 5, "swing_n": 10, "sl_buffer_atr": 0.1, "sl_max_atr": 3.0,
          "tp_r": 2.0}


def analyze(symbol, sig_bars, htf_bars, quote, cfg, risk, ctx) -> dict:
    a = C.skeleton(symbol, sig_bars, quote, cfg, ctx)
    if not C.data_ok(a, quote, sig_bars, htf_bars, cfg):
        return a
    g = lambda k: C.p(cfg, PARAMS, k)  # noqa: E731
    digits = int(quote.get("digits") or 5)
    s = C.sig_state(sig_bars)
    h = C.htf_state(htf_bars)
    a["indicators"] = C.base_indicators(s, h, digits)
    if None in (s["ema_fast"], s["ema_slow"], s["rsi_v"], s["rsi_prev"], s["atr_v"], s["h_now"], h["slope"]):
        return C.hold(a, "Indicatori incompleți (date insuficiente).")
    a["regime"] = C.regime(s)
    htf = cfg.get("htf_timeframe", "H1")
    thr = float(g("strong_slope_atr")) * h["atr"]
    if h["slope"] > thr and h["close"] > h["ema50"]:
        side = "BUY"
    elif h["slope"] < -thr and h["close"] < h["ema50"]:
        side = "SELL"
    else:
        return C.hold(a, f"Fără trend puternic pe {htf} (pantă EMA50 sub {float(g('strong_slope_atr')):g}×ATR "
                         f"sau prețul de partea greșită a EMA50).")
    sign = 1 if side == "BUY" else -1
    n = max(1, int(g("pb_lookback")))
    e20, lows, highs = s["e20"], s["lows"], s["highs"]
    touched = any(e20[-i] is not None and (lows[-i] <= e20[-i] if side == "BUY" else highs[-i] >= e20[-i])
                  for i in range(1, n + 1))
    rsi_v, rsi_p, close, ef = s["rsi_v"], s["rsi_prev"], s["close"], s["ema_fast"]
    crossed = (rsi_p <= 50 < rsi_v) if side == "BUY" else (rsi_p >= 50 > rsi_v)
    back = sign * (close - ef) > 0
    if not (touched and crossed and back):
        miss = []
        if not touched:
            miss.append(f"fără atingerea EMA20 în ultimele {n} bare")
        if not crossed:
            miss.append(f"RSI nu a trecut înapoi prin 50 ({rsi_p:.1f}→{rsi_v:.1f})")
        if not back:
            miss.append("închiderea nu a revenit de partea trendului față de EMA20")
        return C.hold(a, f"Trend {'UP' if side == 'BUY' else 'DOWN'} puternic pe {htf}, dar pullback incomplet: "
                         + "; ".join(miss) + ".")
    comps = [f"Trend puternic pe {htf} (pantă EMA50 > {float(g('strong_slope_atr')):g}×ATR, preț de partea trendului)",
             f"Pullback la EMA20 în ultimele {n} bare",
             f"RSI trece înapoi prin 50 ({rsi_p:.1f}→{rsi_v:.1f})"]
    sw = max(1, int(g("swing_n")))
    atr_v = s["atr_v"]
    buf = float(g("sl_buffer_atr")) * atr_v
    sl_price = (min(lows[-sw:]) - buf) if side == "BUY" else (max(highs[-sw:]) + buf)
    entry = float(quote["ask"]) if side == "BUY" else float(quote["bid"])
    extra = []
    smax = float(g("sl_max_atr"))
    if smax > 0 and sign * (entry - sl_price) > smax * atr_v:
        extra.append(f"SL structural prea larg ({sign * (entry - sl_price) / atr_v:.1f}×ATR > {smax:g}).")
    return C.finish(a, symbol, side, sl_price, None, quote, cfg, risk, ctx, comps, len(comps),
                    tp_r=float(g("tp_r")), extra_blockers=extra, note=f"TP {float(g('tp_r')):g}R")
