"""Pattern builder #2 (app.patterns.p2 + pat-/filt- research plugins): causality and crafted cases."""
from __future__ import annotations

import random

import pytest

from app import strategy
from app.patterns import p2
from app.strategies import get as get_plugin
from app.strategies.discovery import _pat2

T0 = 1_780_000_000 - (1_780_000_000 % 86400) + 8 * 3600  # 08:00 UTC on some day
PT = 0.00001


def mk(closes, start=1.1000, wick=0.0002, sec=300, t0=T0, tv=None):
    bars, prev = [], start
    for k, c in enumerate(closes):
        o = prev
        bars.append({"t": t0 + k * sec, "o": o, "h": max(o, c) + wick, "l": min(o, c) - wick, "c": c,
                     "tv": (tv[k] if tv else 1.0)})
        prev = c
    return bars


def path(points, seg=8, flat=20):
    """Piecewise-linear close path through `points` (seg bars per leg) after `flat` flat bars at points[0]."""
    out = [points[0]] * flat
    for a, b in zip(points, points[1:]):
        out += [a + (b - a) * (k + 1) / seg for k in range(seg)]
    return out


def mirror(bars, base=2.2):
    return [{"t": b["t"], "o": base - b["o"], "h": base - b["l"], "l": base - b["h"], "c": base - b["c"],
             "tv": b.get("tv", 1.0)} for b in bars]


