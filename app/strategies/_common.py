"""Shared plumbing for non-live plugins: same output shape, market blockers and context blockers as trend-v1.

Indicators are computed on a fixed tail of the closed bars passed in (SIG_TAIL / HTF_TAIL), so a decision depends
only on the last bars, never on how long a window the caller happens to pass (live 400 vs backtest 400 vs tests)."""
from __future__ import annotations

import time

from .. import indicators as ind
from ..config import TF_SECONDS, iso, spread_cap
from ..strategy import MIN_HTF_BARS, MIN_SIG_BARS, PROB_METHOD, context_blockers, probabilities

SIG_TAIL = 160
HTF_TAIL = 200


def r_(x, nd=6):
    return None if x is None else round(float(x), nd)


def p(cfg: dict, params: dict, key: str):
    v = cfg.get(key)
    return params[key] if v is None else v


def skeleton(symbol: str, sig_bars: list[dict], quote: dict | None, cfg: dict, ctx) -> dict:
    now_ts = ctx.now_ts if ctx.now_ts is not None else time.time()
    return {
        "symbol": symbol, "timeframe": cfg.get("signal_timeframe", "M5"), "time_utc": iso(now_ts),
        "bar_time": sig_bars[-1]["t"] if sig_bars else None,
        "direction": "HOLD", "score": 0.0,
        "probabilities": probabilities(0.0), "probability_method": PROB_METHOD,
        "reasons": [], "indicators": {}, "regime": "RANGE",
        "spread_points": quote.get("spread_points") if quote else None,
        "entry": None, "stop_loss": None, "take_profit": None, "rr": None, "candidate": None,
    }


def data_ok(a: dict, quote, sig_bars, htf_bars, cfg) -> bool:
    reasons = a["reasons"]
    if not quote or not quote.get("bid") or not quote.get("ask"):
        reasons.append("Nu există cotație bid/ask pentru simbol.")
        return False
    if len(sig_bars) < MIN_SIG_BARS:
        reasons.append(f"Date insuficiente: {len(sig_bars)}/{MIN_SIG_BARS} bare închise pe {a['timeframe']}.")
        return False
    if len(htf_bars) < MIN_HTF_BARS:
        reasons.append(f"Date insuficiente pe {cfg.get('htf_timeframe', 'H1')}: {len(htf_bars)}/{MIN_HTF_BARS} bare.")
        return False
    return True


def htf_state(htf_bars: list[dict]) -> dict:
    """HTF EMA50 slope over 3 bars vs 0.05*ATR -> UP/DOWN/FLAT (same rule as trend-v1)."""
    hb = htf_bars[-HTF_TAIL:]
    hc = [b["c"] for b in hb]
    h_e50 = ind.ema(hc, 50)
    h_atr = ind.last(ind.atr([b["h"] for b in hb], [b["l"] for b in hb], hc, 14)) or 0.0
    he_now, he_3 = ind.last(h_e50), ind.last(h_e50, 3)
    trend, slope = "FLAT", None
    if he_now is not None and he_3 is not None:
        slope = he_now - he_3
        thr = 0.05 * h_atr
        if slope > thr:
            trend = "UP"
        elif slope < -thr:
            trend = "DOWN"
    return {"trend": trend, "slope": slope, "atr": h_atr, "ema50": he_now, "close": hc[-1] if hc else None}


def sig_state(sig_bars: list[dict]) -> dict:
    sb = sig_bars[-SIG_TAIL:]
    closes = [b["c"] for b in sb]
    highs = [b["h"] for b in sb]
    lows = [b["l"] for b in sb]
    e20 = ind.ema(closes, 20)
    e50 = ind.ema(closes, 50)
    rsi_s = ind.rsi(closes, 14)
    atr_s = ind.atr(highs, lows, closes, 14)
    _, _, hist = ind.macd(closes, 12, 26, 9)
    return {"bars": sb, "closes": closes, "highs": highs, "lows": lows, "e20": e20, "e50": e50, "rsi": rsi_s,
            "atr_s": atr_s, "hist": hist, "close": closes[-1],
            "ema_fast": ind.last(e20), "ema_slow": ind.last(e50), "rsi_v": ind.last(rsi_s),
            "rsi_prev": ind.last(rsi_s, 1), "atr_v": ind.last(atr_s), "h_now": ind.last(hist)}


def base_indicators(s: dict, h: dict, digits: int) -> dict:
    return {"ema_fast": r_(s["ema_fast"], digits + 1), "ema_slow": r_(s["ema_slow"], digits + 1),
            "rsi": r_(s["rsi_v"], 2), "atr": r_(s["atr_v"], digits + 1), "macd_hist": r_(s["h_now"], digits + 2),
            "htf_trend": h["trend"]}


def regime(s: dict) -> str:
    atr_v, ef, es = s["atr_v"], s["ema_fast"], s["ema_slow"]
    atr_hist = [x for x in s["atr_s"][-100:] if x is not None]
    med = ind.median(atr_hist) or atr_v
    if med and atr_v > 2 * med:
        return "HIGH_VOL"
    if abs(ef - es) > 0.5 * atr_v:
        return "TREND_UP" if ef > es else "TREND_DOWN"
    return "RANGE"


