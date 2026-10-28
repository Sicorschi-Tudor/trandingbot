"""Stage A: stale signals on arm, restart warm-up, news blackout, loss brakes, forced closes on stale ticks."""
import asyncio
import time

import pytest

from app import risk as risk_mod
from app import strategy as strategy_mod
from app.config import iso
from app.position_manager import FAIL_MAX_ATTEMPTS, plan
from tests.conftest import ADMIN, make_bars, quote, zigzag
from tests.test_engine import analysis_stub


def ready(engine):
    """Skip the restart warm-up."""
    engine.rt.started_ts -= 60
    engine.rt.bridge["sync_count"] = 1


def up_data():
    sig = make_bars(zigzag(150, 0.0003, 0.0002, end_up=True), 300)
    htf = make_bars(zigzag(80, 0.0030, 0.0020, end_up=True), 3600, wick=0.0005)
    return sig, htf


def load_uptrend(engine, symbol="EURUSD"):
    sig, htf = up_data()
    engine.rt.ingest_history(symbol, "M5", sig)
    engine.rt.ingest_history(symbol, "H1", htf)
    engine.rt.ingest_quote(symbol, quote(bid=sig[-1]["c"]))


def fresh_calendar(engine, events):
    engine.rt.calendar = events
    engine.rt.calendar_ts = time.time()


def add_trade(engine, ticket, net, closed_ts, mode="PAPER", symbol="EURUSD"):
    engine._insert_trade(f"T:{ticket}:{closed_ts}", {
        "ticket": ticket, "symbol": symbol, "side": "BUY", "volume": 0.1, "price_open": 1.08, "price_close": 1.08,
        "profit": net, "commission": 0.0, "swap": 0.0, "opened_at_utc": iso(closed_ts - 600),
        "closed_at_utc": iso(closed_ts), "close_reason": "SL", "mode": mode, "signal_id": None})


# ---- 1. stale signal on arm --------------------------------------------------
def test_signal_created_disarmed_is_not_executed_after_arming(engine):
    engine.rt.ingest_quote("EURUSD", quote(bid=1.08000))
    sig = engine.create_signal(analysis_stub())
    assert sig["created_mode"] == "PAPER" and sig["created_armed"] is False
    engine.set_execution(True)
    engine.try_execute(sig)
    s = engine.get_signal(sig["id"])
    assert s["status"] == "NO_TRADE" and "înainte de armare" in s["status_reason"]
    assert not engine.paper.positions


def test_signal_older_than_arm_or_other_mode_refused(engine):
    engine.rt.ingest_quote("EURUSD", quote(bid=1.08000))
    engine.set_execution(True)
    sig = engine.create_signal(analysis_stub())
    sig["created_ts"] -= 5
    # disarm + re-arm after the signal was created
    engine.set_execution(False)
    engine.set_execution(True)
    assert engine.rt.trading["armed_ts"] > sig["created_ts"]
    engine.try_execute(sig)
    assert engine.get_signal(sig["id"])["status"] == "NO_TRADE"

    sig2 = engine.create_signal(analysis_stub(bar_time=1730000300))
    sig2["created_mode"] = "DEMO"  # created in another mode
    engine.try_execute(sig2)
    assert engine.get_signal(sig2["id"])["status"] == "NO_TRADE"

    sig3 = engine.create_signal(analysis_stub(bar_time=1730000600))
    engine.try_execute(sig3)
    assert engine.get_signal(sig3["id"])["status"] == "EXECUTED"


def test_mode_change_sets_armed_ts(engine):
    t0 = time.time()
    engine.set_mode("SIGNAL_ONLY")
    assert engine.rt.trading["armed_ts"] >= t0 and engine.rt.trading["execution_enabled"] is False


