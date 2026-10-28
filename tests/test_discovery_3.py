"""Discovery builder #3 plugins (disc-h04, disc-h08, disc-h12, disc-h16, disc-h20): crafted scenarios (side, levels),
no-lookahead of the research hooks, tail independence and the clock-time exit helper."""
import math
import random
from datetime import date, datetime, timedelta, timezone

import pytest

from app import exits, labeler
from app import strategies as S
from app.config import Settings
from app.strategies.discovery import _disc3 as D
from app.strategies.discovery import disc_h04, disc_h08, disc_h12, disc_h16, disc_h20
from app.strategy import AnalysisContext

SET = Settings(_env_file=None)
RISK = SET.default_risk()
H, DAY = 3600, 86400
MINE = ["disc-h04", "disc-h08", "disc-h12", "disc-h16", "disc-h20"]


def ts(y, m, d, h=0):
    return int(datetime(y, m, d, h, tzinfo=timezone.utc).timestamp())


def quote(c, sym="EURUSD", sp=10):
    jpy = sym.endswith("JPY")
    pt = 0.001 if jpy else 0.00001
    dg = 3 if jpy else 5
    return {"bid": c, "ask": round(c + sp * pt, dg), "spread_points": sp, "digits": dg, "point": pt, "stops_level": 0}


def ctx(now):
    return AnalysisContext(session_open=True, has_bot_position=False, signals_this_session=0, tick_age=0.0,
                           now_ts=now, open_exposures=[], corr_limit=1)


def cfg(name, tf="H1", **over):
    live = {**SET.default_strategy(), "signal_timeframe": tf, "htf_timeframe": "NONE"}
    return S.merged_cfg(name, live, over)


def run(mod, bars, htf=None, sym="EURUSD", now=None, **over):
    tf = "D1" if mod is disc_h12 else "H1"
    sec = DAY if tf == "D1" else H
    now = now or bars[-1]["t"] + sec
    return mod.analyze(sym, bars, htf or [], quote(bars[-1]["c"], sym), cfg(mod.NAME, tf, **over), RISK, ctx(now))


def hourly(t_from, n, start=1.1, seed=1, vol=0.0003, wick=0.0002, skip_weekend=True):
    rng = random.Random(seed)
    out, c, t = [], start, t_from
    while len(out) < n:
        wd = datetime.fromtimestamp(t, timezone.utc).weekday()
        hr = datetime.fromtimestamp(t, timezone.utc).hour
        if skip_weekend and (wd == 5 or (wd == 4 and hr >= 22) or (wd == 6 and hr < 22)):
            t += H
            continue
        o = c
        c = o + rng.gauss(0, vol)
        out.append({"t": t, "o": o, "h": max(o, c) + wick, "l": min(o, c) - wick, "c": c})
        t += H
    return out


def d1_bars(days, start=1.1, rng_w=0.01):
    """D1 bars on broker days (start = 00:00 Europe/Athens of each date), flat closes, range rng_w."""
    out = []
    for d in days:
        t = int(datetime(d.year, d.month, d.day, tzinfo=D.BROKER_TZ).timestamp())
        out.append({"t": t, "o": start, "h": start + rng_w / 2, "l": start - rng_w / 2, "c": start})
    return out


def weekdays(end: date, n: int):
    out, d = [], end
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= timedelta(days=1)
    return out[::-1]


# ---- registry ------------------------------------------------------------------------------------------------

def test_registered_research_only():
    from app.strategies.discovery import discovery_names
    for n in MINE:
        assert n in discovery_names()
        assert S.get(n).NAME == n
        assert n not in S.names(include_research=True)
        assert callable(S.get(n).analyze) and callable(S.get(n).default_exit)


# ---- H04 -----------------------------------------------------------------------------------------------------

