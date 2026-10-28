"""Discovery builder #0 plugins (disc-H01, disc-H05, disc-H09, disc-H13, disc-H17): crafted-scenario side checks,
no-lookahead checks and the research time-exit hook (app/strategies/discovery/_disc0.py)."""
import random
from datetime import datetime, timezone

import pytest

from app import labeler
from app import strategies as S
from app.config import Settings
from app.strategies.discovery import _disc0 as D
from app.strategy import AnalysisContext
from tests.conftest import EURUSD_INFO

SET = Settings(_env_file=None)
CFG = SET.default_strategy()
RISK = SET.default_risk()
H, DAY = 3600, 86400


def T(s: str) -> int:
    return int(datetime.strptime(s, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc).timestamp())


def q_at(c, sp=10):
    d = dict(EURUSD_INFO)
    d.update(bid=c, ask=round(c + sp * d["point"], 5), spread_points=sp)
    return d


def run(name, sig, htf, tf, htf_tf="D1", sec=H, **over):
    cfg = S.merged_cfg(name, {**CFG, "signal_timeframe": tf, "htf_timeframe": htf_tf}, over)
    ctx = AnalysisContext(session_open=True, has_bot_position=False, signals_this_session=0, tick_age=0.0,
                          now_ts=sig[-1]["t"] + sec)
    return S.get(name).analyze("EURUSD", sig, htf, q_at(sig[-1]["c"]), cfg, RISK, ctx)


def bar(t, o, h, l, c):
    return {"t": t, "o": o, "h": h, "l": l, "c": c}


def flat_h1(end_t, n, px=1.1000, rng=0.0010, seed=1):
    """n H1 bars ending with the bar that opens at end_t - H (each bar range ~rng around px, tiny noise)."""
    r = random.Random(seed)
    out = []
    for k in range(n):
        t = end_t - (n - k) * H
        c = px + r.uniform(-0.0002, 0.0002)
        out.append(bar(t, px, max(px, c) + rng / 2, min(px, c) - rng / 2, c))
    return out


def daily_before(t_first_day, n, px=1.1000, rng=0.0100):
    """n weekday D1 bars (winter: open 22:00 UTC) ending the broker day before the H1 day starting t_first_day."""
    out, t = [], t_first_day - DAY
    while len(out) < n:
        if D.broker_date(t).weekday() < 5:
            out.append(bar(t, px, px + rng / 2, px - rng / 2, px))
        t -= DAY
    return out[::-1]


def side(a):
    return a["direction"] if a["direction"] != "HOLD" else (a["candidate"] or {}).get("side", "HOLD")


# ---- registry ---------------------------------------------------------------------------------------------

def test_registered_research_only():
    for n in ("disc-H01", "disc-H05", "disc-H09", "disc-H13", "disc-H17"):
        assert S.get(n).NAME == n
        assert n not in S.names(include_research=True)


def test_broker_day_is_athens_midnight():
    # winter: broker midnight 22:00 UTC; summer: 21:00 UTC (EU DST rules, checked on the feed's D1 bars)
    assert D.broker_midnight(datetime(2024, 1, 9).date()) == T("2024-01-08 22:00")
    assert D.broker_midnight(datetime(2024, 7, 9).date()) == T("2024-07-08 21:00")
    assert D.broker_date(T("2024-01-08 22:00")).isoformat() == "2024-01-09"


# ---- H01 --------------------------------------------------------------------------------------------------

