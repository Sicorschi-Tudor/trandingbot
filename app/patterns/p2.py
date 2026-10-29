"""Pattern library #2 (research only; nothing here is used by the live engine).

Patterns: C3_INSIDEBAR, P2_HS, L1_SR, L4_FIB618, S3_OB, I3_SUPERTREND, I6_VWAP.

Every pattern has
- scan_<id>(bars, params) -> list (one entry per bar, None or an event dict with "dir" = +1/-1 and the levels a
  strategy needs). Causal: entry i depends only on bars[:i+1] (closed bars), so scan(bars[:k]) == scan(bars)[:k]
  for every k (the no-lookahead tests pin this prefix invariance).
- detect_<id>(bars, i, params) -> +1 / -1 / 0: the event direction at bar i, computed from bars[:i+1] only.
- a filter for the live trend-v1 candidates: FILTERS[id] = (prepare(bars, params) -> state,
  verdict(state, i, side, entry, stop_loss, params) -> (keep, reason)). prepare is causal as well (the state at i
  only uses bars[:i+1]); verdict(i) looks at the state up to i only.

Bars are dicts with t,o,h,l,c (and optionally tv = tick volume, used by the VWAP). ATR = Wilder ATR14
(app.indicators.atr) unless a pattern says otherwise; swing points are fractals of half-width w (bar j is a swing
high when H[j] > every high of the w bars before it and >= every high of the w bars after it), confirmed at j+w.
"""
from __future__ import annotations

import math
from bisect import bisect_right
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .. import indicators as ind

BROKER_TZ = ZoneInfo("Europe/Athens")  # broker midnight = 00:00 Athens (checked on the D1 bars, see _disc0)


# ---- shared helpers ------------------------------------------------------------------------------------------

def _hlc(bars):
    return [b["h"] for b in bars], [b["l"] for b in bars], [b["c"] for b in bars]


def atr_series(bars, n: int = 14):
    h, l, c = _hlc(bars)
    return ind.atr(h, l, c, int(n))


def swing_points(bars, w: int):
    """(swing high indices, swing low indices) whose confirmation bar j+w exists in bars."""
    h, l, _ = _hlc(bars)
    n = len(bars)
    sh, sl = [], []
    for j in range(w, n - w):
        hj, lj = h[j], l[j]
        if hj > max(h[j - w:j]) and hj >= max(h[j + 1:j + w + 1]):
            sh.append(j)
        if lj < min(l[j - w:j]) and lj <= min(l[j + 1:j + w + 1]):
            sl.append(j)
    return sh, sl


def _p(params, key, default):
    v = (params or {}).get(key)
    return default if v is None else v


def recent_event(ev, i: int, k: int, d: int):
    """Most recent event of direction d in bars (i-k, i] (k >= 1), else None."""
    for j in range(i, max(-1, i - int(k)), -1):
        e = ev[j]
        if e and e["dir"] == d:
            return j, e
    return None


# ---- C3_INSIDEBAR: inside-bar breakout ----------------------------------------------------------------------

def scan_c3_insidebar(bars, params=None):
    """Mother bar j, then >= min_inside bars inside it (H <= H[j], L >= L[j]); mother range >= mother_atr*ATR[j].
    Event at the first bar b after the last inside bar m (b <= m+expire) whose close is beyond the mother
    (> H[j] bull, < L[j] bear). The latest mother wins when several fire on the same bar."""
    n_min = int(_p(params, "min_inside", 1))
    expire = int(_p(params, "expire", 6))
    mr = float(_p(params, "mother_atr", 0.6))
    h, l, c = _hlc(bars)
    atr = atr_series(bars, 14)
    N = len(bars)
    ev = [None] * N
    for j in range(N - 2):
        a = atr[j]
        hj, lj = h[j], l[j]
        if a is None or hj - lj < mr * a:
            continue
        k = j + 1
        while k < N and h[k] <= hj and l[k] >= lj:
            k += 1
        if k - j - 1 < n_min or k >= N:  # too few inside bars, or the inside streak is still running
            continue
        m = k - 1
        for b in range(m + 1, min(N, m + expire + 1)):
            if c[b] > hj:
                d = 1
            elif c[b] < lj:
                d = -1
            else:
                continue
            ev[b] = {"dir": d, "j": j, "mh": hj, "ml": lj, "n_inside": m - j, "atr": atr[b]}
            break
    return ev


