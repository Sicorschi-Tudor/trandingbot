"""Price-action context for the Claude basket manager (owner's request 2026-11-02): the candlestick, level and chart
patterns a discretionary trader reads, computed in code on closed bars so Claude gets the same picture every bar
instead of eyeballing 30 raw candles.

Per timeframe (M1, M5, M15, H1), from closed bars only:
- candles: engulfing (patterns.p0), pin bar - hammer / shooting star (patterns.p1), inside-bar breakout (patterns.p2),
  morning / evening star (here) - the events of the last CANDLE_LOOKBACK bars, newest first;
- levels: horizontal support / resistance from clustered swing pivots (patterns.p2.SRLevels), the nearest ones below and
  above the close with their distance in USD and in ATR, plus a bounce / rejection on the last bar;
- break_retest: a level the close crossed in the last BR_LOOKBACK bars that the price came back to and held;
- double: double bottom / top - "forming" (two equal swings, neckline intact) or "neckline broken" (patterns.p1).

These detectors were measured as standalone entries in docs/research (STAGE_F): none was an edge on its own. They are
context for Claude's judgement (confluence with trend and level), not signals."""
from __future__ import annotations

from .patterns import p0, p1, p2

CANDLE_LOOKBACK = 3
BR_LOOKBACK = 20
TAIL = 200


def _r(x, nd=2):
    return None if x is None else round(float(x), nd)


def star(bars: list[dict], i: int, atr: float | None) -> int:
    """Morning star (+1): long bearish bar, small-bodied bar, bullish bar closing above the middle of the first body.
    Evening star (-1) mirrored. Long = body >= 0.6 ATR; small = body <= 0.3 x the first body."""
    if i < 2 or not atr:
        return 0
    a, b, c = bars[i - 2], bars[i - 1], bars[i]
    body_a, body_b = abs(a["c"] - a["o"]), abs(b["c"] - b["o"])
    if body_a < 0.6 * atr or body_b > 0.3 * body_a:
        return 0
    mid = (a["o"] + a["c"]) / 2
    if a["c"] < a["o"] and c["c"] > c["o"] and c["c"] > mid:
        return 1
    if a["c"] > a["o"] and c["c"] < c["o"] and c["c"] < mid:
        return -1
    return 0


def _candles(bars: list[dict], atr: list) -> list[dict]:
    n = len(bars)
    ib = p2.scan_c3_insidebar(bars[-60:]) if n >= 60 else []
    out = []
    for back in range(CANDLE_LOOKBACK):
        i = n - 1 - back
        if i < 5:
            break
        found = []
        d = p0.detect_c1_engulf(bars, i)
        if d:
            found.append("engulfing bullish" if d > 0 else "engulfing bearish")
        d = p1.detect_c2_pinbar(bars, i, {})
        if d:
            found.append("pin bar bullish (hammer)" if d > 0 else "pin bar bearish (shooting star)")
        e = ib[len(ib) - 1 - back] if len(ib) > back else None
        if e:
            found.append(f"inside bar breakout {'bullish' if e['dir'] > 0 else 'bearish'} (mother {_r(e['ml'])}–{_r(e['mh'])})")
        d = star(bars, i, atr[i])
        if d:
            found.append("morning star" if d > 0 else "evening star")
        for f in found:
            out.append({"bars_ago": back, "pattern": f})
    return out


def _levels(bars: list[dict], sr: "p2.SRLevels") -> dict:
    i = len(bars) - 1
    a, close = sr.atr[i], bars[i]["c"]
    lv = sr.levels(i)
    if not a or not lv:
        return {}
    below = [x for x in lv if x <= close][-2:][::-1]
    above = [x for x in lv if x > close][:2]
    fmt = lambda x: {"price": _r(x), "dist_usd": _r(abs(close - x)), "dist_atr": _r(abs(close - x) / a, 1)}  # noqa: E731
    out = {"supports": [fmt(x) for x in below], "resistances": [fmt(x) for x in above]}
    ev = p2._l1_event(sr, [b["o"] for b in bars], i)
    if ev:
        out["last_bar"] = (f"bounce off support {_r(ev['level'])}" if ev["dir"] > 0
                           else f"rejection at resistance {_r(ev['level'])}")
    return out


def _break_retest(bars: list[dict], sr: "p2.SRLevels") -> list[dict]:
    """Levels known BR_LOOKBACK bars ago that a close crossed by >= 0.2 ATR since, then the price touched again
    (within 0.3 ATR) and the last close is still on the breakout side."""
    n = len(bars)
    i0 = n - 1 - BR_LOOKBACK
    if i0 < 30:
        return []
    a = sr.atr[n - 1]
    if not a:
        return []
    c = [b["c"] for b in bars]
    out = []
    for lv in sr.levels(i0):
        for d in (1, -1):
            if (c[i0] - lv) * d >= 0:
                continue  # was not on the other side before
            brk = next((k for k in range(i0 + 1, n) if (c[k] - lv) * d >= 0.2 * a), None)
            if brk is None or brk >= n - 1:
                continue
            touch = any((bars[k]["l"] - lv if d > 0 else lv - bars[k]["h"]) <= 0.3 * a for k in range(brk + 1, n))
            if touch and (c[-1] - lv) * d > 0:
                out.append({"level": _r(lv), "dir": "bullish (resistance became support)" if d > 0
                            else "bearish (support became resistance)", "broken_bars_ago": n - 1 - brk})
    return out[:2]


def _double(bars: list[dict], atr: list) -> dict | None:
    n = len(bars)
    for back in range(CANDLE_LOOKBACK):
        i = n - 1 - back
        d = p1.detect_p1_double(bars, i, {})
        if d:
            return {"pattern": "double bottom" if d > 0 else "double top", "state": "neckline broken",
                    "bars_ago": back}
    sh, sl = p2.swing_points(bars, 3)
    a = atr[-1]
    if not a:
        return None
    for d, piv, key in ((1, sl, "l"), (-1, sh, "h")):
        if len(piv) < 2:
            continue
        j1, j2 = piv[-2], piv[-1]
        if not 5 <= j2 - j1 <= 60 or abs(bars[j1][key] - bars[j2][key]) > 0.6 * a:
            continue
        mid = bars[j1 + 1:j2]
        if not mid:
            continue
        neck = max(b["h"] for b in mid) if d > 0 else min(b["l"] for b in mid)
        if (neck - bars[j2][key]) * d < 1.0 * a or (bars[-1]["c"] - neck) * d > 0:
            continue
        return {"pattern": "double bottom" if d > 0 else "double top", "state": "forming (neckline intact)",
                "neckline": _r(neck), "extremes": [_r(bars[j1][key]), _r(bars[j2][key])]}
    return None


def timeframe(bars: list[dict]) -> dict:
    bars = bars[-TAIL:]
    if len(bars) < 60:
        return {}
    atr = p2.atr_series(bars, 14)
    sr = p2.SRLevels(bars)
    out = {"atr14": _r(atr[-1]), "candles": _candles(bars, atr), "levels": _levels(bars, sr)}
    br = _break_retest(bars, sr)
    if br:
        out["break_retest"] = br
    dbl = _double(bars, atr)
    if dbl:
        out["double"] = dbl
    return out


def build(rt, symbol: str, now: float) -> dict:
    out = {}
    for tf in ("M1", "M5", "M15", "H1"):
        try:
            tfc = timeframe(rt.closed_bars(symbol, tf, now))
        except Exception as e:  # a detector failure must never block Claude's decision
            tfc = {"error": repr(e)[:120]}
        if tfc:
            out[tf] = tfc
    return out
