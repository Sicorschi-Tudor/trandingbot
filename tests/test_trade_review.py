"""Claude as trade analyst (app/trade_review.py): post-trade + daily reviews, analysis only."""
import asyncio
import json
from types import SimpleNamespace

from app.ai_claude import DAILY_REVIEW_SCHEMA, TRADE_REVIEW_SCHEMA, ClaudeReviewer
from app.telegram import fmt_daily_review, fmt_trade_review
from tests.conftest import ADMIN, make_settings, quote
from tests.test_engine import analysis_stub

TRADE_RV = {"process_quality": "GOOD", "outcome_class": "GOOD_PROCESS_LOSS", "summary": "Setup valid, SL respectat.",
            "what_worked": ["risc 0.25%"], "what_failed": ["spread mare la intrare"], "lessons": ["Verifică spreadul la 09:00"],
            "tags": ["spread-mare"], "exit_assessment": "FORCED"}
DAILY_RV = {"headline": "Zi cu o tranzacție, fără semnificație statistică.", "summary": "O singură tranzacție PAPER.",
            "patterns": ["intrare la deschiderea sesiunii"], "veto_assessment": "fără VETO azi",
            "risk_assessment": "limite respectate", "hypotheses_to_test": ["filtru spread < 12 pct în prima oră"],
            "confidence": "LOW"}


class FakeMessages:
    """Answers the trade schema or the daily schema depending on the system prompt; records every call."""

    def __init__(self):
        self.calls = []

    async def create(self, **kw):
        self.calls.append(kw)
        schema = kw["output_config"]["format"]["schema"]
        body = TRADE_RV if schema is TRADE_REVIEW_SCHEMA else DAILY_RV
        return SimpleNamespace(stop_reason="end_turn", model=kw["model"], usage=None,
                               content=[SimpleNamespace(type="text", text=json.dumps(body))])


def arm_fake_claude(engine):
    fake = FakeMessages()
    engine.ai.settings = make_settings_like(engine.settings)
    engine.ai._client = SimpleNamespace(beta=SimpleNamespace(messages=fake), messages=fake)
    return fake


def make_settings_like(s):
    return s.model_copy(update={"ANTHROPIC_API_KEY": "sk-ant-test-key-123456"})


def open_and_close_paper_trade(engine, bar_time=1730000000):
    engine.rt.trading.update(mode="PAPER", execution_enabled=True)
    engine.rt.ingest_quote("EURUSD", quote(bid=1.08000))
    sig = engine.create_signal(analysis_stub(bar_time=bar_time))
    engine.try_execute(sig)
    assert engine.get_signal(sig["id"])["status"] == "EXECUTED"
    # price falls through the SL -> paper broker closes, engine records the trade
    engine.rt.ingest_quote("EURUSD", quote(bid=1.07900))
    engine.paper.on_quote("EURUSD", engine.rt.quotes["EURUSD"])
    trades = engine.list_trades()
    assert trades and trades[0]["close_reason"] == "SL"
    return trades[0]


def test_review_disabled_without_key(engine):
    tr = open_and_close_paper_trade(engine)
    # no API key in the test settings -> nothing scheduled, nothing stored
    assert engine.trade_reviews.on_trade_closed(tr) is False
    assert engine.trade_reviews.list_payload() == []
    assert engine.trade_reviews.status()["trade_review_enabled"] is False


def test_trade_payload_contains_signal_path_and_history(engine):
    tr = open_and_close_paper_trade(engine)
    p = engine.trade_reviews.build_trade_payload(tr["id"])
    assert p["trade"]["symbol"] == "EURUSD" and p["trade"]["close_reason"] == "SL"
    assert p["trade"]["net_usd"] < 0 and p["trade"]["r_multiple"] is not None
    assert p["signal"]["direction"] == "BUY" and p["signal"]["ai_at_entry"]["status"] == "DISABLED"
    assert any(ev["type"] == "INTENT_PAPER_FILLED" for ev in p["signal"]["events"])
    assert p["settings"]["strategy"] == "trend-v1" and p["note"].startswith("Rezultat simulat")
    assert p["recent_trades_summary"] is None  # the first trade has no history before it


