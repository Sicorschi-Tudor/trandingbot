"""Intents -> bridge commands (DEMO) or paper fills (PAPER); reconciliation of results."""
from __future__ import annotations

import json
import time
import uuid
from typing import Callable

from .config import iso, utcnow
from .paper import PaperBroker
from .state import Runtime

RETCODES = {
    10004: "REQUOTE", 10006: "REJECT", 10008: "PLACED", 10009: "DONE", 10010: "DONE_PARTIAL",
    10012: "TIMEOUT", 10013: "INVALID", 10014: "INVALID_VOLUME", 10015: "INVALID_PRICE", 10016: "INVALID_STOPS",
    10018: "MARKET_CLOSED", 10019: "NO_MONEY", 10020: "PRICE_CHANGED", 10021: "PRICE_OFF",
    10027: "CLIENT_DISABLES_AT", 10030: "INVALID_FILL",
}
OK_RETCODES = {10008, 10009, 10010}
COMMAND_TIMEOUT_S = 30.0
QUEUE_TIMEOUT_S = 30.0
PENDING_STATES = ("QUEUED", "DELIVERED", "UNKNOWN")
RECONCILE_GRACE_S = 60.0  # after the 30 s timeout, how long UNKNOWN may wait for evidence before FAILED
OPEN_MAX_AGE_S = 20.0  # an OPEN older than this is never delivered (EA v1.10 also rejects it via expires_utc)
FAILURE_WINDOW_S = 3600.0  # consecutive_failures only counts failures this recent
REF_DRIFT_MIN_POINTS = 5
REF_DRIFT_SL_FRACTION = 0.25
EA_EXT_OPEN_VERSION = (1, 10)  # first EA version that understands ref_price/max_slip/expires_utc


def ea_version_tuple(v) -> tuple[int, ...]:
    out = []
    for part in str(v or "").strip().split("."):
        digits = "".join(ch for ch in part if ch.isdigit())
        if not digits:
            break
        out.append(int(digits))
    return tuple(out)


def norm_login(v) -> int | None:
    try:
        return int(v) if v is not None and str(v) != "" else None
    except (TypeError, ValueError):
        return None


def retcode_desc(code) -> str:
    try:
        return RETCODES.get(int(code), f"RETCODE_{int(code)}")
    except (TypeError, ValueError):
        return "UNKNOWN"


def fmt_num(v: float | int | None, digits: int = 8) -> str:
    if v is None:
        return "0"
    s = f"{float(v):.{digits}f}".rstrip("0").rstrip(".")
    return s or "0"


def open_comment(intent_id: str) -> str:
    return "ai:" + intent_id[:8]


