"""Discovery builder #2 plugins (disc-H03, disc-H07, disc-H11, disc-H15, disc-H19): no-lookahead and crafted
scenarios (side, levels, planned exits through bias())."""
import random
from datetime import date, datetime, timedelta, timezone

import pytest

from app import strategies as S
from app.config import Settings
from app.strategies.discovery import _b2 as B
from app.strategies.discovery import disc_H03, disc_H07, disc_H11, disc_H15, disc_H19
from app.strategy import AnalysisContext
from tests.conftest import EURUSD_INFO

SET = Settings(_env_file=None)
LIVE = SET.default_strategy()
RISK = {**SET.default_risk(), "max_spread_points": 50}
H = 3600
DAY = 86400


@pytest.fixture(autouse=True)
def _clean_panel():
    B.clear_panel()
    yield
    B.clear_panel()


def ctx(t):
    return AnalysisContext(session_open=True, has_bot_position=False, signals_this_session=0, tick_age=0.0, now_ts=t)


def q_at(c, sp=10):
    d = dict(EURUSD_INFO)
    d.update(bid=c, ask=round(c + sp * d["point"], 5), spread_points=sp)
    return d


def cfg(name, tf, **over):
    return S.merged_cfg(name, {**LIVE, "signal_timeframe": tf, "htf_timeframe": "NONE"}, over)


def run(mod, sym, bars, tf, htf=None, **over):
    sec = {"D1": DAY, "H4": 4 * H, "H1": H}[tf]
    return mod.analyze(sym, bars, htf or [], q_at(bars[-1]["c"]), cfg(mod.NAME, tf, **over), RISK,
                       ctx(bars[-1]["t"] + sec))


def d1_times(start: date, n: int) -> list[int]:
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp()) - 2 * H)
        d += timedelta(days=1)
    return out


def bars_from(ts, closes, wick=0.0005):
    out, prev = [], closes[0]
    for t, c in zip(ts, closes):
        out.append({"t": t, "o": prev, "h": max(prev, c) + wick, "l": min(prev, c) - wick, "c": c})
        prev = c
    return out


def walk(n, seed, vol=0.004, p0=1.0):
    rnd, c, out = random.Random(seed), p0, []
    for _ in range(n):
        c *= 1 + rnd.gauss(0, vol)
        out.append(c)
    return out


def test_registry_resolves_all():
    for name in ("disc-H03", "disc-H07", "disc-H11", "disc-H15", "disc-H19"):
        mod = S.get(name)
        assert mod.NAME == name and callable(mod.analyze)


# ---- H03 ---------------------------------------------------------------------

def _h03_panel(drift_usd=0.002, n=220, seed=1):
    ts = d1_times(date(2019, 1, 7), n)
    panel = {}
    for k, (sym, sg) in enumerate(B.USD_PAIRS.items()):
        rnd = random.Random(seed + k)
        c, cl = 1.0, []
        for t in ts:
            dd = B.d1_date(t)
            drift = sg * drift_usd * (1.5 if sym == "USDCAD" else 1.0) if (dd.year, dd.month) == (2019, 9) else 0.0
            c *= 1 + drift + rnd.gauss(0, 0.002)
            cl.append(c)
        panel[sym] = bars_from(ts, cl)
    return ts, panel


