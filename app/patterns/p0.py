"""Pattern builder #0 (research only): candlestick, chart-pattern, level, smart-money and indicator patterns.

Every detector is a pure function detect_<id>(bars, i, params) -> +1 (bullish) / -1 (bearish) / 0 that reads only
bars[:i+1] (bar i = the last CLOSED bar; keys o, h, l, c). ATR = Wilder ATR(14) computed on a fixed tail of
ATR_TAIL bars ending at i, so a value never depends on how much history the caller passes (live 400 = backtest 400 =
tests). Level helpers (<id>_levels) return the setup levels a standalone plugin needs; filter helpers decide whether a
trend-v1 candidate agrees with the pattern (filter_<id>). Nothing here is used by the live engine.

Patterns: C1_ENGULF (engulfing), C4_3SOLDIERS (three soldiers / crows), P3_FLAG (bull / bear flag), L2_ROUND (round
numbers 00/50), S1_SWEEP (liquidity sweep of a confirmed swing), I1_ICHI_CLOUD (Ichimoku cloud breakout / state),
I4_ADX (ADX trend filter with DI direction)."""
from __future__ import annotations

import math

from .. import indicators as ind

ATR_N = 14
ATR_TAIL = 100
ADX_TAIL = 150
SWING_LOOKBACK = 100

PATTERN_IDS = ("C1_ENGULF", "C4_3SOLDIERS", "P3_FLAG", "L2_ROUND", "S1_SWEEP", "I1_ICHI_CLOUD", "I4_ADX")


# ---- shared helpers ----------------------------------------------------------------------------------------------

def atr_at(bars: list[dict], i: int, n: int = ATR_N) -> float | None:
    """Wilder ATR(n) at bar i from bars[i-ATR_TAIL+1 .. i] only (None when fewer than n+1 bars exist)."""
    lo = max(0, i - ATR_TAIL + 1)
    if i - lo + 1 < n + 1:
        return None
    seg = bars[lo:i + 1]
    return ind.last(ind.atr([b["h"] for b in seg], [b["l"] for b in seg], [b["c"] for b in seg], n))


def _p(params: dict | None, key: str, default):
    v = (params or {}).get(key)
    return default if v is None else v


def _body(b: dict) -> float:
    return abs(b["c"] - b["o"])


def _bull(b: dict) -> bool:
    return b["c"] > b["o"]


def _bear(b: dict) -> bool:
    return b["c"] < b["o"]


def _slope(ys: list[float]) -> float:
    """Least-squares slope of ys against 0..n-1."""
    n = len(ys)
    if n < 2:
        return 0.0
    mx = (n - 1) / 2.0
    my = sum(ys) / n
    num = sum((k - mx) * (y - my) for k, y in enumerate(ys))
    den = sum((k - mx) ** 2 for k in range(n))
    return num / den if den else 0.0


def _line(ys: list[float]) -> tuple[float, float]:
    """(intercept, slope) of the regression line of ys over x = 0..n-1."""
    n = len(ys)
    s = _slope(ys)
    return sum(ys) / n - s * (n - 1) / 2.0, s


def event_window(detect, bars: list[dict], i: int, params: dict | None, k: int) -> tuple[bool, bool]:
    """(bullish event on one of bars i-k+1..i, bearish event on one of them)."""
    up = dn = False
    for j in range(max(0, i - int(k) + 1), i + 1):
        d = detect(bars, j, params)
        up |= d > 0
        dn |= d < 0
    return up, dn


def event_filter(detect, bars: list[dict], i: int, side: str, params: dict | None, k: int,
                 mode: str = "confirm") -> bool:
    """Filter verdict for an event pattern (True = keep the side-`side` candidate at bar i).
    confirm: keep only if a same-side event printed on bars i-k+1..i and no opposite-side event did.
    veto: keep unless an opposite-side event printed in that window."""
    up, dn = event_window(detect, bars, i, params, k)
    same, opp = (up, dn) if side == "BUY" else (dn, up)
    if mode == "veto":
        return not opp
    return same and not opp


# ---- C1_ENGULF ---------------------------------------------------------------------------------------------------

