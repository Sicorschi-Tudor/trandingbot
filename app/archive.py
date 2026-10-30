"""Bar history archive (bars_archive) backfilled from the EA (v1.11+, HISTX) + live spread statistics.

The planner asks the EA for one batch at a time (HISTX|sym|tf|end_utc|count), walking backwards from the
oldest archived bar until ARCHIVE_DAYS are covered or the broker has nothing older. Once every base pair
([signal_tf, htf_tf, M15] x active symbols) reached ARCHIVE_DAYS, the extended targets of ARCHIVE_EXTRA (TF:days, e.g.
H1:1095,H4:1825,D1:3650; off by default, enabled for research in .env) are walked, D1 first, at most one request per
ARCHIVE_EXTRA_INTERVAL_S counted from the previous extended reply (or its timeout), never while the bot holds MT5
positions. RESEARCH_SYMBOLS (never analysed / quoted / traded) are walked last on D1/H4/H1 with the same targets and
throttling, trying broker name aliases until one is known (UNKNOWN_SYMBOL -> next alias -> unavailable).
DB writes run on a single background thread so the event loop never waits on a 5000-row insert."""
from __future__ import annotations

import logging
import math
import time
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .config import TF_SECONDS, iso, split_csv

ARCHIVE_EA_VERSION = (1, 11)
RESEARCH_EA_VERSION = (1, 13)  # HISTX answers err "UNKNOWN_SYMBOL" when SymbolSelect fails (alias resolution)
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
# research-only symbols (RESEARCH_SYMBOLS): archive only, D1/H4/H1, days from ARCHIVE_EXTRA (fallback below)
RESEARCH_TFS = ("D1", "H4", "H1")
RESEARCH_DEFAULT_DAYS = {"D1": 3650, "H4": 1825, "H1": 1095}
# broker name variants tried in order (the canonical name first)
RESEARCH_ALIASES = {
    "US500": ("US500", "SPX500", "US500.cash", "SP500"),
    "NAS100": ("NAS100", "USTEC", "NDX100"),
    "US30": ("US30", "DJ30"),
    "DE40": ("DE40", "GER40", "DAX40"),
    "UK100": ("UK100", "FTSE100"),
    "JP225": ("JP225", "JPN225", "NI225"),
    "XTIUSD": ("XTIUSD", "USOIL", "WTI"),
    "XBRUSD": ("XBRUSD", "UKOIL", "BRENT"),
}
# EA "symbol not at this broker": 'UNKNOWN_SYMBOL' (protocol name) or 4301 (ERR_MARKET_UNKNOWN_SYMBOL, EA 1.11/1.12 when
# SymbolSelect fails in HISTX)
UNKNOWN_SYMBOL_ERRS = ("UNKNOWN_SYMBOL", "4301")

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


# ---- research symbols ---------------------------------------------------------
def research_list(rt) -> list[str]:
    """RESEARCH_SYMBOLS (canonical names) minus the active symbols; "off" -> []. Never analysed or traded."""
    raw = str(getattr(rt.settings, "RESEARCH_SYMBOLS", "") or "").strip()
    if raw.lower() in EXTRA_OFF:
        return []
    active = set(rt.active_symbols())
    out: list[str] = []
    for sym in split_csv(raw):
        # XAUUSD is already archived as an active symbol when the broker calls it XAUUSDm / XAUUSD.a
        if sym not in active and sym not in out and not any(a.upper().startswith(sym.upper()) for a in active):
            out.append(sym)
    return out


def broker_suffixes(rt) -> list[str]:
    """Suffixes the broker appends to symbol names, read off the active symbols (XAUUSDm -> "m", EURUSD.a -> ".a")."""
    out: list[str] = []
    for a in rt.active_symbols():
        base = a[:6]
        if len(a) > 6 and base.isalpha() and base.isupper() and a[6:] not in out:
            out.append(a[6:])
    return out


