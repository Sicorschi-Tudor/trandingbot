"""Profile gold_scalp: scalp-v1 entry, +target money exit, profit ladder, fixed lot, pinned limits."""
import time

from app import risk as R
from app.config import Settings, profile_pins
from app.position_manager import ladder_levels, plan
from app.strategies import get, merged_cfg
from app.strategy import AnalysisContext

GOLD = {"digits": 2, "point": 0.01, "tick_size": 0.01, "tick_value": 1.0, "volume_min": 0.01, "volume_step": 0.01,
        "stops_level": 0}


def settings():
    return Settings(_env_file=None, PROFILE="gold_scalp")


def bars(n=300, start=4000.0, step=0.3):
    out, p = [], start
    for i in range(n):
        o, p = p, p + step
        out.append({"t": 1793290000 + 60 * i, "o": o, "h": p + 0.2, "l": o - 0.2, "c": p})
    return out


def ctx(has_pos=False):
    return AnalysisContext(session_open=True, has_bot_position=has_pos, signals_this_session=0, tick_age=1.0,
                           now_ts=time.time(), open_exposures=[], corr_limit=3, news_block=None, last_loss_ts=None,
                           symbol_positions=None)


def test_profile_values_and_pins():
    s = settings()
    assert (s.STRATEGY_NAME, s.SIGNAL_TIMEFRAME, s.HTF_TIMEFRAME) == ("scalp-v1", "M1", "M1")
    assert s.SESSION_ENABLED is False and s.SCALP_TARGET_USD == 1.0
    assert s.CLAUDE_ENABLED and s.CLAUDE_REQUIRED and s.CLAUDE_EXECUTE_ON == "AGREE" and s.CLAUDE_REVIEW_EFFORT == "high"
    pins = profile_pins("gold_scalp")
    assert "scalp_target_usd" in pins["strategy"] and "max_trades_per_day" in pins["risk"]
    assert "max_trades_per_day" not in profile_pins("gold")["risk"]


def test_scalp_entry_tp_is_one_dollar_on_fixed_lot():
    s = settings()
    b = bars()
    q = {**GOLD, "bid": b[-1]["c"], "ask": b[-1]["c"] + 0.5, "spread_points": 50}
    a = get("scalp-v1").analyze("XAUUSD", b, b, q, merged_cfg("scalp-v1", s.default_strategy()), s.default_risk(), ctx())
    assert a["direction"] == "SELL"  # a steady climb = RSI ~100 -> fade it
    assert round(a["entry"] - a["take_profit"], 2) == 1.0  # 0.01 lot of gold: 1 USD of price = 1 USD of profit
    assert a["stop_loss"] - a["entry"] >= 3.0
    held = get("scalp-v1").analyze("XAUUSD", b, b, q, merged_cfg("scalp-v1", s.default_strategy()), s.default_risk(),
                                   ctx(has_pos=True))
    assert held["direction"] == "HOLD"  # one position at a time


def test_profit_target_close():
    cfg = {**settings().default_strategy(), "ladder_enabled": False}
    pos = {"side": "SELL", "price_open": 4000.0, "sl": 4003.0, "tp": 3999.0, "opened_ts": time.time(), "volume": 0.01}
    assert plan(pos, {**GOLD, "bid": 3998.6, "ask": 3999.1}, cfg, time.time(), True) == []
    assert plan(pos, {**GOLD, "bid": 3998.5, "ask": 3999.0}, cfg, time.time(), True) == [
        {"type": "CLOSE", "reason": "PROFIT_TARGET"}]


def test_profit_target_uses_broker_profit():
    cfg = {**settings().default_strategy(), "ladder_enabled": False}
    pos = {"side": "SELL", "price_open": 4000.0, "sl": 4003.0, "tp": 3990.0, "opened_ts": time.time(), "volume": 0.01}
    bad_tick = {**GOLD, "tick_value": 0.1, "bid": 3999.4, "ask": 3999.0}  # tick_value says 0.10 USD, broker says 1.00
    assert plan({**pos, "profit": 0.99}, bad_tick, cfg, time.time(), True) == []
    assert plan({**pos, "profit": 1.0}, bad_tick, cfg, time.time(), True) == [{"type": "CLOSE", "reason": "PROFIT_TARGET"}]


def test_tp_uses_contract_size():
    s = settings()
    b = bars()
    q = {**GOLD, "tick_value": 0.1, "contract_size": 100.0, "bid": b[-1]["c"], "ask": b[-1]["c"] + 0.5,
         "spread_points": 50}
    a = get("scalp-v1").analyze("XAUUSD", b, b, q, merged_cfg("scalp-v1", s.default_strategy()), s.default_risk(), ctx())
    assert round(a["entry"] - a["take_profit"], 2) == 1.0


def test_rsi_entry_waits_in_the_middle():
    s = settings()
    b = [{"t": 1793290000 + 60 * i, "o": 4000.0, "h": 4000.5, "l": 3999.5, "c": 4000.0 + (0.3 if i % 2 else -0.3)}
         for i in range(300)]
    q = {**GOLD, "bid": 4000.0, "ask": 4000.4, "spread_points": 40}
    a = get("scalp-v1").analyze("XAUUSD", b, b, q, merged_cfg("scalp-v1", s.default_strategy()), s.default_risk(), ctx())
    assert a["direction"] == "HOLD" and "RSI" in a["reasons"][0]


