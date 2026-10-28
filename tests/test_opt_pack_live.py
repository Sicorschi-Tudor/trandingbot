"""Live optimisation pack: rescue exit, smart break-even lock, loss cooldown, rollover blackout, pre-AI arbitration."""
import asyncio
import time
from datetime import datetime, timezone

import pytest

from app import risk as risk_mod
from app import strategy
from app.config import iso
from app.db import DB
from app.engine import Engine
from app.position_manager import be_lock_distance, plan, rescue_due
from tests.conftest import make_settings, quote


def _utc(*a) -> float:
    return datetime(*a, tzinfo=timezone.utc).timestamp()


# ---- config / API keys -------------------------------------------------------
def test_default_keys(settings):
    st, rk = settings.default_strategy(), settings.default_risk()
    assert st["rescue_enabled"] is True and st["rescue_minutes"] == 30 and st["rescue_mae_r"] == 0.3
    assert st["rescue_neg_frac"] == 0.6 and st["rescue_close_r"] == 0.1 and st["be_lock_r"] == 0.1
    assert rk["cooldown_after_loss_minutes"] == 30 and rk["rollover_blackout_minutes"] == 20


def test_api_validation():
    from fastapi import HTTPException

    from app import api
    cur = {"signal_timeframe": "M5", "htf_timeframe": "H1"}
    out = api.validate_strategy_update({"be_lock_r": 0.2, "rescue_enabled": False, "rescue_minutes": 45,
                                        "rescue_mae_r": 0.5, "rescue_neg_frac": 0.7, "rescue_close_r": 0.05}, cur)
    assert out["rescue_enabled"] is False and out["rescue_minutes"] == 45 and out["be_lock_r"] == 0.2
    assert api.validate_risk_update({"cooldown_after_loss_minutes": 0, "rollover_blackout_minutes": 30}) == {
        "cooldown_after_loss_minutes": 0, "rollover_blackout_minutes": 30}
    for bad in ({"rescue_neg_frac": 1.5}, {"be_lock_r": -0.1}, {"rescue_enabled": 1}):
        with pytest.raises(HTTPException):
            api.validate_strategy_update(bad, cur)
    with pytest.raises(HTTPException):
        api.validate_risk_update({"rollover_blackout_minutes": 500})


# ---- smart break-even lock -----------------------------------------------------
BE_CFG = {"break_even_at_r": 1.0, "trailing_enabled": False, "max_hold_minutes": 0, "close_at_session_end": False}


def test_be_lock_distance_formula():
    p = 0.00001
    assert be_lock_distance(0, 10 * p, 100 * p, p) == pytest.approx(2 * p)  # old behaviour
    assert be_lock_distance(0.1, 10 * p, 100 * p, p) == pytest.approx(13 * p)  # spread + 3 points wins
    assert be_lock_distance(0.1, 10 * p, 300 * p, p) == pytest.approx(30 * p)  # 0.1R wins


def test_be_lock_buy_sell_and_zero_is_old():
    now = time.time()
    pos = {"side": "BUY", "price_open": 1.08000, "sl": 1.07900, "tp": 1.08300, "opened_ts": now}
    q = quote(bid=1.08100, spread_pts=10)
    assert plan(pos, q, {**BE_CFG, "be_lock_r": 0}, now, True, 1.079)[0]["sl"] == 1.08002
    assert plan(pos, q, BE_CFG, now, True, 1.079)[0]["sl"] == 1.08002  # key missing = old behaviour
    acts = plan(pos, q, {**BE_CFG, "be_lock_r": 0.1}, now, True, 1.079)
    assert acts == [{"type": "MODIFY", "sl": 1.08013, "tp": 1.083, "reason": "BREAK_EVEN"}]
    # bigger initial risk: 0.1R (30 points) > spread + 3
    pos3 = dict(pos, sl=1.07700)
    acts = plan(pos3, quote(bid=1.08300, spread_pts=10), {**BE_CFG, "be_lock_r": 0.1}, now, True, 1.077)
    assert acts[0]["sl"] == 1.0803
    # SELL: ask = 1.07900 -> +1R; lock 13 points below the entry
    sell = {"side": "SELL", "price_open": 1.08000, "sl": 1.08100, "tp": 1.07500, "opened_ts": now}
    acts = plan(sell, quote(bid=1.07890, spread_pts=10), {**BE_CFG, "be_lock_r": 0.1}, now, True, 1.081)
    assert acts[0]["sl"] == 1.07987 and acts[0]["reason"] == "BREAK_EVEN"
    # only tightens: an SL already beyond the lock is left alone
    assert plan(dict(pos, sl=1.08020), q, {**BE_CFG, "be_lock_r": 0.1}, now, True, 1.079) == []