def research_targets(settings) -> dict[str, int]:
    """{tf: days} for research symbols: D1/H4/H1 with ARCHIVE_EXTRA's days; {} while ARCHIVE_EXTRA is off."""
    extras = extra_targets(settings)
    if not extras:
        return {}
    return {tf: int(extras.get(tf) or RESEARCH_DEFAULT_DAYS[tf]) for tf in RESEARCH_TFS}


def research_candidates(rt, sym: str) -> list[str]:
    """Broker names to try for a research symbol, in order; names of active symbols are never used."""
    active = set(rt.active_symbols())
    out: list[str] = []
    names = (sym,) + tuple(RESEARCH_ALIASES.get(sym, ()))
    names += tuple(n + sfx for sfx in broker_suffixes(rt) for n in names)
    for name in names:
        if name and name not in out and (name == sym or name not in active):
            out.append(name)
    return out


def _research_state(rt, sym: str) -> dict:
    return rt.archive_plan.setdefault("research", {}).setdefault(
        sym, {"idx": 0, "broker": None, "available": None, "tried": []})


def research_broker(rt, sym: str) -> str | None:
    """Broker name to ask for (resolved, or the next untried alias); None when the symbol is unavailable."""
    st = _research_state(rt, sym)
    if st["available"] is False:
        return None
    if st["broker"]:
        return st["broker"]
    cands = research_candidates(rt, sym)
    return cands[st["idx"]] if st["idx"] < len(cands) else None


def research_only_names(rt) -> set[str]:
    """Every name (canonical + aliases already asked for) that belongs to a research symbol and not to trading."""
    active = set(rt.active_symbols())
    names = set(research_list(rt))
    names.update(b for b, c in (rt.archive_plan.get("research_names") or {}).items() if c in names)
    return names - active


def _is_unknown_symbol(err) -> bool:
    return str(err).strip().upper() in UNKNOWN_SYMBOL_ERRS if err is not None else False


def pair_days(rt, sym: str, tf: str) -> int:
    if sym in research_list(rt):
        rtg = research_targets(rt.settings)
        if tf in rtg:
            return rtg[tf]
    return days_target(rt, tf)


def pair_target_ts(rt, sym: str, tf: str, now: float) -> int:
    return int(now - pair_days(rt, sym, tf) * 86400)


def target_ts(rt, now: float, tf: str | None = None) -> int:
    """Oldest bar time wanted; without tf: the base ARCHIVE_DAYS target."""
    days = base_days(rt) if tf is None else days_target(rt, tf)
    return int(now - days * 86400)


def _entry(rt, symbol: str, tf: str, now: float) -> dict:
    e = rt.archive_status.setdefault(symbol, {}).setdefault(
        tf, {"bars": 0, "oldest_ts": None, "target_ts": pair_target_ts(rt, symbol, tf, now), "done": False})
    e["target_ts"] = pair_target_ts(rt, symbol, tf, now)
    e["done"] = (symbol, tf) in rt.archive_plan["exhausted"] or (
        e["oldest_ts"] is not None and e["oldest_ts"] <= e["target_ts"])
    return e


def _base_reached(rt, symbol: str, tf: str, e: dict, now: float) -> bool:
    return e["done"] or (e["oldest_ts"] is not None and e["oldest_ts"] <= target_ts(rt, now))


def _view_row(rt, sym: str, tf: str, info: dict, now: float, extended: bool) -> dict:
    plan = rt.archive_plan
    oldest = info.get("oldest_ts")
    return {**info, "days_target": pair_days(rt, sym, tf),
            "days_loaded": round(max(0.0, now - oldest) / 86400, 1) if oldest else 0.0,
            "extended": extended, "exhausted": (sym, tf) in plan["exhausted"],
            "history_start_ts": plan.get("history_start", {}).get((sym, tf))}


