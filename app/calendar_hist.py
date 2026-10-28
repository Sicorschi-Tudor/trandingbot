"""Economic-calendar HISTORY with numbers (EA >= CALHIST_MIN_EA, research only: news-surprise studies).

Protocol extension (one request in flight, answered in the NEXT sync, like HISTX):
  server -> EA   CALHIST|<from_utc>|<to_utc>|<min_imp>
                 from/to in the EA's UTC frame (real UTC + clock skew, shifted for the broker DST rule like HISTX
                 end_utc); min_imp = lowest MqlCalendarEvent importance wanted (1 LOW .. 3 HIGH, 0 = everything).
  EA -> server   "calendar_hist": {"from_utc":<echo>, "to_utc":<echo>, "srv_off":<TimeTradeServer()-TimeGMT()>,
                                   "ok":true, "err":0, "total":<values matched>, "truncated":false,
                                   "items":[{"id":value_id, "eid":event_id, "t":<ToUtc(value.time)>, "ccy":"USD",
                                             "cc":"US", "imp":3, "name":"Nonfarm Payrolls", "tm":time_mode,
                                             "unit":ENUM_CALENDAR_EVENT_UNIT, "mult":ENUM_CALENDAR_EVENT_MULTIPLIER,
                                             "dig":digits, "a":actual_value, "f":forecast_value, "p":prev_value,
                                             "r":revised_prev_value, "imx":impact_type, "rev":revision,
                                             "per":<value.period, server time>}]}
                 a/f/p/r are the raw MqlCalendarValue longs (value x 10^6) or null (LONG_MIN = not set; a raw
                 LONG_MIN is also accepted). Items oldest -> newest, at most EA CALHIST_MAX_ITEMS; when more values
                 matched, "truncated":true and "total" = how many matched (the server shrinks the window and retries).

Planner: covers [CALHIST_START (2015-01-01), now + 7 d] walking BACK from the newest uncovered point, one window per
request, never crossing a 1 January boundary (year by year), window size adaptive (starts at 1 year, shrinks on
truncation). A window counts as final only up to its fetch time - SETTLE_S (actuals/forecasts of recent and upcoming
releases change), so the recent zone is re-fetched every RECENT_EVERY_S. Progress lives in calendar_history_windows,
values in calendar_history (one row per MT5 value id = PRIMARY KEY value_id: a rescheduled release keeps its id
and only moves t, so later fetches update the time and the numbers in place, never add a second row).

Gating: ea_version >= CALHIST_MIN_EA, CALHIST_ENABLED (default true), bridge connected, not sim, no OPEN/MODIFY/CLOSE
in the response nor a DEMO intent pending, no HIST refill pending, no HISTX in this response nor outstanding, no bot
MT5 position (unless ARCHIVE_EXTRA_WITH_POSITIONS), at most one request per CALHIST_INTERVAL_S counted from the
previous reply (or its timeout). The table is research data only: nothing in live trading/analysis reads it.

`calendar_hist` is NOT touched by bridge.normalize_clock: this module removes the clock skew itself."""
from __future__ import annotations

import logging
import math
import sqlite3
import time
from concurrent.futures import Future
from datetime import datetime, timezone

CALHIST_MIN_EA = "1.14"
CALHIST_START = int(datetime(2015, 1, 1, tzinfo=timezone.utc).timestamp())
DAY = 86400
HORIZON_S = 7 * DAY           # also fetch upcoming releases (forecast / previous already known)
SETTLE_S = 3 * DAY            # a window is final only up to fetched_ts - SETTLE_S
RECENT_EVERY_S = 6 * 3600     # re-fetch of the not-yet-final zone
RECENT_SLACK_S = DAY          # a recent request reaches at least this far below now - SETTLE_S; an older gap
                              # is backfilled as a normal window only once it starts below that line
