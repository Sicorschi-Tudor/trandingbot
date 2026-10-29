"""Telegram outbox + sender worker (retry/backoff/idempotency). Token never logged."""
from __future__ import annotations

import asyncio
import html
import time
from datetime import timezone
from zoneinfo import ZoneInfo

import httpx

from .config import Settings, iso, parse_iso, redact, utcnow
from .db import DB

MAX_ATTEMPTS = 6
MAX_LEN = 4096
DISCLAIMER = "Probabilitățile sunt estimări euristice, nu garanții."

e = html.escape


def _local(ts_iso: str | None, tz: str) -> str:
    d = parse_iso(ts_iso)
    if not d:
        return "-"
    try:
        z = ZoneInfo(tz)
    except Exception:
        z = timezone.utc
    return d.astimezone(z).strftime("%d.%m %H:%M")


def _pct(x) -> str:
    try:
        return f"{float(x) * 100:.0f}%"
    except (TypeError, ValueError):
        return "-"


def truncate(text: str, limit: int = MAX_LEN) -> str:
    if len(text) <= limit:
        return text
    cut = text[: limit - 2]
    nl = cut.rfind("\n")
    if nl > limit // 2:
        cut = cut[:nl]
    # never leave a dangling html entity / tag fragment
    amp, semi = cut.rfind("&"), cut.rfind(";")
    if amp > semi:
        cut = cut[:amp]
    lt, gt = cut.rfind("<"), cut.rfind(">")
    if lt > gt:
        cut = cut[:lt]
    return cut + "…"


def exec_status_label(signal: dict) -> str:
    st = signal.get("status")
    mode = signal.get("mode")
    if st == "EXECUTED":
        return f"FILLED ({mode})"
    if st == "INTENT_CREATED":
        return f"SENT ({mode}) — așteaptă confirmarea brokerului"
    if st == "REJECTED":
        return f"REJECTED ({mode})"
    if st == "NO_TRADE":
        return "NO TRADE"
    return "NOT SENT (doar semnal)"


def fmt_signal(sig: dict, tz: str) -> str:
    p = sig.get("probabilities") or {}
    ai = sig.get("ai") or {}
    reasons = sig.get("reasons") or []
    lines = [
        f"📊 <b>SEMNAL NOU — {e(sig['symbol'])}</b> [{e(sig.get('feed_label') or '')}]",
        f"Direcție: <b>{e(sig['direction'])}</b>",
        f"Timeframe: {e(sig['timeframe'])} | Orizont evaluat: {e(str(sig.get('horizon_minutes')))} min",
        f"Intrare orientativă: {e(str(sig.get('entry')))}",
        f"Stop Loss: {e(str(sig.get('stop_loss')))}",
        f"Take Profit: {e(str(sig.get('take_profit')))} (R:R {e(str(sig.get('rr')))})",
        f"Probabilități estimate (euristic, necalibrat): UP {_pct(p.get('up'))} | DOWN {_pct(p.get('down'))} | NEUTRAL {_pct(p.get('neutral'))}",
        f"Spread la emitere: {e(str(sig.get('spread_points')))} puncte",
        f"Expiră: {_local(sig.get('expires_at_utc'), tz)} ({e(tz)})",
    ]
    if reasons:
        lines.append("Motiv: " + e("; ".join(str(r) for r in reasons[:5]))[:700])
    lines.append("Riscuri/invalidare: " + e(str(sig.get("invalidation") or "-"))[:500])
    ai_status = ai.get("status")
    if ai_status == "DONE":
        lines.append(f"🤖 Claude: <b>{e(str(ai.get('verdict')))}</b> — {e(str(ai.get('explanation') or ''))[:700]}")
        if ai.get("risks"):
            lines.append("Riscuri Claude: " + e("; ".join(ai["risks"][:4]))[:500])
    elif ai_status == "ERROR":
        lines.append("🤖 Claude: eroare (" + e(str(ai.get("explanation") or ""))[:200] + ")")
    elif ai_status == "DISABLED":
        lines.append("🤖 Claude: dezactivat")
    lines.append(f"Status execuție: <b>{e(exec_status_label(sig))}</b>")
    if sig.get("status_reason"):
        lines.append("Detaliu: " + e(str(sig["status_reason"]))[:400])
    lines.append(f"Mod: {e(str(sig.get('mode')))} | Model: {e(str(sig.get('strategy_version')))}")
    lines.append(f"ID: <code>{e(sig['id'])}</code>")
    lines.append("")
    lines.append(f"<i>{e(DISCLAIMER)} Semnalul nu înseamnă că un ordin a fost executat.</i>")
    return truncate("\n".join(lines))


