"""disc-f4-sessmom: session momentum (Stage F, family F4; research only).

At the close of the first `move_bars` full signal-TF bars after the session open (`open_hour_utc`, 07:00 UTC London or
13:00 UTC New York; H1 bars → the decision is at 08:00 UTC for London with move_bars 1), go in the direction of that
move if |close − open of the first bar| >= k_move × ATR(atr_n) (closed bars, signal TF). Optional min_body_frac: the
move must be at least that fraction of the high-low span of the move bars (a clean directional bar, not a wick).
Optional htf_filter (no entries against the HTF EMA50 trend; HTF otherwise reported only, 'NONE' allowed).
Stop: SL = k_sl × ATR, TP = k_tp × ATR from the entry (floor min_sl_points via _common.finish). Time stop at a fixed
hour = the run's session (--session-json, e.g. 07:00–16:00 UTC) + close_at_session_end (RUN_DEFAULTS).
Exactly one decision bar per symbol per day (the bar that closes move_bars after the open), so the setup is
"one per day" without state. Strictly on closed bars: the side is sign(close − open) of closed bars; the quote only sets
the fill price and the spread filter."""
from __future__ import annotations

from .. import _common as C
from .. import _htf as X
from ...strategy import MIN_HTF_BARS, MIN_SIG_BARS

NAME = "disc-f4-sessmom"
VERSION = "0.1"
DESCRIPTION_RO = ("Cercetare F4: momentum de sesiune — la închiderea primei ore după deschiderea Londrei (07:00 UTC) "
                  "sau New York (13:00 UTC) intră în direcția mișcării din acea oră dacă |mișcarea| ≥ k_move × ATR(14); "
                  "SL = k_sl × ATR, TP = k_tp × ATR; închidere la ora fixă prin sesiune (16:00 UTC).")
PARAMS = {"open_hour_utc": 7, "move_bars": 1, "k_move": 0.5, "k_sl": 1.0, "k_tp": 1.5, "atr_n": 14,
          "min_body_frac": 0.0, "htf_filter": False}
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
    if None in (s["ema_fast"], s["ema_slow"], s["rsi_v"], s["atr_v"], s["h_now"]):
        return C.hold(a, "Indicatori incompleți (date insuficiente).")
    a["regime"] = C.regime(s)
    atr_n = max(2, int(g("atr_n")))
    atr_v = s["atr_v"]
    if atr_n != 14:
        sb = s["bars"]
        atr_v = X.atr_series(sb, atr_n)[-1] if len(sb) > atr_n else None
    if not atr_v:
        return C.hold(a, "ATR indisponibil.")
    a["indicators"]["atr"] = C.r_(atr_v, digits + 1)

    sec = C.tf_seconds(cfg)
    n_mv = max(1, int(g("move_bars")))
    last = sig_bars[-1]
    t = int(last["t"])
    day0 = t - t % 86400
    oh = int(g("open_hour_utc"))
    t_open = day0 + oh * 3600
    if t != t_open + (n_mv - 1) * sec:
        return C.hold(a, f"Nu este bara de decizie (închiderea primelor {n_mv} bare după {oh:02d}:00 UTC).")
    mv = sig_bars[-n_mv:]
    if int(mv[0]["t"]) != t_open:
        return C.hold(a, f"Barele de după deschiderea {oh:02d}:00 UTC lipsesc (gol de date).")
    move = float(last["c"]) - float(mv[0]["o"])
    span = max(b["h"] for b in mv) - min(b["l"] for b in mv)
    a["indicators"].update({"session_move": C.r_(move, digits + 1), "session_move_atr": C.r_(move / atr_v, 3),
                            "session_span": C.r_(span, digits + 1)})
    k_move = float(g("k_move"))
    if abs(move) < k_move * atr_v:
        return C.hold(a, f"Mișcarea primei ore {move / atr_v:+.2f}×ATR sub pragul {k_move:g}×ATR.")
    bf = float(g("min_body_frac"))
    if bf > 0 and (span <= 0 or abs(move) < bf * span):
        return C.hold(a, f"Mișcarea nu este curată (corp {abs(move) / span if span > 0 else 0:.2f} < {bf:g} din amplitudine).")
    side = "BUY" if move > 0 else "SELL"
    htf = cfg.get("htf_timeframe", "NONE")
    if bool(g("htf_filter")) and ((side == "BUY" and h["trend"] == "DOWN") or (side == "SELL" and h["trend"] == "UP")):
        return C.hold(a, f"Mișcare {side} contra trendului pe {htf} ({h['trend']}) — ziua este ignorată.")
    comps = [f"Mișcarea primelor {n_mv} bare după {oh:02d}:00 UTC: {move / atr_v:+.2f}×ATR (prag {k_move:g})",
             f"Trend pe {htf}: {h['trend']}"]
    sign = 1 if side == "BUY" else -1
    entry = float(quote["ask"]) if side == "BUY" else float(quote["bid"])
    sl_price = entry - sign * float(g("k_sl")) * atr_v
    tp_price = entry + sign * float(g("k_tp")) * atr_v
    return C.finish(a, symbol, side, sl_price, tp_price, quote, cfg, risk, ctx, comps, len(comps),
                    note=f"SL {float(g('k_sl')):g}×ATR, TP {float(g('k_tp')):g}×ATR")
