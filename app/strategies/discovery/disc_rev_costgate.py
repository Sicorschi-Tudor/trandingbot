"""disc-rev-costgate: trend-v1 with a RELATIVE, tight transaction-cost gate (review idea, research only).

Why: on gold the live gate blocks a setup only when spread > 20 % of the ATR stop (app/strategy.py). With a
standard-account spread of ~0.30 USD and a 1.5xATR(M15) stop of ~2 USD the typical trade pays 10-19 % of its risk at
the door and still passes; the TRAIN run with zero costs has ~0 edge, so the whole -0.19 R is cost. This plugin runs
trend-v1 unchanged (same cfg / risk / context, bit-identical candidate levels) and then adds ONE extra market blocker:
    (ask - bid) > max_spread_sl_frac x FINAL SL distance   (SL distance after the min_sl_points floor, i.e. the R unit)
max_spread_sl_frac = 1.0 disables the gate (pure trend-v1). The blocker is appended to candidate.blockers so the backtest
counts it as blocked_market and the live engine would show it under "blocat de filtre" like the other market filters.
Research only (discovery package): never offered to the live engine / UI."""
from __future__ import annotations

from ... import strategy
from .. import trend_v1

NAME = "disc-rev-costgate"
VERSION = "0.1"
DESCRIPTION_RO = ("Cercetare (review): trend-v1 identic, plus un filtru de cost relativ — setupul este blocat când "
                  "spread-ul depășește max_spread_sl_frac din distanța finală până la SL (implicit 6 %).")
PARAMS = {**trend_v1.PARAMS, "max_spread_sl_frac": 0.06}


def analyze(symbol, sig_bars, htf_bars, quote, cfg, risk, ctx) -> dict:
    a = strategy.analyze(symbol, sig_bars, htf_bars, quote, cfg, risk, ctx)
    cand = a.get("candidate")
    if not cand or not quote:
        return a
    frac = cfg.get("max_spread_sl_frac")
    frac = float(PARAMS["max_spread_sl_frac"] if frac is None else frac)
    sl_dist = abs(float(cand["entry"]) - float(cand["stop_loss"]))
    spread = float(quote["ask"]) - float(quote["bid"])
    if sl_dist <= 0 or spread <= frac * sl_dist:
        return a
    blocker = (f"Spread-ul ({spread:.2f}) depășește {frac:.0%} din distanța până la SL ({sl_dist:.2f}) "
               f"— filtru de cost relativ.")
    cand["blockers"].append(blocker)
    if a["direction"] != "HOLD":
        comps = list(a["reasons"])
        a["direction"] = "HOLD"
        a["entry"] = a["stop_loss"] = a["take_profit"] = a["rr"] = None
        a["reasons"] = [f"Setup {cand['side']} detectat, dar blocat de filtre:", blocker, *("(+) " + c for c in comps)]
    else:
        a["reasons"].append(blocker)
    return a
