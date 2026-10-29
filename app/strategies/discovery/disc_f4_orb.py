"""disc-f4-orb: opening-range breakout (Stage F, family F4; research only).

Range = high/low of the first `range_bars` signal-TF bars opening at/after `open_hour_utc` (07:00 UTC London, 13:00 UTC
New York) of the current UTC day; all `range_bars` bars must be present. Trade window: bars opening in
[range end, range end + trade_window_hours). Trigger only on the FIRST closed bar of the window whose close is beyond
the range (either side) — any earlier window bar that closed outside the range ends the day for that symbol, so the
setup is "one per symbol per day" without state (same approach as breakout_v1).
Filters: min_range_atr × ATR(14) <= range width <= max_range_atr × ATR(14) (ATR on the signal TF, closed bars);
optional htf_filter (break against the HTF EMA50 trend is skipped; HTF is otherwise reported only, 'NONE' allowed).
Stop: SL at the opposite side of the range (sl_mode 'opposite') or at its midpoint ('mid'), optionally capped at
sl_max_atr × ATR (0 = no cap), floored at min_sl_points by _common.finish; TP = tp_range_mult × range width from the
entry. Fixed-hour exit = the run's session (--session-json) + close_at_session_end (RUN_DEFAULTS) or an exit
max_hold_* parameter.
Strictly on closed bars: the side is decided from sig_bars[-1]['c'] vs the range; the quote only sets the fill price
and the spread filter."""
from __future__ import annotations

from .. import _common as C
from .. import _htf as X
from ...strategy import MIN_HTF_BARS, MIN_SIG_BARS

NAME = "disc-f4-orb"
VERSION = "0.1"
DESCRIPTION_RO = ("Cercetare F4: spargerea intervalului de deschidere (Londra 07:00 UTC sau New York 13:00 UTC): "
                  "intervalul = primele range_bars bare după deschidere; intră pe prima bară închisă în afara lui "
                  "(în fereastra trade_window_hours), dacă lățimea este între min_range_atr și max_range_atr × ATR(14). "
                  "SL la partea opusă (sau mijloc), TP = tp_range_mult × interval; o singură intrare pe zi; "
                  "închidere la ora fixă prin sesiune.")
PARAMS = {"open_hour_utc": 7, "range_bars": 2, "trade_window_hours": 4.0, "min_range_atr": 0.5, "max_range_atr": 3.0,
          "sl_mode": "opposite", "sl_max_atr": 0.0, "tp_range_mult": 1.5, "htf_filter": False}
# backtest-only defaults (app.backtest.resolve applies them unless the run sets the key): pure SL/TP/time exit,
# no break-even, no live RESCUE overlay, position closed when the run's session ends (the fixed-hour exit)
RUN_DEFAULTS = {"max_hold_minutes": 0, "break_even_at_r": 0, "be_lock_r": 0, "rescue_enabled": False,
                "close_at_session_end": True}


def _htf(htf_bars) -> dict:
    if htf_bars and len(htf_bars) >= MIN_HTF_BARS:
        return C.htf_state(htf_bars)
    return {"trend": "FLAT", "slope": None, "atr": 0.0, "ema50": None, "close": None}


def _ready(a: dict, quote, sig_bars) -> bool:
    if not quote or not quote.get("bid") or not quote.get("ask"):
        a["reasons"].append("Nu există cotație bid/ask pentru simbol.")
        return False
    if len(sig_bars) < MIN_SIG_BARS:
        a["reasons"].append(f"Date insuficiente: {len(sig_bars)}/{MIN_SIG_BARS} bare închise pe {a['timeframe']}.")
        return False
    return True


def analyze(symbol, sig_bars, htf_bars, quote, cfg, risk, ctx) -> dict:
    a = C.skeleton(symbol, sig_bars, quote, cfg, ctx)
    if not _ready(a, quote, sig_bars):
        return a
    g = lambda k: C.p(cfg, PARAMS, k)  # noqa: E731
    digits = int(quote.get("digits") or 5)
    s = C.sig_state(sig_bars)
    h = _htf(htf_bars)
    a["indicators"] = C.base_indicators(s, h, digits)
    if None in (s["ema_fast"], s["ema_slow"], s["rsi_v"], s["atr_v"], s["h_now"]) or not s["atr_v"]:
        return C.hold(a, "Indicatori incompleți (date insuficiente).")
    a["regime"] = C.regime(s)

    sec = C.tf_seconds(cfg)
    n_rng = max(1, int(g("range_bars")))
    last = sig_bars[-1]
    t = int(last["t"])
    day0 = t - t % 86400
    r0 = day0 + int(g("open_hour_utc")) * 3600
    r1 = r0 + n_rng * sec
    w1 = r1 + int(round(float(g("trade_window_hours")) * 3600))
    oh = int(g("open_hour_utc"))
    if not (r1 <= t < w1):
        return C.hold(a, f"În afara ferestrei de spargere ({oh:02d}:00 UTC + {n_rng} bare → +{float(g('trade_window_hours')):g} h).")
    rng, win = [], []
    for b in reversed(sig_bars):
        bt = int(b["t"])
        if bt < r0:
            break
        if bt >= r1:
            win.append(b)
        else:
            rng.append(b)
    if len(rng) < n_rng:
        return C.hold(a, f"Interval de deschidere incomplet ({len(rng)}/{n_rng} bare).")
    hi, lo = max(b["h"] for b in rng), min(b["l"] for b in rng)
    width = hi - lo
    atr_v = s["atr_v"]
    a["indicators"].update({"range_high": C.r_(hi, digits), "range_low": C.r_(lo, digits),
                            "range_width": C.r_(width, digits + 1), "range_atr": C.r_(width / atr_v, 3)})
    if width <= 0:
        return C.hold(a, "Interval de deschidere fără lățime.")
    rmin, rmax = float(g("min_range_atr")), float(g("max_range_atr"))
    if rmax > 0 and width > rmax * atr_v:
        return C.hold(a, f"Interval prea larg ({width / atr_v:.2f}×ATR > {rmax:g}).")
    if rmin > 0 and width < rmin * atr_v:
        return C.hold(a, f"Interval prea îngust ({width / atr_v:.2f}×ATR < {rmin:g}).")
    win.reverse()  # chronological; the last element is the current (closed) bar
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
    htf = cfg.get("htf_timeframe", "NONE")
    if bool(g("htf_filter")) and ((side == "BUY" and h["trend"] == "DOWN") or (side == "SELL" and h["trend"] == "UP")):
        return C.hold(a, f"Spargere {side} contra trendului pe {htf} ({h['trend']}) — ziua este ignorată.")
    comps = [f"Închidere {'peste maximul' if side == 'BUY' else 'sub minimul'} intervalului de deschidere "
             f"{oh:02d}:00 UTC ({(hi if side == 'BUY' else lo):.{digits}f}, lățime {width / atr_v:.2f}×ATR)",
             f"Trend pe {htf}: {h['trend']}"]
    sign = 1 if side == "BUY" else -1
    entry = float(quote["ask"]) if side == "BUY" else float(quote["bid"])
    mid = (hi + lo) / 2
    sl_price = mid if str(g("sl_mode")) == "mid" else (lo if side == "BUY" else hi)
    cap = float(g("sl_max_atr")) * atr_v
    if cap > 0 and sign * (entry - sl_price) > cap:
        sl_price = entry - sign * cap
    tp_price = entry + sign * float(g("tp_range_mult")) * width
    return C.finish(a, symbol, side, sl_price, tp_price, quote, cfg, risk, ctx, comps, len(comps),
                    note=f"TP {float(g('tp_range_mult')):g}× intervalul")
