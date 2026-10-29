"""Pattern builder #1 (research): candlestick, chart-pattern, level, smart-money and indicator patterns.

Every public function reads ONLY bars[:i+1] (closed bars; keys t, o, h, l, c) and, inside that, only a fixed tail
(TAIL_* bars ending at i), so a result never depends on how long a window the caller passes (live 400 bars,
backtest 400 bars, research harness = the whole series) as long as the window holds the tail.

Per pattern <ID>:
- detect_<id>(bars, i, params) -> +1 (bullish event at bar i) / -1 (bearish) / 0
- setup_<id>(bars, i, params, htf=None) -> {side, dir, sl, tp | tp_r, cap_r, atr, note} | None   (standalone entries)
- filter_<id>(bars, i, side, entry, params, htf=None) -> True (keep the candidate) / False (block it)

Pivot levels (L3_PIVOTS) need the previous broker day: `htf` (closed higher-timeframe bars, e.g. H1 under M5) is
used when given, else the signal bars themselves. Broker day = calendar date in Europe/Athens of the bar start
(MetaQuotes-Demo D1 boundary, see app/strategies/discovery/_disc3.py).

Research only: nothing here is used by the live engine."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from .. import indicators as ind

ATR_N = 14
BROKER_TZ = ZoneInfo("Europe/Athens")

TAIL_PIN = 80
TAIL_DOUBLE = 170
TAIL_TRI = 110  # W (<= 60) + fractal confirmation + margin
TAIL_FVG = 120
TAIL_ICHI = 160
TAIL_SQZ = 120
TAIL_PIV = 60  # ATR only (the day levels come from `htf` / a separate window)
DOUBLE_MAX_WAIT = 30  # neckline break must come within 30 bars after P2 is confirmed
TRI_L = 3  # fractal half-width of the triangle pivots


# ---------------------------------------------------------------------------------------------- helpers

def _tail(bars: list[dict], i: int, n: int) -> list[dict]:
    if i < 0 or i >= len(bars):
        raise IndexError(f"bar index {i} outside 0..{len(bars) - 1}")
    return bars[max(0, i - n + 1): i + 1]


def _hlc(sub: list[dict]):
    return [b["h"] for b in sub], [b["l"] for b in sub], [b["c"] for b in sub]


def _atr(sub: list[dict]) -> list:
    h, l, c = _hlc(sub)
    return ind.atr(h, l, c, ATR_N)


def atr_at(bars: list[dict], i: int, tail: int = TAIL_PIV) -> float | None:
    """ATR14 (Wilder) at bar i on a fixed tail."""
    return _atr(_tail(bars, i, tail))[-1]


def _swings(highs: list[float], lows: list[float], L: int, upto: int) -> tuple[list[int], list[int]]:
    """Fractal pivots confirmed by bar `upto` (pivot j needs bars j-L..j+L, j+L <= upto): strict on the left,
    >= / <= on the right (a flat top pivots once, at its first bar)."""
    sh, sl = [], []
    for j in range(L, upto - L + 1):
        hj, lj = highs[j], lows[j]
        if hj > max(highs[j - L:j]) and hj >= max(highs[j + 1:j + L + 1]):
            sh.append(j)
        if lj < min(lows[j - L:j]) and lj <= min(lows[j + 1:j + L + 1]):
            sl.append(j)
    return sh, sl


def _p(params: dict, key: str, default):
    v = (params or {}).get(key)
    return default if v is None else v


# ---------------------------------------------------------------------------------------------- C2_PINBAR

def _pin(sub, atr, k, w) -> int:
    if k < 5:
        return 0
    b = sub[k]
    rng = b["h"] - b["l"]
    a = atr[k - 1]
    if rng <= 0 or a is None or rng < 0.7 * a:
        return 0
    body = abs(b["c"] - b["o"])
    if body > 0.33 * rng:
        return 0
    lw = min(b["o"], b["c"]) - b["l"]
    uw = b["h"] - max(b["o"], b["c"])
    if lw >= w * rng and uw <= 0.25 * rng and b["l"] < min(x["l"] for x in sub[k - 5:k]):
        return 1
    if uw >= w * rng and lw <= 0.25 * rng and b["h"] > max(x["h"] for x in sub[k - 5:k]):
        return -1
    return 0


def detect_c2_pinbar(bars, i, params) -> int:
    sub = _tail(bars, i, TAIL_PIN)
    return _pin(sub, _atr(sub), len(sub) - 1, float(_p(params, "w", 0.66)))


def setup_c2_pinbar(bars, i, params, htf=None):
    sub = _tail(bars, i, TAIL_PIN)
    atr = _atr(sub)
    k = len(sub) - 1
    d = _pin(sub, atr, k, float(_p(params, "w", 0.66)))
    if not d or atr[k] is None:
        return None
    b = sub[k]
    sl = b["l"] - 0.1 * atr[k] if d > 0 else b["h"] + 0.1 * atr[k]
    return {"dir": d, "side": "BUY" if d > 0 else "SELL", "sl": sl, "tp": None, "tp_r": float(_p(params, "R", 2.0)),
            "atr": atr[k], "note": "pin bar"}


def filter_c2_pinbar(bars, i, side, entry, params, htf=None) -> bool:
    K = int(_p(params, "K", 4))
    w = float(_p(params, "w", 0.66))
    sub = _tail(bars, i, TAIL_PIN + K)
    atr = _atr(sub)
    k = len(sub) - 1
    s = 1 if side == "BUY" else -1
    same = opp = False
    for m in range(max(5, k - K + 1), k + 1):
        d = _pin(sub, atr, m, w)
        if d == -s:
            opp = True
        elif d == s and atr[m] is not None:
            tail_ext = sub[m]["l"] if s > 0 else sub[m]["h"]
            if abs(float(entry) - tail_ext) <= 1.0 * atr[m]:
                same = True
    return same and not opp


# ---------------------------------------------------------------------------------------------- P1_DOUBLE

def _double(sub, atr, k, L, tol) -> dict | None:
    """Neckline break at bar k (first close beyond the neckline after P2 is confirmed), on bars sub[:k+1]."""
    highs = [b["h"] for b in sub[:k + 1]]
    lows = [b["l"] for b in sub[:k + 1]]
    closes = [b["c"] for b in sub[:k + 1]]
    sh, sl = _swings(highs, lows, L, k)
    for d, piv in ((1, sl), (-1, sh)):
        if len(piv) < 2:
            continue
        j1, j2 = piv[-2], piv[-1]
        a = atr[j2]
        if a is None or not 5 <= j2 - j1 <= 60 or k < j2 + L or k - (j2 + L) > DOUBLE_MAX_WAIT:
            continue
        if d > 0:
            p1_, p2_ = lows[j1], lows[j2]
            if abs(p1_ - p2_) > tol * a:
                continue
            neck = max(highs[j1 + 1:j2])
            if neck < max(p1_, p2_) + 1.0 * a:
                continue
            if closes[k] > neck and all(closes[m] <= neck for m in range(j2 + L, k)):
                return {"dir": 1, "neck": neck, "ext": min(p1_, p2_), "atr": a, "k": k}
        else:
            p1_, p2_ = highs[j1], highs[j2]
            if abs(p1_ - p2_) > tol * a:
                continue
            neck = min(lows[j1 + 1:j2])
            if neck > min(p1_, p2_) - 1.0 * a:
                continue
            if closes[k] < neck and all(closes[m] >= neck for m in range(j2 + L, k)):
                return {"dir": -1, "neck": neck, "ext": max(p1_, p2_), "atr": a, "k": k}
    return None


def detect_p1_double(bars, i, params) -> int:
    sub = _tail(bars, i, TAIL_DOUBLE)
    r = _double(sub, _atr(sub), len(sub) - 1, int(_p(params, "L", 3)), float(_p(params, "tol", 0.6)))
    return r["dir"] if r else 0


def setup_p1_double(bars, i, params, htf=None):
    sub = _tail(bars, i, TAIL_DOUBLE)
    atr = _atr(sub)
    r = _double(sub, atr, len(sub) - 1, int(_p(params, "L", 3)), float(_p(params, "tol", 0.6)))
    if not r:
        return None
    d, neck, ext, a = r["dir"], r["neck"], r["ext"], r["atr"]
    sl = ext - d * 0.2 * a
    tp = neck + d * abs(neck - ext)  # measured move
    return {"dir": d, "side": "BUY" if d > 0 else "SELL", "sl": sl, "tp": tp, "tp_r": None, "cap_r": 4.0,
            "atr": atr[-1], "note": "dublu fund/vârf"}


def filter_p1_double(bars, i, side, entry, params, htf=None) -> bool:
    K = int(_p(params, "K", 12))
    L, tol = int(_p(params, "L", 3)), float(_p(params, "tol", 0.6))
    sub = _tail(bars, i, TAIL_DOUBLE + K)
    atr = _atr(sub)
    k = len(sub) - 1
    s = 1 if side == "BUY" else -1
    same = opp = False
    for m in range(max(0, k - K + 1), k + 1):
        r = _double(sub, atr, m, L, tol)
        if not r:
            continue
        if r["dir"] == -s:
            opp = True
        elif all((sub[x]["c"] - r["neck"]) * s > 0 for x in range(m, k + 1)):
            same = True
    return same and not opp


# ---------------------------------------------------------------------------------------------- P4_TRIANGLE

def _triangle(sub, atr, k, W, tol) -> dict | None:
    """Ascending / descending triangle breakout at bar k (first close beyond the flat side + 0.1*ATR)."""
    a = atr[k]
    if a is None:
        return None
    highs = [b["h"] for b in sub[:k + 1]]
    lows = [b["l"] for b in sub[:k + 1]]
    closes = [b["c"] for b in sub[:k + 1]]
    sh, sl = _swings(highs, lows, TRI_L, k)
    sh = [j for j in sh if j >= k - W]
    sl = [j for j in sl if j >= k - W]
    if len(sh) < 2 or len(sl) < 2:
        return None
    h1, h2 = sh[-2], sh[-1]
    l1, l2 = sl[-2], sl[-1]
    # ascending: flat highs, rising lows
    if abs(highs[h1] - highs[h2]) <= tol * a and lows[l2] - lows[l1] >= 0.2 * a:
        lvl = max(highs[h1], highs[h2])
        trig = lvl + 0.1 * a
        if closes[k] > trig and all(closes[m] <= lvl + 0.1 * (atr[m] or a) for m in range(h2 + 1, k)):
            return {"dir": 1, "level": lvl, "stop_ref": lows[l2], "height": lvl - min(lows[l1], lows[l2]),
                    "atr": a, "k": k}
    # descending: flat lows, falling highs
    if abs(lows[l1] - lows[l2]) <= tol * a and highs[h1] - highs[h2] >= 0.2 * a:
        lvl = min(lows[l1], lows[l2])
        trig = lvl - 0.1 * a
        if closes[k] < trig and all(closes[m] >= lvl - 0.1 * (atr[m] or a) for m in range(l2 + 1, k)):
            return {"dir": -1, "level": lvl, "stop_ref": highs[h2], "height": max(highs[h1], highs[h2]) - lvl,
                    "atr": a, "k": k}
    return None


def detect_p4_triangle(bars, i, params) -> int:
    sub = _tail(bars, i, TAIL_TRI)
    r = _triangle(sub, _atr(sub), len(sub) - 1, int(_p(params, "W", 60)), float(_p(params, "tol", 0.5)))
    return r["dir"] if r else 0


def setup_p4_triangle(bars, i, params, htf=None):
    sub = _tail(bars, i, TAIL_TRI)
    atr = _atr(sub)
    r = _triangle(sub, atr, len(sub) - 1, int(_p(params, "W", 60)), float(_p(params, "tol", 0.5)))
    if not r:
        return None
    d, a = r["dir"], r["atr"]
    close = sub[-1]["c"]
    return {"dir": d, "side": "BUY" if d > 0 else "SELL", "sl": r["stop_ref"] - d * 0.1 * a,
            "tp": close + d * r["height"], "tp_r": None, "cap_r": 3.0, "atr": a, "note": "triunghi"}


def filter_p4_triangle(bars, i, side, entry, params, htf=None) -> bool:
    K = int(_p(params, "K", 6))
    W, tol = int(_p(params, "W", 60)), float(_p(params, "tol", 0.5))
    sub = _tail(bars, i, TAIL_TRI + K)
    atr = _atr(sub)
    k = len(sub) - 1
    s = 1 if side == "BUY" else -1
    same = opp = False
    for m in range(max(0, k - K + 1), k + 1):
        r = _triangle(sub, atr, m, W, tol)
        if not r:
            continue
        if r["dir"] == -s:
            opp = True
        elif (sub[k]["c"] - r["level"]) * s > 0:
            same = True
    return same and not opp


# ---------------------------------------------------------------------------------------------- L3_PIVOTS

def broker_day(t: float):
    return datetime.fromtimestamp(t, BROKER_TZ).date()


def prev_day_ohlc(src: list[dict], t_bar: float) -> tuple | None:
    """(O, H, L, C) of the latest broker day before the day of a bar starting at t_bar, from the bars of `src`
    that start before t_bar (any timeframe <= D1). None when that day is not (fully) in src."""
    day = broker_day(t_bar)
    prev = None
    o = h = l = c = None
    seen_older = False
    for b in reversed(src):
        if b["t"] >= t_bar:
            continue
        d = broker_day(b["t"])
        if d >= day:
            continue
        if prev is None:
            prev = d
            h, l, c, o = b["h"], b["l"], b["c"], b["o"]
            continue
        if d != prev:
            seen_older = True
            break
        h, l, o = max(h, b["h"]), min(l, b["l"]), b["o"]
    if prev is None or not seen_older:
        return None  # the previous day may be cut by the window start
    return o, h, l, c


def pivot_levels(ohlc) -> dict:
    _, h, l, c = ohlc
    p = (h + l + c) / 3.0
    return {"P": p, "R1": 2 * p - l, "S1": 2 * p - h, "R2": p + (h - l), "S2": p - (h - l)}


def _levels(bars, i, htf):
    src = htf if htf else _tail(bars, i, 400)
    o = prev_day_ohlc(src, bars[i]["t"])
    return pivot_levels(o) if o else None


def _piv_event(bars, i, lv, mode) -> int:
    b = bars[i]
    if mode == "brk":
        if i < 1:
            return 0
        pc = bars[i - 1]["c"]
        if b["c"] > lv["R1"] >= pc:
            return 1
        if b["c"] < lv["S1"] <= pc:
            return -1
        return 0
    if b["l"] <= lv["S1"] < b["c"]:
        return 1
    if b["h"] >= lv["R1"] > b["c"]:
        return -1
    return 0


def detect_l3_pivots(bars, i, params, htf=None) -> int:
    lv = _levels(bars, i, htf)
    return _piv_event(bars, i, lv, str(_p(params, "mode", "rev"))) if lv else 0


def setup_l3_pivots(bars, i, params, htf=None):
    lv = _levels(bars, i, htf)
    a = atr_at(bars, i)
    if not lv or a is None:
        return None
    mode = str(_p(params, "mode", "rev"))
    d = _piv_event(bars, i, lv, mode)
    if not d:
        return None
    if mode == "brk":
        sl = lv["R1"] - a if d > 0 else lv["S1"] + a
        tp = lv["R2"] if d > 0 else lv["S2"]
    else:
        sl = max(lv["S2"], lv["S1"] - a) if d > 0 else min(lv["R2"], lv["R1"] + a)
        tp = lv["P"]
    return {"dir": d, "side": "BUY" if d > 0 else "SELL", "sl": sl, "tp": tp, "tp_r": None, "cap_r": None,
            "atr": a, "note": f"pivot zilnic ({mode})"}


def filter_l3_pivots(bars, i, side, entry, params, htf=None) -> bool:
    lv = _levels(bars, i, htf)
    a = atr_at(bars, i)
    if not lv or a is None:
        return False
    e = float(entry)
    mode = str(_p(params, "fmode", "side_only"))
    D = float(_p(params, "D", 0.5))
    c = bars[i]["c"]
    if side == "BUY":
        if e <= lv["P"]:
            return False
        if mode == "side_only":
            return True
        return lv["R1"] - e >= D * a or (c > lv["R1"] and lv["R2"] - e >= D * a)
    if e >= lv["P"]:
        return False
    if mode == "side_only":
        return True
    return e - lv["S1"] >= D * a or (c < lv["S1"] and e - lv["S2"] >= D * a)


# ---------------------------------------------------------------------------------------------- S2_FVG

def _fvg_zones(sub, atr, k, g) -> list[dict]:
    """Zones created by bars j in [k-50, k] (j = third bar), with their first touch / invalidation up to bar k."""
    out = []
    for j in range(max(2, k - 50), k + 1):
        a = atr[j - 2]
        if a is None:
            continue
        b0, b1, b2 = sub[j - 2], sub[j - 1], sub[j]
        if b2["l"] > b0["h"] and b2["l"] - b0["h"] >= g * a and b1["c"] - b1["o"] >= 1.0 * a:
            z = {"dir": 1, "j": j, "lo": b0["h"], "hi": b2["l"]}
        elif b2["h"] < b0["l"] and b0["l"] - b2["h"] >= g * a and b1["o"] - b1["c"] >= 1.0 * a:
            z = {"dir": -1, "j": j, "lo": b2["h"], "hi": b0["l"]}  # bearish: edge = b0.l (top), zone [b2.h, b0.l]
        else:
            continue
        z["touch"] = z["invalid"] = None
        for m in range(j + 1, k + 1):
            bm = sub[m]
            if z["dir"] > 0:
                if z["touch"] is None and bm["l"] <= z["hi"]:
                    z["touch"] = m
                if bm["c"] < z["lo"]:
                    z["invalid"] = m
                    break
            else:
                if z["touch"] is None and bm["h"] >= z["lo"]:
                    z["touch"] = m
                if bm["c"] > z["hi"]:
                    z["invalid"] = m
                    break
        out.append(z)
    return out


def _fvg_event(zones, k) -> dict | None:
    """First touch at bar k that closes back inside/above the edge (not invalidated at k)."""
    hits = [z for z in zones if z["touch"] == k and z["invalid"] is None]
    dirs = {z["dir"] for z in hits}
    if len(dirs) != 1:
        return None
    return max(hits, key=lambda z: z["j"])


def detect_s2_fvg(bars, i, params) -> int:
    sub = _tail(bars, i, TAIL_FVG)
    k = len(sub) - 1
    z = _fvg_event(_fvg_zones(sub, _atr(sub), k, float(_p(params, "g", 0.2))), k)
    return z["dir"] if z else 0


def setup_s2_fvg(bars, i, params, htf=None):
    sub = _tail(bars, i, TAIL_FVG)
    atr = _atr(sub)
    k = len(sub) - 1
    z = _fvg_event(_fvg_zones(sub, atr, k, float(_p(params, "g", 0.2))), k)
    if not z or atr[k] is None:
        return None
    d, a = z["dir"], atr[k]
    sl = z["lo"] - 0.3 * a if d > 0 else z["hi"] + 0.3 * a
    tp_mode = str(_p(params, "tp_mode", "2R"))
    tp = None
    if tp_mode == "swing":
        seg = sub[z["j"] - 1:k]
        tp = max(b["h"] for b in seg) if d > 0 else min(b["l"] for b in seg)
        if (tp - sub[k]["c"]) * d <= abs(sub[k]["c"] - sl):  # under 1R to the swing: fall back to 2R
            tp = None
    return {"dir": d, "side": "BUY" if d > 0 else "SELL", "sl": sl, "tp": tp, "tp_r": None if tp else 2.0,
            "cap_r": None, "atr": a, "note": "FVG"}


def filter_s2_fvg(bars, i, side, entry, params, htf=None) -> bool:
    K = int(_p(params, "K", 4))
    g = float(_p(params, "g", 0.2))
    variant = str(_p(params, "fvariant", "fill"))
    sub = _tail(bars, i, TAIL_FVG + K)
    atr = _atr(sub)
    k = len(sub) - 1
    s = 1 if side == "BUY" else -1
    zones = [z for z in _fvg_zones(sub, atr, k, g) if z["dir"] == s and z["invalid"] is None]
    if variant == "near":
        a = atr[k]
        if a is None:
            return False
        e = float(entry)
        return any(z["lo"] - 0.3 * a <= e <= z["hi"] + 0.3 * a for z in zones)
    for z in zones:
        t = z["touch"]
        if t is not None and t >= k - K + 1:
            # the touch bar must have closed back beyond the edge (same rule as the event)
            if (s > 0 and sub[t]["c"] > z["lo"]) or (s < 0 and sub[t]["c"] < z["hi"]):
                return True
    return False


# ---------------------------------------------------------------------------------------------- I2_ICHI_TK

def _ichi(sub):
    h, l, c = _hlc(sub)
    n = len(sub)

    def mid(p, m):
        if m < p - 1:
            return None
        return (max(h[m - p + 1:m + 1]) + min(l[m - p + 1:m + 1])) / 2.0

    ten = [mid(9, m) for m in range(n)]
    kij = [mid(26, m) for m in range(n)]
    sb = [mid(52, m) for m in range(n)]
    span_a = [None] * n
    span_b = [None] * n
    for m in range(26, n):
        if ten[m - 26] is not None and kij[m - 26] is not None:
            span_a[m] = (ten[m - 26] + kij[m - 26]) / 2.0
        span_b[m] = sb[m - 26]
    return ten, kij, span_a, span_b


def _tk_cross(ten, kij, sa, sb, m) -> tuple[int, int]:
    """(direction, strength 1 weak / 2 neutral / 3 strong) of a TK cross at m, (0, 0) when none."""
    if m < 1 or None in (ten[m], kij[m], ten[m - 1], kij[m - 1]):
        return 0, 0
    if ten[m] > kij[m] and ten[m - 1] <= kij[m - 1]:
        d = 1
    elif ten[m] < kij[m] and ten[m - 1] >= kij[m - 1]:
        d = -1
    else:
        return 0, 0
    if sa[m] is None or sb[m] is None:
        return d, 0
    x = (ten[m] + kij[m]) / 2.0
    top, bot = max(sa[m], sb[m]), min(sa[m], sb[m])
    if bot <= x <= top:
        return d, 2
    above = x > top
    return d, (3 if above == (d > 0) else 1)


STRENGTH = {"any": 1, "weak": 1, "neutral": 2, "strong": 3}


def detect_i2_ichi_tk(bars, i, params) -> int:
    sub = _tail(bars, i, TAIL_ICHI)
    ten, kij, sa, sb = _ichi(sub)
    d, st = _tk_cross(ten, kij, sa, sb, len(sub) - 1)
    return d if d and st >= STRENGTH[str(_p(params, "min_strength", "strong"))] else 0


def setup_i2_ichi_tk(bars, i, params, htf=None):
    sub = _tail(bars, i, TAIL_ICHI)
    ten, kij, sa, sb = _ichi(sub)
    k = len(sub) - 1
    d, st = _tk_cross(ten, kij, sa, sb, k)
    if not d or st < STRENGTH[str(_p(params, "min_strength", "strong"))]:
        return None
    a = _atr(sub)[k]
    if a is None:
        return None
    seg = sub[k - 9:k + 1]
    if d > 0:
        sl = min(kij[k], min(b["l"] for b in seg)) - 0.2 * a
    else:
        sl = max(kij[k], max(b["h"] for b in seg)) + 0.2 * a
    return {"dir": d, "side": "BUY" if d > 0 else "SELL", "sl": sl, "tp": None, "tp_r": float(_p(params, "R", 2.0)),
            "cap_r": None, "atr": a, "note": "cross Tenkan/Kijun"}


def filter_i2_ichi_tk(bars, i, side, entry, params, htf=None) -> bool:
    K = int(_p(params, "K", 0))
    sub = _tail(bars, i, TAIL_ICHI + K)
    ten, kij, sa, sb = _ichi(sub)
    k = len(sub) - 1
    s = 1 if side == "BUY" else -1
    if ten[k] is None or kij[k] is None:
        return False
    if K <= 0:
        return (ten[k] - kij[k]) * s > 0
    need = STRENGTH[str(_p(params, "min_strength", "any"))]
    for m in range(max(1, k - K + 1), k + 1):
        d, st = _tk_cross(ten, kij, sa, sb, m)
        if d == s and st >= need:
            return True
    return False


# ---------------------------------------------------------------------------------------------- I5_SQUEEZE

def _linreg_last(vals: list[float]) -> float:
    n = len(vals)
    xm = (n - 1) / 2.0
    ym = sum(vals) / n
    sxx = sum((x - xm) ** 2 for x in range(n))
    sxy = sum((x - xm) * (v - ym) for x, v in enumerate(vals))
    slope = sxy / sxx if sxx else 0.0
    return ym + slope * (n - 1 - xm)


def _squeeze(sub, kc_mult):
    h, l, c = _hlc(sub)
    n = len(sub)
    mid, up, lo = ind.bollinger(c, 20, 2.0)
    e20 = ind.ema(c, 20)
    a20 = ind.atr(h, l, c, 20)
    on = [None] * n
    for m in range(n):
        if None in (up[m], lo[m], e20[m], a20[m]):
            continue
        on[m] = up[m] < e20[m] + kc_mult * a20[m] and lo[m] > e20[m] - kc_mult * a20[m]
    dev = [None] * n
    for m in range(19, n):
        dmid = (max(h[m - 19:m + 1]) + min(l[m - 19:m + 1])) / 2.0
        dev[m] = c[m] - (dmid + mid[m]) / 2.0
    return {"c": c, "up": up, "lo": lo, "e20": e20, "on": on, "dev": dev}


def _mom(sq, m) -> float | None:
    seg = sq["dev"][m - 19:m + 1] if m >= 19 else []
    if len(seg) < 20 or any(v is None for v in seg):
        return None
    return _linreg_last(seg)


def _release(sq, m, S) -> int:
    on = sq["on"]
    if m < S or on[m] is None or on[m]:
        return 0
    if not all(on[x] for x in range(m - S, m)):
        return 0
    mo = _mom(sq, m)
    if mo is None:
        return 0
    if mo > 0 and sq["c"][m] > sq["up"][m]:
        return 1
    if mo < 0 and sq["c"][m] < sq["lo"][m]:
        return -1
    return 0


def detect_i5_squeeze(bars, i, params) -> int:
    sub = _tail(bars, i, TAIL_SQZ)
    sq = _squeeze(sub, float(_p(params, "kc_mult", 1.5)))
    return _release(sq, len(sub) - 1, int(_p(params, "S", 4)))


def setup_i5_squeeze(bars, i, params, htf=None):
    sub = _tail(bars, i, TAIL_SQZ)
    sq = _squeeze(sub, float(_p(params, "kc_mult", 1.5)))
    k = len(sub) - 1
    d = _release(sq, k, int(_p(params, "S", 4)))
    a = _atr(sub)[k]
    if not d or a is None:
        return None
    c = sq["c"][k]
    dist = max(abs(c - sq["e20"][k]), 1.5 * a)  # KC middle, never nearer than 1.5*ATR
    return {"dir": d, "side": "BUY" if d > 0 else "SELL", "sl": c - d * dist, "tp": None,
            "tp_r": float(_p(params, "R", 2.5)), "cap_r": None, "atr": a, "note": "squeeze release"}


def filter_i5_squeeze(bars, i, side, entry, params, htf=None) -> bool:
    K = int(_p(params, "K", 6))
    kc = float(_p(params, "kc_mult", 1.5))
    sub = _tail(bars, i, TAIL_SQZ + K)
    sq = _squeeze(sub, kc)
    k = len(sub) - 1
    if str(_p(params, "fvariant", "release")) == "veto_on":
        return sq["on"][k] is False
    S = int(_p(params, "S", 4))
    s = 1 if side == "BUY" else -1
    return any(_release(sq, m, S) == s for m in range(max(0, k - K + 1), k + 1))


# ---------------------------------------------------------------------------------------------- registry

PATTERNS = {
    "C2_PINBAR": (detect_c2_pinbar, setup_c2_pinbar, filter_c2_pinbar),
    "P1_DOUBLE": (detect_p1_double, setup_p1_double, filter_p1_double),
    "P4_TRIANGLE": (detect_p4_triangle, setup_p4_triangle, filter_p4_triangle),
    "L3_PIVOTS": (detect_l3_pivots, setup_l3_pivots, filter_l3_pivots),
    "S2_FVG": (detect_s2_fvg, setup_s2_fvg, filter_s2_fvg),
    "I2_ICHI_TK": (detect_i2_ichi_tk, setup_i2_ichi_tk, filter_i2_ichi_tk),
    "I5_SQUEEZE": (detect_i5_squeeze, setup_i5_squeeze, filter_i5_squeeze),
}

# pattern ids that need the higher-timeframe bars (pivot day levels)
NEEDS_HTF = {"L3_PIVOTS"}

__all__ = ["PATTERNS", "NEEDS_HTF", "atr_at", "prev_day_ohlc", "pivot_levels", "broker_day"] + [
    f"{k}_{n.lower()}" for n in PATTERNS for k in ("detect", "setup", "filter")]