INTERVAL_DEFAULT_S = 30.0     # pause between the previous reply and the next CALHIST
PENDING_TIMEOUT_S = 90.0
ERR_BACKOFF_S = 30.0
ERR_BACKOFF_MAX_S = 600.0
WINDOW_MAX_S = 366 * DAY
WINDOW_MIN_S = DAY
EDGE_PAD_S = 2 * 3600         # requests overlap by 2 h on both sides (DST / skew rounding); dedupe absorbs it
EA_MAX_ITEMS = 4000           # EA cap per reply (mirrors CALHIST_MAX_ITEMS in the EA patch)
MIN_IMP_DEFAULT = 1           # LOW and up (NONE = holidays / bank closures)
MAX_ITEMS_ACCEPTED = 10000
WRITE_CHUNK = 500
LONG_MIN = -(2 ** 63)
VALUE_SCALE = 1_000_000.0

UNIT_NAMES = {0: "NONE", 1: "PERCENT", 2: "CURRENCY", 3: "HOUR", 4: "JOB", 5: "RIG", 6: "USD", 7: "PEOPLE",
              8: "MORTGAGE", 9: "VOTE", 10: "BARREL", 11: "CUBICFEET", 12: "POSITION", 13: "BUILDING"}
MULT_NAMES = {0: "NONE", 1: "THOUSANDS", 2: "MILLIONS", 3: "BILLIONS", 4: "TRILLIONS"}

log = logging.getLogger("bot")

SCHEMA = """
CREATE TABLE IF NOT EXISTS calendar_history (
    value_id INTEGER PRIMARY KEY,
    event_id INTEGER NOT NULL,
    t INTEGER NOT NULL,
    ccy TEXT,
    country TEXT,
    imp INTEGER,
    name TEXT,
    time_mode INTEGER,
    unit INTEGER,
    unit_name TEXT,
    multiplier INTEGER,
    multiplier_name TEXT,
    digits INTEGER,
    actual REAL,
    forecast REAL,
    previous REAL,
    revised_previous REAL,
    impact INTEGER,
    revision INTEGER,
    period_srv INTEGER,
    updated_ts REAL
);
CREATE INDEX IF NOT EXISTS ix_calendar_history_event ON calendar_history(event_id, t);
CREATE INDEX IF NOT EXISTS ix_calendar_history_t ON calendar_history(t);
CREATE INDEX IF NOT EXISTS ix_calendar_history_ccy ON calendar_history(ccy, t);
CREATE TABLE IF NOT EXISTS calendar_history_windows (
    from_utc INTEGER NOT NULL,
    to_utc INTEGER NOT NULL,
    n INTEGER,
    fetched_ts REAL NOT NULL,
    PRIMARY KEY (from_utc, to_utc)
);
"""

SCHEMA_COLS = ("value_id", "event_id", "t", "ccy", "country", "imp", "name", "time_mode", "unit", "unit_name",
               "multiplier", "multiplier_name", "digits", "actual", "forecast", "previous", "revised_previous",
               "impact", "revision", "period_srv", "updated_ts")

UPSERT_SQL = (
    "INSERT INTO calendar_history(event_id, t, value_id, ccy, country, imp, name, time_mode, unit, unit_name, "
    "multiplier, multiplier_name, digits, actual, forecast, previous, revised_previous, impact, revision, period_srv, "
    "updated_ts) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
    "ON CONFLICT(value_id) DO UPDATE SET event_id=excluded.event_id, t=excluded.t, ccy=excluded.ccy, "
    "country=excluded.country, imp=excluded.imp, name=excluded.name, time_mode=excluded.time_mode, "
    "unit=excluded.unit, unit_name=excluded.unit_name, multiplier=excluded.multiplier, "
    "multiplier_name=excluded.multiplier_name, digits=excluded.digits, "
    "actual=COALESCE(excluded.actual, actual), forecast=COALESCE(excluded.forecast, forecast), "
    "previous=COALESCE(excluded.previous, previous), "
    "revised_previous=COALESCE(excluded.revised_previous, revised_previous), impact=excluded.impact, "
    "revision=MAX(COALESCE(revision, 0), COALESCE(excluded.revision, 0)), period_srv=excluded.period_srv, "
    "updated_ts=excluded.updated_ts"
)


