"""scalp-v1: continuous scalping for a fixed money target (owner's rule, profile gold_scalp).

On every closed bar of the signal timeframe (M1 in the profile) with no bot position on the symbol it picks a side by
entry_rule: "rsi_extreme" (default) fades RSI(14) below rsi_low / above rsi_high and waits otherwise; "momentum" always
trades the short-term momentum (EMA fast vs EMA slow; within 0.05×ATR it follows the last closed bar). Measured on 105
days of M1 gold with the profit ladder (scratch backtest, spread 0.47 USD): no direction rule beat a coin flip
reliably; RSI extreme was the only one near zero in both halves (+0.04 / -0.02 USD per trade) vs momentum -0.16. SL = max(atr_sl_mult × ATR(14), min_sl_points). TP = the price distance that makes
scalp_target_usd on the fixed lot (fixed_volume_lots, else the broker's minimum lot); the position manager also closes
at that net profit. Only hard data/market blockers apply (no quote, stale tick, crossed quote, spread above the
maximum, broker stops level) plus the bot-state context blockers (one position at a time).
NOT measured on history: the spread is paid on every trade and the target is a fraction of the stop."""
from __future__ import annotations

from .. import indicators as ind
from ..strategy import context_blockers, probabilities
from . import _common as C
from ..config import spread_cap

NAME = "scalp-v1"
VERSION = "1.0"
DESCRIPTION_RO = ("Scalping continuu: o poziție odată, intrare la RSI extrem (sub 30 cumpără, peste 70 vinde), "
                  "TP inițial +scalp_target_usd, apoi scara de profit mută TP/SL în sus până la plafon.")
PARAMS = {"entry_rule": "rsi_extreme", "rsi_low": 30.0, "rsi_high": 70.0, "ema_fast": 9, "ema_slow": 21,
          "atr_sl_mult": 3.0, "min_sl_points": 300, "scalp_target_usd": 1.0}