def test_post_trade_review_stored_notified_and_idempotent(engine):
    tr = open_and_close_paper_trade(engine)
    fake = arm_fake_claude(engine)
    # outbox only: rows are queued in `notifications`, nothing is sent (the worker is not running in tests)
    engine.telegram.settings = engine.settings.model_copy(
        update={"TELEGRAM_ENABLED": True, "TELEGRAM_BOT_TOKEN": "123:abc", "TELEGRAM_CHAT_ID": "-100"})
    row = asyncio.run(engine.trade_reviews.review_trade(tr["id"]))
    assert row["status"] == "DONE" and row["kind"] == "TRADE" and row["ref_id"] == str(tr["id"])
    assert row["review"]["process_quality"] == "GOOD" and row["review"]["lessons"] == ["Verifică spreadul la 09:00"]
    assert len(fake.calls) == 1
    call = fake.calls[0]
    assert call["output_config"]["effort"] == "medium" and call["output_config"]["format"]["type"] == "json_schema"
    assert "thinking" not in call and "temperature" not in call  # adaptive thinking is implicit on this model family
    assert "ÎNCHISĂ" in call["messages"][0]["content"]
    # second run of the same trade: duplicate -> None, no second API call
    assert asyncio.run(engine.trade_reviews.review_trade(tr["id"])) is None
    assert len(fake.calls) == 1
    evs = [e["event_type"] for e in engine.signal_events(tr["signal_id"])]
    assert "AI_TRADE_REVIEW" in evs
    q = engine.db.query("SELECT event_type, text FROM notifications WHERE event_type='TRADE_REVIEW'")
    assert len(q) == 1 and "ANALIZĂ TRANZACȚIE" in q[0]["text"]
    lst = engine.trade_reviews.list_payload()
    assert len(lst) == 1 and "payload" not in lst[0]
    assert engine.trade_reviews.status()["done"] == {"TRADE": 1, "DAILY": 0}


def test_on_trade_closed_runs_outside_event_loop(engine):
    tr = open_and_close_paper_trade(engine)
    fake = arm_fake_claude(engine)
    # engine hook (no running loop in tests -> runs synchronously); the trade was announced before the fake client
    # existed, so trigger it explicitly here
    assert engine.trade_reviews.on_trade_closed(tr) is True
    assert len(fake.calls) == 1
    assert engine.trade_reviews.list_payload(kind="TRADE")[0]["status"] == "DONE"


def test_daily_review_payload_and_run(engine):
    tr = open_and_close_paper_trade(engine)
    fake = arm_fake_claude(engine)
    asyncio.run(engine.trade_reviews.review_trade(tr["id"]))
    day = tr["closed_at_utc"][:10]
    p = engine.trade_reviews.build_daily_payload(day, "PAPER")
    assert p["summary"]["trades"] == 1 and p["summary"]["losses"] == 1
    assert p["trades"][0]["trade_review"]["process_quality"] == "GOOD"
    assert "by_verdict" in p["shadow_labels"] and p["previous_review"] is None
    row = asyncio.run(engine.trade_reviews.review_day(day))
    assert row["status"] == "DONE" and row["kind"] == "DAILY" and row["ref_id"] == f"PAPER:{day}"
    assert row["review"]["confidence"] == "LOW"
    assert fake.calls[-1]["output_config"]["format"]["schema"] is DAILY_REVIEW_SCHEMA
    # same day again: duplicate unless forced
    assert asyncio.run(engine.trade_reviews.review_day(day)) is None
    n_before = len(fake.calls)
    row2 = asyncio.run(engine.trade_reviews.review_day(day, force=True))
    assert row2["status"] == "DONE" and len(fake.calls) == n_before + 1
    # the next day's payload carries yesterday's lessons forward
    p2 = engine.trade_reviews.build_daily_payload("2099-01-01", "PAPER")
    assert p2 is None  # nothing happened that day -> no review
    nxt = engine.trade_reviews._previous_daily("2099-01-01", "PAPER")
    assert nxt["day"] == day and nxt["hypotheses_to_test"] == DAILY_RV["hypotheses_to_test"]