# ---- schema (called once from DB.__init__) -------------------------------------
def _rekey_old_table(conn: sqlite3.Connection) -> None:
    """A calendar_history from before the value_id key (PRIMARY KEY (event_id, t)) is rebuilt keyed on value_id:
    one row per value id, the most recently updated one wins (rows without a value id are dropped)."""
    cols = {r[1]: r[5] for r in conn.execute("PRAGMA table_info(calendar_history)").fetchall()}
    if not cols or cols.get("value_id") == 1:
        return
    names = [c for c in cols if c in SCHEMA_COLS]
    sel = ", ".join(names)
    conn.execute("BEGIN")
    try:
        conn.execute("ALTER TABLE calendar_history RENAME TO calendar_history_old_key")
        for ix in ("ix_calendar_history_t", "ix_calendar_history_ccy", "ix_calendar_history_event"):
            conn.execute(f"DROP INDEX IF EXISTS {ix}")
        for stmt in SCHEMA.split(";"):
            if "calendar_history " in stmt or "calendar_history(" in stmt:
                if stmt.strip():
                    conn.execute(stmt)
        conn.execute(f"INSERT OR REPLACE INTO calendar_history({sel}) SELECT {sel} FROM calendar_history_old_key "
                     f"WHERE value_id IS NOT NULL ORDER BY COALESCE(updated_ts, 0), t")
        conn.execute("DROP TABLE calendar_history_old_key")
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    log.warning("[calendar_hist] calendar_history recheiat pe value_id")


def migrate(conn: sqlite3.Connection) -> list[str]:
    """CREATE TABLE IF NOT EXISTS, plus a one-time re-key of an old (event_id, t)-keyed calendar_history.
    Never raises (a failure here must not stop the server)."""
    try:
        before = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        if "calendar_history" in before:
            _rekey_old_table(conn)
        conn.executescript(SCHEMA)
        return [t for t in ("calendar_history", "calendar_history_windows") if t not in before]
    except Exception as ex:  # pragma: no cover - defensive
        log.error("[calendar_hist] migrare eșuată: %r", ex)
        return []


# ---- helpers ---------------------------------------------------------------------
def _version_tuple(v) -> tuple[int, ...]:
    out = []
    for part in str(v or "").strip().split("."):
        digits = "".join(ch for ch in part if ch.isdigit())
        if not digits:
            break
        out.append(int(digits))
    return tuple(out)


def ea_supported(version) -> bool:
    v = _version_tuple(version)
    return bool(v) and v >= _version_tuple(CALHIST_MIN_EA)


def year_floor(t: int) -> int:
    d = datetime.fromtimestamp(t, timezone.utc)
    return int(datetime(d.year, 1, 1, tzinfo=timezone.utc).timestamp())


def _setting(rt, name: str, default):
    v = getattr(rt.settings, name, default)
    return default if v is None else v


def _int(x) -> int | None:
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return None
    if isinstance(x, float) and not math.isfinite(x):
        return None
    return int(x)


def _value(x) -> float | None:
    """Raw MqlCalendarValue long (value x 10^6) -> float; null / LONG_MIN / garbage -> None."""
    if x is None or isinstance(x, bool) or not isinstance(x, (int, float)):
        return None
    if isinstance(x, float) and not math.isfinite(x):
        return None
    if x <= LONG_MIN or abs(x) >= 2 ** 63 - 1:
        return None
    return float(x) / VALUE_SCALE


def plan_of(rt) -> dict:
    plan = getattr(rt, "calhist_plan", None)
    if plan is None:
        plan = {"pending": None, "windows": None, "window_s": WINDOW_MAX_S, "last_reply_ts": None,
                "recent_ts": None, "errors": 0, "retry_at": 0.0, "rows": 0, "done_logged": False}
        rt.calhist_plan = plan
    return plan


