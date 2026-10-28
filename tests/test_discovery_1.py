"""Stage D discovery builder #1: research plugins disc-h02 / h06 / h10 / h14 / h18 (app/strategies/discovery).

Registry resolution, no-lookahead (decisions depend only on closed bars up to the decision bar, including the
cross-symbol factor data of disc-h14), tail invariance, and crafted scenarios for the side / SL / TP of each rule."""
import math
import random
from datetime import datetime, timezone

import pytest

from app import backtest
from app import strategies as S
from app.config import Settings
from app.strategies.discovery import discovery_names, get_discovery
from app.strategies.discovery import disc_h02, disc_h06, disc_h10, disc_h14, disc_h18
from app.strategy import AnalysisContext
from tests.conftest import EURUSD_INFO
from tests.test_stage_b_backtest import T0, db, store  # noqa: F401
from tests.test_stage_c2_strategies import d1_params, seed_d1

SET = Settings(_env_file=None)
CFG = SET.default_strategy()
RISK = SET.default_risk()
NAMES = ["disc-h02", "disc-h06", "disc-h10", "disc-h14", "disc-h18"]
TFS = {"disc-h02": ("H1", "D1"), "disc-h06": ("H4", "NONE"), "disc-h10": ("D1", "NONE"), "disc-h14": ("H4", "NONE"),
       "disc-h18": ("H4", "NONE")}
H, DAY = 3600, 86400
JPY_INFO = dict(EURUSD_INFO, point=0.001, digits=3, tick_size=0.001)


def ctx(now):
    return AnalysisContext(session_open=True, has_bot_position=False, signals_this_session=0, tick_age=0.0,
                           now_ts=now)


def q_at(c, info=EURUSD_INFO, sp=10):
    d = dict(info)
    d.update(bid=c, ask=round(c + sp * d["point"], d["digits"] + 3), spread_points=sp)
    return d


def cfg_of(name, **over):
    sig, htf = TFS[name]
    return S.merged_cfg(name, {**CFG, "signal_timeframe": sig, "htf_timeframe": htf}, over)


def run_p(name, sym, sig, htf=None, info=EURUSD_INFO, **over):
    sec = {"H1": H, "H4": 4 * H, "D1": DAY}[TFS[name][0]]
    return S.get(name).analyze(sym, sig, htf or [], q_at(sig[-1]["c"], info), cfg_of(name, **over), RISK,
                               ctx(sig[-1]["t"] + sec))


