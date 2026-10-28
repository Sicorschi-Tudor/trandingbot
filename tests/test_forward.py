"""Forward test runner (PAPER, simulated only): closed-bar entries, fade-v1 mirror levels, tick exits, time stop,
cost-adjusted R, restart reload, verdict, API, and no executor / bridge / live-context interaction."""
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import api, bridge, forward
from app import strategy as strategy_mod
from app.db import DB
from app.engine import Engine
from tests.conftest import ADMIN, make_bars, make_settings, quote, zigzag

H1, H4 = 3600, 14400
T_LAST = 1_800_000_000 // H4 * H4  # last closed H1 bar open time (also an H4 boundary)
NOW = T_LAST + H1 + 5  # 5 s after that H1 bar closed
FADE_CFG = [{"name": "fade-test", "strategy": "fade-v1", "signal_tf": "H1", "htf_tf": "H4", "symbols": "active"}]


def fwd_engine(tmp_path, cfg=None, db_name="test.db"):
    s = make_settings(tmp_path, DATABASE_PATH=str(tmp_path / db_name), FORWARD_TESTS=json.dumps(cfg or FADE_CFG))
    eng = Engine(s, DB(s.db_path))
    eng.startup()
    eng.rt.started_ts -= 60
    eng.rt.bridge["sync_count"] = 1
    return eng


@pytest.fixture
def eng(tmp_path):
    e = fwd_engine(tmp_path)
    yield e
    e.db.close()


def load_up(e, now=NOW, symbol="EURUSD"):
    """H1 + H4 uptrend whose last H1 bar (T_LAST) closed just before `now`: trend-v1 -> BUY."""
    sig = make_bars(zigzag(150, 0.0030, 0.0020, end_up=True), H1, end_t=T_LAST, wick=0.0005)
    htf = make_bars(zigzag(80, 0.0090, 0.0060, end_up=True), H4, end_t=T_LAST - H4, wick=0.0010)
    e.rt.ingest_history(symbol, "H1", sig)
    e.rt.ingest_history(symbol, "H4", htf)
    e.rt.ingest_quote(symbol, quote(bid=sig[-1]["c"], now=now))
    return sig, htf


def set_q(e, bid, now, symbol="EURUSD", **kw):
    e.forward.clock = lambda: now  # live tick: the wall clock is at the quote time (fresh quote)
    e.rt.ingest_quote(symbol, quote(bid=bid, now=now, **kw))


def fpos(e, name="fade-test", sym="EURUSD"):
    return e.forward.positions.get(name, {}).get(sym)


def ftrades(e, name="fade-test"):
    return e.db.query("SELECT * FROM forward_trades WHERE name=? ORDER BY closed_ts", (name,))


def assert_no_live_side_effects(e):
    assert e.db.query("SELECT COUNT(*) AS n FROM intents")[0]["n"] == 0
    assert e.db.query("SELECT COUNT(*) AS n FROM signals")[0]["n"] == 0
    assert e.db.query("SELECT COUNT(*) AS n FROM trades")[0]["n"] == 0
    assert e.executor.take_commands(NOW) == []
    assert e.paper.positions == {}
    assert e.has_bot_position("EURUSD", "ALL") is False
    assert e.open_exposures("ALL") == []


def trend_reference(e, now=NOW):
    """What trend-v1 (live params, H1/H4) says on the same data, neutral context."""
    rt = e.rt
    cfg = {**rt.strategy, "signal_timeframe": "H1", "htf_timeframe": "H4"}
    ctx = strategy_mod.AnalysisContext(tick_age=rt.tick_age("EURUSD", now), now_ts=now)
    return strategy_mod.analyze("EURUSD", rt.closed_bars("EURUSD", "H1", now)[-400:],
                                rt.closed_bars("EURUSD", "H4", now)[-200:], rt.quotes["EURUSD"], cfg, rt.risk, ctx)


