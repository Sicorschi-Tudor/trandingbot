"""Profit-lock trailing (owner's rule): SL follows the price once a profit threshold is reached, only towards profit."""
from app import exits
from app.position_manager import plan, profit_lock_sl
from tests.conftest import ADMIN, quote

GOLD = {"point": 0.01, "digits": 2, "volume_min": 0.01, "volume_max": 100.0, "volume_step": 0.01, "stops_level": 0,
        "freeze_level": 0, "tick_value": 1.0, "tick_size": 0.01, "contract_size": 100}
# activate 0.66 R = +0.99 USD on a 1.50 risk (the owner's "+1.00"), gap 1/3 R = 0.50 USD
CFG = {"profit_lock_enabled": True, "profit_lock_activate_r": 0.66, "profit_lock_gap_r": 1 / 3, "profit_lock_step_r": 0.1,
       "break_even_at_r": 0, "max_hold_minutes": 0, "rescue_enabled": False}


def gold_quote(bid):
    return {**GOLD, "bid": bid, "ask": round(bid + 0.30, 2), "spread_points": 30}


def test_owner_example_buy():
    # BUY at 2000.00, initial SL 1998.50 (risk 1.50 USD): +1.00 -> SL +0.50; +1.30 -> SL +0.80; a drop never lowers it
    pos = {"side": "BUY", "price_open": 2000.00, "sl": 1998.50, "tp": 2005.00, "opened_ts": 0}
    assert plan(pos, gold_quote(1999.80), CFG, 100, True, initial_sl=1998.50) == []  # small dip: nothing
    assert plan(pos, gold_quote(2000.90), CFG, 100, True, initial_sl=1998.50) == []  # +0.90 < +1.00: not armed
    a = plan(pos, gold_quote(2001.00), CFG, 100, True, initial_sl=1998.50)
    assert a and a[0]["reason"] == "PROFIT_LOCK" and a[0]["sl"] == 2000.50
    pos["sl"] = 2000.50
    assert plan(pos, gold_quote(2001.10), CFG, 100, True, initial_sl=1998.50) == []  # +0.10 < step 0.15: no churn
    a = plan(pos, gold_quote(2001.30), CFG, 100, True, initial_sl=1998.50)
    assert a and a[0]["sl"] == 2000.80
    pos["sl"] = 2000.80
    assert plan(pos, gold_quote(2000.90), CFG, 100, True, initial_sl=1998.50) == []  # price falls: SL stays at +0.80
    assert plan(pos, gold_quote(2000.85), CFG, 100, True, initial_sl=1998.50) == []


def test_sell_side_and_disabled():
    pos = {"side": "SELL", "price_open": 2000.00, "sl": 2001.50, "tp": 1995.00, "opened_ts": 0}
    a = plan(pos, gold_quote(1998.70), CFG, 100, True, initial_sl=2001.50)  # ask 1999.00 -> profit 1.00
    assert a and a[0]["reason"] == "PROFIT_LOCK" and a[0]["sl"] == 1999.50
    assert plan(pos, gold_quote(1998.70), {**CFG, "profit_lock_enabled": False}, 100, True, initial_sl=2001.50) == []
    assert profit_lock_sl("BUY", 2001.0, 2000.0, 0.0, CFG, 2) is None


def test_best_candidate_wins_over_break_even():
    cfg = {**CFG, "break_even_at_r": 0.5, "be_lock_r": 0}
    pos = {"side": "BUY", "price_open": 2000.00, "sl": 1998.50, "tp": 2005.00, "opened_ts": 0}
    a = plan(pos, gold_quote(2001.00), cfg, 100, True, initial_sl=1998.50)
    assert a[0]["reason"] == "PROFIT_LOCK" and a[0]["sl"] == 2000.50  # higher than the break-even lock (+0.02)