def detect_c1_engulf(bars: list[dict], i: int, params: dict | None = None) -> int:
    """Bullish: i-1 bearish, i bullish, O[i] <= C[i-1], C[i] >= O[i-1], body[i] >= m*ATR and (local=True) L[i] is the
    lowest low of the last 5 bars. Bearish mirror. params: m (0.5), local (True)."""
    if i < 5 or i >= len(bars):
        return 0
    b0, b1 = bars[i - 1], bars[i]
    side = 0
    if _bear(b0) and _bull(b1) and b1["o"] <= b0["c"] and b1["c"] >= b0["o"]:
        side = 1
    elif _bull(b0) and _bear(b1) and b1["o"] >= b0["c"] and b1["c"] <= b0["o"]:
        side = -1
    if not side:
        return 0
    if bool(_p(params, "local", True)):
        win = bars[i - 4:i + 1]
        if side > 0 and b1["l"] > min(b["l"] for b in win):
            return 0
        if side < 0 and b1["h"] < max(b["h"] for b in win):
            return 0
    a = atr_at(bars, i)
    if not a or _body(b1) < float(_p(params, "m", 0.5)) * a:
        return 0
    return side


def c1_levels(bars: list[dict], i: int, side: int) -> dict:
    a = atr_at(bars, i) or 0.0
    if side > 0:
        return {"sl": min(bars[i]["l"], bars[i - 1]["l"]) - 0.1 * a, "atr": a}
    return {"sl": max(bars[i]["h"], bars[i - 1]["h"]) + 0.1 * a, "atr": a}


# ---- C4_3SOLDIERS ------------------------------------------------------------------------------------------------

def detect_c4_3soldiers(bars: list[dict], i: int, params: dict | None = None) -> int:
    """Soldiers: bars i-2..i bullish, closes rising, each open inside the previous real body, body >= b*ATR(i),
    upper wick <= 0.3*range. Crows: mirror (lower wick). params: b (0.3)."""
    if i < 3 or i >= len(bars):
        return 0
    tri = bars[i - 2:i + 1]
    if all(_bull(x) for x in tri):
        side = 1
    elif all(_bear(x) for x in tri):
        side = -1
    else:
        return 0
    for k in (1, 2):
        p_, c_ = tri[k - 1], tri[k]
        if side > 0 and not (c_["c"] > p_["c"] and p_["o"] <= c_["o"] <= p_["c"]):
            return 0
        if side < 0 and not (c_["c"] < p_["c"] and p_["c"] <= c_["o"] <= p_["o"]):
            return 0
    for x in tri:
        rng = x["h"] - x["l"]
        if rng <= 0:
            return 0
        wick = x["h"] - max(x["o"], x["c"]) if side > 0 else min(x["o"], x["c"]) - x["l"]
        if wick > 0.3 * rng:
            return 0
    a = atr_at(bars, i)
    if not a:
        return 0
    bmin = float(_p(params, "b", 0.3)) * a
    if any(_body(x) < bmin for x in tri):
        return 0
    return side


def c4_levels(bars: list[dict], i: int, side: int, entry: float) -> dict:
    """SL = extreme of the first soldier/crow or entry -/+ 1.5*ATR, whichever is closer to the entry."""
    a = atr_at(bars, i) or 0.0
    if side > 0:
        return {"sl": max(bars[i - 2]["l"], entry - 1.5 * a), "atr": a}
    return {"sl": min(bars[i - 2]["h"], entry + 1.5 * a), "atr": a}


# ---- P3_FLAG -----------------------------------------------------------------------------------------------------

