"""disc-rev-h1range: trend-v1 plus the deterministic half of the 'AI reviewer VETO' review idea (research only).

Review idea: the Claude reviewer should VETO a long entered in the top 10 % of the H1 20-bar range (short: bottom
10 %), i.e. 'cumpărare în zonă de supracumpărare spre rezistență'. The Claude part of the idea can only be measured in
live shadow (engine.shadow_stats by_verdict); this plugin measures the RULE itself on history. It runs trend-v1
unchanged (same cfg / risk / context, bit-identical candidate levels) and appends ONE extra blocker when
    pos = (entry - min(low[-n:])) / (max(high[-n:]) - min(low[-n:]))   on the closed HTF (H1) bars
is > 1 - edge_frac for a BUY or < edge_frac for a SELL. invert=True blocks the complement instead (entry NOT in the
extreme band), as a diagnostic of which side of the range carries the information. edge_frac = 0 disables the gate
(pure trend-v1, used as the matching baseline). Research only (discovery package): never offered to the live engine/UI."""
from __future__ import annotations

from ... import strategy
from .. import trend_v1

NAME = "disc-rev-h1range"
VERSION = "0.1"
DESCRIPTION_RO = ("Cercetare (review): trend-v1 identic, plus blocarea intrărilor contra-extremă — BUY în partea de sus "
                  "(edge_frac) a intervalului ultimelor h1_range_n bare HTF, SELL în partea de jos.")
PARAMS = {**trend_v1.PARAMS, "h1_range_n": 20, "edge_frac": 0.10, "invert": False}


def _param(cfg, key):
    v = cfg.get(key)
    return PARAMS[key] if v is None else v


def range_position(htf_bars, n: int, price: float):
    bars = [b for b in (htf_bars or [])[-int(n):] if b.get("h") is not None and b.get("l") is not None]
    if len(bars) < int(n):
        return None
    hi = max(float(b["h"]) for b in bars)
    lo = min(float(b["l"]) for b in bars)
    if hi <= lo:
        return None
    return (price - lo) / (hi - lo)


def analyze(symbol, sig_bars, htf_bars, quote, cfg, risk, ctx) -> dict:
    a = strategy.analyze(symbol, sig_bars, htf_bars, quote, cfg, risk, ctx)
    cand = a.get("candidate")
    edge = float(_param(cfg, "edge_frac"))
    if not cand or edge <= 0:
        return a
    n = int(_param(cfg, "h1_range_n"))
    invert = bool(_param(cfg, "invert"))
    pos = range_position(htf_bars, n, float(cand["entry"]))
    if pos is None:
        return a
    side = cand["side"]
    extreme = (side == "BUY" and pos > 1 - edge) or (side == "SELL" and pos < edge)
    if extreme == invert:  # not in the blocked region
        return a
    where = "sus" if side == "BUY" else "jos"
    blocker = (f"Intrare {side} cu prețul la {pos:.0%} din intervalul ultimelor {n} bare HTF "
               f"({'în' if not invert else 'în afara'} benzii extreme de {edge:.0%} {where}) — filtru de poziție în interval.")
    cand["blockers"].append(blocker)
    if a["direction"] != "HOLD":
        comps = list(a["reasons"])
        a["direction"] = "HOLD"
        a["entry"] = a["stop_loss"] = a["take_profit"] = a["rr"] = None
        a["reasons"] = [f"Setup {side} detectat, dar blocat de filtre:", blocker, *("(+) " + c for c in comps)]
    else:
        a["reasons"].append(blocker)
    return a