def test_exit_policy_profit_lock_simulation():
    pol = exits.ExitPolicy("profit_lock", {"profit_lock_activate_r": 0.66, "profit_lock_gap_r": 1 / 3})
    from app.labeler import SimPosition
    sp = SimPosition(side="BUY", entry=2000.0, sl=1998.5, tp=2010.0, point=0.01, opened_t=0, be_at_r=0, r_dist=1.5)
    exits.attach(sp, pol, history=[], bar_sec=60)
    bar = lambda t, o, h, l, c: {"t": t, "o": o, "h": h, "l": l, "c": c}  # noqa: E731
    assert exits.step(sp, bar(60, 2000.0, 2000.9, 1999.9, 2000.8), 0.3) is None and sp.sl == 1998.5
    # bars are bid prices: a BUY exits on the close itself. close 2001.3 -> r 0.867 -> SL = 2001.3 - 0.50
    exits.step(sp, bar(120, 2000.8, 2001.2, 2000.7, 2001.3), 0.3)
    assert abs(sp.sl - 2000.8) < 1e-9
    exits.step(sp, bar(180, 2001.3, 2001.7, 2001.2, 2001.6), 0.3)  # close 2001.6 -> SL 2001.10
    assert abs(sp.sl - 2001.1) < 1e-9
    exits.step(sp, bar(240, 2001.6, 2001.7, 2001.15, 2001.2), 0.3)  # price falls: SL never moves down
    assert abs(sp.sl - 2001.1) < 1e-9
    # overlay helper
    p2 = exits.with_profit_lock(None, {"profit_lock_enabled": True, "profit_lock_gap_r": 0.2})
    assert "profit_lock" in p2.modes and p2.p["profit_lock_gap_r"] == 0.2
    p3 = exits.with_profit_lock(exits.ExitPolicy("trailing"), {"profit_lock_enabled": True})
    assert p3.mode == "combo" and set(p3.modes) == {"trailing", "profit_lock"}
    assert exits.with_profit_lock(exits.ExitPolicy("trailing"), {}).mode == "trailing"


def test_settings_and_api(client, engine):
    assert engine.rt.strategy["profit_lock_enabled"] is False
    r = client.put("/api/v1/strategy", headers=ADMIN, json={"profit_lock_enabled": True, "profit_lock_activate_r": 0.5,
                                                               "profit_lock_gap_r": 0.25})
    assert r.status_code == 200 and engine.rt.strategy["profit_lock_enabled"] is True
    assert engine.rt.strategy["profit_lock_activate_r"] == 0.5
    assert client.put("/api/v1/strategy", headers=ADMIN, json={"profit_lock_gap_r": 50}).status_code == 422


def test_gold_profile(tmp_path):
    from app.config import Settings
    from app.db import DB
    from app.engine import Engine
    s = Settings(_env_file=None, PROFILE="gold", DATABASE_PATH=str(tmp_path / "g.db"), ACTIVE_SYMBOLS="EURUSD,GBPUSD",
                 TELEGRAM_ENABLED=False, SESSION_ENABLED=False)
    assert s.ACTIVE_SYMBOLS == "XAUUSD" and s.TRADE_SYMBOLS == "XAUUSD" and s.MAX_POSITIONS_PER_SYMBOL == 3
    assert s.MAX_SPREAD_POINTS == 60 and s.AI_TRADER_ENTRY_MODE == "shadow" and "D1" in s.CHART_TIMEFRAMES
    db = DB(s.db_path)
    db.set_setting("strategy", {"symbols": ["EURUSD"], "trade_symbols": [], "atr_sl_mult": 2.0})  # stale dashboard save
    eng = Engine(s, db)
    eng.startup()
    assert eng.rt.active_symbols() == ["XAUUSD"] and eng.trade_symbols() == ["XAUUSD"]
    assert eng.rt.strategy["atr_sl_mult"] == 2.0  # other saved keys still honoured
    assert eng.ai_trader.conf["entry_mode"] == "shadow" and eng.ai_trader.symbols() == ["XAUUSD"]
    assert Settings(_env_file=None, PROFILE="", ACTIVE_SYMBOLS="EURUSD").ACTIVE_SYMBOLS == "EURUSD"
    db.close()
