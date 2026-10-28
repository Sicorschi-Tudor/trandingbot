"""disc-h20: news-spike overshoot fade (H1, discovery research).

Spike bar S (second to last closed bar): range >= m×ATRh24 (ATR24 on the bars before S) and |close-open| >= 0.6×range.
Next bar N1 (last closed bar) must not exceed S's extreme in the spike direction and must close against it
(close < open after an up spike). At N1's close enter opposite to the spike: SL = S's extreme ± 0.2×ATRh,
TP = S.close - dir × tp_frac × |S.close - S.open| (retracement of S's body from S's close); no setup when the price
is already beyond the TP. Exit: fixed SL/TP, time stop after 6 H1 bars (default_exit)."""
from __future__ import annotations

from .. import _common as C
from .. import _htf as X
from . import _disc3 as D

NAME = "disc-h20"
VERSION = "1.0"
DESCRIPTION_RO = ("Cercetare: fade după o bară-spike (H1). O bară cu range de cel puțin m×ATR24 și corp mare, urmată "
                  "de o bară care nu o depășește și închide în sens opus: intrare contra spike-ului, TP o parte din "
                  "corpul spike-ului, SL peste extrem, ieșire după 6 ore.")
PARAMS = {"h20_m": 2.5, "h20_tp_frac": 0.5, "h20_body_min": 0.6, "h20_sl_buf": 0.2, "h20_atr_n": 24,
          "h20_hold_bars": 6}
RUN_DEFAULTS = dict(D.RUN_DEFAULTS_H1)


def default_exit(cfg: dict) -> dict:
    return {"mode": "base", "params": {"max_hold_hours": float(C.p(cfg, PARAMS, "h20_hold_bars"))}}


def setup(bars: list[dict], cfg: dict) -> dict | None:
    """{side, sl, tp, atr, dir} for a spike at bars[-2] faded at the close of bars[-1], else None."""
    g = lambda k: C.p(cfg, PARAMS, k)  # noqa: E731
    atr_n = int(g("h20_atr_n"))
    if len(bars) < atr_n + 3:
        return None
    s, n1 = bars[-2], bars[-1]
    rng = s["h"] - s["l"]
    body = s["c"] - s["o"]
    if body == 0 or abs(body) < float(g("h20_body_min")) * rng:
        return None
    atr = D.atr_last(bars[-(atr_n + 60):-2], atr_n)
    if not atr or rng < float(g("h20_m")) * atr:
        return None
    d = 1 if body > 0 else -1
    if d > 0 and not (n1["h"] <= s["h"] and n1["c"] < n1["o"]):
        return None
    if d < 0 and not (n1["l"] >= s["l"] and n1["c"] > n1["o"]):
        return None
    ext = s["h"] if d > 0 else s["l"]
    buf = float(g("h20_sl_buf")) * atr
    return {"side": "SELL" if d > 0 else "BUY", "dir": d, "atr": atr, "sl": ext + d * buf,
            "tp": s["c"] - d * float(g("h20_tp_frac")) * abs(body)}


def analyze(symbol, sig_bars, htf_bars, quote, cfg, risk, ctx) -> dict:
    g = lambda k: C.p(cfg, PARAMS, k)  # noqa: E731
    a, bars = X.start(symbol, sig_bars, htf_bars, quote, cfg, ctx, int(g("h20_atr_n")) + 3)
    if bars is None:
        return a
    digits = int(quote.get("digits") or 5)
    s = setup(bars, cfg)
    a["indicators"] = X.indicators(digits, s["atr"] if s else None, "FLAT")
    if s is None:
        return C.hold(a, "Fără spike + bară de respingere.")
    comps = [f"Spike {'în sus' if s['dir'] > 0 else 'în jos'} de cel puțin {float(g('h20_m')):g}×ATR24, "
             "urmat de o bară care nu îl extinde și închide în sens opus",
             f"TP la {float(g('h20_tp_frac')):g} din corpul spike-ului"]
    return C.finish(a, symbol, s["side"], s["sl"], s["tp"], quote, cfg, risk, ctx, comps, len(comps),
                    note=f"ieșire după {int(g('h20_hold_bars'))} bare")