def status_view(rt, now: float | None = None) -> dict:
    """GET /api/archive/status payload: per-TF days_target, extended TFs listed even before their walk starts;
    research symbols under "research" (never in "symbols"), with the resolved broker name and availability."""
    now = now or time.time()
    refresh_status(rt, now)
    plan = rt.archive_plan
    extras = extra_targets(rt.settings)
    base_tfs = archive_tfs(rt)
    rlist = research_list(rt)
    rset = set(rlist)
    pairs = {(sym, tf): e for sym, tfs in rt.archive_status.items() if sym not in rset for tf, e in tfs.items()}
    for sym in rt.active_symbols():
        for tf in extras:
            pairs.setdefault((sym, tf), {"bars": 0, "oldest_ts": None, "target_ts": target_ts(rt, now, tf),
                                         "done": False})
    symbols: dict[str, dict] = {}
    for (sym, tf), info in sorted(pairs.items(), key=lambda kv: (kv[0][0], -TF_SECONDS.get(kv[0][1], 0))):
        symbols.setdefault(sym, {})[tf] = _view_row(rt, sym, tf, info, now, tf in extras)
    base_left = any(not _base_reached(rt, s, tf, _entry(rt, s, tf, now), now)
                    for s in rt.active_symbols() for tf in base_tfs)
    extra_left = any(not v["done"] for s in symbols.values() for tf, v in s.items() if v["extended"])
    rtg = research_targets(rt.settings)
    research_ea_ok = _version_tuple(rt.bridge.get("ea_version")) >= RESEARCH_EA_VERSION
    research: dict[str, dict] = {}
    research_left = False
    for sym in rlist:
        st = _research_state(rt, sym)
        have = rt.archive_status.get(sym, {})
        tfs: dict[str, dict] = {}
        for tf in sorted(set(rtg) | set(have), key=lambda t: -TF_SECONDS.get(t, 0)):
            info = have.get(tf) or {"bars": 0, "oldest_ts": None, "target_ts": pair_target_ts(rt, sym, tf, now),
                                    "done": False}
            tfs[tf] = _view_row(rt, sym, tf, info, now, tf in rtg)
            if tf in rtg and not tfs[tf]["done"] and st["available"] is not False and research_ea_ok:
                research_left = True
        research[sym] = {"broker_symbol": st["broker"], "available": st["available"],
                         "trying": research_broker(rt, sym) if not st["broker"] else None,
                         "candidates": research_candidates(rt, sym), "tried": list(st["tried"]), "tfs": tfs}
    phase = "base" if base_left else ("extra" if extra_left else ("research" if research_left else "done"))
    return {"enabled": bool(getattr(rt.settings, "ARCHIVE_ENABLED", True)), "days_target": base_days(rt),
            "extra": extras, "extra_interval_s": float(getattr(rt.settings, "ARCHIVE_EXTRA_INTERVAL_S", EXTRA_INTERVAL_DEFAULT_S) or 0),
            "phase": phase, "ea_version": rt.bridge.get("ea_version"), "symbols": symbols,
            "research_enabled": bool(rtg and rlist),
            "research_ea_ok": research_ea_ok,
            "research_targets": rtg, "research": research}


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
        return None  # research symbols use the ARCHIVE_EXTRA targets too: nothing extended to do
    if _bot_positions_open(rt) and not getattr(s, "ARCHIVE_EXTRA_WITH_POSITIONS", False):
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
    # 3) research symbols (RESEARCH_SYMBOLS), only once no active pair can be asked for: D1 -> H4 -> H1, same batch
    # size / interval / position gating as (2); the HISTX carries the broker name (resolved or next alias to try)
    rtg = research_targets(s)
    if _version_tuple(rt.bridge.get("ea_version")) < RESEARCH_EA_VERSION:
        return None  # older EAs: no explicit UNKNOWN_SYMBOL, no probing of broker names
    for tf in sorted(rtg, key=lambda t: -TF_SECONDS[t]):
        for sym in research_list(rt):
            broker = research_broker(rt, sym)
            if broker is None:
                continue  # unavailable at this broker
            e = _entry(rt, sym, tf, now)
            if e["done"] or plan["retry_at"].get((sym, tf), 0) > now:
                continue
            plan["extra_last_ts"] = now
            return _request(rt, sym, tf, e, e["target_ts"], xbatch, now, extended=True, broker=broker)
    return None


def _bot_positions_open(rt) -> bool:
    magic = int(rt.settings.MT5_MAGIC)
    return any(isinstance(p, dict) and int(p.get("magic") or 0) == magic for p in (rt.mt5_positions or []))