def _week_then_gap(gap, close_off):
    bars = hourly(ts(2024, 1, 1), 118)  # Mon 00:00 .. Fri 21:00 UTC
    assert datetime.fromtimestamp(bars[-1]["t"], timezone.utc).strftime("%a %H") == "Fri 21"
    fri = bars[-1]["c"]
    o = fri + gap
    c = o + close_off
    bars.append({"t": ts(2024, 1, 7, 22), "o": o, "h": max(o, c) + 0.0001, "l": min(o, c) - 0.0001, "c": c})
    return bars, fri


def test_h04_gap_up_fades_to_friday_close():
    bars, fri = _week_then_gap(0.004, -0.0005)
    a = run(disc_h04, bars, h04_g=0.5, h04_sl_mult=1.0)
    cand = a["candidate"]
    assert cand and cand["side"] == "SELL"
    assert cand["take_profit"] == round(fri, 5)
    assert cand["stop_loss"] > bars[-1]["c"] + 0.0035  # SL >= 1×|gap| above the entry


def test_h04_gap_down_buys_and_filled_gap_holds():
    bars, fri = _week_then_gap(-0.004, 0.0005)
    a = run(disc_h04, bars)
    assert a["candidate"]["side"] == "BUY" and a["candidate"]["take_profit"] == round(fri, 5)
    bars2, _ = _week_then_gap(0.004, -0.0045)  # first bar already closed below Friday's close
    assert run(disc_h04, bars2)["candidate"] is None
    small, _ = _week_then_gap(0.00005, 0.0)
    assert run(disc_h04, small)["candidate"] is None
    assert run(disc_h04, bars[:-1])["candidate"] is None  # Friday bar: not the first bar of the week


def test_h04_exit_deadline_monday_13utc():
    c = disc_h04.PARAMS
    assert disc_h04.exit_deadline(ts(2024, 1, 7, 23), c) == ts(2024, 1, 8, 13)
    assert disc_h04.exit_deadline(ts(2024, 7, 7, 22), c) == ts(2024, 7, 8, 13)
    assert disc_h04.exit_deadline(ts(2024, 1, 8, 0), c) == ts(2024, 1, 8, 13)


# ---- H08 -----------------------------------------------------------------------------------------------------

def _h08_case(sweep_hour=8, extra_after=0):
    # broker day Wed 2024-01-10 starts Tue 22:00 UTC; prior day D1 (Tue): H 1.1050, L 1.0950
    htf = d1_bars(weekdays(date(2024, 1, 9), 30), start=1.1, rng_w=0.01)
    bars = hourly(ts(2024, 1, 3), 400, start=1.1, vol=0.00005, wick=0.0001)
    bars = [b for b in bars if b["t"] < ts(2024, 1, 10, sweep_hour)]
    for b in bars:  # keep everything inside the prior-day range
        for k in "ohlc":
            b[k] = min(max(b[k], 1.0990), 1.1010)
    bars.append({"t": ts(2024, 1, 10, sweep_hour), "o": 1.1005, "h": 1.1065, "l": 1.1000, "c": 1.1045})
    for k in range(extra_after):
        bars.append({"t": ts(2024, 1, 10, sweep_hour + 1 + k), "o": 1.1045, "h": 1.1048, "l": 1.1030, "c": 1.1040})
    return bars, htf


def test_h08_sweep_above_pdh_and_reclaim_sells():
    bars, htf = _h08_case()
    for mode in ("mid", "2R"):
        a = run(disc_h08, bars, htf, h08_x=0.1, h08_tp_mode=mode)
        cand = a["candidate"]
        assert cand and cand["side"] == "SELL"
        assert cand["stop_loss"] == pytest.approx(1.1065 + 0.1 * 0.01, abs=2e-5)
        if mode == "mid":
            assert cand["take_profit"] == pytest.approx(1.1, abs=1e-5)
        else:
            assert cand["take_profit"] == pytest.approx(1.1045 - 2 * (cand["stop_loss"] - 1.1045), abs=2e-5)