def _windows(rt) -> list[tuple[int, int, float]]:
    plan = plan_of(rt)
    if plan["windows"] is None:
        try:
            rows = rt.db.query("SELECT from_utc, to_utc, fetched_ts FROM calendar_history_windows")
        except Exception:
            rows = []
        plan["windows"] = [(int(r["from_utc"]), int(r["to_utc"]), float(r["fetched_ts"])) for r in rows]
    return plan["windows"]


def covered(rt) -> list[tuple[int, int]]:
    """Merged intervals already final: [from, min(to, fetched_ts - SETTLE_S)]."""
    iv = sorted((lo, min(hi, int(f - SETTLE_S))) for lo, hi, f in _windows(rt))
    out: list[list[int]] = []
    for lo, hi in iv:
        if hi <= lo:
            continue
        if out and lo <= out[-1][1]:
            out[-1][1] = max(out[-1][1], hi)
        else:
            out.append([lo, hi])
    return [(a, b) for a, b in out]


def gaps(rt, now: float) -> list[tuple[int, int]]:
    """Uncovered intervals inside [CALHIST_START, now + HORIZON_S], newest first."""
    top = int(now + HORIZON_S)
    out = []
    x = top
    for lo, hi in reversed(covered(rt)):
        if hi < x:
            g_lo = max(hi, CALHIST_START)
            if x > g_lo:
                out.append((g_lo, x))
        x = min(x, lo)
        if x <= CALHIST_START:
            break
    if x > CALHIST_START:
        out.append((CALHIST_START, x))
    return out


# ---- planner -------------------------------------------------------------------------
def _bot_positions_open(rt) -> bool:
    magic = int(rt.settings.MT5_MAGIC)
    return any(isinstance(p, dict) and int(p.get("magic") or 0) == magic for p in (rt.mt5_positions or []))


def _ea_shift(rt, t: int, now: float) -> int:
    """UTC instant -> EA UTC frame (its skewed TimeGMT() and today's server offset), like HISTX end_utc."""
    from . import archive
    return t + int(rt.bridge.get("clock_skew_s") or 0) + archive._end_delta(rt, t, now)


def next_request(rt, now: float, commands_pending: bool = False, hist_pending: bool = False,
                 histx_pending: bool = False) -> str | None:
    """At most one CALHIST line per sync; None when nothing should be asked now."""
    s = rt.settings
    if not bool(_setting(rt, "CALHIST_ENABLED", True)) or s.data_source == "sim":
        return None
    if not rt.bridge_connected() or not ea_supported(rt.bridge.get("ea_version")):
        return None
    if commands_pending or hist_pending or histx_pending:
        return None
    if (getattr(rt, "archive_plan", None) or {}).get("pending") is not None:
        return None  # the EA still owes a HISTX batch: one history job at a time
    plan = plan_of(rt)
    pend = plan["pending"]
    if pend is not None:
        if now - pend["sent_ts"] < PENDING_TIMEOUT_S:
            return None
        plan["pending"] = None
        plan["last_reply_ts"] = now
        if pend.get("recent"):
            plan["recent_ts"] = None  # the recent zone is still due
        _note_error(rt, now, "fără răspuns de la EA")
    if plan["retry_at"] > now:
        return None
    if _bot_positions_open(rt) and not bool(_setting(rt, "ARCHIVE_EXTRA_WITH_POSITIONS", False)):
        return None
    interval = max(0.0, float(_setting(rt, "CALHIST_INTERVAL_S", INTERVAL_DEFAULT_S) or 0))
    if plan["last_reply_ts"] is not None and now - plan["last_reply_ts"] < interval:
        return None
    recent_due = plan["recent_ts"] is None or now - plan["recent_ts"] >= RECENT_EVERY_S
    window_s = int(plan["window_s"])
    settle = int(now - SETTLE_S)  # a window fetched now is final only up to here
    for g_lo, g_hi in gaps(rt, now):
        if g_hi > settle:
            # "recent" is decided per requested window, not per gap: a gap reaching into the not-yet-final zone
            # is split at now - SETTLE_S, so a small window_s can never hide the older history below it.
            if recent_due:
                hi = g_hi
                # always reach below now - SETTLE_S (whatever window_s is), else the request adds no coverage
                lo = max(g_lo, min(hi - window_s, settle - RECENT_SLACK_S), year_floor(hi - 1))
                if hi > lo:
                    return _request(rt, lo, hi, now, True, forced=hi - lo > window_s)
            if g_lo >= settle - RECENT_SLACK_S:
                continue  # only a sliver below the final line: the next recent re-fetch covers it
            g_hi = settle  # backfill the final part now, as a normal (non-recent) window
        hi = g_hi
        lo = max(g_lo, hi - window_s, year_floor(hi - 1))
        if hi <= lo:
            continue
        return _request(rt, lo, hi, now, False)
    if not plan["done_logged"]:
        plan["done_logged"] = True
        rt.log("INFO", "calendar", "Istoric calendar economic complet din 2015 (se reîmprospătează doar zona recentă)")
    return None