# ---- config / tfs ------------------------------------------------------------
def test_config_loaded_and_forward_tfs_added_to_needed(eng):
    assert [t["name"] for t in eng.forward.tests] == ["fade-test"]
    t = eng.forward.tests[0]
    assert t["risk_pct"] == 0.5 and t["equity"] == 200 and t["time_stop_minutes"] == 240
    assert t["criteria"] == forward.DEFAULT_CRITERIA
    tfs = eng.rt.needed_tfs()
    assert "H1" in tfs and "H4" in tfs and tfs == sorted(tfs, key=lambda x: forward.TF_SECONDS[x])
    assert eng.forward.started["fade-test"] > 0


def test_settings_key_overrides_env_and_bad_items_are_skipped(eng):
    eng.db.set_setting("forward_tests", [{"name": "x", "strategy": "nope-v9"}, {"name": "y", "strategy": "trend-v1"},
                                         {"strategy": "trend-v1"}])
    eng.forward.reload_config()
    assert [t["name"] for t in eng.forward.tests] == ["y"]


def test_normalize_item_validation():
    with pytest.raises(ValueError):
        forward.normalize_item({"strategy": "fade-v1"})
    with pytest.raises(ValueError):
        forward.normalize_item({"name": "a", "strategy": "fade-v1", "signal_tf": "H2"})
    t = forward.normalize_item({"name": "a", "strategy": "fade-v1", "symbols": "eurusd, usdjpy",
                                "criteria": {"min_trades": 50}})
    assert t["symbols"] == ["EURUSD", "USDJPY"] and t["criteria"]["min_trades"] == 50
    assert t["criteria"]["pf_gt"] == 1.15


# ---- entries -----------------------------------------------------------------
def test_entry_only_on_newly_closed_bar_with_mirror_levels(eng):
    load_up(eng)
    ref = trend_reference(eng)
    assert ref["direction"] == "BUY"
    eng.forward.tick(NOW)
    p = fpos(eng)
    assert p is not None and p["side"] == "SELL"
    q = eng.rt.quotes["EURUSD"]
    assert p["entry"] == q["bid"]  # SELL at bid
    base_sl_d = ref["entry"] - ref["stop_loss"]
    base_tp_d = ref["take_profit"] - ref["entry"]
    assert p["sl"] == pytest.approx(q["bid"] + base_tp_d, abs=1e-5)  # SL distance = trend-v1 TP distance
    assert p["tp"] == pytest.approx(q["bid"] - base_sl_d, abs=1e-5)  # TP distance = trend-v1 SL distance
    assert p["bar_time"] == T_LAST
    row = eng.db.query_one("SELECT * FROM forward_positions WHERE id=?", (p["id"],))
    assert row["side"] == "SELL" and row["std_spread_pts"] == 10
    assert_no_live_side_effects(eng)


def test_no_entry_while_bar_is_forming_and_one_analysis_per_bar(eng, monkeypatch):
    load_up(eng)
    calls = []
    monkeypatch.setattr(eng.forward, "analyze", lambda t, sym, now: calls.append((sym, now)))
    eng.forward.tick(NOW)
    eng.forward.tick(NOW + 1)
    assert len(calls) == 1  # same closed bar: analyzed once
    # a new H1 bar starts streaming: still forming -> nothing
    eng.rt.ingest_bar("EURUSD", "H1", {"t": T_LAST + H1, "o": 1.1, "h": 1.1, "l": 1.1, "c": 1.1}, publish=False)
    eng.forward.tick(T_LAST + H1 + 1800)
    assert len(calls) == 1
    eng.forward.tick(T_LAST + 2 * H1 + 3)  # it closed 3 s ago
    assert len(calls) == 2


def test_stale_bar_after_restart_is_not_traded(eng):
    load_up(eng, now=NOW + 600)
    eng.forward.tick(NOW + 600)  # bar closed 605 s ago (> 90 s)
    assert fpos(eng) is None


