"""End-to-end: fake EA <-> bridge <-> executor <-> UI API, via TestClient."""
from tests.conftest import ADMIN
from tests.fake_ea import FakeEA, run_scenario


def test_fake_ea_full_cycle(client, engine):
    def post(path, body, headers):
        r = client.post(path, json=body, headers=headers)
        return r.status_code, r.text

    def admin(method, path, body):
        r = client.request(method, path, json=body, headers=ADMIN if method != "GET" else None)
        return r.status_code, r.json()

    def submit(symbol, side):
        engine.set_mode("DEMO")
        engine.set_execution(True)
        q = engine.rt.quotes[symbol]
        entry = float(q["ask"])
        sl, tp = round(entry - 0.0020, 5), round(entry + 0.0030, 5)
        rc = engine.risk_check(symbol, side, entry, sl, tp)
        return engine.executor.submit_open("DEMO", symbol, side, rc["volume"], sl, tp, q, rc)

    facts = run_scenario(FakeEA(post, "bridge-key"), admin, submit, log=lambda *_: None)
    assert facts["trade"]["close_reason"] == "TP"