def _request(rt, lo: int, hi: int, now: float, recent: bool, forced: bool = False) -> str:
    plan = plan_of(rt)
    ea_from = _ea_shift(rt, lo, now) - EDGE_PAD_S
    ea_to = _ea_shift(rt, hi, now) + EDGE_PAD_S
    min_imp = int(_setting(rt, "CALHIST_MIN_IMP", MIN_IMP_DEFAULT))
    min_imp = min(3, max(0, min_imp))
    plan["pending"] = {"lo": lo, "hi": hi, "ea_from": ea_from, "ea_to": ea_to, "sent_ts": now, "recent": recent,
                       "forced": forced}
    if recent:
        plan["recent_ts"] = now
    return f"CALHIST|{ea_from}|{ea_to}|{min_imp}"


def _note_error(rt, now: float, why: str) -> None:
    plan = plan_of(rt)
    plan["errors"] += 1
    n = plan["errors"]
    plan["retry_at"] = now + min(ERR_BACKOFF_MAX_S, ERR_BACKOFF_S * (2 ** min(n - 1, 6)))
    if n == 1 or n % 10 == 0:
        rt.log("WARN", "calendar", f"Istoric calendar: MT5 nu a răspuns ({why}) — reîncerc automat (încercarea {n})")


# ---- ingestion -------------------------------------------------------------------------
def _fix_time(rt, t_ea: int, srv_off: int | None, now: float) -> int:
    """EA UTC (today's offset, skewed TimeGMT) -> real UTC with the broker DST rule valid at that instant."""
    from . import archive
    skew = int(rt.bridge.get("clock_skew_s") or 0)
    if srv_off is not None:
        rule = archive.server_tz_rule(rt, now)
        if rule is not None and archive.rule_offset_utc(rule, now) == srv_off + skew:
            t_srv = t_ea + srv_off
            ro = archive.rule_offset_local(rule, t_srv)
            if ro is not None:
                return t_srv - ro
    return t_ea - skew


