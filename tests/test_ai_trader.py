"""AI trader (app/ai_trader.py): shadow decisions, validation, live execution through the normal pipeline, position
management limited to risk-reducing actions, shadow labelling, TRADE_SYMBOLS scope, API."""
import asyncio
import json
import time
from types import SimpleNamespace

from app import ai_trader as mod
from app.ai_claude import MANAGE_SCHEMA, TRADER_SCHEMA
from tests.conftest import ADMIN, make_bars, make_settings, quote, zigzag
from tests.test_engine import analysis_stub


class FakeMessages:
    def __init__(self, entry=None, manage=None):
        self.entry, self.manage, self.calls = entry, manage, []

    async def create(self, **kw):
        self.calls.append(kw)
        schema = kw["output_config"]["format"]["schema"]
        body = self.entry if schema is TRADER_SCHEMA else self.manage if schema is MANAGE_SCHEMA else {}
        return SimpleNamespace(stop_reason="end_turn", model=kw["model"], usage=None,
                               content=[SimpleNamespace(type="text", text=json.dumps(body))])


def arm(engine, entry=None, manage=None):
    fake = FakeMessages(entry, manage)
    engine.ai.settings = engine.settings.model_copy(update={"ANTHROPIC_API_KEY": "sk-ant-test-key-123456"})
    engine.ai._client = SimpleNamespace(beta=SimpleNamespace(messages=fake), messages=fake)
    return fake


