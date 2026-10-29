"""Stage F, family F3 (the 20 discovery hypotheses re-run on 9 years): smoke tests.

- every disc-H01..H20 module imports and resolves by NAME through app.strategies.get(); the TF/HTF table used by the
  F3 grid (research/results/F3) is pinned to each module's RUN_DEFAULTS;
- the new wrapper disc-f3-h12 (app/strategies/discovery/disc_f3_h12.py: disc-h12 with the cross-sectional universe
  loaded from cfg['xs_db']) returns the trend-v1 output shape on synthetic bars, decides on closed bars only (a
  universe holding the whole future gives the same decision as one truncated at the decision bar), never lets the
  quote set the direction, and runs end to end through app.backtest.run (runner-level sanity)."""
import math
import sqlite3

import pytest

from app import backtest, strategy
from app import strategies as S
from app.strategies.discovery import _disc3 as D3
from app.strategies.discovery import disc_f3_h12 as W
from app.strategies.discovery import disc_h12 as M
from app.strategies.discovery import discovery_names, get_discovery
from tests.test_stage_b_backtest import T0, db, store  # noqa: F401
from tests.test_stage_c2_strategies import CFG, DAY, RISK, cfg_of, ctx, d1_params, daily, q_at, seed_d1

NAME = "disc-f3-h12"

# hypothesis -> (plugin NAME, signal TF, HTF) exactly as run in research/results/F3 (RUN_DEFAULTS of each module;
# run_bt.py always passes --tf/--htf, which override RUN_DEFAULTS, so the grid had to use these values)
GRID_TF = {
    "H01": ("disc-H01", "H1", "D1"), "H02": ("disc-h02", "H1", "D1"), "H03": ("disc-H03", "D1", "NONE"),
    "H04": ("disc-h04", "H1", "NONE"), "H05": ("disc-H05", "H1", "D1"), "H06": ("disc-h06", "H4", "NONE"),
    "H07": ("disc-H07", "D1", "NONE"), "H08": ("disc-h08", "H1", "D1"), "H09": ("disc-H09", "H4", "D1"),
    "H10": ("disc-h10", "D1", "NONE"), "H11": ("disc-H11", "H4", "NONE"), "H12": ("disc-h12", "D1", "NONE"),
    "H13": ("disc-H13", "D1", "NONE"), "H14": ("disc-h14", "H4", "NONE"), "H15": ("disc-H15", "H1", "D1"),
    "H16": ("disc-h16", "H1", "D1"), "H17": ("disc-H17", "H1", "NONE"), "H18": ("disc-h18", "H4", "NONE"),
    "H19": ("disc-H19", "H4", "NONE"), "H20": ("disc-h20", "H1", "NONE"),
}


@pytest.fixture(autouse=True)
def _clean_universe_hook():
    D3.set_universe(None)
    W._LOADED["key"] = None
    yield
    D3.set_universe(None)
    W._LOADED["key"] = None


def test_all_20_hypotheses_import_and_resolve():
    names = discovery_names()
    for hid, (name, tf, htf) in GRID_TF.items():
        assert name in names, hid
        mod = S.get(name)
        assert mod is get_discovery(name) and mod.NAME == name
        assert callable(mod.analyze) and isinstance(mod.PARAMS, dict)
        rd = mod.RUN_DEFAULTS
        assert rd["signal_timeframe"] == tf, (hid, rd)
        assert rd["htf_timeframe"] == htf, (hid, rd)
        assert name not in S.names(include_research=True)  # never offered to the live engine / UI
    assert len(GRID_TF) == 20


def test_wrapper_registry_and_defaults():
    assert NAME in discovery_names() and S.get(NAME) is get_discovery(NAME)
    assert NAME not in S.names(include_research=True)
    assert W.RUN_DEFAULTS == M.RUN_DEFAULTS and W.RUN_DEFAULTS["signal_timeframe"] == "D1"
    assert W.default_exit(W.PARAMS) == M.default_exit(M.PARAMS) == {"mode": "base", "params": {"max_hold_hours": 108}}
    for k, v in M.PARAMS.items():
        assert W.PARAMS[k] == v
    assert S.merged_cfg(NAME, CFG, {"xs_db": "/x.db", "h12_weeks": 6})["h12_weeks"] == 6
    assert W._symbols({"xs_symbols": "eurusd, usdjpy"}) == ["EURUSD", "USDJPY"]
    assert W._symbols({}) == W.RESEARCH_SYMBOLS


