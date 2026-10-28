"""Bar history archive (bars_archive) backfilled from the EA (v1.11+, HISTX) + live spread statistics.

The planner asks the EA for one batch at a time (HISTX|sym|tf|end_utc|count), walking backwards from the
oldest archived bar until ARCHIVE_DAYS are covered or the broker has nothing older. Once every base pair
([signal_tf, htf_tf, M15] x active symbols) reached ARCHIVE_DAYS, the extended targets of ARCHIVE_EXTRA (TF:days, e.g.
H1:1095,H4:1825,D1:3650; off by default, enabled for research in .env) are walked, D1 first, at most one request per
ARCHIVE_EXTRA_INTERVAL_S counted from the previous extended reply (or its timeout), never while the bot holds MT5
positions. DB writes run on a single background thread so the event loop never waits on a 5000-row insert."""
from __future__ import annotations

import logging
import math
import time
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .config import TF_SECONDS, iso

ARCHIVE_EA_VERSION = (1, 11)
ARCHIVE_EXTRA_TF = "M15"
MAX_BATCH = 5000
EXTRA_MAX_BATCH = 3000     # extended (multi-year) walks: smaller batches, shorter CopyRates stalls in the EA
EXTRA_OFF = ("", "0", "off", "none", "false", "no")
PENDING_TIMEOUT_S = 60.0   # no reply to a HISTX in this time -> retry later
EXTRA_INTERVAL_DEFAULT_S = 60.0  # pause between the end of one extended HISTX and the next (EA free ≥ ~2/3 of the time)
ERR_BACKOFF_S = 30.0       # EA series not ready: retry after 30 s, 60 s, ... (max ERR_BACKOFF_MAX_S)
ERR_BACKOFF_MAX_S = 600.0
WRITE_CHUNK = 500
SPREAD_SAMPLE_S = 10.0     # at most one spread sample per symbol every 10 s
SPREAD_FLUSH_S = 60.0
SPREAD_MAX_TICK_AGE_S = 60.0  # frozen quotes (weekend, disconnected) would skew the stats
# Broker server-time rules (MT5_SERVER_TZ). The EA converts with TODAY's offset only; bars from before the broker's
# last DST switch are re-converted with the offset that was valid then. name -> (zone, extra shift in seconds)
SERVER_TZ_RULES = {"EET": ("Europe/Athens", 0), "NY": ("America/New_York", 7 * 3600)}  # NY = New York close (GMT+2/+3)
AUTO_RULE_ORDER = ("NY", "EET")  # tie (both rules give today's offset): NY close, the usual GMT+2/+3 broker clock
TZ_SETTING_KEY = "archive_server_tz"  # {account server: rule detected while the rules disagreed}

log = logging.getLogger("bot")

_pool: ThreadPoolExecutor | None = None


def _executor() -> ThreadPoolExecutor:
    global _pool
    if _pool is None:
        _pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="archive")
    return _pool


def submit(fn, *args) -> Future:
    return _executor().submit(fn, *args)


def drain(timeout: float = 30.0) -> None:
    """Wait until every queued archive write is done (tests, shutdown)."""
    _executor().submit(lambda: None).result(timeout=timeout)


def _version_tuple(v) -> tuple[int, ...]:
    out = []
    for part in str(v or "").strip().split("."):
        digits = "".join(ch for ch in part if ch.isdigit())
        out.append(int(digits) if digits else 0)
    return tuple(out) if out and any(out) else (0,)


# ---- status ----------------------------------------------------------------
def archive_tfs(rt) -> list[str]:
    out = []
    for tf in (rt.strategy.get("signal_timeframe"), rt.strategy.get("htf_timeframe"), ARCHIVE_EXTRA_TF):
        if tf in TF_SECONDS and tf not in out:
            out.append(tf)
    return out


def base_days(rt) -> int:
    return max(1, int(rt.settings.ARCHIVE_DAYS))


def extra_targets(settings) -> dict[str, int]:
    """ARCHIVE_EXTRA "H1:1095,H4:1825,D1:3650" -> {tf: days}; unknown TFs / bad items ignored, "off" -> {}."""
    raw = str(getattr(settings, "ARCHIVE_EXTRA", "") or "").strip()
    out: dict[str, int] = {}
    if raw.lower() in EXTRA_OFF:
        return out
    for part in raw.replace(";", ",").split(","):
        tf, _, d = part.strip().partition(":")
        tf = tf.strip().upper()
        try:
            days = int(float(d))
        except ValueError:
            continue
        if tf in TF_SECONDS and days > 0:
            out[tf] = max(out.get(tf, 0), days)
    return out