def test_h08_one_trade_per_side_per_day_window_and_threshold():
    bars, htf = _h08_case(extra_after=1)
    assert run(disc_h08, bars, htf)["candidate"] is None  # second qualifying bar of the day
    late, htf2 = _h08_case(sweep_hour=18)
    assert run(disc_h08, late, htf2)["candidate"] is None  # next open 19:00 UTC: outside 07-17
    early, htf3 = _h08_case()
    assert run(disc_h08, early, htf3, h08_x=1.0)["candidate"] is None  # sweep 0.15×ATRd < 1.0×ATRd


def test_h08_mirror_long_at_pdl():
    bars, htf = _h08_case()
    m = 2.2
    flip = [{"t": b["t"], "o": m - b["o"], "h": m - b["l"], "l": m - b["h"], "c": m - b["c"]} for b in bars]
    fhtf = [{"t": b["t"], "o": m - b["o"], "h": m - b["l"], "l": m - b["h"], "c": m - b["c"]} for b in htf]
    a = run(disc_h08, flip, fhtf, h08_x=0.1)
    assert a["candidate"] and a["candidate"]["side"] == "BUY"
    assert a["candidate"]["stop_loss"] == pytest.approx(m - 1.1065 - 0.001, abs=2e-5)


def test_h08_exit_deadline():
    assert disc_h08.exit_deadline(ts(2024, 1, 10, 9), disc_h08.PARAMS) == ts(2024, 1, 10, 20)


# ---- H12 -----------------------------------------------------------------------------------------------------

SYMS = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "EURJPY", "GBPJPY", "NZDUSD", "EURGBP", "EURCHF", "AUDJPY"]


def _universe(strong="EUR", weak="JPY", n=150, end=date(2024, 3, 1), seed=3):
    rng = random.Random(seed)
    days = weekdays(end, n)
    val = {c: 1.0 for c in disc_h12.CCYS}
    path = []
    for _ in days:
        for c in val:
            drift = 0.004 if c == strong else -0.004 if c == weak else 0.0
            val[c] *= math.exp(drift + rng.gauss(0, 0.002))
        path.append(dict(val))
    uni = {}
    for s in SYMS:
        bars, prev = [], None
        for d, v in zip(days, path):
            t = int(datetime(d.year, d.month, d.day, tzinfo=D.BROKER_TZ).timestamp())
            c = v[s[:3]] / v[s[3:]] * (100 if s.endswith("JPY") else 1)
            o = prev if prev is not None else c
            bars.append({"t": t, "o": o, "h": max(o, c) * 1.002, "l": min(o, c) * 0.998, "c": c})
            prev = c
        uni[s] = bars
    return uni


@pytest.fixture
def universe():
    yield
    D.set_universe(None)


def test_h12_buys_strong_vs_weak_only_on_that_pair(universe):
    uni = _universe()
    D.set_universe(uni)
    assert D.d1_day(uni["EURJPY"][-1]["t"]).weekday() == 4
    a = run(disc_h12, uni["EURJPY"], sym="EURJPY", h12_weeks=4)
    assert a["candidate"] and a["candidate"]["side"] == "BUY"
    assert run(disc_h12, uni["EURUSD"], sym="EURUSD", h12_weeks=4)["candidate"] is None
    assert run(disc_h12, uni["EURJPY"][:-1], sym="EURJPY", h12_weeks=4)["candidate"] is None  # Thursday


def test_h12_sell_when_strong_is_quote(universe):
    uni = _universe(strong="JPY", weak="EUR")
    D.set_universe(uni)
    a = run(disc_h12, uni["EURJPY"], sym="EURJPY", h12_weeks=4)
    assert a["candidate"] and a["candidate"]["side"] == "SELL"