def random_walk(n=1500, seed=7, sec=300):
    rnd = random.Random(seed)
    c, out = 1.1, []
    for _ in range(n):
        c += rnd.gauss(0, 0.0006) + (0.00015 if (len(out) // 150) % 2 else -0.00015)
        out.append(round(c, 5))
    return mk(out, sec=sec, tv=[rnd.randint(1, 50) for _ in range(n)])


PARAMS = {"C3_INSIDEBAR": {"min_inside": 1}, "P2_HS": {"L": 3, "tol": 1.0}, "L1_SR": {"N": 2},
          "L4_FIB618": {"leg_min": 2.0, "z": 0.05}, "S3_OB": {"m": 1.5}, "I3_SUPERTREND": {"n": 10, "mult": 2.0},
          "I6_VWAP": {"anchor": "day", "k": 1.0, "min_bars": 12}}


# ---- no lookahead ----------------------------------------------------------------------------------------------

@pytest.mark.parametrize("pid", list(p2.SCANS))
def test_scan_is_prefix_invariant(pid):
    """scan(bars[:k]) == scan(bars)[:k]: an event at bar i never depends on bars after i."""
    bars = random_walk()
    full = p2.SCANS[pid](bars, PARAMS[pid])
    assert any(e for e in full), f"{pid}: no event at all on the random walk (test data too tame)"
    for k in (200, 517, 901, 1333):
        part = p2.SCANS[pid](bars[:k], PARAMS[pid])
        assert [(e or {}).get("dir") for e in part] == [(e or {}).get("dir") for e in full[:k]]


@pytest.mark.parametrize("pid", list(p2.SCANS))
def test_detect_uses_only_closed_bars(pid):
    bars = random_walk(900, seed=11)
    full = p2.SCANS[pid](bars, PARAMS[pid])
    idx = [i for i, e in enumerate(full) if e][:5] + [450, 899]
    poisoned = [dict(b) for b in bars]
    for i in idx:
        # future bars replaced by garbage must not change detect(bars, i)
        bad = poisoned[:i + 1] + [{**b, "h": b["h"] + 1, "l": b["l"] - 1, "c": b["c"] + 0.5} for b in bars[i + 1:]]
        want = full[i]["dir"] if full[i] else 0
        assert p2.DETECTS[pid](bars, i, PARAMS[pid]) == want
        assert p2.DETECTS[pid](bad, i, PARAMS[pid]) == want


@pytest.mark.parametrize("pid", list(p2.FILTERS))
def test_filter_verdict_is_causal(pid):
    bars = random_walk(900, seed=5)
    prepare, verdict = p2.FILTERS[pid]
    pp = dict(PARAMS[pid], K=6, D=1.0)
    full = prepare(bars, pp)
    for i in (300, 555, 899):
        part = prepare(bars[:i + 1], pp)
        for side in ("BUY", "SELL"):
            e = bars[i]["c"]
            sl = e - 0.002 if side == "BUY" else e + 0.002
            assert verdict(full, i, side, e, sl, pp)[0] == verdict(part, i, side, e, sl, pp)[0]


# ---- crafted cases ---------------------------------------------------------------------------------------------

def test_inside_bar_breakout_and_expiry():
    closes = [1.0950 + 0.0001 * k for k in range(30)]  # rising: no inside bars before the mother
    bars = mk(closes, start=1.0950, wick=0.0002)
    j = 30
    bars.append({"t": bars[-1]["t"] + 300, "o": 1.1000, "h": 1.1040, "l": 1.0990, "c": 1.1030})  # mother
    bars.append({"t": bars[-1]["t"] + 300, "o": 1.1030, "h": 1.1035, "l": 1.1000, "c": 1.1010})  # inside
    bars.append({"t": bars[-1]["t"] + 300, "o": 1.1010, "h": 1.1030, "l": 1.1005, "c": 1.1020})  # inside
    bars.append({"t": bars[-1]["t"] + 300, "o": 1.1020, "h": 1.1045, "l": 1.1015, "c": 1.1038})  # not inside, no close out
    bars.append({"t": bars[-1]["t"] + 300, "o": 1.1038, "h": 1.1050, "l": 1.1030, "c": 1.1045})  # close > mother high
    ev = p2.scan_c3_insidebar(bars, {"min_inside": 2})
    assert [i for i, e in enumerate(ev) if e] == [len(bars) - 1]
    e = ev[-1]
    assert e["dir"] == 1 and e["j"] == j and e["mh"] == 1.1040 and e["ml"] == 1.0990
    assert p2.scan_c3_insidebar(bars, {"min_inside": 3})[-1] is None  # only 2 inside bars
    # expiry: 6 bars without a close beyond -> no event on the 7th
    late = bars[:j + 3] + [{"t": bars[j + 2]["t"] + 300 * (k + 1), "o": 1.1020, "h": 1.1045, "l": 1.1000,
                            "c": 1.1020} for k in range(6)]
    late.append({"t": late[-1]["t"] + 300, "o": 1.1020, "h": 1.1060, "l": 1.1015, "c": 1.1055})
    assert not [x for x in p2.scan_c3_insidebar(late, {"min_inside": 2}) if x and x["j"] == j]
    # bear mirror
    evm = p2.scan_c3_insidebar(mirror(bars), {"min_inside": 2})
    assert evm[-1]["dir"] == -1


def _hs_bars():
    return mk(path([1.1000, 1.1100, 1.1040, 1.1160, 1.1040, 1.1100, 1.0980]))


def test_head_and_shoulders_top_and_inverse():
    bars = _hs_bars()
    ev = p2.scan_p2_hs(bars, {"L": 3, "tol": 0.5})
    hits = [(i, e) for i, e in enumerate(ev) if e]
    assert len(hits) == 1
    i, e = hits[0]
    rs = 20 + 5 * 8 - 1
    assert e["dir"] == -1 and i >= rs + 3
    assert bars[i]["c"] < e["neck"] and all(b["c"] >= e["neck"] - 1e-12 for b in bars[rs:i])
    assert e["sl"] > e["rs"] and e["target"] < e["neck"]
    assert abs(e["height"] - (1.1160 + 0.0002 - (1.1040 - 0.0002))) < 1e-9
    evm = p2.scan_p2_hs(mirror(bars), {"L": 3, "tol": 0.5})
    assert [(k, x["dir"]) for k, x in enumerate(evm) if x] == [(i, 1)]
    # shoulders too different for the tolerance -> no pattern
    lop = mk(path([1.1000, 1.1100, 1.1040, 1.1160, 1.1040, 1.1135, 1.0980]))
    assert not any(p2.scan_p2_hs(lop, {"L": 3, "tol": 0.5}))


def test_sr_levels_and_bounce():
    # three swing lows at ~1.1000 (support), then a dip to it and a bullish close back above it
    pts = [1.1050, 1.1000, 1.1060, 1.1001, 1.1062, 1.1000, 1.1061, 1.1003]
    closes = path(pts, seg=6)
    bars = mk(closes)
    i = len(bars)
    bars.append({"t": bars[-1]["t"] + 300, "o": 1.1003, "h": 1.1022, "l": 1.0999, "c": 1.1020})
    sr = p2.SRLevels(bars, {"N": 3})
    lv = sr.levels(i)
    assert any(abs(x - 1.0998) < 0.0005 for x in lv)
    ev = p2.event_l1_sr(bars, i, {"N": 3})
    assert ev and ev["dir"] == 1 and ev["sl"] < ev["level"] < bars[i]["c"]
    assert p2.scan_l1_sr(bars, {"N": 3})[i]["dir"] == 1
    assert p2.detect_l1_sr(bars, i, {"N": 9}) == 0  # not enough touches
    # room filter: a BUY right under the cluster of highs is refused, far from it is kept
    prepare, verdict = p2.FILTERS["L1_SR"]
    st = prepare(bars, {"N": 2, "D": 1.0})
    highs = [x for x in st["sr"].levels(i) if x > 1.105]
    assert highs
    assert verdict(st, i, "BUY", highs[0] - 0.1 * st["sr"].atr[i], 1.09, {"D": 1.0})[0] is False
    assert verdict(st, i, "BUY", 1.1010, 1.09, {"D": 1.0})[0] is True


def test_fib_pullback_in_uptrend():
    closes = [1.1000 + 0.0001 * k for k in range(230)]           # long uptrend (EMA50 > EMA200)
    c0 = closes[-1]
    closes += [c0 - 0.0003 * (k + 1) for k in range(10)]          # dip -> swing low A
    a_c = closes[-1]
    closes += [a_c + 0.0006 * (k + 1) for k in range(20)]         # leg up -> swing high B
    b_c = closes[-1]
    D = b_c - a_c
    closes += [b_c - 0.55 * D * (k + 1) / 12 for k in range(12)]  # pullback into the zone
    closes += [closes[-1] + 0.0012 * (k + 1) for k in range(6)]   # bounce
    closes += [closes[-1]] * 12
    bars = mk(closes, start=1.1000, wick=0.0001)
    ev = p2.scan_l4_fib618(bars, {"leg_min": 2.0, "z": 0.05})
    hits = [(i, e) for i, e in enumerate(ev) if e]
    assert len(hits) == 1
    i, e = hits[0]
    assert e["dir"] == 1 and i > 260
    lvl50 = e["B"] - 0.5 * e["D"]
    assert bars[i]["l"] <= lvl50 < bars[i]["c"]
    assert e["sl"] < e["A"] + 0.214 * e["D"] < e["tp"] == e["B"]
    prepare, verdict = p2.FILTERS["L4_FIB618"]
    st = prepare(bars, {"leg_min": 2.0, "z": 0.05})
    assert verdict(st, i + 2, "BUY", bars[i]["c"], 1.0, {"K": 6})[0] is True
    assert verdict(st, i + 2, "BUY", e["B"] - 0.2 * e["D"], 1.0, {"K": 6})[0] is False  # above the 38.2% level
    assert verdict(st, i + 2, "SELL", bars[i]["c"], 2.0, {"K": 6})[0] is False
    assert verdict(st, i + 6, "BUY", bars[i]["c"], 1.0, {"K": 6})[0] is False  # too old
    # a long leg needs a bigger ATR multiple: leg_min 50 -> nothing
    assert not any(p2.scan_l4_fib618(bars, {"leg_min": 50.0}))


def test_order_block_retest():
    closes = path([1.1000, 1.1030, 1.1010], seg=6)                     # swing high ~1.1030
    closes += [1.1012, 1.1008]                                         # bearish bar k (o 1.1012 -> c 1.1008)
    closes += [1.1030, 1.1050, 1.1070]                                 # 3-bar impulse, breaks 1.1030
    closes += [1.1072, 1.1060, 1.1040, 1.1025, 1.1011, 1.1030]         # pull back into the zone, bounce
    bars = mk(closes, wick=0.0002)
    obs, _ = p2.order_blocks(bars, {"m": 1.5})
    bull = [o for o in obs if o["dir"] == 1]
    assert len(bull) == 1
    ob = bull[0]
    k = ob["k"]
    assert bars[k]["c"] < bars[k]["o"] and ob["lo"] == bars[k]["l"] and ob["hi"] == bars[k]["o"]
    ev = p2.scan_s3_ob(bars, {"m": 1.5})
    hits = [(i, e) for i, e in enumerate(ev) if e]
    assert hits and hits[0][0] == ob["retest"] and hits[0][1]["dir"] == 1
    assert bars[ob["retest"]]["l"] <= ob["hi"] and bars[ob["retest"]]["c"] > ob["lo"]
    assert hits[0][1]["sl"] < ob["lo"]
    assert not [o for o in p2.order_blocks(bars, {"m": 9.0})[0] if o["dir"] == 1]
    prepare, verdict = p2.FILTERS["S3_OB"]
    st = prepare(bars, {"m": 1.5})
    r = ob["retest"]
    assert verdict(st, r, "BUY", bars[r]["c"], 1.0, {"K": 2})[0] is True
    # a SELL with the entry inside the (unmitigated before r) bull block is vetoed
    assert verdict(st, r - 1, "SELL", (ob["lo"] + ob["hi"]) / 2, 1.2, {"K": 2})[0] is False


def test_supertrend_flip_and_filter():
    closes = [1.1000 + 0.0003 * k for k in range(80)] + [1.1237 - 0.0012 * k for k in range(30)]
    bars = mk(closes, wick=0.0002)
    dirs, line, flip = p2.supertrend(bars, {"n": 10, "mult": 3.0})
    assert dirs[60] == 1 and line[60] < bars[60]["c"]
    flips = [i for i in range(len(bars)) if flip[i]]
    assert flips and dirs[flips[0]] == -1 and flips[0] > 80
    # the flip bar closes below the previous final lower band
    ev = p2.scan_i3_supertrend(bars, {"n": 10, "mult": 3.0})
    assert ev[flips[0]]["dir"] == -1 and all(e is None for e in ev[:flips[0]])
    prepare, verdict = p2.FILTERS["I3_SUPERTREND"]
    st = prepare(bars, {"n": 10, "mult": 3.0})
    assert verdict(st, 60, "BUY", 1.0, 0.9, {"K": 0})[0] is True
    assert verdict(st, 60, "SELL", 1.0, 1.1, {"K": 0})[0] is False
    assert verdict(st, 60, "BUY", 1.0, 0.9, {"K": 12})[0] is False  # trend up, but no fresh flip
    assert verdict(st, flips[0] + 3, "SELL", 1.0, 1.1, {"K": 12})[0] is True


def test_vwap_session_reset_and_weights():
    sec = 3600
    t0 = 1_781_000_000 - (1_781_000_000 % 86400)  # 00:00 UTC
    bars = mk([1.10, 1.11, 1.12, 1.13], sec=sec, t0=t0 + 18 * sec, tv=[1, 3, 0, 2], wick=0.0)
    vw, sg, nb, lo, hi = p2.vwap_series(bars, "day")
    # 00:00 Athens = 21:00 or 22:00 UTC: the session resets inside the 4 bars (18..21 UTC)
    assert nb[0] == 1 and nb[-1] in (1, 2) and min(nb[1:]) == 1
    tp0 = (bars[0]["h"] + bars[0]["l"] + bars[0]["c"]) / 3
    tp1 = (bars[1]["h"] + bars[1]["l"] + bars[1]["c"]) / 3
    if nb[1] == 2:
        assert abs(vw[1] - (tp0 * 1 + tp1 * 3) / 4) < 1e-12
    # london anchor: 18..21 UTC are in one session
    vwl, _, nbl, _, _ = p2.vwap_series(bars, "london")
    assert nbl == [1, 2, 3, 4]
    assert abs(vwl[1] - (tp0 * 1 + tp1 * 3) / 4) < 1e-12
    assert p2.session_end_ts(t0 + 8 * 3600, "london") == t0 + 86400 + 7 * 3600


def test_vwap_reversion_event():
    t0 = 1_781_000_000 - (1_781_000_000 % 86400) + 3600  # 01:00 UTC: inside one broker day
    closes = [1.1000] * 14 + [1.1000 - 0.0004 * k for k in range(1, 6)] + [1.0985]
    bars = mk(closes, sec=300, t0=t0, wick=0.0001)
    ev = p2.scan_i6_vwap(bars, {"anchor": "day", "k": 1.0, "min_bars": 12})
    assert ev[-1] and ev[-1]["dir"] == 1 and ev[-1]["vwap"] > bars[-1]["c"]
    assert p2.scan_i6_vwap(bars, {"anchor": "day", "k": 1.0, "min_bars": 30})[-1] is None
    prepare, verdict = p2.FILTERS["I6_VWAP"]
    st = prepare(bars, {"anchor": "day"})
    i = len(bars) - 1
    assert verdict(st, i, "BUY", bars[i]["c"], 1.0, {"mode": "side"})[0] is False
    assert verdict(st, i, "SELL", bars[i]["c"], 1.2, {"mode": "side"})[0] is True


# ---- plugins ---------------------------------------------------------------------------------------------------

QUOTE = {"bid": None, "ask": None, "digits": 5, "point": PT, "stops_level": 0, "spread_points": 2}


def _quote(c):
    return {**QUOTE, "bid": c, "ask": round(c + 2 * PT, 5)}


def test_plugins_are_registered():
    for pid in p2.SCANS:
        assert get_plugin(f"pat-{pid}").NAME == f"pat-{pid}"
        assert get_plugin(f"filt-{pid}").NAME == f"filt-{pid}"
        assert not hasattr(get_plugin(f"filt-{pid}"), "RUN_DEFAULTS")  # live exits (incl. RESCUE) apply


def test_standalone_inside_bar_plugin():
    closes = [1.1000 + (0.0001 if k % 2 else -0.0001) for k in range(120)]
    bars = mk(closes, wick=0.0002)
    t = bars[-1]["t"]
    bars += [{"t": t + 300, "o": 1.1000, "h": 1.1040, "l": 1.0990, "c": 1.1030},
             {"t": t + 600, "o": 1.1030, "h": 1.1035, "l": 1.1000, "c": 1.1010},
             {"t": t + 900, "o": 1.1010, "h": 1.1050, "l": 1.1005, "c": 1.1046}]
    ctx = strategy.AnalysisContext(now_ts=bars[-1]["t"] + 300)
    mod = get_plugin("pat-C3_INSIDEBAR")
    a = mod.analyze("EURUSD", bars, [], _quote(1.1046), {"pat_R": 1.5, "min_sl_points": 60}, {}, ctx)
    assert a["direction"] == "BUY", a["reasons"]
    assert a["stop_loss"] == 1.099
    risk = a["entry"] - a["stop_loss"]
    assert abs(a["take_profit"] - (a["entry"] + 1.5 * risk)) < 2 * PT
    # one bar earlier there is no event
    b = mod.analyze("EURUSD", bars[:-1], [], _quote(1.1010), {}, {}, ctx)
    assert b["direction"] == "HOLD" and b["candidate"] is None


def test_filter_plugin_blocks_disagreeing_candidate(monkeypatch):
    closes = [1.1000 + 0.0003 * k for k in range(150)]
    bars = mk(closes, wick=0.0002)
    c = bars[-1]["c"]

    def fake(symbol, sig_bars, htf_bars, quote, cfg, risk, ctx, side="SELL"):
        cand = {"side": side, "entry": c, "stop_loss": c + 0.003, "take_profit": c - 0.005, "score": -0.8,
                "blockers": []}
        return {"direction": side, "entry": c, "stop_loss": c + 0.003, "take_profit": c - 0.005, "rr": 1.67,
                "reasons": ["x"], "candidate": cand}

    monkeypatch.setattr(_pat2.strategy, "analyze", fake)
    mod = get_plugin("filt-I3_SUPERTREND")
    ctx = strategy.AnalysisContext(now_ts=bars[-1]["t"] + 300)
    a = mod.analyze("EURUSD", bars, [], _quote(c), {"pat_K": 0}, {}, ctx)
    assert a["direction"] == "HOLD" and a["entry"] is None
    assert any(b.startswith("Filtru I3_SUPERTREND") for b in a["candidate"]["blockers"])
    monkeypatch.setattr(_pat2.strategy, "analyze", lambda *x: fake(*x, side="BUY") | {
        "candidate": {"side": "BUY", "entry": c, "stop_loss": c - 0.003, "take_profit": c + 0.005, "score": 0.8,
                      "blockers": []}})
    b = mod.analyze("EURUSD", bars, [], _quote(c), {"pat_K": 0}, {}, ctx)
    assert b["direction"] == "BUY" and b["candidate"]["blockers"] == []


def test_filter_plugin_is_trend_v1_when_no_candidate():
    bars = mk([1.1] * 150)
    ctx = strategy.AnalysisContext(now_ts=bars[-1]["t"] + 300)
    want = strategy.analyze("EURUSD", bars, bars[:80], _quote(1.1), {}, {}, ctx)
    got = get_plugin("filt-L1_SR").analyze("EURUSD", bars, bars[:80], _quote(1.1), {}, {}, ctx)
    assert got == want
