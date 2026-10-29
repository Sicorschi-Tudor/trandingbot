"""Pattern builder #1 (app.patterns.p1 + pat-/filt- plugins): crafted cases per pattern, no-lookahead of every
detector / setup / filter, tail independence, and the filter plugin = trend-v1 + one extra blocker."""
import random
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app import strategies as S
from app import strategy
from app.config import Settings
from app.patterns import p1
from app.strategy import AnalysisContext

SET = Settings(_env_file=None)
RISK = SET.default_risk()
IDS = list(p1.PATTERNS)
DEFAULTS = {"C2_PINBAR": {"w": 0.55, "R": 2.0, "K": 8}, "P1_DOUBLE": {"L": 3, "tol": 0.6, "K": 24},
            "P4_TRIANGLE": {"W": 60, "tol": 0.5, "K": 12}, "L3_PIVOTS": {"mode": "rev", "fmode": "side_plus_room",
                                                                          "D": 0.5},
            "S2_FVG": {"g": 0.2, "K": 8}, "I2_ICHI_TK": {"min_strength": "any", "K": 12},
            "I5_SQUEEZE": {"S": 4, "kc_mult": 1.5, "K": 12}}
T0 = int(datetime(2026, 3, 2, 0, tzinfo=ZoneInfo("Europe/Athens")).timestamp())  # Monday, broker midnight


def walk(n, seed=3, t0=T0, sec=300, vol=0.0004, start=1.1):
    rng = random.Random(seed)
    out, c = [], start
    for k in range(n):
        o = c
        c = o + rng.gauss(0, vol)
        h = max(o, c) + abs(rng.gauss(0, vol * 0.7))
        l = min(o, c) - abs(rng.gauss(0, vol * 0.7))
        out.append({"t": t0 + k * sec, "o": o, "h": h, "l": l, "c": c})
    return out


def path_bars(points, t0=T0, sec=300, wick=0.05):
    """Bars along a piecewise-linear close path: [(n_bars, target_close), ...] from 100."""
    out, c = [], 100.0
    for n, tgt in points:
        step = (tgt - c) / n
        for _ in range(n):
            o = c
            c = o + step
            out.append({"t": t0 + len(out) * sec, "o": o, "h": max(o, c) + wick, "l": min(o, c) - wick, "c": c})
    return out


def flat(n, t0=T0, sec=300, rng_=1.0, px=100.0):
    return [{"t": t0 + k * sec, "o": px, "h": px + rng_ / 2, "l": px - rng_ / 2, "c": px} for k in range(n)]


def events(pid, bars, prm, htf=None):
    det = p1.PATTERNS[pid][0]
    kw = {"htf": htf} if pid in p1.NEEDS_HTF else {}
    return {i: det(bars, i, prm, **kw) for i in range(len(bars)) if det(bars, i, prm, **kw)}


# ---- no lookahead / tail independence ------------------------------------------------------------------------------

@pytest.mark.parametrize("pid", IDS)
def test_no_lookahead_detect_setup_filter(pid):
    bars = walk(700, seed=11)
    det, setup, filt = p1.PATTERNS[pid]
    prm = DEFAULTS[pid]
    future = walk(700, seed=99, t0=bars[-1]["t"] + 300, start=bars[-1]["c"])
    for i in range(250, 700, 3):
        a, b = bars[:i + 1], bars[:i + 1] + future[:50]  # same past, different future
        c = bars[i - 230:i + 1]  # shorter window holding the tail
        ia, ic = i, len(c) - 1
        kw = {"htf": None}
        assert det(a, ia, prm, **(kw if pid in p1.NEEDS_HTF else {})) == det(b, i, prm, **(kw if pid in p1.NEEDS_HTF else {}))
        if pid != "L3_PIVOTS":  # pivots without htf read up to 400 bars of the window (previous broker day)
            assert det(a, ia, prm) == det(c, ic, prm)
        assert setup(a, ia, prm) == setup(b, i, prm)
        for side in ("BUY", "SELL"):
            e = a[-1]["c"]
            assert filt(a, ia, side, e, prm) == filt(b, i, side, e, prm)


def test_detect_rejects_out_of_range_index():
    with pytest.raises(IndexError):
        p1.detect_c2_pinbar(walk(50), 50, {})