def test_h12_no_lookahead_universe(universe):
    uni = _universe()
    cut = uni["EURJPY"][-1]["t"] + DAY  # decision time
    D.set_universe(uni)
    base = run(disc_h12, uni["EURJPY"], sym="EURJPY", now=cut)
    poisoned = {s: list(b) for s, b in uni.items()}
    for s, b in poisoned.items():  # wild bars after the decision: must be ignored
        for k in range(1, 6):
            t = b[-1]["t"] + k * DAY
            px = b[-1]["c"] * (5.0 if s.startswith("AUD") else 0.2)
            b.append({"t": t, "o": px, "h": px, "l": px, "c": px})
    D.set_universe(poisoned)
    again = run(disc_h12, uni["EURJPY"], sym="EURJPY", now=cut)
    assert again["candidate"] == base["candidate"] and again["indicators"]["strength"] == base["indicators"]["strength"]


def test_h12_pick_falls_back_to_existing_pair():
    st = {"CHF": 2.0, "EUR": 1.0, "USD": 0.0, "GBP": 0.0, "JPY": -0.5, "AUD": 0.0, "NZD": 0.0, "CAD": -2.0}
    # CHFCAD / CADCHF do not exist; best existing spread is EURCHF SELL (3-?): EUR-CHF = -1 -> |1|; USDCAD BUY = 2
    assert disc_h12.pick(st, SYMS) == ("USDCAD", "BUY")
    assert disc_h12.pick({"EUR": 1.0, "USD": -1.0}, SYMS) == ("EURUSD", "BUY")


# ---- H16 -----------------------------------------------------------------------------------------------------

@pytest.fixture
def history():
    yield
    D.set_history(None)


def _h16_bars(last_t, n=400, drift=0.0, seed=5):
    bars = hourly(last_t - (n + 200) * H, n + 200, seed=seed)
    bars = [b for b in bars if b["t"] <= last_t][-n:]
    if drift:
        for b in bars:  # add a deterministic move inside the 07-12 UTC window
            hr = b["t"] % DAY // H
            if 7 <= hr < 12:
                b["c"] = b["o"] + drift
                b["h"], b["l"] = max(b["o"], b["c"]) + 0.0001, min(b["o"], b["c"]) - 0.0001
    return bars


def test_h16_eur_session_sells_at_07utc_only():
    htf = d1_bars(weekdays(date(2024, 1, 9), 30))
    bars = _h16_bars(ts(2024, 1, 10, 6))
    a = run(disc_h16, bars, htf, h16_variant="eur")
    assert a["candidate"] and a["candidate"]["side"] == "SELL"
    sl_dist = a["candidate"]["stop_loss"] - bars[-1]["c"]
    assert sl_dist == pytest.approx(0.01, rel=0.05)  # 1×ATR14 D1
    assert run(disc_h16, bars[:-1], htf, h16_variant="eur")["candidate"] is None
    assert run(disc_h16, bars, htf, sym="GBPUSD", h16_variant="eur")["candidate"] is None
    us = _h16_bars(ts(2024, 1, 10, 12))
    assert run(disc_h16, us, htf, h16_variant="us")["candidate"]["side"] == "BUY"
    assert disc_h16.default_exit(cfg("disc-h16", h16_variant="us"))["params"]["max_hold_hours"] == 7


def test_h16_filter_uses_prior_days_and_history_hook(history):
    htf = d1_bars(weekdays(date(2024, 1, 31), 30))
    full = _h16_bars(ts(2024, 2, 1, 6), n=1000, drift=-0.0002)
    sig = full[-400:]
    assert run(disc_h16, sig, htf, h16_variant="eur", h16_filter=1)["candidate"] is None  # < 20 days in 400 bars
    D.set_history({"EURUSD": full})
    a = run(disc_h16, sig, htf, h16_variant="eur", h16_filter=1)
    assert a["candidate"] and a["candidate"]["side"] == "SELL"
    up = _h16_bars(ts(2024, 2, 1, 6), n=1000, drift=+0.0002)
    D.set_history({"EURUSD": up})
    assert run(disc_h16, up[-400:], htf, h16_variant="eur", h16_filter=1)["candidate"] is None