def detect_c3_insidebar(bars, i, params=None) -> int:
    e = scan_c3_insidebar(bars[:i + 1], params)[i]
    return e["dir"] if e else 0


def _c3_prepare(bars, params):
    return {"ev": scan_c3_insidebar(bars, params)}


def _c3_verdict(st, i, side, entry, sl, params):
    d = 1 if side == "BUY" else -1
    k = int(_p(params, "K", 3))
    hit = recent_event(st["ev"], i, k, d)
    if hit is None:
        return False, f"nicio spargere inside-bar {side} în ultimele {k} bare"
    if int(_p(params, "strict", 0)):
        e = hit[1]
        if (d > 0 and sl > e["ml"]) or (d < 0 and sl < e["mh"]):
            return False, "SL-ul nu este dincolo de extrema opusă a barei-mamă"
    return True, ""


# ---- P2_HS: head & shoulders / inverse ----------------------------------------------------------------------

def _hs_side(bars, params, atr, piv, trough_fn, d):
    """d=-1: H&S top from swing highs (troughs = min lows between); d=+1: inverse from swing lows (mirror).
    Writes events into ev (dict index -> event)."""
    w = int(_p(params, "L", 3))
    tol = float(_p(params, "tol", 0.5))
    max_wait = int(_p(params, "max_wait", 60))
    h, l, c = _hlc(bars)
    N = len(bars)
    ext = h if d < 0 else l  # pivot price series
    out = {}
    for t in range(2, len(piv)):
        a, b, cc = piv[t - 2], piv[t - 1], piv[t]
        at = atr[cc]
        if at is None or b - a < 2 or cc - b < 2:
            continue
        LS, HD, RS = ext[a], ext[b], ext[cc]
        if d < 0:
            if not (HD > LS + 0.5 * at and HD > RS + 0.5 * at):
                continue
        else:
            if not (HD < LS - 0.5 * at and HD < RS - 0.5 * at):
                continue
        if abs(LS - RS) > tol * at:
            continue
        t1 = trough_fn(a + 1, b)
        t2 = trough_fn(b + 1, cc)
        y1 = l[t1] if d < 0 else h[t1]
        y2 = l[t2] if d < 0 else h[t2]
        slope = (y2 - y1) / (t2 - t1)
        neck_head = y1 + slope * (b - t1)
        height = abs(HD - neck_head)
        conf = cc + w
        nxt = piv[t + 1] + w if t + 1 < len(piv) else N  # the next pivot's confirmation replaces the triple
        for k in range(conf, min(N, conf + max_wait + 1, nxt)):
            if (d < 0 and h[k] > HD) or (d > 0 and l[k] < HD):
                break  # beyond the head: pattern invalid
            neck = y1 + slope * (k - t1)
            if (d < 0 and c[k] < neck) or (d > 0 and c[k] > neck):
                ak = atr[k] or at
                out[k] = {"dir": d, "head": HD, "rs": RS, "ls": LS, "neck": neck, "height": height,
                          "sl": RS + 0.2 * ak if d < 0 else RS - 0.2 * ak,
                          "target": neck - height if d < 0 else neck + height, "atr": ak}
                break
    return out


def scan_p2_hs(bars, params=None):
    w = int(_p(params, "L", 3))
    h, l, _ = _hlc(bars)
    atr = atr_series(bars, 14)
    sh, sl = swing_points(bars, w)
    N = len(bars)

    def min_low(x, y):
        return min(range(x, y), key=lambda q: l[q])

    def max_high(x, y):
        return max(range(x, y), key=lambda q: h[q])

    ev = [None] * N
    tops = _hs_side(bars, params, atr, sh, min_low, -1)
    bots = _hs_side(bars, params, atr, sl, max_high, +1)
    for k, e in tops.items():
        ev[k] = e
    for k, e in bots.items():
        ev[k] = None if k in tops else e  # both on one bar: ambiguous, no event
    return ev