def analyze(symbol, sig_bars, htf_bars, quote, cfg, risk, ctx) -> dict:
    a = C.skeleton(symbol, sig_bars, quote, cfg, ctx)
    if not C.data_ok(a, quote, sig_bars, htf_bars, cfg):
        return a
    g = lambda k: C.p(cfg, PARAMS, k)  # noqa: E731
    digits = int(quote.get("digits") or 5)
    point = float(quote.get("point") or 10 ** -digits)
    s = C.sig_state(sig_bars)
    h = C.htf_state(htf_bars)
    closes = s["closes"]
    fast = ind.last(ind.ema(closes, int(g("ema_fast"))))
    slow = ind.last(ind.ema(closes, int(g("ema_slow"))))
    atr_v = s["atr_v"]
    a["indicators"] = C.base_indicators(s, h, digits)
    a["indicators"].update({"scalp_ema_fast": C.r_(fast, digits + 1), "scalp_ema_slow": C.r_(slow, digits + 1)})
    if None in (fast, slow, atr_v) or atr_v <= 0:
        return C.hold(a, "Indicatori incompleți (date insuficiente).")
    a["regime"] = C.regime(s)

    bar = s["bars"][-1]
    rsi_v = s["rsi_v"]
    if str(g("entry_rule")) == "rsi_extreme":
        lo, hi = float(g("rsi_low")), float(g("rsi_high"))
        if rsi_v is None or lo <= rsi_v <= hi:
            return C.hold(a, f"Aștept RSI extrem: RSI {rsi_v if rsi_v is None else round(rsi_v, 1)} între {lo:g} și {hi:g}.")
        side = "BUY" if rsi_v < lo else "SELL"
        why = (f"RSI {rsi_v:.1f} {'sub' if side == 'BUY' else 'peste'} {lo if side == 'BUY' else hi:g} — "
               f"{'supravânzare, cumpăr revenirea' if side == 'BUY' else 'supracumpărare, vând revenirea'}")
    elif abs(fast - slow) > 0.05 * atr_v:
        side = "BUY" if fast > slow else "SELL"
        why = f"Impuls {'în sus' if side == 'BUY' else 'în jos'}: EMA{int(g('ema_fast'))} {fast:.{digits}f} " \
              f"{'>' if side == 'BUY' else '<'} EMA{int(g('ema_slow'))} {slow:.{digits}f}"
    else:
        side = "BUY" if bar["c"] >= bar["o"] else "SELL"
        why = f"EMA-uri plate — urmez ultima bară ({'verde' if side == 'BUY' else 'roșie'})"

    bid, ask = float(quote["bid"]), float(quote["ask"])
    sign = 1 if side == "BUY" else -1
    entry = ask if side == "BUY" else bid
    sl_dist = max(float(g("atr_sl_mult")) * atr_v, spread_cap(g("min_sl_points"), symbol, point) * point)

    tick_size = float(quote.get("tick_size") or point)
    tick_value = float(quote.get("tick_value") or 0)
    vmin = float(quote.get("volume_min") or 0.01)
    volume = float(risk.get("fixed_volume_lots") or 0) or vmin
    target = float(g("scalp_target_usd"))
    # money per 1.0 of price on this lot: contract size first (some brokers report a tick_value that does not match
    # the real P/L, e.g. MetaQuotes-Demo gold), tick_value / tick_size otherwise
    contract = float(quote.get("contract_size") or 0)
    per_price = contract * volume if contract > 0 else (tick_value / tick_size * volume if tick_value > 0 else 0.0)
    if per_price <= 0 or target <= 0:
        return C.hold(a, "contract_size/tick_value indisponibil sau țintă de profit 0 — nu pot calcula TP-ul în bani.")
    tp_dist = target / per_price

    blockers: list[str] = []
    spread_points = a["spread_points"]
    if spread_points is None:
        spread_points = round((ask - bid) / point)
    max_spread = spread_cap(risk.get("max_spread_points", 30), symbol, point)
    if bid > ask:
        blockers.append(f"Cotație inversată de la broker (bid {bid} > ask {ask}) — date invalide.")
    elif spread_points > max_spread:
        blockers.append(f"Spread {spread_points} puncte > maxim {max_spread:g}.")
    max_age = float(risk.get("max_tick_age_seconds", 10))
    if ctx.tick_age is None or ctx.tick_age > max_age:
        blockers.append(f"Cotație veche (vârsta tick {ctx.tick_age if ctx.tick_age is None else round(ctx.tick_age, 1)} s > {max_age:g} s).")
    stops_level = float(quote.get("stops_level") or 0)
    min_dist = (stops_level + 5) * point
    if tp_dist < min_dist:
        tp_dist = min_dist  # broker minimum: the position manager still closes at the money target
    if sl_dist < min_dist:
        sl_dist = min_dist
    blockers.extend(context_blockers(symbol, side, ctx, risk))

    sl, tp = entry - sign * sl_dist, entry + sign * tp_dist
    score = float(sign)
    a["score"] = score
    a["probabilities"] = probabilities(score)
    a["candidate"] = {"side": side, "entry": round(entry, digits), "stop_loss": round(sl, digits),
                      "take_profit": round(tp, digits), "score": score, "blockers": list(blockers)}
    if blockers:
        a["reasons"].append(f"Scalp {side} blocat:")
        a["reasons"].extend(blockers)
        return a
    a["direction"] = side
    a["entry"], a["stop_loss"], a["take_profit"] = round(entry, digits), round(sl, digits), round(tp, digits)
    a["rr"] = round(tp_dist / sl_dist, 2)
    a["reasons"].extend([why, f"Țintă +{target:g} USD pe {volume:g} lot = {tp_dist / point:.0f} puncte; "
                              f"SL {sl_dist / point:.0f} puncte; spread {spread_points} puncte."])
    return a
