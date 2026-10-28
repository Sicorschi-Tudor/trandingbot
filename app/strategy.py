"""Deterministic signal engine trend-v1 (CONTRACT §4)."""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from . import indicators as ind
from .config import STRATEGY_VERSION, iso
from .risk import currency_conflict

PROB_METHOD = "heuristic-v1 (NOT calibrated)"
MIN_SIG_BARS = 100
MIN_HTF_BARS = 60


@dataclass
class AnalysisContext:
    session_open: bool = True
    has_bot_position: bool = False
    signals_this_session: int = 0
    tick_age: float | None = 0.0
    now_ts: float | None = None
    open_exposures: list = field(default_factory=list)
    corr_limit: int = 1
    news_block: str | None = None  # economic-calendar blackout reason (blocks before any Claude call)


def probabilities(score: float) -> dict:
    p_dir = 0.5 + 0.25 * abs(score)
    p_opp = (1 - p_dir) * 0.6
    neutral = 1 - p_dir - p_opp
    if score > 0:
        up, down = p_dir, p_opp
    elif score < 0:
        up, down = p_opp, p_dir
    else:
        up = down = round((p_dir + p_opp) / 2, 4)
    return {"up": round(up, 4), "down": round(down, 4), "neutral": round(neutral, 4)}


def _r(x, nd=6):
    return None if x is None else round(float(x), nd)


def context_blockers(symbol: str, side: str, ctx: AnalysisContext, risk: dict) -> list[str]:
    """Blockers that depend on the bot's state (session, book, exposures, news, signal count), not on the market.
    Shared with the backtest so simulated decisions use exactly the live rules."""
    out: list[str] = []
    if not ctx.session_open:
        out.append("Sesiunea de tranzacționare este închisă.")
    if ctx.has_bot_position:
        out.append("Există deja o poziție a botului pe acest simbol.")
    conflict = currency_conflict(symbol, side, ctx.open_exposures, ctx.corr_limit)
    if conflict:
        out.append(f"Expunere corelată: {conflict} (max {ctx.corr_limit} pe monedă și direcție).")
    if ctx.news_block:
        out.append(ctx.news_block + ".")
    max_sig = int(risk.get("max_signals_per_symbol_per_session", 5))
    if ctx.signals_this_session >= max_sig:
        out.append(f"Limita de semnale pe sesiune atinsă ({ctx.signals_this_session}/{max_sig}).")
    return out