def extra_order(settings) -> list[str]:
    """Extended TFs, cheapest (fewest bars per day) first: D1, H4, H1, ..."""
    return sorted(extra_targets(settings), key=lambda tf: -TF_SECONDS[tf])


def days_target(rt, tf: str) -> int:
    """Days of history wanted for a TF: max(ARCHIVE_DAYS if tf is in the base set, ARCHIVE_EXTRA[tf])."""
    base = base_days(rt) if tf in archive_tfs(rt) else 0
    return max(base, extra_targets(rt.settings).get(tf, 0)) or base_days(rt)


def target_ts(rt, now: float, tf: str | None = None) -> int:
    """Oldest bar time wanted; without tf: the base ARCHIVE_DAYS target."""
    days = base_days(rt) if tf is None else days_target(rt, tf)
    return int(now - days * 86400)


def _entry(rt, symbol: str, tf: str, now: float) -> dict:
    e = rt.archive_status.setdefault(symbol, {}).setdefault(
        tf, {"bars": 0, "oldest_ts": None, "target_ts": target_ts(rt, now, tf), "done": False})
    e["target_ts"] = target_ts(rt, now, tf)
    e["done"] = (symbol, tf) in rt.archive_plan["exhausted"] or (
        e["oldest_ts"] is not None and e["oldest_ts"] <= e["target_ts"])
    return e


def _base_reached(rt, symbol: str, tf: str, e: dict, now: float) -> bool:
    return e["done"] or (e["oldest_ts"] is not None and e["oldest_ts"] <= target_ts(rt, now))


def status_view(rt, now: float | None = None) -> dict:
    """GET /api/archive/status payload: per-TF days_target, extended TFs listed even before their walk starts."""
    now = now or time.time()
    refresh_status(rt, now)
    plan = rt.archive_plan
    extras = extra_targets(rt.settings)
    base_tfs = archive_tfs(rt)
    pairs = {(sym, tf): e for sym, tfs in rt.archive_status.items() for tf, e in tfs.items()}
    for sym in rt.active_symbols():
        for tf in extras:
            pairs.setdefault((sym, tf), {"bars": 0, "oldest_ts": None, "target_ts": target_ts(rt, now, tf),
                                         "done": False})
    symbols: dict[str, dict] = {}
    for (sym, tf), info in sorted(pairs.items(), key=lambda kv: (kv[0][0], -TF_SECONDS.get(kv[0][1], 0))):
        oldest = info.get("oldest_ts")
        symbols.setdefault(sym, {})[tf] = {
            **info, "days_target": days_target(rt, tf),
            "days_loaded": round(max(0.0, now - oldest) / 86400, 1) if oldest else 0.0,
            "extended": tf in extras, "exhausted": (sym, tf) in plan["exhausted"],
            "history_start_ts": plan.get("history_start", {}).get((sym, tf)),
        }
    base_left = any(not _base_reached(rt, s, tf, _entry(rt, s, tf, now), now)
                    for s in rt.active_symbols() for tf in base_tfs)
    extra_left = any(not v["done"] for s in symbols.values() for tf, v in s.items() if v["extended"])
    return {"enabled": bool(getattr(rt.settings, "ARCHIVE_ENABLED", True)), "days_target": base_days(rt),
            "extra": extras, "extra_interval_s": float(getattr(rt.settings, "ARCHIVE_EXTRA_INTERVAL_S", EXTRA_INTERVAL_DEFAULT_S) or 0),
            "phase": "base" if base_left else ("extra" if extra_left else "done"),
            "ea_version": rt.bridge.get("ea_version"), "symbols": symbols}


def rebuild_status(rt, now: float | None = None) -> dict:
    """Startup: per (symbol, tf) bar count + oldest bar straight from bars_archive."""
    now = now or time.time()
    rt.archive_status.clear()
    try:
        rows = rt.db.query("SELECT symbol, tf, COUNT(*) AS n, MIN(t) AS mn FROM bars_archive GROUP BY symbol, tf")
    except Exception:
        rows = []
    for r in rows:
        e = rt.archive_status.setdefault(r["symbol"], {}).setdefault(r["tf"], {})
        e.update({"bars": int(r["n"] or 0), "oldest_ts": int(r["mn"]) if r["mn"] is not None else None,
                  "target_ts": target_ts(rt, now), "done": False})
    refresh_status(rt, now)
    return rt.archive_status


