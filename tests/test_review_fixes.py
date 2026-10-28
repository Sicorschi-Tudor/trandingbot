import asyncio
import time

from app.bridge import process_sync
from app.db import DB
from app.engine import Engine
from app.paper import PaperBroker
from app.position_manager import plan
from app.risk import evaluate
from app.state import EventBus
from tests.conftest import make_settings, quote
from tests.test_bridge import payload
from tests.test_engine import analysis_stub
from tests.test_risk import base_ctx, check


def test_magic_mismatch_blocks_commands_and_demo(engine):
    now = time.time()
    process_sync(engine, payload(now), now)
    assert engine.rt.bridge["magic_ok"] is True
    it = engine.executor.submit_open("DEMO", "EURUSD", "BUY", 0.01, 1.079, 1.082, engine.rt.quotes["EURUSD"],
                                     {"passed": True, "checks": []})
    txt = process_sync(engine, payload(now + 1, magic=1), now + 1)
    assert "OPEN|" not in txt
    assert engine.executor.get(it["id"])["state"] == "FAILED"
    assert engine.rt.bridge["magic_ok"] is False
    try:
        engine.set_mode("DEMO")
        raise AssertionError("expected refusal")
    except Exception as e:
        assert getattr(e, "status", None) == 409
    rc = evaluate(base_ctx(mode="DEMO", ea_magic_ok=False, bridge_connected=True,
                           terminal={"trade_allowed": True}, mt5_account={"trade_mode": 0, "trade_expert": True}))
    assert not check(rc, "ea_magic_matches")["ok"]


def test_claude_required_blocks_disabled(tmp_path):
    s = make_settings(tmp_path, CLAUDE_REQUIRED=True)
    eng = Engine(s, DB(s.db_path))
    eng.startup()
    eng.rt.trading.update(mode="PAPER", execution_enabled=True)
    sig = eng.create_signal(analysis_stub())
    assert sig["ai"]["status"] == "DISABLED"
    asyncio.run(eng.signal_pipeline(sig))
    got = eng.get_signal(sig["id"])
    assert got["status"] == "NO_TRADE" and "CLAUDE_REQUIRED" in got["status_reason"]
    assert not eng.paper.positions


def test_test_order_requires_tick_value():
    rc = evaluate(base_ctx(test_order=True, symbol_info=quote(tick_value=0)))
    assert not check(rc, "volume_ok")["ok"]


def test_unknown_open_fails_after_deadline_and_modify_tolerance(engine):
    ex = engine.executor
    engine.rt.ingest_quote("EURUSD", quote(tick_size=0.0001))
    it = ex.submit_open("DEMO", "EURUSD", "BUY", 0.01, 1.079, 1.082, engine.rt.quotes["EURUSD"], {"passed": True, "checks": []})
    ex.take_commands()
    t0 = time.time()
    ex.check_timeouts(t0 + 31)
    ex.reconcile([], [], t0 + 40)
    assert ex.get(it["id"])["state"] == "UNKNOWN"
    ex.reconcile([], [], t0 + 100)
    assert ex.get(it["id"])["state"] == "FAILED"
    assert not ex.has_pending_open("EURUSD")
    # OPEN filled then closed before reconcile: found via IN deal
    it2 = ex.submit_open("DEMO", "EURUSD", "BUY", 0.01, 1.079, 1.082, engine.rt.quotes["EURUSD"], {"passed": True, "checks": []})
    ex.take_commands()
    ex.check_timeouts(time.time() + 31)
    ex.reconcile([], [{"entry": 0, "comment": f"ai:{it2['id'][:8]}", "position_id": 88, "price": 1.08, "volume": 0.01}])
    assert ex.get(it2["id"])["state"] == "FILLED" and ex.get(it2["id"])["ticket"] == 88
    # MODIFY confirmed with tick-size rounding
    m = ex.submit_modify("MT5", 88, "EURUSD", "BUY", 1.08004, 1.082)
    ex.take_commands()
    ex.check_timeouts(time.time() + 31)
    ex.reconcile([{"ticket": 88, "symbol": "EURUSD", "sl": 1.0800, "tp": 1.082, "volume": 0.01}])
    assert ex.get(m["id"])["state"] == "FILLED"