def analyze(symbol: str, sig_bars: list[dict], htf_bars: list[dict], quote: dict | None,
            cfg: dict, risk: dict, ctx: AnalysisContext) -> dict:
    """sig_bars/htf_bars: CLOSED bars only (keys t,o,h,l,c). cfg = strategy settings, risk = risk settings."""
    now_ts = ctx.now_ts if ctx.now_ts is not None else time.time()
    tf = cfg.get("signal_timeframe", "M5")
    spread_points = quote.get("spread_points") if quote else None
    a = {
        "symbol": symbol, "timeframe": tf, "time_utc": iso(now_ts),
        "bar_time": sig_bars[-1]["t"] if sig_bars else None,
        "direction": "HOLD", "score": 0.0,
        "probabilities": probabilities(0.0), "probability_method": PROB_METHOD,
        "reasons": [], "indicators": {}, "regime": "RANGE",
        "spread_points": spread_points, "entry": None, "stop_loss": None, "take_profit": None, "rr": None,
        # setup levels even when filters block it (shadow labels / backtest); None when there is no setup
        "candidate": None,
    }
    reasons: list[str] = a["reasons"]
    if not quote or not quote.get("bid") or not quote.get("ask"):
        reasons.append("Nu există cotație bid/ask pentru simbol.")
        return a
    if len(sig_bars) < MIN_SIG_BARS:
        reasons.append(f"Date insuficiente: {len(sig_bars)}/{MIN_SIG_BARS} bare închise pe {tf}.")
        return a
    if len(htf_bars) < MIN_HTF_BARS:
        reasons.append(f"Date insuficiente pe {cfg.get('htf_timeframe', 'H1')}: {len(htf_bars)}/{MIN_HTF_BARS} bare.")
        return a

    digits = int(quote.get("digits") or 5)
    point = float(quote.get("point") or 10 ** -digits)
    closes = [b["c"] for b in sig_bars]
    highs = [b["h"] for b in sig_bars]
    lows = [b["l"] for b in sig_bars]
    e20 = ind.ema(closes, 20)
    e50 = ind.ema(closes, 50)
    rsi_s = ind.rsi(closes, 14)
    atr_s = ind.atr(highs, lows, closes, 14)
    _, _, hist = ind.macd(closes, 12, 26, 9)
    ema_fast, ema_slow = ind.last(e20), ind.last(e50)
    rsi_v, atr_v = ind.last(rsi_s), ind.last(atr_s)
    h_now, h_prev = ind.last(hist), ind.last(hist, 1)
    close = closes[-1]

    hc = [b["c"] for b in htf_bars]
    h_e50 = ind.ema(hc, 50)
    h_atr = ind.last(ind.atr([b["h"] for b in htf_bars], [b["l"] for b in htf_bars], hc, 14)) or 0.0
    he_now, he_3 = ind.last(h_e50), ind.last(h_e50, 3)
    htf_trend = "FLAT"
    if he_now is not None and he_3 is not None:
        slope = he_now - he_3
        thr = 0.05 * h_atr
        if slope > thr:
            htf_trend = "UP"
        elif slope < -thr:
            htf_trend = "DOWN"

    a["indicators"] = {
        "ema_fast": _r(ema_fast, digits + 1), "ema_slow": _r(ema_slow, digits + 1),
        "rsi": _r(rsi_v, 2), "atr": _r(atr_v, digits + 1), "macd_hist": _r(h_now, digits + 2),
        "htf_trend": htf_trend,
    }
    if None in (ema_fast, ema_slow, rsi_v, atr_v, h_now, h_prev):
        reasons.append("Indicatori incompleți (date insuficiente).")
        return a

    long_c, short_c = [], []
    if ema_fast > ema_slow:
        long_c.append("EMA20 peste EMA50 (trend ascendent pe termen scurt)")
    elif ema_fast < ema_slow:
        short_c.append("EMA20 sub EMA50 (trend descendent pe termen scurt)")
    if close > ema_fast:
        long_c.append("Închiderea peste EMA20")
    elif close < ema_fast:
        short_c.append("Închiderea sub EMA20")
    if h_now > 0 and h_now > h_prev:
        long_c.append("Histograma MACD pozitivă și în creștere")
    elif h_now < 0 and h_now < h_prev:
        short_c.append("Histograma MACD negativă și în scădere")
    if 50 < rsi_v < 70:
        long_c.append(f"RSI {rsi_v:.1f} în zona 50–70 (impuls fără supracumpărare)")
    elif 30 < rsi_v < 50:
        short_c.append(f"RSI {rsi_v:.1f} în zona 30–50 (impuls fără supravânzare)")
    if htf_trend == "UP":
        long_c.append(f"Trend ascendent pe {cfg.get('htf_timeframe', 'H1')} (pantă EMA50)")
    elif htf_trend == "DOWN":
        short_c.append(f"Trend descendent pe {cfg.get('htf_timeframe', 'H1')} (pantă EMA50)")

    score = (len(long_c) - len(short_c)) / 5.0
    a["score"] = round(score, 3)
    a["probabilities"] = probabilities(score)

    # regime
    atr_hist = [x for x in atr_s[-100:] if x is not None]
    med = ind.median(atr_hist) or atr_v
    if med and atr_v > 2 * med:
        a["regime"] = "HIGH_VOL"
    elif abs(ema_fast - ema_slow) > 0.5 * atr_v:
        a["regime"] = "TREND_UP" if ema_fast > ema_slow else "TREND_DOWN"
    else:
        a["regime"] = "RANGE"

    min_score = int(cfg.get("min_score", 4))
    candidate = "HOLD"
    if len(long_c) >= min_score and htf_trend != "DOWN":
        candidate = "BUY"
    elif len(short_c) >= min_score and htf_trend != "UP":
        candidate = "SELL"

    if candidate == "HOLD":
        if len(long_c) >= min_score and htf_trend == "DOWN":
            reasons.append(f"Setup BUY ({len(long_c)}/5) blocat: trendul pe {cfg.get('htf_timeframe', 'H1')} este descendent.")
        elif len(short_c) >= min_score and htf_trend == "UP":
            reasons.append(f"Setup SELL ({len(short_c)}/5) blocat: trendul pe {cfg.get('htf_timeframe', 'H1')} este ascendent.")
        else:
            reasons.append(f"Scor insuficient: long {len(long_c)}/5, short {len(short_c)}/5 (minim {min_score}).")
        for c in long_c:
            reasons.append("(+) " + c)
        for c in short_c:
            reasons.append("(−) " + c)
        return a

    comps = long_c if candidate == "BUY" else short_c
    bid, ask = float(quote["bid"]), float(quote["ask"])
    entry = ask if candidate == "BUY" else bid
    sl_mult, tp_mult = float(cfg.get("atr_sl_mult", 1.5)), float(cfg.get("atr_tp_mult", 2.5))
    # tiny M1 ATRs give stops inside the noise and oversized lots: floor the stop, keep the RR
    atr_sl_dist = sl_mult * atr_v
    sl_dist = max(atr_sl_dist, float(cfg.get("min_sl_points", 60)) * point)
    tp_dist = sl_dist * tp_mult / sl_mult
    sign = 1 if candidate == "BUY" else -1
    sl = entry - sign * sl_dist
    tp = entry + sign * tp_dist

    blockers: list[str] = []
    max_spread = float(risk.get("max_spread_points", 30))
    if spread_points is None:
        spread_points = round((ask - bid) / point)
    if bid > ask or spread_points < 0:
        blockers.append(f"Cotație inversată de la broker (bid {bid} > ask {ask}) — date invalide.")
    elif spread_points > max_spread:
        blockers.append(f"Spread {spread_points} puncte > maxim {max_spread:g}.")
    if atr_v <= 0:
        blockers.append("ATR este 0 (piață fără mișcare).")
    elif (ask - bid) > 0.2 * atr_sl_dist:
        blockers.append("Spread-ul depășește 20% din distanța până la SL.")
    max_age = float(risk.get("max_tick_age_seconds", 10))
    if ctx.tick_age is None or ctx.tick_age > max_age:
        blockers.append(f"Cotație veche (vârsta tick {ctx.tick_age if ctx.tick_age is None else round(ctx.tick_age, 1)} s > {max_age:g} s).")
    stops_level = float(quote.get("stops_level") or 0)
    min_dist = (stops_level + 5) * point
    if sl_dist < min_dist or tp_dist < min_dist:
        blockers.append(f"Distanța SL/TP sub nivelul minim al brokerului ({stops_level:g}+5 puncte).")
    blockers.extend(context_blockers(symbol, candidate, ctx, risk))
    a["candidate"] = {"side": candidate, "entry": round(entry, digits), "stop_loss": round(sl, digits),
                      "take_profit": round(tp, digits), "score": a["score"], "blockers": list(blockers)}

    if blockers:
        reasons.append(f"Setup {candidate} detectat, dar blocat de filtre:")
        reasons.extend(blockers)
        reasons.extend("(+) " + c for c in comps)
        return a

    a["direction"] = candidate
    a["entry"] = round(entry, digits)
    a["stop_loss"] = round(sl, digits)
    a["take_profit"] = round(tp, digits)
    a["rr"] = round(tp_dist / sl_dist, 2) if sl_dist else None
    reasons.extend(comps)
    floored = sl_dist > atr_sl_dist + point / 2
    sl_note = (f"SL {sl_dist / point:.0f} puncte (minim impus; {sl_mult:g}×ATR ar fi fost {atr_sl_dist / point:.0f})"
               if floored else f"SL {sl_dist / point:.0f} puncte ({sl_mult:g}×ATR)")
    reasons.append(f"Regim: {a['regime']}; ATR {atr_v / point:.1f} puncte ({atr_v:.{digits + 1}f}); {sl_note}; "
                   f"TP {tp_dist / point:.0f} puncte (RR {tp_dist / sl_dist:.2f}).")
    return a


def invalidation_text(a: dict, expires_local: str) -> str:
    d = a["direction"]
    ema_slow = (a.get("indicators") or {}).get("ema_slow")
    if d == "BUY":
        return (f"Invalid dacă prețul atinge SL {a['stop_loss']} sau o lumânare {a['timeframe']} se închide sub EMA50 "
                f"({ema_slow}); expiră la {expires_local}.")
    return (f"Invalid dacă prețul atinge SL {a['stop_loss']} sau o lumânare {a['timeframe']} se închide peste EMA50 "
            f"({ema_slow}); expiră la {expires_local}.")


def dedup_key(symbol: str, timeframe: str, bar_time: int, direction: str) -> str:
    return f"{symbol}|{timeframe}|{bar_time}|{direction}"


__all__ = ["analyze", "context_blockers", "AnalysisContext", "probabilities", "dedup_key", "invalidation_text", "STRATEGY_VERSION"]