def fmt_intent(it: dict, tz: str) -> str:
    st = it.get("state")
    res = it.get("result") or {}
    icon = {"FILLED": "✅", "PAPER_FILLED": "✅", "FAILED": "❌", "REJECTED_RISK": "⛔", "UNKNOWN": "⚠️",
            "QUEUED": "📤", "DELIVERED": "📤"}.get(st, "ℹ️")
    title = {"FILLED": "ORDIN EXECUTAT", "PAPER_FILLED": "ORDIN EXECUTAT (PAPER)", "FAILED": "ORDIN RESPINS",
             "REJECTED_RISK": "ORDIN BLOCAT DE RISK ENGINE", "UNKNOWN": "STARE NECUNOSCUTĂ",
             "QUEUED": "ORDIN TRIMIS", "DELIVERED": "ORDIN TRIMIS"}.get(st, st)
    lines = [f"{icon} <b>{e(str(title))}</b> — {e(str(it.get('kind')))} {e(str(it.get('symbol') or ''))} {e(str(it.get('side') or ''))}"]
    lines.append(f"Mod: {e(str(it.get('mode')))} | Volum: {e(str(it.get('volume')))}")
    if it.get("sl") or it.get("tp"):
        lines.append(f"SL: {e(str(it.get('sl')))} | TP: {e(str(it.get('tp')))}")
    if res:
        lines.append(f"Rezultat: {e(str(res.get('retcode')))} {e(str(res.get('retcode_desc')))} | preț {e(str(res.get('price')))}"
                     + (f" | poziție {e(str(res.get('position')))}" if res.get("position") else ""))
        if res.get("comment"):
            lines.append("Comentariu: " + e(str(res["comment"]))[:200])
    if st == "REJECTED_RISK":
        bad = [c for c in (it.get("risk_check") or {}).get("checks", []) if not c.get("ok")]
        for c in bad[:6]:
            lines.append(f"• {e(c['name'])}: {e(str(c['detail']))}")
    lines.append(f"ID intent: <code>{e(it['id'])}</code> | {_local(it.get('updated_at_utc'), tz)}")
    return truncate("\n".join(lines))


def fmt_trade(tr: dict, tz: str) -> str:
    net = float(tr.get("profit") or 0) + float(tr.get("commission") or 0) + float(tr.get("swap") or 0)
    icon = "🟢" if net > 0 else ("🔴" if net < 0 else "⚪")
    lines = [
        f"{icon} <b>POZIȚIE ÎNCHISĂ — {e(str(tr.get('symbol')))}</b> ({e(str(tr.get('mode')))})",
        f"{e(str(tr.get('side')))} {e(str(tr.get('volume')))} | {e(str(tr.get('price_open')))} → {e(str(tr.get('price_close')))}",
        f"Motiv închidere: {e(str(tr.get('close_reason')))}",
        f"Rezultat net: <b>{net:+.2f}</b> (profit {float(tr.get('profit') or 0):+.2f}, comision {float(tr.get('commission') or 0):+.2f}, swap {float(tr.get('swap') or 0):+.2f})",
        f"Ticket: {e(str(tr.get('ticket')))} | {_local(tr.get('closed_at_utc'), tz)}",
    ]
    if tr.get("mode") == "PAPER":
        lines.append("<i>Rezultat simulat (paper), nu profit real.</i>")
    return truncate("\n".join(lines))


def fmt_trade_review(tr: dict, rv: dict) -> str:
    """Claude's post-trade analysis (review_json of a DONE trade_reviews row)."""
    q = str(rv.get("process_quality") or "?")
    icon = {"GOOD": "🟢", "ACCEPTABLE": "🟡", "POOR": "🔴"}.get(q, "ℹ️")
    r = tr.get("r_multiple")
    r_txt = f"{float(r):+.2f}R" if r is not None else "R necunoscut"
    lines = [
        f"🧠 <b>ANALIZĂ TRANZACȚIE — {e(str(tr.get('symbol')))} {e(str(tr.get('side')))}</b> ({r_txt}, {e(str(tr.get('close_reason')))})",
        f"{icon} Proces: <b>{e(q)}</b> · {e(str(rv.get('outcome_class') or ''))} · ieșire {e(str(rv.get('exit_assessment') or ''))}",
        e(str(rv.get("summary") or "")),
    ]
    lessons = [str(x) for x in (rv.get("lessons") or []) if x]
    if lessons:
        lines.append("<b>Lecții:</b>\n" + "\n".join(f"• {e(x)}" for x in lessons[:4]))
    tags = [str(x) for x in (rv.get("tags") or []) if x]
    if tags:
        lines.append("<i>" + e(", ".join(tags[:6])) + "</i>")
    lines.append("<i>Analiză informativă — nu modifică setările botului.</i>")
    return truncate("\n".join(lines))


