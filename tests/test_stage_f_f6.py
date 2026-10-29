"""Stage F, family F6: smoke tests for the H4 trend plugins (donchian-v1, ma-trend-v1, tsmom-v1) as run by
research/run_bt.py on the gold / JPY sub-universes.

No new plugin: F6 re-uses the Stage C2 plugins on signal timeframe H4 with htf NONE. Checks: registry resolution
and H4 auto-parameters, trend-v1 output shape on synthetic H4 bars, closed-bars-only decisions (quote never sets
the direction, window independence), the exit specs used by the family ('trailing', 'channel') build valid
policies, and a runner-level sanity run through app.backtest.run on H4 with the F6 portfolio risk settings."""
import random

import pytest

from app import backtest, strategy
from app import strategies as S
from app.exits import ExitPolicy
from app.strategies import donchian_v1
from tests.test_stage_b_backtest import T0, db, store  # noqa: F401
from tests.test_stage_c2_strategies import CFG, RISK, ctx, q_at

H4 = 4 * 3600
DAY = 86400
C2 = ["donchian-v1", "ma-trend-v1", "tsmom-v1"]
# the risk settings every F6 run uses (portfolio caps raised, account-protection limits off, H4 closes at the
# rollover hour are not blocked, spread filter 100 points: the spread is still paid in full)
F6_RISK = {"max_open_positions": 12, "max_trades_per_day": 50, "max_same_currency_direction": 4,
           "max_spread_points": 100, "rollover_blackout_minutes": 0, "cooldown_after_loss_minutes": 0,
           "max_consecutive_losses": 1000, "max_total_open_risk_pct": 100, "max_daily_loss_pct": 100}


def cfg_h4(name, **over):
    return S.merged_cfg(name, {**CFG, "signal_timeframe": "H4", "htf_timeframe": "NONE"}, over)