def refresh_status(rt, now: float | None = None) -> None:
    """Make sure every active pair has an entry and recompute target/done for all of them."""
    now = now or time.time()
    for sym in rt.active_symbols():
        for tf in archive_tfs(rt):
            _entry(rt, sym, tf, now)
    for sym, tfs in list(rt.archive_status.items()):
        for tf in list(tfs):
            _entry(rt, sym, tf, now)


# ---- planner ---------------------------------------------------------------
def next_request(rt, now: float, commands_pending: bool = False, hist_pending: bool = False) -> str | None:
    """At most one HISTX line per sync; None when nothing should be asked right now."""
    s = rt.settings
    if not s.ARCHIVE_ENABLED or s.data_source == "sim":
        return None
    if not rt.bridge_connected() or _version_tuple(rt.bridge.get("ea_version")) < ARCHIVE_EA_VERSION:
        return None
    if commands_pending or hist_pending:
        return None  # trading commands and live refills first: never make the EA busier than needed
    plan = rt.archive_plan
    pend = plan.get("pending")
    if pend is not None:
        if now - pend["sent_ts"] < PENDING_TIMEOUT_S:
            return None
        plan["pending"] = None
        if pend.get("extended"):
            plan["extra_last_ts"] = now  # the EA may have been busy until now: the pause starts here
        _note_error(rt, (pend["symbol"], pend["tf"]), now, "fără răspuns de la EA")
    batch = max(1, min(MAX_BATCH, int(s.ARCHIVE_BATCH_BARS or MAX_BATCH)))
    # 1) base set [signal_tf, htf_tf, M15] up to ARCHIVE_DAYS, every active symbol
    for sym in rt.active_symbols():
        for tf in archive_tfs(rt):
            e = _entry(rt, sym, tf, now)
            if _base_reached(rt, sym, tf, e, now):
                continue
            if plan["retry_at"].get((sym, tf), 0) > now:
                continue
            return _request(rt, sym, tf, e, target_ts(rt, now), batch, now, extended=False)
    # 2) extended targets (ARCHIVE_EXTRA), D1 -> H4 -> H1, at most one request per ARCHIVE_EXTRA_INTERVAL_S counted from
    # the previous extended reply (a CopyRates download can block the EA for tens of seconds).
    # A base pair stuck in error backoff does not hold the extended walk back.
    if not extra_targets(s):
        return None
    if _bot_positions_open(rt):
        return None  # the multi-year walk waits for a quiet moment: no bot position whose SL/trailing is managed
    interval = max(0.0, float(getattr(s, "ARCHIVE_EXTRA_INTERVAL_S", EXTRA_INTERVAL_DEFAULT_S) or 0))
    last = plan.get("extra_last_ts")
    if last is not None and now - last < interval:
        return None
    xbatch = max(1, min(batch, EXTRA_MAX_BATCH))
    for tf in extra_order(s):
        for sym in rt.active_symbols():
            e = _entry(rt, sym, tf, now)
            if e["done"] or plan["retry_at"].get((sym, tf), 0) > now:
                continue
            plan["extra_last_ts"] = now
            return _request(rt, sym, tf, e, e["target_ts"], xbatch, now, extended=True)
    return None


def _bot_positions_open(rt) -> bool:
    magic = int(rt.settings.MT5_MAGIC)
    return any(isinstance(p, dict) and int(p.get("magic") or 0) == magic for p in (rt.mt5_positions or []))


