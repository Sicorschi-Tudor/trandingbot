"""Claude basket manager (the bot's only strategy): hard limits in code, model routing, armed entries, scalp exits,
loss-close / zone guards, prompt cache and cost."""
import asyncio
import json
import time
from types import SimpleNamespace

import pytest

from app import pattern_context
from app.ai_claude import usage_cost, usage_record
from app.config import Settings, profile_pins
from tests.conftest import quote


def test_gold_basket_profile():
    s = Settings(_env_file=None, PROFILE="gold_basket", ACTIVE_SYMBOLS="EURUSD,XAUUSD.a")
    assert s.BASKET_ENABLED and s.MAX_POSITIONS_PER_SYMBOL == 8 and s.CLAUDE_ENABLED
    assert s.TRADE_SYMBOLS == "XAUUSD.a" and s.ACTIVE_SYMBOLS == "XAUUSD.a"  # the broker's gold name wins
    assert s.BASKET_MAX_LOSS_USD == 15.0 and s.BASKET_SL_DISTANCE == 40.0
    # 04.11: hedging off (live -21.98 vs -16 with the plain SL), TP 2 with a 20-minute time stop
    assert s.BASKET_HEDGE_AT_PRICE == 0.0 and not s.BASKET_CLAUDE_HEDGE and s.BASKET_REOPEN_BLACKOUT_MINUTES == 15.0
    assert s.BASKET_SCALP_TP_PRICE == 2.0 and s.BASKET_SCALP_SL_PRICE == 4.0 and s.BASKET_SCALP_TIME_STOP_MINUTES == 180
    assert s.BASKET_DECISION_TF == "M15" and s.BASKET_TREND_TF == "H1" and s.BASKET_NO_ENTRY_UTC_HOURS == ""
    assert s.BASKET_SCALP_TP_ATR_MULT == 1.0 and s.BASKET_SCALP_SL_ATR_MULT == 1.5 and s.BASKET_MAX_ATR_M1 == 0.0
    assert s.BASKET_MIN_M5_GAP_ATR == 0.2 and s.SESSION_BREAKOUT_ENABLED and s.SESSION_BREAKOUT_TP_R == 2.5
    assert s.BASKET_MAX_RUN_BARS == 3 and s.BASKET_MAX_STRETCH_EMA50_ATR == 2.0
    assert s.default_strategy()["signal_timeframe"] == "M1" and not s.default_schedule()["enabled"]
    assert profile_pins("gold_basket")["strategy"] == ("symbols", "trade_symbols", "signal_timeframe", "htf_timeframe")

def test_history_reset_ignores_older_deals(engine):
    import time as _t
    eng = engine
    now = _t.time()
    eng.db.set_setting("history_reset", {"ts": now})
    magic = eng.settings.MT5_MAGIC
    old = [{"ticket": 1, "position_id": 11, "entry": 0, "magic": magic, "time": now - 600, "price": 1.08, "type": 0},
           {"ticket": 2, "position_id": 11, "entry": 1, "magic": magic, "time": now - 300, "price": 1.081, "type": 1,
            "symbol": "EURUSD", "volume": 0.01, "profit": 1.0}]
    eng.ingest_deals(old)
    assert not eng.db.query("SELECT * FROM trades")


def test_clear_history_endpoint(engine, client, tmp_path, monkeypatch):
    import json as _json

    from app import config as cfgmod
    from tests.conftest import ADMIN, quote
    monkeypatch.setattr(cfgmod, "ROOT_DIR", tmp_path)
    eng = engine
    eng.rt.ingest_quote("EURUSD", quote(bid=1.08000))
    q = eng.rt.quotes["EURUSD"]
    keep = eng.executor.submit_open("PAPER", "EURUSD", "BUY", 0.01, 1.07, 1.09, q, {"passed": True, "checks": []})
    gone = eng.executor.submit_open("PAPER", "EURUSD", "BUY", 0.01, 1.07, 1.09, q, {"passed": True, "checks": []})
    eng.paper.close(gone["ticket"], q, reason="MANUAL")  # closed -> its intent is history
    assert client.post("/api/v1/history/clear", json={"what": "trades"}, headers=ADMIN).status_code == 400
    assert client.post("/api/v1/history/clear", json={"what": "trades", "confirm": "DELETE"}).status_code == 401
    r = client.post("/api/v1/history/clear", json={"what": "intents", "confirm": "DELETE"}, headers=ADMIN)
    assert r.status_code == 200, r.text
    left = {row["id"] for row in eng.db.query("SELECT id FROM intents")}
    assert keep["id"] in left and gone["id"] not in left and r.json()["deleted"] >= 1
    assert isinstance(_json.loads((tmp_path / r.json()["backup"]).read_text()), list)
    assert eng.db.query("SELECT * FROM trades")
    r = client.post("/api/v1/history/clear", json={"what": "trades", "confirm": "DELETE"}, headers=ADMIN)
    assert r.status_code == 200 and eng.db.query("SELECT * FROM trades") == [] and eng.history_reset_ts() > 0


def test_basket_limit_api(engine, client):
    from tests.conftest import ADMIN
    eng = engine
    eng.settings = eng.settings.model_copy(update={"BASKET_ENABLED": True, "BASKET_MAX_LOSS_USD": 40.0,
                                                   "BASKET_SL_DISTANCE": 40.0})
    assert client.get("/api/v1/basket").json()["max_loss_usd"] == 40.0
    assert client.put("/api/v1/basket", json={"max_loss_usd": 60}).status_code == 401
    assert client.put("/api/v1/basket", json={"max_loss_usd": 0}, headers=ADMIN).status_code == 422
    r = client.put("/api/v1/basket", json={"max_loss_usd": 60}, headers=ADMIN)
    assert r.status_code == 200 and r.json()["max_loss_usd"] == 60.0
    c = eng.basket.cfg()
    assert c["limit"] == 60.0 and c["sl_dist"] == 60.0
    client.put("/api/v1/basket", json={"max_loss_usd": 25}, headers=ADMIN)
    assert eng.basket.cfg()["sl_dist"] == 40.0  # never tighter than the profile's broker SL


def test_claude_manager_applies_actions_with_limits(engine):
    import asyncio
    import time as _t

    from tests.conftest import quote
    eng = engine
    eng.settings = eng.settings.model_copy(update={"BASKET_ROLLOVER_BLACKOUT_MINUTES": 0, "BASKET_MAX_RISK_PCT_PER_ENTRY": 0.0, "BASKET_ENABLED": True, "CLAUDE_MAX_LOT": 0.08,
                                                   "BASKET_SURE_MIN_CONFIDENCE": 0.0, "BASKET_NO_ENTRY_UTC_HOURS": "",
                                                   "BASKET_MAX_POSITIONS_CLAUDE": 8, "BASKET_SL_DISTANCE": 0.05})
    eng.rt.risk.update(max_open_positions=8, max_positions_per_symbol=8, pyramid_min_r=-1000.0,
                       pyramid_min_distance_atr=0.0, max_total_open_risk_pct=1000.0, max_trades_per_day=1000,
                       max_same_currency_direction=99, min_free_margin_pct=0, fixed_volume_lots=0.01, allow_hedge=True,
                       max_volume_lots=1.0)
    eng.db.set_setting("basket", {"claude": True})
    eng.rt.risk.update(rollover_blackout_minutes=0)
    eng.rt.trading.update(mode="PAPER", execution_enabled=True)
    eng.rt.ingest_quote("EURUSD", quote(bid=1.08000))

    async def fake(payload, **kw):
        assert "positions" in payload and payload["constraints"]["max_lot"] == 0.08
        return {"status": "DONE", "actions": [
            {"type": "OPEN", "side": "BUY", "lot": 0.2, "tp_usd": None, "ticket": None, "why": "impuls M1"},
            {"type": "CLOSE", "side": None, "lot": None, "tp_usd": None, "ticket": 999, "why": "x"}],
            "market_view": "sus pe M1", "plan": "țin BUY", "confidence": 0.6}
    eng.ai.decide_basket = fake
    rec = asyncio.run(eng.basket.claude_decide_and_apply("EURUSD", eng.basket._claude_payload("EURUSD", [], eng.rt.quotes["EURUSD"], _t.time()), "PAPER"))
    vols = [p["volume"] for p in eng.paper.positions.values()]
    assert vols == [0.08] and "deschid BUY 0.08" in rec["done"][0] and "refuzat" in rec["done"][1]
    assert eng.basket.claude_last["plan"] == "țin BUY" and eng.basket.claude_history


def test_claude_manager_limit_closes_without_claude(engine):
    import time as _t

    from tests.conftest import quote
    eng = engine
    eng.settings = eng.settings.model_copy(update={"BASKET_ENABLED": True, "BASKET_MAX_LOSS_USD": 0.2})
    eng.db.set_setting("basket", {"claude": True})
    eng.rt.trading.update(mode="PAPER", execution_enabled=True)
    eng.rt.ingest_quote("EURUSD", quote(bid=1.08000))
    eng.paper.open("EURUSD", "BUY", 0.01, 1.07, 1.09, eng.rt.quotes["EURUSD"])
    eng.rt.ingest_quote("EURUSD", quote(bid=1.07960))
    eng.paper.on_quote("EURUSD", eng.rt.quotes["EURUSD"])
    called = []
    eng.ai.decide_basket = lambda payload, **kw: called.append(1)
    eng.basket.tick(_t.time())
    assert not eng.paper.positions and not called


def _claude_basket(engine, **upd):
    from tests.conftest import quote
    eng = engine
    eng.settings = eng.settings.model_copy(update={"BASKET_ENABLED": True, "CLAUDE_MAX_LOT": 0.08,
                                                   "BASKET_MAX_POSITIONS_CLAUDE": 8, "BASKET_SL_DISTANCE": 0.05,
                                                   "BASKET_ZR_MULT": 2.0, "BASKET_RECOVERY_EXIT_USD": 0.1,
                                                   "BASKET_RECOVERY_TRIGGER_USD": 0.2,
                                                   # EURUSD paper tests: a 4.0 "price" SL is 4000 USD per 0.01 lot, so the
                                                   # per-entry risk cap would pin every lot to 0.01 - off unless a test sets it
                                                   "BASKET_MAX_RISK_PCT_PER_ENTRY": 0.0,
                                                   # the confidence gate on big lots is tested on its own
                                                   "BASKET_SURE_MIN_CONFIDENCE": 0.0,
                                                   # the guard tests seed flat M5 bars: the M5-flat guard is tested on its own
                                                   "BASKET_MIN_M5_GAP_ATR": 0.0,
                                                   # the guard tests arm a few ATR away from the price on purpose
                                                   "BASKET_PULLBACK_MAX_OVERSHOOT_ATR": 0.0,
                                                   # tests run at any wall-clock hour: no blocked window by default
                                                   "BASKET_NO_ENTRY_UTC_HOURS": "",
                                                   # tests run at any wall-clock time: no rollover blackout (22:00 UTC)
                                                   "BASKET_ROLLOVER_BLACKOUT_MINUTES": 0, **upd})
    eng.rt.risk.update(max_open_positions=8, max_positions_per_symbol=8, pyramid_min_r=-1000.0,
                       pyramid_min_distance_atr=0.0, max_total_open_risk_pct=1000.0, max_trades_per_day=1000,
                       max_same_currency_direction=99, min_free_margin_pct=0, fixed_volume_lots=0.01, allow_hedge=True,
                       max_volume_lots=1.0, rollover_blackout_minutes=0)
    eng.db.set_setting("basket", {"claude": True})
    eng.rt.trading.update(mode="PAPER", execution_enabled=True)
    eng.rt.ingest_quote("EURUSD", quote(bid=1.08000))
    return eng


def test_claude_hedge_is_bigger_has_no_tp_and_group_closes_together(engine):
    import time as _t

    from tests.conftest import quote
    eng = _claude_basket(engine)
    eng.paper.open("EURUSD", "BUY", 0.01, 1.07, 1.09, eng.rt.quotes["EURUSD"])
    eng.rt.ingest_quote("EURUSD", quote(bid=1.07980))
    eng.paper.on_quote("EURUSD", eng.rt.quotes["EURUSD"])
    done = eng.basket.apply_claude_actions("EURUSD", [{"type": "OPEN", "side": "SELL", "lot": 0.01, "why": "M1+M5 jos"}],
                                           "PAPER", _t.time())
    assert "acoperire" in done[0] and "ridicat" in done[0]
    hedge = next(p for p in eng.paper.positions.values() if p["side"] == "SELL")
    assert hedge["volume"] == 0.02 and abs(hedge["tp"] - 1.07980) > 0.04  # bigger lot, TP far (group exit)
    # one leg of a hedged group never closes alone
    buy = next(p for p in eng.paper.positions.values() if p["side"] == "BUY")
    done = eng.basket.apply_claude_actions("EURUSD", [{"type": "CLOSE", "ticket": buy["ticket"]}], "PAPER", _t.time())
    assert "refuzat" in done[0] and len(eng.paper.positions) == 2
    # price keeps falling: the bigger SELL pulls the group back above zero -> both close at once, without Claude
    eng.rt.ingest_quote("EURUSD", quote(bid=1.07900))
    eng.paper.on_quote("EURUSD", eng.rt.quotes["EURUSD"])
    eng.ai.decide_basket = lambda payload, **kw: (_ for _ in ()).throw(AssertionError("Claude not needed"))
    eng.basket.tick(_t.time() + 10)
    assert not eng.paper.positions