def h01_case(up=True, ext=0.0):
    day0 = T("2024-01-10 00:00")
    sig = flat_h1(day0, 150)
    px, sg = 1.1000, 1 if up else -1
    for k in range(7):  # Asia drifts 0.0060 (ATRd = 0.0100)
        o = px + sg * 0.0060 * k / 7
        c = px + sg * 0.0060 * (k + 1) / 7
        sig.append(bar(day0 + k * H, o, max(o, c) + 0.0002, min(o, c) - 0.0002, c))
    hi, lo = max(b["h"] for b in sig[-7:]), min(b["l"] for b in sig[-7:])
    c7 = px + sg * 0.0050
    ext_px = (hi + ext) if up else (lo - ext)
    sig.append(bar(day0 + 7 * H, sig[-1]["c"], max(ext_px, c7) if up else max(sig[-1]["c"], c7) + 0.0001,
                   min(c7, sig[-1]["c"]) - 0.0001 if up else min(ext_px, c7), c7))
    htf = daily_before(day0 - 2 * H, 40)
    return sig, htf, hi, lo


def test_h01_fades_asian_move_both_sides():
    sig, htf, hi, lo = h01_case(up=True)
    a = run("disc-H01", sig, htf, "H1", k_atr=0.5)
    assert side(a) == "SELL"
    atr = a["indicators"]["atr"]
    assert a["stop_loss"] == pytest.approx(hi + 0.15 * atr, abs=2e-5)
    assert a["take_profit"] == pytest.approx((hi + lo) / 2, abs=2e-5)
    a = run("disc-H01", sig, htf, "H1", k_atr=0.5, tp_mode="open")
    assert a["take_profit"] == pytest.approx(sig[-8]["o"], abs=2e-5)
    sig, htf, hi, lo = h01_case(up=False)
    a = run("disc-H01", sig, htf, "H1", k_atr=0.5)
    assert side(a) == "BUY" and a["stop_loss"] < lo


def test_h01_filters():
    sig, htf, _, _ = h01_case(up=True)
    assert side(run("disc-H01", sig, htf, "H1", k_atr=0.7)) == "HOLD"  # move 0.6 ATR < 0.7 ATR
    sig2, htf2, _, _ = h01_case(up=True, ext=0.0030)  # 07:00 bar extends the high by 0.3 ATR > 0.25
    assert side(run("disc-H01", sig2, htf2, "H1", k_atr=0.5)) == "HOLD"
    nxt = dict(sig[-1], t=sig[-1]["t"] + H)  # 08:00 bar never signals
    assert side(run("disc-H01", sig + [nxt], htf, "H1", k_atr=0.5)) == "HOLD"


def test_h01_time_exit_at_1600_utc():
    cfg = S.merged_cfg("disc-H01", {}, {})
    te = S.get("disc-H01").time_exit
    o = T("2024-01-10 08:00")
    assert te(o, T("2024-01-10 14:00"), H, 7, cfg) is None
    assert te(o, T("2024-01-10 15:00"), H, 8, cfg) == "close"
    assert te(o, T("2024-01-10 17:00"), H, 9, cfg) == "open"


# ---- H05 --------------------------------------------------------------------------------------------------

def h05_case(break_up=True, early_break=False):
    day = T("2024-01-10 22:00")  # broker Thursday 2024-01-11 starts
    htf = daily_before(day, 40, rng=0.0100)
    htf[-2] = bar(htf[-2]["t"], 1.1000, 1.1080, 1.0920, 1.1000)  # wide prior bar
    htf[-1] = bar(htf[-1]["t"], 1.1000, 1.1020, 1.0985, 1.1005)  # NR + inside
    sig = flat_h1(day, 150, px=1.1000, rng=0.0008)
    for k in range(5):
        sig.append(bar(day + k * H, 1.1000, 1.1010, 1.0990, 1.1002))
    if early_break:
        sig.append(bar(day + 5 * H, 1.1002, 1.1040, 1.1000, 1.1035))
        sig.append(bar(day + 6 * H, 1.1035, 1.1045, 1.1030, 1.1040))
    else:
        c = 1.1035 if break_up else 1.0970
        sig.append(bar(day + 5 * H, 1.1002, max(1.1002, c) + 0.0002, min(1.1002, c) - 0.0002, c))
    return sig, htf


