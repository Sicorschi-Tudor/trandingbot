"""disc-h06: ATR compression -> expansion bar on H4 (Stage D discovery, hypothesis H06).

Rationale: after deep volatility compression the first wide-range bar that closes at its extreme reflects new
information / released stop clusters; the expansion persists for a few bars (directional follow-through).

Rules (closed bars only; Wilder ATRs from app.indicators):
- compression: ATR(fast_n=5)/ATR(slow_n=100) < c on at least comp_min=3 of the comp_look=5 bars before the trigger.
- trigger = last closed bar: range >= range_mult(1.5) × ATR(100) (value at the bar before the trigger, so the trigger
  does not inflate its own yardstick) and close in the top close_frac(20%) of its range (BUY) / bottom 20% (SELL).
- enter at the next open. SL = trigger-bar low (BUY) / high (SELL), distance floored at sl_min_atr(1.0)×ATR(100) and
  capped at sl_max_atr(2.0)×ATR(100).
Exit (default_exit by exit_variant): 'fixed' = TP tp_r(2)R + time stop; 'trail' = trailing from +1R at 1.5×ATR14
(exit-module ATR) with TP far (trail_tp_r 10R) + the same time stop. Time stop hold_hours = 48 h (12 H4 bars of
wall-clock time; a weekend inside the trade shortens it)."""
from __future__ import annotations

from .. import _common as C
from .. import _htf as X

NAME = "disc-h06"
VERSION = "1.0"
DESCRIPTION_RO = ("Compresie de volatilitate urmată de o bară largă (H4): după ce ATR(5)/ATR(100) a fost sub prag, "
                  "prima bară cu range ≥ 1,5×ATR(100) închisă la extremă dă direcția; SL la extrema opusă a barei "
                  "(între 1 și 2×ATR(100)), TP 2R sau trailing, ieșire după 12 bare.")
PARAMS = {"c": 0.6, "exit_variant": "fixed", "fast_n": 5, "slow_n": 100, "comp_look": 5, "comp_min": 3,
          "range_mult": 1.5, "close_frac": 0.2, "sl_min_atr": 1.0, "sl_max_atr": 2.0, "tp_r": 2.0,
          "trail_tp_r": 10.0, "trail_start_r": 1.0, "trail_atr_mult": 1.5, "hold_hours": 48}
RUN_DEFAULTS = {**X.RUN_DEFAULTS, "signal_timeframe": "H4", "signal_expiry_minutes": 4 * 1440}


def default_exit(cfg: dict) -> dict:
    g = lambda k: C.p(cfg, PARAMS, k)  # noqa: E731
    hold = float(g("hold_hours"))
    if str(g("exit_variant")) == "trail":
        return {"mode": "trailing", "params": {"trail_start_r": float(g("trail_start_r")),
                                               "trail_atr_mult": float(g("trail_atr_mult")), "trail_min_r": 0.0,
                                               "max_hold_hours": hold}}
    return {"mode": "base", "params": {"max_hold_hours": hold}}


def signal(bars: list[dict], cfg: dict):
    """(side 'BUY'/'SELL'/None, atr_slow at the bar before the trigger, info dict) on the last closed bar."""
    g = lambda k: C.p(cfg, PARAMS, k)  # noqa: E731
    fast = X.atr_series(bars, int(g("fast_n")))
    slow = X.atr_series(bars, int(g("slow_n")))
    look, need = int(g("comp_look")), int(g("comp_min"))
    n = len(bars)
    if n < look + 2 or slow[n - 2] is None:
        return None, None, {}
    ratios = [(fast[j] / slow[j]) if fast[j] is not None and slow[j] else None for j in range(n - 1 - look, n - 1)]
    comp = sum(1 for r in ratios if r is not None and r < float(g("c")))
    atr_s = slow[n - 2]
    b = bars[-1]
    rng = b["h"] - b["l"]
    info = {"compressed": comp, "range_atr": rng / atr_s if atr_s else None,
            "ratio_prev": ratios[-1] if ratios else None}
    if comp < need or rng < float(g("range_mult")) * atr_s or rng <= 0:
        return None, atr_s, info
    loc = (b["c"] - b["l"]) / rng
    info["close_loc"] = loc
    cf = float(g("close_frac"))
    if loc >= 1 - cf:
        return "BUY", atr_s, info
    if loc <= cf:
        return "SELL", atr_s, info
    return None, atr_s, info


def analyze(symbol, sig_bars, htf_bars, quote, cfg, risk, ctx) -> dict:
    g = lambda k: C.p(cfg, PARAMS, k)  # noqa: E731
    a, bars = X.start(symbol, sig_bars, htf_bars, quote, cfg, ctx, int(g("slow_n")) + int(g("comp_look")) + 3)
    if bars is None:
        return a
    digits = int(quote.get("digits") or 5)
    side, atr_s, info = signal(bars, cfg)
    a["regime"] = "RANGE"
    a["indicators"] = X.indicators(digits, atr_s, "FLAT", compressed_bars=info.get("compressed"),
                                   range_atr=C.r_(info.get("range_atr"), 3), close_loc=C.r_(info.get("close_loc"), 3))
    if not atr_s:
        return C.hold(a, "ATR(100) indisponibil.")
    if side is None:
        return C.hold(a, f"Fără bară de expansiune după compresie (compresie {info.get('compressed')} bare, "
                         f"range {info.get('range_atr') or 0:.2f}×ATR100).")
    sign = 1 if side == "BUY" else -1
    a["regime"] = "TREND_UP" if sign > 0 else "TREND_DOWN"
    entry = float(quote["ask"]) if side == "BUY" else float(quote["bid"])
    b = bars[-1]
    raw = sign * (entry - (b["l"] if sign > 0 else b["h"]))
    dist = min(max(raw, float(g("sl_min_atr")) * atr_s), float(g("sl_max_atr")) * atr_s)
    sl = entry - sign * dist
    trail = str(g("exit_variant")) == "trail"
    comps = [f"Compresie: ATR5/ATR100 < {float(g('c')):g} pe {info['compressed']} din {int(g('comp_look'))} bare",
             f"Bară de expansiune {info['range_atr']:.2f}×ATR100 închisă la {info['close_loc'] * 100:.0f}% din range"]
    return C.finish(a, symbol, side, sl, None, quote, cfg, risk, ctx, comps, len(comps),
                    tp_r=float(g("trail_tp_r") if trail else g("tp_r")),
                    note="trailing de la +1R" if trail else f"TP {float(g('tp_r')):g}R")