def test_one_position_per_symbol_and_correlation_limit_in_own_book(eng):
    load_up(eng)
    eng.forward.tick(NOW)
    assert fpos(eng)["side"] == "SELL"
    t = eng.forward.tests[0]
    before = len(eng.db.query("SELECT * FROM forward_positions"))
    eng.forward.analyze(t, "EURUSD", NOW)
    assert len(eng.db.query("SELECT * FROM forward_positions")) == before  # symbol already held
    ctx = eng.forward._ctx(t, "GBPUSD", NOW)
    assert strategy_mod.context_blockers("GBPUSD", "SELL", ctx, eng.rt.risk)  # USD long already held (EURUSD SELL)
    assert not strategy_mod.context_blockers("GBPUSD", "BUY", ctx, eng.rt.risk)


def test_news_blackout_does_not_block_fade_entry(eng):
    """fade-v1 pre-registered definition has no news filter (nor does the STAGE_C2 backtest)."""
    load_up(eng)
    eng.rt.calendar = [{"t": NOW + 300, "ccy": "USD", "imp": 3, "name": "NFP"}]
    eng.rt.calendar_ts = NOW
    assert eng.news_check("EURUSD", NOW)[0]  # live would block
    eng.forward.tick(NOW)
    assert fpos(eng) is not None and fpos(eng)["side"] == "SELL"


def test_builtin_mirror_matches_fade_plugin(tmp_path, monkeypatch):
    e1, e2 = fwd_engine(tmp_path, db_name="a.db"), fwd_engine(tmp_path, db_name="b.db")
    try:
        for e in (e1, e2):
            load_up(e)
        orig = e2.forward.registered
        monkeypatch.setattr(e2.forward, "registered", lambda n: False if n == "fade-v1" else orig(n))
        e1.forward.tick(NOW)
        e2.forward.tick(NOW)
        p1, p2 = fpos(e1), fpos(e2)
        assert p1 and p2 and p2.get("base_side") == "BUY"
        assert (p1["side"], p1["entry"], p1["sl"], p1["tp"]) == (p2["side"], p2["entry"], p2["sl"], p2["tp"])
        m = forward.mirror_levels("BUY", 1.1001, 1.0951, 1.1101, 1.1000, 1.1001)
        assert m["side"] == "SELL" and m["entry"] == 1.1000
        assert m["sl"] == pytest.approx(1.1100) and m["tp"] == pytest.approx(1.0950)
        m = forward.mirror_levels("SELL", 1.1000, 1.1050, 1.0900, 1.1000, 1.1001)
        assert m["side"] == "BUY" and m["entry"] == 1.1001
        assert m["sl"] == pytest.approx(1.0901) and m["tp"] == pytest.approx(1.1051)
    finally:
        e1.db.close()
        e2.db.close()


def test_trend_strategy_runs_as_is(tmp_path):
    e = fwd_engine(tmp_path, [{"name": "trend-fwd", "strategy": "trend-v1", "symbols": ["EURUSD"]}])
    try:
        load_up(e)
        e.forward.tick(NOW)
        p = fpos(e, "trend-fwd")
        q = e.rt.quotes["EURUSD"]
        assert p["side"] == "BUY" and p["entry"] == q["ask"] and p["sl"] < p["entry"] < p["tp"]
        assert_no_live_side_effects(e)
    finally:
        e.db.close()


# ---- exits -------------------------------------------------------------------
def pos(side, entry, sl, tp, opened=0.0):
    return {"side": side, "entry": entry, "sl": sl, "tp": tp, "opened_ts": opened}