def _flag(bars: list[dict], i: int, params: dict | None):
    """(side, info) of a flag breakout triggered by the close of bar i, else (0, None). Flag = bars i-f..i-1
    (3 <= f <= max_flag_bars), pole = the n bars before it (3..8). Shortest flag / shortest pole wins."""
    p_atr = float(_p(params, "p", 2.0))
    max_f = int(_p(params, "max_flag_bars", 12))
    if i < 3 + 3 + 1 or i >= len(bars):
        return 0, None
    a = atr_at(bars, i)
    if not a:
        return 0, None
    c_i = bars[i]["c"]
    for f in range(3, max_f + 1):
        b = i - f - 1  # last pole bar
        if b - 2 < 0:
            break
        flag = bars[i - f:i]
        hs = [x["h"] for x in flag]
        ls = [x["l"] for x in flag]
        f_hi, f_lo = max(hs), min(ls)
        h0, hs_s = _line(hs)
        l0, ls_s = _line(ls)
        for side in (1, -1):
            if side > 0 and not (hs_s <= 0 and ls_s <= 0):
                continue
            if side < 0 and not (hs_s >= 0 and ls_s >= 0):
                continue
            # trigger: close beyond the regression line of the flag extended to bar i (x = f), first close beyond
            if side > 0:
                lvl_i, lvl_prev = h0 + hs_s * f + 0.1 * a, h0 + hs_s * (f - 1) + 0.1 * a
                if not (c_i > lvl_i and bars[i - 1]["c"] <= lvl_prev):
                    continue
            else:
                lvl_i, lvl_prev = l0 + ls_s * f - 0.1 * a, l0 + ls_s * (f - 1) - 0.1 * a
                if not (c_i < lvl_i and bars[i - 1]["c"] >= lvl_prev):
                    continue
            for n in range(3, 9):
                a_ = b - n + 1
                if a_ < 0:
                    break
                pole = bars[a_:b + 1]
                height = (pole[-1]["c"] - pole[0]["o"]) * side
                if height < p_atr * a:
                    continue
                if sum(1 for x in pole if (_bull(x) if side > 0 else _bear(x))) * 3 < 2 * n:
                    continue
                if f_hi - f_lo > 0.5 * height:
                    continue
                top = pole[-1]["c"]
                if side > 0 and f_lo < top - 0.5 * height:
                    continue
                if side < 0 and f_hi > top + 0.5 * height:
                    continue
                return side, {"height": height, "flag_hi": f_hi, "flag_lo": f_lo, "flag_bars": f, "pole_bars": n,
                              "atr": a}
    return 0, None


def detect_p3_flag(bars: list[dict], i: int, params: dict | None = None) -> int:
    """Bull/bear flag breakout on the close of bar i. params: p (pole size in ATR, 2.0), max_flag_bars (12)."""
    return _flag(bars, i, params)[0]


def p3_levels(bars: list[dict], i: int, params: dict | None = None) -> dict | None:
    side, info = _flag(bars, i, params)
    if not side:
        return None
    a = info["atr"]
    sl = info["flag_lo"] - 0.1 * a if side > 0 else info["flag_hi"] + 0.1 * a
    return {"side": side, "sl": sl, "height": info["height"], "atr": a}


# ---- L2_ROUND ----------------------------------------------------------------------------------------------------

def pip_size(price: float) -> float:
    """FX pip: 0.01 for JPY-style prices (> 20), else 0.0001."""
    return 0.01 if price > 20 else 0.0001


def round_levels(price: float, step_pips: float) -> tuple[float, float]:
    """(nearest round level <= price, nearest round level > price) for a grid of step_pips pips."""
    step = float(step_pips) * pip_size(price)
    lo = math.floor(price / step + 1e-9) * step
    return lo, lo + step


def detect_l2_round(bars: list[dict], i: int, params: dict | None = None) -> int:
    """Rejection at a round level: +1 when bar i opened and closed above a round level and its low pierced it,
    -1 when it opened and closed below a round level and its high pierced it. params: step (pips, 50)."""
    if i < 0 or i >= len(bars):
        return 0
    b = bars[i]
    step = float(_p(params, "step", 50))
    lvl, _ = round_levels(min(b["o"], b["c"]), step)  # highest level at or below the body
    if b["l"] < lvl < min(b["o"], b["c"]):
        return 1
    _, lvl = round_levels(max(b["o"], b["c"]), step)  # lowest level above the body
    if max(b["o"], b["c"]) < lvl < b["h"]:
        return -1
    return 0


def filter_l2_round(bars: list[dict], i: int, side: str, entry: float, params: dict | None = None) -> bool:
    """Keep a BUY when no round level lies in (entry, entry + D*ATR] or the nearest level below is within 0.3*ATR
    (entry just above support). SELL mirror. params: step (pips, 50), D (ATR units, 0.5)."""
    a = atr_at(bars, i)
    if not a:
        return True
    step = float(_p(params, "step", 50))
    d = float(_p(params, "D", 0.5)) * a
    lo, hi = round_levels(entry, step)
    if side == "BUY":
        return hi - entry > d or entry - lo <= 0.3 * a
    if abs(entry - lo) < 1e-12:  # SELL exactly on a level: the level is not below the path
        lo -= float(step) * pip_size(entry)
    return entry - lo > d or hi - entry <= 0.3 * a