def test_daily_review_on_session_close_hook(engine):
    tr = open_and_close_paper_trade(engine)
    fake = arm_fake_claude(engine)
    assert engine.trade_reviews.on_session_closed() is True
    assert any(c["output_config"]["format"]["schema"] is DAILY_REVIEW_SCHEMA for c in fake.calls)
    assert engine.trade_reviews.list_payload(kind="DAILY")[0]["status"] == "DONE"


def test_api_error_is_recorded_not_raised(engine):
    tr = open_and_close_paper_trade(engine)
    engine.ai.settings = make_settings_like(engine.settings)

    class Boom:
        async def create(self, **kw):
            raise RuntimeError("kaput")

    engine.ai._client = SimpleNamespace(beta=SimpleNamespace(messages=Boom()), messages=Boom())
    row = asyncio.run(engine.trade_reviews.review_trade(tr["id"]))
    assert row["status"] == "ERROR" and "kaput" in row["review"]["error"]
    assert engine.ai.last_error and "kaput" in engine.ai.last_error


def test_api_routes(client, engine):
    r = client.get("/api/v1/trade-reviews")
    assert r.status_code == 200 and r.json()["items"] == [] and r.json()["status"]["trade_review_enabled"] is False
    assert client.get("/api/v1/trade-reviews?kind=X").status_code == 422
    # Claude disabled (no key) -> 409 on manual runs
    assert client.post("/api/v1/trade-reviews/daily", headers=ADMIN, json={}).status_code == 409
    assert client.post("/api/v1/trade-reviews/trade/1", headers=ADMIN).status_code == 409
    assert client.post("/api/v1/trade-reviews/daily", json={}).status_code == 401
    tr = open_and_close_paper_trade(engine)
    arm_fake_claude(engine)
    r = client.post(f"/api/v1/trade-reviews/trade/{tr['id']}", headers=ADMIN)
    assert r.status_code == 200 and r.json()["review"]["process_quality"] == "GOOD"
    assert client.post(f"/api/v1/trade-reviews/trade/{tr['id']}", headers=ADMIN).status_code == 409
    assert client.post("/api/v1/trade-reviews/trade/999", headers=ADMIN).status_code == 404
    r = client.post("/api/v1/trade-reviews/daily", headers=ADMIN, json={"day": tr["closed_at_utc"][:10]})
    assert r.status_code == 200 and r.json()["kind"] == "DAILY"
    assert client.post("/api/v1/trade-reviews/daily", headers=ADMIN, json={"day": "bad"}).status_code == 422
    r = client.get("/api/v1/trade-reviews?payload=true")
    assert len(r.json()["items"]) == 2 and all("payload" in it for it in r.json()["items"])


def test_signal_review_context_includes_history(engine):
    tr = open_and_close_paper_trade(engine)
    ctx = engine._ai_context("EURUSD")
    assert ctx["recent_trades_same_symbol"][0]["close_reason"] == "SL"
    assert ctx["recent_trades_summary"]["trades"] == 1
    assert "local_time" in ctx and "recent_bars_signal_tf" in ctx


def test_effort_setting_and_status(tmp_path):
    s = make_settings(tmp_path, CLAUDE_REVIEW_EFFORT="high")
    assert ClaudeReviewer(s).effort == "high"
    s2 = make_settings(tmp_path, CLAUDE_REVIEW_EFFORT="bogus")
    rv = ClaudeReviewer(s2)
    assert rv.effort == "medium"
    st = rv.status()
    assert st["effort"] == "medium" and st["trade_review_enabled"] is True


def test_telegram_formats():
    t = fmt_trade_review({"symbol": "EURUSD", "side": "BUY", "r_multiple": -1.0, "close_reason": "SL"}, TRADE_RV)
    assert "ANALIZĂ TRANZACȚIE" in t and "-1.00R" in t and "Verifică spreadul" in t
    d = fmt_daily_review("2026-10-29", DAILY_RV, {"trades": 1, "wins": 0, "avg_r": -1.0, "net_usd": -2.5})
    assert "BILANȚ ZILNIC 2026-10-29" in d and "nu se aplică automat" in d and "-2.50" in d