def fmt_daily_review(day: str, rv: dict, summary: dict | None = None) -> str:
    s = summary or {}
    lines = [f"📋 <b>BILANȚ ZILNIC {e(day)}</b> (încredere {e(str(rv.get('confidence') or '?'))})"]
    if s:
        n = s.get("trades") or 0
        avg = s.get("avg_r")
        lines.append(f"Tranzacții: {n} · câștigătoare {s.get('wins') or 0} · R mediu "
                     f"{f'{float(avg):+.2f}' if avg is not None else '—'} · net {float(s.get('net_usd') or 0):+.2f}")
    lines.append(f"<b>{e(str(rv.get('headline') or ''))}</b>")
    lines.append(e(str(rv.get("summary") or "")))
    pats = [str(x) for x in (rv.get("patterns") or []) if x]
    if pats:
        lines.append("<b>Tipare:</b>\n" + "\n".join(f"• {e(x)}" for x in pats[:5]))
    if rv.get("veto_assessment"):
        lines.append(f"<b>Verdicte AI:</b> {e(str(rv['veto_assessment']))}")
    hyp = [str(x) for x in (rv.get("hypotheses_to_test") or []) if x]
    if hyp:
        lines.append("<b>De testat în backtest (nu se aplică automat):</b>\n" + "\n".join(f"• {e(x)}" for x in hyp[:5]))
    return truncate("\n".join(lines))


def fmt_risk_alert(text: str) -> str:
    return truncate(f"⚠️ <b>ALERTĂ RISC</b>\n{e(text)}")


def fmt_modify(pos: dict, action: dict) -> str:
    return truncate(
        f"🛡 <b>SL MODIFICAT ({e(action.get('reason', ''))})</b> — {e(str(pos.get('symbol')))} {e(str(pos.get('side')))}\n"
        f"SL nou: {e(str(action.get('sl')))} | TP: {e(str(action.get('tp')))} | ticket {e(str(pos.get('ticket')))}"
    )


def fmt_daily_start(info: dict) -> str:
    return truncate(
        "🌅 <b>SESIUNEA A ÎNCEPUT</b>\n"
        f"Program: {e(str(info.get('start')))}–{e(str(info.get('end')))} ({e(str(info.get('tz')))})\n"
        f"Mod: {e(str(info.get('mode')))} | Execuție: {'armată' if info.get('execution_enabled') else 'dezarmată'}\n"
        f"Simboluri: {e(', '.join(info.get('symbols') or []))} | Feed: {e(str(info.get('feed_label')))}\n"
        f"<i>{e(DISCLAIMER)}</i>"
    )