def walk(n, sec, seed, t0, p0=1.10, vol=0.0012):
    """Weekday random walk (weekend bars skipped) with drift regimes."""
    rng = random.Random(seed)
    out, p, drift, t = [], p0, 0.0, t0
    while len(out) < n:
        if (t // DAY + 3) % 7 < 5:
            if rng.random() < 0.03:
                drift = rng.gauss(0, vol * 0.3)
            o = p
            p = o + drift + rng.gauss(0, vol)
            out.append({"t": t, "o": round(o, 5), "h": round(max(o, p) + abs(rng.gauss(0, vol / 2)), 5),
                        "l": round(min(o, p) - abs(rng.gauss(0, vol / 2)), 5), "c": round(p, 5), "sp": 3})
        t += sec
    return out


def strip(a):
    return {k: a[k] for k in ("direction", "entry", "stop_loss", "take_profit", "candidate")}


# ---- registry ---------------------------------------------------------------------------------------

def test_registry_resolves_discovery_plugins():
    for n in NAMES:
        assert n in discovery_names()
        assert S.get(n) is get_discovery(n)
        assert n not in S.names(include_research=True)  # never offered to the live engine / UI
        assert callable(S.get(n).default_exit)
    for n in ("disc-h10", "disc-h14"):
        assert callable(S.get(n).bias)  # signal_flip exits


# ---- no lookahead / tail invariance -----------------------------------------------------------------

def _series(name, seed=3):
    tf = TFS[name][0]
    sec = {"H1": H, "H4": 4 * H, "D1": DAY}[tf]
    t0 = (T0 - 900 * sec) // DAY * DAY
    return walk(900, sec, seed, t0, vol=0.0012 if tf != "D1" else 0.006), sec


@pytest.mark.parametrize("name", ["disc-h06", "disc-h10", "disc-h18"])
def test_decisions_ignore_future_bars_and_extra_history(name):
    base, sec = _series(name)
    fired = 0
    for i in range(500, 700):
        over = {"c": 1.1, "range_mult": 1.0} if name == "disc-h06" else {}  # loose so a plain walk triggers
        a = run_p(name, "EURUSD", base[: i + 1], **over)
        assert strip(a) == strip(run_p(name, "EURUSD", base[i - 399: i + 1], **over))  # only the 400-bar tail matters
        fired += a["candidate"] is not None
    assert fired > 0  # the rule actually triggers on a random walk


def test_h14_factor_uses_only_bars_up_to_decision():
    t0 = (T0 - 600 * 4 * H) // DAY * DAY
    bars = {s: walk(600, 4 * H, 40 + k, t0, p0=150.0 if s == "USDJPY" else 1.3 if s == "USDCAD" else 1.1)
            for k, s in enumerate(disc_h14.USD_PAIRS)}
    disc_h14.set_factor_bars("H4", bars)
    cfg = cfg_of("disc-h14", N=6, z0=1.5)
    t_dec = bars["EURUSD"][400]["t"]
    z = disc_h14.zscore("EURUSD", t_dec, cfg, "H4")
    assert z is not None
    rng = random.Random(1)
    alt = {s: [b if b["t"] <= t_dec else dict(b, c=b["c"] * (1 + rng.gauss(0, 0.05))) for b in v]
           for s, v in bars.items()}
    disc_h14.set_factor_bars("H4", alt)
    assert disc_h14.zscore("EURUSD", t_dec, cfg, "H4") == pytest.approx(z, abs=1e-12)
    assert disc_h14.zscore("EURGBP", t_dec, cfg, "H4") is None  # not a USD pair
    assert disc_h14.zscore("EURUSD", t_dec + 1, cfg, "H4") is None  # not a common bar time


# ---- crafted scenarios --------------------------------------------------------------------------------

def _h1_day(day0, open_px, fix_px, open_h, fix_h):
    """H1 bars of one day: flat until open_h, linear rise to fix_px at the close of the fix_h bar."""
    out, p = [], open_px
    steps = fix_h - open_h + 1
    for h in range(0, fix_h + 1):
        o = p
        if h >= open_h:
            p = open_px + (fix_px - open_px) * (h - open_h + 1) / steps
        out.append({"t": day0 + h * H, "o": round(o, 5), "h": round(max(o, p) + 0.0001, 5),
                    "l": round(min(o, p) - 0.0001, 5), "c": round(p, 5), "sp": 3})
    return out


def _d1_flat(day0, n=30, rng_px=0.01):
    return [{"t": day0 - (n - k) * DAY - 2 * H, "o": 1.10, "h": 1.10 + rng_px / 2, "l": 1.10 - rng_px / 2, "c": 1.10,
             "sp": 3} for k in range(n)]


@pytest.mark.parametrize("date,utc_open,utc_fix", [("2026-01-14", 7, 15), ("2026-07-15", 6, 14)])
def test_h02_fix_reversal_side_levels_and_dst(date, utc_open, utc_fix):
    day0 = int(datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())
    prev = walk(150, H, 1, day0 - 200 * H, vol=0.0001)
    prev = [b for b in prev if b["t"] < day0]
    bars = prev + _h1_day(day0, 1.1000, 1.1080, utc_open, utc_fix)  # +80 pips = 0.8 x ATRd (ATR 0.01)
    htf = _d1_flat(day0)
    a = run_p("disc-h02", "EURUSD", bars, htf, k=0.7, retr=0.5)
    assert a["direction"] == "SELL", a["reasons"]
    entry = a["entry"]
    assert entry == pytest.approx(1.1080)
    assert a["stop_loss"] == pytest.approx(entry + 0.005, abs=1e-5)  # 0.5 x ATRd
    assert a["take_profit"] == pytest.approx(entry - 0.5 * 0.0080, abs=1e-5)
    assert run_p("disc-h02", "EURUSD", bars, htf, k=0.9)["candidate"] is None  # 0.8 ATR < 0.9
    assert run_p("disc-h02", "EURUSD", bars[:-1], htf, k=0.4)["candidate"] is None  # not the fix bar
    down = prev + _h1_day(day0, 1.1000, 1.0920, utc_open, utc_fix)
    b = run_p("disc-h02", "EURUSD", down, htf, k=0.7, retr=0.33)
    assert b["direction"] == "BUY" and b["take_profit"] > b["entry"] > b["stop_loss"]
    assert disc_h02.default_exit({})["params"]["max_hold_hours"] == 5


def _h4_compressed(n=300, seed=5):
    """Wide random walk then 10 very quiet bars (ATR5/ATR100 << 0.6)."""
    t0 = (T0 - (n + 20) * 4 * H) // DAY * DAY
    bars = walk(n, 4 * H, seed, t0, vol=0.0015)
    p = bars[-1]["c"]
    t = bars[-1]["t"]
    for _ in range(10):
        t += 4 * H
        bars.append({"t": t, "o": p, "h": p + 0.0002, "l": p - 0.0002, "c": p, "sp": 3})
    return bars, p, t


def test_h06_expansion_after_compression():
    bars, p, t = _h4_compressed()
    up = bars + [{"t": t + 4 * H, "o": p, "h": p + 0.0050, "l": p - 0.0002, "c": p + 0.0048, "sp": 3}]
    a = run_p("disc-h06", "EURUSD", up, c=0.6)
    assert a["direction"] == "BUY", a["reasons"]
    atr_s = disc_h06.signal(up[-400:], cfg_of("disc-h06"))[1]
    sl_dist = a["entry"] - a["stop_loss"]
    assert atr_s * 1.0 - 1e-5 <= sl_dist <= atr_s * 2.0 + 1e-5  # floored / capped
    assert a["take_profit"] - a["entry"] == pytest.approx(2 * sl_dist, abs=2e-5)
    dn = bars + [{"t": t + 4 * H, "o": p, "h": p + 0.0002, "l": p - 0.0050, "c": p - 0.0049, "sp": 3}]
    assert run_p("disc-h06", "EURUSD", dn)["direction"] == "SELL"
    mid = bars + [{"t": t + 4 * H, "o": p, "h": p + 0.0025, "l": p - 0.0025, "c": p, "sp": 3}]
    assert run_p("disc-h06", "EURUSD", mid)["candidate"] is None  # close mid-range
    tr = run_p("disc-h06", "EURUSD", up, exit_variant="trail")
    assert tr["take_profit"] - tr["entry"] == pytest.approx(10 * (tr["entry"] - tr["stop_loss"]), abs=5e-5)
    assert disc_h06.default_exit({"exit_variant": "trail"})["mode"] == "trailing"


def _d1_trend_then_drop(up=True):
    t0 = (T0 - 400 * DAY) // DAY * DAY
    bars, p, t = [], 1.0, t0
    while len(bars) < 300:
        if (t // DAY + 3) % 7 < 5:
            o = p
            p = o * (1.002 if up else 0.998) + (0.002 if len(bars) % 2 else -0.002)
            bars.append({"t": t, "o": o, "h": max(o, p) + 0.002, "l": min(o, p) - 0.002, "c": p, "sp": 3})
        t += DAY
    for _ in range(3):  # sharp counter-move: RSI2 extreme
        while (t // DAY + 3) % 7 >= 5:
            t += DAY
        o = p
        p = o * (0.985 if up else 1.015)
        bars.append({"t": t, "o": o, "h": max(o, p) + 0.001, "l": min(o, p) - 0.001, "c": p, "sp": 3})
        t += DAY
    return bars


def test_h10_rsi2_entries_filter_and_exit_bias():
    bars = _d1_trend_then_drop(up=True)
    a = run_p("disc-h10", "EURUSD", bars, th=10, trend_filter=1)
    assert a["direction"] == "BUY", a["reasons"]
    assert a["stop_loss"] < a["entry"]
    assert disc_h10.bias("EURUSD", bars, [], {}) == 1  # close below SMA5: long keeps running
    dn = _d1_trend_then_drop(up=False)
    assert run_p("disc-h10", "EURUSD", dn, th=10, trend_filter=1)["direction"] == "SELL"
    # filter: an RSI2 dip in a downtrend (close < SMA200) is no BUY with the filter, a BUY without it
    dip = []
    p = dn[-4]["c"]
    for b in dn[-3:]:
        o = p
        p = o * 0.99
        dip.append(dict(b, o=o, h=o + 0.001, l=p - 0.001, c=p))
    dip = dn[:-3] + dip
    assert run_p("disc-h10", "EURUSD", dip, th=10, trend_filter=1)["direction"] != "BUY"
    assert run_p("disc-h10", "EURUSD", dip, th=10, trend_filter=0)["direction"] == "BUY"
    ex = disc_h10.default_exit({})
    assert ex["mode"] == "signal_flip" and ex["params"]["max_hold_days"] == 7


def _factor_bars(shock_sym=None, shock=0.0):
    """Six USD pairs driven by a common USD factor + small noise; optional USD-side shock on one pair at the end."""
    rng = random.Random(77)
    t0 = (T0 - 400 * 4 * H) // DAY * DAY
    px = {"EURUSD": 1.1, "GBPUSD": 1.3, "AUDUSD": 0.65, "NZDUSD": 0.6, "USDJPY": 150.0, "USDCAD": 1.35}
    out = {s: [] for s in px}
    t = t0
    n = 0
    while n < 300:
        if (t // DAY + 3) % 7 < 5:
            f = rng.gauss(0, 0.002)
            for s, sg in disc_h14.USD_PAIRS.items():
                u = f + rng.gauss(0, 0.0005)
                if s == shock_sym and n >= 294:
                    u += shock / 6
                o = px[s]
                px[s] = o * math.exp(sg * u)
                out[s].append({"t": t, "o": o, "h": max(o, px[s]) * 1.0005, "l": min(o, px[s]) * 0.9995,
                               "c": px[s], "sp": 3})
            n += 1
        t += 4 * H
    return out


def test_h14_residual_side_mapping_and_exit():
    bars = _factor_bars("USDJPY", 0.03)  # USD-side of USDJPY jumps vs the factor
    disc_h14.set_factor_bars("H4", bars)
    cfg = cfg_of("disc-h14", N=6, z0=2.0)
    z = disc_h14.zscore("USDJPY", bars["USDJPY"][-1]["t"], cfg, "H4")
    assert z > 2.0
    a = run_p("disc-h14", "USDJPY", bars["USDJPY"], info=JPY_INFO, N=6, z0=2.0)
    assert a["direction"] == "SELL", a["reasons"]  # USD too strong -> sell USD
    assert disc_h14.bias("USDJPY", bars["USDJPY"], [], cfg) == -1
    bars2 = _factor_bars("EURUSD", 0.03)  # USD-side of EURUSD strong = EURUSD fell -> BUY EURUSD
    disc_h14.set_factor_bars("H4", bars2)
    b = run_p("disc-h14", "EURUSD", bars2["EURUSD"], N=6, z0=2.0)
    assert b["direction"] == "BUY", b["reasons"]
    atr = b["indicators"]["atr"]
    assert b["entry"] - b["stop_loss"] == pytest.approx(2 * atr, rel=1e-3)
    assert b["take_profit"] - b["entry"] == pytest.approx(1.5 * atr, rel=1e-3)
    assert disc_h14.side_for("EURUSD", 0.2, 0.3) == 0  # |z| < z_exit -> flat -> flip_on_flat closes
    assert run_p("disc-h14", "EURGBP", bars2["EURUSD"])["candidate"] is None
    assert disc_h14.default_exit({})["params"]["flip_on_flat"] == 1


def _h4_prefix(n=120):
    t0 = (T0 - (n + 10) * 4 * H) // DAY * DAY
    return walk(n, 4 * H, 8, t0, vol=0.0008)


def _bar(t, o, h, l, c):
    return {"t": t, "o": o, "h": h, "l": l, "c": c, "sp": 3}


def test_h18_failed_inside_bar_break():
    pre = _h4_prefix()
    t = pre[-1]["t"]
    M = _bar(t + 4 * H, 1.1000, 1.1100, 1.0900, 1.1050)
    I = _bar(t + 8 * H, 1.1050, 1.1060, 1.1000, 1.1030)
    B = _bar(t + 12 * H, 1.1030, 1.1080, 1.1010, 1.1040)  # breaks I.h, closes back inside I (and M)
    a = run_p("disc-h18", "EURUSD", pre + [M, I, B], tp_mode="mother_extreme", require_close_inside="I")
    assert a["direction"] == "SELL", a["reasons"]
    atr = a["indicators"]["atr"]
    assert a["stop_loss"] == pytest.approx(1.1080 + 0.1 * atr, abs=2e-5)
    assert a["take_profit"] == pytest.approx(1.0900)
    r2 = run_p("disc-h18", "EURUSD", pre + [M, I, B], tp_mode="2R", require_close_inside="I")
    assert r2["take_profit"] == pytest.approx(r2["entry"] - 2 * (r2["stop_loss"] - r2["entry"]), abs=2e-5)
    B2 = dict(B, c=1.1070)  # close above I.h but inside M
    assert run_p("disc-h18", "EURUSD", pre + [M, I, B2], require_close_inside="I")["candidate"] is None
    assert run_p("disc-h18", "EURUSD", pre + [M, I, B2], require_close_inside="M")["direction"] == "SELL"
    # B two bars after I, middle bar stays inside I
    mid = _bar(t + 12 * H, 1.1030, 1.1050, 1.1010, 1.1020)
    Bd = _bar(t + 16 * H, 1.1020, 1.1040, 1.0980, 1.1015)  # breaks I.l, closes back inside
    assert run_p("disc-h18", "EURUSD", pre + [M, I, mid, Bd], require_close_inside="I")["direction"] == "BUY"
    # middle bar already broke I: B is not the first break
    mid2 = dict(mid, h=1.1070)
    assert run_p("disc-h18", "EURUSD", pre + [M, I, mid2, Bd], require_close_inside="I")["candidate"] is None


# ---- backtest resolution ------------------------------------------------------------------------------

def test_backtest_runs_discovery_plugin_with_signal_flip(db):
    seed_d1(db)
    r = backtest.run(db, d1_params("disc-h10", strategy={"trend_filter": 0, "th": 10}))
    assert r["params"]["strategy_name"] == "disc-h10"
    assert r["params"]["exit"]["mode"] == "signal_flip"
    assert r["summary"]["trades"] > 3, r["counts"]
    assert "FLIP" in r["by_outcome"]
    # time stop 7 calendar days = 5 D1 bars (6 for a Monday entry: no bar closes between Friday and Monday)
    assert all(t["exit_t"] - t["entry_t"] <= 8 * DAY for t in r["trades"])