def detect_p2_hs(bars, i, params=None) -> int:
    e = scan_p2_hs(bars[:i + 1], params)[i]
    return e["dir"] if e else 0


def _hs_prepare(bars, params):
    return {"ev": scan_p2_hs(bars, params)}


def _hs_verdict(st, i, side, entry, sl, params):
    d = 1 if side == "BUY" else -1
    k = int(_p(params, "K", 12))
    if str(_p(params, "mode", "veto")) == "keep":
        hit = recent_event(st["ev"], i, k, d)
        return (hit is not None, "" if hit else f"niciun H&S {side} în ultimele {k} bare")
    hit = recent_event(st["ev"], i, k, -d)
    return (hit is None, "" if hit is None else f"H&S opus confirmat acum {i - hit[0]} bare")


# ---- L1_SR: horizontal S/R from clustered swing pivots ------------------------------------------------------

class SRLevels:
    """Swing pivots (half-width w) and ATR of a series; levels(i) = clustered pivots confirmed by bar i."""

    def __init__(self, bars, params=None):
        self.w = int(_p(params, "w", 3))
        self.lookback = int(_p(params, "lookback", 200))
        self.width = float(_p(params, "width_atr", 0.3))
        self.n_touch = int(_p(params, "N", 2))
        self.h, self.l, self.c = _hlc(bars)
        self.atr = atr_series(bars, 14)
        sh, sl = swing_points(bars, self.w)
        piv = sorted([(j, self.h[j]) for j in sh] + [(j, self.l[j]) for j in sl])
        self.pj = [p[0] for p in piv]
        self.pp = [p[1] for p in piv]
        self._memo = {}

    def levels(self, i: int):
        """Sorted level prices at bar i (pivots j with j+w <= i and j > i-lookback)."""
        if i in self._memo:
            return self._memo[i]
        a = self.atr[i]
        if a is None:
            return []
        hi = bisect_right(self.pj, i - self.w)
        lo = bisect_right(self.pj, i - self.lookback)
        prices = sorted(self.pp[lo:hi])
        width = self.width * a
        out = []
        k = 0
        while k < len(prices):
            m = k
            while m + 1 < len(prices) and prices[m + 1] - prices[k] <= width:
                m += 1
            grp = prices[k:m + 1]
            if len(grp) >= self.n_touch:
                g = len(grp)
                out.append(grp[g // 2] if g % 2 else (grp[g // 2 - 1] + grp[g // 2]) / 2)
            k = m + 1
        if len(self._memo) > 4096:
            self._memo.clear()
        self._memo[i] = out
        return out


def _l1_event(sr: "SRLevels", o, i: int):
    a = sr.atr[i]
    if a is None:
        return None
    lv = sr.levels(i)
    if not lv:
        return None
    h, l, c = sr.h, sr.l, sr.c
    k = bisect_right(lv, c[i])
    sup = lv[k - 1] if k > 0 else None
    res = lv[k] if k < len(lv) else None
    if sup is not None and c[i] > o[i] and c[i] > sup and sup - 0.5 * a < l[i] <= sup + 0.2 * a:
        return {"dir": 1, "level": sup, "sl": sup - 0.5 * a, "next": res, "atr": a}
    if res is not None and c[i] < o[i] and c[i] < res and res - 0.2 * a <= h[i] < res + 0.5 * a:
        return {"dir": -1, "level": res, "sl": res + 0.5 * a, "next": sup, "atr": a}
    return None


def scan_l1_sr(bars, params=None):
    """Bounce off support: low within 0.2*ATR of the nearest support below the close (and not below it by
    0.5*ATR or more), close back above it with C > O -> +1; resistance rejection mirrored -> -1."""
    sr = SRLevels(bars, params)
    o = [b["o"] for b in bars]
    return [_l1_event(sr, o, i) for i in range(len(bars))]


def event_l1_sr(bars, i, params=None):
    """scan_l1_sr(bars[:i+1])[i] without computing the levels of every earlier bar."""
    sub = bars[:i + 1]
    return _l1_event(SRLevels(sub, params), [b["o"] for b in sub], i)


def detect_l1_sr(bars, i, params=None) -> int:
    e = event_l1_sr(bars, i, params)
    return e["dir"] if e else 0


def _sr_prepare(bars, params):
    return {"sr": SRLevels(bars, params)}


def _sr_verdict(st, i, side, entry, sl, params):
    sr = st["sr"]
    a = sr.atr[i]
    if a is None:
        return True, ""
    lv = sr.levels(i)
    k = bisect_right(lv, entry)
    below = lv[k - 1] if k > 0 else None
    above = lv[k] if k < len(lv) else None
    room = float(_p(params, "D", 1.0)) * a
    if side == "BUY":
        if above is not None and above - entry < room:
            return False, f"rezistență la {(above - entry) / a:.2f}×ATR deasupra intrării"
        if int(_p(params, "strict", 0)) and below is not None and sl > below:
            return False, "SL-ul nu este sub suportul cel mai apropiat"
    else:
        if below is not None and entry - below < room:
            return False, f"suport la {(entry - below) / a:.2f}×ATR sub intrare"
        if int(_p(params, "strict", 0)) and above is not None and sl < above:
            return False, "SL-ul nu este peste rezistența cea mai apropiată"
    return True, ""


# ---- L4_FIB618: 50-61.8% retracement in trend ---------------------------------------------------------------

def scan_l4_fib618(bars, params=None):
    """Up leg: latest confirmed swing high b (half-width 5) and the latest confirmed swing low a < b;
    A = L[a], B = max(H[a..b]); B-A >= leg_min*ATR and EMA50 > EMA200. Fires once per leg at the first bar after
    the swing high's confirmation whose low is in [B-(0.618+z)D, B-0.5D] and whose close is > B-0.5D, while no high
    above B and no earlier close below A+0.214D happened since b. Down leg mirrored (EMA50 < EMA200)."""
    w = int(_p(params, "w", 5))
    z = float(_p(params, "z", 0.0))
    leg_min = float(_p(params, "leg_min", 3.0))
    h, l, c = _hlc(bars)
    N = len(bars)
    atr = atr_series(bars, 14)
    e50, e200 = ind.ema(c, 50), ind.ema(c, 200)
    sh, sl = swing_points(bars, w)
    ev = [None] * N
    for d in (1, -1):
        hi_piv, lo_piv = (sh, sl) if d > 0 else (sl, sh)  # the leg ends at hi_piv (B), starts at lo_piv (A)
        conf_end = [j + w for j in hi_piv]
        state = None  # (b, key, fired, invalid, extreme beyond B since b)
        for i in range(N):
            kb = bisect_right(conf_end, i) - 1
            if kb < 0:
                continue
            b = hi_piv[kb]
            ka = bisect_right(lo_piv, b - 1) - 1
            if ka < 0 or lo_piv[ka] + w > i:
                continue
            a = lo_piv[ka]
            if state is None or state[0] != (a, b):
                seg = range(b + 1, i)  # bars after b, before i (state up to i-1)
                if d > 0:
                    B = max(h[a:b + 1])
                    A = l[a]
                else:
                    B = min(l[a:b + 1])
                    A = h[a]
                D = abs(B - A)
                inv = A + d * 0.214 * D
                beyond = any((h[q] > B) if d > 0 else (l[q] < B) for q in seg)
                invalid = any((c[q] < inv) if d > 0 else (c[q] > inv) for q in seg)
                state = [(a, b), A, B, D, inv, False, invalid, beyond]
            _, A, B, D, inv, fired, invalid, beyond = state
            beyond = beyond or ((h[i] > B) if d > 0 else (l[i] < B))
            state[7] = beyond
            at = atr[i]
            if not (fired or invalid or beyond) and at is not None and e200[i] is not None and D >= leg_min * at \
                    and ((e50[i] > e200[i]) if d > 0 else (e50[i] < e200[i])):
                lvl50 = B - d * 0.5 * D
                deep = B - d * (0.618 + z) * D
                if d > 0:
                    hit = deep <= l[i] <= lvl50 and c[i] > lvl50
                else:
                    hit = lvl50 <= h[i] <= deep and c[i] < lvl50
                if hit:
                    state[5] = True
                    ev[i] = None if ev[i] else {"dir": d, "A": A, "B": B, "D": D, "sl": inv - d * 0.2 * at,
                                                "tp": B, "l382": B - d * 0.382 * D, "atr": at}
            if (c[i] < inv) if d > 0 else (c[i] > inv):
                state[6] = True
    return ev


def detect_l4_fib618(bars, i, params=None) -> int:
    e = scan_l4_fib618(bars[:i + 1], params)[i]
    return e["dir"] if e else 0


def _fib_prepare(bars, params):
    return {"ev": scan_l4_fib618(bars, params)}


def _fib_verdict(st, i, side, entry, sl, params):
    d = 1 if side == "BUY" else -1
    k = int(_p(params, "K", 6))
    hit = recent_event(st["ev"], i, k, d)
    if hit is None:
        return False, f"nicio retragere 50–61,8% {side} în ultimele {k} bare"
    e = hit[1]
    if (d > 0 and entry >= e["l382"]) or (d < 0 and entry <= e["l382"]):
        return False, "intrarea este deja peste nivelul de 38,2% (vârful piciorului)"
    return True, ""


# ---- S3_OB: order block retest ------------------------------------------------------------------------------

def order_blocks(bars, params=None):
    """Order blocks: bar k = e-3 against the impulse (bearish for a bull OB) followed by 3 bars with
    C[e]-C[k] >= m*ATR[e] (sign mirrored) and a break of the latest swing high (half-width 3, confirmed by e, j <= k)
    by a high of k+1..e. Zone [L[k], max(O[k],C[k])] (bull). Fields: e (created, active from e+1), inval (first
    close beyond the far end after e, or None), retest (first bar <= e+life with the low in the zone and a close
    above L[k], before inval, or None)."""
    m = float(_p(params, "m", 1.5))
    life = int(_p(params, "life", 50))
    w = int(_p(params, "w", 3))
    o = [b["o"] for b in bars]
    h, l, c = _hlc(bars)
    N = len(bars)
    atr = atr_series(bars, 14)
    sh, sl = swing_points(bars, w)
    obs = []
    for e in range(3, N):
        k = e - 3
        a = atr[e]
        if a is None:
            continue
        for d in (1, -1):
            if d > 0 and not (c[k] < o[k] and c[e] - c[k] >= m * a):
                continue
            if d < 0 and not (c[k] > o[k] and c[k] - c[e] >= m * a):
                continue
            piv = sh if d > 0 else sl
            q = bisect_right(piv, min(k, e - w)) - 1
            if q < 0:
                continue
            ref = h[piv[q]] if d > 0 else l[piv[q]]
            if d > 0 and not max(h[k + 1:e + 1]) > ref:
                continue
            if d < 0 and not min(l[k + 1:e + 1]) < ref:
                continue
            if d > 0:
                lo, hi_ = l[k], max(o[k], c[k])
                ext = max(h[k + 1:e + 1])
            else:
                lo, hi_ = min(o[k], c[k]), h[k]
                ext = min(l[k + 1:e + 1])
            inval = retest = None
            for r in range(e + 1, min(N, e + life + 1)):
                if (d > 0 and c[r] < lo) or (d < 0 and c[r] > hi_):
                    inval = r
                    break
                if retest is None and ((d > 0 and l[r] <= hi_) or (d < 0 and h[r] >= lo)):
                    retest = r
                    # keep scanning for the invalidation (the veto filter needs it)
            obs.append({"dir": d, "k": k, "e": e, "lo": lo, "hi": hi_, "ext": ext, "inval": inval, "retest": retest})
    return obs, atr


def scan_s3_ob(bars, params=None):
    obs, atr = order_blocks(bars, params)
    ev = [None] * len(bars)
    for ob in obs:
        r = ob["retest"]
        if r is None:
            continue
        a = atr[r]
        d = ob["dir"]
        e = {"dir": d, "lo": ob["lo"], "hi": ob["hi"], "ext": ob["ext"],
             "sl": ob["lo"] - 0.2 * a if d > 0 else ob["hi"] + 0.2 * a, "atr": a}
        if ev[r] and ev[r]["dir"] != d:
            ev[r] = {"dir": 0}  # opposite retests on one bar: ambiguous
        elif not ev[r]:
            ev[r] = e
    return [x if x and x["dir"] else None for x in ev]


def detect_s3_ob(bars, i, params=None) -> int:
    e = scan_s3_ob(bars[:i + 1], params)[i]
    return e["dir"] if e else 0


def _ob_prepare(bars, params):
    obs, atr = order_blocks(bars, params)
    return {"obs": sorted(obs, key=lambda x: x["e"]), "es": sorted(x["e"] for x in obs),
            "ev": scan_s3_ob(bars, params), "life": int(_p(params, "life", 50))}


def _ob_verdict(st, i, side, entry, sl, params):
    d = 1 if side == "BUY" else -1
    k = int(_p(params, "K", 4))
    life = st["life"]
    lo_i = bisect_right(st["es"], i - life - 1)
    hi_i = bisect_right(st["es"], i - 1)  # created at e <= i-1 -> active from e+1 <= i
    inside_same = False
    for ob in st["obs"][lo_i:hi_i]:
        if ob["inval"] is not None and ob["inval"] <= i:
            continue
        inside = ob["lo"] <= entry <= ob["hi"]
        if ob["dir"] == -d and inside and (ob["retest"] is None or ob["retest"] >= i):
            return False, "intrarea este într-un order block opus nemitigat"
        if ob["dir"] == d and inside:
            inside_same = True
    if inside_same or recent_event(st["ev"], i, k, d) is not None:
        return True, ""
    return False, f"niciun retest de order block {side} în ultimele {k} bare"


# ---- I3_SUPERTREND -------------------------------------------------------------------------------------------

def supertrend(bars, params=None):
    """(direction list +1/-1/0, line list, flip list) — standard Supertrend on closed bars (Wilder ATR n)."""
    n = int(_p(params, "n", 10))
    mult = float(_p(params, "mult", 3.0))
    h, l, c = _hlc(bars)
    atr = ind.atr(h, l, c, n)
    N = len(bars)
    dirs, line, flip = [0] * N, [None] * N, [False] * N
    fu = fl = None
    dprev = 0
    for i in range(N):
        a = atr[i]
        if a is None:
            continue
        mid = (h[i] + l[i]) / 2
        bu, bl = mid + mult * a, mid - mult * a
        if fu is None:
            nfu, nfl = bu, bl
            d = 1 if c[i] >= mid else -1
        else:
            nfu = bu if (bu < fu or c[i - 1] > fu) else fu
            nfl = bl if (bl > fl or c[i - 1] < fl) else fl
            d = dprev
            if dprev < 0 and c[i] > fu:
                d = 1
            elif dprev > 0 and c[i] < fl:
                d = -1
            flip[i] = d != dprev
        fu, fl = nfu, nfl
        dirs[i] = d
        line[i] = fl if d > 0 else fu
        dprev = d
    return dirs, line, flip


def scan_i3_supertrend(bars, params=None):
    dirs, line, flip = supertrend(bars, params)
    return [{"dir": dirs[i], "line": line[i]} if flip[i] else None for i in range(len(bars))]


def detect_i3_supertrend(bars, i, params=None) -> int:
    e = scan_i3_supertrend(bars[:i + 1], params)[i]
    return e["dir"] if e else 0


def _st_prepare(bars, params):
    dirs, line, flip = supertrend(bars, params)
    return {"dirs": dirs, "flip": flip}


def _st_verdict(st, i, side, entry, sl, params):
    d = 1 if side == "BUY" else -1
    if st["dirs"][i] != d:
        return False, "Supertrend în direcția opusă"
    k = int(_p(params, "K", 0))
    if k > 0 and not any(st["flip"][j] for j in range(i, max(-1, i - k), -1)):
        return False, f"trendul Supertrend nu e proaspăt (fără flip în ultimele {k} bare)"
    return True, ""


# ---- I6_VWAP: session VWAP ----------------------------------------------------------------------------------

def session_key(t: int, anchor: str):
    """Session id of a bar opening at UTC t: broker day (00:00 Athens) or the London day starting 07:00 UTC."""
    if anchor == "london":
        return (datetime.fromtimestamp(t, timezone.utc) - timedelta(hours=7)).date()
    return datetime.fromtimestamp(t, BROKER_TZ).date()


def session_end_ts(t: int, anchor: str) -> int:
    """UTC timestamp of the end of the session that contains the instant t."""
    if anchor == "london":
        d = (datetime.fromtimestamp(t, timezone.utc) - timedelta(hours=7)).date() + timedelta(days=1)
        return int(datetime(d.year, d.month, d.day, 7, tzinfo=timezone.utc).timestamp())
    d = datetime.fromtimestamp(t, BROKER_TZ).date() + timedelta(days=1)
    return int(datetime(d.year, d.month, d.day, tzinfo=BROKER_TZ).timestamp())


def vwap_series(bars, anchor: str = "day"):
    """(vwap, sigma, bars in session incl. this one, session low, session high) per bar; weights = tv (tick
    volume; 1 when missing or 0)."""
    N = len(bars)
    vw, sg, nb, slo, shi = [None] * N, [None] * N, [0] * N, [None] * N, [None] * N
    key = None
    sv = stv = st2 = 0.0
    cnt = 0
    lo = hi = None
    for i, b in enumerate(bars):
        k = session_key(b["t"], anchor)
        if k != key:
            key, sv, stv, st2, cnt, lo, hi = k, 0.0, 0.0, 0.0, 0, None, None
        v = float(b.get("tv") or b.get("v") or 0.0) or 1.0
        tp = (b["h"] + b["l"] + b["c"]) / 3.0
        sv += v
        stv += tp * v
        st2 += tp * tp * v
        cnt += 1
        lo = b["l"] if lo is None else min(lo, b["l"])
        hi = b["h"] if hi is None else max(hi, b["h"])
        m = stv / sv
        vw[i] = m
        sg[i] = math.sqrt(max(st2 / sv - m * m, 0.0))
        nb[i] = cnt
        slo[i], shi[i] = lo, hi
    return vw, sg, nb, slo, shi


def adx_series(bars, n: int = 14):
    """Wilder ADX(n)."""
    h, l, c = _hlc(bars)
    N = len(bars)
    out = [None] * N
    if N < 2 * n + 1:
        return out
    tr, pdm, mdm = [0.0] * N, [0.0] * N, [0.0] * N
    for i in range(1, N):
        up, dn = h[i] - h[i - 1], l[i - 1] - l[i]
        pdm[i] = up if up > dn and up > 0 else 0.0
        mdm[i] = dn if dn > up and dn > 0 else 0.0
        tr[i] = max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
    s_tr, s_p, s_m = sum(tr[1:n + 1]), sum(pdm[1:n + 1]), sum(mdm[1:n + 1])
    dx = [None] * N
    for i in range(n, N):
        if i > n:
            s_tr = s_tr - s_tr / n + tr[i]
            s_p = s_p - s_p / n + pdm[i]
            s_m = s_m - s_m / n + mdm[i]
        if s_tr <= 0:
            dx[i] = 0.0
            continue
        pdi, mdi = 100 * s_p / s_tr, 100 * s_m / s_tr
        dx[i] = 0.0 if pdi + mdi == 0 else 100 * abs(pdi - mdi) / (pdi + mdi)
    a = sum(dx[n:2 * n]) / n
    out[2 * n - 1] = a
    for i in range(2 * n, N):
        a = (a * (n - 1) + dx[i]) / n
        out[i] = a
    return out


def scan_i6_vwap(bars, params=None):
    """Reversion signal at bar i: bar i-1 (same session, >= min_bars into it) closed beyond VWAP -/+ k*sigma and
    bar i closes back toward VWAP (C[i] > C[i-1] long) while still on the stretched side of VWAP. Carries ADX14,
    the session low/high and the VWAP (TP) for the strategy."""
    anchor = str(_p(params, "anchor", "day"))
    k = float(_p(params, "k", 1.5))
    min_bars = int(_p(params, "min_bars", 12))
    vw, sg, nb, slo, shi = vwap_series(bars, anchor)
    adx = adx_series(bars, 14)
    atr = atr_series(bars, 14)
    c = [b["c"] for b in bars]
    ev = [None] * len(bars)
    for i in range(1, len(bars)):
        if nb[i] < 2 or nb[i - 1] < min_bars or sg[i - 1] <= 0:
            continue
        if c[i - 1] < vw[i - 1] - k * sg[i - 1] and c[i] > c[i - 1] and c[i] < vw[i]:
            d = 1
        elif c[i - 1] > vw[i - 1] + k * sg[i - 1] and c[i] < c[i - 1] and c[i] > vw[i]:
            d = -1
        else:
            continue
        ev[i] = {"dir": d, "vwap": vw[i], "sigma": sg[i], "adx": adx[i], "atr": atr[i], "s_lo": slo[i],
                 "s_hi": shi[i], "t": bars[i]["t"]}
    return ev


def detect_i6_vwap(bars, i, params=None) -> int:
    e = scan_i6_vwap(bars[:i + 1], params)[i]
    return e["dir"] if e else 0


def _vw_prepare(bars, params):
    vw, sg, nb, _, _ = vwap_series(bars, str(_p(params, "anchor", "day")))
    return {"vw": vw, "sg": sg, "nb": nb, "c": [b["c"] for b in bars]}


def _vw_verdict(st, i, side, entry, sl, params):
    d = 1 if side == "BUY" else -1
    vw, sg = st["vw"][i], st["sg"][i]
    if vw is None:
        return True, ""
    if str(_p(params, "mode", "side")) == "side":
        if (d > 0 and entry <= vw) or (d < 0 and entry >= vw):
            return False, "prețul este de partea greșită a VWAP-ului de sesiune"
        return True, ""
    if st["nb"][i] < int(_p(params, "min_bars", 12)):
        return True, ""  # too early in the session for a meaningful sigma
    k = float(_p(params, "k", 1.5))
    c = st["c"][i]
    if d > 0 and (entry > vw + k * sg or c <= vw - 2 * sg):
        return False, f"BUY supraextins față de VWAP (> +{k:g}σ) sau sub VWAP-2σ"
    if d < 0 and (entry < vw - k * sg or c >= vw + 2 * sg):
        return False, f"SELL supraextins față de VWAP (< -{k:g}σ) sau peste VWAP+2σ"
    return True, ""


# ---- registry ------------------------------------------------------------------------------------------------

SCANS = {
    "C3_INSIDEBAR": scan_c3_insidebar, "P2_HS": scan_p2_hs, "L1_SR": scan_l1_sr, "L4_FIB618": scan_l4_fib618,
    "S3_OB": scan_s3_ob, "I3_SUPERTREND": scan_i3_supertrend, "I6_VWAP": scan_i6_vwap,
}
DETECTS = {
    "C3_INSIDEBAR": detect_c3_insidebar, "P2_HS": detect_p2_hs, "L1_SR": detect_l1_sr,
    "L4_FIB618": detect_l4_fib618, "S3_OB": detect_s3_ob, "I3_SUPERTREND": detect_i3_supertrend,
    "I6_VWAP": detect_i6_vwap,
}


def event_at(pid: str, bars, i: int, params=None):
    """The event of pattern pid at bar i from bars[:i+1] (None when there is none)."""
    if pid == "L1_SR":
        return event_l1_sr(bars, i, params)
    return SCANS[pid](bars[:i + 1], params)[i]


FILTERS = {
    "C3_INSIDEBAR": (_c3_prepare, _c3_verdict), "P2_HS": (_hs_prepare, _hs_verdict), "L1_SR": (_sr_prepare, _sr_verdict),
    "L4_FIB618": (_fib_prepare, _fib_verdict), "S3_OB": (_ob_prepare, _ob_verdict),
    "I3_SUPERTREND": (_st_prepare, _st_verdict), "I6_VWAP": (_vw_prepare, _vw_verdict),
}

__all__ = ["SCANS", "DETECTS", "FILTERS", "swing_points", "supertrend", "vwap_series", "adx_series", "SRLevels",
           "order_blocks", "event_at", "session_end_ts", "session_key", "recent_event"] + [f"detect_{x.lower()}" for x in SCANS]