def test_extend_back_never_adds_newer_bars(history):
    full = hourly(ts(2024, 1, 1), 600)
    sig = full[100:300]
    D.set_history({"EURUSD": full})
    ext = D.extend_back("EURUSD", sig, 50)
    assert ext[-200:] == sig and len(ext) == 250 and ext[49]["t"] < sig[0]["t"]
    assert max(b["t"] for b in ext) == sig[-1]["t"]


# ---- H20 -----------------------------------------------------------------------------------------------------

def _spike(n1_h_extra=0.0, n1_close=1.1050):
    bars = hourly(ts(2024, 1, 2), 150, vol=0.0002, wick=0.0001)
    t = bars[-1]["t"] + H
    for b in bars:
        for k in "ohlc":
            b[k] = b[k] - bars[-1]["c"] + 1.1
    bars.append({"t": t, "o": 1.1000, "h": 1.1065, "l": 1.0998, "c": 1.1060})
    bars.append({"t": t + H, "o": 1.1058, "h": 1.1062 + n1_h_extra, "l": 1.1045, "c": n1_close})
    return bars


def test_h20_spike_fade_levels():
    bars = _spike()
    a = run(disc_h20, bars, h20_m=2.5, h20_tp_frac=0.5)
    cand = a["candidate"]
    assert cand and cand["side"] == "SELL"
    atr = D.atr_last(bars[-(24 + 60):-2], 24)
    assert cand["stop_loss"] == pytest.approx(1.1065 + 0.2 * atr, abs=2e-5)
    assert cand["take_profit"] == pytest.approx(1.1060 - 0.5 * 0.0060, abs=1e-5)
    assert disc_h20.default_exit(cfg("disc-h20"))["params"]["max_hold_hours"] == 6


def test_h20_no_trade_when_n1_extends_or_closes_with_spike():
    assert run(disc_h20, _spike(n1_h_extra=0.001))["candidate"] is None
    assert run(disc_h20, _spike(n1_close=1.1061))["candidate"] is None
    assert run(disc_h20, _spike(), h20_m=50)["candidate"] is None


# ---- generic: tail independence (decisions depend only on closed bars of a fixed tail) ---------------------------

@pytest.mark.parametrize("mod", [disc_h04, disc_h20])
def test_tail_independence(mod):
    bars = hourly(ts(2024, 1, 1), 900, vol=0.0006, seed=11)
    for k in range(450, 900, 7):
        full = run(mod, bars[k - 400:k])
        short = run(mod, bars[k - 300:k])
        assert full["candidate"] == short["candidate"], k


# ---- clock exit helper ------------------------------------------------------------------------------------------

def test_clock_exit_closes_at_deadline_and_restores():
    orig_step, orig_reasons = labeler.SimPosition.step, exits.AT_CLOSE_REASONS
    t0 = ts(2024, 1, 8, 0)
    pos = labeler.SimPosition("BUY", 1.1, 1.09, 1.2, 0.00001, t0, be_at_r=0)
    bar = lambda t, c: {"t": t, "o": 1.1, "h": 1.101, "l": 1.099, "c": c}  # noqa: E731
    with D.clock_exit(lambda t: t + 2 * H, H):
        assert "CLOCK" in exits.AT_CLOSE_REASONS
        assert pos.step(bar(t0, 1.1), 0.0001) is None
        assert pos.step(bar(t0 + H, 1.1005), 0.0001) == ("CLOCK", 1.1005)
    assert labeler.SimPosition.step is orig_step and exits.AT_CLOSE_REASONS == orig_reasons
    pos2 = labeler.SimPosition("BUY", 1.1, 1.09, 1.2, 0.00001, t0, be_at_r=0)
    with D.clock_exit(lambda t: t + H, H):  # SL inside the deadline bar keeps priority
        assert pos2.step({"t": t0, "o": 1.1, "h": 1.1, "l": 1.08, "c": 1.095}, 0.0)[0] == "SL"