# ---- S1_SWEEP ----------------------------------------------------------------------------------------------------

def _swings(bars: list[dict], i: int, half: int, kind: str) -> list[int]:
    """Indices j (newest first) of confirmed fractal swing lows/highs: strict extreme against the `half` bars before,
    not exceeded by the `half` bars after, with j + half <= i - 1 (confirmed before bar i), within SWING_LOOKBACK."""
    out = []
    lo = max(half, i - SWING_LOOKBACK)
    for j in range(i - 1 - half, lo - 1, -1):
        v = bars[j]["l"] if kind == "low" else bars[j]["h"]
        before = bars[j - half:j]
        after = bars[j + 1:j + half + 1]
        if kind == "low":
            if all(v < x["l"] for x in before) and all(v <= x["l"] for x in after):
                out.append(j)
        else:
            if all(v > x["h"] for x in before) and all(v >= x["h"] for x in after):
                out.append(j)
    return out


def _sweep(bars: list[dict], i: int, params: dict | None):
    half = int(_p(params, "L", 3))
    pierce = float(_p(params, "min_pierce", 0.1))
    if i < 2 * half + 6 or i >= len(bars):
        return 0, None
    a = atr_at(bars, i)
    if not a:
        return 0, None
    b = bars[i]
    lows = [j for j in _swings(bars, i, half, "low") if j <= i - 5]
    highs = [j for j in _swings(bars, i, half, "high") if j <= i - 5]
    if lows:
        lvl = bars[lows[0]]["l"]
        if b["l"] <= lvl - pierce * a and b["c"] > lvl:
            return 1, {"level": lvl, "atr": a, "target": bars[highs[0]]["h"] if highs else None}
    if highs:
        lvl = bars[highs[0]]["h"]
        if b["h"] >= lvl + pierce * a and b["c"] < lvl:
            return -1, {"level": lvl, "atr": a, "target": bars[lows[0]]["l"] if lows else None}
    return 0, None


def detect_s1_sweep(bars: list[dict], i: int, params: dict | None = None) -> int:
    """+1: bar i pierced the latest confirmed swing low (>= 5 bars old) by >= min_pierce*ATR and closed back above it;
    -1 mirror on the latest swing high. params: L (fractal half-width, 3), min_pierce (ATR units, 0.1)."""
    return _sweep(bars, i, params)[0]


def s1_levels(bars: list[dict], i: int, params: dict | None = None) -> dict | None:
    side, info = _sweep(bars, i, params)
    if not side:
        return None
    a = info["atr"]
    sl = bars[i]["l"] - 0.1 * a if side > 0 else bars[i]["h"] + 0.1 * a
    return {"side": side, "sl": sl, "target": info["target"], "atr": a}


# ---- I1_ICHI_CLOUD -----------------------------------------------------------------------------------------------

def _mid(bars: list[dict], j: int, n: int) -> float:
    seg = bars[j - n + 1:j + 1]
    return (max(x["h"] for x in seg) + min(x["l"] for x in seg)) / 2.0


def ichimoku_at(bars: list[dict], i: int, scale: int = 1) -> dict | None:
    """Tenkan/Kijun at i, the cloud at i (SpanA/SpanB computed at i-disp, disp = 26*scale), the spans computed now
    (future cloud) and the close disp bars ago. None when the history is too short."""
    t_n, k_n, b_n = 9 * scale, 26 * scale, 52 * scale
    disp = k_n
    j = i - disp
    if j - b_n + 1 < 0 or i >= len(bars):
        return None
    span_a_then = (_mid(bars, j, t_n) + _mid(bars, j, k_n)) / 2.0
    span_b_then = _mid(bars, j, b_n)
    tenkan, kijun = _mid(bars, i, t_n), _mid(bars, i, k_n)
    return {"tenkan": tenkan, "kijun": kijun, "top": max(span_a_then, span_b_then),
            "bottom": min(span_a_then, span_b_then), "span_a_now": (tenkan + kijun) / 2.0,
            "span_b_now": _mid(bars, i, b_n), "close_disp": bars[j]["c"]}