# ---- 2. warm-up + mode-independent checks ----------------------------------------
def test_warmup_blocks_signals_until_ready(engine, monkeypatch):
    load_uptrend(engine)
    assert engine.warmup_reason("EURUSD") is not None
    a = engine.analyze_symbol("EURUSD", allow_signal=True)
    assert a["direction"] == "BUY", a["reasons"]
    assert any("Încălzire" in r for r in a["reasons"])
    assert engine.list_signals() == []

    engine.rt.started_ts -= 60
    assert "sincronizare" in engine.warmup_reason("EURUSD")   # still no bridge sync
    engine.rt.bridge["sync_count"] = 1
    engine.rt.hist_force[("EURUSD", "M5")] = {"since": time.time(), "sent": None}
    assert "istoricului" in engine.warmup_reason("EURUSD")
    assert engine.warmup_reason("GBPUSD") is None
    engine.rt.hist_force.clear()
    logs = engine.db.query("SELECT message FROM system_events WHERE message LIKE 'Încălzire după pornire%'")
    assert len(logs) == 1
    engine.analyze_symbol("EURUSD", allow_signal=True)
    assert len(engine.list_signals()) == 1
    engine.warmup_reason("")
    assert len(engine.db.query("SELECT message FROM system_events WHERE message LIKE 'Încălzire după pornire%'")) == 1


def test_analysis_sees_positions_of_all_modes(engine, monkeypatch):
    ready(engine)
    seen = {}

    def fake_analyze(symbol, sig_bars, htf_bars, q, cfg, risk, ctx):
        seen["ctx"] = ctx
        return {"symbol": symbol, "direction": "HOLD", "reasons": [], "time_utc": iso(time.time())}

    monkeypatch.setattr(strategy_mod, "analyze", fake_analyze)
    q = quote(bid=1.08)
    engine.paper.open("EURUSD", "BUY", 0.1, 1.079, 1.082, q)
    engine.rt.mt5_positions = [{"ticket": 5001, "symbol": "GBPUSD", "type": 1, "volume": 0.01, "price_open": 1.30,
                                "sl": 1.31, "tp": 1.29, "magic": engine.settings.MT5_MAGIC, "time": int(time.time())}]
    for mode in ("PAPER", "DEMO", "SIGNAL_ONLY"):
        engine.rt.trading["mode"] = mode
        engine.analyze_symbol("EURUSD", allow_signal=False)
        ctx = seen["ctx"]
        assert ctx.has_bot_position is True, mode
        assert ("EURUSD", "BUY") in ctx.open_exposures and ("GBPUSD", "SELL") in ctx.open_exposures, mode


# ---- 3. news blackout -----------------------------------------------------------
def test_news_blocker_unit():
    now = time.time()
    evs = [{"t": int(now + 600), "ccy": "USD", "imp": 3, "name": "FOMC"},
           {"t": int(now + 300), "ccy": "JPY", "imp": 3, "name": "BoJ"},
           {"t": int(now + 120), "ccy": "EUR", "imp": 2, "name": "PMI"}]
    r = risk_mod.news_blocker("EURUSD", evs, now, 30, 3)
    assert r and "FOMC" in r and "USD" in r and "10 min" in r
    assert risk_mod.news_blocker("EURGBP", evs, now, 30, 3) is None
    assert "PMI" in risk_mod.news_blocker("EURGBP", evs, now, 30, 2)
    assert risk_mod.news_blocker("EURUSD", evs, now, 5, 3) is None
    assert risk_mod.news_blocker("EURUSD", evs, now, 0, 3) is None  # disabled


def test_news_blackout_blocks_signal_before_claude(engine, monkeypatch):
    ready(engine)
    load_uptrend(engine)
    calls = []

    async def fake_review(signal, context=None):
        calls.append(signal)
        return {"status": "DONE", "verdict": "OK", "explanation": "", "risks": [], "model": "m"}

    monkeypatch.setattr(engine.ai, "review", fake_review)
    fresh_calendar(engine, [{"t": int(time.time() + 900), "ccy": "USD", "imp": 3, "name": "FOMC"}])
    a = engine.analyze_symbol("EURUSD", allow_signal=True)
    assert a["direction"] == "HOLD"
    assert any("FOMC" in r and "USD" in r for r in a["reasons"])
    assert engine.list_signals() == [] and calls == []

    rc = engine.risk_check("EURUSD", "BUY", 1.0801, 1.0795, 1.0811)
    chk = {c["name"]: c for c in rc["checks"]}
    assert chk["news_blackout_ok"]["ok"] is False and "FOMC" in chk["news_blackout_ok"]["detail"]

    st = engine.status()["news"]
    assert st["ok"] is True and st["next_high"]["name"] == "FOMC" and st["events_next_24h"] == 1
    assert st["blocked"] and st["blocked"][0]["symbol"] == "EURUSD"


