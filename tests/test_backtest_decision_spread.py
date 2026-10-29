"""Backtest live parity: the decision quote carries the spread of the decision INSTANT (bar close = next bar's opening
hour), not the decision bar's own opening-hour spread (stale by one bar: 4 h on H4, 24 h on D1). Found by the Stage F
adversarial verifiers (research/results/F3/VERIFY_costs-data.md)."""
from app import backtest
from tests.test_stage_b_backtest import T0, db, run_params, seed_market  # noqa: F401  (db fixture)


def _stats(db_, wide_hours):
    db_.execute("DELETE FROM spread_stats")
    db_.executemany("INSERT INTO spread_stats(symbol, hour_utc, n, sum_pts, sum_sq, max_pts) VALUES(?,?,?,?,?,?)",
                    [("EURUSD", h, 10, 10 * (500.0 if h in wide_hours else 5.0), 0, 600) for h in range(24)])


def test_decision_uses_next_bar_hour_spread(db):
    seed_market(db, days=8)
    p = run_params(cost={"mode": "stats"}, signal_tf="H1", htf_tf="H1", risk={"max_spread_points": 30,
                   "max_signals_per_symbol_per_session": 50, "max_trades_per_day": 50, "max_consecutive_losses": 99,
                   "max_daily_loss_pct": 50})
    _stats(db, set())
    base = backtest.run(db, p)
    assert base["counts"]["candidates"] > 0 and base["counts"]["blocked_market"] == 0
    # a 500-pt spread in EVERY hour h means: a decision taken at the close of the bar opening at h-1 (instant h)
    # sees 500 pts and is blocked; the fill at hour h would also pay 500 -> all candidates blocked
    _stats(db, set(range(24)))
    allwide = backtest.run(db, p)
    assert allwide["counts"]["blocked_market"] == allwide["counts"]["candidates"] > 0
    # trade["signal_t"] is the decision INSTANT (= bar close = fill bar open). Wide spread in the single hour H where
    # most decisions happen: with the live rule every decision at instant H sees 500 pts and is blocked; the old stale
    # rule read the decision bar's own opening hour (H-1, spread 5) and let them through.
    from collections import Counter
    inst_hours = Counter(int(t["signal_t"] % 86400 // 3600) for t in base["trades"])
    H, n_h = inst_hours.most_common(1)[0]
    _stats(db, {H})
    r = backtest.run(db, p)
    after = Counter(int(t["signal_t"] % 86400 // 3600) for t in r["trades"])
    assert after[H] == 0, "decisions taken at instant H saw the 500-pt spread and must be blocked"
    assert r["counts"]["blocked_market"] >= n_h > 0
    assert r["counts"]["candidates"] == base["counts"]["candidates"]  # same analyses, only the market filter changed


def test_bar_mode_keeps_own_bar_spread(db):
    """cost.mode 'bar' uses the recorded spread of the decision bar (the next bar's spread is not known at the close)."""
    seed_market(db, days=6)
    p = run_params(cost={"mode": "bar"}, signal_tf="H1", htf_tf="H1")
    r = backtest.run(db, p)
    assert r["counts"]["blocked_market"] == 0 and r["counts"]["candidates"] > 0


def test_all_trades_flag_returns_full_list(db):
    seed_market(db, days=8)
    p = run_params(signal_tf="M5", htf_tf="H1", all_trades=True)
    r = backtest.run(db, p)
    assert len(r["trades"]) == r["summary"]["trades"]