def test_ladder_levels():
    cfg = settings().default_strategy()
    assert ladder_levels(1.0, 0.6, cfg) == (1.0, None)  # not yet
    tp, lock = ladder_levels(1.0, 0.7, cfg)  # +0.70 -> TP 1.50, SL locks +0.10
    assert (tp, round(lock, 2)) == (1.5, 0.1)
    tp, lock = ladder_levels(1.5, 1.25, cfg)
    assert (tp, round(lock, 2)) == (2.0, 0.6)
    tp, lock = ladder_levels(1.0, 2.3, cfg)  # a jump over several steps lands on the last one
    assert (tp, round(lock, 2)) == (3.0, 1.6)
    assert ladder_levels(10.0, 9.9, cfg) == (10.0, None)  # at the cap: no further step


def test_ladder_modify_moves_tp_and_sl_never_back():
    cfg = settings().default_strategy()
    pos = {"side": "BUY", "price_open": 4000.0, "sl": 3997.0, "tp": 4001.0, "opened_ts": time.time(), "volume": 0.01}
    info = {**GOLD, "contract_size": 100.0, "bid": 4000.75, "ask": 4001.1}
    assert plan(pos, info, cfg, time.time(), True) == [{"type": "MODIFY", "sl": 4000.1, "tp": 4001.5, "reason": "LADDER"}]
    locked = {**pos, "sl": 4000.5}  # SL already above the new lock: kept, TP still moves
    assert plan(locked, info, cfg, time.time(), True) == [{"type": "MODIFY", "sl": 4000.5, "tp": 4001.5,
                                                           "reason": "LADDER"}]


def test_ladder_cap_close():
    cfg = settings().default_strategy()
    pos = {"side": "BUY", "price_open": 4000.0, "sl": 4009.0, "tp": 4010.0, "opened_ts": time.time(), "volume": 0.01,
           "profit": 10.0}
    assert plan(pos, {**GOLD, "bid": 4010.0, "ask": 4010.4}, cfg, time.time(), True) == [
        {"type": "CLOSE", "reason": "PROFIT_TARGET"}]


def test_fixed_volume_ignores_equity_risk():
    s = settings()
    c = R.RiskContext(mode="PAPER", execution_enabled=True, entries_paused=False, side="BUY", entry=4001.0, sl=3998.0,
                      tp=4002.0, symbol_info={**GOLD, "bid": 4000.5, "ask": 4001.0}, account={"equity": 188.0,
                      "margin_free": 150.0}, risk=s.default_risk(), tick_age=1.0, symbol="XAUUSD",
                      day_start_equity=250.0, peak_equity=300.0, consecutive_losses=20, trades_today=500)
    rc = R.evaluate(c)
    assert rc["passed"], R.failed_summary(rc)
    assert rc["volume"] == 0.01


# ---- Claude as the scalp entry analyst ------------------------------------------------------------------------------
def test_agree_gate_blocks_caution(engine, monkeypatch):
    import asyncio

    from tests.conftest import quote
    from tests.test_engine import analysis_stub
    engine.settings = engine.settings.model_copy(update={"CLAUDE_EXECUTE_ON": "AGREE"})
    engine.rt.ingest_quote("EURUSD", quote(bid=1.08000))
    engine.rt.trading.update(mode="PAPER", execution_enabled=True)

    def run(verdict, bar_time):
        sig = engine.create_signal(analysis_stub(bar_time=bar_time))
        sig["ai"]["status"] = "PENDING"

        async def fake_review(signal, context=None):
            return {"status": "DONE", "verdict": verdict, "explanation": "x", "risks": [], "model": "m"}

        monkeypatch.setattr(engine.ai, "review", fake_review)
        asyncio.run(engine.signal_pipeline(sig))
        return engine.get_signal(sig["id"])

    s = run("CAUTION", 1730000000)
    assert s["status"] == "NO_TRADE" and "nu a confirmat" in s["status_reason"] and not engine.paper.positions
    s = run("AGREE", 1730000300)
    assert s["status"] == "EXECUTED", s["status_reason"]


def test_scalp_review_uses_scalp_prompt(engine):
    import asyncio
    import json
    from types import SimpleNamespace

    from app.ai_claude import SCALP_PROMPT, SCALP_SCHEMA
    calls = []

    class Fake:
        async def create(self, **kw):
            calls.append(kw)
            body = {"verdict": "AGREE", "p_first_step": 0.9, "explanation": "suport M15", "risks": [], "invalidation": "x"}
            return SimpleNamespace(stop_reason="end_turn", model=kw["model"], usage=None,
                                   content=[SimpleNamespace(type="text", text=json.dumps(body))])

    engine.ai.settings = engine.settings.model_copy(update={"ANTHROPIC_API_KEY": "sk-ant-test-key-123456"})
    engine.ai._client = SimpleNamespace(beta=SimpleNamespace(messages=Fake()), messages=Fake())
    out = asyncio.run(engine.ai.review({"id": "s1", "symbol": "XAUUSD", "strategy_version": "scalp-v1"}, {"scalp": {}}))
    assert out["verdict"] == "AGREE" and out["p_first_step"] == 0.9
    assert calls[0]["system"] == SCALP_PROMPT and calls[0]["output_config"]["format"]["schema"] is SCALP_SCHEMA


