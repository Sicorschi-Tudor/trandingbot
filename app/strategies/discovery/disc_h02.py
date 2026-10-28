"""disc-h02: London 4pm fix reversal on H1 (Stage D discovery, hypothesis H02).

Rationale: WM/Reuters 16:00 London fix concentrates rebalancing/hedging flows; the European-session move into the
fix is partly reversed afterwards (Evans 2018; Melvin & Prins 2015).

Rules (closed bars only). Times are London local by default (london_time=1, DST-aware: in winter identical to the
UTC hours of the spec, in BST one hour earlier in UTC, so the fix bar always ends exactly at the 16:00 London fix);
london_time=0 uses fixed UTC hours.
- Decision only at the close of the fix bar (bar starting fix_hour=15:00, ending at 16:00).
- M = close(fix bar) - open(session bar starting open_hour=07:00, same day). ATRd = ATR14 of the closed D1 bars
  (HTF series, htf_timeframe D1).
- |M| >= k*ATRd -> enter opposite to M at the next bar open. SL = sl_atrd*ATRd (0.5), TP = retr*|M| toward the
  session open.
Exit: default_exit() = fixed SL/TP + time stop hold_hours (5 h: closes at the close of the 20:00 bar, before the
New York rollover, so no swap)."""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from .. import _common as C
from .. import _htf as X

NAME = "disc-h02"
VERSION = "1.0"
DESCRIPTION_RO = ("Inversare după fixing-ul de la Londra (H1): dacă mișcarea sesiunii europene 07:00→16:00 este de cel "
                  "puțin k×ATR zilnic, intră în sens opus la deschiderea barei de după fixing; TP = o parte din "
                  "mișcare, SL 0,5×ATR zilnic, ieșire la ora 21:00 Londra (înainte de rollover).")
PARAMS = {"k": 0.4, "retr": 0.5, "sl_atrd": 0.5, "open_hour": 7, "fix_hour": 15, "london_time": 1, "atr_n": 14,
          "hold_hours": 5}
RUN_DEFAULTS = {**X.RUN_DEFAULTS, "signal_timeframe": "H1", "htf_timeframe": "D1", "signal_expiry_minutes": 120}
LONDON = ZoneInfo("Europe/London")


def default_exit(cfg: dict) -> dict:
    return {"mode": "base", "params": {"max_hold_hours": float(C.p(cfg, PARAMS, "hold_hours"))}}


def _hour(t: int, london: bool) -> int:
    return datetime.fromtimestamp(t, LONDON if london else timezone.utc).hour


def session_move(bars: list[dict], cfg: dict):
    """(M, open bar, fix bar) when the last closed bar is the fix bar and the session-open bar of the same day exists."""
    g = lambda k: C.p(cfg, PARAMS, k)  # noqa: E731
    if not bars:
        return None
    london = bool(int(g("london_time")))
    fix = bars[-1]
    if _hour(fix["t"], london) != int(g("fix_hour")):
        return None
    t_open = fix["t"] - (int(g("fix_hour")) - int(g("open_hour"))) * 3600
    for b in reversed(bars[-30:]):
        if b["t"] == t_open:
            return fix["c"] - b["o"], b, fix
        if b["t"] < t_open:
            break
    return None


def analyze(symbol, sig_bars, htf_bars, quote, cfg, risk, ctx) -> dict:
    g = lambda k: C.p(cfg, PARAMS, k)  # noqa: E731
    a = C.skeleton(symbol, sig_bars, quote, cfg, ctx)
    if not quote or not quote.get("bid") or not quote.get("ask"):
        return C.hold(a, "Nu există cotație bid/ask pentru simbol.")
    sm = session_move(sig_bars, cfg)  # cheap gate first: one decision per day
    if sm is None:
        return C.hold(a, "Nu este bara fixing-ului de la Londra (sau lipsește bara de deschidere a sesiunii).")
    a, bars = X.start(symbol, sig_bars, htf_bars, quote, cfg, ctx, 30)
    if bars is None:
        return a
    digits = int(quote.get("digits") or 5)
    atr_n = int(g("atr_n"))
    hb = list(htf_bars or [])[-X.TAIL:]
    atr_d = X.atr_series(hb, atr_n)[-1] if len(hb) >= atr_n + 1 else None
    move, ob, fb = sm
    sign = -1 if move > 0 else 1 if move < 0 else 0
    a["regime"] = "RANGE"
    a["indicators"] = X.indicators(digits, atr_d, "FLAT", session_move=C.r_(move, digits + 1),
                                   move_atrd=C.r_(abs(move) / atr_d, 3) if atr_d else None)
    if not atr_d:
        return C.hold(a, f"ATR zilnic indisponibil (necesită {atr_n + 1} bare D1 închise).")
    k = float(g("k"))
    if sign == 0 or abs(move) < k * atr_d:
        return C.hold(a, f"Mișcarea sesiunii {abs(move) / atr_d:.2f}×ATR zilnic < {k:g} — fără setup.")
    side = "BUY" if sign > 0 else "SELL"
    entry = float(quote["ask"]) if side == "BUY" else float(quote["bid"])
    sl = entry - sign * float(g("sl_atrd")) * atr_d
    tp = entry + sign * float(g("retr")) * abs(move)
    comps = [f"Mișcare sesiune europeană {move:+.{digits}f} ({abs(move) / atr_d:.2f}×ATR zilnic) până la fixing",
             f"Inversare parțială {float(g('retr')):g}×mișcare spre deschiderea sesiunii"]
    return C.finish(a, symbol, side, sl, tp, quote, cfg, risk, ctx, comps, len(comps),
                    note=f"ieșire forțată după {float(g('hold_hours')):g} ore")
