"""Stage A end to end: fake EA v1.10 (calendar, instance_id) ↔ bridge ↔ engine ↔ UI API, via TestClient."""
import time

from tests.conftest import ADMIN
from tests.fake_ea import FakeEA, gen_bars
from tests.test_stage_a_engine import up_data


class FakeEA110(FakeEA):
    """FakeEA speaking v1.10: instance_id, calendar (sent once until HTTP 200), optional fixed uptrend bars."""

    def __init__(self, post, key, version="1.10", login=52123456, uptrend=False):
        super().__init__(post, key)
        self.version, self.login, self.instance_id = version, login, "inst-a1b2c3"
        self.calendar = None  # list -> sent on the next sync, then cleared
        self.uptrend = None
        if uptrend:
            sig, htf = up_data()
            self.uptrend = [dict(b, symbol="EURUSD", tf="M5") for b in sig] + \
                           [dict(b, symbol="EURUSD", tf="H1") for b in htf]
            self.bid = sig[-1]["c"]

    def payload(self, history_for=None):
        p = super().payload(history_for)
        p["ea_version"] = self.version
        p["account"]["login"] = self.login
        if self.version >= "1.10":
            p["instance_id"] = self.instance_id
            if self.calendar is not None:
                p["calendar"], p["calendar_ok"] = list(self.calendar), True
        if self.uptrend is not None:
            # fixed uptrend on the analysis TFs, full history on the chart-only TFs (no refill left pending)
            now = time.time()
            p["bars"] = list(self.uptrend) + [b for tf in self.tfs if tf not in ("M5", "H1")
                                              for b in gen_bars("EURUSD", tf, 300, now, self.bid)]
        return p

    def sync(self, history=False):
        lines = super().sync(history)
        self.calendar = None  # delivered with HTTP 200
        return lines


def adapters(client):
    def post(path, body, headers):
        r = client.post(path, json=body, headers=headers)
        return r.status_code, r.text

    def admin(method, path, body=None):
        r = client.request(method, path, json=body, headers=ADMIN if method != "GET" else None)
        return r.status_code, r.json()

    return post, admin


def past_warmup(engine):
    engine.rt.started_ts -= 60


def handshake(ea):
    ea.sync()
    ea.sync(history=True)


def test_e2e_news_blackout_from_ea_calendar(client, engine):
    post, admin = adapters(client)
    ea = FakeEA110(post, "bridge-key", uptrend=True)
    now = time.time()
    ea.calendar = [{"t": int(now + 15 * 60), "ccy": "USD", "imp": 3, "name": "Non-Farm Payrolls"},
                   {"t": int(now + 10 * 60), "ccy": "JPY", "imp": 3, "name": "BoJ"}]
    ea.sync()  # handshake: learns SYMBOLS/TF
    lines = ea.sync()
    assert lines[0][0] == "OK"
    rt = engine.rt
    assert rt.bridge["instances_active"] == 1 and rt.bridge["calendar_ok"] is True
    assert [e["name"] for e in rt.calendar] == ["BoJ", "Non-Farm Payrolls"]
    assert not any(k[0] == "EURUSD" for k in rt.hist_force)
    past_warmup(engine)
    assert engine.warmup_reason("EURUSD") is None

    a = engine.analyze_symbol("EURUSD", allow_signal=True)
    assert a["direction"] == "HOLD"
    assert any("Non-Farm Payrolls" in r and "USD" in r for r in a["reasons"]), a["reasons"]
    assert engine.list_signals() == []
    code, st = admin("GET", "/api/v1/status")
    assert code == 200
    news = st["news"]
    assert news["ok"] is True and news["next_high"]["name"] == "Non-Farm Payrolls"
    assert any(b["symbol"] == "EURUSD" for b in news["blocked"])

    # event moved outside the ±30 min window -> the same setup produces a signal
    ea.calendar = [{"t": int(time.time() + 3 * 3600), "ccy": "USD", "imp": 3, "name": "Non-Farm Payrolls"}]
    ea.sync()
    a = engine.analyze_symbol("EURUSD", allow_signal=True)
    assert a["direction"] == "BUY", a["reasons"]
    sigs = engine.list_signals()
    assert len(sigs) == 1 and sigs[0]["direction"] == "BUY"
    code, st = admin("GET", "/api/v1/status")
    assert st["news"]["blocked"] == [] and st["news"]["events_next_24h"] == 1


