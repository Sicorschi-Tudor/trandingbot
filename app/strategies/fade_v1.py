"""fade-v1: exact MIRROR of trend-v1 (forward-test hypothesis after Stage C2; pre-registered, not tuned).

Definition: trend-v1 (app.strategy.analyze, same live params: min_score, atr_sl_mult, atr_tp_mult, min_sl_points;
signal TF H1, HTF H4) is run with a NEUTRAL context, so its candidate carries only its own market filters (spread,
ATR, spread vs SL distance, tick age, broker stops level). Whenever it has a BUY/SELL candidate, fade-v1 proposes the
OPPOSITE side on the same quote (BUY at ask, SELL at bid — the same reference quote/bar as trend-v1), with
SL distance = trend-v1's TP distance and TP distance = trend-v1's SL distance. The market blockers of trend-v1 are
propagated unchanged; the context blockers (session, one position per symbol, correlation max 1 per
currency+direction, signal count) are evaluated for fade-v1's OWN side and book (the ctx passed in). NO news
blackout: it is not part of the pre-registered definition and the STAGE_C2 backtest that motivates it has none, so any
ctx.news_block is ignored.
The candidate is present even when blocked (same output shape as trend-v1).

Exit (pre-registered): no break-even, no trailing, time stop 240 minutes (PARAMS time_stop_minutes /
break_even_at_r, RUN_DEFAULTS + default_exit() for the backtest). No Claude review. Research/forward plugin only:
never in names() (live engine / UI list), so it cannot drive a live DEMO decision."""
from __future__ import annotations

import dataclasses

from .. import strategy
from . import trend_v1

NAME = "fade-v1"
VERSION = "1.0"
DESCRIPTION_RO = ("Oglinda exactă a trend-v1 (test forward pe hârtie): când trend-v1 are un setup BUY/SELL care "
                  "trece filtrele de piață, fade-v1 intră în sens OPUS, cu SL la distanța TP-ului trend-v1 și TP la "
                  "distanța SL-ului trend-v1; fără break-even, fără trailing, închidere după 240 de minute.")
PARAMS = {**trend_v1.PARAMS, "time_stop_minutes": 240, "break_even_at_r": 0}
# backtest-only (app.backtest.resolve applies them over the live settings unless the run sets the key itself)
RUN_DEFAULTS = {"signal_timeframe": "H1", "htf_timeframe": "H4", "max_hold_minutes": 240, "break_even_at_r": 0,
                "trailing_enabled": False}
OPPOSITE = {"BUY": "SELL", "SELL": "BUY"}


def default_exit(cfg: dict) -> dict:
    """base exit (SL/TP only) with the 240-minute time stop; break-even off via break_even_at_r = 0."""
    return {"mode": "base", "params": {"max_hold_minutes": int(PARAMS["time_stop_minutes"])}}


def _market_ctx(ctx) -> strategy.AnalysisContext:
    """Neutral context: keeps only what trend-v1 treats as a market filter (tick age) + the clock."""
    return strategy.AnalysisContext(session_open=True, has_bot_position=False, signals_this_session=0,
                                    tick_age=getattr(ctx, "tick_age", 0.0), now_ts=getattr(ctx, "now_ts", None),
                                    open_exposures=[], corr_limit=int(getattr(ctx, "corr_limit", 1) or 1),
                                    news_block=None)


def mirror(cand: dict, quote: dict) -> tuple[str, float, float, float, float, float]:
    """(side, entry, sl, tp, sl_dist, tp_dist) of the fade trade for a trend-v1 candidate."""
    digits = int(quote.get("digits") or 5)
    t_entry, t_sl, t_tp = float(cand["entry"]), float(cand["stop_loss"]), float(cand["take_profit"])
    t_sl_dist, t_tp_dist = abs(t_entry - t_sl), abs(t_tp - t_entry)
    side = OPPOSITE[cand["side"]]
    entry = float(quote["ask"]) if side == "BUY" else float(quote["bid"])
    sign = 1 if side == "BUY" else -1
    sl_dist, tp_dist = t_tp_dist, t_sl_dist
    return (side, round(entry, digits), round(entry - sign * sl_dist, digits), round(entry + sign * tp_dist, digits),
            sl_dist, tp_dist)


def analyze(symbol, sig_bars, htf_bars, quote, cfg, risk, ctx) -> dict:
    base = strategy.analyze(symbol, sig_bars, htf_bars, quote, cfg, risk, _market_ctx(ctx))
    cand = base.get("candidate")
    a = dict(base)
    a["reasons"] = []
    if not cand:
        a["reasons"] = ["Fără setup trend-v1 de oglindit."] + list(base["reasons"])
        return a
    t_side = cand["side"]
    side, entry, sl, tp, sl_dist, tp_dist = mirror(cand, quote)
    score = round(-float(base["score"]), 3)
    a["score"] = score
    a["probabilities"] = strategy.probabilities(score)
    a["direction"] = "HOLD"
    a["entry"] = a["stop_loss"] = a["take_profit"] = a["rr"] = None
    blockers = list(cand.get("blockers") or [])  # trend-v1 market filters (neutral context -> market only)
    own_ctx = dataclasses.replace(ctx, news_block=None) if ctx is not None else strategy.AnalysisContext()
    blockers.extend(strategy.context_blockers(symbol, side, own_ctx, risk))  # fade-v1's own book (no news)
    a["candidate"] = {"side": side, "entry": entry, "stop_loss": sl, "take_profit": tp, "score": score,
                      "blockers": list(blockers)}
    head = (f"Oglinda trend-v1: trend-v1 propune {t_side} (SL {cand['stop_loss']}, TP {cand['take_profit']}); "
            f"fade-v1 intră {side} cu SL la distanța TP-ului și TP la distanța SL-ului.")
    trend_lines = ["trend-v1: " + r for r in base["reasons"]]
    if blockers:
        a["reasons"] = [head, f"Setup {side} (fade-v1) detectat, dar blocat de filtre:", *blockers, *trend_lines]
        return a
    a["direction"] = side
    a["entry"], a["stop_loss"], a["take_profit"] = entry, sl, tp
    a["rr"] = round(tp_dist / sl_dist, 2) if sl_dist else None
    point = float(quote.get("point") or 10 ** -int(quote.get("digits") or 5))
    a["reasons"] = [head, f"SL {sl_dist / point:.0f} puncte, TP {tp_dist / point:.0f} puncte (RR {a['rr']}); "
                          f"fără break-even, fără trailing, închidere după {int(PARAMS['time_stop_minutes'])} de minute.",
                    *trend_lines]
    return a