def test_h05_first_break_side_and_levels():
    sig, htf = h05_case(True)
    a = run("disc-H05", sig, htf, "H1", nr_n=7, tp_r=1.5)
    assert side(a) == "BUY" and a["stop_loss"] == pytest.approx(1.0985, abs=1e-5)
    assert (a["take_profit"] - a["entry"]) == pytest.approx(1.5 * (a["entry"] - a["stop_loss"]), abs=2e-5)
    sig, htf = h05_case(False)
    a = run("disc-H05", sig, htf, "H1", nr_n=7)
    assert side(a) == "SELL" and a["stop_loss"] == pytest.approx(1.1020, abs=1e-5)


def test_h05_only_first_break_and_setup_required():
    sig, htf = h05_case(early_break=True)
    assert side(run("disc-H05", sig, htf, "H1")) == "HOLD"
    sig, htf = h05_case(True)
    htf[-1] = bar(htf[-1]["t"], 1.1000, 1.1090, 1.0985, 1.1005)  # not inside
    assert side(run("disc-H05", sig, htf, "H1")) == "HOLD"


def test_h05_time_exit_end_of_next_broker_day():
    cfg = S.merged_cfg("disc-H05", {}, {})
    te = S.get("disc-H05").time_exit
    # signal on broker Thursday 2024-01-11 -> exit at the end of Friday (Fri 22:00 UTC = Sat 00:00 broker)
    o = T("2024-01-11 04:00")
    assert te(o, T("2024-01-12 20:00"), H, 30, cfg) is None
    assert te(o, T("2024-01-12 21:00"), H, 31, cfg) == "close"
    # signal on Friday's last bar, filled Monday open -> exit at the end of Monday
    o = T("2024-01-14 22:00")
    assert te(o, T("2024-01-15 21:00"), H, 24, cfg) == "close"
    assert te(o, T("2024-01-15 20:00"), H, 23, cfg) is None


# ---- H09 --------------------------------------------------------------------------------------------------

def h09_case(short=True):
    wk0 = T("2024-01-14 22:00")  # broker Monday 2024-01-15 (H4 bars at 22,02,06,...)
    htf = daily_before(wk0, 40, rng=0.0100)  # prior week: PWH 1.1050 / PWL 1.0950
    sig = []
    t = wk0 - 120 * 4 * H
    while t < wk0:
        sig.append(bar(t, 1.1000, 1.1030, 1.0970, 1.1000))
        t += 4 * H
    sg = 1 if short else -1
    sig.append(bar(wk0, 1.1000, 1.1030, 1.0970, 1.1000 + sg * 0.0020))
    sig.append(bar(wk0 + 4 * H, 1.1020 if short else 1.0980, 1.1062 if short else 1.0990,
                   1.1010 if short else 1.0938, 1.1045 if short else 1.0955))  # sweep
    sig.append(bar(wk0 + 8 * H, 1.1045 if short else 1.0955, 1.1050 if short else 1.0970,
                   1.1030 if short else 1.0950, 1.1035 if short else 1.0965))  # reclaim
    return sig, htf


def test_h09_sweep_reclaim_sides():
    sig, htf = h09_case(True)
    a = run("disc-H09", sig, htf, "H4", sec=4 * H, sweep_atr=0.0)
    assert side(a) == "SELL"
    assert a["stop_loss"] == pytest.approx(1.1062 + 0.2 * a["indicators"]["atr"], abs=2e-5)
    assert a["take_profit"] == pytest.approx(1.1000, abs=1e-5)
    assert side(run("disc-H09", sig, htf, "H4", sec=4 * H, sweep_atr=0.15)) == "HOLD"  # 12 pips < 0.15 ATR
    sig, htf = h09_case(False)
    a = run("disc-H09", sig, htf, "H4", sec=4 * H, sweep_atr=0.0, tp_mode="2R")
    assert side(a) == "BUY"
    assert (a["take_profit"] - a["entry"]) == pytest.approx(2 * (a["entry"] - a["stop_loss"]), abs=2e-5)


