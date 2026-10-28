import pytest

from app import indicators as ind


def test_ema_constant_and_seed():
    v = [2.0] * 30
    e = ind.ema(v, 10)
    assert e[8] is None and e[9] == pytest.approx(2.0) and e[-1] == pytest.approx(2.0)


def test_ema_known_values():
    e = ind.ema([1, 2, 3, 4, 5], 3)
    # seed = sma(1,2,3)=2; k=0.5 -> 3, 4
    assert e[2] == pytest.approx(2.0) and e[3] == pytest.approx(3.0) and e[4] == pytest.approx(4.0)


def test_rsi_extremes_and_balance():
    assert ind.rsi(list(range(1, 40)), 14)[-1] == pytest.approx(100.0)
    assert ind.rsi(list(range(40, 1, -1)), 14)[-1] == pytest.approx(0.0)
    zz = [1.0 + (0.01 if i % 2 else 0) for i in range(60)]
    assert 40 < ind.rsi(zz, 14)[-1] < 60


def test_atr_constant_range():
    h = [1.1] * 30
    l = [1.0] * 30
    c = [1.05] * 30
    a = ind.atr(h, l, c, 14)
    assert a[12] is None and a[-1] == pytest.approx(0.1)


def test_macd_constant_is_zero():
    line, sig, hist = ind.macd([1.5] * 60)
    assert hist[-1] == pytest.approx(0.0) and line[-1] == pytest.approx(0.0)


def test_macd_uptrend_positive():
    line, sig, hist = ind.macd([1 + i * 0.001 + (i * 0.00002) ** 2 for i in range(100)])
    assert line[-1] > 0


def test_bollinger_constant():
    mid, up, lo = ind.bollinger([3.0] * 25, 20, 2)
    assert mid[-1] == up[-1] == lo[-1] == pytest.approx(3.0)


def test_median():
    assert ind.median([3, 1, 2]) == 2
    assert ind.median([4, 1, 2, 3]) == 2.5
    assert ind.median([]) is None