def clean_items(rt, raw, srv_off: int | None, now: float) -> list[tuple]:
    rows: dict[int, tuple] = {}  # value_id -> row (the MT5 value id is the identity of a release)
    for it in raw if isinstance(raw, list) else []:
        if not isinstance(it, dict):
            continue
        eid = _int(it.get("eid"))
        t_ea = _int(it.get("t"))
        vid = _int(it.get("id"))
        if eid is None or eid <= 0 or t_ea is None or t_ea <= 0 or vid is None or vid <= 0:
            continue
        t = _fix_time(rt, t_ea, srv_off, now)
        if t < CALHIST_START - 31 * DAY or t > now + 400 * DAY:
            continue
        imp = _int(it.get("imp"))
        unit = _int(it.get("unit"))
        mult = _int(it.get("mult"))
        name = str(it.get("name") or "")[:200]
        ccy = str(it.get("ccy") or "")[:8].upper() or None
        cc = str(it.get("cc") or "")[:8].upper() or None
        rows[vid] = (
            eid, t, vid, ccy, cc, imp if imp is not None and 0 <= imp <= 3 else None, name,
            _int(it.get("tm")), unit, UNIT_NAMES.get(unit) if unit is not None else None, mult,
            MULT_NAMES.get(mult) if mult is not None else None, _int(it.get("dig")),
            _value(it.get("a")), _value(it.get("f")), _value(it.get("p")), _value(it.get("r")),
            _int(it.get("imx")), _int(it.get("rev")), _int(it.get("per")), now,
        )
    return sorted(rows.values(), key=lambda r: (r[1], r[0], r[2]))