def test_live_status_shape(engine):
    from tests.conftest import quote
    engine.rt.ingest_quote("EURUSD", quote(bid=1.08000))
    d = engine.live_status()
    assert d["symbols"] and {"Date MT5", "Analiză", "Semnal", "Claude", "Ordin", "Poziție"} == {
        s["step"] for s in d["symbols"][0]["steps"]}
    assert d["symbols"][0]["headline"]


# ---- SMC check (app/smc.py) -------------------------------------------------------------------------------------------
def _b(t, o, h, l, c):
    return {"t": t, "o": o, "h": h, "l": l, "c": c}


def test_smc_trend_and_shape():
    from app import smc
    px, up = 100.0, []
    for i in range(120):  # zigzag climbing: 5 bars up 1.0, 3 bars down 0.8 -> higher highs and higher lows
        step = 1.0 if i % 8 < 5 else -0.8
        up.append(_b(i * 900, px, max(px, px + step) + 0.2, min(px, px + step) - 0.2, px + step))
        px += step
    assert smc.structure_trend(up) == 1
    m1 = [_b(i * 60, 200 + (i % 7) * 0.1, 200.5 + (i % 7) * 0.1, 199.5 + (i % 7) * 0.1, 200.2 + (i % 7) * 0.1)
          for i in range(300)]
    s = smc.analyze(m1, {"M15": up, "H1": up}, "M1", 0.3, now_ts=m1[-1]["t"] + 12 * 3600)
    assert s["direction"] == 1 and [x["key"] for x in s["steps"]] == ["trend", "sweep", "shift", "zone", "room"]
    assert s["steps"][0]["status"] == "pass" and 0 <= s["score"] <= 95 and s["verdict"] in ("ENTER", "WAIT", "NONE", "STOP")
    assert s["levels"]["sl"] < s["levels"]["entry"] < s["levels"]["tp1"]


def test_smc_rollover_block():
    from app import smc
    m1 = [_b(i * 60, 200, 200.5, 199.5, 200.1) for i in range(300)]
    s = smc.analyze(m1, {"M15": m1, "H1": m1}, "M1", 0.3, now_ts=22 * 3600)
    assert s["verdict"] == "STOP" and any("Rollover" in b for b in s["blocks"])


# ---- smc-v1 plugin (profile gold_smc) ---------------------------------------------------------------------------------
def test_gold_smc_profile():
    s = Settings(_env_file=None, PROFILE="gold_smc")
    assert (s.STRATEGY_NAME, s.SIGNAL_TIMEFRAME, s.HTF_TIMEFRAME) == ("smc-v1", "M15", "H1")
    assert s.LADDER_ENABLED and s.SCALP_TARGET_USD == 1.0 and s.CLAUDE_EXECUTE_ON == "AGREE"
    assert "ladder_enabled" in profile_pins("gold_smc")["strategy"]


def test_smc_plugin_enter_uses_ladder_tp(monkeypatch):
    from app import smc
    s = Settings(_env_file=None, PROFILE="gold_smc")
    b = bars(n=300, step=0.0)
    fake = {"direction": -1, "score": 70, "verdict": "ENTER", "title": "Intră SELL (market, scor 70)", "steps": [],
            "blocks": [], "levels": {"entry": 4000.0, "etype": "market", "sl": 4004.0, "tp1": 3992.0}, "hold_txt": "30–45 min"}
    monkeypatch.setattr(smc, "analyze", lambda *a, **k: dict(fake))
    q = {**GOLD, "contract_size": 100.0, "bid": 4000.0, "ask": 4000.3, "spread_points": 30}
    c = ctx()
    c.htf_extra = {"H1": b, "H4": b}
    a = get("smc-v1").analyze("XAUUSD", b, b, q, merged_cfg("smc-v1", s.default_strategy()), s.default_risk(), c)
    assert a["direction"] == "SELL" and a["smc"]["verdict"] == "ENTER"
    assert round(a["stop_loss"] - a["entry"], 2) == 4.0 and round(a["entry"] - a["take_profit"], 2) == 1.0  # no M1: SMC SL
    c.sl_bars = [_b(i * 60, 4000, 4000.5, 3999.5, 4000) for i in range(60)]  # ATR(M1) = 1.0 -> SL max(3.0, 3.00)
    a = get("smc-v1").analyze("XAUUSD", b, b, q, merged_cfg("smc-v1", s.default_strategy()), s.default_risk(), c)
    assert round(a["stop_loss"] - a["entry"], 2) == 3.0 and "3×ATR(M1)" in a["reasons"][0]
    monkeypatch.setattr(smc, "analyze", lambda *a, **k: {**fake, "levels": {**fake["levels"], "etype": "limit"}})
    held = get("smc-v1").analyze("XAUUSD", b, b, q, merged_cfg("smc-v1", s.default_strategy()), s.default_risk(), c)
    assert held["direction"] == "HOLD" and "limită" in held["reasons"][0]