def test_tick_exit_ordering_and_fill_prices():
    b = pos("BUY", 1.1000, 1.0950, 1.1100)
    assert forward.tick_exit(b, 1.0990, 1.0991, 10, 0) is None
    assert forward.tick_exit(b, 1.0940, 1.0941, 10, 0) == ("SL", 1.0940)  # gap through SL: filled at bid
    assert forward.tick_exit(b, 1.0950, 1.0951, 10, 0) == ("SL", 1.0950)
    assert forward.tick_exit(b, 1.1150, 1.1151, 10, 0) == ("TP", 1.1100)  # TP never better than TP
    # SL before time stop on the same tick
    assert forward.tick_exit(b, 1.0940, 1.0941, 99999, 60) == ("SL", 1.0940)
    s = pos("SELL", 1.1000, 1.1050, 1.0900)
    assert forward.tick_exit(s, 1.1049, 1.1050, 10, 0) == ("SL", 1.1050)  # SELL exits on ask
    assert forward.tick_exit(s, 1.1059, 1.1060, 10, 0) == ("SL", 1.1060)
    assert forward.tick_exit(s, 1.0899, 1.0900, 10, 0) == ("TP", 1.0900)
    assert forward.tick_exit(s, 1.0899, 1.0901, 10, 0) is None  # bid below TP but ask not: still open


def test_tick_exits_close_and_persist(eng):
    load_up(eng)
    eng.forward.tick(NOW)
    p = fpos(eng)
    set_q(eng, p["tp"] - 0.00020, NOW + 60)  # ask = tp - 10 pts -> TP for SELL
    assert fpos(eng) is None
    tr = ftrades(eng)[0]
    assert tr["reason"] == "TP" and tr["exit_price"] == p["tp"]
    r = (p["entry"] - p["tp"]) / (p["sl"] - p["entry"])
    assert tr["r"] == pytest.approx(r, abs=1e-4)
    # demo spread (10) already paid inside r; standard EURUSD spread is 10 -> only the 1-point slippage is extra
    assert tr["r_adj"] == pytest.approx(r - 1 * 0.00001 / (p["sl"] - p["entry"]), abs=1e-4)
    assert tr["usd_adj"] == pytest.approx(tr["r_adj"] * 200 * 0.5 / 100, abs=1e-3)
    assert eng.db.query("SELECT * FROM forward_positions") == []
    assert_no_live_side_effects(eng)


def test_time_stop_on_tick_and_in_loop(eng):
    load_up(eng)
    eng.forward.tick(NOW)
    p = fpos(eng)
    mid = p["entry"] + 0.00010
    eng.forward.clock = lambda: NOW + 239 * 60
    set_q(eng, mid, NOW + 239 * 60)
    assert fpos(eng) is not None
    eng.forward.clock = lambda: NOW + 240 * 60
    set_q(eng, mid, NOW + 240 * 60)
    assert fpos(eng) is None
    tr = ftrades(eng)[0]
    assert tr["reason"] == "TIME" and tr["exit_price"] == pytest.approx(mid + 0.00010)  # SELL closes at ask


def test_time_stop_from_loop_without_new_tick(eng, monkeypatch):
    load_up(eng)
    eng.forward.tick(NOW)
    later = NOW + 240 * 60 + 5
    eng.rt.quotes["EURUSD"]["time_msc"] = int(later * 1000)  # fresh quote, no hook call
    eng.forward.tick(later)
    assert ftrades(eng)[0]["reason"] == "TIME"


def test_cost_adjusted_r_jpy_points():
    # USDJPY: point 0.001, SL 0.200 away = 200 points; standard spread 12 points = 0.06R
    assert forward.std_spread_points("USDJPY") == 12
    assert forward.std_spread_points("GBPJPY.a") == 28
    assert forward.std_spread_points("XAUUSD", fallback=25) == 25
    r = forward.realized_r("BUY", 150.000, 149.800, 150.400)
    assert r == pytest.approx(2.0)
    assert forward.cost_adjusted_r(r, 150.000, 149.800, 0.001, 12) == pytest.approx(1.94)
    r = forward.realized_r("SELL", 150.000, 150.200, 150.200)
    assert r == pytest.approx(-1.0)
    assert forward.cost_adjusted_r(r, 150.000, 150.200, 0.001, 12) == pytest.approx(-1.06)