def _ichi_conf(bars: list[dict], i: int, side: int, ic: dict, conf: str) -> bool:
    c = bars[i]["c"]
    if conf in ("chikou", "full") and not ((c - ic["close_disp"]) * side > 0):
        return False
    if conf == "full" and not ((ic["span_a_now"] - ic["span_b_now"]) * side > 0):
        return False
    return True


def detect_i1_ichi_cloud(bars: list[dict], i: int, params: dict | None = None) -> int:
    """Cloud breakout on bar i: +1 when C[i] > cloud top at i and C[i-1] <= cloud top at i-1 (with conf: 'none',
    'chikou' = C[i] > C[i-disp], 'full' = chikou + SpanA_now > SpanB_now); -1 mirror below the bottom.
    params: conf ('none'), scale (1; 2 doubles 9/26/52)."""
    scale = int(_p(params, "scale", 1))
    conf = str(_p(params, "conf", "none"))
    if i < 1:
        return 0
    ic, ip = ichimoku_at(bars, i, scale), ichimoku_at(bars, i - 1, scale)
    if ic is None or ip is None:
        return 0
    c, cp = bars[i]["c"], bars[i - 1]["c"]
    if c > ic["top"] and cp <= ip["top"] and _ichi_conf(bars, i, 1, ic, conf):
        return 1
    if c < ic["bottom"] and cp >= ip["bottom"] and _ichi_conf(bars, i, -1, ic, conf):
        return -1
    return 0


def ichi_state(bars: list[dict], i: int, params: dict | None = None) -> int:
    """+1 when C[i] is above the cloud (with conf), -1 below it (with conf), else 0."""
    scale = int(_p(params, "scale", 1))
    conf = str(_p(params, "conf", "none"))
    ic = ichimoku_at(bars, i, scale)
    if ic is None:
        return 0
    c = bars[i]["c"]
    if c > ic["top"] and _ichi_conf(bars, i, 1, ic, conf):
        return 1
    if c < ic["bottom"] and _ichi_conf(bars, i, -1, ic, conf):
        return -1
    return 0


def filter_i1_ichi_cloud(bars: list[dict], i: int, side: str, params: dict | None = None) -> bool:
    """K = 0: state filter (BUY only above the cloud, SELL only below). K > 0: a same-side breakout within the last
    K bars (and no opposite one)."""
    k = int(_p(params, "K", 0))
    if k <= 0:
        st = ichi_state(bars, i, params)
        return st > 0 if side == "BUY" else st < 0
    return event_filter(detect_i1_ichi_cloud, bars, i, side, params, k, "confirm")


# ---- I4_ADX ------------------------------------------------------------------------------------------------------

def dmi_at(bars: list[dict], i: int, n: int = 14) -> dict | None:
    """Wilder +DI, -DI and ADX (n) at bars i, i-1 and i-3 from bars[i-ADX_TAIL+1 .. i] only."""
    lo = max(0, i - ADX_TAIL + 1)
    seg = bars[lo:i + 1]
    m = len(seg)
    if m < 2 * n + 4:
        return None
    tr_s = pdm_s = mdm_s = 0.0
    adx = None
    dx_acc: list[float] = []
    out: list[tuple[float, float, float | None]] = []
    for k in range(1, m):
        h, l_, pc = seg[k]["h"], seg[k]["l"], seg[k - 1]["c"]
        up, dn = h - seg[k - 1]["h"], seg[k - 1]["l"] - l_
        pdm = up if up > dn and up > 0 else 0.0
        mdm = dn if dn > up and dn > 0 else 0.0
        tr = max(h - l_, abs(h - pc), abs(l_ - pc))
        if k <= n:
            tr_s += tr
            pdm_s += pdm
            mdm_s += mdm
            if k < n:
                out.append((0.0, 0.0, None))
                continue
        else:
            tr_s = tr_s - tr_s / n + tr
            pdm_s = pdm_s - pdm_s / n + pdm
            mdm_s = mdm_s - mdm_s / n + mdm
        pdi = 100.0 * pdm_s / tr_s if tr_s > 0 else 0.0
        mdi = 100.0 * mdm_s / tr_s if tr_s > 0 else 0.0
        dx = 100.0 * abs(pdi - mdi) / (pdi + mdi) if pdi + mdi > 0 else 0.0
        if adx is None:
            dx_acc.append(dx)
            if len(dx_acc) == n:
                adx = sum(dx_acc) / n
        else:
            adx = (adx * (n - 1) + dx) / n
        out.append((pdi, mdi, adx))
    if len(out) < 4 or out[-4][2] is None:
        return None
    return {"pdi": out[-1][0], "mdi": out[-1][1], "adx": out[-1][2], "adx_prev": out[-2][2], "adx_3": out[-4][2],
            "pdi_prev": out[-2][0], "mdi_prev": out[-2][1]}