def _trend(n, p0, drift, t0, wick=0.0008):
    closes = [round(p0 * math.exp(drift * i), 5) for i in range(n)]
    return daily(closes, t0=t0, wick=wick)


def _universe(t0):
    """EURUSD strongly up, USDJPY mildly down (USD weakest, EUR strongest), GBPUSD flat."""
    return {"EURUSD": _trend(400, 1.10, 0.003, t0), "USDJPY": [dict(b, o=b["o"] * 100, h=b["h"] * 100, l=b["l"] * 100,
                                                                     c=b["c"] * 100) for b in _trend(400, 1.10, -0.0005, t0)],
            "GBPUSD": _trend(400, 1.30, 0.0, t0)}


def _xs_db(path, uni):
    con = sqlite3.connect(str(path))
    con.execute("CREATE TABLE bars_archive (symbol TEXT, tf TEXT, t INTEGER, o REAL, h REAL, l REAL, c REAL, tv REAL, sp REAL, "
                "PRIMARY KEY (symbol, tf, t))")
    for s, bars in uni.items():
        con.executemany("INSERT INTO bars_archive VALUES (?,?,?,?,?,?,?,?,?)",
                        [(s, "D1", b["t"], b["o"], b["h"], b["l"], b["c"], 0, 3) for b in bars])
    con.commit()
    con.close()
    return str(path)


def _fridays(bars):
    return [i for i in range(300, len(bars)) if D3.d1_day(bars[i]["t"]).weekday() == 4]


def test_output_shape_and_direction_on_synthetic_bars(tmp_path):
    t0 = T0 - 1000 * DAY
    uni = _universe(t0)
    path = _xs_db(tmp_path / "xs.db", uni)
    sym_bars = uni["EURUSD"]
    cfg = cfg_of(NAME, xs_db=path, xs_symbols="EURUSD,USDJPY,GBPUSD")
    live = strategy.analyze("EURUSD", sym_bars[-400:], [], q_at(sym_bars[-1]["c"]), CFG, RISK, ctx())
    buys, holds = 0, 0
    for i in _fridays(sym_bars):
        sig = sym_bars[: i + 1]
        a = W.analyze("EURUSD", sig, [], q_at(sig[-1]["c"]), cfg, RISK, ctx(now_ts=sig[-1]["t"] + DAY))
        assert set(a) == set(live)
        assert set(live["indicators"]) <= set(a["indicators"])
        if a["direction"] != "HOLD":
            assert a["direction"] == "BUY", a["reasons"]  # EUR strongest vs USD weakest -> BUY EURUSD
            assert a["stop_loss"] < a["entry"] < a["take_profit"]
            assert a["candidate"]["side"] == "BUY" and not a["candidate"]["blockers"]
            assert abs(a["entry"] - a["stop_loss"]) == pytest.approx(3.0 * a["indicators"]["atr"], rel=1e-3)
            buys += 1
        else:
            holds += 1
    assert buys >= 3, (buys, holds)
    assert D3.universe() and set(D3.universe()) == {"EURUSD", "USDJPY", "GBPUSD"}  # loaded once from xs_db
    # a non-Friday bar always holds, with the shape intact
    i = next(j for j in range(300, 400) if D3.d1_day(sym_bars[j]["t"]).weekday() == 1)
    a = W.analyze("EURUSD", sym_bars[: i + 1], [], q_at(sym_bars[i]["c"]), cfg, RISK, ctx(now_ts=sym_bars[i]["t"] + DAY))
    assert a["direction"] == "HOLD" and a["candidate"] is None and set(a) == set(live)