class Executor:
    def __init__(self, rt: Runtime, paper: PaperBroker, deviation_points: int = 20):
        self.rt = rt
        self.db = rt.db
        self.paper = paper
        self.deviation_points = deviation_points
        self.on_update: Callable[[dict, str | None], None] | None = None

    # ---- persistence -----------------------------------------------------
    def _save(self, it: dict, comment: str | None = None, reason: str | None = None,
              delivered_at: float | None = None, insert: bool = False) -> None:
        it["updated_at_utc"] = iso(utcnow())
        data = json.dumps(it)
        if insert:
            self.db.execute(
                "INSERT INTO intents(id, created_at_utc, updated_at_utc, kind, signal_id, symbol, state, mode, ticket, "
                "comment, reason, delivered_at, data_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (it["id"], it["created_at_utc"], it["updated_at_utc"], it["kind"], it.get("signal_id"), it.get("symbol"),
                 it["state"], it["mode"], it.get("ticket"), comment, reason, delivered_at, data),
            )
        else:
            sets = ["updated_at_utc=?", "state=?", "ticket=?", "data_json=?"]
            params: list = [it["updated_at_utc"], it["state"], it.get("ticket"), data]
            if delivered_at is not None:
                sets.append("delivered_at=?")
                params.append(delivered_at)
            params.append(it["id"])
            self.db.execute(f"UPDATE intents SET {', '.join(sets)} WHERE id=?", params)

    def get(self, intent_id: str) -> dict | None:
        row = self.db.query_one("SELECT data_json FROM intents WHERE id=?", (intent_id,))
        return json.loads(row["data_json"]) if row else None

    def _row(self, intent_id: str) -> dict | None:
        return self.db.query_one("SELECT * FROM intents WHERE id=?", (intent_id,))

    def list(self, limit: int = 100) -> list[dict]:
        rows = self.db.query("SELECT data_json FROM intents ORDER BY created_at_utc DESC, rowid DESC LIMIT ?", (limit,))
        return [json.loads(r["data_json"]) for r in rows]

    def pending(self) -> list[dict]:
        rows = self.db.query(
            "SELECT data_json FROM intents WHERE state IN ('QUEUED','DELIVERED','UNKNOWN') ORDER BY created_at_utc"
        )
        return [json.loads(r["data_json"]) for r in rows]

    def has_pending_for_ticket(self, ticket: int, kinds: tuple[str, ...] = ("CLOSE", "MODIFY"),
                               states: tuple[str, ...] = PENDING_STATES) -> bool:
        kq = ",".join("?" * len(kinds))
        sq = ",".join("?" * len(states))
        r = self.db.query_one(
            f"SELECT 1 AS x FROM intents WHERE ticket=? AND kind IN ({kq}) AND state IN ({sq}) LIMIT 1",
            (int(ticket), *kinds, *states),
        )
        return bool(r)

    def count_pending_open(self, mode: str, exclude_tickets: set[int] | None = None, recent_filled_s: float = 30.0) -> int:
        """OPEN intents in flight (not yet visible as positions); also very recent FILLED ones not yet synced."""
        exclude = exclude_tickets or set()
        rows = self.db.query(
            "SELECT state, ticket, updated_at_utc FROM intents WHERE kind='OPEN' AND mode=? "
            "AND state IN ('QUEUED','DELIVERED','UNKNOWN','FILLED')", (mode,))
        now = time.time()
        n = 0
        for r in rows:
            if r["ticket"] is not None and int(r["ticket"]) in exclude:
                continue
            if r["state"] == "FILLED":
                upd = _parse_ts(r["updated_at_utc"])
                if r["ticket"] is None or not upd or now - upd > recent_filled_s:
                    continue
            n += 1
        return n

    def consecutive_failures(self, ticket: int, kind: str, now: float | None = None,
                             window_s: float = FAILURE_WINDOW_S) -> tuple[int, float | None]:
        """(number of consecutive FAILED intents of this kind for the ticket within the last `window_s`,
        ts of the newest one). Older failures no longer count, so a burst of rejections cannot block
        break-even/time-stop retries for the rest of the trade."""
        now = now if now is not None else time.time()
        rows = self.db.query(
            "SELECT state, updated_at_utc FROM intents WHERE ticket=? AND kind=? ORDER BY created_at_utc DESC, rowid DESC LIMIT 20",
            (int(ticket), kind))
        n, last = 0, None
        for r in rows:
            if r["state"] != "FAILED":
                break
            upd = _parse_ts(r["updated_at_utc"])
            if upd is None or now - upd > window_s:
                break
            if last is None:
                last = upd
            n += 1
        return n, last

    def pending_open_exposures(self, mode: str) -> list[tuple[str, str]]:
        rows = self.db.query(
            "SELECT data_json FROM intents WHERE kind='OPEN' AND mode=? AND state IN ('QUEUED','DELIVERED','UNKNOWN')", (mode,))
        out = []
        for r in rows:
            d = json.loads(r["data_json"])
            if d.get("symbol") and d.get("side"):
                out.append((d["symbol"], d["side"]))
        return out

    def has_pending_open(self, symbol: str) -> bool:
        r = self.db.query_one(
            "SELECT 1 AS x FROM intents WHERE symbol=? AND kind='OPEN' AND state IN ('QUEUED','DELIVERED','UNKNOWN') LIMIT 1",
            (symbol,),
        )
        return bool(r)

    def open_intent_for_ticket(self, ticket: int) -> dict | None:
        row = self.db.query_one(
            "SELECT data_json FROM intents WHERE kind='OPEN' AND ticket=? ORDER BY created_at_utc DESC LIMIT 1", (int(ticket),)
        )
        return json.loads(row["data_json"]) if row else None

    def open_intent_by_comment(self, comment: str) -> dict | None:
        if not comment or not comment.startswith("ai:"):
            return None
        row = self.db.query_one(
            "SELECT data_json FROM intents WHERE kind='OPEN' AND comment=? ORDER BY created_at_utc DESC LIMIT 1", (comment,)
        )
        return json.loads(row["data_json"]) if row else None

    def _emit(self, it: dict, prev: str | None) -> None:
        self.rt.bus.publish("intent", it)
        if self.on_update:
            try:
                self.on_update(it, prev)
            except Exception as e:  # pragma: no cover
                self.rt.log("ERROR", "executor", f"on_update failed: {e!r}")

    @staticmethod
    def _new(kind: str, mode: str, symbol: str | None, side: str | None, volume: float | None,
             sl: float | None, tp: float | None, ticket: int | None, signal_id: str | None, risk_check: dict | None) -> dict:
        now = iso(utcnow())
        return {
            "id": str(uuid.uuid4()), "created_at_utc": now, "updated_at_utc": now, "kind": kind,
            "signal_id": signal_id, "symbol": symbol, "side": side, "volume": volume, "sl": sl, "tp": tp,
            "ticket": ticket, "mode": mode, "state": "QUEUED",
            "risk_check": risk_check, "result": None,
        }

    # ---- account binding / OPEN guard ------------------------------------
    def current_login(self) -> int | None:
        return norm_login((self.rt.mt5_account or {}).get("login"))

    def _open_guard(self, symbol: str, side: str, sl: float | None, info: dict | None, now: float | None = None) -> dict:
        """ref_price (ask for BUY, bid for SELL at queue time), max_slip_points (25% of the SL distance, min 5)
        and the queue timestamp used for expiry."""
        now = now if now is not None else time.time()
        q = info or self.rt.quotes.get(symbol) or {}
        ref = q.get("ask") if side == "BUY" else q.get("bid")
        point = q.get("point") or (self.rt.quotes.get(symbol) or {}).get("point") or 0
        out: dict = {"queued_ts": now, "ref_price": None, "max_slip_points": None}
        try:
            ref = float(ref) if ref else None
            point = float(point)
        except (TypeError, ValueError):
            return out
        if ref and point > 0:
            sl_pts = abs(ref - float(sl)) / point if sl else 0.0
            out["ref_price"] = ref
            out["max_slip_points"] = max(REF_DRIFT_MIN_POINTS, int(round(REF_DRIFT_SL_FRACTION * sl_pts)))
        return out

    def _fail(self, it: dict, desc: str, comment: str, level: str = "WARN") -> None:
        prev = it["state"]
        it["state"] = "FAILED"
        it["result"] = {"retcode": None, "retcode_desc": desc, "price": None, "volume": it.get("volume"),
                        "position": it.get("ticket"), "comment": comment}
        self._save(it)
        self.rt.log(level, "executor", f"Comanda {it['kind']} {it.get('symbol') or ''} {it['id'][:8]} → FAILED ({desc}): {comment}")
        self._emit(it, prev)

    def fail_other_accounts(self, new_login, reason: str = "ACCOUNT_CHANGED") -> int:
        """FAIL every pending DEMO intent (QUEUED/DELIVERED/UNKNOWN) not bound to `new_login`."""
        cur = norm_login(new_login)
        rows = self.db.query(
            "SELECT data_json FROM intents WHERE mode='DEMO' AND state IN ('QUEUED','DELIVERED','UNKNOWN')")
        n = 0
        for r in rows:
            it = json.loads(r["data_json"])
            if norm_login(it.get("account_login")) == cur and cur is not None:
                continue
            self._fail(it, reason, f"Contul MT5 s-a schimbat (comanda era pentru contul {it.get('account_login')})")
            n += 1
        return n

    # ---- submit ----------------------------------------------------------
    def submit_open(self, mode: str, symbol: str, side: str, volume: float, sl: float, tp: float,
                    info: dict, risk_check: dict, signal_id: str | None = None) -> dict:
        rc = {"passed": bool(risk_check.get("passed")), "checks": risk_check.get("checks", [])}
        it = self._new("OPEN", mode, symbol, side, volume, sl, tp, None, signal_id, rc)
        comment = open_comment(it["id"])
        if not rc["passed"]:
            it["state"] = "REJECTED_RISK"
            self._save(it, comment=comment, reason="RISK", insert=True)
            self.rt.log("WARN", "risk", f"Intent OPEN {symbol} {side} respins de Risk Engine")
            self._emit(it, None)
            return it
        if mode == "PAPER":
            pos = self.paper.open(symbol, side, volume, sl, tp, info, comment=comment, intent_id=it["id"], signal_id=signal_id)
            it["state"] = "PAPER_FILLED"
            it["ticket"] = pos["ticket"]
            it["result"] = {"retcode": 10009, "retcode_desc": "DONE (paper)", "price": pos["price_open"],
                            "volume": volume, "position": pos["ticket"], "comment": comment}
            self._save(it, comment=comment, reason="ENTRY", insert=True)
            self.rt.log("INFO", "executor", f"PAPER OPEN {side} {volume} {symbol} @ {pos['price_open']} (ticket {pos['ticket']})")
        elif mode == "DEMO":
            it["state"] = "QUEUED"
            it["account_login"] = self.current_login()
            it.update(self._open_guard(symbol, side, sl, info))
            self._save(it, comment=comment, reason="ENTRY", insert=True)
            self.rt.log("INFO", "executor", f"DEMO OPEN {side} {volume} {symbol} pus în coadă (cmd {it['id'][:8]})")
        else:
            it["state"] = "REJECTED_RISK"
            self._save(it, comment=comment, reason=f"MODE_{mode}", insert=True)
        self._emit(it, None)
        return it

    def submit_close(self, source: str, ticket: int, symbol: str, side: str | None, volume: float,
                     info: dict | None, reason: str = "BOT", signal_id: str | None = None) -> dict:
        mode = "PAPER" if source == "PAPER" else "DEMO"
        it = self._new("CLOSE", mode, symbol, side, volume, None, None, int(ticket), signal_id, None)
        if mode == "DEMO":
            it["account_login"] = self.current_login()
        if mode == "PAPER":
            if not info or not info.get("bid"):
                it["state"] = "FAILED"
                it["result"] = {"retcode": 10021, "retcode_desc": "PRICE_OFF", "price": None, "volume": volume,
                                "position": ticket, "comment": "fără cotație"}
            else:
                tr = self.paper.close(ticket, info, reason="MANUAL" if reason in ("MANUAL", "CLOSE_ALL") else "BOT")
                if tr:
                    it["state"] = "PAPER_FILLED"
                    it["result"] = {"retcode": 10009, "retcode_desc": "DONE (paper)", "price": tr["price_close"],
                                    "volume": tr["volume"], "position": ticket, "comment": reason}
                else:
                    it["state"] = "FAILED"
                    it["result"] = {"retcode": 10013, "retcode_desc": "INVALID", "price": None, "volume": volume,
                                    "position": ticket, "comment": "poziție inexistentă"}
            self._save(it, reason=reason, insert=True)
        else:
            self._save(it, reason=reason, insert=True)
        self.rt.log("INFO", "executor", f"CLOSE {mode} ticket {ticket} ({reason}) → {it['state']}")
        self._emit(it, None)
        return it

    def submit_modify(self, source: str, ticket: int, symbol: str, side: str | None, sl: float, tp: float,
                      reason: str = "MODIFY") -> dict:
        mode = "PAPER" if source == "PAPER" else "DEMO"
        it = self._new("MODIFY", mode, symbol, side, None, sl, tp, int(ticket), None, None)
        it["reason"] = reason
        if mode == "DEMO":
            it["account_login"] = self.current_login()
        if mode == "PAPER":
            ok = self.paper.modify(ticket, sl, tp)
            it["state"] = "PAPER_FILLED" if ok else "FAILED"
            it["result"] = {"retcode": 10009 if ok else 10013, "retcode_desc": "DONE (paper)" if ok else "INVALID",
                            "price": None, "volume": None, "position": ticket, "comment": reason}
        self._save(it, reason=reason, insert=True)
        self.rt.log("INFO", "executor", f"MODIFY {mode} ticket {ticket} SL={sl} TP={tp} ({reason}) → {it['state']}")
        self._emit(it, None)
        return it

    # ---- bridge side -----------------------------------------------------
    def take_commands(self, now: float | None = None) -> list[str]:
        """QUEUED DEMO intents -> command lines. Only intents bound to the current MT5 login are delivered;
        an OPEN older than OPEN_MAX_AGE_S is never delivered."""
        rows = self.db.query(
            "SELECT id, comment, created_at_utc, data_json FROM intents WHERE state='QUEUED' AND mode='DEMO' ORDER BY created_at_utc"
        )
        lines = []
        now = now if now is not None else time.time()
        login = self.current_login()
        ext = ea_version_tuple(self.rt.bridge.get("ea_version")) >= EA_EXT_OPEN_VERSION
        for r in rows:
            it = json.loads(r["data_json"])
            if norm_login(it.get("account_login")) != login:
                self._fail(it, "ACCOUNT_CHANGED", f"Comanda era pentru contul {it.get('account_login')}, "
                                                  f"contul MT5 curent este {login} — nu se livrează", level="ERROR")
                continue
            if it["kind"] == "OPEN":
                queued = it.get("queued_ts") or _parse_ts(r["created_at_utc"])
                if queued is None or now - float(queued) > OPEN_MAX_AGE_S:
                    self._fail(it, "EXPIRED_BEFORE_DELIVERY",
                               f"OPEN nelivrat în {int(OPEN_MAX_AGE_S)} s — semnalul este prea vechi, nu se trimite")
                    continue
            line = self.command_line(it, r["comment"] or "", extended=ext)
            if not line:
                continue
            prev = it["state"]
            it["state"] = "DELIVERED"
            self._save(it, delivered_at=now)
            lines.append(line)
            self._emit(it, prev)
        return lines

    def command_line(self, it: dict, comment: str, extended: bool = False) -> str | None:
        digits = int((self.rt.quotes.get(it.get("symbol") or "", {}) or {}).get("digits") or 5)
        if it["kind"] == "OPEN":
            fields = [
                "OPEN", it["id"], it["symbol"], it["side"], fmt_num(it["volume"]),
                fmt_num(round(it["sl"], digits), digits), fmt_num(round(it["tp"], digits), digits),
                str(int(self.deviation_points)), comment.replace("|", "")[:31],
            ]
            if extended:
                # EA >= 1.10: ref_price|max_slip_points|expires_utc (0 = check disabled); expiry in the EA's clock
                ref = it.get("ref_price")
                slip = it.get("max_slip_points")
                queued = it.get("queued_ts") or _parse_ts(it.get("created_at_utc")) or time.time()
                # exact EA clock offset: timezone step + measured drift (EA compares with its own TimeGMT())
                skew = int(self.rt.bridge.get("ea_gmt_skew_s") or 0) + int(self.rt.bridge.get("ea_clock_drift_s") or 0)
                fields += [
                    fmt_num(round(float(ref), digits), digits) if ref else "0",
                    str(int(slip)) if ref and slip else "0",
                    str(int(float(queued) + OPEN_MAX_AGE_S) + skew),
                ]
            return "|".join(fields)
        if it["kind"] == "MODIFY":
            return "|".join(["MODIFY", it["id"], str(int(it["ticket"])),
                             fmt_num(round(it["sl"] or 0, digits), digits), fmt_num(round(it["tp"] or 0, digits), digits)])
        if it["kind"] == "CLOSE":
            return "|".join(["CLOSE", it["id"], str(int(it["ticket"])), fmt_num(it["volume"])])
        return None

    def apply_results(self, results: list[dict]) -> None:
        for res in results or []:
            cmd_id = str(res.get("cmd_id") or "")
            if not cmd_id:
                continue
            it = self.get(cmd_id)
            if not it:
                self.rt.log("WARN", "executor", f"Rezultat pentru comandă necunoscută {cmd_id[:8]}")
                continue
            if it["state"] not in ("QUEUED", "DELIVERED", "UNKNOWN"):
                continue
            prev = it["state"]
            code = res.get("retcode")
            ok = bool(res.get("ok")) and (code is None or int(code) in OK_RETCODES)
            it["result"] = {
                "retcode": code, "retcode_desc": retcode_desc(code) if code is not None else str(res.get("retcode_desc") or ""),
                "price": res.get("price"), "volume": res.get("volume"), "position": res.get("position"),
                "comment": str(res.get("comment") or res.get("retcode_desc") or ""),
            }
            it["state"] = "FILLED" if ok else "FAILED"
            if ok and it["kind"] == "OPEN" and res.get("position"):
                it["ticket"] = int(res["position"])
            self._save(it)
            lvl = "INFO" if ok else "WARN"
            self.rt.log(lvl, "executor", f"Rezultat {it['kind']} {it.get('symbol')} cmd {cmd_id[:8]}: {it['state']} "
                                         f"retcode={code} {it['result']['retcode_desc']}")
            self._emit(it, prev)

    def fail_queued(self, reason: str) -> None:
        rows = self.db.query("SELECT data_json FROM intents WHERE state='QUEUED' AND mode='DEMO'")
        for r in rows:
            it = json.loads(r["data_json"])
            prev = it["state"]
            it["state"] = "FAILED"
            it["result"] = {"retcode": None, "retcode_desc": "NOT_DELIVERED", "price": None, "volume": it.get("volume"),
                            "position": it.get("ticket"), "comment": reason}
            self._save(it)
            self.rt.log("ERROR", "executor", f"Comanda {it['kind']} {it['id'][:8]} anulată: {reason}")
            self._emit(it, prev)

    def check_timeouts(self, now: float | None = None) -> list[dict]:
        now = now or time.time()
        changed = []
        rows = self.db.query("SELECT id, created_at_utc, delivered_at, data_json, state FROM intents WHERE state IN ('QUEUED','DELIVERED')")
        for r in rows:
            it = json.loads(r["data_json"])
            prev = it["state"]
            if r["state"] == "DELIVERED" and r["delivered_at"] and now - r["delivered_at"] > COMMAND_TIMEOUT_S:
                it["state"] = "UNKNOWN"
                self._save(it)
                self.rt.log("ERROR", "executor", f"Comanda {it['kind']} {it['id'][:8]} fără rezultat după 30 s → UNKNOWN (reconciliere prin poziții)")
            elif r["state"] == "QUEUED":
                created = _parse_ts(r["created_at_utc"])
                if created and now - created > QUEUE_TIMEOUT_S:
                    it["state"] = "FAILED"
                    it["result"] = {"retcode": None, "retcode_desc": "NOT_DELIVERED", "price": None, "volume": it.get("volume"),
                                    "position": it.get("ticket"), "comment": "Comanda nu a fost livrată (bridge offline)"}
                    self._save(it)
                    self.rt.log("WARN", "executor", f"Comanda {it['kind']} {it['id'][:8]} nelivrată în 30 s → FAILED (nu se retrimite)")
                else:
                    continue
            else:
                continue
            changed.append(it)
            self._emit(it, prev)
        return changed

    def reconcile(self, positions: list[dict], deals: list[dict] | None = None, now: float | None = None,
                  magic: int | None = None) -> None:
        """Resolve UNKNOWN intents using a fresh MT5 positions (+ deals) snapshot; FAILED after a deadline."""
        rows = self.db.query("SELECT id, comment, delivered_at, data_json FROM intents WHERE state='UNKNOWN'")
        if not rows:
            return
        now = now or time.time()
        deals = deals or []
        by_ticket = {int(p["ticket"]): p for p in positions}
        claimed = {int(r["ticket"]) for r in self.db.query(
            "SELECT ticket FROM intents WHERE kind='OPEN' AND ticket IS NOT NULL AND state IN ('FILLED','UNKNOWN')")}
        login = self.current_login()
        for r in rows:
            it = json.loads(r["data_json"])
            prev = it["state"]
            bound = norm_login(it.get("account_login"))
            if it.get("mode") == "DEMO" and bound is not None and login is not None and bound != login:
                # positions of another login are never evidence for this intent
                self._fail(it, "ACCOUNT_CHANGED", f"Intent UNKNOWN pentru contul {bound}, contul MT5 curent este {login}")
                continue
            resolved = False
            delivered = r["delivered_at"] or 0
            expired = bool(delivered) and now - delivered > COMMAND_TIMEOUT_S + RECONCILE_GRACE_S
            if it["kind"] == "OPEN":
                cm = r["comment"] or "~"
                match = next((p for p in positions if (p.get("comment") or "") == cm), None)
                if match is None and magic is not None and delivered:
                    match = next((p for p in positions if int(p.get("magic") or 0) == magic
                                  and p.get("symbol") == it.get("symbol") and int(p["ticket"]) not in claimed
                                  and (p.get("time") or 0) >= delivered - 60), None)
                deal = None
                if match is None:
                    deal = next((d for d in deals if int(d.get("entry", -1)) == 0 and (d.get("comment") or "") == cm), None)
                if match or deal:
                    pos_id = int(match["ticket"]) if match else int(deal.get("position_id") or 0)
                    it["ticket"] = pos_id
                    it["state"] = "FILLED"
                    it["result"] = {"retcode": None, "retcode_desc": "RECONCILED",
                                    "price": (match or {}).get("price_open") or (deal or {}).get("price"),
                                    "volume": (match or deal or {}).get("volume"), "position": pos_id,
                                    "comment": "găsit în poziții" if match else "găsit în deal-uri"}
                    claimed.add(pos_id)
                    resolved = True
            elif it["kind"] == "CLOSE":
                if it.get("ticket") and int(it["ticket"]) not in by_ticket:
                    it["state"] = "FILLED"
                    it["result"] = {"retcode": None, "retcode_desc": "RECONCILED", "price": None, "volume": it.get("volume"),
                                    "position": it["ticket"], "comment": "poziția nu mai există"}
                    resolved = True
            elif it["kind"] == "MODIFY":
                p = by_ticket.get(int(it.get("ticket") or 0))
                q = self.rt.quotes.get(it.get("symbol") or "") or {}
                tol = max(float(q.get("point") or 0), float(q.get("tick_size") or 0), 1e-9) / 2
                if p is None or (abs((p.get("sl") or 0) - (it.get("sl") or 0)) <= tol
                                 and abs((p.get("tp") or 0) - (it.get("tp") or 0)) <= tol):
                    it["state"] = "FILLED"
                    it["result"] = {"retcode": None, "retcode_desc": "RECONCILED", "price": None, "volume": None,
                                    "position": it.get("ticket"), "comment": "SL/TP confirmate în poziții"}
                    resolved = True
            if resolved:
                self._save(it)
                self.rt.log("INFO", "executor", f"Intent {it['kind']} {it['id'][:8]} reconciliat → FILLED")
                self._emit(it, prev)
            elif expired:
                it["state"] = "FAILED"
                it["result"] = {"retcode": None, "retcode_desc": "NOT_EXECUTED (reconciled)", "price": None,
                                "volume": it.get("volume"), "position": it.get("ticket"),
                                "comment": "Fără dovadă de execuție în poziții/deal-uri — considerată neexecutată"}
                self._save(it)
                self.rt.log("WARN", "executor", f"Intent {it['kind']} {it['id'][:8]} UNKNOWN → FAILED (neexecutat, reconciliat)")
                self._emit(it, prev)


def _parse_ts(s: str | None) -> float | None:
    from .config import parse_iso
    d = parse_iso(s)
    return d.timestamp() if d else None
