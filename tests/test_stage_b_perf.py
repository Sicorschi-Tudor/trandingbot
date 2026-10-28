"""Stage B (performance): analyses hooks, equity snapshots, Claude usage cost, /stats, /shadow, backtest + archive API."""
import asyncio
import json
import sys
import threading
import time
import types
from types import SimpleNamespace

import pytest

from app import strategy as strategy_mod
from app.ai_claude import usage_cost, usage_record
from app.config import iso
from app.engine import blocker_type, mean_ci90, trade_summary
from tests.conftest import ADMIN, quote
from tests.test_engine import analysis_stub
from tests.test_stage_a_engine import load_uptrend, ready


def analyses(engine):
    return engine.db.query("SELECT * FROM analyses ORDER BY id")


# ---- engine hooks ------------------------------------------------------------
def test_analysis_row_signal_link_and_ai_verdict(engine, monkeypatch):
    ready(engine)
    load_uptrend(engine)
    a = engine.analyze_symbol("EURUSD", allow_signal=True)
    assert a["direction"] == "BUY", a["reasons"]
    rows = analyses(engine)
    assert len(rows) == 1
    r = rows[0]
    assert (r["symbol"], r["tf"], r["bar_time"]) == ("EURUSD", "M5", a["bar_time"])
    assert r["direction"] == "BUY" and r["candidate_side"] == "BUY" and r["account_key"] == "PAPER"
    assert r["entry"] == a["entry"] and r["sl"] == a["stop_loss"] and r["tp"] == a["take_profit"]
    sig = engine.list_signals()[0]
    assert r["signal_id"] == sig["id"]
    assert sig["account_key"] == "PAPER"
    assert engine.db.query_one("SELECT account_key FROM signals WHERE id=?", (sig["id"],))["account_key"] == "PAPER"
    # same bar again: INSERT OR IGNORE keeps one row and the link
    engine.analyze_symbol("EURUSD", allow_signal=False)
    assert len(analyses(engine)) == 1 and analyses(engine)[0]["signal_id"] == sig["id"]

    async def fake_review(signal, context=None):
        return {"status": "DONE", "verdict": "VETO", "explanation": "x", "risks": [], "model": "m"}

    monkeypatch.setattr(engine.ai, "review", fake_review)
    sig["ai"]["status"] = "PENDING"
    asyncio.run(engine.signal_pipeline(sig))
    assert analyses(engine)[0]["ai_verdict"] == "VETO"


def test_analysis_row_for_blocked_candidate(engine, monkeypatch):
    def fake_analyze(symbol, sig_bars, htf_bars, q, cfg, risk, ctx):
        return {"symbol": symbol, "timeframe": "M5", "bar_time": 1730000000, "direction": "HOLD", "score": 0.8,
                "reasons": [], "indicators": {"atr": 0.0004}, "time_utc": iso(time.time()),
                "candidate": {"side": "SELL", "entry": 1.08, "stop_loss": 1.081, "take_profit": 1.078, "score": -0.8,
                              "blockers": ["Spread 40 puncte > maxim 30."]}}

    monkeypatch.setattr(strategy_mod, "analyze", fake_analyze)
    engine.analyze_symbol("EURUSD", allow_signal=True)
    r = analyses(engine)[0]
    assert r["direction"] == "HOLD" and r["candidate_side"] == "SELL" and r["sl"] == 1.081 and r["score"] == -0.8
    assert json.loads(r["blockers_json"]) == ["Spread 40 puncte > maxim 30."]
    assert r["signal_id"] is None


def test_no_analysis_row_without_data(engine):
    engine.analyze_symbol("EURUSD", allow_signal=False)  # no quote, no bars
    assert analyses(engine) == []


