import asyncio
import json
from types import SimpleNamespace

import pytest

from app.ai_claude import REVIEW_SCHEMA, ClaudeReviewer
from app.config import redact
from app.db import DB
from app.telegram import DISCLAIMER, TelegramNotifier, fmt_signal, fmt_trade, truncate
from tests.conftest import make_settings


def sample_signal(**kw):
    s = {
        "id": "11111111-2222-3333-4444-555555555555", "created_at_utc": "2026-10-28T08:00:00Z", "symbol": "EURUSD",
        "timeframe": "M5", "bar_time": 1, "direction": "BUY", "horizon_minutes": 60,
        "probabilities": {"up": 0.7, "down": 0.18, "neutral": 0.12}, "entry": 1.0801, "stop_loss": 1.0795,
        "take_profit": 1.0811, "rr": 1.67, "spread_points": 10, "expires_at_utc": "2026-10-28T08:15:00Z",
        "invalidation": "SL <atins> & EMA50", "reasons": ["<script>alert(1)</script>", "RSI 60 & MACD"],
        "indicators": {}, "ai": {"status": "DONE", "verdict": "AGREE", "explanation": "Trend <b>clar</b> & curat",
                                 "risks": ["Spread > normal"], "model": "claude-opus-5-5"},
        "status": "NEW", "status_reason": "", "mode": "PAPER", "feed_label": "SIMULATED",
        "strategy_version": "trend-v1", "intent_id": None,
    }
    s.update(kw)
    return s


def test_signal_message_escapes_and_disclaimer():
    txt = fmt_signal(sample_signal(), "Europe/Chisinau")
    assert "<script>" not in txt and "&lt;script&gt;" in txt
    assert "Trend &lt;b&gt;clar&lt;/b&gt; &amp; curat" in txt
    assert "SL &lt;atins&gt; &amp; EMA50" in txt
    assert DISCLAIMER in txt
    assert "10:15" in txt  # expiry in Chisinau time (UTC+2 on 28 Oct 2026)
    assert "NOT SENT" in txt
    assert len(txt) <= 4096


def test_trade_message_net():
    txt = fmt_trade({"symbol": "EURUSD", "mode": "DEMO", "side": "BUY", "volume": 0.01, "price_open": 1.08,
                     "price_close": 1.081, "close_reason": "TP", "profit": 1.0, "commission": -0.07, "swap": 0,
                     "ticket": 5, "closed_at_utc": "2026-10-28T08:00:00Z"}, "UTC")
    assert "+0.93" in txt


def test_truncate_no_broken_entity():
    t = truncate("a" * 4090 + "&amp;bbbbbbbbbbbb", 4096)
    assert len(t) <= 4096 and not t.rstrip("…").endswith("&am")


def test_button_only_for_https(tmp_path):
    s = make_settings(tmp_path, TELEGRAM_ENABLED=True, TELEGRAM_BOT_TOKEN="123456:ABCDEFGHIJKLMNOPQRSTUVWXYZ",
                      TELEGRAM_CHAT_ID="-100123", PUBLIC_APP_URL="http://127.0.0.1:8000", TELEGRAM_MESSAGE_THREAD_ID="7")
    tg = TelegramNotifier(s, DB(s.db_path))
    assert tg.button_url("abc") is None
    p = tg.build_payload("x", tg.button_url("abc"))
    assert "reply_markup" not in p and p["message_thread_id"] == 7 and p["parse_mode"] == "HTML"
    s2 = make_settings(tmp_path, TELEGRAM_ENABLED=True, TELEGRAM_BOT_TOKEN="1:x", TELEGRAM_CHAT_ID="1",
                       PUBLIC_APP_URL="https://bot.example.com/")
    tg2 = TelegramNotifier(s2, DB(s2.db_path))
    assert tg2.button_url("abc") == "https://bot.example.com/#signal/abc"
    assert tg2.build_payload("x", tg2.button_url("abc"))["reply_markup"]["inline_keyboard"][0][0]["url"].startswith("https://")


