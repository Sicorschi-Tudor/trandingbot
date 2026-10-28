"""breakout-v1: Asian-range breakout.

Range = high/low of signal bars opening in [range_start_h, range_end_h) UTC of the current day (needs at least
min_range_frac of the expected bars). Trade window: bars opening in [range_end_h, trade_end_h) UTC.
Trigger only on the FIRST closed bar of the window that closes beyond the range (either side): that keeps one
setup per symbol per day without state. The break must agree with the HTF trend (UP/FLAT for BUY, DOWN/FLAT for
SELL); a break against the trend ends the day for that symbol.
Own stop logic: SL at the range midpoint (sl_mode 'mid') or the opposite side ('opposite'), its distance capped at
sl_max_atr × ATR and floored at min_sl_points; TP = tp_range_mult × range width from entry.
Optional range_max_atr > 0 skips days whose range is wider than range_max_atr × signal ATR."""
from __future__ import annotations

from . import _common as C

NAME = "breakout-v1"
VERSION = "1.0"
DESCRIPTION_RO = ("Spargerea intervalului asiatic (00:00–06:00 UTC): între 06:00 și 10:00 UTC intră pe prima bară "
                  "închisă în afara intervalului, în direcția trendului H1 sau cu H1 plat. SL la mijlocul "
                  "intervalului (plafonat la ATR), TP = 1,5× lățimea intervalului; o singură intrare pe zi.")
PARAMS = {"range_start_h": 0, "range_end_h": 6, "trade_end_h": 10, "min_range_frac": 0.8, "sl_mode": "mid",
          "sl_max_atr": 3.0, "tp_range_mult": 1.5, "range_max_atr": 0.0}


def analyze(symbol, sig_bars, htf_bars, quote, cfg, risk, ctx) -> dict:
    a = C.skeleton(symbol, sig_bars, quote, cfg, ctx)
    if not C.data_ok(a, quote, sig_bars, htf_bars, cfg):
        return a
    g = lambda k: C.p(cfg, PARAMS, k)  # noqa: E731
    digits = int(quote.get("digits") or 5)
    s = C.sig_state(sig_bars)
    h = C.htf_state(htf_bars)
    a["indicators"] = C.base_indicators(s, h, digits)
    if None in (s["ema_fast"], s["ema_slow"], s["rsi_v"], s["atr_v"], s["h_now"]):
        return C.hold(a, "Indicatori incompleți (date insuficiente).")
    a["regime"] = C.regime(s)

    sec = C.tf_seconds(cfg)
    last = sig_bars[-1]
    t = int(last["t"])
    day0 = t - t % 86400
    r0, r1, w1 = day0 + int(g("range_start_h")) * 3600, day0 + int(g("range_end_h")) * 3600, day0 + int(g("trade_end_h")) * 3600
    if not (r1 <= t < w1):
        return C.hold(a, f"În afara ferestrei de spargere ({int(g('range_end_h')):02d}:00–{int(g('trade_end_h')):02d}:00 UTC).")
    # scan back to the range start (bars are time-ordered)
    rng, win = [], []
    for b in reversed(sig_bars):
        bt = b["t"]
        if bt < r0:
            break
        if bt >= r1:
            win.append(b)
        else:
            rng.append(b)
    expected = max(1, (r1 - r0) // sec)
    if len(rng) < float(g("min_range_frac")) * expected:
        return C.hold(a, f"Interval asiatic incomplet ({len(rng)}/{expected} bare).")
    hi, lo = max(b["h"] for b in rng), min(b["l"] for b in rng)
    width = hi - lo
    a["indicators"].update({"range_high": C.r_(hi, digits), "range_low": C.r_(lo, digits),
                            "range_width": C.r_(width, digits + 1)})
    if width <= 0:
        return C.hold(a, "Interval asiatic fără lățime.")
    rmax = float(g("range_max_atr"))
    if rmax > 0 and width > rmax * s["atr_v"]:
        return C.hold(a, f"Interval asiatic prea larg ({width / s['atr_v']:.1f}×ATR > {rmax:g}).")
    win.reverse()  # chronological, last element is the current bar
    for b in win[:-1]:
        if b["c"] > hi or b["c"] < lo:
            return C.hold(a, "Intervalul a fost deja spart azi (o singură intrare pe zi).")
    close = last["c"]
    if close > hi:
        side = "BUY"
    elif close < lo:
        side = "SELL"
    else:
        return C.hold(a, f"Prețul este în interval ({lo:.{digits}f}–{hi:.{digits}f}).")
    htf = cfg.get("htf_timeframe", "H1")
    if (side == "BUY" and h["trend"] == "DOWN") or (side == "SELL" and h["trend"] == "UP"):
        return C.hold(a, f"Spargere {side} contra trendului pe {htf} ({h['trend']}) — ziua este ignorată.")
    comps = [f"Închidere {'peste maximul' if side == 'BUY' else 'sub minimul'} intervalului asiatic "
             f"({(hi if side == 'BUY' else lo):.{digits}f})",
             f"Trend pe {htf}: {h['trend']}"]
    sign = 1 if side == "BUY" else -1
    entry = float(quote["ask"]) if side == "BUY" else float(quote["bid"])
    mid = (hi + lo) / 2
    sl_price = mid if g("sl_mode") == "mid" else (lo if side == "BUY" else hi)
    cap = float(g("sl_max_atr")) * s["atr_v"]
    if cap > 0 and sign * (entry - sl_price) > cap:
        sl_price = entry - sign * cap
    return C.finish(a, symbol, side, sl_price, entry + sign * float(g("tp_range_mult")) * width, quote, cfg, risk,
                    ctx, comps, len(comps), note=f"TP {float(g('tp_range_mult')):g}× intervalul")
