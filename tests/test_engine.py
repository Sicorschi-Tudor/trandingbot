import time

import pytest

from app.executor import Executor
from app.paper import PaperBroker
from app.position_manager import plan
from tests.conftest import ADMIN, make_bars, make_settings, quote, zigzag


def analysis_stub(direction="BUY", bar_time=1730000000):
    return {
        "symbol": "EURUSD", "timeframe": "M5", "time_utc": "2026-10-28T08:00:00Z", "bar_time": bar_time,
        "direction": direction, "score": 0.8, "probabilities": {"up": 0.7, "down": 0.18, "neutral": 0.12},
        "probability_method": "heuristic-v1 (NOT calibrated)", "reasons": ["test"],
        "indicators": {"ema_fast": 1.08, "ema_slow": 1.079, "rsi": 60, "atr": 0.0004, "macd_hist": 0.00001, "htf_trend": "UP"},
        "regime": "TREND_UP", "spread_points": 10, "entry": 1.0801, "stop_loss": 1.0795, "take_profit": 1.0811, "rr": 1.67,
    }


# ---- signal dedup ---------------------------------------------------------
def test_signal_dedup(engine):
    s1 = engine.create_signal(analysis_stub())
    assert s1 and s1["status"] == "NEW" and s1["ai"]["status"] == "DISABLED"
    assert engine.create_signal(analysis_stub()) is None
    assert engine.create_signal(analysis_stub("SELL")) is not None  # different direction = different key
    assert engine.create_signal(analysis_stub(bar_time=1730000300)) is not None
    assert len(engine.list_signals()) == 3


def test_pipeline_signal_only_and_paper_execution(engine):
    engine.rt.ingest_quote("EURUSD", quote(bid=1.08000))
    sig = engine.create_signal(analysis_stub())
    engine.try_execute(sig)
    s = engine.get_signal(sig["id"])
    assert s["status"] == "NEW" and "Doar semnal" in s["status_reason"]

    engine.rt.trading.update(mode="PAPER", execution_enabled=True)
    sig2 = engine.create_signal(analysis_stub(bar_time=1730000300))
    engine.try_execute(sig2)
    s2 = engine.get_signal(sig2["id"])
    assert s2["status"] == "EXECUTED", s2["status_reason"]
    assert len(engine.paper.positions) == 1
    it = engine.executor.get(s2["intent_id"])
    assert it["state"] == "PAPER_FILLED" and it["volume"] > 0

    # second signal on same symbol: blocked by existing bot position
    sig3 = engine.create_signal(analysis_stub(bar_time=1730000600))
    engine.try_execute(sig3)
    s3 = engine.get_signal(sig3["id"])
    assert s3["status"] == "NO_TRADE" and "no_existing_bot_position_on_symbol" in s3["status_reason"]


def test_veto_makes_no_trade(engine, monkeypatch):
    import asyncio
    engine.rt.ingest_quote("EURUSD", quote(bid=1.08000))
    engine.rt.trading.update(mode="PAPER", execution_enabled=True)
    sig = engine.create_signal(analysis_stub())
    sig["ai"]["status"] = "PENDING"

    async def fake_review(signal, context=None):
        return {"status": "DONE", "verdict": "VETO", "explanation": "Trend contrar pe H1", "risks": [], "model": "m"}

    monkeypatch.setattr(engine.ai, "review", fake_review)
    asyncio.run(engine.signal_pipeline(sig))
    s = engine.get_signal(sig["id"])
    assert s["status"] == "NO_TRADE" and "VETO" in s["status_reason"]
    assert not engine.paper.positions


# ---- paper broker -------------------------------------------------------
def test_paper_sl_tp_and_pnl():
    closed = []
    pb = PaperBroker(10000, 552211, on_close=lambda tr, p: closed.append(tr))
    q = quote(bid=1.08000)
    p = pb.open("EURUSD", "BUY", 0.10, 1.07900, 1.08200, q)
    assert p["price_open"] == q["ask"] and p["ticket"] > 0
    pb.on_quote("EURUSD", quote(bid=1.08100))
    assert pb.positions[p["ticket"]]["profit"] == pytest.approx((1.081 - 1.0801) / 0.00001 * 1.0 * 0.1, abs=0.01)
    pb.on_quote("EURUSD", quote(bid=1.07890))
    assert closed and closed[0]["close_reason"] == "SL"
    assert closed[0]["profit"] == pytest.approx((1.0789 - 1.0801) / 0.00001 * 0.1, abs=0.01)
    assert pb.balance == pytest.approx(10000 + closed[0]["profit"])
    s = pb.open("EURUSD", "SELL", 0.10, 1.08200, 1.07800, quote(bid=1.08000))
    pb.on_quote("EURUSD", quote(bid=1.07780, spread_pts=10))
    assert closed[-1]["close_reason"] == "TP" and closed[-1]["price_close"] == 1.078
    assert s["ticket"] not in pb.positions


