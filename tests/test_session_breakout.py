"""Asian-range breakout (app.session_breakout, 09.11)."""
import time
from datetime import datetime, timezone

from tests.conftest import quote
from tests.test_basket import _claude_basket, _bar


def _engine(engine, **upd):
    eng = _claude_basket(engine, SESSION_BREAKOUT_ENABLED=True, **upd)
    eng.rt.trading.update(mode="PAPER", execution_enabled=True)
    return eng


def _range_day(eng, day: datetime, lo=1.0790, hi=1.0830):
    """M15 bars from 00:00 to 06:45 UTC of `day` oscillating between lo and hi (M1 bars: none - the range must come
    from M15, the runtime keeps only 5 hours of M1)."""
    t0 = int(datetime(day.year, day.month, day.day, 0, tzinfo=timezone.utc).timestamp())
    bars = [_bar(t0 + 900 * i, (lo + hi) / 2, hi if i % 3 == 0 else (lo + hi) / 2 + 0.0005,
                 lo if i % 4 == 0 else (lo + hi) / 2 - 0.0005, (lo + hi) / 2) for i in range(28)]
    eng.rt.closed_bars = lambda sym, tf, now=None, n=None: bars if tf == "M15" else []
    return t0


def test_breakout_opens_once_after_the_range_with_range_sized_exits(engine, monkeypatch):
    eng = _engine(engine, SESSION_BREAKOUT_MIN_RANGE_PCT=0.0, SESSION_BREAKOUT_MAX_RANGE_PCT=10.0,
                  SESSION_BREAKOUT_SL_CAP_PCT=10.0)
    sb = eng.session_bo
    day = datetime(2026, 11, 10, tzinfo=timezone.utc)   # a Tuesday
    t0 = _range_day(eng, day)
    # 04:30 UTC: still inside the range window -> nothing
    eng.rt.ingest_quote("EURUSD", quote(bid=1.0850))
    sb.tick(t0 + 4.5 * 3600)
    assert not eng.paper.positions
    # 06:00 UTC, price inside the range -> armed, no trade
    eng.rt.ingest_quote("EURUSD", quote(bid=1.0810))
    sb.tick(t0 + 6 * 3600)
    assert not eng.paper.positions and sb.days["EURUSD"]["status"] == "armed"
    # 06:05 UTC, ask above the range high -> BUY with SL = range height, TP = 2.5 x
    eng.rt.ingest_quote("EURUSD", quote(bid=1.0840))
    sb.tick(t0 + 6 * 3600 + 300)
    pos = list(eng.paper.positions.values()) if isinstance(eng.paper.positions, dict) else list(eng.paper.positions)
    assert len(pos) == 1
    p = sb.positions("EURUSD")[0]
    assert p["side"] == "BUY" and sb.owns(p) and sb.days["EURUSD"]["status"] == "traded"
    risk = 1.0830 - 1.0790
    assert abs((p["price_open"] - p["sl"]) - risk) < 1e-6 and abs((p["tp"] - p["price_open"]) - 2.5 * risk) < 1e-6
    # the basket does not see it
    assert eng.basket.positions("EURUSD") == []
    # a second break the same day does not open again (one trade a day), even after the position is gone
    eng.paper.positions.clear()
    eng.rt.ingest_quote("EURUSD", quote(bid=1.0700))
    sb.tick(t0 + 7 * 3600)
    assert not eng.paper.positions


def test_breakout_skips_a_day_with_a_range_outside_the_limits(engine):
    eng = _engine(engine, SESSION_BREAKOUT_MIN_RANGE_PCT=1.0)   # the test range is ~0.37 % -> too narrow
    sb = eng.session_bo
    t0 = _range_day(eng, datetime(2026, 11, 10, tzinfo=timezone.utc))
    eng.rt.ingest_quote("EURUSD", quote(bid=1.0900))
    sb.tick(t0 + 6 * 3600)
    assert not eng.paper.positions and sb.days["EURUSD"]["status"] == "skipped"


def test_breakout_moves_sl_to_entry_at_be_r_and_closes_at_flat_time(engine, monkeypatch):
    eng = _engine(engine, SESSION_BREAKOUT_MIN_RANGE_PCT=0.0, SESSION_BREAKOUT_MAX_RANGE_PCT=10.0,
                  SESSION_BREAKOUT_SL_CAP_PCT=10.0)
    sb = eng.session_bo
    t0 = _range_day(eng, datetime(2026, 11, 10, tzinfo=timezone.utc))
    eng.rt.ingest_quote("EURUSD", quote(bid=1.0840))
    sb.tick(t0 + 6 * 3600)
    p = sb.positions("EURUSD")[0]
    risk = 1.0830 - 1.0790
    modifies, closes = [], []
    monkeypatch.setattr(eng.executor, "submit_modify", lambda src, tk, sym, side, sl, tp, reason="": modifies.append((sl, tp, reason)))
    monkeypatch.setattr(eng, "close_position", lambda src, tk, reason="BOT": closes.append((tk, reason)))
    # +1.6R in profit -> SL to entry (+ spread), once
    eng.rt.ingest_quote("EURUSD", quote(bid=p["price_open"] + 1.6 * risk))
    sb.tick(t0 + 8 * 3600)
    sb.tick(t0 + 8 * 3600 + 60)
    assert len(modifies) == 1 and modifies[0][0] >= p["price_open"] and modifies[0][2] == "SESSION_BE"
    # 21:00 UTC -> closed at market
    sb.tick(t0 + 21 * 3600 + 30)
    assert closes and closes[0][1] == "SESSION_FLAT"


def test_breakout_is_off_unless_enabled(engine):
    eng = _claude_basket(engine)   # SESSION_BREAKOUT_ENABLED defaults to False
    sb = eng.session_bo
    t0 = _range_day(eng, datetime(2026, 11, 10, tzinfo=timezone.utc))
    eng.rt.ingest_quote("EURUSD", quote(bid=1.0900))
    sb.tick(t0 + 6 * 3600)
    assert not eng.paper.positions and "EURUSD" not in sb.days