def test_equity_snapshot_cadence(engine):
    now = 1_800_000_030.0
    engine.perf_tick(now)
    engine.perf_tick(now + 30)
    rows = engine.db.query("SELECT * FROM equity_snapshots")
    assert len(rows) == 1 and rows[0]["account_key"] == "PAPER" and rows[0]["ts"] == 1_800_000_000
    assert rows[0]["equity"] == engine.paper.account()["equity"]
    engine.perf_tick(now + 61)
    assert len(engine.db.query("SELECT * FROM equity_snapshots")) == 2
    # DEMO account snapshot only while the bridge is connected
    engine.rt.mt5_account = {"login": 777, "balance": 200.0, "equity": 199.5, "trade_mode": 0}
    engine.rt.bridge["last_sync_ts"] = time.time()
    engine.perf_tick(now + 130)
    keys = {r["account_key"] for r in engine.db.query("SELECT account_key FROM equity_snapshots")}
    assert keys == {"PAPER", "DEMO:777"}
    assert not engine._labeler_busy  # no running loop in this test: labeler skipped, flag reset


def test_labeler_called_with_closed_bars(engine, monkeypatch):
    calls = {}

    def label_pending(db, get_bars, cfg, now_ts, limit=500):
        calls.update(cfg=cfg, now=now_ts, bars=get_bars("EURUSD", "M1", 0, 2_000_000_000))
        return 3

    monkeypatch.setitem(sys.modules, "app.labeler", types.SimpleNamespace(label_pending=label_pending))
    t0 = int(time.time()) // 60 * 60 - 600
    engine.db.execute("INSERT INTO bars_archive(symbol,tf,t,o,h,l,c,tv,sp) VALUES('EURUSD','M1',?,1,1,1,1.0,1,5)", (t0,))
    engine.db.execute("INSERT INTO bars_archive(symbol,tf,t,o,h,l,c,tv,sp) VALUES('EURUSD','M1',?,1,1,1,1.0,1,5)", (t0 + 60,))
    engine.db.execute("INSERT INTO bars(symbol,tf,t,o,h,l,c,tv,sp) VALUES('EURUSD','M1',?,2,2,2,2.0,1,7)", (t0 + 60,))
    engine.db.execute("INSERT INTO bars(symbol,tf,t,o,h,l,c,tv,sp) VALUES('EURUSD','M1',?,3,3,3,3.0,1,7)",
                      (int(time.time()) // 60 * 60,))  # forming bar: excluded
    assert engine.run_labeler(123.0) == 3
    assert calls["now"] == 123.0
    assert calls["cfg"] == {"break_even_at_r": 1.0, "max_hold_minutes": 240, "label_tf": "M1"}
    assert [(b["t"], b["c"], b["sp"]) for b in calls["bars"]] == [(t0, 1.0, 5), (t0 + 60, 2.0, 7)]


def test_labeler_runs_in_executor_from_loop(engine, monkeypatch):
    ran = threading.Event()

    def label_pending(db, get_bars, cfg, now_ts, limit=500):
        ran.set()
        return 0

    monkeypatch.setitem(sys.modules, "app.labeler", types.SimpleNamespace(label_pending=label_pending))

    async def go():
        engine.perf_tick(time.time())
        for _ in range(100):
            if ran.is_set() and not engine._labeler_busy:
                break
            await asyncio.sleep(0.01)

    asyncio.run(go())
    assert ran.is_set() and not engine._labeler_busy


def test_intent_and_trade_perf_fields(engine):
    engine.rt.ingest_quote("EURUSD", quote(bid=1.08000))
    engine.set_execution(True)
    sig = engine.create_signal(analysis_stub())
    engine.try_execute(sig)
    assert engine.get_signal(sig["id"])["status"] == "EXECUTED"
    it = engine.db.query_one("SELECT account_key, ticket FROM intents WHERE signal_id=?", (sig["id"],))
    assert it["account_key"] == "PAPER"
    pos = engine.paper.positions[it["ticket"]]
    q = quote(bid=1.0811)
    engine.paper.close(pos["ticket"], q, "TP", price=1.0811)
    t = engine.db.query_one("SELECT * FROM trades")
    assert t["account_key"] == "PAPER" and t["signal_entry"] == 1.0801 and t["initial_sl"] == 1.0795
    sl_dist = abs(pos["price_open"] - 1.0795)
    exp_risk = pos["volume"] * sl_dist / 0.00001 * 1.0
    assert t["risk_money"] == pytest.approx(exp_risk, rel=1e-3)
    net = t["profit"] + t["commission"] + t["swap"]
    assert t["r_multiple"] == pytest.approx(net / t["risk_money"], abs=1e-3)
    assert t["r_multiple"] > 1


def test_startup_backfills_trade_account_and_stale_backtests(settings):
    from app.db import DB
    from app.engine import Engine
    db = DB(settings.db_path)
    db.execute("INSERT INTO trades(deal_key, ticket, symbol, side, mode, profit) VALUES('P:1', 1, 'EURUSD', 'BUY', 'PAPER', 1)")
    db.execute("INSERT INTO trades(deal_key, ticket, symbol, side, mode, profit) VALUES('M:9', 99, 'EURUSD', 'BUY', 'DEMO', 1)")
    db.execute("INSERT INTO intents(id, created_at_utc, updated_at_utc, kind, state, mode, ticket, data_json) "
               "VALUES('i1','2026-01-01T00:00:00Z','2026-01-01T00:00:00Z','OPEN','FILLED','DEMO',99,?)",
               (json.dumps({"account_login": 555, "sl": 1.07}),))
    db.execute("INSERT INTO backtest_runs(id, created_ts, status) VALUES('b1', 1, 'RUNNING')")
    eng = Engine(settings, db)
    eng.startup()
    keys = {r["deal_key"]: (r["account_key"], r["initial_sl"]) for r in db.query("SELECT * FROM trades")}
    assert keys == {"P:1": ("PAPER", None), "M:9": ("DEMO:555", 1.07)}
    assert db.query_one("SELECT status FROM backtest_runs WHERE id='b1'")["status"] == "ERROR"
    db.close()


# ---- Claude usage --------------------------------------------------------------
def test_usage_cost_table():
    assert usage_cost("claude-opus-5-5", 1_000_000, 0) == pytest.approx(4.0)
    assert usage_cost("claude-opus-5-5", 0, 1_000_000) == pytest.approx(20.0)
    assert usage_cost("claude-opus-5-5", 0, 0, 1_000_000) == pytest.approx(0.2)
    assert usage_cost("claude-opus-5-5", 1700, 500) == pytest.approx((1700 * 4 + 500 * 20) / 1e6)
    assert usage_cost("claude-sonnet-5-5", 1_000_000, 1_000_000) == pytest.approx(12.0)
    assert usage_cost("claude-haiku-5-5", 1_000_000, 1_000_000) == pytest.approx(0.6)
    assert usage_cost("claude-opus-5-5-20260101", 1_000_000, 0) == pytest.approx(4.0)
    assert usage_cost("gpt-x", 1000, 1000) is None


def test_usage_recorded_after_call(engine):
    resp = SimpleNamespace(model="claude-opus-5-5", usage=SimpleNamespace(input_tokens=2000, output_tokens=400,
                                                                          cache_read_input_tokens=1000))
    engine.ai._record_usage(resp, "sig-1")
    resp2 = SimpleNamespace(model="mystery-model", usage=SimpleNamespace(input_tokens=10, output_tokens=5))
    engine.ai._record_usage(resp2, None)
    rows = engine.db.query("SELECT * FROM claude_usage ORDER BY id")
    assert rows[0]["signal_id"] == "sig-1" and rows[0]["cache_read_tokens"] == 1000
    assert rows[0]["cost_usd"] == pytest.approx((2000 * 4 + 400 * 20 + 1000 * 0.2) / 1e6)
    assert rows[1]["cost_usd"] is None and rows[1]["cache_read_tokens"] == 0
    c = engine.claude_cost()
    assert c["calls_total"] == 2 and c["calls_today"] == 2 and c["unpriced_calls"] == 1
    assert c["total_usd"] == pytest.approx(0.0162, abs=1e-4) and c["today_usd"] == c["total_usd"]
    # no usage on the response -> nothing recorded, no exception
    assert usage_record(SimpleNamespace(), "claude-opus-5-5", None) is None
    engine.ai._record_usage(SimpleNamespace(), None)
    assert len(engine.db.query("SELECT * FROM claude_usage")) == 2


def test_claude_call_records_usage(engine, monkeypatch):
    class FakeMessages:
        async def create(self, **kw):
            return SimpleNamespace(stop_reason="end_turn", model="claude-opus-5-5",
                                   usage=SimpleNamespace(input_tokens=100, output_tokens=50),
                                   content=[SimpleNamespace(type="text", text=json.dumps(
                                       {"verdict": "AGREE", "explanation": "ok", "risks": [], "invalidation": "x"}))])

    fake = SimpleNamespace(beta=SimpleNamespace(messages=FakeMessages()), messages=FakeMessages())
    monkeypatch.setattr(engine.ai, "_get_client", lambda: fake)
    out = asyncio.run(engine.ai._call({"id": "sig-9", "symbol": "EURUSD"}, None))
    assert out["verdict"] == "AGREE"
    r = engine.db.query_one("SELECT * FROM claude_usage")
    assert r["signal_id"] == "sig-9" and r["input_tokens"] == 100 and r["output_tokens"] == 50


# ---- /stats ---------------------------------------------------------------------
def add_trade(engine, n, net, r, closed_ts, key="DEMO:777", symbol="EURUSD", signal_id=None, mode="DEMO"):
    engine.db.execute(
        "INSERT INTO trades(deal_key, ticket, symbol, side, volume, price_open, price_close, profit, commission, swap, "
        "opened_at_utc, closed_at_utc, close_reason, mode, signal_id, account_key, r_multiple, risk_money) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (f"T:{n}", n, symbol, "BUY", 0.01, 1.08, 1.08, net, 0.0, 0.0, iso(closed_ts - 600), iso(closed_ts), "TP",
         mode, signal_id, key, r, 1.0 if r is not None else None))


def add_signal(engine, sid, verdict):
    sig = {"id": sid, "ai": {"status": "DONE", "verdict": verdict}}
    engine.db.execute("INSERT INTO signals(id, created_at_utc, symbol, timeframe, bar_time, direction, status, dedup_key, "
                      "data_json) VALUES(?,?,?,?,?,?,?,?,?)",
                      (sid, iso(time.time()), "EURUSD", "M5", 1, "BUY", "EXECUTED", sid, json.dumps(sig)))


def test_trade_summary_math():
    trades = [{"net": x, "r": r, "t": i * 86400} for i, (x, r) in
              enumerate([(2.0, 2.0), (-1.0, -1.0), (-1.0, -1.0), (-1.0, -1.0), (3.0, 3.0), (-0.5, None)])]
    s = trade_summary(trades)
    assert s["trades"] == 6 and s["wins"] == 2 and s["win_rate"] == pytest.approx(2 / 6, abs=1e-4)
    assert s["avg_r"] == pytest.approx(0.4) and s["expectancy_r"] == s["avg_r"] and s["trades_with_r"] == 5
    assert s["profit_factor"] == pytest.approx(5 / 3.5, abs=1e-3)
    assert s["max_dd_r"] == pytest.approx(3.0) and s["max_consec_losses"] == 3
    assert s["net_usd"] == pytest.approx(1.5) and s["sample_days"] == 5.0
    m, ci = mean_ci90([2.0, -1.0, -1.0, -1.0, 3.0])
    assert ci[0] < m < ci[1] and s["ci90_avg_r"] == ci
    assert trade_summary([{"net": 1.0, "r": 1.0, "t": 0}])["profit_factor"] is None
    assert trade_summary([])["trades"] == 0


def test_stats_endpoint(engine, client):
    now = time.time()
    add_signal(engine, "s-agree", "AGREE")
    add_signal(engine, "s-caution", "CAUTION")
    add_trade(engine, 1, 2.0, 2.0, now - 3 * 3600, signal_id="s-agree")
    add_trade(engine, 2, -1.0, -1.0, now - 2 * 3600, signal_id="s-caution", symbol="GBPUSD")
    add_trade(engine, 3, -1.0, -1.0, now - 3600)
    add_trade(engine, 4, 5.0, 5.0, now - 40 * 86400)                   # outside 30 days
    add_trade(engine, 5, 9.0, 1.0, now - 600, key="PAPER", mode="PAPER")  # another account
    for i, eq in enumerate([200.0, 202.0, 199.0, 201.0]):
        engine.db.execute("INSERT INTO equity_snapshots(account_key, ts, balance, equity) VALUES(?,?,?,?)",
                          ("DEMO:777", int(now) - 4000 + i * 60, eq, eq))
    d = client.get("/api/v1/stats", params={"account_key": "DEMO:777", "days": 30}).json()
    s = d["summary"]
    assert s["trades"] == 3 and s["wins"] == 1 and s["net_usd"] == 0.0 and s["avg_r"] == 0.0
    assert s["profit_factor"] == pytest.approx(1.0) and s["max_consec_losses"] == 2 and s["max_dd_r"] == 2.0
    assert s["max_dd_usd"] == 3.0 and s["max_dd_pct"] == pytest.approx(1.49, abs=0.01)
    assert set(d["by_symbol"]) == {"EURUSD", "GBPUSD"} and d["by_symbol"]["GBPUSD"]["trades"] == 1
    assert d["by_ai_verdict"]["AGREE"]["net_usd"] == 2.0 and d["by_ai_verdict"]["CAUTION"]["trades"] == 1
    assert d["by_ai_verdict"]["FĂRĂ SEMNAL"]["trades"] == 1
    assert sum(v["trades"] for v in d["by_hour"].values()) == 3
    assert [p[1] for p in d["pnl_curve"]] == [2.0, 1.0, 0.0]
    assert [p[1] for p in d["equity"]] == [200.0, 202.0, 199.0, 201.0]
    assert set(d["accounts"]) == {"DEMO:777", "PAPER"}
    assert any("Eșantion mic" in w for w in d["warnings"])
    assert "today_usd" in d["claude"]
    assert client.get("/api/v1/stats", params={"account_key": "DEMO:777", "days": 0}).json()["summary"]["trades"] == 4
    allacc = client.get("/api/v1/stats", params={"days": 30}).json()
    assert allacc["summary"]["trades"] == 4 and allacc["equity"] == []


# ---- /shadow --------------------------------------------------------------------
def add_analysis(engine, i, side, outcome, r, blockers=(), signal_id=None, verdict=None, direction="HOLD", bar_time=None):
    engine.db.execute(
        "INSERT INTO analyses(symbol, tf, bar_time, created_ts, account_key, direction, candidate_side, entry, sl, tp, score, "
        "blockers_json, signal_id, ai_verdict, outcome, outcome_r, mfe_r, mae_r, labeled_ts) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("EURUSD", "M5", bar_time or int(time.time()) - i * 300, time.time(), "PAPER", direction, side, 1.08, 1.079, 1.0825,
         0.8, json.dumps(list(blockers)), signal_id, verdict, outcome, r, 1.0 if r is not None else None,
         -0.5 if r is not None else None, time.time() if outcome else None))