def test_jpy_close_records_adjusted_r(eng):
    t = eng.forward.tests[0]
    q = quote(bid=150.000, now=NOW, point=0.001, digits=3, tick_size=0.001)
    q["ask"] = 150.010
    q["spread_points"] = 10
    p = eng.forward.open(t, "USDJPY", "BUY", 150.010, 149.810, 150.410, q, NOW, T_LAST)
    tr = eng.forward.close(t, p, "SL", 149.810, NOW + 100)
    # demo spread 10 already in r; extra = (12 - 10) standard + 1 slippage = 3 points on a 200-point SL
    assert tr["r"] == pytest.approx(-1.0) and tr["r_adj"] == pytest.approx(-1.015)
    assert tr["usd_adj"] == pytest.approx(-1.015)  # 200 USD × 0.5%


# ---- restart -----------------------------------------------------------------
def test_restart_reloads_open_positions_and_exits_them(tmp_path):
    e1 = fwd_engine(tmp_path)
    load_up(e1)
    e1.forward.tick(NOW)
    p = fpos(e1)
    started = e1.forward.started["fade-test"]
    e1.db.close()
    e2 = fwd_engine(tmp_path)
    try:
        p2 = fpos(e2)
        assert p2 is not None and p2["id"] == p["id"] and p2["sl"] == p["sl"] and p2["side"] == "SELL"
        assert e2.forward.started["fade-test"] == started
        assert e2.forward.last_bar[("fade-test", "EURUSD")] == T_LAST  # same bar never re-entered
        set_q(e2, p["sl"] + 0.00005, NOW + 120)  # ask above SL
        assert fpos(e2) is None and ftrades(e2)[0]["reason"] == "SL"
        assert_no_live_side_effects(e2)
    finally:
        e2.db.close()


# ---- stats / verdict ------------------------------------------------------------
def test_summarize_and_verdict_logic():
    crit = dict(forward.DEFAULT_CRITERIA)
    s = forward.summarize([])
    assert forward.criteria_check(s, 1.0, crit)[0] == "ÎN CURS"
    assert forward.criteria_check(s, 8.0, crit)[0] == "RESPINS"  # max weeks, nothing proven
    good = [1.0, -0.5] * 60  # avg 0.25, PF 2, tight CI
    s = forward.summarize(good)
    assert s["trades_count"] == 120 and s["pf_adj"] == 2.0 and s["avg_r_adj"] == pytest.approx(0.25)
    assert s["win_rate"] == 0.5 and s["sum_r_adj"] == pytest.approx(30.0)
    v, checks = forward.criteria_check(s, 2.0, crit)
    assert v == "PROMIȚĂTOR" and all(c["ok"] for c in checks if c["key"] not in ("max_weeks",))
    s_small = forward.summarize(good[:50])
    assert forward.criteria_check(s_small, 2.0, crit)[0] == "ÎN CURS"  # not decided before 100 trades / 8 weeks
    assert forward.criteria_check(s_small, 8.1, crit)[0] == "PROMIȚĂTOR"
    weak = [1.0, -0.9] * 60  # PF 1.11 < 1.15
    assert forward.criteria_check(forward.summarize(weak), 2.0, crit)[0] == "RESPINS"
    bad = [-0.1] * 100 + [0.0]
    assert forward.criteria_check(forward.summarize(bad), 2.0, crit)[0] == "RESPINS"


# ---- API ---------------------------------------------------------------------------
@pytest.fixture
def fclient(eng):
    app = FastAPI()
    app.include_router(api.router)
    app.include_router(bridge.router)
    app.state.engine = eng
    return TestClient(app)