def _jpy(bid, spread_pts, **kw):
    q = {"point": 0.001, "digits": 3, "bid": bid, "ask": round(bid + spread_pts * 0.001, 3), "spread_points": spread_pts,
         "stops_level": 0, "freeze_level": 0, "tick_value": 0.65, "tick_size": 0.001, "volume_min": 0.01,
         "volume_step": 0.01, "volume_max": 100, "time_msc": int(time.time() * 1000)}
    q.update(kw)
    return q


def test_be_lock_jpy_and_stops_level():
    now = time.time()
    pos = {"side": "BUY", "price_open": 150.000, "sl": 149.800, "tp": 150.600, "opened_ts": now}
    cfg = {**BE_CFG, "be_lock_r": 0.1}
    # risk 200 points: lock = max(15 + 3, 20) = 20 points = 0.020
    acts = plan(pos, _jpy(150.200, 15), cfg, now, True, 149.8)
    assert acts[0]["sl"] == 150.02 and acts[0]["reason"] == "BREAK_EVEN"
    # wide spread: lock = spread 40 + 3 = 43 points
    assert plan(pos, _jpy(150.250, 40), cfg, now, True, 149.8)[0]["sl"] == 150.043
    # stops level 150 points: 150.200 - 150.020 = 180 points >= 151 -> allowed
    assert plan(pos, _jpy(150.200, 15, stops_level=150), cfg, now, True, 149.8)[0]["sl"] == 150.02
    # stops level 190 points: the lock would sit inside it -> no modification (retried later)
    assert plan(pos, _jpy(150.200, 15, stops_level=190), cfg, now, True, 149.8) == []


# ---- rescue exit ---------------------------------------------------------------
RESCUE_CFG = {"break_even_at_r": 1.0, "max_hold_minutes": 0, "rescue_enabled": True, "rescue_minutes": 30,
              "rescue_mae_r": 0.3, "rescue_neg_frac": 0.6, "rescue_close_r": 0.1}


def test_rescue_due_rules():
    now = 10_000.0
    st = {"opened_ts": now - 31 * 60, "mae_r": 0.5, "n": 31, "neg": 25}
    assert rescue_due(RESCUE_CFG, st, 0.12, now)
    assert not rescue_due(RESCUE_CFG, st, 0.05, now)  # profit below rescue_close_r
    assert not rescue_due(RESCUE_CFG, dict(st, opened_ts=now - 20 * 60), 0.12, now)  # too early
    assert not rescue_due(RESCUE_CFG, dict(st, mae_r=0.2), 0.12, now)  # never deep enough under water
    assert not rescue_due(RESCUE_CFG, dict(st, neg=10), 0.12, now)  # mostly in profit
    assert not rescue_due({**RESCUE_CFG, "rescue_enabled": False}, st, 0.12, now)
    assert not rescue_due(RESCUE_CFG, None, 0.12, now)
    pos = {"side": "BUY", "price_open": 1.08000, "sl": 1.07900, "tp": 1.08300, "opened_ts": now - 31 * 60}
    acts = plan(pos, quote(bid=1.08012), RESCUE_CFG, now, True, 1.079, rescue_state=st)
    assert acts == [{"type": "CLOSE", "reason": "RESCUE"}]
    # unknown initial risk -> no rescue
    assert plan(pos, quote(bid=1.08012), RESCUE_CFG, now, True, None, rescue_state=st) == []


@pytest.fixture
def eng(tmp_path):
    s = make_settings(tmp_path)
    db = DB(s.db_path)
    e = Engine(s, db)
    e.startup()
    yield e
    db.close()


def _spy(e, monkeypatch):
    closes, mods = [], []
    orig_close = e.close_position

    def close(source, ticket, reason="BOT"):
        closes.append((source, ticket, reason))
        return orig_close(source, ticket, reason=reason)

    def modify(*a, **kw):
        mods.append((a, kw))
        return {"state": "QUEUED"}

    monkeypatch.setattr(e, "close_position", close)
    monkeypatch.setattr(e.executor, "submit_modify", modify)
    return closes, mods