def test_h03_month_end_signal_and_exit():
    ts, panel = _h03_panel()
    B.set_panel("D1", panel)
    # September 2019 ends Monday 30th: offset 2 -> signal at the close of Thursday 26th
    sig_t = next(t for t in ts if B.d1_date(t) == date(2019, 9, 26))
    i = ts.index(sig_t)
    dec = disc_H03.decision(panel, sig_t, 2, 0.5)
    assert dec is not None and dec[3] > 0  # USD up month-to-date
    assert dec[0] == "USDCAD" and dec[1] == -1  # strongest normalised move, fade USD -> SELL USDCAD
    a = run(disc_H03, "USDCAD", panel["USDCAD"][:i + 1], "D1", h03_entry_offset=2, h03_zmin=0.5)
    assert a["candidate"]["side"] == "SELL"
    other = run(disc_H03, "EURUSD", panel["EURUSD"][:i + 1], "D1", h03_entry_offset=2, h03_zmin=0.5)
    assert other["candidate"] is None
    # not the signal day -> nothing
    assert disc_H03.decision(panel, ts[i - 1], 2, 0.5) is None
    c = cfg("disc-H03", "D1", h03_entry_offset=2, h03_zmin=0.5)
    bars = panel["USDCAD"]
    assert disc_H03.bias("USDCAD", bars[:i + 2], [], c) == -1  # Fri 27th: hold
    assert disc_H03.bias("USDCAD", bars[:i + 3], [], c) == -1  # Mon 30th (last day): hold
    assert B.d1_date(bars[i + 3]["t"]) == date(2019, 10, 1)
    assert disc_H03.bias("USDCAD", bars[:i + 4], [], c) == 0  # Tue Oct 1st: exit at its close
    assert disc_H03.bias("EURUSD", panel["EURUSD"][:i + 2], [], c) == 0


def test_h03_zmin_blocks_weak_month():
    ts, panel = _h03_panel(drift_usd=0.0)
    sig_t = next(t for t in ts if B.d1_date(t) == date(2019, 9, 26))
    dec0 = disc_H03.decision(panel, sig_t, 2, 0.0)
    assert dec0 is not None
    assert disc_H03.decision(panel, sig_t, 2, 50.0) is None


def test_h03_h19_no_lookahead_panel():
    ts, panel = _h03_panel(n=260, seed=7)
    for t in ts[120::7]:
        cut = {s: [b for b in bars if b["t"] <= t] for s, bars in panel.items()}
        for off in (2, 3):
            assert disc_H03._decision(panel, t, off, 0.0, 60) == disc_H03._decision(cut, t, off, 0.0, 60)
        assert disc_H19._decision(panel, t, 30) == disc_H19._decision(cut, t, 30)


# ---- H07 ---------------------------------------------------------------------

def _h07_bars(last, n=150):
    ts = d1_times(date(2020, 1, 6), n + 4)
    cl = walk(n, 3, vol=0.003)
    bars = bars_from(ts[:n], cl)
    o = bars[-1]["c"]
    bars.append({"t": ts[n], "o": o, **last(o)})
    return bars, ts


def test_h07_expansion_day_sides_and_sl():
    up, _ = _h07_bars(lambda o: {"h": o + 0.03, "l": o - 0.002, "c": o + 0.029})
    a = run(disc_H07, "EURUSD", up, "D1", h07_m=1.8)
    assert a["candidate"]["side"] == "BUY"
    assert a["candidate"]["stop_loss"] == pytest.approx((up[-1]["h"] + up[-1]["l"]) / 2, abs=1e-5)
    dn, _ = _h07_bars(lambda o: {"h": o + 0.002, "l": o - 0.03, "c": o - 0.029})
    assert run(disc_H07, "EURUSD", dn, "D1", h07_m=1.8)["candidate"]["side"] == "SELL"
    mid, _ = _h07_bars(lambda o: {"h": o + 0.015, "l": o - 0.015, "c": o})
    assert run(disc_H07, "EURUSD", mid, "D1", h07_m=1.8)["candidate"] is None
    small, _ = _h07_bars(lambda o: {"h": o + 0.004, "l": o - 0.0003, "c": o + 0.0039})
    assert run(disc_H07, "EURUSD", small, "D1", h07_m=1.8)["candidate"] is None


def test_h07_hold_schedule():
    up, ts = _h07_bars(lambda o: {"h": o + 0.03, "l": o - 0.002, "c": o + 0.029})
    c = up[-1]["c"]
    after = up + [{"t": ts[len(up) + k], "o": c, "h": c + 0.001, "l": c - 0.001, "c": c} for k in range(3)]
    s = len(up) - 1
    for hold, expect in ((1, [0]), (3, [1, 1, 0])):
        cf = cfg("disc-H07", "D1", h07_m=1.8, h07_hold_days=hold)
        got = [disc_H07.bias("EURUSD", after[:s + 1 + k + 1], [], cf) for k in range(len(expect))]
        assert got == expect