def test_claude_investigation_called_at_once_with_recovery_brief(engine):
    import time as _t

    from tests.conftest import quote
    eng = _claude_basket(engine, CLAUDE_ENABLED=True)
    eng.paper.open("EURUSD", "BUY", 0.01, 1.07, 1.09, eng.rt.quotes["EURUSD"])
    eng.rt.ingest_quote("EURUSD", quote(bid=1.07970))
    eng.paper.on_quote("EURUSD", eng.rt.quotes["EURUSD"])
    now = _t.time()
    eng.rt.closed_bars = lambda sym, tf, n=None: [{"t": int(now // 60 * 60) - 60, "o": 1.08, "h": 1.08, "l": 1.0796,
                                                  "c": 1.0797}]
    st = eng.basket.state.setdefault("EURUSD", {})
    st["claude_bar"] = int(now // 60 * 60) - 60  # this bar was already decided: only the investigation may call
    seen = []

    async def fake(payload, **kw):
        seen.append(payload)
        return {"status": "DONE", "actions": [], "market_view": "corecție", "plan": "aștept", "confidence": 0.5}
    eng.ai.decide_basket = fake
    eng.ai.settings = eng.ai.settings.model_copy(update={"CLAUDE_ENABLED": True, "ANTHROPIC_API_KEY": "test-key"})
    eng.basket.tick(now)
    assert seen and seen[0]["investigation_now"] is True
    rec = seen[0]["recovery"]
    assert rec["losing_side"] == "BUY" and rec["suggested_hedge"]["side"] == "SELL" and rec["suggested_hedge"]["lot"] == 0.02


def test_basket_frozen_on_abnormal_spread(engine):
    import time as _t

    from tests.conftest import quote
    eng = _claude_basket(engine, BASKET_MAX_LOSS_USD=0.2)
    eng.paper.open("EURUSD", "SELL", 0.01, 1.09, 1.07, eng.rt.quotes["EURUSD"])
    eng.rt.ingest_quote("EURUSD", {**quote(bid=1.08000), "ask": 1.08600, "spread_points": 600})  # weekly-close quote
    eng.paper.on_quote("EURUSD", eng.rt.quotes["EURUSD"])
    eng.ai.decide_basket = lambda payload, **kw: (_ for _ in ()).throw(AssertionError("no Claude on a broken quote"))
    eng.basket.tick(_t.time())
    assert len(eng.paper.positions) == 1  # the loss limit did not close it on the fake spread
    assert "spread anormal" in eng.basket.state["EURUSD"]["abnormal"]


def test_claude_new_entry_refused_near_rollover(engine, monkeypatch):
    import time as _t
    eng = _claude_basket(engine)
    monkeypatch.setattr(eng.basket, "rollover_note", lambda now: "Rollover 17:00 New York peste 4 min")
    done = eng.basket.apply_claude_actions("EURUSD", [{"type": "OPEN", "side": "SELL", "lot": 0.01, "why": "x"}],
                                           "PAPER", _t.time())
    assert "refuzat" in done[0] and "Rollover" in done[0] and not eng.paper.positions



def _bar(t, o, h, l, c):
    return {"t": t, "o": o, "h": h, "l": l, "c": c}


def test_start_lot_sets_entry_lot_and_scales_usd_limits(engine):
    eng = _claude_basket(engine)
    eng.db.set_setting("basket", {"claude": True, "max_loss_usd": 10})
    base = eng.basket.cfg()
    assert base["start_lot"] == 0 and base["limit"] == 10
    out = eng.basket.set_start_lot(0.05)
    c = eng.basket.cfg()
    assert out["start_lot"] == 0.05 and c["lot_scale"] == 5
    assert c["limit"] == 50 and c["rec_trig"] == round(base["rec_trig"] * 5, 2) and c["claude_max_lot"] == 0.4
    assert out["max_loss_per_001_usd"] == 10 and out["max_loss_usd"] == 50
    assert eng.basket.lot(eng.rt.quotes["EURUSD"]) == 0.05
    eng.settings = eng.settings.model_copy(update={"BASKET_TRAIL_ENABLED": False})
    done = eng.basket.apply_claude_actions("EURUSD", [{"type": "OPEN", "side": "SELL", "lot": 0.01, "why": "x"}],
                                           "PAPER", time.time())
    assert "deschid SELL 0.05" in done[0]
    assert [p["volume"] for p in eng.paper.positions.values()] == [0.05]
    # a second trade on the same side (not a hedge) also takes the start lot, whatever lot Claude asked for
    done = eng.basket.apply_claude_actions("EURUSD", [{"type": "OPEN", "side": "SELL", "lot": 0.02, "why": "y"}],
                                           "PAPER", time.time())
    assert "deschid SELL 0.05" in done[0] and c["rec_exit"] == round(base["rec_exit"] * 5, 2)
    with pytest.raises(ValueError):
        eng.basket.set_start_lot(5)
    assert eng.basket.set_start_lot(0)["start_lot"] == 0


def test_star_and_price_action_context():
    a = [_bar(0, 10.0, 10.1, 8.9, 9.0), _bar(60, 9.0, 9.1, 8.8, 8.95), _bar(120, 8.95, 9.8, 8.9, 9.7)]
    assert pattern_context.star(a, 2, 1.0) == 1
    b = [_bar(0, 9.0, 10.1, 8.9, 10.0), _bar(60, 10.0, 10.2, 9.9, 10.05), _bar(120, 10.05, 10.1, 9.2, 9.3)]
    assert pattern_context.star(b, 2, 1.0) == -1
    assert pattern_context.star(a, 2, 5.0) == 0  # first bar not long against a bigger ATR
    # a zig-zag between 100 and 104: levels on both sides, nothing crashes
    bars, p = [], 100.0
    for i in range(200):
        tgt = 104.0 if (i // 10) % 2 == 0 else 100.0
        o, p = p, p + (tgt - p) * 0.3
        bars.append(_bar(i * 60, o, max(o, p) + 0.1, min(o, p) - 0.1, p))
    out = pattern_context.timeframe(bars)
    assert out["atr14"] and set(out["levels"]) >= {"supports", "resistances"}
    json.dumps(out)
    assert pattern_context.timeframe(bars[:30]) == {}


def test_prompt_cache_on_system_and_cache_write_cost(engine, monkeypatch):
    import sys
    # only BadRequestError is needed from the SDK here; the real package is slow to import cold
    monkeypatch.setitem(sys.modules, "anthropic", SimpleNamespace(BadRequestError=type("BadRequestError", (Exception,), {})))
    calls = []

    class Msgs:
        async def create(self, **kw):
            calls.append(kw)
            return SimpleNamespace(stop_reason="end_turn", model="claude-opus-5-5",
                                   content=[SimpleNamespace(type="text", text=json.dumps({"ok": 1}))],
                                   usage=SimpleNamespace(input_tokens=6000, output_tokens=500, cache_read_input_tokens=0,
                                                         cache_creation_input_tokens=3700))
    m = Msgs()
    engine.ai._client = SimpleNamespace(beta=SimpleNamespace(messages=m), messages=m)
    recs = []
    engine.ai.on_usage = recs.append
    asyncio.run(engine.ai._call_json("SYSTEM", "user", {"type": "object", "properties": {}, "required": []}, None))
    sys_block = calls[0]["system"][0]
    assert sys_block["text"] == "SYSTEM" and sys_block["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    assert recs[0]["cost_usd"] == round((6000 * 4 + 500 * 20 + 3700 * 4 * 2) / 1e6, 6)
    assert usage_cost("claude-opus-5-5", 0, 0, 0, 1_000_000) == 8.0
    rec = usage_record(SimpleNamespace(model="claude-opus-5-5", usage=SimpleNamespace(
        input_tokens=6000, output_tokens=500, cache_read_input_tokens=3700, cache_creation_input_tokens=0)),
        "claude-opus-5-5", None)
    assert rec["cache_read_tokens"] == 3700 and rec["cost_usd"] == round((6000 * 4 + 500 * 20 + 3700 * 0.2) / 1e6, 6)


def test_investigation_not_repeated_while_the_loss_wobbles_around_a_level(engine):
    eng = _claude_basket(engine, CLAUDE_ENABLED=True, BASKET_RECOVERY_TRIGGER_USD=4.0)
    eng.ai.settings = eng.ai.settings.model_copy(update={"CLAUDE_ENABLED": True, "ANTHROPIC_API_KEY": "test-key"})
    now = time.time()
    bar_t = int(now // 60 * 60) - 60
    eng.rt.closed_bars = lambda sym, tf, n=None: [_bar(bar_t, 1.08, 1.08, 1.08, 1.08)]
    seen = []

    async def fake(payload, **kw):
        seen.append(payload)
        return {"status": "DONE", "actions": [], "market_view": "x", "plan": "y", "confidence": 0.5}
    eng.ai.decide_basket = fake
    st = eng.basket.state.setdefault("EURUSD", {})
    st["claude_bar"] = bar_t
    pos = [{"ticket": 1, "side": "BUY", "volume": 0.01, "profit": 0.0, "swap": 0, "commission": 0}]
    eng.basket.positions = lambda sym: pos
    for name in ("_auto_hedge", "_trail_step", "_ride_step", "_strip_group_tps"):
        setattr(eng.basket, name, lambda *a, **k: False)
    eng.basket._claude_payload = lambda *a, **k: {}
    for pl, calls in ((-4.1, 1), (-3.9, 1), (-4.05, 1), (-3.0, 1), (-1.9, 1), (-4.2, 2)):
        pos[0]["profit"] = pl
        eng.basket._claude_task = None
        eng.basket.tick(now)
        assert len(seen) == calls, (pl, len(seen))


def test_armed_entry_fires_in_code_at_the_price_and_is_replaced_by_the_next_decision(engine, monkeypatch):
    eng = _claude_basket(engine, BASKET_TRAIL_ENABLED=False)
    monkeypatch.setattr(eng.basket, "atr_m1", lambda sym, now: 0.0010)
    now = time.time()
    q = dict(eng.rt.quotes["EURUSD"])
    bid, ask = float(q["bid"]), float(q["ask"])
    bar_t = int(now // 60 * 60)  # the M1 bar that closes after the arm
    closed = [_bar(bar_t - 120, bid, bid, bid, bid)]
    eng.rt.closed_bars = lambda sym, tf, n=None: closed
    done = eng.basket.apply_claude_actions("EURUSD", [
        {"type": "ARM", "side": "SELL", "trigger": "BREAKOUT", "price": round(bid - 0.0005, 5), "lot": 0.02,
         "valid_minutes": 5, "why": "sub suport"},
        {"type": "ARM", "side": "BUY", "trigger": "BREAKOUT", "price": round(ask + 0.0005, 5), "lot": 0.01,
         "valid_minutes": 5, "why": "peste rezistență"},
        {"type": "ARM", "side": "BUY", "trigger": "BREAKOUT", "price": round(bid - 0.0002, 5), "why": "deja"},
        {"type": "ARM", "side": "BUY", "trigger": "PULLBACK", "price": round(bid - 0.05, 5), "why": "departe"}],
        "PAPER", now)
    assert done[0].startswith("armez SELL") and done[1].startswith("armez BUY")
    assert "deja acolo" in done[2] and "prea departe" in done[3]
    assert len(eng.basket.armed["EURUSD"]) == 2
    # price inside the range: nothing fires
    assert eng.basket._fire_armed("EURUSD", [], q, "PAPER", now) is False and not eng.paper.positions
    # a wick through the support (tick below, no M1 close below yet): nothing fires
    q.update(bid=round(bid - 0.0006, 5), ask=round(bid - 0.0006 + (ask - bid), 5))
    eng.rt.quotes["EURUSD"].update(bid=q["bid"], ask=q["ask"])
    assert eng.basket._fire_armed("EURUSD", [], q, "PAPER", now) is False
    # an M1 bar that closed after the arm closes below the support: the SELL opens with Claude's lot, BUY cancelled
    closed.append(_bar(bar_t, bid, bid, round(bid - 0.0007, 5), round(bid - 0.0006, 5)))
    assert eng.basket._fire_armed("EURUSD", [], q, "PAPER", now + 61) is True
    assert [(p["side"], p["volume"]) for p in eng.paper.positions.values()] == [("SELL", 0.02)]
    assert eng.basket.armed["EURUSD"] == []
    # expiry
    eng.paper.positions.clear()
    eng.basket.apply_claude_actions("EURUSD", [{"type": "ARM", "side": "BUY", "trigger": "PULLBACK",
                                                "price": round(float(q["ask"]) - 0.0003, 5), "valid_minutes": 1,
                                                "why": "x"}], "PAPER", now)
    q2 = dict(q, ask=round(float(q["ask"]) - 0.0004, 5), bid=round(float(q["bid"]) - 0.0004, 5))
    assert eng.basket._fire_armed("EURUSD", [], q2, "PAPER", now + 61) is False and not eng.paper.positions
    # the next Claude decision replaces what was armed
    eng.basket.apply_claude_actions("EURUSD", [{"type": "ARM", "side": "SELL", "trigger": "PULLBACK",
                                                "price": round(float(q["bid"]) + 0.0003, 5), "why": "y"}], "PAPER", now)

    async def fake(payload, **kw):
        return {"status": "DONE", "actions": [], "market_view": "x", "plan": "y", "confidence": 0.5}
    eng.ai.decide_basket = fake
    asyncio.run(eng.basket.claude_decide_and_apply("EURUSD", {"positions": []}, "PAPER"))
    assert eng.basket.armed["EURUSD"] == []


def test_claude_sizes_the_lot_by_confidence_capped_by_margin_and_limits_follow_the_group_lot(engine, monkeypatch):
    eng = _claude_basket(engine, CLAUDE_MAX_LOT=0.5, BASKET_TRAIL_ENABLED=False)
    eng.db.set_setting("basket", {"claude": True, "max_loss_usd": 10, "start_lot": 0.01})
    q = eng.rt.quotes["EURUSD"]
    real_account = eng.risk_account
    acc = {"balance": 123.0, "equity": 123.0, "margin": 0.0, "margin_free": 123.0, "leverage": 100}
    monkeypatch.setattr(eng, "risk_account", lambda mode=None: ("X", acc))
    # EURUSD 1.08 x 100000 x 0.01 / 100 = ~10.8 USD per 0.01 lot -> half of 123 pays 0.05 lot
    assert eng.basket.margin_max_lot(q) == 0.05
    assert eng.basket._lot_tiers(eng.basket.cfg(), q) == {"unsure": 0.01, "good": 0.03, "very_sure": 0.05}
    acc["margin_free"] = 5.0
    done = eng.basket.apply_claude_actions("EURUSD", [{"type": "OPEN", "side": "BUY", "lot": 0.01, "why": "x"}],
                                           "PAPER", time.time())
    assert "marjă liberă insuficientă" in done[0]
    monkeypatch.setattr(eng, "risk_account", real_account)
    monkeypatch.setattr(eng.basket, "margin_max_lot", lambda q: 0.1)
    done = eng.basket.apply_claude_actions("EURUSD", [{"type": "OPEN", "side": "BUY", "lot": 0.3, "why": "sigur"}],
                                           "PAPER", time.time())
    assert "deschid BUY 0.1" in done[0] and "marja liberă" in done[0]
    assert [p["volume"] for p in eng.paper.positions.values()] == [0.1]
    # the group's first lot (0.10) scales the "USD at 0.01 lot" limits x10; flat again -> back to the start lot
    eng.basket._set_group_lot("EURUSD", eng.basket.positions("EURUSD"))
    c = eng.basket.cfg()
    assert c["lot_scale"] == 10 and c["limit"] == 100
    eng.basket._set_group_lot("EURUSD", [])
    assert eng.basket.cfg()["limit"] == 10
    # a lot below the start lot is raised to it
    eng.paper.positions.clear()
    eng.db.set_setting("basket", {"claude": True, "max_loss_usd": 10, "start_lot": 0.02})
    done = eng.basket.apply_claude_actions("EURUSD", [{"type": "OPEN", "side": "SELL", "lot": 0.01, "why": "y"}],
                                           "PAPER", time.time())
    assert "deschid SELL 0.02" in done[0]


def test_flat_basket_calls_use_the_cheaper_model_and_open_positions_keep_the_main_one(engine, monkeypatch):
    """Owner, 2026-11-03: the API cost (8.79 USD) was above the trading profit (+7.68 USD) since the morning restart -
    while the basket is flat Claude only re-arms entries and that call goes to BASKET_CLAUDE_FLAT_MODEL."""
    eng = _claude_basket(engine)
    eng.ai.settings = eng.ai.settings.model_copy(update={"CLAUDE_ENABLED": True, "ANTHROPIC_API_KEY": "test-key"})
    monkeypatch.setattr(pattern_context, "build", lambda rt, sym, now: {})
    now = time.time()
    eng.rt.closed_bars = lambda sym, tf, n=None: [_bar(int(now // 60 * 60) - 60, 1.08, 1.08, 1.08, 1.08)]
    models = []

    async def fake(payload, model=None):
        models.append(model)
        return {"status": "DONE", "actions": [], "market_view": "x", "plan": "y", "confidence": 0.5}
    eng.ai.decide_basket = fake
    eng.basket.tick(now)
    assert models == ["claude-sonnet-5-5"]
    eng.paper.open("EURUSD", "BUY", 0.01, 1.07, 1.09, eng.rt.quotes["EURUSD"])
    eng.rt.closed_bars = lambda sym, tf, n=None: [_bar(int(now // 60 * 60), 1.08, 1.08, 1.08, 1.08)]
    eng.basket._claude_task = None
    eng.basket.tick(now + 60)
    assert models == ["claude-sonnet-5-5", None]  # None = CLAUDE_MODEL (the position is brand new)
    # empty setting: every call on CLAUDE_MODEL
    eng.settings = eng.settings.model_copy(update={"BASKET_CLAUDE_FLAT_MODEL": ""})
    eng.paper.positions.clear()
    eng.rt.closed_bars = lambda sym, tf, n=None: [_bar(int(now // 60 * 60) + 60, 1.08, 1.08, 1.08, 1.08)]
    eng.basket._claude_task = None
    eng.basket.tick(now + 120)
    assert models[-1] is None


def test_decide_basket_passes_the_model_override_to_the_api(engine, monkeypatch):
    ai = engine.ai
    ai.settings = ai.settings.model_copy(update={"CLAUDE_ENABLED": True, "ANTHROPIC_API_KEY": "test-key"})
    seen = {}

    async def call_json(system, user_msg, schema, signal_id, model=None):
        seen["model"] = model
        return {"actions": [], "market_view": "x", "plan": "y", "confidence": 0.5, "gates": "-"}
    monkeypatch.setattr(ai, "_call_json", call_json)
    out = asyncio.run(ai.decide_basket({"positions": []}, model="claude-sonnet-5-5"))
    assert seen["model"] == "claude-sonnet-5-5" and out["status"] == "DONE" and out["model"] == "claude-sonnet-5-5"
    asyncio.run(ai.decide_basket({"positions": []}))
    assert seen["model"] == ai.settings.CLAUDE_MODEL


def test_failed_breakout_closes_in_code_and_holds_a_breakout_that_keeps_going(engine, monkeypatch):
    """03.11 09:02: SELL 0.02 on an M1 close under 3896.1, the 09:04 bar closed back at 3897.9 (-3.6 USD) and the position
    was held to -16.92 - a breakout entry now closes in code once a bar closes back through the level by 0.3 x ATR."""
    eng = _claude_basket(engine, BASKET_TRAIL_ENABLED=False)
    atr = 0.0010
    monkeypatch.setattr(eng.basket, "atr_m1", lambda sym, now: atr)
    now = time.time()
    q = dict(eng.rt.quotes["EURUSD"])
    bid = float(q["bid"])
    bar_t = int(now // 60 * 60)
    lvl = round(bid - 0.0002, 5)
    closed = [_bar(bar_t - 120, bid, bid, bid, bid)]
    eng.rt.closed_bars = lambda sym, tf, n=None: closed
    eng.basket.apply_claude_actions("EURUSD", [{"type": "ARM", "side": "SELL", "trigger": "BREAKOUT", "price": lvl,
                                                "why": "sub suport"}], "PAPER", now)
    closed.append(_bar(bar_t, bid, bid, round(lvl - 0.0003, 5), round(lvl - 0.0001, 5)))
    assert eng.basket._fire_armed("EURUSD", [], q, "PAPER", now + 61) is True
    assert eng.basket.breakout["EURUSD"]["level"] == lvl
    pos = eng.basket.positions("EURUSD")
    # a bar closing back above the level by less than 0.3 ATR: held
    closed.append(_bar(bar_t + 60, lvl, lvl + 0.0003, lvl, round(lvl + 0.0002, 5)))
    assert eng.basket._failed_breakout("EURUSD", pos, -0.5, now + 121) is False
    # in profit: never closed by this rule
    closed.append(_bar(bar_t + 120, lvl, lvl + 0.0006, lvl, round(lvl + 0.0005, 5)))
    assert eng.basket._failed_breakout("EURUSD", pos, +0.2, now + 181) is False
    # on a loss with the bar closed back 0.5 ATR above the level: the group closes as FAILED_BREAKOUT
    closes = []
    monkeypatch.setattr(eng.basket, "_close_all", lambda sym, p, reason, total, now: closes.append(reason))
    assert eng.basket._failed_breakout("EURUSD", pos, -1.2, now + 181) is True
    assert closes == ["FAILED_BREAKOUT"] and "EURUSD" not in eng.basket.breakout
    # a PULLBACK entry carries no breakout level
    eng.basket.breakout["EURUSD"] = {"side": "SELL", "level": lvl, "since": now}
    eng.paper.positions.clear()
    eng.basket.apply_claude_actions("EURUSD", [{"type": "ARM", "side": "SELL", "trigger": "PULLBACK",
                                                "price": round(float(q["bid"]) + 0.0002, 5), "why": "x"}], "PAPER", now)
    q2 = dict(q, bid=round(float(q["bid"]) + 0.0002, 5), ask=round(float(q["ask"]) + 0.0002, 5))
    assert eng.basket._fire_armed("EURUSD", [], q2, "PAPER", now + 200) is True
    assert "EURUSD" not in eng.basket.breakout


def test_broker_sl_sits_at_the_loss_limit_when_the_hedge_does_not_fit_in_the_margin(engine, monkeypatch):
    """Lot 0.02 on ~130 USD: the hedge (0.04) has no margin, and the Mac slept 05:03-06:49 - the broker SL must cap the
    position at the basket limit, not BASKET_SL_DISTANCE away."""
    eng = _claude_basket(engine, BASKET_SL_DISTANCE=0.05)
    eng.db.set_setting("basket", {"claude": True, "start_lot": 0.02, "max_loss_usd": 5.0})
    now = time.time()
    q = eng.rt.quotes["EURUSD"]
    contract = float(q.get("contract_size") or 100000)
    monkeypatch.setattr(eng.basket, "margin_max_lot", lambda q: 0.03)  # 0.02 + hedge 0.04 does not fit
    assert eng.basket.hedge_fits(q, [], 0.02) is False and eng.basket.hedge_fits(q, [], 0.01) is True
    eng.basket.apply_claude_actions("EURUSD", [{"type": "OPEN", "side": "BUY", "lot": 0.02, "why": "x"}], "PAPER", now)
    p = eng.basket.positions("EURUSD")[0]
    want = round(5.0 / (contract * 0.01) * 1.1, 5)
    assert abs((float(p["price_open"]) - float(p["sl"])) - want) < 2e-5
    # the hedge fits (0.01 + 0.02 <= 0.03): the broker SL is the fixed scalp SL
    eng.paper.positions.clear()
    eng.db.set_setting("basket", {"claude": True, "start_lot": 0.01, "max_loss_usd": 5.0})
    eng.basket.last_action["EURUSD"] = 0
    eng.basket.apply_claude_actions("EURUSD", [{"type": "OPEN", "side": "BUY", "lot": 0.01, "why": "x"}], "PAPER", now)
    p = eng.basket.positions("EURUSD")[0]
    assert abs((float(p["price_open"]) - float(p["sl"])) - eng.basket.cfg()["scalp_sl"]) < 2e-5


def test_protect_and_target_in_one_decision_go_to_the_broker_as_one_modify(engine, monkeypatch):
    """03.11 10:01: Claude sent PROTECT (SL 3909) and TARGET (TP 3904.8) together; the TARGET was refused because the
    PROTECT's MODIFY was still in flight - both now go out as one MODIFY."""
    eng = _claude_basket(engine)
    now = time.time()
    eng.basket.apply_claude_actions("EURUSD", [{"type": "OPEN", "side": "BUY", "lot": 0.01, "why": "x"}], "PAPER", now)
    p = eng.basket.positions("EURUSD")[0]
    tk, open_px = int(p["ticket"]), float(p["price_open"])
    eng.rt.quotes["EURUSD"].update(bid=round(open_px + 0.0010, 5), ask=round(open_px + 0.0011, 5))
    sent = []
    eng.executor.submit_modify = lambda *a, **k: sent.append(a)
    eng.executor.has_pending_for_ticket = lambda *a, **k: bool(sent)  # in flight after the first MODIFY
    done = eng.basket.apply_claude_actions("EURUSD", [
        {"type": "PROTECT", "ticket": tk, "price": round(open_px + 0.0004, 5), "why": "x"},
        {"type": "TARGET", "ticket": tk, "price": round(open_px + 0.0030, 5), "why": "y"}], "PAPER", now)
    assert len(sent) == 1 and sent[0][4] == round(open_px + 0.0004, 5) and sent[0][5] == round(open_px + 0.0030, 5)
    assert len(done) == 1 and "SL #" in done[0] and "TP #" in done[0]


def test_losing_streak_summarises_the_last_losses_on_one_side():
    from app.basket import BasketTrader
    t = lambda side, px, pl, at: {"side": side, "price_open": px, "profit": pl, "closed_at_utc": at}
    recent = [t("SELL", 3904.43, -4.00, "11:00"), t("SELL", 3905.15, -2.30, "10:50"), t("SELL", 3904.28, -1.34, "10:47"),
              t("SELL", 3904.82, 3.96, "10:40")]
    st = BasketTrader._losing_streak(recent)
    assert st == {"side": "SELL", "losses_in_row": 3, "lost_usd": -7.64, "entry_zone": [3904.28, 3905.15],
                  "first_close_utc": "10:47"}
    assert BasketTrader._losing_streak(recent[1:2] + recent[3:]) is None  # one loss is not a streak
    assert BasketTrader._losing_streak([t("SELL", 1, -1, "a"), t("BUY", 1, -1, "b")]) is None  # sides differ
    assert BasketTrader._losing_streak([]) is None


def test_claude_close_on_a_young_losing_position_is_refused(engine):
    """30.10-03.11: Claude's losing closes in the first 10 minutes cost -103.67 USD vs -14.57 had the SL been left."""
    eng = _claude_basket(engine)
    eng.paper.open("EURUSD", "BUY", 0.01, 1.07, 1.09, eng.rt.quotes["EURUSD"])
    eng.rt.ingest_quote("EURUSD", quote(bid=1.07950))
    eng.paper.on_quote("EURUSD", eng.rt.quotes["EURUSD"])
    tk = next(iter(eng.paper.positions.values()))["ticket"]
    now = time.time()
    done = eng.basket.apply_claude_actions("EURUSD", [{"type": "CLOSE", "ticket": tk}], "PAPER", now)
    assert "refuzat" in done[0] and "PROTECT" in done[0] and len(eng.paper.positions) == 1
    done = eng.basket.apply_claude_actions("EURUSD", [{"type": "CLOSE_ALL"}], "PAPER", now)
    assert "refuzat" in done[0] and len(eng.paper.positions) == 1
    # after the minimum age the close goes through
    done = eng.basket.apply_claude_actions("EURUSD", [{"type": "CLOSE", "ticket": tk}], "PAPER", now + 11 * 60)
    assert "închid" in done[0] and not eng.paper.positions


def test_claude_close_in_profit_is_never_held_back(engine):
    eng = _claude_basket(engine)
    eng.paper.open("EURUSD", "BUY", 0.01, 1.07, 1.09, eng.rt.quotes["EURUSD"])
    eng.rt.ingest_quote("EURUSD", quote(bid=1.08300))
    eng.paper.on_quote("EURUSD", eng.rt.quotes["EURUSD"])
    done = eng.basket.apply_claude_actions("EURUSD", [{"type": "CLOSE_ALL"}], "PAPER", time.time())
    assert "închid" in done[0] and not eng.paper.positions


def test_smart_route_sends_routine_bars_to_the_light_model_and_decisions_to_the_main_one(engine, monkeypatch):
    """Owner, 2026-11-03: Opus for the important decisions, Sonnet for the light ones."""
    eng = _claude_basket(engine)
    b = eng.basket
    now = time.time()
    monkeypatch.setattr(b, "fast_vote", lambda sym, now: (None, {}))
    assert b._route_model("EURUSD", [], False, False, now)[0] == "claude-sonnet-5-5"
    monkeypatch.setattr(b, "fast_vote", lambda sym, now: ("SELL", {}))
    assert b._route_model("EURUSD", [], False, False, now)[0] is None  # direction decision
    # 06.11: aligned but the M5 gap is under the entry minimum -> no entry can pass, the light model watches
    eng.settings = eng.settings.model_copy(update={"BASKET_MIN_M5_GAP_ATR": 0.4})
    monkeypatch.setattr(b, "m5_gap_atr", lambda sym, now: -0.2)
    m, why = b._route_model("EURUSD", [], False, False, now)
    assert m == "claude-sonnet-5-5" and "M5 fără trend" in why
    monkeypatch.setattr(b, "m5_gap_atr", lambda sym, now: -0.9)
    assert b._route_model("EURUSD", [], False, False, now)[0] is None
    monkeypatch.setattr(b, "m5_gap_atr", lambda sym, now: None)  # unknown gap: unchanged behaviour
    assert b._route_model("EURUSD", [], False, False, now)[0] is None
    eng.settings = eng.settings.model_copy(update={"BASKET_MIN_M5_GAP_ATR": 0.0})
    eng.paper.open("EURUSD", "BUY", 0.01, 1.07, 1.09, eng.rt.quotes["EURUSD"])
    pos = b.positions("EURUSD")
    assert b._route_model("EURUSD", pos, False, False, now)[0] is None  # brand-new position
    later = now + 5 * 60
    assert b._route_model("EURUSD", pos, False, False, later)[0] == "claude-sonnet-5-5"
    assert b._route_model("EURUSD", pos, False, True, later)[0] is None  # investigation
    assert b._route_model("EURUSD", pos, True, False, later)[0] is None  # hedged group


def test_light_model_entry_or_exit_is_confirmed_by_the_main_model(engine, monkeypatch):
    eng = _claude_basket(engine)
    calls = []

    async def fake(payload, model=None):
        calls.append(model)
        if model:  # light model wants to enter
            return {"status": "DONE", "actions": [{"type": "OPEN", "side": "BUY", "lot": 0.01}], "plan": "buy"}
        return {"status": "DONE", "actions": [], "market_view": "nu", "plan": "aștept"}
    eng.ai.decide_basket = fake
    rec = asyncio.run(eng.basket.claude_decide_and_apply("EURUSD", {"positions": []}, "PAPER", "claude-sonnet-5-5"))
    assert calls == ["claude-sonnet-5-5", None] and not eng.paper.positions and rec["plan"] == "aștept"
    # a light answer with no entry/exit is executed as it is, no second call
    calls.clear()

    async def quiet(payload, model=None):
        calls.append(model)
        return {"status": "DONE", "actions": [], "plan": "aștept"}
    eng.ai.decide_basket = quiet
    asyncio.run(eng.basket.claude_decide_and_apply("EURUSD", {"positions": []}, "PAPER", "claude-sonnet-5-5"))
    assert calls == ["claude-sonnet-5-5"]


def test_no_reentry_on_the_same_side_inside_the_zone_of_the_last_two_losses(engine, monkeypatch):
    """03.11 10:42-11:40: five SELLs at 3903.8-3905.2 under the same level, all stopped out."""
    from datetime import datetime, timezone
    eng = _claude_basket(engine)
    b = eng.basket
    monkeypatch.setattr(b, "atr_m1", lambda sym, now: 0.0005)
    now = time.time()
    iso = lambda t: datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def trade(i, side, px, pl, ago):
        eng.db.execute("INSERT INTO trades (deal_key, ticket, symbol, side, volume, price_open, price_close, profit, "
                       "opened_at_utc, closed_at_utc, close_reason, mode) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                       (f"z{i}", 200 + i, "EURUSD", side, 0.02, px, px, pl, iso(now - ago - 60), iso(now - ago), "SL", "DEMO"))
    trade(1, "SELL", 1.0800, -1.34, 1200)
    trade(2, "BUY", 1.0790, -2.18, 900)  # the other side does not break the SELL run
    trade(3, "SELL", 1.0803, -2.30, 600)
    assert "pierdut" in b._zone_block("EURUSD", "SELL", 1.0806, now)  # within 1.0803 +- 0.0005
    assert b._zone_block("EURUSD", "SELL", 1.0812, now) is None  # between two far-apart losses is NOT blocked
    assert b._zone_block("EURUSD", "SELL", 1.0790, now) is None  # well outside the zone
    assert b._zone_block("EURUSD", "BUY", 1.0801, now) is None  # the BUY side has only one loss
    trade(5, "BUY", 1.1500, -3.0, 24 * 3600)  # a BUY loss from the day before does not make a run
    assert b._zone_block("EURUSD", "BUY", 1.0795, now) is None
    assert b._zone_block("EURUSD", "SELL", 1.0801, now + 91 * 60) is None  # expired
    done = b.apply_claude_actions("EURUSD", [{"type": "ARM", "side": "SELL", "trigger": "PULLBACK", "price": 1.0804,
                                              "valid_minutes": 5}], "PAPER", now)
    assert "refuzat" in done[0] and not b.armed.get("EURUSD")
    trade(4, "SELL", 1.0801, 0.8, 300)  # a SELL win resets the run
    assert b._zone_block("EURUSD", "SELL", 1.0801, now) is None


def test_entry_gets_fixed_scalp_tp_and_sl(engine, monkeypatch):
    """Owner, 2026-11-03 14:30: many small wins - fixed TP 1.5 / SL 4 (price) on every Claude entry."""
    eng = _claude_basket(engine, BASKET_SCALP_TP_PRICE=0.0015, BASKET_SCALP_SL_PRICE=0.0040)
    monkeypatch.setattr(eng.basket, "atr_m1", lambda sym, now: 0.0010)
    sent = []
    monkeypatch.setattr(eng.executor, "submit_modify", lambda *a, **k: sent.append(a))
    done = eng.basket.apply_claude_actions("EURUSD", [{"type": "OPEN", "side": "BUY", "lot": 0.01}],
                                           "PAPER", time.time())
    assert "deschid BUY" in done[0]
    p = next(iter(eng.paper.positions.values()))
    assert abs(p["tp"] - (p["price_open"] + 0.0015)) < 2e-5 and abs(p["sl"] - (p["price_open"] - 0.0040)) < 2e-5
    # price runs in favour: no MODIFY, the broker TP takes the profit
    eng.rt.ingest_quote("EURUSD", quote(bid=p["price_open"] + 0.0012))
    eng.paper.on_quote("EURUSD", eng.rt.quotes["EURUSD"])
    eng.basket._claude_tick("EURUSD", eng.basket.positions("EURUSD"), eng.rt.quotes["EURUSD"], "PAPER", time.time())
    assert not sent
    # PROTECT may lock profit but not pull the SL onto the loss side
    done = eng.basket.apply_claude_actions("EURUSD", [{"type": "PROTECT", "ticket": p["ticket"],
                                                       "price": round(p["price_open"] - 0.0010, 5)}], "PAPER", time.time())
    assert "refuzat" in done[0] and "fix" in done[0]



def test_m5_gate_refuses_a_new_entry_against_the_m5_trend(engine, monkeypatch):
    """03.11: entries against M5 (EMA9 vs EMA21) won 5 of 12 and lost 14 USD per 0.01 lot."""
    eng = _claude_basket(engine)
    monkeypatch.setattr(eng.basket, "m5_gap_atr", lambda sym, now: -1.0)  # trend timeframe (M5) points down
    done = eng.basket.apply_claude_actions("EURUSD", [{"type": "OPEN", "side": "BUY", "lot": 0.01, "why": "x"}],
                                           "PAPER", time.time())
    assert "refuzat" in done[0] and "M5" in done[0] and not eng.paper.positions
    done = eng.basket.apply_claude_actions("EURUSD", [{"type": "OPEN", "side": "SELL", "lot": 0.01, "why": "x"}],
                                           "PAPER", time.time())
    assert "deschid SELL" in done[0]
    # unknown M5 (not enough bars) does not block
    eng.paper.positions.clear()
    monkeypatch.setattr(eng.basket, "m5_gap_atr", lambda sym, now: None)
    eng.basket.last_action["EURUSD"] = 0
    done = eng.basket.apply_claude_actions("EURUSD", [{"type": "OPEN", "side": "BUY", "lot": 0.01, "why": "x"}],
                                           "PAPER", time.time())
    assert "deschid BUY" in done[0]


def test_m5_flat_refuses_open_and_holds_an_armed_entry(engine, monkeypatch):
    """06.11: 140 entries, M5 EMA9-EMA21 gap under 0.4 x ATR M1 won 17 of 35 (-28.4 USD / 0.01 lot); 06:20 and 07:40
    two SELLs at a 0.2-0.3 gap inside a 3-hour range, both stopped out by the breakout."""
    eng = _claude_basket(engine, BASKET_MIN_M5_GAP_ATR=0.4)
    b = eng.basket
    now = time.time()
    monkeypatch.setattr(b, "fast_vote", lambda sym, now: (None, {"M1": 0, "M5": 1}))  # M5 "up", but glued
    monkeypatch.setattr(b, "m5_gap_atr", lambda sym, now: 0.2)
    done = b.apply_claude_actions("EURUSD", [{"type": "OPEN", "side": "BUY", "lot": 0.01, "why": "x"}], "PAPER", now)
    assert "refuzat" in done[0] and "M5 fără trend" in done[0] and not eng.paper.positions
    # an armed pullback at the price waits (the arm stays) instead of filling
    b.armed["EURUSD"] = [{"side": "BUY", "trigger": "PULLBACK", "price": 1.0805, "expires": now + 300, "lot": 0.01,
                          "armed_at": now - 30, "expires_utc": "", "why": "x"}]
    assert b._fire_armed("EURUSD", [], eng.rt.quotes["EURUSD"], "PAPER", now) is False
    assert b.armed["EURUSD"] and not eng.paper.positions
    # a real M5 trend (gap 0.6) lets the same entry through; a SELL against a +0.6 gap is still refused
    monkeypatch.setattr(b, "m5_gap_atr", lambda sym, now: 0.6)
    assert b._fire_armed("EURUSD", [], eng.rt.quotes["EURUSD"], "PAPER", now) is True
    assert eng.paper.positions
    eng.paper.positions.clear()
    b.last_action["EURUSD"] = 0
    monkeypatch.setattr(b, "fast_vote", lambda sym, now: (None, {"M1": 0, "M5": 0}))
    done = b.apply_claude_actions("EURUSD", [{"type": "OPEN", "side": "SELL", "lot": 0.01, "why": "x"}], "PAPER", now)
    assert "refuzat" in done[0] and "contra trendului" in done[0]
    # not enough M5 bars (None) does not block; 0 switches the guard off
    monkeypatch.setattr(b, "m5_gap_atr", lambda sym, now: None)
    b.last_action["EURUSD"] = 0
    done = b.apply_claude_actions("EURUSD", [{"type": "OPEN", "side": "SELL", "lot": 0.01, "why": "x"}], "PAPER", now)
    assert "deschid SELL" in done[0]


def test_m5_gap_atr_reads_the_closed_m5_bars(engine, monkeypatch):
    eng = _claude_basket(engine, BASKET_MIN_M5_GAP_ATR=0.4)
    b = eng.basket
    now = time.time()
    t0 = int(now // 300 * 300)
    monkeypatch.setattr(b, "atr_m1", lambda sym, now: 0.0010)
    # 40 rising M5 closes: EMA9 above EMA21 by a few ATR
    eng.rt.closed_bars = lambda sym, tf, n=None: [_bar(t0 - 300 * (40 - i), 1.08 + i * 0.001, 1.08 + i * 0.001,
                                                        1.08 + i * 0.001, 1.08 + i * 0.001) for i in range(40)]
    gap = b.m5_gap_atr("EURUSD", now)
    assert gap is not None and gap > 2
    assert b._m5_flat("EURUSD", "BUY", now) is None and "M5 fără trend" in b._m5_flat("EURUSD", "SELL", now)
    assert "m5_gap_atr" in b._trend_now("EURUSD", eng.rt.quotes["EURUSD"], now)
    eng.rt.closed_bars = lambda sym, tf, n=None: []
    assert b.m5_gap_atr("EURUSD", now) is None


def test_pullback_fill_waits_while_the_m1_bar_is_an_impulse_against(engine, monkeypatch):
    """13:34 / 14:23: BUY pullbacks filled inside a falling impulse and stopped out within a minute."""
    eng = _claude_basket(engine)
    b = eng.basket
    now = time.time()
    monkeypatch.setattr(b, "atr_m1", lambda sym, now: 0.0010)
    monkeypatch.setattr(b, "fast_vote", lambda sym, now: (None, {"M1": 0, "M5": 0}))
    t0 = int(now // 60 * 60)
    # last closed bar flat, forming bar opened 0.0015 above the current ask (a 1.5 x ATR drop in progress)
    eng.rt.closed_bars = lambda sym, tf, n=None: [_bar(t0 - 60, 1.0800, 1.0800, 1.0800, 1.0800)]
    eng.rt.bars[("EURUSD", "M1")] = [_bar(t0 - 60, 1.0800, 1.0800, 1.0800, 1.0800), _bar(t0, 1.0816, 1.0816, 1.0800, 1.0801)]
    b.armed["EURUSD"] = [{"side": "BUY", "trigger": "PULLBACK", "price": 1.0805, "expires": now + 300, "lot": 0.01,
                          "armed_at": now - 30, "expires_utc": "", "why": "x"}]
    assert b._fire_armed("EURUSD", [], eng.rt.quotes["EURUSD"], "PAPER", now) is False
    assert b.armed["EURUSD"] and not eng.paper.positions and "impuls" in b.state["EURUSD"].get("analysis", "impuls")
    # the impulse stalled: the forming bar now opened only 0.0003 above -> the fill goes through
    eng.rt.bars[("EURUSD", "M1")][-1] = _bar(t0, 1.0804, 1.0805, 1.0800, 1.0801)
    assert b._fire_armed("EURUSD", [], eng.rt.quotes["EURUSD"], "PAPER", now) is True
    assert eng.paper.positions and not b.armed["EURUSD"]


def test_margin_max_lot_keeps_half_of_the_free_margin(engine, monkeypatch):
    eng = _claude_basket(engine)
    monkeypatch.setattr(eng, "risk_account", lambda mode=None: ("PAPER", {"margin_free": 84.0, "leverage": 100.0}))
    q = {"ask": 3946.0, "contract_size": 100}
    assert eng.basket.margin_max_lot(q) == 0.01  # 0.5 x 84 = 42 USD pays for one 0.01 lot (39.46 USD)
    monkeypatch.setattr(eng, "risk_account", lambda mode=None: ("PAPER", {"margin_free": 200.0, "leverage": 100.0}))
    assert eng.basket.margin_max_lot(q) == 0.02


def test_armed_entry_skips_claude_calls_until_it_fires_or_the_price_leaves(engine, monkeypatch):
    """API cost: a live armed entry is filled by the code; the bars in between do not call Claude. (Since 06.11 an arm the
    price ran away from waits for the M5 close; this test keeps the flat-wait throttle off so the run-away bar calls.)"""
    eng = _claude_basket(engine, BASKET_CLAUDE_FLAT_WAIT_EVERY_BARS=1)
    eng.ai.settings = eng.ai.settings.model_copy(update={"CLAUDE_ENABLED": True, "ANTHROPIC_API_KEY": "test-key"})
    monkeypatch.setattr(pattern_context, "build", lambda rt, sym, now: {})
    monkeypatch.setattr(eng.basket, "atr_m1", lambda sym, now: 0.0010)
    # M1 and M5 aligned -> the call goes straight to the main model (no light-model confirmation round trip)
    monkeypatch.setattr(eng.basket, "fast_vote", lambda sym, now: ("BUY", {"M1": 1, "M5": 1}))
    now = time.time()
    t0 = int(now // 60 * 60)
    calls = []

    async def fake(payload, model=None):
        calls.append(model)
        return {"status": "DONE", "actions": [{"type": "ARM", "side": "BUY", "trigger": "PULLBACK", "price": 1.0790,
                                               "valid_minutes": 10}], "market_view": "x", "plan": "y", "confidence": 0.5}
    eng.ai.decide_basket = fake
    eng.rt.closed_bars = lambda sym, tf, n=None: [_bar(t0 - 60, 1.08, 1.08, 1.08, 1.08)]
    eng.basket.tick(now)
    assert len(calls) == 1 and eng.basket.armed["EURUSD"]
    # next bar, price still within 1.5 x ATR of the arm: no call
    eng.rt.closed_bars = lambda sym, tf, n=None: [_bar(t0, 1.08, 1.08, 1.08, 1.08)]
    eng.basket._claude_task = None
    eng.basket.tick(now + 60)
    assert len(calls) == 1 and "armată activă" in eng.basket.state["EURUSD"]["analysis"]
    # the price ran away (> 1.5 x ATR from the arm): Claude is asked again
    eng.rt.ingest_quote("EURUSD", quote(bid=1.08300))
    eng.rt.closed_bars = lambda sym, tf, n=None: [_bar(t0 + 60, 1.083, 1.083, 1.083, 1.083)]
    eng.basket._claude_task = None
    eng.basket.tick(now + 120)
    assert len(calls) == 2


def test_open_position_calls_claude_on_the_first_bar_then_every_third(engine, monkeypatch):
    eng = _claude_basket(engine)
    eng.ai.settings = eng.ai.settings.model_copy(update={"CLAUDE_ENABLED": True, "ANTHROPIC_API_KEY": "test-key"})
    monkeypatch.setattr(pattern_context, "build", lambda rt, sym, now: {})
    now = time.time()
    t0 = int(now // 60 * 60)
    calls = []

    async def fake(payload, model=None):
        calls.append(model)
        return {"status": "DONE", "actions": [], "market_view": "x", "plan": "y", "confidence": 0.5}
    eng.ai.decide_basket = fake
    eng.paper.open("EURUSD", "BUY", 0.01, 1.07, 1.09, eng.rt.quotes["EURUSD"])
    for i in range(6):
        eng.rt.closed_bars = lambda sym, tf, n=None, i=i: [_bar(t0 + 60 * i, 1.08, 1.08, 1.08, 1.08)]
        eng.basket._claude_task = None
        eng.basket.tick(now + 60 * (i + 1))
    assert len(calls) == 3  # bars 1, 3 and 6


def test_entry_lot_is_capped_by_the_risk_of_its_fixed_sl(engine, monkeypatch):
    """Exness 1:2000: the margin allows 0.5 lot on 200 USD; one 4 USD SL would lose the account."""
    eng = _claude_basket(engine, BASKET_SCALP_SL_PRICE=4.0, BASKET_MAX_RISK_PCT_PER_ENTRY=5.0, CLAUDE_MAX_LOT=0.5)
    acc = {"balance": 200.0, "equity": 200.0, "margin": 0.0, "margin_free": 200.0, "leverage": 2000}
    monkeypatch.setattr(eng, "risk_account", lambda mode=None: ("X", acc))
    eng.rt.quotes["EURUSD"].update(contract_size=100, ask=3946.0, bid=3945.8)
    q = eng.rt.quotes["EURUSD"]
    assert eng.basket.risk_max_lot(q) == 0.02  # 10 USD of risk / 4 USD per 0.01 lot
    assert eng.basket._entry_max_lot(eng.basket.cfg(), q) == 0.02
    done = eng.basket.apply_claude_actions("EURUSD", [{"type": "OPEN", "side": "BUY", "lot": 0.5, "why": "sigur"}],
                                           "PAPER", time.time())
    assert "deschid BUY 0.02" in done[0] and "% din sold" in done[0]


def test_auto_hedge_opens_x4_against_a_losing_position_and_the_group_closes_at_zero(engine, monkeypatch):
    """Owner's rule 03.11: at -3.5 USD of price the code hedges with 4 x the lot instead of taking the 4 USD stop."""
    eng = _claude_basket(engine, BASKET_HEDGE_AT_PRICE=0.0020, BASKET_ZR_MULT=4.0, BASKET_SCALP_SL_PRICE=0.0040,
                         BASKET_MAX_LOSS_USD=50.0, BASKET_RECOVERY_EXIT_USD=0.1, BASKET_HEDGE_ON_CLOSE=False)
    eng.ai.settings = eng.ai.settings.model_copy(update={"CLAUDE_ENABLED": True, "ANTHROPIC_API_KEY": "test-key"})
    monkeypatch.setattr(pattern_context, "build", lambda rt, sym, now: {})
    monkeypatch.setattr(eng.basket, "hedge_fits", lambda q, pos, volume=None: True)

    async def quiet(payload, model=None):
        return {"status": "DONE", "actions": [], "market_view": "x", "plan": "y", "confidence": 0.5}
    eng.ai.decide_basket = quiet
    now = time.time()
    eng.rt.closed_bars = lambda sym, tf, n=None: [_bar(int(now // 60 * 60) - 60, 1.08, 1.08, 1.08, 1.08)]
    eng.basket.apply_claude_actions("EURUSD", [{"type": "OPEN", "side": "BUY", "lot": 0.01, "why": "x"}], "PAPER", now)
    base = eng.basket.positions("EURUSD")[0]
    # 0.0010 against: nothing yet
    eng.rt.ingest_quote("EURUSD", quote(bid=round(base["price_open"] - 0.0010, 5)))
    eng.paper.on_quote("EURUSD", eng.rt.quotes["EURUSD"])
    eng.basket.last_action["EURUSD"] = 0
    eng.basket.tick(now + 10)
    assert len(eng.paper.positions) == 1
    # 0.0021 against: the SELL 0.04 hedge opens with a far TP
    eng.rt.ingest_quote("EURUSD", quote(bid=round(base["price_open"] - 0.0021, 5)))
    eng.paper.on_quote("EURUSD", eng.rt.quotes["EURUSD"])
    eng.basket.last_action["EURUSD"] = 0
    eng.basket.tick(now + 20)
    hedge = next(p for p in eng.paper.positions.values() if p["side"] == "SELL")
    assert hedge["volume"] == 0.04 and abs(hedge["tp"] - eng.rt.quotes["EURUSD"]["bid"]) > 0.04
    # the base leg's SL/TP are pushed far so it cannot close alone
    eng.basket.last_action["EURUSD"] = 0
    eng.basket.tick(now + 30)
    b = eng.paper.positions[base["ticket"]]
    assert b["sl"] < base["price_open"] - 0.04 and b["tp"] > base["price_open"] + 0.04
    # the price keeps falling: the x4 hedge pulls the group above zero -> both close in code
    eng.rt.ingest_quote("EURUSD", quote(bid=round(base["price_open"] - 0.0030, 5)))
    eng.paper.on_quote("EURUSD", eng.rt.quotes["EURUSD"])
    eng.basket.last_action["EURUSD"] = 0
    eng.basket.tick(now + 40)
    assert not eng.paper.positions


def test_payload_carries_session_trend_and_bot_stats(engine, monkeypatch):
    """Owner, 03.11 evening: Claude must see the session, the live trend and the bot's own record for this hour."""
    eng = _claude_basket(engine)
    monkeypatch.setattr(pattern_context, "build", lambda rt, sym, now: {})
    monkeypatch.setattr(eng.basket, "fast_vote", lambda sym, now: ("SELL", {"M1": -1, "M5": -1}))
    monkeypatch.setattr(eng.basket, "mtf_vote", lambda sym, now: (None, {"M1": -1, "M5": -1, "M15": 1, "H1": 1}))
    now = time.time()
    t0 = int(now // 60 * 60)
    eng.rt.closed_bars = lambda sym, tf, n=None: [_bar(t0 - 60 * (20 - i), 1.080, 1.081, 1.079, 1.0805) for i in range(20)]
    eng.db.execute("INSERT INTO trades (deal_key, ticket, symbol, side, volume, price_open, price_close, profit, "
                   "opened_at_utc, closed_at_utc, close_reason, mode) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                   ("s1", 1, "EURUSD", "SELL", 0.01, 1.08, 1.079, 1.5, time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - 120)),
                    time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - 60)), "TP", "DEMO"))
    pl = eng.basket._claude_payload("EURUSD", [], eng.rt.quotes["EURUSD"], now)
    assert pl["session_now"]["session"] and pl["session_now"]["utc"] == time.strftime("%H:%M", time.gmtime(now))
    t = pl["trend_now"]
    assert t["m5_ema9_21"] == "jos" and t["m15_ema20_50"] == "sus" and t["short_term_consensus"] == "SELL"
    assert t["range20_usd"] == 0.0 or t["range_regime"] == "îngust"
    assert pl["bot_stats"]["today"]["n"] == 1 and pl["bot_stats"]["this_utc_hour_7d"]["wins"] == 1


def test_no_claude_call_while_the_rollover_blackout_blocks_entries(engine, monkeypatch):
    """21:44-22:20 UTC on 03.11: 28 Opus calls that could only answer "wait"."""
    eng = _claude_basket(engine)
    eng.ai.settings = eng.ai.settings.model_copy(update={"CLAUDE_ENABLED": True, "ANTHROPIC_API_KEY": "test-key"})
    monkeypatch.setattr(pattern_context, "build", lambda rt, sym, now: {})
    monkeypatch.setattr(eng.basket, "rollover_note", lambda now: "Rollover în 10 min")
    calls = []

    async def fake(payload, model=None):
        calls.append(model)
        return {"status": "DONE", "actions": [], "market_view": "x", "plan": "y", "confidence": 0.5}
    eng.ai.decide_basket = fake
    now = time.time()
    eng.rt.closed_bars = lambda sym, tf, n=None: [_bar(int(now // 60 * 60) - 60, 1.08, 1.08, 1.08, 1.08)]
    eng.basket.tick(now)
    assert not calls and "Rollover" in eng.basket.state["EURUSD"]["analysis"]
    # with a position open the rollover does not silence Claude
    eng.paper.open("EURUSD", "BUY", 0.01, 1.07, 1.09, eng.rt.quotes["EURUSD"])
    eng.rt.closed_bars = lambda sym, tf, n=None: [_bar(int(now // 60 * 60), 1.08, 1.08, 1.08, 1.08)]
    eng.basket._claude_task = None
    eng.basket.tick(now + 60)
    assert len(calls) == 1


def test_hedge_waits_for_an_m1_close_beyond_the_trigger_not_a_wick(engine, monkeypatch):
    """23:11 on 03.11: the hedge opened on the exact low of a wick and the next bar jumped 5 USD against it."""
    eng = _claude_basket(engine, BASKET_HEDGE_AT_PRICE=0.0020, BASKET_ZR_MULT=4.0, BASKET_SCALP_SL_PRICE=0.0040,
                         BASKET_MAX_LOSS_USD=50.0)
    eng.ai.settings = eng.ai.settings.model_copy(update={"CLAUDE_ENABLED": True, "ANTHROPIC_API_KEY": "test-key"})
    monkeypatch.setattr(pattern_context, "build", lambda rt, sym, now: {})
    monkeypatch.setattr(eng.basket, "hedge_fits", lambda q, pos, volume=None: True)

    async def quiet(payload, model=None):
        return {"status": "DONE", "actions": [], "market_view": "x", "plan": "y", "confidence": 0.5}
    eng.ai.decide_basket = quiet
    now = time.time()
    t0 = int(now // 60 * 60)
    eng.basket.apply_claude_actions("EURUSD", [{"type": "OPEN", "side": "BUY", "lot": 0.01, "why": "x"}], "PAPER", now)
    base = eng.basket.positions("EURUSD")[0]
    po = base["price_open"]
    # the price wicks 0.0025 against, but the last closed bar closed only 0.0010 against: no hedge yet
    eng.rt.ingest_quote("EURUSD", quote(bid=round(po - 0.0025, 5)))
    eng.paper.on_quote("EURUSD", eng.rt.quotes["EURUSD"])
    eng.rt.closed_bars = lambda sym, tf, n=None: [_bar(t0 - 60, po, po, po - 0.0025, po - 0.0010)]
    eng.basket.last_action["EURUSD"] = 0
    eng.basket.tick(now + 10)
    assert len(eng.paper.positions) == 1 and "așteaptă" in [r["message"] for r in eng.db.query(
        "SELECT message FROM system_events ORDER BY id DESC LIMIT 3")][0] or len(eng.paper.positions) == 1
    # a bar closes 0.0022 against: the hedge opens
    eng.rt.closed_bars = lambda sym, tf, n=None: [_bar(t0, po - 0.0010, po - 0.0010, po - 0.0026, po - 0.0022)]
    eng.basket.last_action["EURUSD"] = 0
    eng.basket.tick(now + 20)
    assert any(p["side"] == "SELL" and p["volume"] == 0.04 for p in eng.paper.positions.values())


def test_no_new_entry_and_no_claude_call_right_after_the_market_reopens(engine, monkeypatch):
    """Exness daily break 22:00-23:05 UTC: the first minutes after the reopen whip both ways."""
    eng = _claude_basket(engine, BASKET_REOPEN_BLACKOUT_MINUTES=15, BASKET_REOPEN_GAP_MINUTES=20)
    eng.ai.settings = eng.ai.settings.model_copy(update={"CLAUDE_ENABLED": True, "ANTHROPIC_API_KEY": "test-key"})
    monkeypatch.setattr(pattern_context, "build", lambda rt, sym, now: {})
    calls = []

    async def fake(payload, model=None):
        calls.append(model)
        return {"status": "DONE", "actions": [], "market_view": "x", "plan": "y", "confidence": 0.5}
    eng.ai.decide_basket = fake
    now = time.time()
    t0 = int(now // 60 * 60)
    eng.rt.closed_bars = lambda sym, tf, n=None: [_bar(t0 - 60, 1.08, 1.08, 1.08, 1.08)]
    eng.rt.ingest_quote("EURUSD", quote(bid=1.08, time_msc=int((now - 3600) * 1000)))
    eng.basket.tick(now - 3600)  # a normal tick an hour ago
    assert len(calls) == 1
    eng.rt.ingest_quote("EURUSD", quote(bid=1.081, time_msc=int(now * 1000)))  # first tick after a 60-min gap
    eng.rt.closed_bars = lambda sym, tf, n=None: [_bar(t0, 1.08, 1.081, 1.08, 1.081)]
    eng.basket._claude_task = None
    eng.basket.tick(now)
    assert len(calls) == 1 and "redeschis" in eng.basket.state["EURUSD"]["analysis"]
    done = eng.basket.apply_claude_actions("EURUSD", [{"type": "OPEN", "side": "BUY", "lot": 0.01, "why": "x"}], "PAPER", now)
    assert "refuzat" in done[0] and "redeschis" in done[0]
    # 16 minutes later: back to normal
    eng.rt.closed_bars = lambda sym, tf, n=None: [_bar(t0 + 960, 1.08, 1.081, 1.08, 1.081)]
    eng.basket._claude_task = None
    eng.basket.tick(now + 16 * 60)
    assert len(calls) == 2


def test_entry_waits_after_a_run_of_m1_bars_in_the_trade_direction(engine, monkeypatch):
    """04.11 09:28: a BUY pullback filled after three rising M1 bars and was stopped out in 5 minutes."""
    eng = _claude_basket(engine)
    b = eng.basket
    now = time.time()
    monkeypatch.setattr(b, "atr_m1", lambda sym, now: 0.0010)
    monkeypatch.setattr(b, "fast_vote", lambda sym, now: (None, {"M1": 0, "M5": 0}))
    t0 = int(now // 60 * 60)
    rising = [_bar(t0 - 240, 1.0790, 1.0794, 1.0790, 1.0793), _bar(t0 - 180, 1.0793, 1.0797, 1.0793, 1.0796),
              _bar(t0 - 120, 1.0796, 1.0800, 1.0796, 1.0799)]
    eng.rt.closed_bars = lambda sym, tf, n=None: rising
    eng.rt.bars[("EURUSD", "M1")] = rising + [_bar(t0, 1.0799, 1.0806, 1.0799, 1.0801)]
    b.armed["EURUSD"] = [{"side": "BUY", "trigger": "PULLBACK", "price": 1.0805, "expires": now + 300, "lot": 0.01,
                          "armed_at": now - 30, "expires_utc": "", "why": "x"}]
    assert b._fire_armed("EURUSD", [], eng.rt.quotes["EURUSD"], "PAPER", now) is False
    assert b.armed["EURUSD"] and not eng.paper.positions
    # a direct OPEN after the run is refused too; a SELL (against the run) is not chasing
    done = b.apply_claude_actions("EURUSD", [{"type": "OPEN", "side": "BUY", "lot": 0.01, "why": "x"}], "PAPER", now)
    assert any("3 bare M1 la rând" in d for d in done) and not eng.paper.positions
    assert b._chasing_run("EURUSD", "SELL", now) is None
    # the last bar closed against -> the fill goes through
    rising[-1] = _bar(t0 - 120, 1.0796, 1.0800, 1.0794, 1.0795)
    assert b._fire_armed("EURUSD", [], eng.rt.quotes["EURUSD"], "PAPER", now) is True
    assert eng.paper.positions and not b.armed["EURUSD"]


def test_big_lot_needs_confidence_and_m5_m15_with_the_entry(engine, monkeypatch):
    """Owner 04.11: the bigger volume only on the entries Claude is sure of; the code checks it."""
    eng = _claude_basket(engine, BASKET_SURE_MIN_CONFIDENCE=0.8)
    b = eng.basket
    eng.db.set_setting("basket", {"claude": True, "max_loss_usd": 10, "start_lot": 0.01})
    monkeypatch.setattr(b, "margin_max_lot", lambda q: 0.1)
    monkeypatch.setattr(b, "fast_vote", lambda sym, now: ("BUY", {"M1": 1, "M5": 1}))
    monkeypatch.setattr(b, "mtf_vote", lambda sym, now: ("BUY", {"M1": 1, "M5": 1, "M15": 1, "H1": 1}))
    now = time.time()
    assert b._big_lot_block("EURUSD", "BUY", None, now).startswith("încredere lipsă")
    assert "0.60 sub 0.8" in b._big_lot_block("EURUSD", "BUY", 0.6, now)
    assert "M5" in b._big_lot_block("EURUSD", "SELL", 0.9, now)
    assert b._big_lot_block("EURUSD", "BUY", 0.9, now) is None
    # low confidence -> the start lot; high confidence with the trends aligned -> the requested lot; confidence kept on the intent
    done = b.apply_claude_actions("EURUSD", [{"type": "OPEN", "side": "BUY", "lot": 0.02, "confidence": 0.5, "why": "x"}],
                                  "PAPER", now)
    assert "deschid BUY 0.01" in done[0] and "încredere 0.50 sub 0.8" in done[0]
    for p in list(eng.paper.positions.values()):
        eng.paper.close(p["ticket"], eng.rt.quotes["EURUSD"], "test")
    seen, real_open = [], b._open
    monkeypatch.setattr(b, "_open", lambda *a, **k: (seen.append(a[5]), real_open(*a, **k)))
    done = b.apply_claude_actions("EURUSD", [{"type": "OPEN", "side": "BUY", "lot": 0.02, "confidence": 0.9, "why": "x"}],
                                  "PAPER", now)
    assert done[0] == "deschid BUY 0.02" and "încredere 0.90" in seen[0]  # kept in the entry log for the lot study
    # an M15 against the entry blocks the big lot even at high confidence
    monkeypatch.setattr(b, "mtf_vote", lambda sym, now: (None, {"M1": 1, "M5": 1, "M15": -1, "H1": 1}))
    assert "M15" in b._big_lot_block("EURUSD", "BUY", 0.95, now)


def test_time_stop_closes_a_lone_scalp_position_after_the_minutes(engine, monkeypatch):
    """04.11: the winners come in the first minutes; a position still open after 20 minutes is closed at market."""
    import time as _t
    eng = _claude_basket(engine, BASKET_SCALP_TIME_STOP_MINUTES=20)
    b = eng.basket
    now = _t.time()
    old = _t.strftime("%Y-%m-%dT%H:%M:%SZ", _t.gmtime(now - 21 * 60))
    young = _t.strftime("%Y-%m-%dT%H:%M:%SZ", _t.gmtime(now - 5 * 60))
    pos = [{"ticket": 1, "side": "BUY", "volume": 0.01, "price_open": 1.08, "opened_at_utc": young, "profit": -0.5}]
    assert b._time_stop("EURUSD", pos, -0.5, now) is False
    pos[0]["opened_at_utc"] = old
    closed = []
    monkeypatch.setattr(b, "_close_all", lambda sym, p, reason, total, t: closed.append(reason))
    assert b._time_stop("EURUSD", pos, -0.5, now) is True and closed == ["BASKET_TIME_STOP"]
    b.engine.settings = b.engine.settings.model_copy(update={"BASKET_SCALP_TIME_STOP_MINUTES": 0})
    assert b._time_stop("EURUSD", pos, -0.5, now) is False


def test_entry_waits_while_the_price_is_stretched_beyond_ema50(engine, monkeypatch):
    """04.11: entries >= 2 ATR beyond the EMA50 M1 in their direction won 12 of 17 (-5.57); the fill waits."""
    eng = _claude_basket(engine, BASKET_MAX_STRETCH_EMA50_ATR=2.0, BASKET_ENTRY_NEEDS_PULLBACK=False)
    b = eng.basket
    now = time.time()
    monkeypatch.setattr(b, "atr_m1", lambda sym, now: 0.0010)
    monkeypatch.setattr(b, "fast_vote", lambda sym, now: (None, {"M1": 0, "M5": 0}))
    t0 = int(now // 60 * 60)
    flat = [_bar(t0 - 60 * (80 - i), 1.0770, 1.0771, 1.0769, 1.0770) for i in range(80)]  # EMA50 ~1.0770
    eng.rt.closed_bars = lambda sym, tf, n=None: flat
    eng.rt.bars[("EURUSD", "M1")] = flat
    q = eng.rt.quotes["EURUSD"]  # ask ~1.0800 -> 3 ATR above the EMA50
    assert "întinsă" in b._stretched("EURUSD", "BUY", q, now)
    assert b._stretched("EURUSD", "SELL", q, now) is None
    done = b.apply_claude_actions("EURUSD", [{"type": "OPEN", "side": "BUY", "lot": 0.01, "why": "x"}], "PAPER", now)
    assert "refuzat" in done[0] and "EMA50" in done[0] and not eng.paper.positions
    b.armed["EURUSD"] = [{"side": "BUY", "trigger": "PULLBACK", "price": 1.0805, "expires": now + 300, "lot": 0.01,
                          "armed_at": now - 30, "expires_utc": "", "why": "x"}]
    assert b._fire_armed("EURUSD", [], q, "PAPER", now) is False and b.armed["EURUSD"]
    # the price came back to within 2 ATR of the EMA50 -> the fill goes through
    from tests.conftest import quote
    eng.rt.ingest_quote("EURUSD", quote(bid=1.07850))
    assert b._fire_armed("EURUSD", [], eng.rt.quotes["EURUSD"], "PAPER", now) is True and eng.paper.positions


def test_claude_hedge_is_refused_when_hedging_is_off(engine):
    """04.11 live: 4 hedged groups -21.98 USD vs -16 with the plain SL - no hedge by hand either."""
    import time as _t
    from tests.conftest import quote
    eng = _claude_basket(engine, BASKET_CLAUDE_HEDGE=False)
    eng.paper.open("EURUSD", "BUY", 0.01, 1.07, 1.09, eng.rt.quotes["EURUSD"])
    eng.rt.ingest_quote("EURUSD", quote(bid=1.07980))
    eng.paper.on_quote("EURUSD", eng.rt.quotes["EURUSD"])
    done = eng.basket.apply_claude_actions("EURUSD", [{"type": "OPEN", "side": "SELL", "lot": 0.01, "why": "x"}],
                                           "PAPER", _t.time())
    assert "acoperirea e oprită" in done[0] and len(eng.paper.positions) == 1


def test_entry_waits_until_a_counter_m1_bar_or_a_counter_10_minutes(engine, monkeypatch):
    """04.11: entries straight after a bar in their own direction won 13 of 22; after a counter move 31 of 35."""
    eng = _claude_basket(engine, BASKET_ENTRY_NEEDS_PULLBACK=True, BASKET_MAX_STRETCH_EMA50_ATR=0.0,
                         BASKET_MAX_RUN_BARS=0)
    b = eng.basket
    now = time.time()
    monkeypatch.setattr(b, "atr_m1", lambda sym, now: 0.0010)
    monkeypatch.setattr(b, "fast_vote", lambda sym, now: (None, {"M1": 0, "M5": 0}))
    t0 = int(now // 60 * 60)
    # 12 closed bars, all rising a little: no pullback at all
    up = [_bar(t0 - 60 * (12 - i), 1.0780 + i * 0.0001, 1.0782 + i * 0.0001, 1.0780 + i * 0.0001, 1.0781 + i * 0.0001)
          for i in range(12)]
    eng.rt.closed_bars = lambda sym, tf, n=None: up
    eng.rt.bars[("EURUSD", "M1")] = up
    q = eng.rt.quotes["EURUSD"]
    assert "nicio revenire" in b._no_pullback_yet("EURUSD", "BUY", now)
    assert b._no_pullback_yet("EURUSD", "SELL", now) is None  # for a SELL the rise IS the counter move
    done = b.apply_claude_actions("EURUSD", [{"type": "OPEN", "side": "BUY", "lot": 0.01, "why": "x"}], "PAPER", now)
    assert "refuzat" in done[0] and "revenire" in done[0] and not eng.paper.positions
    b.armed["EURUSD"] = [{"side": "BUY", "trigger": "PULLBACK", "price": 1.0805, "expires": now + 300, "lot": 0.01,
                          "armed_at": now - 30, "expires_utc": "", "why": "x"}]
    assert b._fire_armed("EURUSD", [], q, "PAPER", now) is False and b.armed["EURUSD"]
    # the last bar closed against -> a real pullback -> the fill goes through
    up[-1] = _bar(up[-1]["t"], 1.0792, 1.0792, 1.0788, 1.0789)
    assert b._no_pullback_yet("EURUSD", "BUY", now) is None
    assert b._fire_armed("EURUSD", [], q, "PAPER", now) is True and eng.paper.positions
    b.engine.settings = b.engine.settings.model_copy(update={"BASKET_ENTRY_NEEDS_PULLBACK": False})
    up[-1] = _bar(up[-1]["t"], 1.0791, 1.0793, 1.0791, 1.0792)
    assert b._no_pullback_yet("EURUSD", "BUY", now) is None


def test_armed_pullback_is_dropped_once_the_price_runs_past_its_level(engine, monkeypatch):
    """04.11 12:16: a SELL pullback armed at 4034.9 filled at 4036.1 inside the reversal and hit the SL."""
    eng = _claude_basket(engine, BASKET_PULLBACK_MAX_OVERSHOOT_ATR=0.3, BASKET_ENTRY_NEEDS_PULLBACK=False,
                         BASKET_MAX_STRETCH_EMA50_ATR=0.0, BASKET_MAX_RUN_BARS=0)
    b = eng.basket
    now = time.time()
    monkeypatch.setattr(b, "atr_m1", lambda sym, now: 0.0010)
    monkeypatch.setattr(b, "fast_vote", lambda sym, now: (None, {"M1": 0, "M5": 0}))
    assert b._overshoot("EURUSD", "BUY", 1.0805, 1.0803, now) is None            # 0.2 ATR past: fine
    over = b._overshoot("EURUSD", "BUY", 1.0805, 1.0800, now)   # 0.5 ATR past: the level failed
    assert over and abs(over[0] - 0.0005) < 1e-9 and abs(over[1] - 0.5) < 1e-6
    over = b._overshoot("EURUSD", "SELL", 1.0795, 1.0800, now)
    assert over and abs(over[0] - 0.0005) < 1e-9 and abs(over[1] - 0.5) < 1e-6
    q = eng.rt.quotes["EURUSD"]  # ask ~1.0800
    b.armed["EURUSD"] = [{"side": "BUY", "trigger": "PULLBACK", "price": 1.0806, "expires": now + 300, "lot": 0.01,
                          "armed_at": now - 30, "expires_utc": "", "why": "x"}]
    assert b._fire_armed("EURUSD", [], q, "PAPER", now) is False
    assert b.armed["EURUSD"] == [] and not eng.paper.positions  # dropped, not filled
    b.armed["EURUSD"] = [{"side": "BUY", "trigger": "PULLBACK", "price": 1.0802, "expires": now + 300, "lot": 0.01,
                          "armed_at": now - 30, "expires_utc": "", "why": "x"}]
    assert b._fire_armed("EURUSD", [], q, "PAPER", now) is True and eng.paper.positions


def test_an_arm_the_price_ran_away_from_waits_for_the_m5_close(engine, monkeypatch):
    """06.11 18:45-19:00: 7 Opus calls in 15 minutes re-arming a BUY pullback under a rally, no fill."""
    eng = _claude_basket(engine, BASKET_CLAUDE_FLAT_WAIT_EVERY_BARS=5, BASKET_CLAUDE_FLAT_WAIT_MOVE_ATR=1.0,
                         BASKET_CLAUDE_ARMED_SKIP=True, BASKET_CLAUDE_ARMED_MOVE_ATR=1.5)
    eng.ai.settings = eng.ai.settings.model_copy(update={"CLAUDE_ENABLED": True, "ANTHROPIC_API_KEY": "test-key"})
    monkeypatch.setattr(pattern_context, "build", lambda rt, sym, now: {})
    monkeypatch.setattr(eng.basket, "atr_m1", lambda sym, now: 0.0010)
    monkeypatch.setattr(eng.basket, "fast_vote", lambda sym, now: ("BUY", {"M1": 1, "M5": 1}))
    now = time.time()
    m = int(now // 60) // 5 * 5
    t0 = (m - 3) * 60
    calls = []

    async def fake(payload, model=None):
        calls.append(model)
        return {"status": "DONE", "market_view": "x", "plan": "armez", "confidence": 0.5,
                "actions": [{"type": "ARM", "side": "BUY", "trigger": "PULLBACK", "price": 1.0780, "minutes": 10,
                             "lot": 0.01, "why": "x"}]}
    eng.ai.decide_basket = fake
    eng.rt.closed_bars = lambda sym, tf, n=None: [_bar(t0 - 60, 1.08, 1.08, 1.08, 1.08)]
    eng.basket.tick(t0 + 5)
    assert len(calls) == 1 and eng.basket.armed["EURUSD"]
    # the price runs 2 ATR away from where Claude decided (and 4 ATR from the arm): not an M5 close -> no call
    eng.rt.ingest_quote("EURUSD", quote(bid=1.08200))
    eng.rt.closed_bars = lambda sym, tf, n=None: [_bar(t0, 1.082, 1.082, 1.082, 1.082)]
    eng.basket._claude_task = None
    eng.basket.tick(t0 + 65)
    assert len(calls) == 1 and "fugit" in eng.basket.state["EURUSD"]["analysis"]
    eng.rt.closed_bars = lambda sym, tf, n=None: [_bar(t0 + 60, 1.082, 1.082, 1.082, 1.082)]
    eng.basket._claude_task = None
    eng.basket.tick(t0 + 125)
    assert len(calls) == 1
    # the M5 close -> call
    eng.rt.closed_bars = lambda sym, tf, n=None: [_bar(t0 + 120, 1.082, 1.082, 1.082, 1.082)]
    eng.basket._claude_task = None
    eng.basket.tick(t0 + 185)
    assert len(calls) == 2


def test_m15_mode_calls_claude_only_on_a_decision_bar_close_with_a_mechanical_setup(engine, monkeypatch):
    """09.11: on M15 the backtest edge is in the setup (PF 1.19); Claude is the gate, called once per M15 close that has it."""
    eng = _claude_basket(engine, BASKET_DECISION_TF="M15", BASKET_TREND_TF="H1", BASKET_CLAUDE_FLAT_WAIT_EVERY_BARS=5)
    b = eng.basket
    now = time.time()
    st = b.state.setdefault("EURUSD", {})
    q = eng.rt.quotes["EURUSD"]
    monkeypatch.setattr(b, "_setup_side", lambda sym, q, now: "BUY")
    t_close = int(now // 900) * 900 - 60          # the M1 bar whose close completes an M15 bar
    t_mid = t_close - 5 * 60                      # an M1 bar in the middle of the M15 bar
    note = b._skip_bar("EURUSD", [], False, q, st, t_mid)
    assert note and "M15" in note and "nu e chemat" in note
    assert b._skip_bar("EURUSD", [], False, q, st, t_close) is None and st["setup_side"] == "BUY"
    # the decision bar closed but there is no setup -> no call, any stale arm is dropped
    monkeypatch.setattr(b, "_setup_side", lambda sym, q, now: None)
    b.armed["EURUSD"] = [{"side": "BUY", "trigger": "PULLBACK", "price": 1.07, "expires": now + 900, "lot": 0.01,
                          "armed_at": now, "expires_utc": "", "why": "x"}]
    note = b._skip_bar("EURUSD", [], False, q, st, t_close)
    assert note and "fără setup" in note and b.armed["EURUSD"] == []
    # with an open position Claude is consulted only at the M15 close
    eng.paper.open("EURUSD", "BUY", 0.01, 1.07, 1.09, q)
    pos = b.positions("EURUSD")
    assert "închiderea barei M15" in (b._skip_bar("EURUSD", pos, False, q, st, t_mid) or "")
    assert b._skip_bar("EURUSD", pos, False, q, st, t_close) is None
    # every call on a higher timeframe is a decision -> the main model
    assert b._route_model("EURUSD", [], False, False, now)[0] is None
    assert b._route_model("EURUSD", pos, False, False, now + 3600)[0] is None


def test_m15_mode_reads_guards_atr_and_trend_from_the_configured_timeframes(engine, monkeypatch):
    eng = _claude_basket(engine, BASKET_DECISION_TF="M15", BASKET_TREND_TF="H1", BASKET_PULLBACK_LOOKBACK_BARS=3,
                         BASKET_MIN_M5_GAP_ATR=0.4)
    b = eng.basket
    now = time.time()
    t0 = int(now // 900) * 900
    seen = []

    def closed(sym, tf, n=None):
        seen.append(tf)
        if tf == "M15":   # 80 flat bars then 3 rising ones: ATR from M15, last bars up
            flat = [_bar(t0 - 900 * (83 - i), 1.0800, 1.0810, 1.0790, 1.0800) for i in range(80)]
            return flat + [_bar(t0 - 900 * (3 - i), 1.0800 + i * 0.001, 1.0820 + i * 0.001, 1.0790 + i * 0.001, 1.0810 + i * 0.001) for i in range(3)]
        if tf == "H1":    # rising H1 closes: EMA9 well above EMA21
            return [_bar(t0 - 3600 * (40 - i), 1.0 + i * 0.002, 1.0 + i * 0.002, 1.0 + i * 0.002, 1.0 + i * 0.002) for i in range(40)]
        return [_bar(t0 - 60 * (40 - i), 1.08, 1.08, 1.08, 1.08) for i in range(40)]  # M1/M5: irrelevant, flat
    eng.rt.closed_bars = closed
    atr = b.atr_m1("EURUSD", now)
    assert atr and abs(atr - 0.0020) < 0.0005          # the M15 range (0.0020), not the flat M1 bars
    gap = b.m5_gap_atr("EURUSD", now)
    assert gap is not None and gap > 0.4 and "H1" in seen  # the trend comes from H1
    assert b._against_m5("EURUSD", "BUY", now) is None and "contra trendului H1" in b._against_m5("EURUSD", "SELL", now)
    # no pullback yet: the last 3 M15 bars rose -> a BUY waits; the message names the look-back on the decision bars
    assert "nicio revenire" in b._no_pullback_yet("EURUSD", "BUY", now)
    assert b._no_pullback_yet("EURUSD", "SELL", now) is None
    # the trend_now payload carries the timeframes and the mechanical setup field
    tn = b._trend_now("EURUSD", eng.rt.quotes["EURUSD"], now)
    assert tn["decision_tf"] == "M15" and tn["trend_tf"] == "H1" and "mechanical_setup" in tn and "m15_ema20" in tn


def test_flat_wait_calls_claude_only_on_m5_close_or_a_real_move(engine, monkeypatch):
    """API cost (04.11): after "wait" with nothing armed, the bars that changed nothing do not call Claude."""
    eng = _claude_basket(engine, BASKET_CLAUDE_FLAT_WAIT_EVERY_BARS=5, BASKET_CLAUDE_FLAT_WAIT_MOVE_ATR=1.0)
    eng.ai.settings = eng.ai.settings.model_copy(update={"CLAUDE_ENABLED": True, "ANTHROPIC_API_KEY": "test-key"})
    monkeypatch.setattr(pattern_context, "build", lambda rt, sym, now: {})
    monkeypatch.setattr(eng.basket, "atr_m1", lambda sym, now: 0.0010)
    monkeypatch.setattr(eng.basket, "fast_vote", lambda sym, now: ("BUY", {"M1": 1, "M5": 1}))
    now = time.time()
    m = int(now // 60) // 5 * 5          # a minute that is a multiple of 5 (an M5 close)
    t0 = (m - 3) * 60                     # closed-bar times: t0 -> minute m-3 ... the bar ending at minute m is an M5 close
    calls = []

    async def fake(payload, model=None):
        calls.append(model)
        return {"status": "DONE", "actions": [], "market_view": "x", "plan": "aștept", "confidence": 0.5}
    eng.ai.decide_basket = fake
    eng.rt.closed_bars = lambda sym, tf, n=None: [_bar(t0 - 60, 1.08, 1.08, 1.08, 1.08)]
    eng.basket.tick(t0 + 5)
    assert len(calls) == 1 and eng.basket.state["EURUSD"]["waited_px"]
    # next bar (minute m-2 closes): nothing changed -> no call
    eng.rt.closed_bars = lambda sym, tf, n=None: [_bar(t0, 1.08, 1.08, 1.08, 1.08)]
    eng.basket._claude_task = None
    eng.basket.tick(t0 + 65)
    assert len(calls) == 1 and "aștept" in eng.basket.state["EURUSD"]["analysis"]
    # bar m-1 closes, still quiet -> no call
    eng.rt.closed_bars = lambda sym, tf, n=None: [_bar(t0 + 60, 1.08, 1.08, 1.08, 1.08)]
    eng.basket._claude_task = None
    eng.basket.tick(t0 + 125)
    assert len(calls) == 1
    # the bar ending at minute m closes = M5 close -> call
    eng.rt.closed_bars = lambda sym, tf, n=None: [_bar(t0 + 120, 1.08, 1.08, 1.08, 1.08)]
    eng.basket._claude_task = None
    eng.basket.tick(t0 + 185)
    assert len(calls) == 2
    # next bar: the price moved 1.2 x ATR from where Claude waited -> call even though it is not an M5 close
    eng.rt.ingest_quote("EURUSD", quote(bid=1.08120))
    eng.rt.closed_bars = lambda sym, tf, n=None: [_bar(t0 + 180, 1.0812, 1.0812, 1.0812, 1.0812)]
    eng.basket._claude_task = None
    eng.basket.tick(t0 + 245)
    assert len(calls) == 3


def test_investigation_waits_until_a_close_could_be_executed(engine, monkeypatch):
    """04.11: 3-4 paid "investigation" calls per losing trade while every CLOSE would be refused (young position,
    hedging off) - they are skipped until the position is old enough."""
    import time as _t
    eng = _claude_basket(engine, BASKET_CLAUDE_HEDGE=False, BASKET_CLAUDE_LOSS_CLOSE_MIN_MINUTES=10,
                         BASKET_INVESTIGATE_ONLY_WHEN_ACTIONABLE=True)
    eng.ai.settings = eng.ai.settings.model_copy(update={"CLAUDE_ENABLED": True, "ANTHROPIC_API_KEY": "test-key"})
    monkeypatch.setattr(pattern_context, "build", lambda rt, sym, now: {})
    monkeypatch.setattr(eng.basket, "atr_m1", lambda sym, now: 0.0010)
    monkeypatch.setattr(eng.basket, "fast_vote", lambda sym, now: ("BUY", {"M1": 1, "M5": 1}))
    now = _t.time()
    t0 = int(now // 60 * 60)
    calls = []

    async def fake(payload, model=None):
        calls.append(bool(payload.get("investigation_now")))
        return {"status": "DONE", "actions": [], "market_view": "x", "plan": "țin", "confidence": 0.5}
    eng.ai.decide_basket = fake
    eng.paper.open("EURUSD", "BUY", 0.01, 1.07, 1.09, eng.rt.quotes["EURUSD"])
    p = next(iter(eng.paper.positions.values()))
    p["opened_ts"] = now - 3 * 60                                                        # 3 minutes old
    eng.rt.ingest_quote("EURUSD", quote(bid=1.07970))                                   # -0.3 USD: past the 0.2 step
    eng.paper.on_quote("EURUSD", eng.rt.quotes["EURUSD"])
    eng.rt.closed_bars = lambda sym, tf, n=None: [_bar(t0 - 60, 1.08, 1.08, 1.08, 1.08)]
    eng.basket.tick(now)
    assert calls == [False]   # the routine first-bar call, no investigation
    # the position is now 12 minutes old and sinks another step -> the investigation call goes out
    p["opened_ts"] = now - 12 * 60
    eng.rt.ingest_quote("EURUSD", quote(bid=1.07900))   # -1.1 USD: several steps further
    eng.paper.on_quote("EURUSD", eng.rt.quotes["EURUSD"])
    eng.basket._claude_task = None
    eng.basket.tick(now + 1)
    assert calls[-1] is True


def test_forexfactory_feed_parses_into_calendar_items():
    from app.news_feed import parse_events
    raw = [{"title": "Non-Farm Employment Change", "country": "USD", "date": "2026-11-06T08:30:00-05:00", "impact": "High"},
           {"title": "ISM Services PMI", "country": "USD", "date": "2026-11-04T10:00:00-05:00", "impact": "Medium"},
           {"title": "Daylight Saving Time Shift", "country": "USD", "date": "2026-11-01T02:00:00-05:00", "impact": "Holiday"},
           {"title": "bad", "country": "USD", "date": "not-a-date", "impact": "High"}, "junk"]
    items = parse_events(raw)
    assert [(x["ccy"], x["imp"], x["name"]) for x in items] == [("USD", 2, "ISM Services PMI"),
                                                                 ("USD", 3, "Non-Farm Employment Change")]
    assert items[0]["t"] == 1793804400  # 2026-11-04 15:00:00 UTC


def test_news_blackout_uses_the_feed_when_the_ea_calendar_is_missing(engine, monkeypatch):
    """04.11: Exness EA has no calendar, two SELLs straddled the 13:15 ADP release; the fallback feed now blocks them."""
    eng = _claude_basket(engine)
    eng.settings = eng.settings.model_copy(update={"DATA_SOURCE": "mt5_bridge"})
    eng.rt.risk.update(news_blackout_minutes=10, news_min_importance=2)
    eng._warmup_done = True
    now = time.time()
    eng.rt.calendar, eng.rt.calendar_ts = [], None
    assert eng.news_check("XAUUSDm", now)[1].startswith("calendar economic indisponibil")
    monkeypatch.setattr(eng.news_feed, "poll", lambda now: None)
    monkeypatch.setattr(eng.news_feed, "snapshot",
                        lambda: ([{"t": int(now + 4 * 60), "ccy": "USD", "imp": 2, "name": "ADP Non-Farm Employment Change"}], now - 60))
    eng._check_calendar(now)
    assert eng.calendar_fresh(now) and eng.rt.bridge.get("calendar_source") == "forexfactory"
    block, _ = eng.news_check("XAUUSDm", now)
    assert block and "ADP" in block and "±10" in block
    assert eng.news_check("XAUUSDm", now + 20 * 60)[0] is None   # 16 min after: free again
    assert eng.news_check("EURJPY", now)[0] is None               # not a USD pair


def test_no_new_entries_in_the_blocked_utc_hours(engine, monkeypatch):
    """04.11: the London/NY overlap (12-16 UTC) lost 16 of 22 real trades; no entries and no flat Claude calls there."""
    import calendar as _cal
    eng = _claude_basket(engine, BASKET_NO_ENTRY_UTC_HOURS="12,13,14,15")
    b = eng.basket
    inside = _cal.timegm((2026, 11, 4, 13, 20, 0, 0, 0, 0))
    outside = _cal.timegm((2026, 11, 4, 16, 0, 30, 0, 0, 0))
    assert "12:00–16:00 UTC" in b.hour_block_note(inside) and b.hour_block_note(outside) is None
    done = b.apply_claude_actions("EURUSD", [{"type": "OPEN", "side": "BUY", "lot": 0.01, "why": "x"}], "PAPER", inside)
    assert "refuzat" in done[0] and "12:00–16:00" in done[0] and not eng.paper.positions
    done = b.apply_claude_actions("EURUSD", [{"type": "ARM", "side": "BUY", "trigger": "PULLBACK", "price": 1.0790,
                                              "valid_minutes": 5, "why": "x"}], "PAPER", inside)
    assert "refuzat" in done[0] and not b.armed.get("EURUSD")
    q = eng.rt.quotes["EURUSD"]
    assert "fără intrări noi" in (b._skip_bar("EURUSD", [], False, q, {}, inside - 60) or "")
    b.engine.settings = b.engine.settings.model_copy(update={"BASKET_NO_ENTRY_UTC_HOURS": ""})
    assert b.hour_block_note(inside) is None


def test_same_side_reentry_waits_for_a_new_level(engine, monkeypatch):
    """04.11 16:14: BUY 4045.5 three minutes after a +1.99 win from 4045.4 hit the SL; re-entries at the same level
    won 24 of 36 (-1.1 USD), entries at a new level 19 of 24 (+21.2)."""
    import time as _t
    eng = _claude_basket(engine, BASKET_REENTRY_MIN_ATR=0.5, BASKET_REENTRY_MINUTES=15)
    b = eng.basket
    now = _t.time()
    monkeypatch.setattr(b, "atr_m1", lambda sym, now: 0.0010)
    iso = lambda t: _t.strftime("%Y-%m-%dT%H:%M:%SZ", _t.gmtime(t))  # noqa: E731
    eng.db.execute("INSERT INTO trades (deal_key, ticket, symbol, side, volume, price_open, price_close, profit, "
                   "opened_at_utc, closed_at_utc, close_reason, mode) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                   ("t-reentry", 1, "EURUSD", "BUY", 0.01, 1.0800, 1.0820, 2.0, iso(now - 600), iso(now - 180), "TP", "PAPER"))
    assert "același nivel" in b._same_level_reentry("EURUSD", "BUY", 1.0803, now)      # 0.3 ATR away, 3 min later
    assert b._same_level_reentry("EURUSD", "BUY", 1.0808, now) is None                # 0.8 ATR away: a new level
    assert b._same_level_reentry("EURUSD", "SELL", 1.0803, now) is None               # other side: no previous SELL
    assert b._same_level_reentry("EURUSD", "BUY", 1.0803, now + 20 * 60) is None      # 23 min later: free again
    done = b.apply_claude_actions("EURUSD", [{"type": "OPEN", "side": "BUY", "lot": 0.01, "why": "x"}], "PAPER", now)
    assert "refuzat" in done[0] and "același nivel" in done[0] and not eng.paper.positions


def test_entry_is_dropped_when_claude_s_own_gates_show_a_cross(engine, monkeypatch):
    """Prompt-design research 04.11: the model echoes numbered entry gates and the code holds it to them."""
    import asyncio
    eng = _claude_basket(engine)
    b = eng.basket
    monkeypatch.setattr(b, "atr_m1", lambda sym, now: 0.0010)

    async def fake(payload, model=None):
        return {"status": "DONE", "gates": "G1✓ G2✗ G3✓ G4✓ G5✓ G6✓", "confidence": 0.7, "market_view": "x", "plan": "y",
                "actions": [{"type": "ARM", "side": "BUY", "trigger": "PULLBACK", "price": 1.0790, "valid_minutes": 5,
                             "lot": 0.01, "why": "x", "ticket": None}]}
    eng.ai.decide_basket = fake
    rec = asyncio.run(b.claude_decide_and_apply("EURUSD", {"time_utc": "t", "positions": []}, "PAPER"))
    assert any("porțile" in d and "✗" in d for d in rec["done"]) and not b.armed.get("EURUSD")

    async def fake_ok(payload, model=None):
        return {"status": "DONE", "gates": "G1✓ G2✓ G3✓ G4✓ G5✓ G6✓", "confidence": 0.7, "market_view": "x", "plan": "y",
                "actions": [{"type": "ARM", "side": "BUY", "trigger": "PULLBACK", "price": 1.0790, "valid_minutes": 5,
                             "lot": 0.01, "why": "x", "ticket": None}]}
    eng.ai.decide_basket = fake_ok
    rec = asyncio.run(b.claude_decide_and_apply("EURUSD", {"time_utc": "t", "positions": []}, "PAPER"))
    assert b.armed.get("EURUSD") and not any("porțile" in d for d in rec["done"])


def test_no_new_entries_while_the_m1_atr_is_extreme(engine, monkeypatch):
    """05.11 01:26: a BUY at ATR 6.2 USD was stopped out in 60 seconds; the fixed 4 USD stop is inside one bar."""
    eng = _claude_basket(engine, BASKET_MAX_ATR_M1=3.5, BASKET_NO_ENTRY_UTC_HOURS="")
    b = eng.basket
    now = time.time()
    monkeypatch.setattr(b, "atr_m1", lambda sym, now: 6.2)
    assert "volatilitate extremă" in b.atr_block_note("EURUSD", now)
    done = b.apply_claude_actions("EURUSD", [{"type": "OPEN", "side": "BUY", "lot": 0.01, "why": "x"}], "PAPER", now)
    assert "refuzat" in done[0] and "ATR M1 6.20" in done[0] and not eng.paper.positions
    done = b.apply_claude_actions("EURUSD", [{"type": "ARM", "side": "BUY", "trigger": "PULLBACK", "price": 1.0790,
                                              "valid_minutes": 5, "why": "x"}], "PAPER", now)
    assert "refuzat" in done[0] and not b.armed.get("EURUSD")
    assert "volatilitate" in (b._skip_bar("EURUSD", [], False, eng.rt.quotes["EURUSD"], {}, now - 60) or "")
    monkeypatch.setattr(b, "atr_m1", lambda sym, now: 2.4)
    assert b.atr_block_note("EURUSD", now) is None


def test_scalp_exits_widen_with_the_m1_atr(engine, monkeypatch):
    """05.11: once ATR passes 2 USD the fixed 4 USD stop sits inside one bar; TP/SL scale to 1x / 2x ATR."""
    eng = _claude_basket(engine, BASKET_SCALP_TP_PRICE=0.0015, BASKET_SCALP_SL_PRICE=0.0040,
                         BASKET_SCALP_TP_ATR_MULT=1.0, BASKET_SCALP_SL_ATR_MULT=2.0)
    b = eng.basket
    now = time.time()
    monkeypatch.setattr(b, "atr_m1", lambda sym, now: 0.0010)          # calm: the fixed exits
    assert b.scalp_exits("EURUSD", now) == (0.0015, 0.0040)
    monkeypatch.setattr(b, "atr_m1", lambda sym, now: 0.0030)          # 3 "USD" of ATR: TP 3, SL 6
    tp, sl = b.scalp_exits("EURUSD", now)
    assert abs(tp - 0.0030) < 1e-9 and abs(sl - 0.0060) < 1e-9
    monkeypatch.setattr(eng.executor, "submit_modify", lambda *a, **k: None)
    done = b.apply_claude_actions("EURUSD", [{"type": "OPEN", "side": "BUY", "lot": 0.01, "why": "x"}], "PAPER", now)
    assert "deschid BUY" in done[0]
    p = next(iter(eng.paper.positions.values()))
    assert abs(p["tp"] - (p["price_open"] + 0.0030)) < 2e-5 and abs(p["sl"] - (p["price_open"] - 0.0060)) < 2e-5
    b.engine.settings = b.engine.settings.model_copy(update={"BASKET_SCALP_TP_ATR_MULT": 0.0, "BASKET_SCALP_SL_ATR_MULT": 0.0})
    assert b.scalp_exits("EURUSD", now) == (0.0015, 0.0040)


def test_owner_start_lot_is_honoured_above_the_risk_cap(engine, monkeypatch):
    """05.11: the dashboard lot is the owner's decision; the 5 % risk cap trims only what Claude adds above it."""
    eng = _claude_basket(engine, BASKET_MAX_RISK_PCT_PER_ENTRY=5.0, BASKET_SCALP_SL_PRICE=0.0040)
    b = eng.basket
    eng.db.set_setting("basket", {"claude": True, "max_loss_usd": 10, "start_lot": 0.03})
    monkeypatch.setattr(b, "margin_max_lot", lambda q: 0.1)
    monkeypatch.setattr(b, "balance", lambda: 200.0)
    q = eng.rt.quotes["EURUSD"]
    rx = b.risk_max_lot(q)                      # 0.004 price x 100000 x 0.01 = 4 "USD" per 0.01 -> 5 % of 200 = 0.02
    assert rx == 0.02
    assert b._entry_max_lot(b.cfg(), q) == 0.03  # the owner's 0.03 is allowed although the cap alone says 0.02
    done = b.apply_claude_actions("EURUSD", [{"type": "OPEN", "side": "BUY", "lot": 0.01, "why": "x"}], "PAPER", time.time())
    assert done[0] == "deschid BUY 0.03"
    for p in list(eng.paper.positions.values()):
        eng.paper.close(p["ticket"], q, "test")
    done = b.apply_claude_actions("EURUSD", [{"type": "OPEN", "side": "BUY", "lot": 0.08, "confidence": 0.95, "why": "x"}],
                                  "PAPER", time.time())
    assert "deschid BUY 0.03" in done[0] and "lot redus de la 0.08 la 0.03" in done[0]


def test_entry_waits_when_the_pullback_went_too_deep_or_the_price_sits_on_ema50(engine, monkeypatch):
    """05.11: fills more than 0.7 ATR beyond the EMA20 against the trade, or within 0.5 ATR of the EMA50, won 27 of 47
    (-21.4 USD); the rest 75 of 105 (+64.1)."""
    eng = _claude_basket(engine, BASKET_PULLBACK_MAX_DEPTH_ATR=0.7, BASKET_MIN_DIST_EMA50_ATR=0.5,
                         BASKET_ENTRY_NEEDS_PULLBACK=False, BASKET_MAX_STRETCH_EMA50_ATR=0.0, BASKET_MAX_RUN_BARS=0)
    b = eng.basket
    now = time.time()
    monkeypatch.setattr(b, "atr_m1", lambda sym, now: 0.0010)
    monkeypatch.setattr(b, "fast_vote", lambda sym, now: (None, {"M1": 0, "M5": 0}))
    t0 = int(now // 60 * 60)
    from tests.conftest import quote
    # 80 flat bars at 1.0810: EMA20 = EMA50 = 1.0810
    flat = [_bar(t0 - 60 * (80 - i), 1.0810, 1.0811, 1.0809, 1.0810) for i in range(80)]
    eng.rt.closed_bars = lambda sym, tf, n=None: flat
    eng.rt.bars[("EURUSD", "M1")] = flat
    eng.rt.ingest_quote("EURUSD", quote(bid=1.07990))             # ask ~1.0800: 1 ATR below the EMA20 -> too deep
    assert "prea adâncă" in b._pullback_exhausted("EURUSD", "BUY", eng.rt.quotes["EURUSD"], now)
    eng.rt.ingest_quote("EURUSD", quote(bid=1.08090))             # ask ~1.0810: on the EMA50 -> consumed
    assert "EMA50" in b._pullback_exhausted("EURUSD", "BUY", eng.rt.quotes["EURUSD"], now)
    done = b.apply_claude_actions("EURUSD", [{"type": "OPEN", "side": "BUY", "lot": 0.01, "why": "x"}], "PAPER", now)
    assert "refuzat" in done[0] and not eng.paper.positions
    # EMA20 above EMA50 (rising bars), price between them + 0.6 ATR above the EMA50 -> allowed
    rising = [_bar(t0 - 60 * (80 - i), 1.0790 + i * 0.00005, 1.0791 + i * 0.00005, 1.0789 + i * 0.00005, 1.0790 + i * 0.00005)
              for i in range(80)]
    eng.rt.closed_bars = lambda sym, tf, n=None: rising
    import app.indicators as ind
    e50 = ind.last(ind.ema([x["c"] for x in rising], 50))
    eng.rt.ingest_quote("EURUSD", quote(bid=round(e50 + 0.0006 - 0.0001, 5)))   # ask = EMA50 + 0.6 ATR
    assert b._pullback_exhausted("EURUSD", "BUY", eng.rt.quotes["EURUSD"], now) is None
    b.engine.settings = b.engine.settings.model_copy(update={"BASKET_PULLBACK_MAX_DEPTH_ATR": 0.0, "BASKET_MIN_DIST_EMA50_ATR": 0.0})
    eng.rt.closed_bars = lambda sym, tf, n=None: flat
    eng.rt.ingest_quote("EURUSD", quote(bid=1.08090))
    assert b._pullback_exhausted("EURUSD", "BUY", eng.rt.quotes["EURUSD"], now) is None
