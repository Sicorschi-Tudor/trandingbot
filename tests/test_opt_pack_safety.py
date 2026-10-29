"""Adversarial live-safety checks of the optimisation pack (rescue exit, BE lock, cooldown, arbitration, rollover)."""
import time
from datetime import datetime, timezone

import pytest

from app import risk as risk_mod
from app.config import iso
from app.db import DB
from app.engine import Engine
from app.position_manager import plan
from tests.conftest import make_settings, quote

RESCUE_CFG = {"rescue_enabled": True, "rescue_minutes": 30, "rescue_mae_r": 0.3, "rescue_neg_frac": 0.6,
              "rescue_close_r": 0.1, "break_even_at_r": 1.0, "be_lock_r": 0.1}


@pytest.fixture
def eng(tmp_path):
    s = make_settings(tmp_path)
    db = DB(s.db_path)
    e = Engine(s, db)
    e.startup()
    yield e
    db.close()


def test_plan_rescue_due_hides_break_even_at_1r():
    """While the rescue exit is due, plan() returns only the CLOSE: the BE MODIFY is never even computed."""
    now = time.time()
    pos = {"side": "BUY", "price_open": 1.08010, "sl": 1.07950, "tp": 1.08300, "opened_ts": now - 3600}
    st = {"opened_ts": now - 3600, "mae_r": 0.5, "n": 60, "neg": 50}
    acts = plan(pos, quote(bid=1.08085, now=now), RESCUE_CFG, now, True, 1.07950, rescue_state=st)  # +1.25R
    assert acts == [{"type": "CLOSE", "reason": "RESCUE"}]
    no_rescue = plan(pos, quote(bid=1.08085, now=now), RESCUE_CFG, now, True, 1.07950, rescue_state=None)
    assert no_rescue and no_rescue[0]["reason"] == "BREAK_EVEN"


def test_rescue_close_backing_off_must_not_block_break_even(eng, monkeypatch):
    now = time.time()
    q0 = quote(bid=1.08000, spread_pts=10, now=now - 3600)
    eng.rt.ingest_quote("EURUSD", q0)
    p = eng.paper.open("EURUSD", "BUY", 0.1, 1.07950, 1.08300, q0, now=now - 3600)  # entry 1.08010, R = 60 pts
    eng._paths[("PAPER", str(p["ticket"]))] = {"entry": 1.0801, "risk": 0.0006, "side": "BUY", "symbol": "EURUSD",
                                                "opened_ts": now - 3600, "mfe": 1.3, "mae": 0.5, "n": 60, "neg": 50,
                                                "last_ts": 0}
    closes, mods = [], []
    monkeypatch.setattr(eng, "close_position", lambda s, t, reason="BOT": closes.append(reason))
    monkeypatch.setattr(eng.executor, "submit_modify",
                        lambda *a, **kw: mods.append(kw.get("reason")) or {"state": "QUEUED"})
    # the RESCUE CLOSE already failed 6 times (abandoned); MODIFY has no failures
    monkeypatch.setattr(eng.executor, "consecutive_failures",
                        lambda ticket, kind, *a, **kw: (6, now - 5) if kind == "CLOSE" else (0, None))
    eng.rt.ingest_quote("EURUSD", quote(bid=1.08085, spread_pts=10, now=now))  # +1.25R
    eng.pm.run(now)
    assert closes == []  # the close is abandoned...
    assert mods == ["BREAK_EVEN"]  # ...so the break-even must still go out


def test_be_lock_only_tightens_and_is_applied_once():
    now = time.time()
    cfg = {"break_even_at_r": 1.0, "be_lock_r": 0.1}
    pos = {"side": "SELL", "price_open": 1.08000, "sl": 1.08060, "tp": 1.07700, "opened_ts": now}
    a = plan(pos, quote(bid=1.07920, spread_pts=10, now=now), cfg, now, True, 1.08060)
    assert a[0]["reason"] == "BREAK_EVEN" and a[0]["sl"] == pytest.approx(1.07987)  # max(10+3, 6) pts
    pos["sl"] = a[0]["sl"]
    # same spread: no new MODIFY; a narrower spread never loosens
    assert plan(pos, quote(bid=1.07920, spread_pts=10, now=now), cfg, now, True, 1.08060) == []
    assert plan(pos, quote(bid=1.07920, spread_pts=2, now=now), cfg, now, True, 1.08060) == []
    # break-even is applied once: a wider spread later must not trigger MODIFY after MODIFY
    assert plan(pos, quote(bid=1.07910, spread_pts=20, now=now), cfg, now, True, 1.08060) == []


def _insert(e, key, mode, net, minutes_ago, now, ticket=None):
    e._insert_trade(key, {"ticket": ticket, "symbol": "EURUSD", "side": "BUY", "volume": 0.1, "price_open": 1.08,
                          "price_close": 1.08, "profit": net, "commission": 0.0, "swap": 0.0,
                          "opened_at_utc": iso(now - minutes_ago * 60 - 600), "closed_at_utc": iso(now - minutes_ago * 60),
                          "close_reason": "SL", "mode": mode, "signal_id": None})


def test_cooldown_not_keyed_per_account(eng):
    """A PAPER loss starts the cooldown for the DEMO account too (loss_streak is keyed per account, this is not)."""
    now = time.time()
    eng.rt.trading.update(mode="DEMO")
    _insert(eng, "PAPER:1", "PAPER", -50.0, 5, now)
    assert eng.loss_streak("DEMO")[0] == 0  # streak: per account, PAPER ignored
    assert eng.last_loss_ts("EURUSD", now) == pytest.approx(now - 300, abs=1)  # cooldown: global


def test_rollover_dst_edges():
    # Sunday 2026-03-08 (US DST starts): 17:00 EDT = 21:00 UTC; Sunday 2026-11-01: 17:00 EST = 22:00 UTC
    for ts, exp in ((datetime(2026, 3, 8, 21, 5, tzinfo=timezone.utc), datetime(2026, 3, 8, 21, 0, tzinfo=timezone.utc)),
                    (datetime(2026, 11, 1, 21, 55, tzinfo=timezone.utc), datetime(2026, 11, 1, 22, 0, tzinfo=timezone.utc)),
                    (datetime(2026, 3, 29, 20, 50, tzinfo=timezone.utc), datetime(2026, 3, 29, 21, 0, tzinfo=timezone.utc))):
        assert risk_mod.nearest_rollover_ts(ts.timestamp()) == exp.timestamp()
    assert risk_mod.rollover_blocker(datetime(2026, 11, 1, 21, 39, tzinfo=timezone.utc).timestamp(), 20) is None
