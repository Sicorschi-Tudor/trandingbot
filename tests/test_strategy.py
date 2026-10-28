import time

import pytest

from app.config import Settings
from app.strategy import AnalysisContext, analyze, probabilities
from tests.conftest import make_bars, quote, zigzag

S = Settings(_env_file=None)
CFG = S.default_strategy()
RISK = S.default_risk()


def up_data():
    sig = make_bars(zigzag(150, 0.0003, 0.0002, end_up=True), 300)
    htf = make_bars(zigzag(80, 0.0030, 0.0020, end_up=True), 3600, wick=0.0005)
    last = sig[-1]["c"]
    return sig, htf, quote(bid=last)


def down_data():
    sig = make_bars(zigzag_down(150), 300)
    htf = make_bars(zigzag_down(80, 0.0030, 0.0020), 3600, wick=0.0005)
    return sig, htf, quote(bid=sig[-1]["c"])


def zigzag_down(n, down=0.0003, up=0.0002, start=1.0800):
    closes = [start]
    for i in range(n - 1):
        closes.append(round(closes[-1] + (-down if i % 2 == 0 else up), 5))
    if closes[-1] > closes[-2]:
        closes.append(round(closes[-1] - down, 5))
    return closes


def ctx(**kw):
    base = dict(session_open=True, has_bot_position=False, signals_this_session=0, tick_age=0.5, now_ts=time.time())
    base.update(kw)
    return AnalysisContext(**base)


def test_buy_signal_on_uptrend():
    sig, htf, q = up_data()
    a = analyze("EURUSD", sig, htf, q, CFG, RISK, ctx())
    assert a["direction"] == "BUY", a["reasons"]
    assert a["indicators"]["htf_trend"] == "UP"
    assert a["stop_loss"] < a["entry"] < a["take_profit"]
    assert a["entry"] == q["ask"]
    assert a["rr"] == pytest.approx(2.5 / 1.5, abs=0.01)
    assert a["probabilities"]["up"] > a["probabilities"]["down"]
    assert a["probability_method"].startswith("heuristic-v1")


def test_sell_signal_on_downtrend():
    sig, htf, q = down_data()
    a = analyze("EURUSD", sig, htf, q, CFG, RISK, ctx())
    assert a["direction"] == "SELL", a["reasons"]
    assert a["take_profit"] < a["entry"] < a["stop_loss"]
    assert a["entry"] == q["bid"]
    assert a["score"] < 0


def test_hold_on_range():
    closes = zigzag(150, 0.0002, 0.0002)
    sig = make_bars(closes, 300)
    htf = make_bars(zigzag(80, 0.0010, 0.0010), 3600)
    a = analyze("EURUSD", sig, htf, quote(bid=closes[-1]), CFG, RISK, ctx())
    assert a["direction"] == "HOLD"
    assert a["entry"] is None and a["stop_loss"] is None


def test_hold_insufficient_bars():
    sig, htf, q = up_data()
    a = analyze("EURUSD", sig[:50], htf, q, CFG, RISK, ctx())
    assert a["direction"] == "HOLD" and "insuficiente" in a["reasons"][0]
    a = analyze("EURUSD", sig, htf[:30], q, CFG, RISK, ctx())
    assert a["direction"] == "HOLD"


@pytest.mark.parametrize("kw,needle", [
    (dict(session_open=False), "Sesiunea"),
    (dict(has_bot_position=True), "poziție a botului"),
    (dict(tick_age=60.0), "veche"),
    (dict(signals_this_session=5), "Limita de semnale"),
])
def test_filters_force_hold(kw, needle):
    sig, htf, q = up_data()
    a = analyze("EURUSD", sig, htf, q, CFG, RISK, ctx(**kw))
    assert a["direction"] == "HOLD"
    assert any(needle in r for r in a["reasons"]), a["reasons"]


def test_spread_filter():
    sig, htf, _ = up_data()
    q = quote(bid=sig[-1]["c"], spread_pts=45)
    a = analyze("EURUSD", sig, htf, q, CFG, RISK, ctx())
    assert a["direction"] == "HOLD"
    assert any("Spread" in r for r in a["reasons"])


def test_no_quote_hold():
    sig, htf, _ = up_data()
    assert analyze("EURUSD", sig, htf, None, CFG, RISK, ctx())["direction"] == "HOLD"


def test_probabilities_sum_to_one():
    for s in (-1, -0.6, 0, 0.4, 1):
        p = probabilities(s)
        assert sum(p.values()) == pytest.approx(1.0, abs=1e-3)
    assert probabilities(1.0)["up"] == pytest.approx(0.75)


def test_signal_blocked_by_correlated_exposure():
    sig, htf, q = up_data()
    free = analyze("EURUSD", sig, htf, q, CFG, RISK, ctx())
    assert free["direction"] == "BUY"
    c = ctx()
    c.open_exposures = [("EURGBP", "BUY")]  # already EUR long
    a = analyze("EURUSD", sig, htf, q, CFG, RISK, c)
    assert a["direction"] == "HOLD" and any("Expunere corelată" in r for r in a["reasons"])