def _request(rt, sym: str, tf: str, e: dict, target: int, batch: int, now: float, extended: bool,
             broker: str | None = None) -> str:
    sec = TF_SECONDS[tf]
    end = int(e["oldest_ts"]) if e["oldest_ts"] is not None else int(now // sec * sec)
    need = math.ceil((end - target) / sec)
    count = max(1, min(batch, need))
    # the EA converts end_utc with its own (possibly skewed) UTC clock and today's server offset
    ea_end = end + int(rt.bridge.get("clock_skew_s") or 0) + _end_delta(rt, end, now)
    rt.archive_plan["pending"] = {"symbol": sym, "tf": tf, "end_utc": end, "ea_end": ea_end, "count": count,
                                  "sent_ts": now, "extended": extended}
    if broker is not None:
        # research symbol: archived under its canonical name, asked for under the broker's name
        rt.archive_plan["pending"].update({"broker": broker, "research": True})
        rt.archive_plan.setdefault("research_names", {})[broker] = sym
        return f"HISTX|{broker}|{tf}|{ea_end}|{count}"
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
        raw_sym = sym
        canon = (plan.get("research_names") or {}).get(sym)
        if canon and sym not in rt.active_symbols() and canon in research_list(rt):
            sym = canon  # research symbol replied under its broker name: archive under the canonical name
        key = (sym, tf)
        pend = plan.get("pending")
        skew = int(rt.bridge.get("clock_skew_s") or 0)
        it_end = int(it["end_utc"]) if isinstance(it.get("end_utc"), (int, float)) else None
        # a reply answers the outstanding HISTX only when it echoes that request's end; a batch resent after a
        # lost HTTP 200 (or a late reply) carries an older end and must not count as "nothing older"
        is_reply = (pend is not None and (pend["symbol"], pend["tf"]) == key and it_end is not None
                    and pend.get("broker", pend["symbol"]) == raw_sym
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
        if pend.get("research"):
            if not rows and _is_unknown_symbol(err):
                _research_unknown(rt, sym, raw_sym, tf)
                continue
            st = _research_state(rt, sym)
            if st["broker"] != raw_sym and (rows or err in (None, 0, "0", "")):
                st["broker"], st["available"] = raw_sym, True
                rt.log("INFO", "archive", f"Arhivă cercetare {sym}: simbolul brokerului este {raw_sym}")
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
        else:
            days = round((now - float(e["oldest_ts"])) / 86400) if e.get("oldest_ts") else 0
            target = round((now - float(e["target_ts"])) / 86400) if e.get("target_ts") else 0
            rt.log("INFO", "archive", f"Arhivă {sym} {tf}: +{len(rows)} bare ({e['bars']} în total, acoperă {days} din {target} zile; "
                                      f"cea mai veche {iso(e['oldest_ts'])[:10] if e.get('oldest_ts') else '—'})")
        futures.append(submit(_write_rows, rt, sym, tf, rows))
    return futures


def _research_unknown(rt, sym: str, broker: str, tf: str) -> None:
    """EA: `broker` is not a symbol at this broker -> try the next alias, or mark the research symbol unavailable."""
    st = _research_state(rt, sym)
    plan = rt.archive_plan
    if broker not in st["tried"]:
        st["tried"].append(broker)
    cands = research_candidates(rt, sym)
    st["broker"] = None
    st["idx"] = max(st["idx"], cands.index(broker) + 1 if broker in cands else st["idx"] + 1)
    for t in RESEARCH_TFS + (tf,):
        plan["errors"].pop((sym, t), None)
        plan["retry_at"].pop((sym, t), None)
    if st["idx"] >= len(cands):
        st["available"] = False
        rt.log("WARN", "archive", f"Arhivă cercetare {sym}: simbol indisponibil la broker (încercat: "
                                  f"{', '.join(st['tried'])}) — nu mai este cerut")
    else:
        rt.log("INFO", "archive", f"Arhivă cercetare {sym}: {broker} nu există la broker, încerc {cands[st['idx']]}")


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