def _request(rt, sym: str, tf: str, e: dict, target: int, batch: int, now: float, extended: bool) -> str:
    sec = TF_SECONDS[tf]
    end = int(e["oldest_ts"]) if e["oldest_ts"] is not None else int(now // sec * sec)
    need = math.ceil((end - target) / sec)
    count = max(1, min(batch, need))
    # the EA converts end_utc with its own (possibly skewed) UTC clock and today's server offset
    ea_end = end + int(rt.bridge.get("clock_skew_s") or 0) + _end_delta(rt, end, now)
    rt.archive_plan["pending"] = {"symbol": sym, "tf": tf, "end_utc": end, "ea_end": ea_end, "count": count,
                                  "sent_ts": now, "extended": extended}
    return f"HISTX|{sym}|{tf}|{ea_end}|{count}"


# ---- broker server time zone (DST) ------------------------------------------
def _rule_spec(name: str):
    if name in SERVER_TZ_RULES:
        zone, shift = SERVER_TZ_RULES[name]
        return ZoneInfo(zone), shift
    try:
        return ZoneInfo(name), 0
    except (ZoneInfoNotFoundError, ValueError):
        return None


def rule_offset_utc(name: str, t_utc: float) -> int | None:
    """Server - UTC (s) under the rule at a UTC instant."""
    spec = _rule_spec(name)
    if spec is None:
        return None
    zi, shift = spec
    return int(datetime.fromtimestamp(t_utc, zi).utcoffset().total_seconds()) + shift


def rule_offset_local(name: str, t_srv: float) -> int | None:
    """Server - UTC (s) under the rule for a broker server-time stamp (bar open times are server-local)."""
    spec = _rule_spec(name)
    if spec is None:
        return None
    zi, shift = spec
    naive = datetime.fromtimestamp(t_srv - shift, timezone.utc).replace(tzinfo=None)
    return int(naive.replace(tzinfo=zi).utcoffset().total_seconds()) + shift


def server_tz_rule(rt, now: float) -> str | None:
    """Rule used to convert archive bars, or None = today's offset for every bar (EA < 1.12, fixed-offset broker)."""
    off = rt.bridge.get("srv_offset")
    if not isinstance(off, (int, float)):
        return None
    true_off = int(off) + int(rt.bridge.get("clock_skew_s") or 0)  # EA offset is relative to its own TimeGMT()
    plan = rt.archive_plan
    setting = str(getattr(rt.settings, "MT5_SERVER_TZ", "auto") or "auto").strip()
    cache_key = (setting, true_off, int(now // 3600))
    if plan.get("tz_cache_key") == cache_key:
        return plan.get("tz_rule")
    rule, why = None, ""
    if setting.lower() in ("fixed", "off", "none"):
        why = "MT5_SERVER_TZ=fixed: offset-ul curent pentru toate barele"
    elif setting.lower() != "auto":
        if rule_offset_utc(setting, now) is None:
            why = f"MT5_SERVER_TZ={setting} necunoscut — folosesc offset-ul curent"
        elif rule_offset_utc(setting, now) != true_off:
            why = (f"MT5_SERVER_TZ={setting} dă {rule_offset_utc(setting, now) // 60} min, dar serverul brokerului "
                   f"are acum {true_off // 60} min — folosesc offset-ul curent")
        else:
            rule, why = setting, f"MT5_SERVER_TZ={setting}"
    else:
        matches = [n for n in AUTO_RULE_ORDER if rule_offset_utc(n, now) == true_off]
        server = str((rt.mt5_account or {}).get("server") or "")
        saved = {}
        try:
            saved = rt.db.get_setting(TZ_SETTING_KEY, {}) or {}
        except Exception:
            pass
        if len(matches) == 1:
            rule, why = matches[0], "detectat automat (regulile DST diferă în această perioadă)"
            if server and saved.get(server) != rule:
                try:
                    rt.db.set_setting(TZ_SETTING_KEY, {**saved, server: rule})
                except Exception:
                    pass
        elif matches:
            rule = saved.get(server) if saved.get(server) in matches else matches[0]
            why = "detectat anterior" if saved.get(server) in matches else \
                "presupus (setează MT5_SERVER_TZ=EET|NY|fixed dacă brokerul folosește altă regulă)"
        else:
            why = f"offset {true_off // 60} min fără regulă DST cunoscută — folosesc offset-ul curent"
    plan["tz_cache_key"] = cache_key
    if (rule, why) != (plan.get("tz_rule"), plan.get("tz_why")):
        rt.log("INFO", "archive", f"Arhivă: ora serverului brokerului convertită cu regula {rule or 'fixă'} ({why})")
    plan["tz_rule"], plan["tz_why"] = rule, why
    return rule


def _end_delta(rt, end: int, now: float) -> int:
    """Shift so that the EA's end + today's offset lands on the server time of `end` under the DST rule."""
    rule = server_tz_rule(rt, now)
    if rule is None:
        return 0
    true_off = int(rt.bridge["srv_offset"]) + int(rt.bridge.get("clock_skew_s") or 0)
    return int(rule_offset_utc(rule, end) or true_off) - true_off


def _dst_fix_bars(rt, item: dict, now: float) -> None:
    """Re-convert bar times stamped with today's offset to the offset valid at each bar (in place)."""
    off = item.get("srv_off")
    if not isinstance(off, (int, float)):
        return
    rule = server_tz_rule(rt, now)
    if rule is None:
        return
    true_off = int(off) + int(rt.bridge.get("clock_skew_s") or 0)
    if rule_offset_utc(rule, now) != true_off:
        return  # the reply was built with another offset than the rule's current one: leave it untouched
    for b in item.get("bars") or []:
        if isinstance(b, dict) and isinstance(b.get("t"), (int, float)):
            t = int(b["t"])
            ro = rule_offset_local(rule, t + true_off)
            if ro is not None:
                b["t"] = t + true_off - ro


def _note_error(rt, key: tuple[str, str], now: float, why: str) -> None:
    plan = rt.archive_plan
    n = plan["errors"].get(key, 0) + 1
    plan["errors"][key] = n
    plan["retry_at"][key] = now + min(ERR_BACKOFF_MAX_S, ERR_BACKOFF_S * (2 ** min(n - 1, 6)))
    if n == 1 or n % 10 == 0:
        rt.log("WARN", "archive", f"Arhivă {key[0]} {key[1]}: istoricul nu este încă disponibil în MT5 ({why}) — "
                                  f"reîncerc automat (încercarea {n})")


# ---- ingestion -------------------------------------------------------------
def _clean_bars(raw, tf: str, end_utc: int | None, now: float) -> list[tuple]:
    sec = TF_SECONDS[tf]
    seen: dict[int, tuple] = {}
    for b in raw if isinstance(raw, list) else []:
        if not isinstance(b, dict):
            continue
        try:
            t = int(b["t"])
            o, h, l, c = float(b["o"]), float(b["h"]), float(b["l"]), float(b["c"])
            tv = float(b.get("tv") or 0)
            sp = float(b.get("sp") or 0)
        except (KeyError, TypeError, ValueError):
            continue
        if t <= 0 or t % 60 or t + sec > now:  # misaligned or still forming
            continue
        if end_utc is not None and t >= end_utc:
            continue
        if not all(math.isfinite(x) and x > 0 for x in (o, h, l, c)) or h < l:
            continue
        seen[t] = (t, o, h, l, c, tv, sp)
    return [seen[t] for t in sorted(seen)]


def ingest_payload(rt, items, now: float | None = None) -> list[Future]:
    """Handle the EA's "archive" key: validate in the request, write on the archive thread."""
    now = now or time.time()
    futures: list[Future] = []
    if not isinstance(items, list):
        return futures
    plan = rt.archive_plan
    for it in items[:4]:
        if not isinstance(it, dict):
            continue
        sym = str(it.get("symbol") or "")
        tf = str(it.get("tf") or "")
        if not sym or tf not in TF_SECONDS:
            continue
        key = (sym, tf)
        pend = plan.get("pending")
        skew = int(rt.bridge.get("clock_skew_s") or 0)
        it_end = int(it["end_utc"]) if isinstance(it.get("end_utc"), (int, float)) else None
        # a reply answers the outstanding HISTX only when it echoes that request's end; a batch resent after a
        # lost HTTP 200 (or a late reply) carries an older end and must not count as "nothing older"
        is_reply = (pend is not None and (pend["symbol"], pend["tf"]) == key and it_end is not None
                    and it_end in (pend.get("ea_end"), int(pend["end_utc"]) + skew))
        _dst_fix_bars(rt, it, now)
        if is_reply:
            end = int(pend["end_utc"])
            plan["pending"] = None
            if pend.get("extended"):
                plan["extra_last_ts"] = now  # throttle from the reply: a slow EA still gets the full pause
        else:
            end = it_end - skew - _end_delta(rt, it_end - skew, now) if it_end is not None else None
        rows = _clean_bars(it.get("bars"), tf, end, now)
        if not is_reply:
            # stale duplicate / late reply: rows are harmless (INSERT OR IGNORE); never touch exhausted/errors/pending
            if rows:
                futures.append(submit(_write_rows, rt, sym, tf, rows))
            continue
        err = it.get("err")
        e = _entry(rt, sym, tf, now)
        if not rows:
            if err not in (None, 0, "0", ""):
                _note_error(rt, key, now, f"eroare {err}")
                continue
            # nothing older at the broker: the pair is complete as far as it can go (oldest_ts = real history start)
            plan["exhausted"].add(key)
            plan.setdefault("history_start", {})[key] = e["oldest_ts"]
            plan["errors"].pop(key, None)
            _entry(rt, sym, tf, now)
            rt.log("INFO", "archive", f"Arhivă {sym} {tf} completă: brokerul nu are bare mai vechi de "
                                      f"{iso(e['oldest_ts']) if e['oldest_ts'] else '—'} ({e['bars']} bare)")
            continue
        plan["errors"].pop(key, None)
        plan["retry_at"].pop(key, None)
        oldest = rows[0][0]
        if e["oldest_ts"] is not None and oldest >= e["oldest_ts"]:
            # no progress (the EA keeps answering with bars we already have): stop walking this pair
            plan["exhausted"].add(key)
            plan.setdefault("history_start", {})[key] = e["oldest_ts"]
        else:
            e["oldest_ts"] = oldest
        e["bars"] = int(e["bars"] or 0) + len(rows)  # approximate until the writer recounts
        was_done = e["done"]
        _entry(rt, sym, tf, now)
        if e["done"] and not was_done:
            rt.log("INFO", "archive", f"Arhivă {sym} {tf} completă până la {iso(e['oldest_ts'])}")
        futures.append(submit(_write_rows, rt, sym, tf, rows))
    return futures


def _write_rows(rt, symbol: str, tf: str, rows: list[tuple]) -> int:
    sql = ("INSERT OR IGNORE INTO bars_archive(symbol, tf, t, o, h, l, c, tv, sp) VALUES(?,?,?,?,?,?,?,?,?)")
    try:
        for i in range(0, len(rows), WRITE_CHUNK):
            # short transactions: the shared connection lock is released between chunks
            rt.db.executemany(sql, [(symbol, tf) + r for r in rows[i:i + WRITE_CHUNK]])
        r = rt.db.query_one("SELECT COUNT(*) AS n, MIN(t) AS mn FROM bars_archive WHERE symbol=? AND tf=?",
                            (symbol, tf))
    except Exception as ex:
        # worker thread: plain logger only (rt.log publishes to asyncio queues)
        log.error("[archive] Scriere arhivă %s %s eșuată: %r", symbol, tf, ex)
        return 0
    e = rt.archive_status.get(symbol, {}).get(tf)
    if e is not None and r:
        e["bars"] = int(r["n"] or 0)
        if r["mn"] is not None and (e["oldest_ts"] is None or int(r["mn"]) < e["oldest_ts"]):
            e["oldest_ts"] = int(r["mn"])
    return len(rows)


# ---- live spread statistics --------------------------------------------------
def sample_spreads(rt, symbols: list[str], now: float) -> None:
    """Cheap in-memory aggregation of live quote spreads (points) per symbol x UTC hour of day."""
    last = rt.spread_agg["last"]
    agg = rt.spread_agg["acc"]
    hour = int(now // 3600) % 24
    for sym in symbols:
        if now - last.get(sym, 0.0) < SPREAD_SAMPLE_S:
            continue
        q = rt.quotes.get(sym) or {}
        sp = q.get("spread_points")
        if not isinstance(sp, (int, float)) or sp < 0 or not math.isfinite(sp):
            continue
        age = rt.tick_age(sym, now)
        if age is None or age > SPREAD_MAX_TICK_AGE_S:
            continue
        last[sym] = now
        a = agg.setdefault((sym, hour), [0, 0.0, 0.0, 0.0])
        a[0] += 1
        a[1] += float(sp)
        a[2] += float(sp) * float(sp)
        a[3] = max(a[3], float(sp))


def flush_spreads(rt, now: float, force: bool = False) -> Future | None:
    st = rt.spread_agg
    if not st["acc"] or (not force and now - st["flush_ts"] < SPREAD_FLUSH_S):
        return None
    st["flush_ts"] = now
    acc, st["acc"] = st["acc"], {}
    rows = [(sym, hour, a[0], a[1], a[2], a[3]) for (sym, hour), a in acc.items()]
    return submit(_write_spreads, rt, rows)


def _write_spreads(rt, rows: list[tuple]) -> None:
    try:
        rt.db.executemany(
            "INSERT INTO spread_stats(symbol, hour_utc, n, sum_pts, sum_sq, max_pts) VALUES(?,?,?,?,?,?) "
            "ON CONFLICT(symbol, hour_utc) DO UPDATE SET n=n+excluded.n, sum_pts=sum_pts+excluded.sum_pts, "
            "sum_sq=sum_sq+excluded.sum_sq, max_pts=MAX(COALESCE(max_pts, 0), excluded.max_pts)",
            rows,
        )
    except Exception as ex:
        log.error("[archive] Scriere spread_stats eșuată: %r", ex)