def test_h09_only_first_reclaim_and_friday_exit():
    sig, htf = h09_case(True)
    sig2 = sig + [bar(sig[-1]["t"] + 4 * H, 1.1035, 1.1040, 1.1020, 1.1030)]
    assert side(run("disc-H09", sig2, htf, "H4", sec=4 * H)) == "HOLD"
    cfg = S.merged_cfg("disc-H09", {}, {})
    te = S.get("disc-H09").time_exit
    o = T("2024-01-15 10:00")
    assert te(o, T("2024-01-19 14:00"), 4 * H, 20, cfg) is None
    assert te(o, T("2024-01-19 18:00"), 4 * H, 21, cfg) == "close"  # last H4 bar ends Fri 22:00 UTC


# ---- H13 --------------------------------------------------------------------------------------------------

def xs_universe(n=150, spike="EURUSD", sign=1, seed=3):
    r = random.Random(seed)
    t0 = T("2018-01-01 22:00")
    days = []
    t = t0
    while len(days) < n:
        if D.broker_date(t).weekday() < 5:
            days.append(t)
        t += DAY
    uni = {}
    for s in ("EURUSD", "GBPUSD", "AUDUSD", "USDCAD", "NZDUSD"):
        px, bars = 1.0, []
        for k, tt in enumerate(days):
            o = px
            c = px * (1 + r.gauss(0, 0.004))
            if s == spike and k == n - 1:
                c = o * (1 + sign * 0.03)
            bars.append(bar(tt, o, max(o, c) * 1.002, min(o, c) * 0.998, c))
            px = c
        uni[s] = bars
    return uni


def run_xs(uni, sym="EURUSD", k=None, **over):
    bars = uni[sym] if k is None else uni[sym][:k + 1]
    cfg = S.merged_cfg("disc-H13", {**CFG, "signal_timeframe": "D1", "htf_timeframe": "NONE"},
                       {"xs_symbols": list(uni), **over})
    ctx = AnalysisContext(now_ts=bars[-1]["t"] + DAY)
    return S.get("disc-H13").analyze(sym, bars, [], q_at(bars[-1]["c"]), cfg, RISK, ctx)


def test_h13_extreme_pair_reverses():
    uni = xs_universe(sign=1)
    D.set_universe(uni)
    try:
        a = run_xs(uni, lookback=1)
        assert side(a) == "SELL" and a["indicators"]["xs_rank"] == 1
        assert a["stop_loss"] - a["entry"] == pytest.approx(2 * a["indicators"]["atr"], rel=0.01)
        assert side(run_xs(uni, "GBPUSD", lookback=1)) in ("HOLD", "BUY")
        uni2 = xs_universe(sign=-1)
        D.set_universe(uni2)
        assert side(run_xs(uni2, lookback=1)) == "BUY"
    finally:
        D.set_universe(None)


def test_h13_no_lookahead_in_universe():
    uni = xs_universe(sign=1)
    k = 120
    D.set_universe(uni)
    try:
        before = run_xs(uni, k=k, lookback=5)
        fut = {s: b[:k + 1] + [dict(x, c=x["c"] * 1.5, h=x["h"] * 1.5) for x in b[k + 1:]] for s, b in uni.items()}
        D.set_universe(fut)
        after = run_xs(fut, k=k, lookback=5)
    finally:
        D.set_universe(None)
    assert before["indicators"].get("xs_z") == after["indicators"].get("xs_z")
    assert side(before) == side(after)


def test_h13_time_exit_counts_bars():
    cfg = S.merged_cfg("disc-H13", {}, {"hold": 5})
    te = S.get("disc-H13").time_exit
    assert te(0, 0, DAY, 4, cfg) is None and te(0, 0, DAY, 5, cfg) == "close"


# ---- H17 --------------------------------------------------------------------------------------------------

