"""disc-H03: month-end USD rebalancing fade (D1, research).

USDX(d) = mean over EURUSD, GBPUSD, AUDUSD, NZDUSD, USDJPY, USDCAD of the USD-signed daily log return (panel,
see _b2). MTD = sum of USDX since the first trading day of the month; z = MTD / (sd60(USDX) * sqrt(days elapsed)).
At the close of the trading day with exactly `entry_offset` weekdays left in the month, if |z| >= zmin, one
position against the USD MTD direction in the USD pair with the largest |pair MTD move / ATR14| (MTD move = close
minus the last close of the previous month). SL = 1.5×ATR14, TP far placeholder (20×ATR). Exit: close of the first
trading day of the new month (signal_flip, flip_on_flat=1) or SL. Only the six USD pairs ever trade."""
from __future__ import annotations

from .. import _common as C
from .. import _htf as X
from . import _b2 as B

NAME = "disc-H03"
VERSION = "0.1"
DESCRIPTION_RO = ("Cercetare: la sfârșitul lunii, contra mișcării USD de la începutul lunii (rebalansare), în perechea "
                  "USD cu cea mai mare mișcare normalizată; ieșire la închiderea primei zile din luna nouă.")
PARAMS = {"h03_entry_offset": 2, "h03_zmin": 0.5, "h03_vol_n": 60, "h03_sl_atr": 1.5, "h03_tp_atr": 20.0}
RUN_DEFAULTS = B.run_defaults("D1")
SYMBOLS = list(B.USD_PAIRS)


def default_exit(cfg: dict) -> dict:
    return {"mode": "signal_flip", "params": {"flip_on_flat": 1}}


def _g(cfg, k):
    return C.p(cfg, PARAMS, k)


def decision(panel: dict, t: int, offset: int, zmin: float, vol_n: int = 60):
    """(symbol, side_sign, z, mtd, score) for the D1 bar opening at t, or None. Uses panel bars with time <= t only."""
    return B._memo(("h03", id(panel), int(t), int(offset), float(zmin), int(vol_n)),
                   lambda: _decision(panel, t, offset, zmin, vol_n) or False, panel) or None


def _ym(t: int) -> tuple[int, int]:
    d = B.d1_date(t)
    return d.year, d.month


def _decision(panel, t, offset, zmin, vol_n):
    d = B.d1_date(t)
    if B.weekdays_left_in_month(d) != offset:
        return None
    ts, r, _ = B.usdx(panel)
    i = B.idx_at(ts, t)
    if i is None or i + 1 < vol_n:
        return None
    k0 = i
    ym = (d.year, d.month)
    while k0 - 1 >= 0 and _ym(ts[k0 - 1]) == ym:
        k0 -= 1
    if k0 == 0:
        return None  # the month start is not in the panel
    mtd = sum(r[k0:i + 1])
    n = i - k0 + 1
    sd = B.sstdev(r[i - vol_n + 1:i + 1])
    if sd <= 0 or mtd == 0:
        return None
    z = mtd / (sd * n ** 0.5)
    if abs(z) < zmin:
        return None
    usd_dir = 1 if mtd > 0 else -1
    best = None
    for sym, sg in B.USD_PAIRS.items():
        pts, bars, atr_s = B.pair_view(panel, sym)
        j = B.idx_at(pts, t)
        if j is None or atr_s[j] is None or atr_s[j] <= 0:
            continue
        j0 = j
        while j0 - 1 >= 0 and _ym(pts[j0 - 1]) == ym:
            j0 -= 1
        if j0 == 0:
            continue
        score = abs(bars[j]["c"] - bars[j0 - 1]["c"]) / atr_s[j]
        if best is None or score > best[1]:
            best = (sym, score, -usd_dir * sg)
    if best is None:
        return None
    return best[0], best[2], z, mtd, best[1]


def bias(symbol, sig_bars, htf_bars, cfg) -> int:
    """Trade direction while the month-end trade should stay open, 0 at the first trading day of the new month."""
    if len(sig_bars) < 2:
        return 0
    panel = B.get_panel(cfg, cfg.get("signal_timeframe", "D1"))
    if panel is None:
        return 0
    offset = int(_g(cfg, "h03_entry_offset"))
    d = B.d1_date(sig_bars[-1]["t"])
    if B.weekdays_left_in_month(d) >= offset:
        return 0  # first day of a month (exit bar) or before the signal day: nothing to hold
    for b in reversed(sig_bars[-12:-1]):
        db = B.d1_date(b["t"])
        if (db.year, db.month) != (d.year, d.month):
            return 0
        if B.weekdays_left_in_month(db) == offset:
            dec = decision(panel, b["t"], offset, float(_g(cfg, "h03_zmin")), int(_g(cfg, "h03_vol_n")))
            return dec[1] if dec and dec[0] == symbol else 0
    return 0


def analyze(symbol, sig_bars, htf_bars, quote, cfg, risk, ctx) -> dict:
    a, bars = X.start(symbol, sig_bars, htf_bars, quote, cfg, ctx, 30)
    if bars is None:
        return a
    digits = int(quote.get("digits") or 5)
    atr_v = X.atr_series(bars, 14)[-1]
    a["indicators"] = X.indicators(digits, atr_v, "FLAT")
    if symbol not in B.USD_PAIRS:
        return C.hold(a, "disc-H03 tranzacționează doar perechile USD.")
    panel = B.get_panel(cfg, cfg.get("signal_timeframe", "D1"))
    if panel is None:
        return C.hold(a, "Panel USD indisponibil (cfg.panel_db lipsă).")
    offset = int(_g(cfg, "h03_entry_offset"))
    dec = decision(panel, sig_bars[-1]["t"], offset, float(_g(cfg, "h03_zmin")), int(_g(cfg, "h03_vol_n")))
    if not dec:
        return C.hold(a, "Nu este ziua de intrare de sfârșit de lună sau z sub prag.")
    sym, sign, z, mtd, score = dec
    a["indicators"].update(usd_mtd=C.r_(mtd, 6), usd_z=C.r_(z, 3), pick_score=C.r_(score, 3), pick=sym)
    if sym != symbol or not atr_v:
        return C.hold(a, f"Perechea aleasă este {sym}.")
    side = "BUY" if sign > 0 else "SELL"
    comps = [f"USD MTD {mtd * 100:+.2f}% (z {z:+.2f}), {offset} zile lucrătoare rămase",
             f"{symbol}: cea mai mare mișcare MTD normalizată ({score:.2f} ATR)"]
    return X.finish_atr(a, symbol, side, quote, cfg, risk, ctx, comps, atr_v, _g(cfg, "h03_sl_atr"),
                        _g(cfg, "h03_tp_atr"), note="ieșire la prima zi din luna nouă")