# ---- executor timeouts ----------------------------------------------------
def test_command_timeout_to_unknown_and_reconcile(engine):
    ex: Executor = engine.executor
    engine.rt.ingest_quote("EURUSD", quote())
    it = ex.submit_open("DEMO", "EURUSD", "BUY", 0.01, 1.079, 1.082, engine.rt.quotes["EURUSD"], {"passed": True, "checks": []})
    ex.take_commands()
    ex.check_timeouts(time.time() + 31)
    assert ex.get(it["id"])["state"] == "UNKNOWN"
    ex.reconcile([{"ticket": 55, "symbol": "EURUSD", "comment": f"ai:{it['id'][:8]}", "price_open": 1.08, "volume": 0.01}])
    got = ex.get(it["id"])
    assert got["state"] == "FILLED" and got["ticket"] == 55


def test_queued_not_delivered_fails(engine):
    ex = engine.executor
    engine.rt.ingest_quote("EURUSD", quote())
    it = ex.submit_open("DEMO", "EURUSD", "BUY", 0.01, 1.079, 1.082, engine.rt.quotes["EURUSD"], {"passed": True, "checks": []})
    ex.check_timeouts(time.time() + 40)
    assert ex.get(it["id"])["state"] == "FAILED"


# ---- position manager -----------------------------------------------------
def test_break_even_moves_sl_and_never_widens():
    cfg = {"break_even_at_r": 1.0, "trailing_enabled": False, "max_hold_minutes": 0, "close_at_session_end": False}
    pos = {"side": "BUY", "price_open": 1.08000, "sl": 1.07900, "tp": 1.08300, "opened_ts": time.time()}
    # profit 0.5R -> nothing
    assert plan(pos, quote(bid=1.08050), cfg, time.time(), True, 1.079) == []
    # profit 1R -> BE at entry + 2 points
    acts = plan(pos, quote(bid=1.08100), cfg, time.time(), True, 1.079)
    assert acts == [{"type": "MODIFY", "sl": 1.08002, "tp": 1.083, "reason": "BREAK_EVEN"}]
    # already at BE (or better) -> no widening
    pos2 = dict(pos, sl=1.08050)
    assert plan(pos2, quote(bid=1.08100), cfg, time.time(), True, 1.079) == []


def test_trailing_only_tightens_sell():
    cfg = {"break_even_at_r": 0, "trailing_enabled": True, "atr_sl_mult": 1.5, "max_hold_minutes": 0}
    pos = {"side": "SELL", "price_open": 1.08000, "sl": 1.08100, "tp": 1.07500, "opened_ts": time.time()}
    acts = plan(pos, quote(bid=1.07800), cfg, time.time(), True, 1.081, atr=0.0004)
    assert acts and acts[0]["sl"] < 1.081 and acts[0]["reason"] == "TRAILING"
    # price moved against: trailing would widen -> nothing
    assert plan(pos, quote(bid=1.08050), cfg, time.time(), True, 1.081, atr=0.0004) == []


def test_time_stop_and_session_end():
    cfg = {"break_even_at_r": 1.0, "max_hold_minutes": 60, "close_at_session_end": True}
    pos = {"side": "BUY", "price_open": 1.08, "sl": 1.079, "tp": 1.083, "opened_ts": time.time() - 3700}
    assert plan(pos, quote(), cfg, time.time(), True)[0]["reason"] == "TIME_STOP"
    pos["opened_ts"] = time.time()
    assert plan(pos, quote(), cfg, time.time(), False)[0]["reason"] == "SESSION_END"


# ---- API controls ---------------------------------------------------------
def test_admin_token_required(client):
    assert client.post("/api/v1/control/pause-entries").status_code == 401
    assert client.post("/api/v1/control/pause-entries", headers={"X-Admin-Token": "bad"}).status_code == 401
    assert client.post("/api/v1/control/pause-entries", headers=ADMIN).status_code == 200