def test_no_lookahead_full_universe_equals_truncated_universe(tmp_path):
    t0 = T0 - 1000 * DAY
    uni = _universe(t0)
    path = _xs_db(tmp_path / "xs.db", uni)
    sym_bars = uni["EURUSD"]
    cfg_db = cfg_of(NAME, xs_db=path, xs_symbols="EURUSD,USDJPY,GBPUSD")
    cfg_mem = cfg_of("disc-h12")
    n_dir = 0
    for i in _fridays(sym_bars)[:25]:
        sig = sym_bars[: i + 1]
        q, c = q_at(sig[-1]["c"]), ctx(now_ts=sig[-1]["t"] + DAY)
        full = W.analyze("EURUSD", sig, [], q, cfg_db, RISK, c)  # hook holds the whole future
        D3.set_universe({s: [b for b in bars if b["t"] <= sig[-1]["t"]] for s, bars in uni.items()})
        trunc = M.analyze("EURUSD", sig, [], q, cfg_mem, RISK, c)  # nothing after the decision bar exists
        W._LOADED["key"] = None  # force the next wrapper call to reload the full universe
        assert full["direction"] == trunc["direction"] and full["candidate"] == trunc["candidate"]
        assert full["indicators"].get("strength") == trunc["indicators"].get("strength")
        n_dir += full["direction"] != "HOLD"
    assert n_dir >= 2


def test_quote_never_sets_the_direction(tmp_path):
    t0 = T0 - 1000 * DAY
    uni = _universe(t0)
    cfg = cfg_of(NAME, xs_db=_xs_db(tmp_path / "xs.db", uni), xs_symbols="EURUSD,USDJPY,GBPUSD")
    sym_bars = uni["EURUSD"]
    for i in _fridays(sym_bars)[:20]:
        sig = sym_bars[: i + 1]
        c = ctx(now_ts=sig[-1]["t"] + DAY)
        hi = W.analyze("EURUSD", sig, [], q_at(round(sig[-1]["c"] * 1.02, 5)), cfg, RISK, c)
        lo = W.analyze("EURUSD", sig, [], q_at(round(sig[-1]["c"] * 0.98, 5)), cfg, RISK, c)
        assert hi["direction"] == lo["direction"]
        assert hi["indicators"].get("strength") == lo["indicators"].get("strength")


def test_without_xs_db_delegates_to_the_hook_as_is():
    t0 = T0 - 1000 * DAY
    uni = _universe(t0)
    sym_bars = uni["EURUSD"]
    i = _fridays(sym_bars)[5]
    sig = sym_bars[: i + 1]
    q, c = q_at(sig[-1]["c"]), ctx(now_ts=sig[-1]["t"] + DAY)
    D3.set_universe({s: [b for b in bars if b["t"] <= sig[-1]["t"]] for s, bars in uni.items()})
    a = W.analyze("EURUSD", sig, [], q, cfg_of(NAME), RISK, c)
    b = M.analyze("EURUSD", sig, [], q, cfg_of("disc-h12"), RISK, c)
    assert a == b


def test_insufficient_data_and_no_quote(tmp_path):
    t0 = T0 - 1000 * DAY
    uni = _universe(t0)
    cfg = cfg_of(NAME, xs_db=_xs_db(tmp_path / "xs.db", uni), xs_symbols="EURUSD,USDJPY,GBPUSD")
    a = W.analyze("EURUSD", uni["EURUSD"][:10], [], q_at(1.1), cfg, RISK, ctx())
    assert a["direction"] == "HOLD" and a["candidate"] is None
    a = W.analyze("EURUSD", uni["EURUSD"], [], None, cfg, RISK, ctx())
    assert a["direction"] == "HOLD" and a["candidate"] is None


def test_runner_level_backtest(db, tmp_path):  # noqa: F811
    seeded = seed_d1(db)
    r = backtest.run(db, d1_params(NAME, strategy={"xs_db": str(tmp_path / "bt.db"), "xs_symbols": "EURUSD,GBPUSD"},
                                   htf_tf="NONE", signal_tf="D1",
                                   cost={"mode": "fixed", "fixed_spread_pts": 10, "swap_mode": "table"}))
    s = r["summary"]
    assert s["trades"] > 5 and isinstance(s["avg_r"], float)
    assert all(abs(t["r"]) < 50 for t in r["trades"])
    assert "TIME" in set(r["by_outcome"]), r["by_outcome"]  # the 108 h weekly time stop really closes positions
    assert set(D3.universe()) == {"EURUSD", "GBPUSD"}  # universe came from the xs_db file
    for t in r["trades"]:
        # the decision bar (last closed bar before the fill at the next open) is always a Friday
        decision = max(b["t"] for b in seeded[t["symbol"]] if b["t"] < t["entry_t"])
        assert D3.d1_day(decision).weekday() == 4, (t["symbol"], t["entry_t"])
        assert t["exit_t"] - t["entry_t"] <= 108 * 3600 + DAY
    assert r["params"]["cost"]["swap_mode"] == "table"