def tf_seconds(cfg: dict) -> int:
    return TF_SECONDS.get(str(cfg.get("signal_timeframe", "M5")).upper(), 300)


def hold(a: dict, *lines: str) -> dict:
    a["reasons"].extend(lines)
    return a


def finish(a: dict, symbol: str, side: str, sl_price: float, tp_price: float | None, quote: dict, cfg: dict,
           risk: dict, ctx, comps: list[str], n_comps: int, *, tp_r: float | None = None,
           extra_blockers: list[str] | None = None, min_rr: float = 0.0, note: str = "") -> dict:
    """Plugin found a setup: apply the SL floor (min_sl_points), the trend-v1 market filters and context blockers.
    Stop is structural (sl_price); TP is either a price (tp_price) or tp_r × final SL distance (tp_r).
    When the floor widens a structural SL the price TP is kept (RR shrinks) and a tp_r TP scales with it."""
    digits = int(quote.get("digits") or 5)
    point = float(quote.get("point") or 10 ** -digits)
    bid, ask = float(quote["bid"]), float(quote["ask"])
    sign = 1 if side == "BUY" else -1
    entry = ask if side == "BUY" else bid
    raw_sl = sign * (entry - sl_price)
    floor = spread_cap(cfg.get("min_sl_points", 60), symbol, point) * point
    sl_dist = max(raw_sl, floor)
    tp_dist = sl_dist * float(tp_r) if tp_r is not None else sign * (float(tp_price) - entry)
    score = round(sign * len(comps) / float(n_comps), 3)
    a["score"] = score
    a["probabilities"] = probabilities(score)
    if tp_dist <= 0:
        return hold(a, f"Setup {side} fără spațiu până la TP (prețul a depășit deja ținta).",
                    *("(+) " + c for c in comps))
    sl = entry - sign * sl_dist
    tp = entry + sign * tp_dist
    atr_v = a["indicators"].get("atr") or 0.0

    blockers: list[str] = []
    spread_points = a["spread_points"]
    max_spread = spread_cap(risk.get("max_spread_points", 30), symbol, point)
    if spread_points is None:
        spread_points = round((ask - bid) / point)
    if bid > ask or spread_points < 0:
        blockers.append(f"Cotație inversată de la broker (bid {bid} > ask {ask}) — date invalide.")
    elif spread_points > max_spread:
        blockers.append(f"Spread {spread_points} puncte > maxim {max_spread:g}.")
    if atr_v <= 0:
        blockers.append("ATR este 0 (piață fără mișcare).")
    elif (ask - bid) > 0.2 * max(raw_sl, 0.0):
        blockers.append("Spread-ul depășește 20% din distanța până la SL.")
    max_age = float(risk.get("max_tick_age_seconds", 10))
    if ctx.tick_age is None or ctx.tick_age > max_age:
        blockers.append(f"Cotație veche (vârsta tick {ctx.tick_age if ctx.tick_age is None else round(ctx.tick_age, 1)} s > {max_age:g} s).")
    stops_level = float(quote.get("stops_level") or 0)
    min_dist = (stops_level + 5) * point
    if sl_dist < min_dist or tp_dist < min_dist:
        blockers.append(f"Distanța SL/TP sub nivelul minim al brokerului ({stops_level:g}+5 puncte).")
    if min_rr > 0 and tp_dist / sl_dist < min_rr:
        blockers.append(f"RR {tp_dist / sl_dist:.2f} sub minimul {min_rr:g}.")
    blockers.extend(extra_blockers or [])
    blockers.extend(context_blockers(symbol, side, ctx, risk))
    a["candidate"] = {"side": side, "entry": round(entry, digits), "stop_loss": round(sl, digits),
                      "take_profit": round(tp, digits), "score": score, "blockers": list(blockers)}
    if blockers:
        a["reasons"].append(f"Setup {side} detectat, dar blocat de filtre:")
        a["reasons"].extend(blockers)
        a["reasons"].extend("(+) " + c for c in comps)
        return a

    a["direction"] = side
    a["entry"] = round(entry, digits)
    a["stop_loss"] = round(sl, digits)
    a["take_profit"] = round(tp, digits)
    a["rr"] = round(tp_dist / sl_dist, 2) if sl_dist else None
    a["reasons"].extend(comps)
    floored = sl_dist > raw_sl + point / 2
    sl_note = (f"SL {sl_dist / point:.0f} puncte (minim impus; structural ar fi fost {raw_sl / point:.0f})"
               if floored else f"SL {sl_dist / point:.0f} puncte")
    a["reasons"].append(f"Regim: {a['regime']}; {sl_note}; TP {tp_dist / point:.0f} puncte "
                        f"(RR {tp_dist / sl_dist:.2f}){'; ' + note if note else ''}.")
    return a