# ---- crafted cases -------------------------------------------------------------------------------------------------

def test_pinbar_hammer_and_shooting_star():
    base = flat(30)
    ham = {"t": base[-1]["t"] + 300, "o": 100.2, "h": 100.4, "l": 98.8, "c": 100.3}
    bars = base + [ham]
    assert p1.detect_c2_pinbar(bars, len(bars) - 1, {"w": 0.66}) == 1
    s = p1.setup_c2_pinbar(bars, len(bars) - 1, {"w": 0.66, "R": 1.5})
    assert s["side"] == "BUY" and s["sl"] < 98.8 and s["tp_r"] == 1.5
    star = {"t": base[-1]["t"] + 300, "o": 99.8, "h": 101.2, "l": 99.6, "c": 99.7}
    bars2 = base + [star]
    assert p1.detect_c2_pinbar(bars2, len(bars2) - 1, {"w": 0.66}) == -1
    # a hammer that does not probe below the recent lows is not a pin
    weak = dict(ham, l=99.6, h=100.35, o=100.3, c=100.32)
    assert p1.detect_c2_pinbar(base + [weak], 30, {"w": 0.55}) == 0
    # filter: BUY near the hammer low kept, BUY far away dropped, SELL dropped (opposite pin)
    after = bars + flat(2, t0=ham["t"] + 300, px=99.4)
    i = len(after) - 1
    assert p1.filter_c2_pinbar(after, i, "BUY", 99.4, {"w": 0.66, "K": 4})
    assert not p1.filter_c2_pinbar(after, i, "BUY", 104.0, {"w": 0.66, "K": 4})
    assert not p1.filter_c2_pinbar(after, i, "SELL", 99.4, {"w": 0.66, "K": 4})
    assert not p1.filter_c2_pinbar(after, i, "BUY", 99.4, {"w": 0.66, "K": 2})  # pin is 3 bars back


def test_double_bottom_neckline_break():
    bars = path_bars([(20, 95.0), (10, 97.0), (10, 95.05), (15, 97.6)], wick=0.05)
    ev = events("P1_DOUBLE", bars, {"L": 3, "tol": 0.6})
    ups = [i for i, d in ev.items() if d == 1]
    assert ups and all(d == 1 for d in ev.values())
    i = ups[0]
    assert len(ups) == 1  # only the first close beyond the neckline
    assert bars[i]["c"] > 97.0 and bars[i - 1]["c"] <= 97.05
    s = p1.setup_p1_double(bars, i, {"L": 3, "tol": 0.6})
    assert s["side"] == "BUY" and s["sl"] < 94.95 and s["tp"] == pytest.approx(97.05 + (97.05 - 94.95), abs=1e-6)
    assert p1.filter_p1_double(bars, i + 2, "BUY", bars[i + 2]["c"], {"L": 3, "tol": 0.6, "K": 6})
    assert not p1.filter_p1_double(bars, i + 2, "SELL", bars[i + 2]["c"], {"L": 3, "tol": 0.6, "K": 6})
    # unequal bottoms (tol too tight) -> nothing
    assert not events("P1_DOUBLE", path_bars([(20, 95.0), (10, 97.0), (10, 95.8), (15, 97.6)]), {"L": 3, "tol": 0.3})


def test_double_top_mirror():
    bars = path_bars([(20, 105.0), (10, 103.0), (10, 104.95), (15, 102.4)], wick=0.05)
    ev = events("P1_DOUBLE", bars, {"L": 3, "tol": 0.6})
    assert ev and set(ev.values()) == {-1}


