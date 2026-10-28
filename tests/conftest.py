import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import api, bridge
from app.config import Settings, set_settings
from app.db import DB
from app.engine import Engine

EURUSD_INFO = {
    "point": 0.00001, "digits": 5, "volume_min": 0.01, "volume_max": 100.0, "volume_step": 0.01,
    "stops_level": 0, "freeze_level": 0, "tick_value": 1.0, "tick_size": 0.00001, "contract_size": 100000,
    "trade_mode": 4, "filling_mode": 3,
}


def make_settings(tmp_path, **kw) -> Settings:
    base = dict(
        _env_file=None, DATABASE_PATH=str(tmp_path / "test.db"), ADMIN_TOKEN="adm-token", MT5_BRIDGE_KEY="bridge-key",
        DATA_SOURCE="mt5_bridge", ANTHROPIC_API_KEY="", TELEGRAM_ENABLED=False, TELEGRAM_BOT_TOKEN="",
        TELEGRAM_CHAT_ID="", TRADING_MODE="PAPER", EXECUTION_ENABLED=False, SESSION_ENABLED=False,
    )
    base.update(kw)
    s = Settings(**base)
    set_settings(s)
    return s


def quote(bid=1.08000, spread_pts=10, now=None, **extra):
    q = dict(EURUSD_INFO)
    q.update({"bid": bid, "ask": round(bid + spread_pts * 0.00001, 5), "spread_points": spread_pts,
              "time_msc": int((now or time.time()) * 1000)})
    q.update(extra)
    return q


def make_bars(closes, tf_sec=300, end_t=None, wick=0.00005):
    end_t = end_t or (int(time.time()) // tf_sec * tf_sec - tf_sec)
    start = end_t - (len(closes) - 1) * tf_sec
    bars = []
    prev = closes[0]
    for i, c in enumerate(closes):
        o = prev
        bars.append({"t": start + i * tf_sec, "o": o, "h": max(o, c) + wick, "l": min(o, c) - wick, "c": c,
                     "tv": 100, "sp": 10})
        prev = c
    return bars


def zigzag(n, up, down, start=1.0800, end_up=True):
    """alternating +up / -down steps; ends on the `end_up` step."""
    closes = [start]
    steps = []
    for i in range(n - 1):
        steps.append(up if i % 2 == 0 else -down)
    if end_up and steps and steps[-1] < 0:
        steps = steps[1:] + [up]
    if not end_up and steps and steps[-1] > 0:
        steps = steps[1:] + [-down]
    for s in steps:
        closes.append(round(closes[-1] + s, 5))
    return closes


@pytest.fixture
def settings(tmp_path):
    return make_settings(tmp_path)


@pytest.fixture
def engine(settings):
    db = DB(settings.db_path)
    eng = Engine(settings, db)
    eng.startup()
    yield eng
    db.close()


@pytest.fixture
def client(engine):
    app = FastAPI()
    app.include_router(api.router)
    app.include_router(bridge.router)
    app.state.engine = engine
    return TestClient(app)


ADMIN = {"X-Admin-Token": "adm-token"}
