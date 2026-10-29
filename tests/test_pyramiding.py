"""Opt-in pyramiding: risk.max_positions_per_symbol > 1 allows further bot positions on one symbol, same direction only,
only while every open position on it is >= pyramid_min_r in open profit; default (1) keeps the one-position rule."""
from app import risk as risk_mod
from app.strategy import AnalysisContext, context_blockers, pyramid_blocker
from tests.conftest import quote
from tests.test_engine import analysis_stub


def test_pyramid_blocker_rules():
    assert pyramid_blocker("BUY", None, 3, 0.5) is None
    assert pyramid_blocker("BUY", [], 3, 0.5) is None
    assert "Limita" in pyramid_blocker("BUY", [{"side": "BUY", "open_r": 1.0}], 1, 0.5)
    assert "opusă" in pyramid_blocker("SELL", [{"side": "BUY", "open_r": 1.0}], 3, 0.5)
    assert "în curs" in pyramid_blocker("BUY", [{"side": None, "open_r": None}], 3, 0.5)
    assert "profit" in pyramid_blocker("BUY", [{"side": "BUY", "open_r": 0.2}], 3, 0.5)
    assert pyramid_blocker("BUY", [{"side": "BUY", "open_r": 0.6}, {"side": "BUY", "open_r": 2.0}], 3, 0.5) is None


def test_context_blockers_default_and_pyramid():
    risk = {"max_positions_per_symbol": 1}
    ctx = AnalysisContext(has_bot_position=True, symbol_positions=[{"side": "BUY", "open_r": 2.0}])
    assert any("deja o poziție" in b for b in context_blockers("XAUUSD", "BUY", ctx, risk))
    risk = {"max_positions_per_symbol": 3, "pyramid_min_r": 0.5}
    # backtest / forward callers pass no symbol_positions -> the old rule stays (they do not simulate pyramiding)
    ctx = AnalysisContext(has_bot_position=True, symbol_positions=None)
    assert any("deja o poziție" in b for b in context_blockers("XAUUSD", "BUY", ctx, risk))
    ctx = AnalysisContext(has_bot_position=True, symbol_positions=[{"side": "BUY", "open_r": 0.8}],
                          open_exposures=[("XAUUSD", "BUY")], corr_limit=1)
    assert context_blockers("XAUUSD", "BUY", ctx, risk) == []  # same-symbol same-side exposure is the pyramid itself
    assert any("opusă" in b for b in context_blockers("XAUUSD", "SELL", ctx, risk))
    ctx = AnalysisContext(has_bot_position=True, symbol_positions=[{"side": "BUY", "open_r": 0.1}])
    assert any("profit" in b for b in context_blockers("XAUUSD", "BUY", ctx, risk))


def _rc(**kw):
    base = dict(mode="PAPER", execution_enabled=True, entries_paused=False, side="BUY", entry=1.0801, sl=1.0795, tp=1.0811,
                symbol_info={**quote(bid=1.08), "symbol": "EURUSD"}, account={"equity": 10000, "balance": 10000, "margin_free": 10000},
                risk={"max_risk_per_trade_pct": 0.25, "max_open_positions": 5, "max_spread_points": 30}, symbol="EURUSD")
    base.update(kw)
    return risk_mod.RiskContext(**base)


def _check(rc, name):
    return next(c for c in rc["checks"] if c["name"] == name)


def test_risk_engine_pyramid_check():
    r = risk_mod.evaluate(_rc(has_bot_position_on_symbol=True))
    assert not _check(r, "no_existing_bot_position_on_symbol")["ok"]
    rk = {"max_risk_per_trade_pct": 0.25, "max_open_positions": 5, "max_spread_points": 30, "max_positions_per_symbol": 3}
    r = risk_mod.evaluate(_rc(has_bot_position_on_symbol=True, risk=rk, pyramid_ok=False, pyramid_note="sub 0.5R"))
    c = _check(r, "no_existing_bot_position_on_symbol")
    assert not c["ok"] and "0.5R" in c["detail"]
    r = risk_mod.evaluate(_rc(has_bot_position_on_symbol=True, risk=rk, pyramid_ok=True, pyramid_note="ok",
                              open_exposures=[("EURUSD", "BUY")]))
    assert _check(r, "no_existing_bot_position_on_symbol")["ok"] and _check(r, "currency_exposure_ok")["ok"]


def test_engine_paper_pyramiding_flow(engine):
    engine.rt.trading.update(mode="PAPER", execution_enabled=True)
    engine.rt.risk.update({"max_open_positions": 5, "max_trades_per_day": 20, "max_signals_per_symbol_per_session": 20})
    engine.rt.ingest_quote("EURUSD", quote(bid=1.08000))
    s1 = engine.create_signal(analysis_stub(bar_time=1730000000))
    engine.try_execute(s1)
    assert engine.get_signal(s1["id"])["status"] == "EXECUTED"
    # default: one position per symbol
    s2 = engine.create_signal(analysis_stub(bar_time=1730000300))
    engine.try_execute(s2)
    assert "deja o poziție" in engine.get_signal(s2["id"])["status_reason"]
    engine.rt.risk.update({"max_positions_per_symbol": 3, "pyramid_min_r": 0.5})
    assert engine.symbol_positions("EURUSD")[0]["open_r"] is not None
    # not enough open profit yet (entry 1.0801, SL 1.0795 -> risk 0.0006; price 1.0800 bid)
    s3 = engine.create_signal(analysis_stub(bar_time=1730000600))
    engine.try_execute(s3)
    assert "profit" in engine.get_signal(s3["id"])["status_reason"]
    # price moves +1R: a second BUY is allowed; a SELL is not
    engine.rt.ingest_quote("EURUSD", quote(bid=1.08070))
    assert engine.symbol_positions("EURUSD")[0]["open_r"] >= 0.5
    sell = engine.create_signal({**analysis_stub("SELL", bar_time=1730000900), "entry": 1.0807, "stop_loss": 1.0813,
                                 "take_profit": 1.0797})
    engine.try_execute(sell)
    assert "opusă" in engine.get_signal(sell["id"])["status_reason"]
    s4 = engine.create_signal({**analysis_stub(bar_time=1730001200), "entry": 1.0808, "stop_loss": 1.0802,
                               "take_profit": 1.0818})
    engine.try_execute(s4)
    assert engine.get_signal(s4["id"])["status"] == "EXECUTED", engine.get_signal(s4["id"])["status_reason"]
    assert len(engine.paper.positions) == 2
    # the analysis context reports the book to the strategy's context blockers
    ctx_positions = engine.symbol_positions("EURUSD", "ALL")
    assert len(ctx_positions) == 2 and all(p["side"] == "BUY" for p in ctx_positions)