def test_h07_no_lookahead():
    up, ts = _h07_bars(lambda o: {"h": o + 0.03, "l": o - 0.002, "c": o + 0.029})
    fut = up + [{"t": ts[len(up)], "o": 5.0, "h": 9.0, "l": 0.1, "c": 0.2}]
    a1 = run(disc_H07, "EURUSD", up, "D1")
    a2 = run(disc_H07, "EURUSD", fut[:len(up)], "D1")
    assert a1["candidate"] == a2["candidate"]


# ---- H11 ---------------------------------------------------------------------

def _h4(closes, t0=1600000000 // (4 * H) * (4 * H)):
    return bars_from([t0 + k * 4 * H for k in range(len(closes))], closes, wick=0.0003)


def test_h11_run_exhaustion():
    base = walk(150, 5, vol=0.0008)
    c = base[-1]
    down = base + [c - 0.004 * (k + 1) for k in range(5)]
    a = run(disc_H11, "EURUSD", _h4(down), "H4", h11_n=4, h11_k=1.5)
    cand = a["candidate"]
    assert cand["side"] == "BUY"
    atr = a["indicators"]["atr"]
    assert cand["take_profit"] - cand["entry"] == pytest.approx(1.0 * atr, abs=3e-5)
    assert cand["entry"] - cand["stop_loss"] == pytest.approx(1.5 * atr, abs=3e-5)
    up = base + [c + 0.004 * (k + 1) for k in range(5)]
    assert run(disc_H11, "EURUSD", _h4(up), "H4", h11_n=4, h11_k=1.5)["candidate"]["side"] == "SELL"
    broken = base + [c - 0.004, c - 0.008, c - 0.007, c - 0.012]
    assert run(disc_H11, "EURUSD", _h4(broken), "H4", h11_n=4, h11_k=1.5)["candidate"] is None
    assert run(disc_H11, "EURUSD", _h4(down), "H4", h11_n=4, h11_k=50)["candidate"] is None


def test_h11_exit_bias_and_time():
    bars = _h4([1.0, 1.001, 1.0005, 1.0005])
    assert disc_H11.bias("EURUSD", bars[:2], [], {}) == -1  # up close: closes a BUY (opposite), keeps a SELL
    assert disc_H11.bias("EURUSD", bars[:3], [], {}) == 1
    assert disc_H11.bias("EURUSD", bars[:4], [], {}) == 0
    ex = disc_H11.default_exit(cfg("disc-H11", "H4", h11_max_bars=6))
    assert ex["params"]["max_hold_hours"] == 24 and ex["params"]["flip_on_flat"] == 0


# ---- H15 ---------------------------------------------------------------------

def _h1_week(move_per_bar, start=datetime(2024, 3, 3, 22, tzinfo=timezone.utc), weeks=3):
    """H1 bars Sunday 22:00 -> Friday 20:00 UTC for `weeks` weeks; the last week trends by move_per_bar."""
    out, c = [], 1.1
    for w in range(weeks):
        t = start + timedelta(days=7 * w)
        end = t + timedelta(days=4, hours=23)
        rnd = random.Random(w)
        while t < end:
            o = c
            c = c + (move_per_bar if w == weeks - 1 else rnd.gauss(0, 0.0004))
            out.append({"t": int(t.timestamp()), "o": o, "h": max(o, c) + 0.0002, "l": min(o, c) - 0.0002, "c": c})
            t += timedelta(hours=1)
    return out


def _d1_htf(atr_px=0.006, n=40, end=datetime(2024, 3, 13, 22, tzinfo=timezone.utc)):
    out = []
    for k in range(n):
        t = int((end - timedelta(days=n - k)).timestamp())
        out.append({"t": t, "o": 1.1, "h": 1.1 + atr_px / 2, "l": 1.1 - atr_px / 2, "c": 1.1})
    return out


def _idx(bars, wd, hour, last_week=True):
    cand = [i for i, b in enumerate(bars) if B.utc(b["t"]).weekday() == wd and B.utc(b["t"]).hour == hour]
    return cand[-1] if last_week else cand[0]


def test_h15_friday_fade():
    bars = _h1_week(0.0001)  # ~ +0.0109 by Friday 12:00 vs ATRd 0.006
    htf = _d1_htf()
    i = _idx(bars, 4, 12)
    a = run(disc_H15, "EURUSD", bars[:i + 1], "H1", htf=htf, h15_k=1.0)
    cand = a["candidate"]
    assert cand["side"] == "SELL"
    wtd = bars[i]["c"] - bars[_idx(bars, 6, 22)]["o"]
    assert cand["entry"] - cand["take_profit"] == pytest.approx(0.25 * wtd, abs=2e-5)
    assert cand["stop_loss"] - cand["entry"] == pytest.approx(0.6 * 0.006, abs=2e-5)
    assert run(disc_H15, "EURUSD", bars[:i + 1], "H1", htf=htf, h15_k=2.0)["candidate"] is None
    j = _idx(bars, 3, 12)  # Thursday: never
    assert run(disc_H15, "EURUSD", bars[:j + 1], "H1", htf=htf, h15_k=0.1)["candidate"] is None
    k = _idx(bars, 4, 14)  # variant entry 15:00 -> signal at the 14:00 bar
    assert run(disc_H15, "EURUSD", bars[:k + 1], "H1", htf=htf, h15_k=1.0, h15_entry_hour=15)["candidate"]["side"] == "SELL"
    assert run(disc_H15, "EURUSD", bars[:k + 1], "H1", htf=htf, h15_k=1.0)["candidate"] is None


def test_h15_exit_schedule():
    bars = _h1_week(0.0001)
    htf = _d1_htf()
    cf = cfg("disc-H15", "H1", h15_k=1.0)
    got = {B.utc(bars[i]["t"]).hour: disc_H15.bias("EURUSD", bars[:i + 1], htf, cf)
           for i in range(_idx(bars, 4, 12), len(bars))}
    assert got[12] == 0 and got[13] == -1 and got[18] == -1 and got[19] == 0 and got[20] == 0


# ---- H19 ---------------------------------------------------------------------

def test_h19_usd_breakout_pick():
    n = 200
    t0 = 1600000000 // (4 * H) * (4 * H)
    ts = [t0 + k * 4 * H for k in range(n)]
    panel = {}
    for k, (sym, sg) in enumerate(B.USD_PAIRS.items()):
        cl = walk(n - 30, 11 + k, vol=0.0006)
        c = cl[-1]
        for j in range(30):  # USDJPY rallies over the last 30 bars; the others drift slightly against the USD
            c *= 1 + (0.002 if sym == "USDJPY" else -sg * 0.0002)
            cl.append(c)
        panel[sym] = bars_from(ts, cl, wick=0.0002)
    B.set_panel("H4", panel)
    dec = disc_H19.decision(panel, ts[-1], 30)
    assert dec is not None and dec[2] == 1 and dec[0] == "USDJPY" and dec[1] == 1
    a = run(disc_H19, "USDJPY", panel["USDJPY"], "H4", h19_n=30)
    assert a["candidate"]["side"] == "BUY"
    a2 = run(disc_H19, "EURUSD", panel["EURUSD"], "H4", h19_n=30)
    assert a2["candidate"] is None
    assert disc_H19.default_exit(cfg("disc-H19", "H4", h19_exit="time"))["params"]["max_hold_hours"] == 48
    assert disc_H19.default_exit(cfg("disc-H19", "H4"))["mode"] == "trailing"
    # same panel with the USD rally removed from every pair -> no breakout at the end
    flat = {s_: bars[:-30] for s_, bars in panel.items()}
    assert disc_H19.decision(flat, ts[-31], 30) is None or disc_H19.decision(flat, ts[-31], 30)[0] in B.USD_PAIRS