def feed_bars(engine, symbol="EURUSD", n=120, back_bars=1):
    now = int(time.time())
    end = now // 900 * 900 - back_bars * 900
    bars = make_bars(zigzag(n, 0.0004, 0.0003, end_up=True), 900, end_t=end)
    engine.rt.bars[(symbol, "M15")] = bars
    engine.rt.bars[(symbol, "H1")] = make_bars(zigzag(60, 0.0010, 0.0008), 3600, end_t=now // 3600 * 3600 - 3600)
    engine.rt.ingest_quote(symbol, quote(bid=round(bars[-1]["c"], 5)))
    return bars


BUY = {"action": "BUY", "stop_loss": None, "take_profit": None, "hold_minutes": 120, "confidence": 0.7, "setup": "breakout",
       "reasoning": "Închidere peste maximul range-ului.", "invalidation": "închidere sub range"}


def test_off_by_default_and_modes(engine):
    t = engine.ai_trader
    assert t.conf["entry_mode"] == "off" and t.conf["manage_mode"] == "off"
    t.tick(time.time())  # nothing happens, no error
    out = t.update_settings({"entry_mode": "shadow", "timeframe": "M15", "bogus": 1, "manage_every_min": 999})
    assert out["entry_mode"] == "shadow" and out["manage_every_min"] == 240
    assert engine.db.get_setting("ai_trader")["entry_mode"] == "shadow"
    assert t.update_settings({"entry_mode": "weird"})["entry_mode"] == "off"


def test_validation():
    q = {"bid": 1.0800, "ask": 1.0801, "point": 0.00001, "stops_level": 0}
    v = mod.AITrader.validate_entry
    assert v({"action": "NONE"}, q, 0.001)[0] == "NONE"
    assert v({**BUY, "stop_loss": 1.0790, "take_profit": 1.0830}, q, 0.001)[0] == "VALID"
    assert v({**BUY, "stop_loss": 1.0810, "take_profit": 1.0830}, q, 0.001)[0] == "INVALID"  # SL above ask
    assert "RR" in v({**BUY, "stop_loss": 1.0790, "take_profit": 1.0805}, q, 0.001)[1]
    assert "aproape" in v({**BUY, "stop_loss": 1.08005, "take_profit": 1.0830}, q, 0.001)[1]
    assert "hold" in v({**BUY, "stop_loss": 1.0790, "take_profit": 1.0830, "hold_minutes": 5}, q, 0.001)[1]
    assert v({"action": "SELL", "stop_loss": 1.0810, "take_profit": 1.0770, "hold_minutes": 60}, q, 0.001)[0] == "VALID"


def test_shadow_decision_stored_and_payload(engine):
    bars = feed_bars(engine)
    px = bars[-1]["c"]
    fake = arm(engine, entry={**BUY, "stop_loss": round(px - 0.0030, 5), "take_profit": round(px + 0.0060, 5)})
    t = engine.ai_trader
    t.update_settings({"entry_mode": "shadow"})
    payload = t.build_entry_payload("EURUSD")
    assert payload["timeframe"] == "M15" and len(payload["candles"]["M15"]) == 60 and payload["candles"]["H1"]
    assert payload["constraints"]["min_sl_distance"] > 0 and payload["self_stats"]["labeled"] == 0
    row = asyncio.run(t.decide("EURUSD"))
    assert row["status"] == "VALID" and row["action"] == "BUY" and row["mode"] == "shadow" and row["signal_id"] is None
    assert row["sl"] < row["entry"] < row["tp"] and row["hold_minutes"] == 120
    assert len(fake.calls) == 1 and "long_term_context" in fake.calls[0]["messages"][0]["content"]
    assert engine.list_signals() == []  # shadow: no signal, no order
    st = t.stats()
    assert st["entries"].get("BUY/VALID") == 1 and st["calls_today"] == 1
    # same bar again -> duplicate, not stored twice
    asyncio.run(t.decide("EURUSD", bar_time=row["bar_time"]))
    assert len(t.list_payload()) == 1


def test_tick_decides_once_per_closed_bar(engine):
    bars = feed_bars(engine)
    px = bars[-1]["c"]
    fake = arm(engine, entry={"action": "NONE", "stop_loss": None, "take_profit": None, "hold_minutes": None,
                             "confidence": 0.3, "setup": "fără setup", "reasoning": "nimic", "invalidation": ""})
    engine.rt.schedule["enabled"] = False  # session always open in tests
    t = engine.ai_trader
    t.update_settings({"entry_mode": "shadow"})
    now = bars[-1]["t"] + 900 + 10  # the latest bar closed 10 s ago
    engine.rt.quotes["EURUSD"]["time_msc"] = int(now * 1000)
    t.tick(now)
    t.tick(now + 1)
    assert len(fake.calls) == 1 and t.list_payload()[0]["action"] == "NONE"
    # budget exhausted -> no further calls
    t.update_settings({"max_calls_per_day": 1})
    t.last_bar.clear()
    t.tick(now + 2)
    assert len(fake.calls) == 1


def test_live_decision_goes_through_pipeline_and_risk_engine(engine):
    bars = feed_bars(engine)
    px = bars[-1]["c"]
    arm(engine, entry={**BUY, "stop_loss": round(px - 0.0030, 5), "take_profit": round(px + 0.0060, 5)})
    engine.rt.trading.update(mode="PAPER", execution_enabled=True)
    t = engine.ai_trader
    t.update_settings({"entry_mode": "live"})
    row = asyncio.run(t.decide("EURUSD"))
    assert row["mode"] == "live" and row["signal_id"]
    sig = engine.get_signal(row["signal_id"])
    assert sig["strategy_version"] == "ai-trader-v1" and sig["ai"]["verdict"] == "AGREE"
    assert sig["status"] == "EXECUTED", sig["status_reason"]
    assert len(engine.paper.positions) == 1
    # disarmed -> the pipeline refuses, exactly like any other signal
    engine.rt.trading.update(execution_enabled=False)
    engine.rt.bars[("EURUSD", "M15")] = bars[:-1]  # new 'latest' bar -> new decision key
    row2 = asyncio.run(t.decide("EURUSD"))
    assert "Doar semnal" in engine.get_signal(row2["signal_id"])["status_reason"]


def test_manage_shadow_and_live(engine):
    bars = feed_bars(engine)
    px = bars[-1]["c"]
    arm(engine, entry={**BUY, "stop_loss": round(px - 0.0030, 5), "take_profit": round(px + 0.0060, 5)},
        manage={"action": "TIGHTEN_SL", "new_sl": round(px - 0.0010, 5), "confidence": 0.6, "reasoning": "blochez profit"})
    engine.rt.trading.update(mode="PAPER", execution_enabled=True)
    t = engine.ai_trader
    t.update_settings({"entry_mode": "live", "manage_mode": "shadow"})
    asyncio.run(t.decide("EURUSD"))
    pos = engine.bot_positions("PAPER")[0]
    engine.track_trade_paths(time.time())  # seeds _paths (initial risk) for the manage payload
    row = asyncio.run(t.manage(pos))
    assert row["kind"] == "MANAGE" and row["action"] == "TIGHTEN_SL" and row["status"] == "VALID"
    assert engine.paper.positions[pos["ticket"]]["sl"] == pos["sl"]  # shadow: nothing applied
    t.update_settings({"manage_mode": "live"})
    row = asyncio.run(t.manage(pos))
    assert "SL mutat" in (row["note"] or "")
    assert engine.paper.positions[pos["ticket"]]["sl"] == round(px - 0.0010, 5)
    # a wider SL is refused even in live mode
    engine.ai._client.beta.messages.manage = {"action": "TIGHTEN_SL", "new_sl": round(px - 0.0050, 5), "confidence": 0.9, "reasoning": "x"}
    row = asyncio.run(t.manage(engine.bot_positions("PAPER")[0]))
    assert row["status"] == "INVALID" and "strâns" in row["note"]
    assert engine.paper.positions[pos["ticket"]]["sl"] == round(px - 0.0010, 5)


def test_labeling_with_future_bars(engine):
    bars = feed_bars(engine, back_bars=40)  # scenario 10 h in the past: the 'future' bars are closed in real time too
    px = bars[-1]["c"]
    arm(engine, entry={**BUY, "stop_loss": round(px - 0.0030, 5), "take_profit": round(px + 0.0040, 5), "hold_minutes": 600})
    t = engine.ai_trader
    t.update_settings({"entry_mode": "shadow"})
    row = asyncio.run(t.decide("EURUSD"))
    assert t.label_pending(time.time()) == 0  # no bars after the decision yet
    # write future M15 bars into bars_archive: price rallies through the TP
    t0 = row["bar_time"] + 900
    fut = [(t0 + i * 900, px + 0.0005 * i, px + 0.0005 * i + 0.0008, px + 0.0005 * i - 0.0002, px + 0.0005 * (i + 1)) for i in range(12)]
    engine.db.executemany("INSERT OR IGNORE INTO bars_archive(symbol, tf, t, o, h, l, c, tv, sp) VALUES(?,?,?,?,?,?,?,?,?)",
                          [("EURUSD", "M15", t, o, h, l, c, 1, 10) for t, o, h, l, c in fut])
    assert t.label_pending(t0 + 13 * 900) == 1
    it = t.list_payload()[0]
    assert it["outcome"] == "TP" and it["outcome_r"] > 0
    assert t.stats()["shadow"]["labeled"] == 1 and t.self_stats()["win_rate"] == 1.0


def test_trade_symbols_scope(engine):
    engine.rt.strategy["symbols"] = ["EURUSD", "XAUUSD"]
    assert engine.trade_symbols() == ["EURUSD", "XAUUSD"]
    engine.rt.strategy["trade_symbols"] = ["XAUUSD"]
    assert engine.trade_symbols() == ["XAUUSD"]
    assert engine.ai_trader.symbols() == ["XAUUSD"]
    # a context-only symbol is analysed but never produces a signal
    engine.rt.trading.update(mode="PAPER", execution_enabled=True)
    engine.rt.ingest_quote("EURUSD", quote(bid=1.08000))
    sig = make_bars(zigzag(150, 0.0003, 0.0002, end_up=True), 300)
    htf = make_bars(zigzag(80, 0.0030, 0.0020, end_up=True), 3600, wick=0.0005)
    engine.rt.bars[("EURUSD", "M5")], engine.rt.bars[("EURUSD", "H1")] = sig, htf
    engine._warmup_done = True
    a = engine.analyze_symbol("EURUSD")
    assert a["direction"] == "BUY"
    assert any("doar pentru context" in r for r in a["reasons"]) and engine.list_signals() == []


def test_pyramid_distance_rule(engine):
    from app.strategy import pyramid_blocker
    assert "aproape" in pyramid_blocker("BUY", [{"side": "BUY", "open_r": 1.0, "distance_atr": 0.3}], 3, 0.5, 1.0)
    assert pyramid_blocker("BUY", [{"side": "BUY", "open_r": 1.0, "distance_atr": 1.4}], 3, 0.5, 1.0) is None
    assert pyramid_blocker("BUY", [{"side": "BUY", "open_r": 1.0, "distance_atr": 0.3}], 3, 0.5, 0) is None
    feed_bars(engine)
    engine.rt.strategy["signal_timeframe"] = "M15"
    engine.rt.trading.update(mode="PAPER", execution_enabled=True)
    engine.rt.risk.update({"max_open_positions": 5, "max_positions_per_symbol": 3, "pyramid_min_r": -1.0,
                           "pyramid_min_distance_atr": 1.0, "max_trades_per_day": 20})
    px = engine.rt.quotes["EURUSD"]["bid"]
    s1 = engine.create_signal({**analysis_stub(bar_time=1730000000), "entry": round(px + 0.0001, 5), "stop_loss": round(px - 0.0030, 5),
                               "take_profit": round(px + 0.0060, 5)})
    engine.try_execute(s1)
    assert engine.get_signal(s1["id"])["status"] == "EXECUTED"
    pos = engine.symbol_positions("EURUSD")
    assert pos and pos[0]["distance_atr"] is not None and pos[0]["distance_atr"] < 1.0
    s2 = engine.create_signal({**analysis_stub(bar_time=1730000300), "entry": round(px + 0.0001, 5), "stop_loss": round(px - 0.0030, 5),
                               "take_profit": round(px + 0.0060, 5)})
    engine.try_execute(s2)
    assert "aproape" in engine.get_signal(s2["id"])["status_reason"]


def test_api(client, engine):
    r = client.get("/api/v1/ai-trader")
    assert r.status_code == 200 and r.json()["status"]["entry_mode"] == "off" and r.json()["items"] == []
    assert client.put("/api/v1/ai-trader/settings", headers=ADMIN, json={"entry_mode": "live"}).status_code == 400
    r = client.put("/api/v1/ai-trader/settings", headers=ADMIN, json={"entry_mode": "shadow", "timeframe": "H1"})
    assert r.status_code == 200 and r.json()["entry_mode"] == "shadow" and r.json()["timeframe"] == "H1"
    assert client.put("/api/v1/ai-trader/settings", headers=ADMIN, json={"timeframe": "X"}).status_code == 422
    assert client.post("/api/v1/ai-trader/decide/EURUSD", headers=ADMIN).status_code == 409  # no key
    assert client.post("/api/v1/ai-trader/decide/NOPE", headers=ADMIN).status_code == 404
    r = client.put("/api/v1/strategy", headers=ADMIN, json={"symbols": ["EURUSD", "XAUUSD"], "trade_symbols": "XAUUSD"})
    assert r.status_code == 200 and r.json()["trade_symbols"] == ["XAUUSD"]
    assert client.put("/api/v1/strategy", headers=ADMIN, json={"trade_symbols": ["GBPUSD"]}).status_code == 422
    r = client.put("/api/v1/risk", headers=ADMIN, json={"max_positions_per_symbol": 2, "pyramid_min_distance_atr": 1.5})
    assert r.status_code == 200 and engine.rt.risk["pyramid_min_distance_atr"] == 1.5
