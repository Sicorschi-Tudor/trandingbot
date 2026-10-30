"""smc-v1: entry step = the CLUB 81 five-step SMC check (app/smc.py) on the signal timeframe (M15 in profile gold_smc)
with its two higher timeframes (H1 + H4, passed by the engine in ctx.htf_extra).

Only the ENTRY step changes (owner's rule): a BUY/SELL is proposed on the verdict ENTER with the price already inside the
order block (entry at market; the bot places no limit orders). Stop and target stay those of the scalp system:
SL = max(atr_sl_mult x ATR(14) on M1 (ctx.sl_bars), min_sl_points), TP = scalp_target_usd on the fixed lot, then the
profit ladder. Without M1 bars the SMC structural stop is the fallback; with the ladder off the TP is the SMC 2R. Hard blockers: no quote, stale tick, crossed quote,
spread above the maximum, broker stops level, SMC market blocks (news spike, dead market, rollover) and the bot-state
context blockers (one position at a time).
Measured on 2 years of M15 gold (scratch backtest, spread 0.47, market entries only): SMC direction beat its mirror
(-0.10 vs -0.95 USD/trade with SL/TP 1:2) but with the +1 -> +10 ladder exit it lost about -1 USD per trade."""
from __future__ import annotations

from .. import indicators as ind
from .. import smc
from ..strategy import context_blockers, probabilities
from . import _common as C
from ..config import spread_cap

NAME = "smc-v1"
VERSION = "1.0"
DESCRIPTION_RO = ("Analiza SMC în 5 pași (CLUB 81): trend H1/H4, lichiditate ridicată, ruperea structurii, revenire în "
                  "order block, spațiu până la țintă; intrare la verdictul ENTER cu prețul în zonă, SL structural.")
PARAMS = {"scalp_target_usd": 1.0, "atr_sl_mult": 3.0, "min_sl_points": 300}


def analyze(symbol, sig_bars, htf_bars, quote, cfg, risk, ctx) -> dict:
    a = C.skeleton(symbol, sig_bars, quote, cfg, ctx)
    if not C.data_ok(a, quote, sig_bars, htf_bars, cfg):
        return a
    tf = str(cfg.get("signal_timeframe", "M15")).upper()
    digits = int(quote.get("digits") or 5)
    point = float(quote.get("point") or 10 ** -digits)
    bid, ask = float(quote["bid"]), float(quote["ask"])
    htf = getattr(ctx, "htf_extra", None) or {}
    s = smc.analyze(sig_bars[-260:], htf, tf, ask - bid, ctx.now_ts)
    a["smc"] = s
    a["indicators"] = {"atr": s.get("atr"), "atr_ratio": s.get("atr_ratio"),
                       "htf_trend": ",".join(f"{k}:{v:+d}" for k, v in (s.get("htf_votes") or {}).items())}
    d = int(s.get("direction") or 0)
    a["score"] = round(d * s.get("score", 0) / 95.0, 3)
    a["probabilities"] = probabilities(a["score"])
    a["probability_method"] = f"SMC: {s.get('title')} (scor {s.get('score')}/95, euristic, NEcalibrat)"
    a["regime"] = "TREND_UP" if d == 1 else "TREND_DOWN" if d == -1 else "RANGE"
    steps_txt = [f"{x['name']}: {'✓' if x['status'] == 'pass' else '½' if x['status'] == 'partial' else '✗'} — {x['text']}"
                 for x in s.get("steps") or []]
    L = s.get("levels") or {}
    if s.get("verdict") != "ENTER" or not d:
        return C.hold(a, f"SMC: {s.get('title')}", *steps_txt)
    if L.get("etype") != "market":
        return C.hold(a, f"SMC: {s.get('title')} — prețul nu e încă în zonă (aștept revenirea la {L.get('entry')}; "
                         "botul nu pune ordine limită).", *steps_txt)

    side = "BUY" if d == 1 else "SELL"
    entry = ask if side == "BUY" else bid
    sl_dist = abs(float(L["entry"]) - float(L["sl"]))
    m1 = getattr(ctx, "sl_bars", None) or []
    atr_m1 = ind.last(ind.atr([b["h"] for b in m1], [b["l"] for b in m1], [b["c"] for b in m1], 14)) if len(m1) >= 20 else None
    if atr_m1:
        sl_dist = max(float(C.p(cfg, PARAMS, "atr_sl_mult")) * atr_m1, spread_cap(C.p(cfg, PARAMS, "min_sl_points"), symbol, point) * point)
        sl_note = f"SL 3×ATR(M1) = {sl_dist:.2f} USD de preț"
    else:
        sl_note = f"SL structural SMC {sl_dist:.2f} USD de preț (fără bare M1)"
    if cfg.get("ladder_enabled"):
        vmin = float(quote.get("volume_min") or 0.01)
        volume = float(risk.get("fixed_volume_lots") or 0) or vmin
        contract = float(quote.get("contract_size") or 0)
        tick_size, tick_value = float(quote.get("tick_size") or point), float(quote.get("tick_value") or 0)
        per_price = contract * volume if contract > 0 else (tick_value / tick_size * volume if tick_value > 0 else 0.0)
        target = float(C.p(cfg, PARAMS, "scalp_target_usd"))
        if per_price <= 0 or target <= 0:
            return C.hold(a, "contract_size/tick_value indisponibil — nu pot calcula TP-ul în bani.")
        tp_dist = target / per_price
        tp_note = f"TP inițial +{target:g} USD, apoi scara de profit"
    else:
        tp_dist = 2 * sl_dist
        tp_note = "TP 1:2"

    blockers = list(s.get("blocks") or [])
    spread_points = a["spread_points"] if a["spread_points"] is not None else round((ask - bid) / point)
    max_spread = spread_cap(risk.get("max_spread_points", 30), symbol, point)
    if bid > ask:
        blockers.append(f"Cotație inversată de la broker (bid {bid} > ask {ask}) — date invalide.")
    elif spread_points > max_spread:
        blockers.append(f"Spread {spread_points} puncte > maxim {max_spread:g}.")
    max_age = float(risk.get("max_tick_age_seconds", 10))
    if ctx.tick_age is None or ctx.tick_age > max_age:
        blockers.append(f"Cotație veche (vârsta tick {ctx.tick_age if ctx.tick_age is None else round(ctx.tick_age, 1)} s > {max_age:g} s).")
    min_dist = (float(quote.get("stops_level") or 0) + 5) * point
    tp_dist, sl_dist = max(tp_dist, min_dist), max(sl_dist, min_dist)
    blockers.extend(context_blockers(symbol, side, ctx, risk))
    sign = 1 if side == "BUY" else -1
    sl, tp = entry - sign * sl_dist, entry + sign * tp_dist
    a["candidate"] = {"side": side, "entry": round(entry, digits), "stop_loss": round(sl, digits),
                      "take_profit": round(tp, digits), "score": a["score"], "blockers": list(blockers)}
    if blockers:
        a["reasons"].append(f"SMC {side} blocat:")
        a["reasons"].extend(blockers)
        a["reasons"].extend(steps_txt)
        return a
    a["direction"] = side
    a["entry"], a["stop_loss"], a["take_profit"] = round(entry, digits), round(sl, digits), round(tp, digits)
    a["rr"] = round(tp_dist / sl_dist, 2)
    a["reasons"].append(f"SMC: {s.get('title')} — {sl_note}; {tp_note}; "
                        f"spread {spread_points} puncte; durată orientativă {s.get('hold_txt')}.")
    a["reasons"].extend(steps_txt)
    return a