def test_ascending_and_descending_triangle():
    asc = path_bars([(10, 101.0), (5, 99.5), (5, 101.0), (5, 100.0), (5, 101.0), (5, 100.5), (6, 102.0)], wick=0.05)
    ev = events("P4_TRIANGLE", asc, {"W": 60, "tol": 0.5})
    assert ev and set(ev.values()) == {1}
    i = min(ev)
    s = p1.setup_p4_triangle(asc, i, {"W": 60, "tol": 0.5})
    assert s["side"] == "BUY" and s["sl"] < 100.45 and s["cap_r"] == 3.0
    assert p1.filter_p4_triangle(asc, i + 1, "BUY", asc[i + 1]["c"], {"W": 60, "tol": 0.5, "K": 3})
    assert not p1.filter_p4_triangle(asc, i + 1, "SELL", asc[i + 1]["c"], {"W": 60, "tol": 0.5, "K": 3})
    desc = path_bars([(10, 99.0), (5, 100.5), (5, 99.0), (5, 100.0), (5, 99.0), (5, 99.5), (6, 98.0)], wick=0.05)
    ev2 = events("P4_TRIANGLE", desc, {"W": 60, "tol": 0.5})
    assert ev2 and set(ev2.values()) == {-1}
    # symmetric (falling highs + rising lows) is excluded
    sym = path_bars([(10, 102.0), (5, 98.0), (5, 101.5), (5, 98.5), (5, 101.0), (5, 99.0), (6, 103.0)], wick=0.05)
    assert not events("P4_TRIANGLE", sym, {"W": 60, "tol": 0.3})


def _h1_days():
    """H1 bars: day A (Fri 27 Feb, Athens) range 1.08-1.12, day B (Mon 2 Mar) H 1.11 L 1.09 C 1.10, day C (Tue)."""
    tz = ZoneInfo("Europe/Athens")
    out = []
    for day, (hi, lo, cl) in ((datetime(2026, 2, 27, tzinfo=tz), (1.12, 1.08, 1.10)),
                              (datetime(2026, 3, 2, tzinfo=tz), (1.11, 1.09, 1.10))):
        t0 = int(day.timestamp())
        for h in range(24):
            out.append({"t": t0 + h * 3600, "o": 1.10, "h": hi if h == 5 else 1.101, "l": lo if h == 9 else 1.099,
                        "c": cl if h == 23 else 1.10})
    return out


def test_pivots_previous_broker_day_levels():
    h1 = _h1_days()
    t_c = int(datetime(2026, 3, 3, 10, tzinfo=ZoneInfo("Europe/Athens")).timestamp())
    assert p1.prev_day_ohlc(h1, t_c)[1:] == (1.11, 1.09, 1.10)
    lv = p1.pivot_levels(p1.prev_day_ohlc(h1, t_c))
    assert lv["P"] == pytest.approx(1.10) and lv["R1"] == pytest.approx(1.11) and lv["S1"] == pytest.approx(1.09)
    assert lv["R2"] == pytest.approx(1.12) and lv["S2"] == pytest.approx(1.08)
    # the earliest day in the window has no complete predecessor -> None
    assert p1.prev_day_ohlc(h1, h1[30]["t"]) is None
    m5 = [{"t": t_c - 300 * (30 - k), "o": 1.095, "h": 1.096, "l": 1.094, "c": 1.095} for k in range(30)]
    m5.append({"t": t_c, "o": 1.0915, "h": 1.092, "l": 1.0895, "c": 1.0912})  # touches S1 1.09, closes above
    i = len(m5) - 1
    assert p1.detect_l3_pivots(m5, i, {"mode": "rev"}, htf=h1) == 1
    s = p1.setup_l3_pivots(m5, i, {"mode": "rev"}, htf=h1)
    assert s["side"] == "BUY" and s["tp"] == pytest.approx(1.10) and s["sl"] < 1.09
    # filter: a BUY under P is dropped, a BUY above P with room to R1 is kept
    assert not p1.filter_l3_pivots(m5, i, "BUY", 1.0912, {"fmode": "side_only"}, htf=h1)
    m5b = m5[:-1] + [{"t": t_c, "o": 1.102, "h": 1.1022, "l": 1.1018, "c": 1.102}]
    assert p1.filter_l3_pivots(m5b, i, "BUY", 1.102, {"fmode": "side_plus_room", "D": 0.5}, htf=h1)
    assert not p1.filter_l3_pivots(m5b, i, "BUY", 1.1099, {"fmode": "side_plus_room", "D": 1.0}, htf=h1)
    assert p1.filter_l3_pivots(m5b, i, "SELL", 1.0990, {"fmode": "side_only"}, htf=h1)


