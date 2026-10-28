"""Fixes after the optimisation-pack review: RESCUE arbitrated like ML_EXIT (a failing close never blocks the SL
move), live neutral band for the backtest loss cooldown, slippage on the intrabar RESCUE exit."""
import time

import pytest

from app import exits
from app.exits import ExitPolicy
from app.labeler import SimPosition
from tests.conftest import quote
from tests.test_opt_pack_safety import eng  # noqa: F401  (fixture)
from tests.test_opt_pack_sim import BUY_EU, NOCOST, PT, T0, db, flat_bars, sim  # noqa: F401  (db fixture)


def _rescue_setup(eng, monkeypatch, close_fail):  # noqa: F811
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
    monkeypatch.setattr(eng.executor, "consecutive_failures",
                        lambda ticket, kind, *a, **kw: close_fail(now) if kind == "CLOSE" else (0, None))
    eng.rt.ingest_quote("EURUSD", quote(bid=1.08085, spread_pts=10, now=now))  # +1.25R
    return now, closes, mods


def test_rescue_close_sendable_supersedes_break_even(eng, monkeypatch):  # noqa: F811
    now, closes, mods = _rescue_setup(eng, monkeypatch, lambda now: (0, None))
    eng.pm.run(now)
    assert closes == ["RESCUE"] and mods == []


def test_rescue_close_in_backoff_lets_break_even_out(eng, monkeypatch):  # noqa: F811
    # 2 failed RESCUE closes, the last one 10 s ago: backing off (60 s) -> this round the BE MODIFY goes out
    now, closes, mods = _rescue_setup(eng, monkeypatch, lambda now: (2, now - 10))
    eng.pm.run(now)
    assert closes == [] and mods == ["BREAK_EVEN"]


def test_rescue_close_abandoned_lets_break_even_and_ml_exit_logic_run(eng, monkeypatch):  # noqa: F811
    now, closes, mods = _rescue_setup(eng, monkeypatch, lambda now: (6, now - 5))
    eng.pm.run(now)
    assert closes == [] and mods == ["BREAK_EVEN"]
    assert (next(iter(eng.paper.positions)), "CLOSE") in {(int(t), k) for t, k in eng.pm.gave_up}


# ---- backtest loss cooldown: |net| < 0.1R is neutral (live _trade_outcome) ---------------------------------

def _time_exit_bars(close_px):
    bars = flat_bars(30)
    for k in range(1, 4):
        bars[k] = {"t": T0 + k * 300, "o": close_px, "h": close_px + 0.00002, "l": close_px - 0.00002, "c": close_px,
                   "sp": 0}
    return bars


@pytest.mark.parametrize("close_px,blocked", [(1.10015, False), (1.09980, True)])
def test_cooldown_ignores_neutral_small_loss(db, close_px, blocked):  # noqa: F811
    # BUY entry 1.10010 (spread 10 pts), R = 100 pts; time stop after 10 min at the bar close
    # net (spread + commission) of the TIME exit: about -0.05R (neutral, no cooldown) / -0.3R (loss, cooldown)
    bars = _time_exit_bars(close_px)
    (res, _) = sim(db, {"EURUSD": {0: BUY_EU, 5: BUY_EU}}, {"EURUSD": bars}, cost=NOCOST,
                   strategy={"max_hold_minutes": 10, "rescue_enabled": False, "be_lock_r": 0},
                   risk={"cooldown_after_loss_minutes": 30, "rollover_blackout_minutes": 0})
    first = res["trades"][0]
    assert first["outcome"] == "TIME" and first["usd"] < 0
    assert (abs(first["r"]) < 0.1) is (not blocked)
    assert (res["counts"].get("blocked_context") == {"Pauză": 1}) is blocked
    assert len(res["trades"]) == (1 if blocked else 2)


# ---- intrabar RESCUE pays slippage (live = market close) -------------------------------------------------

def test_intrabar_rescue_exit_pays_slippage():
    bars = flat_bars(20)
    for k in range(1, 7):  # 30 min under water: lows -0.6R, closes -0.4R
        bars[k] = {"t": T0 + k * 300, "o": 1.0998, "h": 1.1, "l": 1.0995, "c": 1.0997, "sp": 0}
    bars[7] = {"t": T0 + 7 * 300, "o": 1.0998, "h": 1.1003, "l": 1.0997, "c": 1.1002, "sp": 0}
    pos = SimPosition("BUY", 1.10010, 1.09910, 1.10210, PT, T0 + 300, be_at_r=1.0, slip_px=2 * PT)
    exits.attach(pos, exits.with_rescue(ExitPolicy(), {"rescue_enabled": True}), history=[], bar_sec=300)
    out = None
    for b in bars[1:]:
        out = pos.step(b, 10 * PT)
        if out:
            break
    assert out[0] == "RESCUE" and out[1] == pytest.approx(1.10020 - 2 * PT)