def h4_walk(n, seed, prefix=None, vol=0.0025, p0=1.10):
    """Weekday H4 random walk (6 bars/day, bars open at 21/01/05/09/13/17 UTC like the research DB) with
    persistent drift regimes; `prefix` keeps given bars and continues from them."""
    rng = random.Random(seed)
    bars = list(prefix or [])
    p = bars[-1]["c"] if bars else p0
    t = bars[-1]["t"] + H4 if bars else T0 - 900 * DAY - 3 * 3600
    drift = 0.0
    while len(bars) < n:
        if (t // DAY + 3) % 7 < 5:
            if rng.random() < 0.02:
                drift = rng.gauss(0, vol * 0.3)
            o = p
            p = o + drift + rng.gauss(0, vol)
            bars.append({"t": t, "o": round(o, 5), "h": round(max(o, p) + abs(rng.gauss(0, vol / 2)), 5),
                         "l": round(min(o, p) - abs(rng.gauss(0, vol / 2)), 5), "c": round(p, 5), "sp": 3})
        t += H4
    return bars


def run_p(name, sig, q=None, **over):
    return S.get(name).analyze("EURUSD", sig, [], q or q_at(sig[-1]["c"]), cfg_h4(name, **over), RISK,
                               ctx(now_ts=sig[-1]["t"] + H4))


def test_registry_and_h4_defaults():
    for n in C2:
        m = S.get(n)
        assert m.NAME == n and callable(m.analyze) and m.RUN_DEFAULTS["htf_timeframe"] == "NONE"
        assert ExitPolicy.from_spec(m.default_exit(cfg_h4(n))).mode in ("channel", "combo", "signal_flip")
    assert donchian_v1.entry_n(cfg_h4("donchian-v1")) == 100  # H4 auto entry_n
    assert donchian_v1.entry_n(cfg_h4("donchian-v1", entry_n=55)) == 55
    assert cfg_h4("tsmom-v1", lookback=63)["lookback"] == 63 and cfg_h4("ma-trend-v1", ema_slow_n=100)["ema_slow_n"] == 100


@pytest.mark.parametrize("name", C2)
def test_output_shape_same_as_trend_v1_on_h4(name):
    bars = h4_walk(1600, 3)
    live = strategy.analyze("EURUSD", bars[-400:], [], q_at(bars[-1]["c"]), CFG, RISK, ctx())
    seen = set()
    for i in range(420, 1600):
        a = run_p(name, bars[i - 399: i + 1])
        assert set(a) == set(live)
        assert set(live["indicators"]) <= set(a["indicators"])
        assert a["timeframe"] == "H4" and a["bar_time"] == bars[i]["t"]
        seen.add(a["direction"])
        if a["direction"] != "HOLD":
            sg = 1 if a["direction"] == "BUY" else -1
            assert (a["entry"] - a["stop_loss"]) * sg > 0 and (a["take_profit"] - a["entry"]) * sg > 0
            assert a["candidate"]["side"] == a["direction"] and not a["candidate"]["blockers"]
    assert "HOLD" in seen and len(seen) >= 2, seen


@pytest.mark.parametrize("name", C2)
def test_closed_bars_only_quote_never_sets_direction(name):
    a_bars = h4_walk(1400, 5)
    b_bars = h4_walk(1400, 77, prefix=a_bars[:1000])  # same past, different future
    m, cfg = S.get(name), cfg_h4(name)
    n_cand = 0
    for i in range(400, 999):
        sa, sb = a_bars[i - 399: i + 1], b_bars[i - 399: i + 1]
        q, c = q_at(a_bars[i]["c"]), ctx(now_ts=a_bars[i]["t"] + H4)
        ra = m.analyze("EURUSD", sa, [], q, cfg, RISK, c)
        assert ra == m.analyze("EURUSD", sb, [], q, cfg, RISK, c)
        assert ra == m.analyze("EURUSD", a_bars[: i + 1], [], q, cfg, RISK, c)  # longer window, same decision
        hi = m.analyze("EURUSD", sa, [], q_at(round(a_bars[i]["c"] * 1.01, 5)), cfg, RISK, c)
        lo = m.analyze("EURUSD", sa, [], q_at(round(a_bars[i]["c"] * 0.99, 5)), cfg, RISK, c)
        assert hi["direction"] == lo["direction"] == ra["direction"]  # the quote only prices the entry
        n_cand += ra["candidate"] is not None
    assert n_cand > 3


def test_f6_exit_specs_are_valid_policies():
    tr = ExitPolicy.from_spec({"mode": "trailing", "params": {"trail_start_r": 1.0, "trail_atr_mult": 3.0, "trail_min_r": 0.0}})
    assert list(tr.modes) == ["trailing"] and tr.needs_indicators
    ch = ExitPolicy.from_spec({"mode": "channel", "params": {"channel_n": 10}})
    assert list(ch.modes) == ["channel"] and ch.p["channel_n"] == 10
    combo = ExitPolicy.from_spec({"mode": "combo", "params": {"modes": ["channel", "trailing"], "channel_n": 20,
                                                              "trail_start_r": 2.0, "trail_atr_mult": 2.0, "trail_min_r": 0.0}})
    assert set(combo.modes) == {"channel", "trailing"}


def seed_h4(db, n=2400):  # noqa: F811
    out = {}
    for sym, seed, shift in (("EURUSD", 21, 0.0), ("GBPUSD", 22, 0.2)):
        bars = h4_walk(n, seed)
        if shift:
            bars = [dict(b, o=round(b["o"] + shift, 5), h=round(b["h"] + shift, 5), l=round(b["l"] + shift, 5),
                         c=round(b["c"] + shift, 5)) for b in bars]
        store(db, sym, "H4", bars)
        out[sym] = bars
    return out


def h4_params(name, **kw):
    p = {"symbols": ["EURUSD", "GBPUSD"], "strategy_name": name, "signal_tf": "H4", "htf_tf": "NONE",
         "start_ts": T0 - 700 * DAY, "end_ts": T0 - 100 * DAY, "now_ts": T0 + 30 * DAY, "initial_equity": 10000,
         "size_mode": "r_only", "workers": 1, "session": None,
         "specs": {"EURUSD": {"point": 0.00001, "digits": 5, "tick_value": 1.0},
                   "GBPUSD": {"point": 0.00001, "digits": 5, "tick_value": 1.0}},
         "cost": {"mode": "fixed", "fixed_spread_pts": 10, "swap_mode": "table"}, "risk": dict(F6_RISK)}
    p.update(kw)
    return p


def test_runner_level_h4_backtest_with_f6_exits(db):  # noqa: F811
    seed_h4(db)
    r = backtest.run(db, h4_params("donchian-v1", strategy={"entry_n": 55, "sl_atr": 2.0}))
    s = r["summary"]
    assert s["trades"] > 5 and isinstance(s["avg_r"], float) and r["params"]["signal_tf"] == "H4"
    assert "CHANNEL" in set(r["by_outcome"]), r["by_outcome"]  # default exit of the plugin
    assert r["counts"]["blocked_context"].get("Rollover", 0) == 0  # F6 risk: rollover blackout off on H4
    assert r["params"]["cost"]["swap_mode"] == "table" and s["swap_r"] < 0
    r2 = backtest.run(db, h4_params("donchian-v1", strategy={"entry_n": 55, "sl_atr": 2.0}))
    assert r2["summary"] == s  # deterministic
    tr = backtest.run(db, h4_params("donchian-v1", strategy={"entry_n": 55, "sl_atr": 2.0},
                                    exit={"mode": "trailing", "params": {"trail_start_r": 1.0, "trail_atr_mult": 3.0, "trail_min_r": 0.0}}))
    assert tr["summary"]["trades"] > 5 and "TRAIL" in set(tr["by_outcome"]), tr["by_outcome"]
    assert [t["entry_t"] for t in tr["trades"]][:3] != [] and tr["params"]["exit"]["mode"] == "trailing"
    ch = backtest.run(db, h4_params("tsmom-v1", strategy={"lookback": 126}, exit={"mode": "channel", "params": {"channel_n": 20}}))
    assert ch["summary"]["trades"] > 5 and "CHANNEL" in set(ch["by_outcome"]), ch["by_outcome"]