def test_gold_scalp_fast_profile():
    s = Settings(_env_file=None, PROFILE="gold_scalp_fast")
    assert (s.STRATEGY_NAME, s.SIGNAL_TIMEFRAME) == ("scalp-v1", "M1") and s.LADDER_ENABLED
    assert s.CLAUDE_EXECUTE_ON == "VETO" and not s.CLAUDE_REQUIRED and s.CLAUDE_REVIEW_EFFORT == "medium"
    assert "H4" in s.CHART_TIMEFRAMES


def test_smc_aggressive_mode_lowers_bar():
    from app import smc
    m1 = [_b(i * 60, 200, 200.5, 199.5, 200.1) for i in range(300)]
    s = smc.analyze(m1, {"M15": m1, "H1": m1}, "M1", 0.3, now_ts=22 * 3600, mode="aggressive")
    assert s["mode"] == "aggressive" and not s["blocks"] and any("Rollover" in w for w in s["warnings"])


# ---- basket / grid experiment (app/basket.py, profile gold_basket) ------------------------------------------------------
def test_gold_basket_profile():
    s = Settings(_env_file=None, PROFILE="gold_basket")
    assert s.BASKET_ENABLED and not s.LADDER_ENABLED and s.MAX_POSITIONS_PER_SYMBOL == 8 and s.CLAUDE_ENABLED
    assert s.CLAUDE_MAX_LOT == 0.08 and s.BASKET_MAX_POSITIONS_CLAUDE == 8
    assert s.BASKET_ENTRY_MODE == "mtf" and s.BASKET_MAX_POSITIONS == 3 and s.BASKET_ENTRY_VOTE == "short"
    assert s.BASKET_MAX_LOSS_PCT == 0.0 and s.BASKET_SL_DISTANCE == 40.0
    assert s.BASKET_RECOVERY_ENABLED and s.BASKET_RECOVERY_TRIGGER_USD == 2.0 and s.BASKET_RECOVERY_MAX_LOSS_USD == 40.0
    assert (s.BASKET_START_POSITIONS, s.BASKET_STEP_USD, s.BASKET_TP_PER_POSITION_USD) == (5, 0.5, 0.8)
    assert s.BASKET_POSITION_TP_USD == 0.8