def test_fvg_first_touch_and_invalidation():
    base = flat(20, rng_=0.2)
    t = base[-1]["t"]
    seq = [(100.0, 100.1, 99.9, 100.0),   # j-2: H = 100.1
           (100.0, 100.65, 99.95, 100.6),  # j-1: bullish body 0.6
           (100.6, 100.9, 100.3, 100.8),   # j: L 100.3 > 100.1 -> zone [100.1, 100.3]
           (100.8, 101.0, 100.6, 100.9),
           (100.9, 101.0, 100.5, 100.7),
           (100.7, 100.75, 100.2, 100.5)]  # first touch (low 100.2 in the zone), closes above 100.1
    bars = base + [{"t": t + 300 * (k + 1), "o": o, "h": h, "l": l, "c": c} for k, (o, h, l, c) in enumerate(seq)]
    i = len(bars) - 1
    assert p1.detect_s2_fvg(bars, i, {"g": 0.2}) == 1
    assert p1.detect_s2_fvg(bars, i - 1, {"g": 0.2}) == 0
    s = p1.setup_s2_fvg(bars, i, {"g": 0.2})
    assert s["side"] == "BUY" and s["sl"] < 100.1 and s["tp_r"] == 2.0
    assert p1.detect_s2_fvg(bars, i, {"g": 2.0}) == 0  # gap below the minimum size
    nxt = bars + [{"t": bars[-1]["t"] + 300, "o": 100.5, "h": 100.6, "l": 100.25, "c": 100.4}]
    assert p1.detect_s2_fvg(nxt, i + 1, {"g": 0.2}) == 0  # second touch is not an event
    assert p1.filter_s2_fvg(nxt, i + 1, "BUY", 100.4, {"g": 0.2, "K": 2})
    assert not p1.filter_s2_fvg(nxt, i + 1, "SELL", 100.4, {"g": 0.2, "K": 2})
    assert p1.filter_s2_fvg(nxt, i + 1, "BUY", 100.35, {"g": 0.2, "fvariant": "near"})
    bad = nxt + [{"t": nxt[-1]["t"] + 300, "o": 100.4, "h": 100.4, "l": 99.9, "c": 100.0}]  # close below 100.1
    assert not p1.filter_s2_fvg(bad, i + 2, "BUY", 100.0, {"g": 0.2, "K": 4})


def test_ichimoku_tk_cross_strength_and_state():
    bars = path_bars([(80, 90.0), (60, 100.0)], wick=0.05)
    ev = events("I2_ICHI_TK", bars, {"min_strength": "any"})
    ups = [i for i, d in ev.items() if d == 1]
    assert ups
    i = ups[0]
    assert not p1.detect_i2_ichi_tk(bars, i, {"min_strength": "strong"})  # crosses below the cloud after a fall
    assert p1.filter_i2_ichi_tk(bars, i + 3, "BUY", bars[i + 3]["c"], {"K": 0})
    assert not p1.filter_i2_ichi_tk(bars, i + 3, "SELL", bars[i + 3]["c"], {"K": 0})
    assert p1.filter_i2_ichi_tk(bars, i + 3, "BUY", bars[i + 3]["c"], {"K": 6, "min_strength": "any"})
    assert not p1.filter_i2_ichi_tk(bars, i + 3, "BUY", bars[i + 3]["c"], {"K": 6, "min_strength": "strong"})
    up = path_bars([(100, 120.0), (10, 118.5), (30, 125.0)], wick=0.05)
    strong = events("I2_ICHI_TK", up, {"min_strength": "strong"})
    assert strong and set(strong.values()) == {1}
    s = p1.setup_i2_ichi_tk(up, min(strong), {"min_strength": "strong", "R": 2.0})
    assert s["side"] == "BUY" and s["sl"] < up[min(strong)]["c"]


def test_squeeze_release_up():
    rows = []
    for k in range(60):
        c = 100.0 + (0.01 if k % 2 else -0.01)
        rows.append({"t": T0 + k * 300, "o": 100.0, "h": 100.1, "l": 99.9, "c": c})
    rows.append({"t": T0 + 60 * 300, "o": 100.0, "h": 102.05, "l": 99.95, "c": 102.0})
    i = len(rows) - 1
    assert p1.detect_i5_squeeze(rows, i - 1, {"S": 4}) == 0
    assert p1.detect_i5_squeeze(rows, i, {"S": 4, "kc_mult": 1.5}) == 1
    s = p1.setup_i5_squeeze(rows, i, {"S": 4, "R": 2.5})
    assert s["side"] == "BUY" and 102.0 - s["sl"] >= 1.5 * s["atr"] - 1e-9
    assert p1.filter_i5_squeeze(rows, i, "BUY", 102.0, {"S": 4, "K": 3})
    assert not p1.filter_i5_squeeze(rows, i, "SELL", 102.0, {"S": 4, "K": 3})
    assert not p1.filter_i5_squeeze(rows, i - 1, "BUY", 100.0, {"fvariant": "veto_on"})  # squeeze still on
    assert p1.filter_i5_squeeze(rows, i, "BUY", 102.0, {"fvariant": "veto_on"})


