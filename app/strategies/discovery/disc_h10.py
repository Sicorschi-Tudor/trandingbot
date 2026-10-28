"""disc-h10: RSI(2) daily mean reversion (Connors) on D1 (Stage D discovery, hypothesis H10).

Rationale: short-horizon (1-5 day) overreaction from liquidity-demanding flows reverts, best in the direction of the
long-term drift. Differs from Bollinger fades: a 2-period oscillator extreme, optionally aligned with SMA200.

Rules (closed bars only): RSI(2) (Wilder, app.indicators.rsi) on closes. BUY at the next open when RSI2 < th and
(trend_filter == 0 or close > SMA200); SELL when RSI2 > 100-th and (trend_filter == 0 or close < SMA200).
SL = sl_atr(2.5)×ATR14 (catastrophe stop), TP far placeholder (tp_atr 20×ATR).
Exit: default_exit() = signal_flip on bias() = sign(SMA5 - close): a long closes at the first close above SMA5, a short
at the first close below SMA5; time stop hold_days = 7 calendar days (= 5 D1 bars, 6 for a Monday entry, of a Mon-Fri broker week);
swap charged by the backtest."""
from __future__ import annotations

from .. import _common as C
from .. import _htf as X
from ... import indicators as ind

NAME = "disc-h10"
VERSION = "1.0"
DESCRIPTION_RO = ("Revenire la medie pe RSI(2) zilnic: cumpără când RSI2 < prag (opțional doar peste SMA200), vinde "
                  "când RSI2 > 100−prag; iese la prima închidere peste/sub SMA5 sau după 5 zile; SL de avarie 2,5×ATR.")
PARAMS = {"th": 10.0, "trend_filter": 1, "rsi_n": 2, "sma_n": 200, "exit_sma_n": 5, "atr_n": 14, "sl_atr": 2.5,
          "tp_atr": 20.0, "hold_days": 7}
RUN_DEFAULTS = dict(X.RUN_DEFAULTS)


def default_exit(cfg: dict) -> dict:
    return {"mode": "signal_flip", "params": {"flip_on_flat": 0, "max_hold_days": float(C.p(cfg, PARAMS, "hold_days"))}}


def bias(symbol, sig_bars, htf_bars, cfg) -> int:
    """+1 while the close is below SMA5 (a long still has room), -1 above it, 0 when equal/undefined."""
    n = int(C.p(cfg, PARAMS, "exit_sma_n"))
    closes = [b["c"] for b in sig_bars[-n:]]
    if len(closes) < n:
        return 0
    s = sum(closes) / n
    c = closes[-1]
    return 1 if c < s else -1 if c > s else 0


def analyze(symbol, sig_bars, htf_bars, quote, cfg, risk, ctx) -> dict:
    g = lambda k: C.p(cfg, PARAMS, k)  # noqa: E731
    sma_n, atr_n = int(g("sma_n")), int(g("atr_n"))
    a, bars = X.start(symbol, sig_bars, htf_bars, quote, cfg, ctx, sma_n + 2)
    if bars is None:
        return a
    digits = int(quote.get("digits") or 5)
    closes = [b["c"] for b in bars]
    r2 = ind.last(ind.rsi(closes, int(g("rsi_n"))))
    sma = ind.last(ind.sma(closes, sma_n))
    atr_s = X.atr_series(bars, atr_n)
    atr_v = atr_s[-1]
    c = closes[-1]
    a["regime"] = X.regime(atr_s, 0 if sma is None else (1 if c > sma else -1))
    a["indicators"] = X.indicators(digits, atr_v, "FLAT", rsi2=C.r_(r2, 2), sma200=C.r_(sma, digits + 1))
    if r2 is None or sma is None or not atr_v:
        return C.hold(a, "Indicatori incompleți (RSI2/SMA200/ATR).")
    th = float(g("th"))
    filt = bool(int(g("trend_filter")))
    side = None
    if r2 < th and (not filt or c > sma):
        side = "BUY"
    elif r2 > 100 - th and (not filt or c < sma):
        side = "SELL"
    if side is None:
        return C.hold(a, f"RSI2 {r2:.1f} fără extremă validă (prag {th:g}, filtru SMA200 {'on' if filt else 'off'}).")
    comps = [f"RSI(2) = {r2:.1f} ({'sub' if side == 'BUY' else 'peste'} {th if side == 'BUY' else 100 - th:g})",
             f"Închidere {'peste' if c > sma else 'sub'} SMA{sma_n}" + ("" if filt else " (filtru dezactivat)")]
    return X.finish_atr(a, symbol, side, quote, cfg, risk, ctx, comps, atr_v, g("sl_atr"), g("tp_atr"),
                        note="ieșire la traversarea SMA5 sau după 5 zile")