def h17_case(short=True, touched=False):
    end = T("2024-01-10 12:00")
    sig = flat_h1(end, 150, px=1.0960 if short else 1.1040, rng=0.0010)
    if touched:
        b = sig[-10]
        sig[-10] = dict(b, h=1.1001) if short else dict(b, l=1.0999)
    if short:
        sig.append(bar(end, 1.0985, 1.09995, 1.0980, 1.0990))
    else:
        sig.append(bar(end, 1.1015, 1.1020, 1.10005, 1.1010))
    return sig


def test_h17_first_touch_rejection_sides():
    a = run("disc-H17", h17_case(True), [], "H1", htf_tf="NONE", lvl="00", tp_r=1.5)
    assert side(a) == "SELL"
    atr = a["indicators"]["atr"]
    assert a["stop_loss"] == pytest.approx(1.1000 + 0.5 * atr, abs=2e-5)
    assert (a["entry"] - a["take_profit"]) == pytest.approx(1.5 * (a["stop_loss"] - a["entry"]), abs=2e-5)
    a = run("disc-H17", h17_case(False), [], "H1", htf_tf="NONE", lvl="00")
    assert side(a) == "BUY"


def test_h17_requires_first_touch_and_rejection():
    assert side(run("disc-H17", h17_case(True, touched=True), [], "H1", htf_tf="NONE")) == "HOLD"
    sig = h17_case(True)
    sig[-1] = dict(sig[-1], c=1.0998)  # closes too near the level
    assert side(run("disc-H17", sig, [], "H1", htf_tf="NONE")) == "HOLD"


# ---- generic no-lookahead: a decision at bar i ignores every later bar ------------------------------------------

@pytest.mark.parametrize("name,maker", [("disc-H01", lambda: h01_case()[:2]), ("disc-H05", h05_case),
                                        ("disc-H09", h09_case), ("disc-H17", lambda: (h17_case(), []))])
def test_prefix_decisions_ignore_future(name, maker):
    sig, htf = maker()
    tf, sec = ("H4", 4 * H) if name == "disc-H09" else ("H1", H)
    hn = "NONE" if name == "disc-H17" else "D1"
    full = sig + [bar(sig[-1]["t"] + (k + 1) * sec, 2.0, 2.1, 1.9, 2.0) for k in range(5)]
    for i in range(len(sig) - 4, len(sig)):
        a = run(name, sig[:i + 1], htf, tf, htf_tf=hn, sec=sec)
        b = run(name, full[:i + 1], htf, tf, htf_tf=hn, sec=sec)
        assert (a["direction"], a["candidate"]) == (b["direction"], b["candidate"])


# ---- time-exit wrapper --------------------------------------------------------------------------------------

class _Plug:
    @staticmethod
    def time_exit(opened_t, bar_t, bar_sec, n_bars, cfg):
        return "close" if n_bars >= 2 else None


def test_time_exit_wrapper_closes_and_restores():
    orig = labeler.SimPosition.step
    with D.time_exits(_Plug, {}, H):
        pos = labeler.SimPosition("BUY", 1.1000, 1.0900, 1.1100, 0.00001, 0, be_at_r=0)
        assert pos.step(bar(0, 1.1000, 1.1010, 1.0990, 1.1005), 0.0001) is None
        hit = pos.step(bar(H, 1.1005, 1.1010, 1.0995, 1.1008), 0.0001)
        assert hit[0] == D.TIMEX and hit[1] == pytest.approx(1.1008)
        assert D.TIMEX in __import__("app.exits", fromlist=["x"]).AT_CLOSE_REASONS
        pos = labeler.SimPosition("BUY", 1.1000, 1.0900, 1.1100, 0.00001, 0, be_at_r=0)
        pos.step(bar(0, 1.1000, 1.1010, 1.0990, 1.1005), 0.0001)
        assert pos.step(bar(H, 1.1005, 1.1200, 1.0995, 1.1008), 0.0001)[0] == "TP"  # SL/TP first within the bar
    assert labeler.SimPosition.step is orig
    assert D.TIMEX not in __import__("app.exits", fromlist=["x"]).AT_CLOSE_REASONS