def test_stale_calendar_does_not_block_but_warns_once(engine):
    ready(engine)
    engine.rt.calendar = [{"t": int(time.time() + 600), "ccy": "USD", "imp": 3, "name": "FOMC"}]
    engine.rt.calendar_ts = time.time() - 3600
    block, note = engine.news_check("EURUSD")
    assert block is None and "indisponibil" in note
    rc = engine.risk_check("EURUSD", "BUY", 1.0801, 1.0795, 1.0811)
    assert next(c for c in rc["checks"] if c["name"] == "news_blackout_ok")["ok"] is True
    engine.warmup_reason("")
    engine._check_calendar(time.time())
    engine._check_calendar(time.time() + 10)
    rows = engine.db.query("SELECT level FROM system_events WHERE message LIKE 'Calendar economic indisponibil%'")
    assert [r["level"] for r in rows] == ["WARN"]
    assert engine.status()["news"]["ok"] is False

    engine.rt.calendar_ts = None   # never received (EA v1.00)
    assert engine.news_check("EURUSD")[0] is None


def test_news_be_moves_profitable_position_to_break_even():
    pos = {"side": "BUY", "price_open": 1.0800, "sl": 1.0790, "tp": 1.0830, "opened_ts": time.time()}
    ev = {"t": time.time() + 120, "ccy": "USD", "imp": 3, "name": "NFP"}
    cfg = {"break_even_at_r": 1.0}
    acts = plan(pos, quote(bid=1.0804), cfg, time.time(), True, news_event=ev)   # +0.4R
    assert acts == [{"type": "MODIFY", "sl": 1.08002, "tp": 1.083, "reason": "NEWS_BE"}]
    assert plan(pos, quote(bid=1.0802), cfg, time.time(), True, news_event=ev) == []  # +0.2R: untouched
    assert plan(pos, quote(bid=1.0804), cfg, time.time(), True) == []                  # no event


def test_news_be_via_position_manager(engine):
    ready(engine)
    engine.rt.ingest_quote("EURUSD", quote(bid=1.0800))
    p = engine.paper.open("EURUSD", "BUY", 0.1, 1.0790, 1.0830, quote(bid=1.0800))
    engine.rt.ingest_quote("EURUSD", quote(bid=1.0805))
    fresh_calendar(engine, [{"t": int(time.time() + 200), "ccy": "EUR", "imp": 3, "name": "ECB"}])
    engine.pm.run()
    assert engine.paper.positions[p["ticket"]]["sl"] == pytest.approx(p["price_open"] + 0.00002)
    assert p["ticket"] in engine.paper.positions  # not force-closed


# ---- 4. loss brakes ------------------------------------------------------------
def test_break_even_trades_are_neutral(engine):
    now = time.time()
    add_trade(engine, 1, -20.0, now - 400)
    add_trade(engine, 2, -20.0, now - 300)
    add_trade(engine, 3, +0.02, now - 200)   # BE exit: < 0.05% of 10000 equity -> neutral
    add_trade(engine, 4, -20.0, now - 100)
    assert engine.loss_streak("PAPER")[0] == 3
    add_trade(engine, 5, +20.0, now - 50)
    assert engine.loss_streak("PAPER")[0] == 0