class TelegramNotifier:
    def __init__(self, settings: Settings, db: DB, log_fn=None):
        self.settings = settings
        self.db = db
        self.log_fn = log_fn
        self.last_ok_utc: str | None = None
        self.last_error: str | None = None
        self._client: httpx.AsyncClient | None = None
        self.wake = asyncio.Event()

    @property
    def configured(self) -> bool:
        return self.settings.telegram_configured

    @property
    def active(self) -> bool:
        return bool(self.settings.TELEGRAM_ENABLED) and self.configured

    def _log(self, level: str, msg: str) -> None:
        if self.log_fn:
            self.log_fn(level, "telegram", msg)

    def pending_count(self) -> int:
        r = self.db.query_one("SELECT COUNT(*) AS n FROM notifications WHERE status='PENDING'")
        return int(r["n"]) if r else 0

    def status(self) -> dict:
        return {"enabled": bool(self.settings.TELEGRAM_ENABLED), "configured": self.configured,
                "last_ok_utc": self.last_ok_utc, "last_error": self.last_error, "pending": self.pending_count()}

    def button_url(self, signal_id: str | None) -> str | None:
        base = (self.settings.PUBLIC_APP_URL or "").rstrip("/")
        if signal_id and base.startswith("https://"):
            return f"{base}/#signal/{signal_id}"
        return None

    def enqueue(self, ref_id: str, event_type: str, text: str, signal_id: str | None = None) -> bool:
        """Insert into outbox; idempotency key = ref_id:event_type. Returns True if newly queued."""
        if not self.active:
            return False
        key = f"{ref_id}:{event_type}"
        cur = self.db.execute(
            "INSERT OR IGNORE INTO notifications(idempotency_key, event_type, ref_id, text, button_url, status, attempts, "
            "next_retry_at, created_at_utc) VALUES(?,?,?,?,?, 'PENDING', 0, 0, ?)",
            (key, event_type, ref_id, text, self.button_url(signal_id), iso(utcnow())),
        )
        if cur.rowcount:
            try:
                self.wake.set()
            except Exception:
                pass
            return True
        return False

    async def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=10.0))
        return self._client

    async def aclose(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    def build_payload(self, text: str, button_url: str | None) -> dict:
        payload: dict = {"chat_id": self.settings.TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML",
                         "disable_web_page_preview": True}
        tid = (self.settings.TELEGRAM_MESSAGE_THREAD_ID or "").strip()
        if tid:
            try:
                payload["message_thread_id"] = int(tid)
            except ValueError:
                pass
        if button_url and button_url.startswith("https://"):
            payload["reply_markup"] = {"inline_keyboard": [[{"text": "Deschide semnalul", "url": button_url}]]}
        return payload

    async def send(self, text: str, button_url: str | None = None) -> dict:
        """returns {"ok", "error", "retry_after", "permanent"}"""
        if not self.configured:
            return {"ok": False, "error": "Telegram nu este configurat (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID)", "retry_after": None, "permanent": True}
        url = f"https://api.telegram.org/bot{self.settings.TELEGRAM_BOT_TOKEN}/sendMessage"
        try:
            client = await self._http()
            r = await client.post(url, json=self.build_payload(text, button_url))
        except httpx.HTTPError as ex:
            return {"ok": False, "error": redact(f"rețea: {type(ex).__name__}", self.settings.secret_values()),
                    "retry_after": None, "permanent": False}
        try:
            data = r.json()
        except Exception:
            data = {}
        if r.status_code == 200 and data.get("ok"):
            return {"ok": True, "error": None, "retry_after": None, "permanent": False}
        desc = redact(str(data.get("description") or f"HTTP {r.status_code}"), self.settings.secret_values())
        if r.status_code == 429:
            ra = (data.get("parameters") or {}).get("retry_after") or 5
            return {"ok": False, "error": f"429 rate limit: {desc}", "retry_after": float(ra), "permanent": False}
        permanent = r.status_code in (400, 401, 403, 404)
        return {"ok": False, "error": f"HTTP {r.status_code}: {desc}", "retry_after": None, "permanent": permanent}

    async def process_due(self) -> int:
        now = time.time()
        rows = self.db.query(
            "SELECT * FROM notifications WHERE status='PENDING' AND next_retry_at <= ? ORDER BY id LIMIT 10", (now,)
        )
        sent = 0
        for n in rows:
            if not self.configured:
                break
            res = await self.send(n["text"], n["button_url"])
            attempts = int(n["attempts"]) + 1
            if res["ok"]:
                self.db.execute("UPDATE notifications SET status='SENT', attempts=?, sent_at_utc=?, last_error=NULL WHERE id=?",
                                (attempts, iso(utcnow()), n["id"]))
                self.last_ok_utc = iso(utcnow())
                self.last_error = None
                sent += 1
                await asyncio.sleep(0.05)
                continue
            self.last_error = res["error"]
            if res["permanent"] or attempts >= MAX_ATTEMPTS:
                self.db.execute("UPDATE notifications SET status='FAILED', attempts=?, last_error=? WHERE id=?",
                                (attempts, res["error"], n["id"]))
                self._log("ERROR", f"Mesaj Telegram {n['idempotency_key']} eșuat definitiv: {res['error']}")
            else:
                delay = res["retry_after"] if res["retry_after"] else min(2 ** attempts, 300)
                self.db.execute("UPDATE notifications SET attempts=?, last_error=?, next_retry_at=? WHERE id=?",
                                (attempts, res["error"], time.time() + delay, n["id"]))
                self._log("WARN", f"Mesaj Telegram {n['idempotency_key']} reîncercare în {delay:.0f}s: {res['error']}")
                if res["retry_after"]:
                    break
        return sent

    async def worker(self) -> None:
        while True:
            try:
                if self.active:
                    await self.process_due()
            except asyncio.CancelledError:
                raise
            except Exception as ex:
                self.last_error = redact(repr(ex), self.settings.secret_values())
                self._log("ERROR", f"worker: {self.last_error}")
            self.wake.clear()
            try:
                await asyncio.wait_for(self.wake.wait(), timeout=1.0)
            except asyncio.TimeoutError:
                pass

    def recent(self, limit: int = 50) -> list[dict]:
        return self.db.query(
            "SELECT id, idempotency_key, event_type, ref_id, status, attempts, last_error, created_at_utc, sent_at_utc "
            "FROM notifications ORDER BY id DESC LIMIT ?", (limit,))