def test_api_forward_payload_and_reset(eng, fclient):
    load_up(eng)
    eng.forward.tick(NOW)
    p = fpos(eng)
    set_q(eng, p["sl"] + 0.00005, NOW + 60)
    eng.forward.tick(NOW + 61)
    d = fclient.get("/api/v1/forward").json()
    assert isinstance(d, list) and len(d) == 1
    x = d[0]
    assert x["name"] == "fade-test" and x["strategy"] == "fade-v1" and x["simulated"] is True
    assert x["trades_count"] == 1 and x["verdict"] == "ÎN CURS" and x["open"] == []
    assert x["avg_r_adj"] < -1 and x["equity_curve_adj"][0][1] == pytest.approx(x["sum_r_adj"])
    assert {c["key"] for c in x["checks"]} == {"min_trades", "max_weeks", "ci_lower_gt", "avg_r_gt", "pf_gt"}
    assert x["config"]["signal_tf"] == "H1" and x["started_utc"] and len(x["last_trades"]) == 1
    assert fclient.post("/api/v1/forward/reset", json={"name": "fade-test"}, headers=ADMIN).status_code == 400
    assert fclient.post("/api/v1/forward/reset", json={"name": "fade-test", "confirm": "RESET"}).status_code in (401, 403)
    assert fclient.post("/api/v1/forward/reset", json={"name": "zzz", "confirm": "RESET"}, headers=ADMIN).status_code == 404
    r = fclient.post("/api/v1/forward/reset", json={"name": "fade-test", "confirm": "RESET"}, headers=ADMIN)
    assert r.status_code == 200 and r.json()["deleted_trades"] == 1
    assert fclient.get("/api/v1/forward").json()[0]["trades_count"] == 0


# ---- isolation from live -------------------------------------------------------------
def test_loop_once_runs_forward_without_touching_live(eng):
    load_up(eng)
    eng.forward.tick(NOW)
    assert fpos(eng) is not None
    eng.loop_once()  # live loop with a forward position open: no intents, no live context change
    assert_no_live_side_effects(eng)
    a = eng.analyze_symbol("EURUSD", allow_signal=False)
    assert "Există deja o poziție a botului pe acest simbol." not in a["reasons"]


# ---- review fixes: stale quotes, cost model, frozen verdict ------------------------------
def test_stale_quote_reingested_never_closes(eng):
    load_up(eng)
    eng.forward.tick(NOW)
    p = fpos(eng)
    friday = NOW + 60
    set_q(eng, p["entry"] + 0.00010, friday)  # last quote before the market closes
    assert fpos(eng) is not None
    weekend = friday + 5 * 3600  # 240 min passed, the same old quote is re-ingested (bridge sync / reconnect)
    eng.forward.clock = lambda: weekend
    eng.rt.ingest_quote("EURUSD", quote(bid=p["entry"] + 0.00010, now=friday))
    assert eng.rt.tick_age("EURUSD", weekend) >= 18000
    assert fpos(eng) is not None and ftrades(eng) == []
    eng.forward.tick(weekend)  # loop time stop also needs a fresh quote
    assert fpos(eng) is not None
    # even an SL-crossing price on a stale quote is ignored
    eng.rt.ingest_quote("EURUSD", quote(bid=p["sl"] + 0.001, now=friday))
    assert fpos(eng) is not None
    # first fresh quote (Monday) applies the time stop at a tradable price
    set_q(eng, p["entry"] + 0.00030, weekend + 100)
    tr = ftrades(eng)[0]
    assert tr["reason"] == "TIME" and tr["closed_ts"] == weekend + 100


def test_extra_cost_points_charges_standard_spread_once():
    assert forward.extra_cost_points(10, 10) == pytest.approx(1.0)  # demo == standard: slippage only
    assert forward.extra_cost_points(14, 6) == pytest.approx(9.0)  # tighter demo spread: the difference + slippage
    assert forward.extra_cost_points(10, 25) == pytest.approx(1.0)  # wider demo spread: already paid inside r
    assert forward.extra_cost_points(None, 20) == pytest.approx(1.0)  # symbol not in the table: no extra spread


