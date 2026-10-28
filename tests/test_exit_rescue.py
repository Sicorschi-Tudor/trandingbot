from app.exits import ExitPolicy, attach, step
from app.labeler import SimPosition


def make_pos(policy):
    pos = SimPosition(side="BUY", entry=1.10000, sl=1.09900, tp=1.10200, point=0.00001, opened_t=0, be_at_r=0)
    attach(pos, policy, history=[], bar_sec=300)
    return pos


def bar(t, o, h, l, c):
    return {"t": t, "o": o, "h": h, "l": l, "c": c}


def test_rescue_exits_at_small_profit_after_long_time_under_water():
    pol = ExitPolicy("rescue", {"rescue_minutes": 30, "rescue_mae_r": 0.3, "rescue_neg_frac": 0.6, "rescue_close_r": 0.1})
    pos = make_pos(pol)
    out = None
    # 6 bars (30 min) mostly under water, dipping to -0.5R
    for i in range(6):
        out = step(pos, bar(i * 300, 1.0998, 1.09990, 1.09950, 1.09970), 0.0)
        assert out is None
    assert pos.xs["rescue_armed"]
    # next bar trades up to +0.15R: closes at +0.1R intrabar
    out = step(pos, bar(6 * 300, 1.0998, 1.10015, 1.09980, 1.10010), 0.0)
    assert out is not None and out[0] == "RESCUE"
    assert abs(out[1] - 1.10010) < 1e-9


def test_rescue_not_armed_before_minimum_time():
    pol = ExitPolicy("rescue", {"rescue_minutes": 60, "rescue_mae_r": 0.3, "rescue_neg_frac": 0.6, "rescue_close_r": 0.1})
    pos = make_pos(pol)
    for i in range(3):
        step(pos, bar(i * 300, 1.0998, 1.09990, 1.09950, 1.09970), 0.0)
    assert not pos.xs["rescue_armed"]
    assert step(pos, bar(900, 1.0998, 1.10015, 1.09980, 1.10010), 0.0) is None


def test_rescue_never_overrides_stop_loss():
    pol = ExitPolicy("rescue", {"rescue_minutes": 5, "rescue_mae_r": 0.1, "rescue_neg_frac": 0.5, "rescue_close_r": 0.1})
    pos = make_pos(pol)
    step(pos, bar(0, 1.0998, 1.09990, 1.09950, 1.09970), 0.0)
    out = step(pos, bar(300, 1.0997, 1.10020, 1.09890, 1.09900), 0.0)  # SL and rescue level in the same bar
    assert out[0] == "SL"


def test_live_rescue_counts_swap():
    from app.position_manager import rescue_close
    cfg = {"rescue_enabled": True, "rescue_minutes": 30, "rescue_mae_r": 0.3, "rescue_neg_frac": 0.6, "rescue_close_r": 0.1}
    state = {"opened_ts": 1000, "mae_r": 0.8, "n": 100, "neg": 90}
    pos = {"side": "SELL", "price_open": 0.56236, "volume": 0.01, "swap": -0.20}
    info = {"bid": 0.56219, "ask": 0.56226, "tick_size": 0.00001, "tick_value": 1.0}
    # price alone is +0.11R (sl 90 points away) but triple swap makes it net negative -> no rescue close
    assert rescue_close(pos, info, cfg, 9000, 0.56326, state) is None
    pos["swap"] = 0.0
    assert rescue_close(pos, info, cfg, 9000, 0.56326, state) == {"type": "CLOSE", "reason": "RESCUE"}
