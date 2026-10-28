"""donchian-v1: Turtle-like channel breakout on higher timeframes (Stage C2).

Entry on a CLOSE beyond the highest high / lowest low of the previous entry_n bars (the signal bar excluded);
entry_n 0 = by timeframe (D1 55, H4 100, otherwise 100). SL = sl_atr × ATR(atr_n) (2×ATR20), TP = tp_atr × ATR
(far placeholder, 20×ATR). Exit: default_exit() = exit mode 'channel' with channel_n = exit_n (20): close when the
close crosses the exit_n-bar opposite extreme. HTF not used."""
from __future__ import annotations

from . import _common as C
from . import _htf as X

NAME = "donchian-v1"
VERSION = "1.0"
DESCRIPTION_RO = ("Spargere de canal Donchian (tip Turtle) pe H4/D1: intrare la închidere peste maximul/sub minimul "
                  "ultimelor N bare (55 pe D1, 100 pe H4), SL 2×ATR(20), ieșire la închiderea dincolo de extrema "
                  "opusă a ultimelor 20 de bare; fără TP real (20×ATR ca plasă).")
PARAMS = {"entry_n": 0, "exit_n": 20, "atr_n": 20, "sl_atr": 2.0, "tp_atr": 20.0}
RUN_DEFAULTS = dict(X.RUN_DEFAULTS)
AUTO_ENTRY_N = {"D1": 55, "H4": 100}


def entry_n(cfg: dict) -> int:
    n = int(C.p(cfg, PARAMS, "entry_n") or 0)
    return n if n > 0 else AUTO_ENTRY_N.get(str(cfg.get("signal_timeframe", "D1")).upper(), 100)


def default_exit(cfg: dict) -> dict:
    return {"mode": "channel", "params": {"channel_n": int(C.p(cfg, PARAMS, "exit_n"))}}


def analyze(symbol, sig_bars, htf_bars, quote, cfg, risk, ctx) -> dict:
    g = lambda k: C.p(cfg, PARAMS, k)  # noqa: E731
    n, m, atr_n = entry_n(cfg), int(g("exit_n")), int(g("atr_n"))
    a, bars = X.start(symbol, sig_bars, htf_bars, quote, cfg, ctx, max(n, m, atr_n) + 2)
    if bars is None:
        return a
    digits = int(quote.get("digits") or 5)
    atr_s = X.atr_series(bars, atr_n)
    atr_v = atr_s[-1]
    prior = bars[-n - 1:-1]
    hi, lo = max(b["h"] for b in prior), min(b["l"] for b in prior)
    ex = bars[-m - 1:-1]
    close = bars[-1]["c"]
    a["indicators"] = X.indicators(digits, atr_v, X.htf_trend(htf_bars), channel_high=C.r_(hi, digits),
                                   channel_low=C.r_(lo, digits), exit_high=C.r_(max(b["h"] for b in ex), digits),
                                   exit_low=C.r_(min(b["l"] for b in ex), digits), entry_n=n, exit_n=m)
    if not atr_v:
        return C.hold(a, "Indicatori incompleți (ATR indisponibil).")
    side = "BUY" if close > hi else "SELL" if close < lo else None
    a["regime"] = X.regime(atr_s, 1 if side == "BUY" else -1 if side == "SELL" else 0)
    if side is None:
        return C.hold(a, f"Prețul este în canalul Donchian de {n} bare ({lo:.{digits}f}–{hi:.{digits}f}).")
    lvl = hi if side == "BUY" else lo
    comps = [f"Închidere {'peste maximul' if side == 'BUY' else 'sub minimul'} ultimelor {n} bare ({lvl:.{digits}f})",
             f"SL {float(g('sl_atr')):g}×ATR({atr_n}); ieșire pe canalul opus de {m} bare"]
    return X.finish_atr(a, symbol, side, quote, cfg, risk, ctx, comps, atr_v, g("sl_atr"), g("tp_atr"),
                        note=f"TP {float(g('tp_atr')):g}×ATR doar ca plasă")
