import pytest

from app.config import Settings
from app.risk import RiskContext, calc_volume, evaluate, floor_to_step
from tests.conftest import EURUSD_INFO, quote

RISK = Settings(_env_file=None).default_risk()


def base_ctx(**kw):
    acc = {"equity": 10000.0, "balance": 10000.0, "margin_free": 9900.0}
    d = dict(mode="PAPER", execution_enabled=True, entries_paused=False, side="BUY", entry=1.08010, sl=1.07910,
             tp=1.08260, symbol_info=quote(bid=1.08000), account=acc, risk=dict(RISK), tick_age=0.5,
             day_start_equity=10000.0, peak_equity=10000.0)
    d.update(kw)
    return RiskContext(**d)


def check(rc, name):
    return next(c for c in rc["checks"] if c["name"] == name)


def test_floor_to_step():
    assert floor_to_step(0.257, 0.01) == 0.25
    assert floor_to_step(1.0, 0.01) == 1.0
    assert floor_to_step(0.0099, 0.01) == 0.0


def test_volume_sizing():
    # 0.25% of 10000 = 25; SL 100 points * 1.0 per lot-point -> 100 per lot -> 0.25 lots
    assert calc_volume(10000, 0.25, 0.00100, EURUSD_INFO) == pytest.approx(0.25)
    assert calc_volume(10000, 0.25, 0.00300, EURUSD_INFO) == pytest.approx(0.08)
    assert calc_volume(10000, 0.25, 0, EURUSD_INFO) == 0.0


def test_all_pass_baseline():
    rc = evaluate(base_ctx())
    assert rc["passed"], [c for c in rc["checks"] if not c["ok"]]
    assert rc["volume"] == pytest.approx(0.25)


@pytest.mark.parametrize("kw,name", [
    (dict(mode="SIGNAL_ONLY"), "mode_allows_execution"),
    (dict(execution_enabled=False), "mode_allows_execution"),
    (dict(entries_paused=True), "entries_not_paused"),
    (dict(tick_age=30.0), "quote_fresh"),
    (dict(tick_age=None), "quote_fresh"),
    (dict(symbol_info=quote(bid=1.08, spread_pts=50)), "spread_ok"),
    (dict(sl=None), "sl_tp_present"),
    (dict(tp=None), "sl_tp_present"),
    (dict(sl=1.0811), "sl_tp_present"),
    (dict(symbol_info=quote(bid=1.08, stops_level=500)), "sl_distance_ok"),
    (dict(symbol_info=quote(bid=1.08, tick_value=0)), "volume_ok"),
    (dict(sl=1.0701, tp=1.1100, account={"equity": 100.0, "margin_free": 100.0}), "volume_ok"),
    (dict(open_bot_positions=1), "max_open_positions"),
    (dict(trades_today=5), "max_trades_per_day"),
    (dict(account={"equity": 9850.0, "margin_free": 9800.0}), "daily_loss_ok"),
    (dict(peak_equity=10400.0, day_start_equity=9800.0, account={"equity": 9790.0, "margin_free": 9700.0}), "drawdown_ok"),
    (dict(consecutive_losses=3), "consecutive_losses_ok"),
    (dict(account={"equity": 10000.0, "margin_free": 4000.0}), "free_margin_ok"),
    (dict(has_bot_position_on_symbol=True), "no_existing_bot_position_on_symbol"),
])
def test_each_blocking_check(kw, name):
    rc = evaluate(base_ctx(**kw))
    assert not rc["passed"]
    assert check(rc, name)["ok"] is False


def test_demo_specific_checks():
    rc = evaluate(base_ctx(mode="DEMO", bridge_connected=False, mt5_account={"trade_mode": 2}, terminal={"trade_allowed": False}))
    for n in ("bridge_connected", "terminal_trade_allowed"):
        assert check(rc, n)["ok"] is False
    for tm in (0, 2):  # demo and real accounts alike
        rc = evaluate(base_ctx(mode="DEMO", bridge_connected=True, mt5_account={"trade_mode": tm, "trade_expert": True},
                               terminal={"trade_allowed": True}))
        assert rc["passed"], [c for c in rc["checks"] if not c["ok"]]


def test_test_order_uses_min_volume_and_2x_limit():
    rc = evaluate(base_ctx(test_order=True))
    assert rc["volume"] == 0.01 and rc["passed"]
    # huge SL: 0.01 lot * 10000 points = 100 > 2*0.25% of 10000 = 50
    rc = evaluate(base_ctx(test_order=True, sl=0.98010, tp=1.2))
    assert check(rc, "volume_ok")["ok"] is False


def test_crossed_quote_blocks():
    info = dict(quote(bid=1.08000), ask=1.07994, spread_points=-6)
    rc = evaluate(base_ctx(symbol_info=info))
    assert not rc["passed"]
    assert not check(rc, "quote_not_crossed")["ok"]
    assert not check(rc, "spread_ok")["ok"]


def test_volume_capped_by_max_volume_lots():
    risk = dict(RISK, max_volume_lots=0.5)
    # SL of 2 points would size a huge lot without the cap
    rc = evaluate(base_ctx(entry=1.08010, sl=1.08008, tp=1.08020, risk=risk))
    assert rc["volume"] <= 0.5


def test_currency_legs_and_conflict():
    from app.risk import currency_conflict, currency_legs
    assert currency_legs("EURUSD", "BUY") == [("EUR", 1), ("USD", -1)]
    assert currency_legs("USDJPY.a", "SELL") == [("USD", -1), ("JPY", 1)]
    assert currency_legs("US30", "BUY") == []
    held = [("EURUSD", "SELL")]  # USD long
    assert currency_conflict("USDJPY", "BUY", held, 1)  # USD long again -> blocked
    assert currency_conflict("GBPUSD", "SELL", held, 1)  # USD long again -> blocked
    assert currency_conflict("GBPUSD", "BUY", held, 1) is None  # USD short: opposite direction
    assert currency_conflict("AUDCAD", "BUY", held, 1) is None  # unrelated currencies
    assert currency_conflict("USDJPY", "BUY", held, 2) is None  # limit 2 allows a second
    assert currency_conflict("USDJPY", "BUY", held, 0) is None  # 0 disables the filter


def test_risk_blocks_correlated_exposure():
    rc = evaluate(base_ctx(symbol="GBPUSD", side="SELL", entry=1.08000, sl=1.08100, tp=1.07750,
                           open_exposures=[("EURUSD", "SELL")]))
    assert not check(rc, "currency_exposure_ok")["ok"] and not rc["passed"]
    rc = evaluate(base_ctx(symbol="EURUSD", open_exposures=[("AUDCAD", "BUY")]))
    assert check(rc, "currency_exposure_ok")["ok"]