def test_basket_opens_adds_and_closes(engine):
    import time as _t

    from tests.conftest import quote
    eng = engine
    eng.settings = eng.settings.model_copy(update={"BASKET_ENABLED": True, "BASKET_STEP_USD": 0.0005,
                                                   "BASKET_TP_USD": 50.0, "BASKET_MAX_LOSS_USD": 1000.0,
                                                   "BASKET_SL_DISTANCE": 0.01, "BASKET_MOMENTUM_BARS": 5})
    eng.rt.risk.update(max_open_positions=5, max_positions_per_symbol=5, pyramid_min_r=-1000.0,
                       pyramid_min_distance_atr=0.0, max_total_open_risk_pct=100.0, max_trades_per_day=1000)
    eng.rt.trading.update(mode="PAPER", execution_enabled=True)
    now = _t.time()
    eng.rt.ingest_quote("EURUSD", quote(bid=1.08000))
    eng.rt.bars[("EURUSD", "M1")] = [{"t": int(now // 60 * 60) - 60 * (20 - k), "o": 1.08, "h": 1.0802, "l": 1.0798,
                                      "c": 1.0800 + k * 0.00001} for k in range(20)]
    eng.basket.tick(now)
    assert len(eng.paper.positions) == 1 and next(iter(eng.paper.positions.values()))["side"] == "BUY"
    eng.rt.ingest_quote("EURUSD", quote(bid=1.07900))  # 10 pips against > step -> one more
    eng.paper.on_quote("EURUSD", eng.rt.quotes["EURUSD"])
    eng.basket.tick(now + 5)
    assert len(eng.paper.positions) == 2
    eng.settings = eng.settings.model_copy(update={"BASKET_MAX_LOSS_USD": 1.0})  # loss now beyond the stop
    eng.basket.tick(now + 10)
    assert not eng.paper.positions


def test_basket_fills_start_positions_and_per_position_tp(engine):
    import time as _t

    from tests.conftest import quote
    eng = engine
    eng.settings = eng.settings.model_copy(update={"BASKET_ENABLED": True, "BASKET_START_POSITIONS": 3,
                                                   "BASKET_STEP_USD": 1.0, "BASKET_TP_PER_POSITION_USD": 1.0,
                                                   "BASKET_MAX_LOSS_USD": 1000.0, "BASKET_SL_DISTANCE": 0.01,
                                                   "BASKET_MOMENTUM_BARS": 5})
    eng.rt.risk.update(max_open_positions=8, max_positions_per_symbol=8, pyramid_min_r=-1000.0,
                       pyramid_min_distance_atr=0.0, max_total_open_risk_pct=100.0, max_trades_per_day=1000)
    eng.rt.trading.update(mode="PAPER", execution_enabled=True)
    now = _t.time()
    eng.rt.ingest_quote("EURUSD", quote(bid=1.08000))
    eng.rt.bars[("EURUSD", "M1")] = [{"t": int(now // 60 * 60) - 60 * (20 - k), "o": 1.08, "h": 1.0802, "l": 1.0798,
                                      "c": 1.0800 + k * 0.00001} for k in range(20)]
    for k in range(4):
        eng.basket.tick(now + 3 * k)
    assert len(eng.paper.positions) == 3  # filled to the start count, one per tick
    assert eng.basket.status("EURUSD")["target"] == round(3 * sum(p["volume"] for p in eng.paper.positions.values())
                                                         / 0.01 / 3, 2)


def test_basket_lot_and_stop_scale_with_balance(engine):
    eng = engine
    eng.settings = eng.settings.model_copy(update={"BASKET_ENABLED": True, "BASKET_LOT_PER_USD": 150.0,
                                                   "BASKET_MAX_LOSS_PCT": 30.0})
    eng.rt.risk.update(max_volume_lots=1.0)
    eng.rt.trading.update(mode="PAPER")
    q = {"volume_step": 0.01, "volume_min": 0.01}
    bal = eng.basket.balance()
    assert eng.basket.lot(q) == round(max(0.01, (bal // 150) * 0.01), 2)
    assert eng.basket.stop_usd({"balance0": 200.0}) == 60.0
    eng.paper.balance = 460.0
    assert eng.basket.lot(q) == 0.03


def test_basket_position_broker_tp(engine):
    import time as _t
    eng = engine
    eng.settings = eng.settings.model_copy(update={"BASKET_ENABLED": True, "BASKET_POSITION_TP_USD": 0.8,
                                                   "BASKET_SL_DISTANCE": 30.0})
    eng.rt.trading.update(mode="PAPER", execution_enabled=True)
    q = {**GOLD, "contract_size": 100.0, "bid": 4000.0, "ask": 4000.3, "symbol": "XAUUSD"}
    seen = {}
    eng.risk_check = lambda sym, side, entry, sl, tp, mode=None: seen.update(sl=sl, tp=tp) or {"passed": False,
                                                                                                "checks": [], "volume": 0.01}
    eng.basket._open("XAUUSD", "BUY", q, "PAPER", _t.time(), "test")
    assert round(seen["tp"] - 4000.3, 2) == 0.8 and round(4000.3 - seen["sl"], 2) == 30.0


def test_basket_pauses_after_broker_refusal(engine):
    import time as _t
    eng = engine
    eng.settings = eng.settings.model_copy(update={"BASKET_ENABLED": True})
    eng.basket.state["XAUUSD"] = {"filling": True}
    eng.basket.on_open_failed({"kind": "OPEN", "symbol": "XAUUSD", "state": "FAILED",
                               "result": {"retcode": 10019, "retcode_desc": "NO_MONEY"}})
    st = eng.basket.state["XAUUSD"]
    assert st["filling"] is False and st["open_pause_until"] > _t.time() + 50


def test_basket_fixes_tp_to_fill_price(engine):
    eng = engine
    eng.settings = eng.settings.model_copy(update={"BASKET_ENABLED": True, "BASKET_POSITION_TP_USD": 0.8,
                                                   "BASKET_SL_DISTANCE": 30.0})
    q = {**GOLD, "contract_size": 100.0, "bid": 4026.40, "ask": 4026.70}
    sent = []
    eng.executor.submit_modify = lambda *a, **k: sent.append((a, k))
    eng.executor.has_pending_for_ticket = lambda *a, **k: False
    pos = [{"ticket": 1, "side": "BUY", "price_open": 4026.35, "tp": 4026.82, "sl": 3996.35, "source": "MT5"}]
    assert eng.basket._fix_tp("XAUUSD", pos, q) is True and sent[0][0][5] == 4027.15
    pos[0]["tp"] = 4027.15
    assert eng.basket._fix_tp("XAUUSD", pos, q) is False


def test_basket_mtf_one_position_per_bar_and_reanalysis(engine):
    import time as _t

    from tests.conftest import quote
    eng = engine
    eng.settings = eng.settings.model_copy(update={"BASKET_ENABLED": True, "BASKET_ENTRY_MODE": "mtf",
                                                   "BASKET_MTF_MIN_VOTES": 3, "BASKET_MAX_POSITIONS": 3,
                                                   "BASKET_MAX_LOSS_USD": 1000.0, "BASKET_SL_DISTANCE": 0.01,
                                                   "BASKET_POSITION_TP_USD": 0.0})
    eng.rt.risk.update(max_open_positions=8, max_positions_per_symbol=8, pyramid_min_r=-1000.0,
                       pyramid_min_distance_atr=0.0, max_total_open_risk_pct=100.0, max_trades_per_day=1000)
    eng.rt.trading.update(mode="PAPER", execution_enabled=True)
    now = _t.time()
    eng.rt.ingest_quote("EURUSD", quote(bid=1.08000))
    for tf, sec in (("M1", 60), ("M5", 300), ("M15", 900), ("H1", 3600)):  # rising everywhere -> BUY consensus
        end = int(now // sec * sec) - sec
        eng.rt.bars[("EURUSD", tf)] = [{"t": end - sec * (99 - k), "o": 1.07 + k * 1e-4, "h": 1.0701 + k * 1e-4,
                                        "l": 1.0699 + k * 1e-4, "c": 1.07 + k * 1e-4} for k in range(100)]
    eng.basket.tick(now)
    eng.basket.tick(now + 5)  # same bar: no second decision
    assert len(eng.paper.positions) == 1 and next(iter(eng.paper.positions.values()))["side"] == "BUY"
    side, votes = eng.basket.mtf_vote("EURUSD", now)
    assert side == "BUY" and set(votes.values()) == {1}


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


def test_basket_recovery_hedge_then_exit_at_zero(engine):
    import time as _t

    from tests.conftest import quote
    eng = engine
    eng.settings = eng.settings.model_copy(update={
        "BASKET_ENABLED": True, "BASKET_ENTRY_MODE": "mtf", "BASKET_MAX_POSITIONS": 3, "BASKET_SL_DISTANCE": 0.05,
        "BASKET_POSITION_TP_USD": 0.0, "BASKET_RECOVERY_ENABLED": True, "BASKET_RECOVERY_TRIGGER_USD": 0.3,
        "BASKET_RECOVERY_POSITIONS": 2, "BASKET_RECOVERY_EXIT_USD": 0.1, "BASKET_RECOVERY_MAX_LOSS_USD": 1000.0})
    eng.rt.risk.update(max_open_positions=8, max_positions_per_symbol=8, pyramid_min_r=-1000.0,
                       pyramid_min_distance_atr=0.0, max_total_open_risk_pct=1000.0, max_trades_per_day=1000,
                       max_same_currency_direction=99, min_free_margin_pct=0, fixed_volume_lots=0.01)
    eng.rt.trading.update(mode="PAPER", execution_enabled=True)
    now = _t.time()
    eng.rt.ingest_quote("EURUSD", quote(bid=1.08000))
    eng.rt.bars[("EURUSD", "M1")] = [{"t": int(now // 60 * 60) - 60 * (5 - k), "o": 1.08, "h": 1.08, "l": 1.08,
                                      "c": 1.08} for k in range(5)]
    eng.paper.open("EURUSD", "BUY", 0.01, 1.0301, 1.09, eng.rt.quotes["EURUSD"])  # SL = fill - 0.05
    eng.rt.ingest_quote("EURUSD", quote(bid=1.07960))  # BUY 0.01 now about -0.5 USD (trigger 0.3 in this test)
    eng.paper.on_quote("EURUSD", eng.rt.quotes["EURUSD"])
    eng.rt.risk.update(allow_hedge=True)
    eng.basket.fast_vote = lambda sym, now: (None, {"M1": -1, "M5": 1})  # M5 still up: M1 alone decides now
    eng.basket.tick(now)
    assert len(eng.paper.positions) == 1  # just started: no turn against yet, nothing opens
    eng.rt.ingest_quote("EURUSD", quote(bid=1.07900))  # BUY worsens again by 0.6 from its best -> hedge now
    eng.paper.on_quote("EURUSD", eng.rt.quotes["EURUSD"])
    for k in range(1, 4):
        eng.basket.tick(now + 3 * k)
    sides = sorted(p["side"] for p in eng.paper.positions.values())
    assert sides == ["BUY", "SELL", "SELL"] and eng.basket.status("EURUSD")["recovery"]
    eng.rt.ingest_quote("EURUSD", quote(bid=1.07700))  # price keeps falling: hedges outweigh the loser
    eng.paper.on_quote("EURUSD", eng.rt.quotes["EURUSD"])
    eng.basket.tick(now + 20)
    assert not eng.paper.positions  # whole group closed once the combined net is >= +0.10


def test_basket_short_term_vote_ignores_h1(engine):
    import time as _t

    from tests.conftest import quote
    eng = engine
    eng.settings = eng.settings.model_copy(update={"BASKET_ENABLED": True, "BASKET_ENTRY_MODE": "mtf",
                                                   "BASKET_ENTRY_VOTE": "short", "BASKET_MAX_POSITIONS": 3,
                                                   "BASKET_SL_DISTANCE": 0.01, "BASKET_POSITION_TP_USD": 0.0,
                                                   "BASKET_MAX_LOSS_USD": 1000.0})
    eng.rt.risk.update(max_open_positions=8, max_positions_per_symbol=8, pyramid_min_r=-1000.0,
                       pyramid_min_distance_atr=0.0, max_total_open_risk_pct=1000.0, max_trades_per_day=1000,
                       fixed_volume_lots=0.01)
    eng.rt.trading.update(mode="PAPER", execution_enabled=True)
    now = _t.time()
    eng.rt.ingest_quote("EURUSD", quote(bid=1.08000))
    for tf, sec, step in (("M1", 60, 1e-4), ("M5", 300, 1e-4), ("M15", 900, -1e-4), ("H1", 3600, -1e-4)):
        end = int(now // sec * sec) - sec  # short term rising, M15/H1 falling
        eng.rt.bars[("EURUSD", tf)] = [{"t": end - sec * (99 - k), "o": 1.07 + k * step, "h": 1.0701 + k * step,
                                        "l": 1.0699 + k * step, "c": 1.07 + k * step} for k in range(100)]
    eng.basket.tick(now)
    assert next(iter(eng.paper.positions.values()))["side"] == "BUY"  # short term decides
    assert "context (nu decide)" in eng.basket.status("EURUSD")["analysis"]


def test_basket_recovery_opens_new_rounds(engine):
    import time as _t

    from tests.conftest import quote
    eng = engine
    eng.settings = eng.settings.model_copy(update={
        "BASKET_ENABLED": True, "BASKET_ENTRY_MODE": "mtf", "BASKET_MAX_POSITIONS": 3, "BASKET_SL_DISTANCE": 0.05,
        "BASKET_POSITION_TP_USD": 0.0, "BASKET_RECOVERY_ENABLED": True, "BASKET_RECOVERY_TRIGGER_USD": 0.3,
        "BASKET_RECOVERY_POSITIONS": 2, "BASKET_RECOVERY_EXIT_USD": 0.1, "BASKET_RECOVERY_MAX_LOSS_USD": 1000.0})
    eng.rt.risk.update(max_open_positions=8, max_positions_per_symbol=8, pyramid_min_r=-1000.0,
                       pyramid_min_distance_atr=0.0, max_total_open_risk_pct=1000.0, max_trades_per_day=1000,
                       max_same_currency_direction=99, min_free_margin_pct=0, fixed_volume_lots=0.01, allow_hedge=True)
    eng.rt.trading.update(mode="PAPER", execution_enabled=True)
    now = _t.time()
    eng.rt.ingest_quote("EURUSD", quote(bid=1.08000))
    eng.paper.open("EURUSD", "BUY", 0.01, 1.0301, 1.09, eng.rt.quotes["EURUSD"])  # SL = fill - 0.05
    eng.paper.open("EURUSD", "BUY", 0.01, 1.0301, 1.09, eng.rt.quotes["EURUSD"])  # SL = fill - 0.05
    eng.rt.ingest_quote("EURUSD", quote(bid=1.07960))
    eng.paper.on_quote("EURUSD", eng.rt.quotes["EURUSD"])
    eng.basket.fast_vote = lambda sym, now: ("SELL", {"M1": -1, "M5": -1})
    def move(bid):
        eng.rt.ingest_quote("EURUSD", quote(bid=bid))
        eng.paper.on_quote("EURUSD", eng.rt.quotes["EURUSD"])
    eng.basket.tick(now)
    move(1.07920)  # worsens again by 0.8 -> round 1
    for k in range(1, 4):
        eng.basket.tick(now + 3 * k)
    assert sorted(p["side"] for p in eng.paper.positions.values()) == ["BUY", "BUY", "SELL", "SELL"]
    for tk in [t for t, p in eng.paper.positions.items() if p["side"] == "SELL"]:
        eng.paper.positions.pop(tk)  # the hedges took their TP; the two BUY are still under water
    move(1.07990)  # the BUY come back towards their TP: wait, nothing opens
    eng.basket.tick(now + 20)
    assert sorted(p["side"] for p in eng.paper.positions.values()) == ["BUY", "BUY"]
    move(1.07950)  # and turn against again by 0.8 from that best -> round 2 at once
    for k in range(1, 4):
        eng.basket.tick(now + 20 + 3 * k)
    assert sorted(p["side"] for p in eng.paper.positions.values()) == ["BUY", "BUY", "SELL", "SELL"]
    assert eng.basket.status("EURUSD")["rounds"] == 2


# ---- Positions page: clear history buttons ------------------------------------------------------------------------------
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


def test_basket_moves_sl_to_new_distance(engine):
    eng = engine
    eng.settings = eng.settings.model_copy(update={"BASKET_ENABLED": True, "BASKET_POSITION_TP_USD": 0.8,
                                                   "BASKET_SL_DISTANCE": 40.0})
    q = {**GOLD, "contract_size": 100.0, "bid": 4000.0, "ask": 4000.3}
    sent = []
    eng.executor.submit_modify = lambda *a, **k: sent.append(a)
    eng.executor.has_pending_for_ticket = lambda *a, **k: False
    pos = [{"ticket": 7, "side": "SELL", "price_open": 4000.5, "tp": 3999.7, "sl": 4015.5, "source": "MT5"}]
    assert eng.basket._fix_tp("XAUUSD", pos, q) is True
    assert sent[0][4] == 4040.5 and sent[0][5] == 3999.7  # SL widened to 40, TP untouched


def test_basket_limit_api(engine, client):
    from tests.conftest import ADMIN
    eng = engine
    eng.settings = eng.settings.model_copy(update={"BASKET_ENABLED": True, "BASKET_RECOVERY_ENABLED": True,
                                                   "BASKET_RECOVERY_MAX_LOSS_USD": 40.0, "BASKET_SL_DISTANCE": 40.0})
    assert client.get("/api/v1/basket").json()["max_loss_usd"] == 40.0
    assert client.put("/api/v1/basket", json={"max_loss_usd": 60}).status_code == 401
    assert client.put("/api/v1/basket", json={"max_loss_usd": 0}, headers=ADMIN).status_code == 422
    r = client.put("/api/v1/basket", json={"max_loss_usd": 60}, headers=ADMIN)
    assert r.status_code == 200 and r.json()["max_loss_usd"] == 60.0 and r.json()["position_sl_usd"] == 60.0
    c = eng.basket.cfg()
    assert c["rec_cap"] == 60.0 and c["stop"] == 60.0 and c["sl_dist"] == 60.0
    client.put("/api/v1/basket", json={"max_loss_usd": 25}, headers=ADMIN)
    assert eng.basket.cfg()["sl_dist"] == 40.0  # never tighter than the profile's broker SL


# ---- Strategy 2: zone recovery with lot doubling ---------------------------------------------------------------------
def _zr_engine(engine):
    import time as _t

    from tests.conftest import quote
    eng = engine
    eng.settings = eng.settings.model_copy(update={
        "BASKET_ENABLED": True, "BASKET_SL_DISTANCE": 0.05, "BASKET_POSITION_TP_USD": 0.0,
        "BASKET_ZR_ZONE_USD": 0.002, "BASKET_ZR_MULT": 2.0, "BASKET_ZR_MAX_LEVELS": 4, "BASKET_ZR_EXIT_USD": 0.1,
        "BASKET_RECOVERY_ENABLED": True, "BASKET_RECOVERY_MAX_LOSS_USD": 1000.0})
    eng.rt.risk.update(max_open_positions=8, max_positions_per_symbol=8, pyramid_min_r=-1000.0,
                       pyramid_min_distance_atr=0.0, max_total_open_risk_pct=1000.0, max_trades_per_day=1000,
                       max_same_currency_direction=99, min_free_margin_pct=0, fixed_volume_lots=0.01, allow_hedge=True,
                       max_volume_lots=1.0)
    eng.db.set_setting("basket", {"strategy": "s2"})
    eng.rt.trading.update(mode="PAPER", execution_enabled=True)
    eng.rt.ingest_quote("EURUSD", quote(bid=1.08000))
    return eng, _t.time(), quote


def test_zr_doubles_lot_on_zone_cross_and_closes_group(engine):
    eng, now, quote = _zr_engine(engine)
    eng.paper.open("EURUSD", "BUY", 0.01, 1.0301, 1.1301, eng.rt.quotes["EURUSD"])  # first level
    def move(bid):
        eng.rt.ingest_quote("EURUSD", quote(bid=bid))
        eng.paper.on_quote("EURUSD", eng.rt.quotes["EURUSD"])
    move(1.07800)  # 2.1 pips below the BUY at ask 1.0801 -> zone (0.002) crossed
    eng.basket.tick(now)
    sides = sorted((p["side"], p["volume"]) for p in eng.paper.positions.values())
    assert sides == [("BUY", 0.01), ("SELL", 0.02)]
    move(1.07500)  # SELL 0.02 now gains more than the BUY 0.01 loses -> group net >= +0.1
    for k in range(1, 5):
        eng.basket.tick(now + 3 * k)
    assert not eng.paper.positions


def test_strategy_switch_needs_stopped_bot(engine, client):
    from tests.conftest import ADMIN
    eng = engine
    eng.settings = eng.settings.model_copy(update={"BASKET_ENABLED": True})
    eng.rt.trading.update(mode="DEMO", execution_enabled=True, entries_paused=False)
    assert client.put("/api/v1/basket", json={"strategy": "s2"}, headers=ADMIN).status_code == 422
    eng.rt.trading.update(entries_paused=True)
    r = client.put("/api/v1/basket", json={"strategy": "s2"}, headers=ADMIN)
    assert r.status_code == 200 and r.json()["strategy"] == "s2" and r.json()["running"] is False
    assert client.get("/api/v1/basket").json()["strategy"] == "s2"


# ---- Claude basket manager ----------------------------------------------------------------------------------------------
def test_claude_manager_applies_actions_with_limits(engine):
    import asyncio
    import time as _t

    from tests.conftest import quote
    eng = engine
    eng.settings = eng.settings.model_copy(update={"BASKET_ENABLED": True, "CLAUDE_MAX_LOT": 0.08,
                                                   "BASKET_MAX_POSITIONS_CLAUDE": 8, "BASKET_SL_DISTANCE": 0.05})
    eng.rt.risk.update(max_open_positions=8, max_positions_per_symbol=8, pyramid_min_r=-1000.0,
                       pyramid_min_distance_atr=0.0, max_total_open_risk_pct=1000.0, max_trades_per_day=1000,
                       max_same_currency_direction=99, min_free_margin_pct=0, fixed_volume_lots=0.01, allow_hedge=True,
                       max_volume_lots=1.0)
    eng.db.set_setting("basket", {"claude": True})
    eng.rt.trading.update(mode="PAPER", execution_enabled=True)
    eng.rt.ingest_quote("EURUSD", quote(bid=1.08000))

    async def fake(payload):
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
    eng.settings = eng.settings.model_copy(update={"BASKET_ENABLED": True, "BASKET_RECOVERY_ENABLED": True,
                                                   "BASKET_RECOVERY_MAX_LOSS_USD": 0.2})
    eng.db.set_setting("basket", {"claude": True})
    eng.rt.trading.update(mode="PAPER", execution_enabled=True)
    eng.rt.ingest_quote("EURUSD", quote(bid=1.08000))
    eng.paper.open("EURUSD", "BUY", 0.01, 1.07, 1.09, eng.rt.quotes["EURUSD"])
    eng.rt.ingest_quote("EURUSD", quote(bid=1.07960))
    eng.paper.on_quote("EURUSD", eng.rt.quotes["EURUSD"])
    called = []
    eng.ai.decide_basket = lambda payload: called.append(1)
    eng.basket.tick(_t.time())
    assert not eng.paper.positions and not called