def test_shadow_endpoint(engine, client):
    add_signal(engine, "sv", "VETO")
    add_signal(engine, "sx", "AGREE")
    add_analysis(engine, 1, "BUY", "SL", -1.0, blockers=["Spread 40 puncte > maxim 30."])
    add_analysis(engine, 2, "BUY", "TP", 1.6, blockers=["Spread 35 puncte > maxim 30.", "Sesiunea de tranzacționare este închisă."])
    add_analysis(engine, 3, "SELL", "TP", 1.6, signal_id="sv", verdict="VETO", direction="SELL")
    add_analysis(engine, 4, "BUY", "SL", -1.0, signal_id="sx", verdict="AGREE", direction="BUY")
    add_analysis(engine, 5, "BUY", None, None, blockers=["Spread 50 puncte > maxim 30."])     # not labeled yet
    add_analysis(engine, 6, None, None, None)                                               # no candidate
    add_analysis(engine, 7, "BUY", "TP", 1.6, blockers=["x"], bar_time=int(time.time()) - 60 * 86400)  # too old
    d = client.get("/api/v1/shadow", params={"days": 30}).json()
    assert d["candidates"] == 5 and d["labeled"] == 4 and d["pending"] == 1
    assert d["all"]["n"] == 4 and d["all"]["avg_r"] == pytest.approx(0.3) and d["all"]["win_rate"] == 0.5
    assert d["all"]["small_sample"] is True and any("Eșantion mic" in w for w in d["warnings"])
    cat = d["by_category"]
    assert cat["Candidat blocat de filtre"]["n"] == 2 and cat["Candidat blocat de filtre"]["avg_r"] == pytest.approx(0.3)
    assert cat["Semnal → VETO Claude"]["avg_r"] == pytest.approx(1.6)
    assert cat["Semnal → executat"]["n"] == 1
    assert d["by_blocker"]["Spread"]["n"] == 2
    assert d["by_blocker"]["Sesiunea de tranzacționare este închisă"]["n"] == 1
    assert set(d["by_ai_verdict"]) == {"VETO", "AGREE"}
    assert d["by_category"]["Semnal → VETO Claude"]["outcomes"] == {"TP": 1}
    assert client.get("/api/v1/shadow", params={"days": 0}).json()["labeled"] == 5