def test_unknown_symbol_spread_not_charged_twice(eng):
    t = eng.forward.tests[0]
    q = quote(bid=1.10000, now=NOW, spread_pts=25)
    p = eng.forward.open(t, "XAGEUR", "BUY", q["ask"], q["ask"] - 0.00300, q["ask"] + 0.00300, q, NOW, T_LAST)
    assert p["std_spread_pts"] == 25  # fallback = demo spread -> extra 0
    tr = eng.forward.close(t, p, "TIME", q["bid"], NOW + 10)  # immediate exit at bid: r = -25/300
    assert tr["r"] == pytest.approx(-25 / 300, abs=1e-4)
    assert tr["r_adj"] == pytest.approx(-26 / 300, abs=1e-4)  # + 1-point slippage only


def _seed_trades(e, rs, start_ts, step=3600.0, name="fade-test"):
    for i, r in enumerate(rs):
        e.db.execute("INSERT INTO forward_trades(id, name, symbol, side, entry, sl, tp, opened_ts, closed_ts, exit_price, "
                     "reason, r, r_adj, usd_adj, bar_time, data_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (f"{name}-{start_ts}-{i}", name, "EURUSD", "SELL", 1.1, 1.101, 1.099, start_ts + i * step - 60,
                      start_ts + i * step, 1.1, "TP" if r > 0 else "SL", r, r, r, None, "{}"))


def test_decision_sample_is_fixed_and_verdict_frozen(eng):
    t = eng.forward.tests[0]
    started = eng.forward.started["fade-test"]
    bad = [1.0, -0.6, -0.6] * 33 + [-0.6]  # first 100: avg < 0 -> RESPINS
    _seed_trades(eng, bad, started + 3600)
    now = started + 2 * forward.WEEK_S
    x = eng.forward.test_payload(t, now)
    assert x["verdict"] == "RESPINS" and x["decision"]["trades_count"] == 100
    # later winners would flip a recomputed verdict; the frozen one stays
    _seed_trades(eng, [2.0] * 40, started + 200 * 3600)
    rows = eng.db.query("SELECT * FROM forward_trades WHERE name=? ORDER BY closed_ts, id", ("fade-test",))
    assert forward.criteria_check(forward.summarize([r["r_adj"] for r in rows]), 3.0, t["criteria"])[0] == "PROMIȚĂTOR"
    y = eng.forward.test_payload(t, now + 3600)
    assert y["verdict"] == "RESPINS" and y["decision"]["trades_count"] == 100 and y["trades_count"] == 140
    row = eng.db.query_one("SELECT decided_json FROM forward_meta WHERE name=?", ("fade-test",))
    assert json.loads(row["decided_json"])["verdict"] == "RESPINS"
    # reset clears the frozen decision
    eng.forward.reset("fade-test", now + 7200)
    assert eng.forward.test_payload(t, now + 7200)["decision"] is None


def test_decision_sample_independent_of_view_time():
    crit = dict(forward.DEFAULT_CRITERIA)
    rows = [{"closed_ts": 1000.0 + i, "r_adj": 0.1} for i in range(130)]
    s1, ts1 = forward.decision_sample(rows[:100], 0.0, crit, 2000.0)
    s2, ts2 = forward.decision_sample(rows, 0.0, crit, 9e9)
    assert len(s1) == len(s2) == 100 and ts1 == ts2 == 1099.0
    assert forward.decision_sample(rows[:99], 0.0, crit, 2000.0) == (None, None)
    # max_weeks first: the sample is the trades closed before the deadline, even if viewed much later
    deadline = 8 * forward.WEEK_S
    late = [{"closed_ts": deadline - 10, "r_adj": 0.1}, {"closed_ts": deadline + 10, "r_adj": 0.1}]
    s, ts = forward.decision_sample(late, 0.0, crit, deadline + 1e6)
    assert len(s) == 1 and ts == deadline