def test_neutral_uses_initial_risk_when_known(engine, monkeypatch):
    engine.rt.ingest_quote("EURUSD", quote(bid=1.08))
    # initial risk: 10 pips * 0.1 lot * 1.0 tick_value / 0.00001 = 10.0 -> 0.1R = 1.0
    monkeypatch.setattr(engine.executor, "open_intent_for_ticket", lambda t: {"sl": 1.0790})
    row = {"ticket": 1, "symbol": "EURUSD", "side": "BUY", "volume": 0.1, "price_open": 1.08,
           "profit": -0.9, "commission": 0.0, "swap": 0.0}
    assert engine._trade_outcome(row, 10000) == 0
    assert engine._trade_outcome({**row, "profit": -1.5}, 10000) == -1


def test_loss_streak_pause_expires(engine):
    engine.rt.risk.update(max_consecutive_losses=3, loss_streak_pause_minutes=90)
    now = time.time()
    for i, ago in enumerate((40, 30, 20)):
        add_trade(engine, 10 + i, -20.0, now - ago * 60)
    n, until = engine.loss_streak("PAPER", now)
    assert n == 3 and until == pytest.approx(now - 20 * 60 + 90 * 60, abs=2)
    engine.rt.ingest_quote("EURUSD", quote(bid=1.08))
    rc = engine.risk_check("EURUSD", "BUY", 1.0801, 1.0795, 1.0811)
    chk = next(c for c in rc["checks"] if c["name"] == "consecutive_losses_ok")
    assert chk["ok"] is False and "pauză până la" in chk["detail"]
    # after the pause the counter resets
    assert engine.loss_streak("PAPER", now + 71 * 60) == (0, None)
    # a new loss after the pause starts a new streak from 1
    add_trade(engine, 20, -20.0, now + 75 * 60)
    assert engine.loss_streak("PAPER", now + 76 * 60)[0] == 1


def test_loss_streak_zero_pause_blocks_rest_of_day(engine):
    engine.rt.risk.update(max_consecutive_losses=2, loss_streak_pause_minutes=0)
    now = time.time()
    add_trade(engine, 1, -20.0, now - 3)
    add_trade(engine, 2, -20.0, now - 2)
    assert engine.loss_streak("PAPER", now) == (2, None)
    assert engine.loss_streak("PAPER", now + 600) == (2, None)  # no timed reset


def test_loss_streak_keyed_per_demo_account(engine, monkeypatch):
    engine.rt.mt5_account = {"login": 111, "trade_mode": 0, "equity": 200.0}
    now = time.time()
    for i in range(3):
        add_trade(engine, 700 + i, -5.0, now - 100 + i, mode="DEMO")
    logins = {700: 222, 701: 222, 702: 111}
    monkeypatch.setattr(engine.executor, "open_intent_for_ticket", lambda t: {"account_login": logins.get(int(t))})
    assert engine.loss_streak("DEMO")[0] == 1
    assert engine.loss_streak("PAPER")[0] == 0


def test_total_open_risk(engine):
    ready(engine)
    engine.rt.ingest_quote("EURUSD", quote(bid=1.0800))
    engine.rt.ingest_quote("GBPUSD", quote(bid=1.3000))
    # 1.0 lot opened at ask 1.30010, SL 1.29900 -> 110 points -> 110.0 at risk (1.1% of 10000)
    p = engine.paper.open("GBPUSD", "BUY", 1.0, 1.2990, 1.3030, quote(bid=1.3000))
    assert engine.open_risk_money("PAPER") == pytest.approx(110.0, rel=1e-3)
    engine.rt.risk.update(max_total_open_risk_pct=1.1, max_open_positions=5, max_same_currency_direction=0)
    rc = engine.risk_check("EURUSD", "BUY", 1.0801, 1.0795, 1.0811)
    chk = {c["name"]: c for c in rc["checks"]}
    assert chk["total_open_risk_ok"]["ok"] is False, chk["total_open_risk_ok"]
    assert "% din" in chk["volume_ok"]["detail"] and "0.25%" in chk["volume_ok"]["detail"]
    # position at break-even counts 0
    engine.paper.modify(p["ticket"], p["price_open"] + 0.00002, None)
    assert engine.open_risk_money("PAPER") == 0.0
    rc = engine.risk_check("EURUSD", "BUY", 1.0801, 1.0795, 1.0811)
    assert next(c for c in rc["checks"] if c["name"] == "total_open_risk_ok")["ok"] is True


