"""End-to-end: fake EA ↔ bridge ↔ executor ↔ UI API, via TestClient."""
from tests.conftest import ADMIN
from tests.fake_ea import FakeEA, run_scenario


def test_fake_ea_full_cycle(client):
    def post(path, body, headers):
        r = client.post(path, json=body, headers=headers)
        return r.status_code, r.text

    def admin(method, path, body):
        r = client.request(method, path, json=body, headers=ADMIN if method != "GET" else None)
        return r.status_code, r.json()

    facts = run_scenario(FakeEA(post, "bridge-key"), admin, log=lambda *_: None)
    assert facts["trade"]["close_reason"] == "TP"