def _walk(e, t0, minutes, bid, sym="EURUSD"):
    """One quote + trade-path sample per minute."""
    for m in range(minutes):
        ts = t0 + m * 60 + 1
        e.rt.ingest_quote(sym, quote(bid=bid, spread_pts=10, now=ts))
        e.track_trade_paths(ts)


def test_rescue_closes_bot_paper_position_on_tick_sequence(eng, monkeypatch):
    closes, mods = _spy(eng, monkeypatch)
    t0 = (time.time() // 60) * 60 - 40 * 60
    q0 = quote(bid=1.08000, spread_pts=10, now=t0)
    eng.rt.ingest_quote("EURUSD", q0)
    p = eng.paper.open("EURUSD", "BUY", 0.1, 1.07950, 1.08200, q0, now=t0)  # entry 1.08010, risk 60 points
    _walk(eng, t0, 32, bid=1.07980)  # 32 minutes at -0.5R
    st = eng._paths[("PAPER", str(p["ticket"]))]
    assert st["n"] == 32 and st["neg"] == 32 and st["mae"] == pytest.approx(0.5)
    t = t0 + 32 * 60 + 5
    # back to +0.05R: below rescue_close_r -> nothing
    eng.rt.ingest_quote("EURUSD", quote(bid=1.08013, spread_pts=10, now=t))
    eng.pm.run(t)
    assert closes == [] and mods == []
    # stale quote at +0.17R -> nothing (rescue only on a fresh bid/ask)
    t += 20
    eng.rt.ingest_quote("EURUSD", quote(bid=1.08020, spread_pts=10, now=t - 25))
    eng.pm.run(t)
    assert closes == [] and mods == []
    # fresh +0.17R -> CLOSE RESCUE; the SL is never modified
    t += 20
    eng.rt.ingest_quote("EURUSD", quote(bid=1.08020, spread_pts=10, now=t))
    eng.pm.run(t)
    assert closes == [("PAPER", p["ticket"], "RESCUE")] and mods == []
    assert p["ticket"] not in eng.paper.positions
    it = eng.db.query_one("SELECT data_json FROM intents WHERE kind='CLOSE' ORDER BY created_at_utc DESC LIMIT 1")
    assert '"RESCUE"' in it["data_json"]


def test_rescue_not_armed_early_or_disabled(eng, monkeypatch):
    closes, mods = _spy(eng, monkeypatch)
    t0 = (time.time() // 60) * 60 - 20 * 60
    q0 = quote(bid=1.08000, spread_pts=10, now=t0)
    eng.rt.ingest_quote("EURUSD", q0)
    p = eng.paper.open("EURUSD", "BUY", 0.1, 1.07950, 1.08200, q0, now=t0)
    _walk(eng, t0, 15, bid=1.07980)
    t = t0 + 16 * 60
    eng.rt.ingest_quote("EURUSD", quote(bid=1.08020, spread_pts=10, now=t))
    eng.pm.run(t)  # only 16 minutes open
    assert closes == []
    eng.rt.strategy["rescue_enabled"] = False
    _walk(eng, t0 + 16 * 60, 20, bid=1.07980)
    t = t0 + 40 * 60
    eng.rt.ingest_quote("EURUSD", quote(bid=1.08020, spread_pts=10, now=t))
    eng.pm.run(t)
    assert closes == [] and mods == [] and p["ticket"] in eng.paper.positions
    assert eng.paper.positions[p["ticket"]]["sl"] == 1.0795


def test_rescue_never_touches_non_bot_positions(eng, monkeypatch):
    closes, _ = _spy(eng, monkeypatch)
    now = time.time()
    eng.rt.mt5_account = {"trade_mode": 0, "login": 1}
    monkeypatch.setattr(eng.rt, "bridge_connected", lambda: True)
    eng.rt.mt5_positions = [{"ticket": 77, "symbol": "EURUSD", "type": 0, "magic": 999, "price_open": 1.08010,
                             "sl": 1.0795, "tp": 1.082, "time": now - 3600, "volume": 0.1}]
    eng._paths[("MT5", "77")] = {"entry": 1.0801, "risk": 0.0006, "side": "BUY", "symbol": "EURUSD",
                                 "opened_ts": now - 3600, "mfe": 0.2, "mae": 0.8, "n": 60, "neg": 60, "last_ts": 0}
    eng.rt.ingest_quote("EURUSD", quote(bid=1.08030, spread_pts=10, now=now))
    eng.pm.run(now)
    assert closes == []


# ---- loss cooldown -------------------------------------------------------------
def test_cooldown_blocker_unit():
    now = 1_800_000_000.0
    ctx = strategy.AnalysisContext(now_ts=now, last_loss_ts=now - 10 * 60)
    out = strategy.context_blockers("EURUSD", "BUY", ctx, {"cooldown_after_loss_minutes": 30})
    assert len(out) == 1 and out[0].startswith("Pauză după pierdere pe simbol") and "încă 20 min" in out[0]
    assert strategy.context_blockers("EURUSD", "BUY", ctx, {"cooldown_after_loss_minutes": 0}) == []
    ctx2 = strategy.AnalysisContext(now_ts=now, last_loss_ts=now - 31 * 60)
    assert strategy.context_blockers("EURUSD", "BUY", ctx2, {"cooldown_after_loss_minutes": 30}) == []


def _trade(e, key, symbol, net, minutes_ago, now):
    e._insert_trade(key, {"ticket": None, "symbol": symbol, "side": "BUY", "volume": 0.1, "price_open": 1.08,
                          "price_close": 1.08, "profit": net, "commission": 0.0, "swap": 0.0,
                          "opened_at_utc": iso(now - minutes_ago * 60 - 600), "closed_at_utc": iso(now - minutes_ago * 60),
                          "close_reason": "SL" if net < 0 else "TP", "mode": "PAPER", "signal_id": None})


def test_cooldown_after_loss_per_symbol_not_after_win_or_be(eng):
    now = time.time()
    _trade(eng, "PAPER:1", "EURUSD", -20.0, 10, now)  # loss 10 min ago
    _trade(eng, "PAPER:2", "GBPUSD", 25.0, 5, now)  # win
    _trade(eng, "PAPER:3", "USDJPY", -1.0, 5, now)  # break-even (|net| < 0.05% of 10000 equity)
    _trade(eng, "PAPER:4", "AUDUSD", -30.0, 45, now)  # loss, but outside the 30 min window
    ll = eng.last_loss_ts("EURUSD", now)
    assert ll == pytest.approx(now - 600, abs=1)
    for sym in ("GBPUSD", "USDJPY", "AUDUSD", "NZDUSD"):
        assert eng.last_loss_ts(sym, now) is None, sym
    eng.rt.risk["cooldown_after_loss_minutes"] = 0
    assert eng.last_loss_ts("EURUSD", now) is None


def test_analyze_symbol_passes_cooldown_to_context(eng, monkeypatch):
    now = time.time()
    _trade(eng, "PAPER:1", "EURUSD", -20.0, 5, now)
    seen = {}

    def fake(symbol, sig_bars, htf_bars, q, cfg, risk, ctx):
        seen["ctx"] = ctx
        seen["blockers"] = strategy.context_blockers(symbol, "BUY", ctx, risk)
        return {"symbol": symbol, "timeframe": "M5", "time_utc": iso(now), "bar_time": None, "direction": "HOLD",
                "reasons": [], "indicators": {}}

    monkeypatch.setattr(eng, "active_strategy", lambda: ("trend-v1", fake, eng.rt.strategy))
    eng.analyze_symbol("EURUSD", allow_signal=False)
    assert seen["ctx"].last_loss_ts is not None
    assert any(b.startswith("Pauză după pierdere") for b in seen["blockers"])


# ---- rollover blackout ---------------------------------------------------------
def test_rollover_summer_and_winter():
    # summer (EDT, UTC-4): rollover 17:00 NY = 21:00 UTC
    assert risk_mod.nearest_rollover_ts(_utc(2026, 7, 15, 20, 45)) == _utc(2026, 7, 15, 21, 0)
    assert risk_mod.rollover_blocker(_utc(2026, 7, 15, 20, 45), 20) is not None
    assert risk_mod.rollover_blocker(_utc(2026, 7, 15, 21, 19), 20) is not None
    assert risk_mod.rollover_blocker(_utc(2026, 7, 15, 20, 35), 20) is None
    assert risk_mod.rollover_blocker(_utc(2026, 7, 15, 22, 0), 20) is None
    # winter (EST, UTC-5): rollover = 22:00 UTC; 21:00 UTC is free
    assert risk_mod.nearest_rollover_ts(_utc(2026, 1, 15, 21, 50)) == _utc(2026, 1, 15, 22, 0)
    assert risk_mod.rollover_blocker(_utc(2026, 1, 15, 21, 50), 20) is not None
    assert risk_mod.rollover_blocker(_utc(2026, 1, 15, 22, 15), 20) is not None
    assert risk_mod.rollover_blocker(_utc(2026, 1, 15, 21, 0), 20) is None
    # US DST switch days (NY switches on the 2nd Sunday of March / 1st Sunday of November)
    assert risk_mod.nearest_rollover_ts(_utc(2026, 3, 8, 20, 50)) == _utc(2026, 3, 8, 21, 0)
    assert risk_mod.nearest_rollover_ts(_utc(2026, 11, 1, 21, 50)) == _utc(2026, 11, 1, 22, 0)
    # around midnight UTC the nearest rollover may be the previous NY day's
    assert risk_mod.nearest_rollover_ts(_utc(2026, 7, 16, 0, 30)) == _utc(2026, 7, 15, 21, 0)
    assert risk_mod.rollover_blocker(_utc(2026, 7, 15, 20, 45), 0) is None


def test_rollover_context_blocker():
    ctx = strategy.AnalysisContext(now_ts=_utc(2026, 1, 15, 21, 50))
    out = strategy.context_blockers("EURUSD", "BUY", ctx, {"rollover_blackout_minutes": 20})
    assert len(out) == 1 and out[0].startswith("Rollover 17:00 New York (22:00 UTC) peste 10 min")
    assert strategy.context_blockers("EURUSD", "BUY", strategy.AnalysisContext(now_ts=_utc(2026, 1, 15, 21, 0)),
                                     {"rollover_blackout_minutes": 20}) == []


# ---- pre-AI arbitration ----------------------------------------------------------
@pytest.fixture
def aeng(tmp_path):
    s = make_settings(tmp_path, ANTHROPIC_API_KEY="sk-ant-test-fake-key-0000")
    db = DB(s.db_path)
    e = Engine(s, db)
    e.startup()
    e.rt.risk.update(max_open_positions=5, max_trades_per_day=20, max_total_open_risk_pct=20,
                     max_same_currency_direction=1)
    yield e
    db.close()


def _cand(e, symbol, side, htf, bid, spread_pts, sl_pts=60, bar_time=1730000000):
    e.rt.ingest_quote(symbol, quote(bid=bid, spread_pts=spread_pts))
    q = e.rt.quotes[symbol]
    sign = 1 if side == "BUY" else -1
    entry = q["ask"] if side == "BUY" else q["bid"]
    a = {"symbol": symbol, "timeframe": "M5", "time_utc": iso(time.time()), "bar_time": bar_time, "direction": side,
         "score": 0.8, "probabilities": {"up": 0.7, "down": 0.18, "neutral": 0.12}, "reasons": ["test"],
         "indicators": {"ema_slow": bid, "htf_trend": htf}, "regime": "TREND_UP", "spread_points": spread_pts,
         "entry": round(entry, 5), "stop_loss": round(entry - sign * sl_pts * 0.00001, 5),
         "take_profit": round(entry + sign * 2 * sl_pts * 0.00001, 5), "rr": 2.0}
    sig = e.create_signal(a)
    assert sig and sig["ai"]["status"] == "PENDING"
    return sig, a


def _fake_ai(e, monkeypatch):
    calls = []

    async def review(signal, context=None):
        calls.append(signal["symbol"])
        return {"status": "DONE", "verdict": "AGREE", "explanation": "ok", "risks": [], "model": "m"}

    monkeypatch.setattr(e.ai, "review", review)
    return calls


def test_arbitration_order_htf_then_cost(aeng):
    items = [_cand(aeng, "GBPUSD", "BUY", "FLAT", 1.30000, 5),
             _cand(aeng, "EURUSD", "BUY", "UP", 1.08000, 20),
             _cand(aeng, "AUDUSD", "BUY", "UP", 0.66000, 5),
             _cand(aeng, "USDJPY", "SELL", "DOWN", 1.50000, 12)]
    # disarmed: no dry run, only the order changes
    order = [s["symbol"] for s in aeng.arbitrate(items)]
    assert order == ["AUDUSD", "USDJPY", "EURUSD", "GBPUSD"]


def test_arbitration_reserves_exposure_and_skips_claude(aeng, monkeypatch):
    calls = _fake_ai(aeng, monkeypatch)
    aeng.rt.trading.update(mode="PAPER", execution_enabled=True)
    eur = _cand(aeng, "EURUSD", "BUY", "FLAT", 1.08000, 10)  # USD short, not aligned
    gbp = _cand(aeng, "GBPUSD", "BUY", "UP", 1.30000, 10)  # USD short, aligned -> first
    jpy = _cand(aeng, "EURJPY", "SELL", "DOWN", 1.60000, 10)  # EUR short, JPY long: independent of GBPUSD
    aeng.dispatch_signals([eur, gbp, jpy])
    assert sorted(calls) == ["EURJPY", "GBPUSD"]  # Claude never sees the rejected candidate
    s_eur = aeng.get_signal(eur[0]["id"])
    assert s_eur["status"] == "NO_TRADE" and s_eur["ai"]["status"] == "SKIPPED"
    assert s_eur["status_reason"].startswith("Respins înainte de AI: ") and "currency_exposure_ok" in s_eur["status_reason"]
    ev = [r["event_type"] for r in aeng.signal_events(eur[0]["id"])]
    assert "PRE_AI_REJECTED" in ev
    for s in (gbp, jpy):
        got = aeng.get_signal(s[0]["id"])
        assert got["status"] == "EXECUTED" and got["ai"]["status"] == "DONE", got["status_reason"]
    assert aeng._pending_ai == {}  # reservations released after the pipeline


def test_arbitration_counts_signals_pending_review(aeng, monkeypatch):
    calls = _fake_ai(aeng, monkeypatch)
    aeng.rt.trading.update(mode="PAPER", execution_enabled=True)
    # a signal of an earlier pass is still waiting for Claude: EUR long reserved
    aeng._pending_ai["older"] = {"signal_id": "older", "symbol": "EURGBP", "side": "BUY", "risk_money": 10.0}
    eur = _cand(aeng, "EURUSD", "BUY", "UP", 1.08000, 10)
    aeng.dispatch_signals([eur])
    assert calls == []
    s = aeng.get_signal(eur[0]["id"])
    assert s["status"] == "NO_TRADE" and s["ai"]["status"] == "SKIPPED" and "EUR long" in s["status_reason"]
    assert set(aeng._pending_ai) == {"older"}


def test_arbitration_max_open_positions_with_reserved(aeng, monkeypatch):
    calls = []
    aeng.rt.trading.update(mode="PAPER", execution_enabled=True)
    aeng.rt.risk.update(max_open_positions=1, max_same_currency_direction=0)

    async def review(signal, context=None):  # checked while the first candidate is still reserved
        calls.append(signal["symbol"])
        return {"status": "DONE", "verdict": "CAUTION", "explanation": "", "risks": [], "model": "m"}

    monkeypatch.setattr(aeng.ai, "review", review)
    a = _cand(aeng, "AUDUSD", "BUY", "UP", 0.66000, 5)
    b = _cand(aeng, "EURJPY", "BUY", "UP", 1.60000, 15)
    c = _cand(aeng, "GBPCHF", "SELL", "FLAT", 1.10000, 5)
    admitted = aeng.arbitrate([c, b, a])
    assert [s["symbol"] for s in admitted] == ["AUDUSD"]
    assert set(aeng._pending_ai) == {a[0]["id"]}
    for x in (b, c):
        s = aeng.get_signal(x[0]["id"])
        assert s["ai"]["status"] == "SKIPPED" and "max_open_positions" in s["status_reason"]
    asyncio.run(aeng.signal_pipeline(admitted[0]))
    assert calls == ["AUDUSD"] and aeng._pending_ai == {}
    assert aeng.get_signal(a[0]["id"])["status"] == "EXECUTED"


def test_arbitration_off_when_disarmed_keeps_claude(aeng, monkeypatch):
    calls = _fake_ai(aeng, monkeypatch)
    aeng.rt.risk.update(max_open_positions=1, max_same_currency_direction=1)
    eur = _cand(aeng, "EURUSD", "BUY", "UP", 1.08000, 10)
    gbp = _cand(aeng, "GBPUSD", "BUY", "UP", 1.30000, 10)
    aeng.dispatch_signals([eur, gbp])  # SIGNAL info only: every signal is still reviewed
    assert sorted(calls) == ["EURUSD", "GBPUSD"]
    assert all(aeng.get_signal(x[0]["id"])["ai"]["status"] == "DONE" for x in (eur, gbp))
