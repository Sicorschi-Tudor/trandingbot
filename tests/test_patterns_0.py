"""Pattern builder #0 (app.patterns.p0 + pat-/filt- research plugins): crafted cases, mirror symmetry, no lookahead."""
from __future__ import annotations

import random

import pytest

from app import strategy
from app.patterns import p0
from app.strategies import get

X = 2.2  # mirror axis (price -> X - price)


def bar(o, h, l, c, t=0):
    return {"t": t, "o": o, "h": h, "l": l, "c": c}


def flat(n, px=1.1000, half=0.0005, t0=0):
    return [bar(px, px + half, px - half, px, t0 + k * 300) for k in range(n)]


def retime(bars):
    return [dict(b, t=k * 300) for k, b in enumerate(bars)]


def mirror(bars):
    return [{"t": b["t"], "o": X - b["o"], "h": X - b["l"], "l": X - b["h"], "c": X - b["c"]} for b in bars]


def walk(n=600, seed=7, px=1.1):
    rnd = random.Random(seed)
    out = []
    c = px
    for k in range(n):
        o = c
        c = o + rnd.gauss(0, 0.0006) + (0.0002 if (k // 80) % 2 else -0.0002)
        h = max(o, c) + abs(rnd.gauss(0, 0.0003))
        l_ = min(o, c) - abs(rnd.gauss(0, 0.0003))
        out.append(bar(o, h, l_, c, k * 300))
    return out


# ---- crafted cases -----------------------------------------------------------------------------------------------

def test_engulf_bull_bear_and_size():
    bars = flat(30)
    bars += [bar(1.0990, 1.0992, 1.0978, 1.0980), bar(1.0978, 1.0997, 1.0970, 1.0996)]
    bars = retime(bars)
    i = len(bars) - 1
    assert p0.detect_c1_engulf(bars, i, {"m": 0.5}) == 1
    assert p0.detect_c1_engulf(mirror(bars), i, {"m": 0.5}) == -1
    assert p0.detect_c1_engulf(bars, i, {"m": 5.0}) == 0  # body too small vs ATR
    nb = [dict(b) for b in bars]
    nb[-1]["c"] = 1.0985  # does not cover the previous open
    assert p0.detect_c1_engulf(nb, i, {"m": 0.1}) == 0
    nb = [dict(b) for b in bars]
    nb[-3]["l"] = 1.0960  # not the lowest low of the last 5 bars
    assert p0.detect_c1_engulf(nb, i, {"m": 0.5}) == 0
    assert p0.detect_c1_engulf(nb, i, {"m": 0.5, "local": False}) == 1


def test_three_soldiers_and_crows():
    bars = retime(flat(30) + [bar(1.1000, 1.1013, 1.0999, 1.1012), bar(1.1006, 1.1021, 1.1005, 1.1020),
                               bar(1.1015, 1.1031, 1.1014, 1.1030)])
    i = len(bars) - 1
    assert p0.detect_c4_3soldiers(bars, i, {"b": 0.3}) == 1
    assert p0.detect_c4_3soldiers(mirror(bars), i, {"b": 0.3}) == -1
    nb = [dict(b) for b in bars]
    nb[-1]["h"] = 1.1060  # long upper wick
    assert p0.detect_c4_3soldiers(nb, i, {"b": 0.3}) == 0
    nb = [dict(b) for b in bars]
    nb[-2]["o"] = 1.0990  # opens below the previous body
    nb[-2]["l"] = 1.0989
    assert p0.detect_c4_3soldiers(nb, i, {"b": 0.3}) == 0


def _flag_bars():
    bars = flat(40, half=0.0002)
    c = 1.1000
    for _ in range(5):  # pole: +50 pips
        bars.append(bar(c, c + 0.0011, c - 0.0001, c + 0.0010))
        c += 0.0010
    for k in range(5):  # flag drifting down
        hi = 1.1050 - 0.0002 * k
        bars.append(bar(hi - 0.0004, hi, hi - 0.0012, hi - 0.0006))
    bars.append(bar(1.1040, 1.1062, 1.1039, 1.1060))  # breakout
    return retime(bars)


def test_flag_breakout_and_levels():
    bars = _flag_bars()
    i = len(bars) - 1
    assert p0.detect_p3_flag(bars, i, {"p": 2.0, "max_flag_bars": 8}) == 1
    assert p0.detect_p3_flag(mirror(bars), i, {"p": 2.0, "max_flag_bars": 8}) == -1
    lv = p0.p3_levels(bars, i, {"p": 2.0, "max_flag_bars": 8})
    assert lv["side"] == 1 and lv["sl"] < min(b["l"] for b in bars[-6:-1]) and lv["height"] > 0
    assert p0.detect_p3_flag(bars, i, {"p": 20.0, "max_flag_bars": 8}) == 0  # pole too small
    assert p0.detect_p3_flag(bars, i - 1, {"p": 2.0, "max_flag_bars": 8}) == 0  # no breakout yet


def test_round_level_rejection_and_filter():
    b = bar(1.1012, 1.1015, 1.0995, 1.1008)
    assert p0.detect_l2_round([b], 0, {"step": 50}) == 1
    assert p0.detect_l2_round(mirror([b]), 0, {"step": 50}) == -1
    assert p0.detect_l2_round([bar(1.1012, 1.1015, 1.1001, 1.1008)], 0, {"step": 50}) == 0
    assert p0.round_levels(150.23, 50) == pytest.approx((150.0, 150.5))
    base = [bar(1.1040, 1.1045, 1.1035, 1.1040, k * 300) for k in range(30)]  # ATR = 0.0010
    i = len(base) - 1
    prm = {"step": 50, "D": 0.5}
    assert p0.filter_l2_round(base, i, "BUY", 1.1040, prm) is True      # 1.1050 is 1.0 ATR away
    assert p0.filter_l2_round(base, i, "BUY", 1.1047, prm) is False     # 1.1050 overhead within 0.5 ATR
    assert p0.filter_l2_round(base, i, "BUY", 1.1002, prm) is True      # just above support
    assert p0.filter_l2_round(base, i, "SELL", 1.1003, prm) is False    # 1.1000 just below
    assert p0.filter_l2_round(base, i, "SELL", 1.1048, prm) is True     # just below resistance


def test_sweep_of_swing_low():
    bars = flat(40)
    bars[30] = bar(1.1000, 1.1005, 1.0980, 1.0998)  # swing low at 1.0980
    bars.append(bar(1.0998, 1.1004, 1.0970, 1.0990))  # pierces 1.0980, closes back above
    bars = retime(bars)
    i = len(bars) - 1
    assert p0.detect_s1_sweep(bars, i, {"L": 3, "min_pierce": 0.1}) == 1
    assert p0.detect_s1_sweep(mirror(bars), i, {"L": 3, "min_pierce": 0.1}) == -1
    assert p0.detect_s1_sweep(bars, i, {"L": 3, "min_pierce": 3.0}) == 0  # pierce too small
    lv = p0.s1_levels(bars, i, {"L": 3, "min_pierce": 0.1})
    assert lv["side"] == 1 and lv["sl"] < 1.0970
    nb = [dict(b) for b in bars]
    nb[-1]["c"] = 1.0975  # closes below the level: a breakdown, not a sweep
    assert p0.detect_s1_sweep(nb, i, {"L": 3, "min_pierce": 0.1}) == 0
    young = flat(40)
    young[37] = bar(1.1000, 1.1005, 1.0980, 1.0998)  # swing confirmed too recently (< 5 bars old)
    young.append(bar(1.0998, 1.1004, 1.0970, 1.0990))
    assert p0.detect_s1_sweep(retime(young), len(young) - 1, {"L": 1, "min_pierce": 0.1}) == 0


def test_ichimoku_breakout_and_confirmations():
    bars = flat(130)
    bars.append(bar(1.1000, 1.1025, 1.0999, 1.1020))
    bars = retime(bars)
    i = len(bars) - 1
    assert p0.detect_i1_ichi_cloud(bars, i, {"conf": "none"}) == 1
    assert p0.detect_i1_ichi_cloud(bars, i, {"conf": "chikou"}) == 1
    assert p0.detect_i1_ichi_cloud(bars, i, {"conf": "full"}) == 0  # future cloud flat, not green
    assert p0.detect_i1_ichi_cloud(mirror(bars), i, {"conf": "none"}) == -1
    assert p0.ichi_state(bars, i, {}) == 1 and p0.ichi_state(bars, i - 1, {}) == 0
    assert p0.filter_i1_ichi_cloud(bars, i, "BUY", {"K": 0}) is True
    assert p0.filter_i1_ichi_cloud(bars, i, "SELL", {"K": 0}) is False
    assert p0.ichimoku_at(bars, 50, 2) is None  # x2 periods need more history


def test_adx_direction_and_cross():
    up = flat(60, half=0.0003)
    c = 1.1000
    for _ in range(60):
        up.append(bar(c, c + 0.0009, c - 0.0001, c + 0.0008))
        c += 0.0008
    up = retime(up)
    i = len(up) - 1
    assert p0.adx_state(up, i, {"T": 25}) == 1
    assert p0.adx_state(mirror(up), i, {"T": 25}) == -1
    assert p0.filter_i4_adx(up, i, "BUY", {"T": 25}) and not p0.filter_i4_adx(up, i, "SELL", {"T": 25})
    fired = [p0.detect_i4_adx(up, j, {"T": 25}) for j in range(60, i + 1)]
    assert 1 in fired and -1 not in fired
    assert p0.adx_state(flat(200), 199, {"T": 20}) == 0


# ---- no lookahead ------------------------------------------------------------------------------------------------

PARAMS = {
    "C1_ENGULF": {"m": 0.3}, "C4_3SOLDIERS": {"b": 0.3}, "P3_FLAG": {"p": 2.0, "max_flag_bars": 12},
    "L2_ROUND": {"step": 50}, "S1_SWEEP": {"L": 3, "min_pierce": 0.1}, "I1_ICHI_CLOUD": {"conf": "none", "scale": 1},
    "I4_ADX": {"T": 20},
}


@pytest.mark.parametrize("pid", p0.PATTERN_IDS)
def test_no_lookahead_detectors(pid):
    bars = walk(500)
    det = p0.DETECTORS[pid]
    prm = PARAMS[pid]
    future = [dict(b, h=b["h"] + 0.01, l=b["l"] - 0.01, c=b["c"] + 0.005) for b in bars]
    for i in range(0, 499, 3):
        v = det(bars, i, prm)
        assert v in (-1, 0, 1)
        assert det(bars[:i + 1], i, prm) == v
        assert det(bars[:i + 1] + future[i + 1:], i, prm) == v


@pytest.mark.parametrize("pid", p0.PATTERN_IDS)
def test_no_lookahead_filters(pid):
    bars = walk(400, seed=11)
    prm = {**PARAMS[pid], "K": 3, "D": 0.5}
    for i in range(150, 399, 7):
        for side in ("BUY", "SELL"):
            e = bars[i]["c"]
            v = p0.filter_keep(pid, bars, i, side, e, prm)
            assert p0.filter_keep(pid, bars[:i + 1], i, side, e, prm) == v


def test_event_filter_modes():
    bars = flat(30)
    bars += [bar(1.0990, 1.0992, 1.0978, 1.0980), bar(1.0978, 1.0997, 1.0970, 1.0996)]
    bars += flat(2, px=1.0996)
    bars = retime(bars)
    i = len(bars) - 1
    prm = {"m": 0.5, "K": 3}
    assert p0.filter_keep("C1_ENGULF", bars, i, "BUY", 1.1, prm) is True
    assert p0.filter_keep("C1_ENGULF", bars, i, "SELL", 1.1, prm) is False
    assert p0.filter_keep("C1_ENGULF", bars, i, "BUY", 1.1, {**prm, "K": 1}) is False  # outside the window
    assert p0.filter_keep("C1_ENGULF", bars, i, "SELL", 1.1, {**prm, "fmode": "veto"}) is False
    assert p0.filter_keep("C1_ENGULF", bars, i, "SELL", 1.1, {**prm, "K": 1, "fmode": "veto"}) is True


# ---- plugins -----------------------------------------------------------------------------------------------------

def _quote(c):
    return {"bid": c, "ask": round(c + 0.00008, 6), "spread_points": 8, "digits": 5, "point": 0.00001,
            "stops_level": 0}


CFG = {"signal_timeframe": "M5", "htf_timeframe": "H1", "min_score": 3, "atr_sl_mult": 1.5, "atr_tp_mult": 2.5,
       "min_sl_points": 60}
RISK = {"max_spread_points": 30, "max_tick_age_seconds": 10}


def _htf(sig):
    out = []
    for k in range(0, len(sig) - 11, 12):
        seg = sig[k:k + 12]
        out.append(bar(seg[0]["o"], max(b["h"] for b in seg), min(b["l"] for b in seg), seg[-1]["c"], seg[0]["t"]))
    return out


@pytest.mark.parametrize("pid", ["C1_ENGULF", "L2_ROUND", "I4_ADX"])
def test_filter_plugin_is_trend_v1_plus_blocker(pid):
    mod = get("filt-" + pid)
    sig = walk(1400, seed=3)
    htf = _htf(sig)
    n_cand = n_blocked = 0
    for i in range(500, 1400, 9):
        s = sig[i - 399:i + 1]
        h = [b for b in htf if b["t"] + 3600 <= s[-1]["t"] + 300][-200:]
        ctx = strategy.AnalysisContext(now_ts=s[-1]["t"] + 300)
        cfg = {**mod.PARAMS, **CFG}
        base = strategy.analyze("EURUSD", s, h, _quote(s[-1]["c"]), cfg, RISK, ctx)
        got = mod.analyze("EURUSD", s, h, _quote(s[-1]["c"]), cfg, RISK, ctx)
        if not base["candidate"]:
            assert got == base
            continue
        n_cand += 1
        keep = p0.filter_keep(pid, s, len(s) - 1, base["candidate"]["side"], base["candidate"]["entry"],
                              {k[4:]: v for k, v in cfg.items() if k.startswith("pat_")})
        if keep:
            assert got == base
        else:
            n_blocked += 1
            assert got["direction"] == "HOLD"
            assert got["candidate"]["blockers"][:-1] == base["candidate"]["blockers"]
            assert got["candidate"]["blockers"][-1].startswith(f"Filtru {pid}")
            assert {k: got["candidate"][k] for k in ("side", "entry", "stop_loss", "take_profit")} == \
                   {k: base["candidate"][k] for k in ("side", "entry", "stop_loss", "take_profit")}
    assert n_cand > 0


@pytest.mark.parametrize("pid", p0.PATTERN_IDS)
def test_standalone_plugin_levels(pid):
    mod = get("pat-" + pid)
    sig = walk(1600, seed=5)
    htf = _htf(sig)
    for i in range(400, 1600, 2):
        s = sig[i - 399:i + 1]
        h = [b for b in htf if b["t"] + 3600 <= s[-1]["t"] + 300][-200:]
        q = _quote(s[-1]["c"])
        a = mod.analyze("EURUSD", s, h, q, {**CFG, **mod.PARAMS}, RISK,
                        strategy.AnalysisContext(now_ts=s[-1]["t"] + 300))
        c = a["candidate"]
        if not c:
            continue
        sign = 1 if c["side"] == "BUY" else -1
        assert sign * (c["entry"] - c["stop_loss"]) > 0 and sign * (c["take_profit"] - c["entry"]) > 0
        if pid != "L2_ROUND":
            assert p0.DETECTORS[pid](s, len(s) - 1, {k[4:]: v for k, v in mod.PARAMS.items()}) == sign
    if hasattr(mod, "bias"):
        assert mod.bias("EURUSD", sig[:400], [], mod.PARAMS) in (-1, 0, 1)
