"""Stage C2 integration: /archive/status per-TF days_target (ARCHIVE_EXTRA) and API backtest validation for the
research plugins (donchian-v1, ma-trend-v1, tsmom-v1) — backtest only, never selectable live."""
import pytest
from fastapi import HTTPException

from app.api import validate_backtest_params
from tests.conftest import ADMIN


def test_archive_status_api_days_target_per_tf(engine, client):
    engine.rt.settings.ARCHIVE_EXTRA = "H1:1095,H4:1825,D1:3650"  # research setup (default: off)
    engine.rt.bridge.update({"ea_version": "1.12"})
    body = client.get("/api/v1/archive/status").json()
    assert body["days_target"] == 120 and body["phase"] in ("base", "extra", "done")
    eur = body["symbols"]["EURUSD"]
    assert eur["M5"]["days_target"] == 120 and eur["M5"]["extended"] is False
    assert eur["D1"]["days_target"] == 3650 and eur["H4"]["days_target"] == 1825 and eur["H1"]["days_target"] == 1095
    assert eur["D1"]["extended"] is True and eur["D1"]["exhausted"] is False and "history_start_ts" in eur["D1"]


def test_research_plugin_backtest_params_keep_plugin_defaults(engine):
    out = validate_backtest_params({"strategy_name": "donchian-v1", "symbols": ["EURUSD"], "days": 3000,
                                    "cost": {"swap_mode": "zero"}, "strategy": {"entry_n": 20}}, engine)
    assert out["strategy_name"] == "donchian-v1" and out["days"] == 3000
    assert "signal_tf" not in out and "htf_tf" not in out  # RUN_DEFAULTS of the plugin (D1, no HTF)
    assert out["cost"]["swap_mode"] == "zero" and out["strategy"]["entry_n"] == 20
    out = validate_backtest_params({"strategy_name": "tsmom-v1", "symbols": ["EURUSD"], "signal_tf": "H4",
                                    "htf_tf": "NONE"}, engine)
    assert out["signal_tf"] == "H4" and out["htf_tf"] == "NONE"


def test_live_strategies_validation_unchanged(engine):
    for body, msg in (({"days": 731}, "days"), ({"htf_tf": "NONE"}, "htf_tf"),
                      ({"strategy_name": "nope-v1"}, "strategie necunoscută"),
                      ({"cost": {"swap_mode": "huge"}}, "swap_mode")):
        with pytest.raises(HTTPException) as ei:
            validate_backtest_params({"symbols": ["EURUSD"], **body}, engine)
        assert msg in str(ei.value.detail)
    out = validate_backtest_params({"symbols": ["EURUSD"]}, engine)
    assert out["signal_tf"] == engine.rt.strategy["signal_timeframe"] and out["days"] == 30


def test_research_plugins_not_selectable_live(engine, client):
    assert "donchian-v1" not in engine.strategy_names()
    r = client.put("/api/v1/strategy", json={"name": "donchian-v1"}, headers=ADMIN)
    assert r.status_code == 422 and engine.active_strategy_name() == "trend-v1"