def test_outbox_idempotent_and_retry(tmp_path):
    s = make_settings(tmp_path, TELEGRAM_ENABLED=True, TELEGRAM_BOT_TOKEN="123456:ABCDEFGHIJKLMNOPQRSTUVWXYZ",
                      TELEGRAM_CHAT_ID="-100123")
    tg = TelegramNotifier(s, DB(s.db_path))
    assert tg.enqueue("sig1", "NEW_SIGNAL", "hello", signal_id="sig1") is True
    assert tg.enqueue("sig1", "NEW_SIGNAL", "hello again") is False
    assert tg.pending_count() == 1
    calls = []

    async def fake_send(text, button_url=None):
        calls.append(text)
        if len(calls) == 1:
            return {"ok": False, "error": "429 rate limit", "retry_after": 0.0, "permanent": False}
        return {"ok": True, "error": None, "retry_after": None, "permanent": False}

    tg.send = fake_send
    asyncio.run(tg.process_due())
    row = tg.db.query_one("SELECT * FROM notifications")
    assert row["status"] == "PENDING" and row["attempts"] == 1
    tg.db.execute("UPDATE notifications SET next_retry_at=0")
    asyncio.run(tg.process_due())
    row = tg.db.query_one("SELECT * FROM notifications")
    assert row["status"] == "SENT" and row["attempts"] == 2


def test_outbox_permanent_failure(tmp_path):
    s = make_settings(tmp_path, TELEGRAM_ENABLED=True, TELEGRAM_BOT_TOKEN="1:x", TELEGRAM_CHAT_ID="1")
    tg = TelegramNotifier(s, DB(s.db_path))
    tg.enqueue("a", "E", "t")

    async def bad(text, button_url=None):
        return {"ok": False, "error": "HTTP 400: chat not found", "retry_after": None, "permanent": True}

    tg.send = bad
    asyncio.run(tg.process_due())
    assert tg.db.query_one("SELECT status FROM notifications")["status"] == "FAILED"


def test_disabled_telegram_does_not_enqueue(tmp_path):
    s = make_settings(tmp_path)
    tg = TelegramNotifier(s, DB(s.db_path))
    assert tg.enqueue("x", "NEW_SIGNAL", "t") is False


def test_redaction(tmp_path):
    s = make_settings(tmp_path, TELEGRAM_BOT_TOKEN="123456789:AAAbbbCCCdddEEEfffGGGhhhIII", ANTHROPIC_API_KEY="sk-ant-api03-secretsecret")
    msg = "url https://api.telegram.org/bot123456789:AAAbbbCCCdddEEEfffGGGhhhIII/sendMessage key sk-ant-api03-secretsecret adm-token"
    out = redact(msg)
    assert "AAAbbb" not in out and "secretsecret" not in out and "adm-token" not in out


# ---- Claude ---------------------------------------------------------------
def test_ai_disabled_without_key(tmp_path):
    r = asyncio.run(ClaudeReviewer(make_settings(tmp_path)).review(sample_signal()))
    assert r["status"] == "DISABLED" and r["verdict"] is None


def test_schema_strict():
    assert REVIEW_SCHEMA["additionalProperties"] is False
    assert set(REVIEW_SCHEMA["required"]) == {"verdict", "explanation", "risks", "invalidation"}


def test_parse_response_refusal_and_ok():
    with pytest.raises(ValueError):
        ClaudeReviewer.parse_response(SimpleNamespace(stop_reason="refusal", content=[]))
    resp = SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text=json.dumps(
        {"verdict": "CAUTION", "explanation": "x", "risks": [], "invalidation": "y"}))])
    assert ClaudeReviewer.parse_response(resp)["verdict"] == "CAUTION"


def test_review_with_fake_client(tmp_path):
    s = make_settings(tmp_path, ANTHROPIC_API_KEY="sk-ant-test-key-123456")
    rv = ClaudeReviewer(s)
    captured = {}

    class FakeBeta:
        async def create(self, **kw):
            captured.update(kw)
            return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text=json.dumps(
                {"verdict": "VETO", "explanation": "Trend contrar pe H1", "risks": ["r1"], "invalidation": "peste 1.09"}))])

    rv._client = SimpleNamespace(beta=SimpleNamespace(messages=FakeBeta()))
    r = asyncio.run(rv.review(sample_signal()))
    assert r["status"] == "DONE" and r["verdict"] == "VETO"
    assert captured["betas"] == ["server-side-fallback-2026-07-01"]
    assert captured["extra_body"] == {"fallbacks": "default"}
    assert captured["output_config"]["format"]["type"] == "json_schema"
    assert "thinking" not in captured and "temperature" not in captured
    assert rv.last_ok_utc is not None


def test_review_timeout(tmp_path):
    s = make_settings(tmp_path, ANTHROPIC_API_KEY="sk-ant-test-key-123456", CLAUDE_TIMEOUT_SECONDS=0.05)
    rv = ClaudeReviewer(s)

    class Slow:
        async def create(self, **kw):
            await asyncio.sleep(1)

    rv._client = SimpleNamespace(beta=SimpleNamespace(messages=Slow()))
    r = asyncio.run(rv.review(sample_signal()))
    assert r["status"] == "ERROR" and "timeout" in r["explanation"]