def test_position_risk_money_unit():
    info = {"tick_size": 0.00001, "tick_value": 1.0}
    assert risk_mod.position_risk_money("SELL", 1.08, 1.081, 0.1, info) == pytest.approx(10.0)
    assert risk_mod.position_risk_money("SELL", 1.08, 1.0799, 0.1, info) == 0.0
    assert risk_mod.position_risk_money("BUY", 1.08, None, 0.1, info) is None


# ---- 5. forced closes on stale ticks ----------------------------------------------
def test_stale_tick_still_sends_time_stop_close(engine):
    ready(engine)
    engine.rt.strategy["max_hold_minutes"] = 60
    old = time.time() - 3600
    p = engine.paper.open("EURUSD", "BUY", 0.1, 1.0790, 1.0830, quote(bid=1.0800), now=old - 120)
    engine.rt.ingest_quote("EURUSD", quote(bid=1.0800, now=old))  # tick one hour old
    assert engine.rt.tick_age("EURUSD") > 100
    engine.pm.run()
    assert p["ticket"] not in engine.paper.positions
    it = engine.executor.list(5)[0]
    assert it["kind"] == "CLOSE" and it["state"] == "PAPER_FILLED"


def test_stale_tick_skips_modify(engine):
    ready(engine)
    old = time.time() - 3600
    p = engine.paper.open("EURUSD", "BUY", 0.1, 1.0790, 1.0830, quote(bid=1.0800))
    engine.rt.ingest_quote("EURUSD", quote(bid=1.0815, now=old))   # +1.5R but stale
    engine.pm.run()
    assert engine.paper.positions[p["ticket"]]["sl"] == 1.0790


def test_forced_close_never_abandoned(engine, monkeypatch):
    now = time.time()
    monkeypatch.setattr(engine.executor, "consecutive_failures", lambda t, k: (FAIL_MAX_ATTEMPTS + 2, now - 2000))
    close = {"type": "CLOSE", "reason": "SESSION_END"}
    assert engine.pm._backing_off(9, close, now) is False        # retried after the capped backoff
    assert (9, "CLOSE") in engine.pm.gave_up                      # alert sent once
    assert engine.pm._backing_off(9, {"type": "CLOSE", "reason": "MANUAL"}, now) is True
    assert engine.pm._backing_off(9, {"type": "MODIFY", "reason": "BREAK_EVEN"}, now) is True
    monkeypatch.setattr(engine.executor, "consecutive_failures", lambda t, k: (FAIL_MAX_ATTEMPTS + 2, now - 10))
    assert engine.pm._backing_off(9, close, now) is True         # still within backoff window


# ---- 6. config / API ------------------------------------------------------------
def test_risk_api_new_keys(client, engine):
    r = client.get("/api/v1/risk").json()
    assert r["news_blackout_minutes"] == 30 and r["news_min_importance"] == 3
    assert r["max_total_open_risk_pct"] == 2.0 and r["loss_streak_pause_minutes"] == 90
    ok = client.put("/api/v1/risk", headers=ADMIN, json={"news_blackout_minutes": 45, "news_min_importance": 2,
                                                         "max_total_open_risk_pct": 1.5, "loss_streak_pause_minutes": 60})
    assert ok.status_code == 200 and ok.json()["news_blackout_minutes"] == 45
    for bad in ({"news_blackout_minutes": 241}, {"news_min_importance": 4}, {"news_min_importance": 0},
                {"max_total_open_risk_pct": 0.05}, {"max_total_open_risk_pct": 21}, {"loss_streak_pause_minutes": 1441}):
        assert client.put("/api/v1/risk", headers=ADMIN, json=bad).status_code == 422, bad
    st = client.get("/api/v1/status").json()
    assert set(st["news"]) >= {"ok", "events_next_24h", "next_high", "age_s"}