def test_admin_token_empty_403(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app import api
    from app.db import DB
    from app.engine import Engine
    s = make_settings(tmp_path, ADMIN_TOKEN="")
    eng = Engine(s, DB(s.db_path))
    eng.startup()
    app = FastAPI()
    app.include_router(api.router)
    app.state.engine = eng
    r = TestClient(app).post("/api/v1/control/pause-entries", headers={"X-Admin-Token": "x"})
    assert r.status_code == 403 and r.json()["detail"] == "Set ADMIN_TOKEN in .env"


def test_live_is_mt5_execution(client):
    # LIVE = the MT5 execution path (same as DEMO): it still needs a connected bridge
    r = client.post("/api/v1/control/mode", json={"mode": "LIVE"}, headers=ADMIN)
    assert r.status_code == 409


def test_demo_refused_without_bridge(client):
    r = client.post("/api/v1/control/mode", json={"mode": "DEMO"}, headers=ADMIN)
    assert r.status_code == 409


def test_demo_refused_in_sim(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app import api
    from app.db import DB
    from app.engine import Engine
    s = make_settings(tmp_path, DATA_SOURCE="sim")
    eng = Engine(s, DB(s.db_path))
    eng.startup()
    app = FastAPI()
    app.include_router(api.router)
    app.state.engine = eng
    r = TestClient(app).post("/api/v1/control/mode", json={"mode": "DEMO"}, headers=ADMIN)
    assert r.status_code == 409 and "sim" in r.json()["detail"]


def test_mode_change_disarms(client, engine):
    engine.rt.trading["execution_enabled"] = True
    r = client.post("/api/v1/control/mode", json={"mode": "SIGNAL_ONLY"}, headers=ADMIN)
    assert r.status_code == 200 and r.json()["mode"] == "SIGNAL_ONLY" and r.json()["execution_enabled"] is False


def test_close_all_requires_confirmation(client, engine):
    engine.rt.ingest_quote("EURUSD", quote())
    engine.paper.open("EURUSD", "BUY", 0.01, 1.079, 1.083, engine.rt.quotes["EURUSD"])
    for body in ({}, {"confirm": True}, {"confirm": "close all"}, {"confirm": "CLOSE"}):
        assert client.post("/api/v1/control/close-all", json=body, headers=ADMIN).status_code == 400
    assert len(engine.paper.positions) == 1
    r = client.post("/api/v1/control/close-all", json={"confirm": "CLOSE ALL"}, headers=ADMIN)
    assert r.status_code == 200 and r.json()["ok"] is True
    assert not engine.paper.positions
    assert client.get("/api/v1/trades").json()[0]["close_reason"] == "MANUAL"


def test_close_position_requires_confirm_and_bot_owned(client, engine):
    engine.rt.ingest_quote("EURUSD", quote())
    p = engine.paper.open("EURUSD", "BUY", 0.01, 1.079, 1.083, engine.rt.quotes["EURUSD"])
    assert client.post("/api/v1/control/close-position", json={"ticket": p["ticket"]}, headers=ADMIN).status_code == 400
    engine.rt.mt5_positions = [{"ticket": 42, "symbol": "EURUSD", "type": 0, "volume": 0.1, "magic": 1}]
    r = client.post("/api/v1/control/close-position", json={"ticket": 42, "confirm": True}, headers=ADMIN)
    assert r.status_code == 403
    r = client.post("/api/v1/control/close-position", json={"ticket": p["ticket"], "confirm": True}, headers=ADMIN)
    assert r.status_code == 200 and r.json()["state"] == "PAPER_FILLED"


def test_test_order_paper(client, engine):
    now = time.time()
    for b in make_bars(zigzag(120, 0.0003, 0.0002), 300, end_t=int(now // 300 * 300) - 300):
        engine.rt.ingest_bar("EURUSD", "M5", b)
    engine.rt.ingest_quote("EURUSD", quote(bid=1.08))
    r = client.post("/api/v1/control/test-order", json={"symbol": "EURUSD", "side": "BUY"}, headers=ADMIN)
    assert r.status_code == 200
    assert r.json()["state"] == "REJECTED_RISK"  # execution disarmed by default
    client.post("/api/v1/control/execution", json={"enabled": True}, headers=ADMIN)
    r = client.post("/api/v1/control/test-order", json={"symbol": "EURUSD", "side": "BUY"}, headers=ADMIN)
    it = r.json()
    assert it["state"] == "PAPER_FILLED" and it["volume"] == 0.01 and it["sl"] < it["tp"]
    pos = client.get("/api/v1/positions").json()["open"]
    assert len(pos) == 1 and pos[0]["source"] == "PAPER"


def test_put_risk_validation(client):
    assert client.put("/api/v1/risk", json={"max_risk_per_trade_pct": 50}, headers=ADMIN).status_code == 422
    assert client.put("/api/v1/risk", json={"must_have_sl_tp": False}, headers=ADMIN).status_code == 422
    assert client.put("/api/v1/risk", json={"bogus": 1}, headers=ADMIN).status_code == 422
    r = client.put("/api/v1/risk", json={"max_open_positions": 2}, headers=ADMIN)
    assert r.status_code == 200 and r.json()["max_open_positions"] == 2


def test_startup_never_restores_armed_mode(tmp_path):
    from app.db import DB
    from app.engine import Engine
    s = make_settings(tmp_path, TRADING_MODE="PAPER", EXECUTION_ENABLED=False)
    db = DB(s.db_path)
    db.set_setting("trading", {"mode": "DEMO", "execution_enabled": True, "entries_paused": True})
    eng = Engine(s, db)
    eng.startup()
    assert eng.rt.trading == {"mode": "PAPER", "execution_enabled": False, "entries_paused": True}
    s2 = make_settings(tmp_path, TRADING_MODE="LIVE")
    eng2 = Engine(s2, db)
    eng2.startup()
    assert eng2.rt.trading["mode"] == "DEMO"


def test_status_shape(client):
    st = client.get("/api/v1/status").json()
    for k in ("app_env", "version", "data_source", "feed_label", "bridge", "account", "paper_account", "session",
              "trading", "ai", "telegram", "counts", "engine"):
        assert k in st
    assert st["trading"]["live_allowed"] is True
    assert st["account"] is None and st["paper_account"]["server"] == "PAPER"
    assert "adm-token" not in str(st) and "bridge-key" not in str(st)