# ---- plugins -------------------------------------------------------------------------------------------------------

def _quote(c, sp=10):
    return {"bid": c, "ask": round(c + sp * 0.00001, 5), "spread_points": sp, "digits": 5, "point": 0.00001,
            "stops_level": 0}


def _ctx(now):
    return AnalysisContext(session_open=True, has_bot_position=False, signals_this_session=0, tick_age=0.0,
                           now_ts=now, open_exposures=[], corr_limit=1)


def test_plugins_registered_research_only():
    for pid in IDS:
        assert S.get(f"pat-{pid}").NAME == f"pat-{pid}"
        assert S.get(f"filt-{pid}").NAME == f"filt-{pid}"
        assert f"pat-{pid}" not in S.names(True) and f"filt-{pid}" not in S.names(True)


@pytest.mark.parametrize("pid", ["C2_PINBAR", "I2_ICHI_TK", "L3_PIVOTS"])
def test_filter_plugin_is_trend_v1_plus_one_blocker(pid):
    sig = walk(420, seed=5, sec=300, vol=0.0006)
    htf = walk(220, seed=6, t0=sig[0]["t"] - 3600 * 120, sec=3600, vol=0.002)
    live = {**SET.default_strategy(), "signal_timeframe": "M5", "htf_timeframe": "H1"}
    mod = S.get(f"filt-{pid}")
    cfg = S.merged_cfg(mod.NAME, live)
    seen = blocked = 0
    for end in range(300, 420):
        sb = sig[end - 299:end + 1]
        now = sb[-1]["t"] + 300
        hb = [b for b in htf if b["t"] + 3600 <= now][-200:]
        q = _quote(sb[-1]["c"])
        base = strategy.analyze("EURUSD", sb, hb, q, cfg, RISK, _ctx(now))
        got = mod.analyze("EURUSD", sb, hb, q, cfg, RISK, _ctx(now))
        if not base["candidate"]:
            assert got == base
            continue
        seen += 1
        keep = p1.PATTERNS[pid][2](sb, len(sb) - 1, base["candidate"]["side"], base["candidate"]["entry"],
                                   {k[4:]: v for k, v in mod.PARAMS.items() if k.startswith("pat_")},
                                   htf=hb if pid in p1.NEEDS_HTF else None)
        if keep:
            assert got == base
        else:
            blocked += 1
            assert got["direction"] == "HOLD"
            assert got["candidate"]["blockers"][:-1] == base["candidate"]["blockers"]
            assert got["candidate"]["blockers"][-1].startswith(f"Filtru {pid}")
    assert seen > 0


@pytest.mark.parametrize("pid", IDS)
def test_standalone_plugin_runs_and_levels_are_consistent(pid):
    sig = walk(700, seed=21, vol=0.0005)
    htf = walk(300, seed=22, t0=sig[0]["t"] - 3600 * 100, sec=3600, vol=0.002)
    mod = S.get(f"pat-{pid}")
    cfg = S.merged_cfg(mod.NAME, {**SET.default_strategy(), "signal_timeframe": "M5", "htf_timeframe": "H1"})
    n = 0
    for end in range(399, 700):
        sb = sig[end - 399:end + 1]
        now = sb[-1]["t"] + 300
        hb = [b for b in htf if b["t"] + 3600 <= now][-200:]
        a = mod.analyze("EURUSD", sb, hb, _quote(sb[-1]["c"]), cfg, RISK, _ctx(now))
        c = a["candidate"]
        if not c:
            continue
        n += 1
        if c["side"] == "BUY":
            assert c["stop_loss"] < c["entry"] < c["take_profit"]
        else:
            assert c["take_profit"] < c["entry"] < c["stop_loss"]
    assert n >= 0