def test_blocker_type():
    assert blocker_type("Spread 40 puncte > maxim 30.") == "Spread"
    assert blocker_type("Expunere corelată: EUR long (max 1 pe monedă și direcție).") == "Expunere corelată"
    assert blocker_type("Cotație veche (vârsta tick 12 s > 10 s).") == "Cotație veche"
    assert blocker_type("Sesiunea de tranzacționare este închisă.") == "Sesiunea de tranzacționare este închisă"


# ---- archive status ------------------------------------------------------------------
def test_archive_status_endpoint(engine, client):
    now = int(time.time())
    engine.rt.archive_status = {"EURUSD": {"M5": {"bars": 5000, "oldest_ts": now - 20 * 86400, "target_ts": now - 120 * 86400,
                                                  "done": False}}}
    d = client.get("/api/v1/archive/status").json()
    m5 = d["symbols"]["EURUSD"]["M5"]
    assert d["days_target"] == 120 and m5["bars"] == 5000 and m5["days_loaded"] == pytest.approx(20, abs=0.1)
    assert m5["days_target"] == 120 and d["enabled"] is True


# ---- backtest API ---------------------------------------------------------------------
def wait_status(client, run_id, timeout=5.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        d = client.get(f"/api/v1/backtest/runs/{run_id}").json()
        if d["status"] != "RUNNING":
            return d
        time.sleep(0.02)
    raise AssertionError("backtest did not finish")


def test_backtest_requires_admin(client):
    assert client.post("/api/v1/backtest/run", json={"symbols": ["EURUSD"]}).status_code == 401
    assert client.post("/api/v1/backtest/run", json={"symbols": ["EURUSD"]},
                       headers={"X-Admin-Token": "wrong"}).status_code == 401


def test_backtest_run_and_poll(engine, client, monkeypatch):
    seen = {}

    def fake_run(db, params, progress=None):
        seen["params"] = params
        progress(0.5)
        return {"summary": {"trades": 12, "avg_r": -0.2, "ci90_avg_r": [-0.5, -0.1]}, "by_symbol": {}, "equity_r": [],
                "trades": [], "warnings": []}

    monkeypatch.setitem(sys.modules, "app.backtest", types.SimpleNamespace(run=fake_run))
    body = {"symbols": ["eurusd", "GBPUSD"], "days": 60, "signal_tf": "M5", "htf_tf": "H1",
            "strategy": {"min_score": 4, "atr_sl_mult": 2.0, "min_sl_points": 60, "atr_tp_mult": 3.0},
            "risk": {"max_same_currency_direction": 1}, "cost": {"mode": "fixed", "fixed_spread_pts": 12,
                                                                 "commission_per_lot_usd": 7}, "session": None}
    r = client.post("/api/v1/backtest/run", json=body, headers=ADMIN)
    assert r.status_code == 200, r.text
    run_id = r.json()["id"]
    d = wait_status(client, run_id)
    assert d["status"] == "DONE" and d["metrics"]["summary"]["trades"] == 12 and d["progress"] == 1.0
    p = seen["params"]
    assert p["symbols"] == ["EURUSD", "GBPUSD"] and p["days"] == 60 and p["session"] is None
    assert p["strategy"]["atr_sl_mult"] == 2.0 and p["risk"]["max_same_currency_direction"] == 1
    assert p["cost"] == {"mode": "fixed", "fixed_spread_pts": 12.0, "commission_per_lot_usd": 7.0, "slippage_pts": 1.0}
    assert p["initial_equity"] == 200
    runs = client.get("/api/v1/backtest/runs").json()
    assert runs[0]["id"] == run_id and runs[0]["summary"]["trades"] == 12 and "metrics" not in runs[0]
    assert client.get("/api/v1/backtest/runs/nope").status_code == 404


def test_backtest_defaults_session_and_errors(engine, client, monkeypatch):
    def boom(db, params, progress=None):
        raise RuntimeError("no bars")

    monkeypatch.setitem(sys.modules, "app.backtest", types.SimpleNamespace(run=boom))
    r = client.post("/api/v1/backtest/run", json={}, headers=ADMIN)
    assert r.status_code == 200
    d = wait_status(client, r.json()["id"])
    assert d["status"] == "ERROR" and "no bars" in d["error"]
    assert d["params"]["symbols"] == engine.rt.active_symbols() and d["params"]["session"]["tz"] == engine.rt.schedule["tz"]
    assert d["params"]["cost"]["mode"] == "stats"
    for bad in ({"days": 0}, {"signal_tf": "H1", "htf_tf": "M5"}, {"cost": {"mode": "magic"}},
                {"cost": {"mode": "fixed"}}, {"strategy": {"unknown": 1}}, {"risk": {"max_risk_per_trade_pct": 9}},
                {"symbols": ["BAD SYMBOL!"]}):
        assert client.post("/api/v1/backtest/run", json=bad, headers=ADMIN).status_code == 422, bad


def test_backtest_single_run_at_a_time(engine, client, monkeypatch):
    gate = threading.Event()

    def slow(db, params, progress=None):
        gate.wait(5)
        return {"summary": {}}

    monkeypatch.setitem(sys.modules, "app.backtest", types.SimpleNamespace(run=slow))
    r1 = client.post("/api/v1/backtest/run", json={"symbols": ["EURUSD"]}, headers=ADMIN)
    assert r1.status_code == 200
    r2 = client.post("/api/v1/backtest/run", json={"symbols": ["EURUSD"]}, headers=ADMIN)
    assert r2.status_code == 409
    gate.set()
    assert wait_status(client, r1.json()["id"])["status"] == "DONE"