def _demo_test_order(admin):
    assert admin("POST", "/api/v1/control/mode", {"mode": "DEMO"})[0] == 200
    assert admin("POST", "/api/v1/control/execution", {"enabled": True})[0] == 200
    code, intent = admin("POST", "/api/v1/control/test-order", {"symbol": "EURUSD", "side": "BUY"})
    assert code == 200 and intent["state"] == "QUEUED", intent
    return intent


def test_e2e_open_line_v110_has_ref_and_expiry(client, engine):
    post, admin = adapters(client)
    ea = FakeEA110(post, "bridge-key", version="1.10")
    ea.calendar = []
    handshake(ea)
    intent = _demo_test_order(admin)
    t0 = time.time()
    opens = [p for p in ea.sync() if p[0] == "OPEN"]
    assert len(opens) == 1 and opens[0][1] == intent["id"] and len(opens[0]) == 12, opens
    ask = round(ea.bid + 0.00010, 5)
    assert float(opens[0][9]) == ask
    assert int(opens[0][10]) >= 5
    assert t0 < int(opens[0][11]) <= t0 + 21


def test_e2e_open_line_v100_unchanged(client, engine):
    post, admin = adapters(client)
    ea = FakeEA110(post, "bridge-key", version="1.00")
    handshake(ea)
    intent = _demo_test_order(admin)
    opens = [p for p in ea.sync() if p[0] == "OPEN"]
    assert len(opens) == 1 and len(opens[0]) == 9 and opens[0][8] == f"ai:{intent['id'][:8]}", opens
    assert "CALENDAR" not in [p[0] for p in ea.sync()]


def test_e2e_login_change_fails_queued_intents(client, engine):
    post, admin = adapters(client)
    ea = FakeEA110(post, "bridge-key")
    ea.calendar = []
    handshake(ea)
    intent = _demo_test_order(admin)
    ea.login = 87654321  # user logs into another account before the EA picked the command up
    lines = ea.sync()
    assert not [p for p in lines if p[0] == "OPEN"], lines
    code, its = admin("GET", "/api/v1/intents?limit=10")
    got = next(i for i in its if i["id"] == intent["id"])
    assert got["state"] == "FAILED" and got["result"]["retcode_desc"] == "ACCOUNT_CHANGED", got
    assert not [p for p in ea.sync() if p[0] == "OPEN"]


def test_e2e_signal_created_disarmed_not_executed_after_arm(client, engine):
    post, admin = adapters(client)
    ea = FakeEA110(post, "bridge-key", uptrend=True)
    ea.calendar = []
    ea.sync()  # handshake: learns SYMBOLS/TF
    ea.sync()
    past_warmup(engine)
    assert admin("POST", "/api/v1/control/mode", {"mode": "DEMO"})[0] == 200  # DEMO, disarmed
    engine.analyze_symbol("EURUSD", allow_signal=True)
    sigs = engine.list_signals()
    assert len(sigs) == 1 and sigs[0]["created_armed"] is False
    time.sleep(0.01)
    assert admin("POST", "/api/v1/control/execution", {"enabled": True})[0] == 200
    engine.try_execute(engine.get_signal(sigs[0]["id"]))
    s = engine.get_signal(sigs[0]["id"])
    assert s["status"] == "NO_TRADE" and "înainte de armare" in s["status_reason"]
    assert not [p for p in ea.sync() if p[0] == "OPEN"]
    code, its = admin("GET", "/api/v1/intents?limit=10")
    assert [i for i in its if i.get("signal_id") == s["id"]] == []