def test_pending_demo_open_counts_toward_max_open(engine):
    now = time.time()
    process_sync(engine, payload(now), now)
    engine.rt.risk["max_open_positions"] = 1
    engine.executor.submit_open("DEMO", "GBPUSD", "BUY", 0.01, 1.2, 1.3, {}, {"passed": True, "checks": []})
    rc = engine.risk_check("EURUSD", "BUY", 1.0801, 1.0791, 1.0821, mode="DEMO")
    assert not check(rc, "max_open_positions")["ok"]


def test_eventbus_drop_delivers_sentinel():
    async def run():
        bus = EventBus()
        q = bus.subscribe()
        for i in range(2001):
            bus.publish("x", i)
        assert q not in bus.subscribers
        assert q.get_nowait() is None
    asyncio.run(run())


def test_gap_triggers_hist_and_drops_stale(engine):
    now = time.time()
    t_now = int(now // 300 * 300)
    old = [{"symbol": "EURUSD", "tf": "M5", "t": t_now - 300 * (500 - i), "o": 1.08, "h": 1.081, "l": 1.079, "c": 1.08,
            "tv": 1, "sp": 10} for i in range(150)]
    process_sync(engine, payload(now, bars=old), now)
    engine.rt.hist_force.clear()
    live = [{"symbol": "EURUSD", "tf": "M5", "t": t_now - 300 * k, "o": 1.08, "h": 1.081, "l": 1.079, "c": 1.08,
             "tv": 1, "sp": 10} for k in (2, 1, 0)]
    txt = process_sync(engine, payload(now + 1, bars=live), now + 1)
    assert "HIST|EURUSD|M5" in txt
    assert ("EURUSD", "M5") in engine.rt.hist_force
    hist = [{"symbol": "EURUSD", "tf": "M5", "t": t_now - 300 * (299 - i), "o": 1.08, "h": 1.081, "l": 1.079, "c": 1.08,
             "tv": 1, "sp": 10} for i in range(300)]
    process_sync(engine, payload(now + 2, bars=hist), now + 2)
    series = engine.rt.bars[("EURUSD", "M5")]
    assert ("EURUSD", "M5") not in engine.rt.hist_force
    assert all(b2["t"] - b1["t"] == 300 for b1, b2 in zip(series, series[1:]))


def test_manual_close_deal_of_bot_position_recorded(engine):
    now = time.time()
    din = {"ticket": 1, "order": 1, "position_id": 500, "symbol": "EURUSD", "type": 0, "entry": 0, "volume": 0.01,
           "price": 1.08, "profit": 0, "commission": 0, "swap": 0, "magic": 552211, "reason": 3, "time": int(now) - 60,
           "comment": "ai:x"}
    dout = dict(din, ticket=2, type=1, entry=1, price=1.079, profit=-1.0, magic=0, reason=0, time=int(now))
    process_sync(engine, payload(now, deals=[din, dout]), now)
    tr = engine.list_trades()
    assert len(tr) == 1 and tr[0]["close_reason"] == "MANUAL"


def test_paper_margin_converts_quote_currency():
    pb = PaperBroker(10000, 1)
    info = {"bid": 150.0, "ask": 150.01, "tick_size": 0.001, "tick_value": 0.6667, "contract_size": 100000, "point": 0.001}
    pb.open("USDJPY", "BUY", 0.25, 149.0, 152.0, info)
    assert 200 < pb.margin() < 300


def test_freeze_level_blocks_modify():
    pos = {"side": "BUY", "price_open": 1.0800, "sl": 1.0790, "tp": 1.0821, "opened_ts": time.time()}
    info = quote(bid=1.0820, freeze_level=50)
    assert plan(pos, info, {"break_even_at_r": 1.0}, time.time(), True) == []


def test_position_manager_backoff_after_failures(engine):
    ex = engine.executor
    engine.rt.ingest_quote("EURUSD", quote())
    for _ in range(2):
        ex.submit_modify("MT5", 77, "EURUSD", "BUY", 1.0801, 1.082)
        ex.fail_queued("test")
    now = time.time()
    a = {"type": "MODIFY", "reason": "BREAK_EVEN"}
    assert engine.pm._backing_off(77, a, now) is True          # 2 failures -> 60 s wait
    assert engine.pm._backing_off(77, a, now + 61) is False
    for _ in range(4):
        ex.submit_modify("MT5", 77, "EURUSD", "BUY", 1.0801, 1.082)
        ex.fail_queued("test")
    assert engine.pm._backing_off(77, a, now + 10_000) is True  # gave up after 6
    assert (77, "MODIFY") in engine.pm.gave_up