def adx_state(bars: list[dict], i: int, params: dict | None = None) -> int:
    """+1 when ADX > T and +DI > -DI (and, rising=True, ADX[i] > ADX[i-3]); -1 when ADX > T and -DI > +DI; else 0."""
    d = dmi_at(bars, i, int(_p(params, "n", 14)))
    if d is None or d["adx"] <= float(_p(params, "T", 25)):
        return 0
    if bool(_p(params, "rising", False)) and not d["adx"] > d["adx_3"]:
        return 0
    if d["pdi"] > d["mdi"]:
        return 1
    if d["mdi"] > d["pdi"]:
        return -1
    return 0


def detect_i4_adx(bars: list[dict], i: int, params: dict | None = None) -> int:
    """Strategy trigger: ADX crosses above T on bar i with +DI > -DI and C[i] > EMA50 -> +1; mirror -> -1."""
    d = dmi_at(bars, i, int(_p(params, "n", 14)))
    T = float(_p(params, "T", 25))
    if d is None or not (d["adx_prev"] <= T < d["adx"]):
        return 0
    lo = max(0, i - ADX_TAIL + 1)
    e50 = ind.last(ind.ema([b["c"] for b in bars[lo:i + 1]], 50))
    if e50 is None:
        return 0
    c = bars[i]["c"]
    if d["pdi"] > d["mdi"] and c > e50:
        return 1
    if d["mdi"] > d["pdi"] and c < e50:
        return -1
    return 0


def filter_i4_adx(bars: list[dict], i: int, side: str, params: dict | None = None) -> bool:
    st = adx_state(bars, i, params)
    return st > 0 if side == "BUY" else st < 0


# ---- registry ----------------------------------------------------------------------------------------------------

DETECTORS = {
    "C1_ENGULF": detect_c1_engulf,
    "C4_3SOLDIERS": detect_c4_3soldiers,
    "P3_FLAG": detect_p3_flag,
    "L2_ROUND": detect_l2_round,
    "S1_SWEEP": detect_s1_sweep,
    "I1_ICHI_CLOUD": detect_i1_ichi_cloud,
    "I4_ADX": detect_i4_adx,
}
EVENT_PATTERNS = ("C1_ENGULF", "C4_3SOLDIERS", "P3_FLAG", "S1_SWEEP")


def filter_keep(pid: str, bars: list[dict], i: int, side: str, entry: float, params: dict | None) -> bool:
    """Filter verdict of pattern `pid` for a side-`side` candidate decided on the close of bar i (True = keep).
    Event patterns take params K (window, bars) and fmode ('confirm' | 'veto')."""
    if pid in EVENT_PATTERNS:
        return event_filter(DETECTORS[pid], bars, i, side, params, int(_p(params, "K", 3)),
                            str(_p(params, "fmode", "confirm")))
    if pid == "L2_ROUND":
        return filter_l2_round(bars, i, side, entry, params)
    if pid == "I1_ICHI_CLOUD":
        return filter_i1_ichi_cloud(bars, i, side, params)
    if pid == "I4_ADX":
        return filter_i4_adx(bars, i, side, params)
    raise KeyError(pid)


__all__ = ["PATTERN_IDS", "DETECTORS", "EVENT_PATTERNS", "atr_at", "event_filter", "filter_keep",
           "detect_c1_engulf", "detect_c4_3soldiers", "detect_p3_flag", "detect_l2_round", "detect_s1_sweep",
           "detect_i1_ichi_cloud", "detect_i4_adx", "c1_levels", "c4_levels", "p3_levels", "s1_levels",
           "filter_l2_round", "filter_i1_ichi_cloud", "filter_i4_adx", "ichimoku_at", "ichi_state", "dmi_at",
           "adx_state", "round_levels", "pip_size"]