def ingest_payload(rt, reply, now: float | None = None) -> list[Future]:
    """Handle the EA's "calendar_hist" key (dict, or a list of dicts). Writes go to the archive writer thread."""
    from . import archive
    now = now or time.time()
    futures: list[Future] = []
    items = reply if isinstance(reply, list) else [reply]
    plan = plan_of(rt)
    for rep in items[:2]:
        if not isinstance(rep, dict):
            continue
        srv_off = _int(rep.get("srv_off"))
        raw = rep.get("items")
        if isinstance(raw, list):
            raw = raw[:MAX_ITEMS_ACCEPTED]
        rows = clean_items(rt, raw, srv_off, now)
        pend = plan["pending"]
        r_from, r_to = _int(rep.get("from_utc")), _int(rep.get("to_utc"))
        is_reply = pend is not None and r_from == pend["ea_from"] and r_to == pend["ea_to"]
        if rows:
            futures.append(archive.submit(_write_rows, rt, rows))
        if not is_reply:
            continue  # resent after a lost HTTP 200 / late reply: rows only, never touch the plan
        plan["pending"] = None
        plan["last_reply_ts"] = now
        err = _int(rep.get("err")) or 0
        if rep.get("ok") is False or (err and not rows):
            if pend.get("recent"):
                plan["recent_ts"] = None
            _note_error(rt, now, f"eroare {err}")
            continue
        plan["errors"] = 0
        plan["retry_at"] = 0.0
        total = _int(rep.get("total"))
        span = pend["hi"] - pend["lo"]
        if rep.get("truncated") is True and span > WINDOW_MIN_S and not pend.get("forced"):
            # too many values for one reply: shrink the window, retry the same top edge (rows kept anyway)
            if total and total > 0:
                new = int(span * 0.8 * EA_MAX_ITEMS / total)
            else:
                new = span // 2
            plan["window_s"] = max(WINDOW_MIN_S, min(span // 2, new))
            if pend.get("recent"):
                plan["recent_ts"] = None  # the recent zone is still due
            continue
        w_hi = pend["hi"]
        if rep.get("truncated") is True and pend.get("forced"):
            # a recent window wider than window_s (it must reach below now - SETTLE_S) cannot shrink by window_s:
            # items come oldest -> newest, so only the part up to the last value received counts as covered and
            # the next recent request starts there (progress every time, no retry loop on the same window)
            plan["window_s"] = max(WINDOW_MIN_S, min(int(plan["window_s"]), span // 2))
            w_hi = min(w_hi, max((r[1] for r in rows), default=pend["lo"]))
            if w_hi < now - SETTLE_S:
                plan["recent_ts"] = None  # the not-yet-final zone was not reached: still due
            if w_hi <= pend["lo"]:
                _note_error(rt, now, "răspuns trunchiat fără valori")
                continue
        elif rep.get("truncated") is True:
            rt.log("WARN", "calendar", f"Istoric calendar: fereastra minimă tot depășește limita EA "
                                       f"({total} valori) — păstrez primele {len(rows)}")
        window = (pend["lo"], w_hi, now)
        _windows(rt).append(window)
        futures.append(archive.submit(_write_window, rt, window, len(rows)))
        if len(rows) < EA_MAX_ITEMS // 4:
            plan["window_s"] = min(WINDOW_MAX_S, int(plan["window_s"]) * 2)
        plan["rows"] = int(plan["rows"]) + len(rows)
    return futures


def _write_rows(rt, rows: list[tuple]) -> int:
    try:
        for i in range(0, len(rows), WRITE_CHUNK):
            rt.db.executemany(UPSERT_SQL, rows[i:i + WRITE_CHUNK])
    except Exception as ex:
        log.error("[calendar_hist] Scriere calendar_history eșuată: %r", ex)
        return 0
    return len(rows)


def _write_window(rt, window: tuple[int, int, float], n: int) -> None:
    try:
        rt.db.execute("INSERT INTO calendar_history_windows(from_utc, to_utc, n, fetched_ts) VALUES(?,?,?,?) "
                      "ON CONFLICT(from_utc, to_utc) DO UPDATE SET n=excluded.n, fetched_ts=excluded.fetched_ts",
                      (window[0], window[1], n, window[2]))
    except Exception as ex:
        log.error("[calendar_hist] Scriere calendar_history_windows eșuată: %r", ex)


# ---- bridge hook -------------------------------------------------------------------------
def on_sync(engine, payload: dict, commands: list[str], now: float, hist_pending: bool = False) -> list[str]:
    """One call from bridge.process_sync, after the HISTX line was (maybe) appended:
        commands = commands + calendar_hist.on_sync(engine, payload, commands, now, hist_pending=...)
    Ingests "calendar_hist" (if present) and returns [] or ["CALHIST|..."]. Never raises."""
    rt = engine.rt
    try:
        if "calendar_hist" in payload:
            ingest_payload(rt, payload.get("calendar_hist"), now)
        if not ea_supported(rt.bridge.get("ea_version")):
            return []
        histx = any(c.split("|", 1)[0] == "HISTX" for c in commands)
        from .bridge import archive_commands_pending
        line = next_request(rt, now, commands_pending=archive_commands_pending(engine, commands),
                            hist_pending=hist_pending, histx_pending=histx)
        return [line] if line else []
    except Exception as ex:
        try:
            rt.log("ERROR", "calendar", f"Istoric calendar: eroare {ex!r}")
        except Exception:
            pass
        return []


# ---- status ------------------------------------------------------------------------------
def status_view(rt, now: float | None = None) -> dict:
    now = now or time.time()
    plan = plan_of(rt)
    cov = covered(rt)
    g = gaps(rt, now)
    older = [x for x in g if x[1] <= now - SETTLE_S]
    by_year: dict[str, int] = {}
    total = 0
    try:
        for r in rt.db.query("SELECT strftime('%Y', t, 'unixepoch') AS y, COUNT(*) AS n FROM calendar_history "
                             "GROUP BY y ORDER BY y"):
            by_year[str(r["y"])] = int(r["n"])
            total += int(r["n"])
    except Exception:
        pass
    pend = plan["pending"]
    return {
        "enabled": bool(_setting(rt, "CALHIST_ENABLED", True)), "ea_min": CALHIST_MIN_EA,
        "ea_version": rt.bridge.get("ea_version"), "ea_ok": ea_supported(rt.bridge.get("ea_version")),
        "start_utc": CALHIST_START, "covered": [list(c) for c in cov],
        "backfill_done": not older, "gaps": [list(x) for x in g],
        "window_days": round(float(plan["window_s"]) / DAY, 2),
        "pending": None if pend is None else {"from_utc": pend["lo"], "to_utc": pend["hi"], "recent": pend["recent"]},
        "errors": plan["errors"], "rows": total, "rows_by_year": by_year,
    }
