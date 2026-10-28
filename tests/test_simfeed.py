from app.db import DB
from app.engine import Engine
from tests.conftest import make_settings


def test_simfeed_history_and_analysis(tmp_path):
    s = make_settings(tmp_path, DATA_SOURCE="sim", ACTIVE_SYMBOLS="EURUSD,XAUUSD")
    eng = Engine(s, DB(s.db_path))
    eng.startup()
    assert eng.status()["feed_label"] == "SIMULATED"
    eng.sim.ensure_symbols()
    for _ in range(5):
        eng.sim.tick()
    assert len(eng.rt.closed_bars("EURUSD", "M5")) >= 600
    assert len(eng.rt.closed_bars("EURUSD", "H1")) >= 60
    q = eng.rt.quotes["XAUUSD"]
    assert 1000 < q["bid"] < 5000 and q["digits"] == 2 and q["ask"] > q["bid"]
    a = eng.analyze_symbol("EURUSD", allow_signal=False)
    assert a["direction"] in ("BUY", "SELL", "HOLD")
    assert a["indicators"].get("atr")
    sym = eng.symbols_payload()[0]
    assert sym["quality"] == "OK"
    assert eng.rt.persist_bars is False
