"""/bridge/v1/sync endpoint (EA protocol, CONTRACT §3) + command delivery."""
from __future__ import annotations

import json
import secrets
import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from . import archive, calendar_hist
from .config import TF_SECONDS
from .executor import EA_EXT_OPEN_VERSION, PENDING_STATES, ea_version_tuple

router = APIRouter()

MAX_BODY = 5 * 1024 * 1024
HIST_MIN_BARS = 100
HIST_RETRY_S = 15.0
HIST_FORCE_MAX_S = 300.0
INSTANCE_WINDOW_S = 30.0
INSTANCE_WARN_EVERY_S = 300.0
AUTH_LOG_EVERY_S = 60.0
CALENDAR_MAX_ITEMS = 500
CALENDAR_STALE_S = 1200.0  # EA refreshes every 10 min; older than this -> ask for a refresh
CALENDAR_REQ_EVERY_S = 60.0


def build_response(server_time_utc: int, symbols: list[str], tfs: list[str],
                   hist_pairs: list[tuple[str, str]], commands: list[str]) -> str:
    lines = [f"OK|{int(server_time_utc)}", "SYMBOLS|" + ",".join(symbols), "TF|" + ",".join(tfs)]
    lines += [f"HIST|{s}|{tf}" for s, tf in hist_pairs]
    lines += commands
    return "\n".join(lines) + "\n"


def check_bridge_auth(configured_key: str, provided: str | None) -> int | None:
    """returns HTTP status code on failure, None if OK."""
    if not configured_key:
        return 403
    if not provided or not secrets.compare_digest(provided.encode(), configured_key.encode()):
        return 401
    return None


def note_auth_failure(rt, now: float | None = None) -> None:
    """Rejected EA requests (401/403): one log line per minute with the count."""
    now = now or time.time()
    rt.bridge["auth_fail_count"] = int(rt.bridge.get("auth_fail_count") or 0) + 1
    if now - float(rt.bridge.get("auth_fail_log_ts") or 0) >= AUTH_LOG_EVERY_S:
        n = rt.bridge["auth_fail_count"]
        rt.bridge["auth_fail_count"] = 0
        rt.bridge["auth_fail_log_ts"] = now
        rt.log("WARN", "bridge", f"Cereri EA respinse (cheie greșită): {n} în ultimul minut")


def track_instance(rt, payload: dict, now: float) -> None:
    """Two EAs (two charts / two terminals) syncing at once would double-send results and fight over the
    account snapshot: warn when more than one instance_id was seen in the last 30 s."""
    iid = payload.get("instance_id")
    if not isinstance(iid, str) or not iid:
        return
    seen: dict = rt.bridge.setdefault("instances", {})
    seen[iid[:64]] = now
    for k, ts in list(seen.items()):
        if now - ts > INSTANCE_WINDOW_S:
            seen.pop(k, None)
    rt.bridge["instance_id"] = iid[:64]
    rt.bridge["instances_active"] = len(seen)
    if len(seen) > 1 and now - float(rt.bridge.get("instances_warn_ts") or 0) >= INSTANCE_WARN_EVERY_S:
        rt.bridge["instances_warn_ts"] = now
        rt.log("WARN", "bridge", f"Mai multe instanțe AIBridgeEA active ({len(seen)} în ultimele {int(INSTANCE_WINDOW_S)} s) — "
                                 f"lasă EA-ul atașat pe un singur grafic, într-un singur terminal")


def ingest_calendar(rt, payload: dict, now: float) -> None:
    """Store the EA's economic calendar (only sent on the sync right after an EA refresh)."""
    if "calendar" not in payload:
        return
    raw = payload.get("calendar")
    ok = payload.get("calendar_ok", True) is not False and isinstance(raw, list)
    was_ok = rt.bridge.get("calendar_ok")
    rt.bridge["calendar_ok"] = ok
    if not ok:
        # keep the previous list: it still covers the next hours better than nothing
        if was_ok is not False:
            rt.log("WARN", "bridge", "Calendarul economic MT5 nu este disponibil (terminal deconectat?) — "
                                     "păstrez ultima listă primită")
        return
    items = []
    for ev in raw[:CALENDAR_MAX_ITEMS]:
        if not isinstance(ev, dict):
            continue
        try:
            t = int(ev["t"])
            imp = min(3, max(1, int(ev.get("imp") or 1)))
        except (KeyError, TypeError, ValueError):
            continue
        ccy = str(ev.get("ccy") or "").strip().upper()[:8]
        if not ccy:
            continue
        items.append({"t": t, "ccy": ccy, "imp": imp, "name": str(ev.get("name") or "")[:160]})
    items.sort(key=lambda x: x["t"])
    first = rt.calendar_ts is None or was_ok is False
    rt.calendar = items
    rt.calendar_ts = now
    if first:
        hi = sum(1 for x in items if x["imp"] >= 3)
        rt.log("INFO", "bridge", f"Calendar economic primit de la EA: {len(items)} evenimente în următoarele 24 h "
                                 f"({hi} de importanță mare)")


def calendar_request_needed(rt, now: float) -> bool:
    """EA >= 1.10 accepts a CALENDAR line (forces a refresh), e.g. after a server restart."""
    if ea_version_tuple(rt.bridge.get("ea_version")) < EA_EXT_OPEN_VERSION:
        return False
    if rt.calendar_ts is not None and now - rt.calendar_ts < CALENDAR_STALE_S:
        return False
    if now - float(rt.bridge.get("calendar_req_ts") or 0) < CALENDAR_REQ_EVERY_S:
        return False
    rt.bridge["calendar_req_ts"] = now
    return True


def on_account_changed(engine, prev_login, new_login) -> None:
    """Login switch: nothing queued/in flight for the old account may reach (or be matched on) the new one."""
    n = engine.executor.fail_other_accounts(new_login)
    known = getattr(engine, "known_mt5_positions", None)
    if isinstance(known, dict):
        known.clear()
    pm = getattr(engine, "pm", None)
    for attr in ("last_action", "gave_up"):
        cache = getattr(pm, attr, None)
        if hasattr(cache, "clear"):
            cache.clear()
    trading = getattr(engine.rt, "trading", None)
    if isinstance(trading, dict):
        # signals created (and reviewed) for the old account must not execute on the new one
        trading["armed_ts"] = time.time()
        if hasattr(engine.rt, "save_trading"):
            engine.rt.save_trading()
    engine.rt.log("WARN", "bridge", f"Schimbare de cont {prev_login} → {new_login}: {n} comenzi în așteptare anulate "
                                    f"(ACCOUNT_CHANGED), cache-ul de poziții MT5 golit")


def hist_needed(rt, symbol: str, tf: str, now: float) -> bool:
    series = rt.bars.get((symbol, tf), [])
    last_req = rt.hist_requested.get((symbol, tf))
    force = rt.hist_force.get((symbol, tf))
    if force is not None and force.get("sent") is None:
        return True  # forced refill (gap detected): ignore retry window once
    if last_req is not None and now - last_req < HIST_RETRY_S:
        return False
    if force is not None:
        return True
    if len(series) < HIST_MIN_BARS:
        return True
    if last_req is None and series[-1]["t"] < now - 3 * TF_SECONDS[tf]:
        return True  # gap after restart: refill once
    return False


ACCOUNT_SETTLE_S = 15.0
CLOCK_STEP_S = 900  # timezone errors come in quarter-hour multiples


def normalize_clock(rt, payload: dict, now: float) -> int:
    """Correct EA timestamps when its UTC conversion is off by a timezone step.

    The EA converts broker server time to UTC with TimeTradeServer()-TimeGMT(); under Wine/macOS
    TimeGMT() can be wrong. Its own `time_utc` is wrong by the same amount, so measuring
    time_utc - now gives the error to remove from every timestamp in the payload."""
    skew = 0
    drift = 0
    ea_utc = payload.get("time_utc")
    if isinstance(ea_utc, (int, float)) and ea_utc > 0:
        raw = float(ea_utc) - now
        skew = int(round(raw / CLOCK_STEP_S)) * CLOCK_STEP_S
        if abs(raw - skew) >= 2:  # time_utc is whole seconds: ignore the ±1 s truncation/latency noise
            drift = int(round(raw - skew))
        rt.bridge["clock_raw_skew_s"] = round(raw, 1)
    rt.bridge["ea_gmt_skew_s"] = skew  # timezone-step error of the EA's TimeGMT() alone
    rt.bridge["ea_clock_drift_s"] = drift  # residual sub-step drift; skew + drift = EA TimeGMT() - real UTC
    # Ticks can be old but never from the future: a tick far ahead of `now` means the EA's
    # server->UTC offset was wrong (e.g. right after an account switch).
    tick_ts = [q["time_msc"] / 1000.0 for q in payload.get("symbols") or []
               if isinstance(q.get("time_msc"), (int, float)) and q["time_msc"] > 0]
    if tick_ts:
        ahead = max(tick_ts) - skew - now
        if ahead > CLOCK_STEP_S / 3:
            skew += int(round(ahead / CLOCK_STEP_S)) * CLOCK_STEP_S
    if skew != rt.bridge.get("clock_skew_s", 0):
        if skew:
            rt.log("WARN", "bridge", f"Ceasul UTC al EA-ului diferă cu {skew} s de ora reală — corectez automat timestamp-urile")
        else:
            rt.log("INFO", "bridge", "Ceasul UTC al EA-ului este corect")
    rt.bridge["clock_skew_s"] = skew
    if not skew:
        return 0
    for b in payload.get("bars") or []:
        if isinstance(b.get("t"), (int, float)):
            b["t"] = int(b["t"]) - skew
    for q in payload.get("symbols") or []:
        if isinstance(q.get("time_msc"), (int, float)):
            q["time_msc"] = int(q["time_msc"]) - skew * 1000
    for key in ("positions", "deals"):
        for x in payload.get(key) or []:
            if isinstance(x.get("time"), (int, float)):
                x["time"] = int(x["time"]) - skew
    for ev in payload.get("calendar") or []:
        # calendar times go through the same ToUtc() in the EA
        if isinstance(ev, dict) and isinstance(ev.get("t"), (int, float)):
            ev["t"] = int(ev["t"]) - skew
    for item in payload.get("archive") or []:
        # archive bars (v1.11) too; end_utc stays in the EA frame (archive.ingest_payload removes the skew)
        if isinstance(item, dict) and isinstance(item.get("bars"), list):
            for b in item["bars"]:
                if isinstance(b, dict) and isinstance(b.get("t"), (int, float)):
                    b["t"] = int(b["t"]) - skew
    return skew


def archive_commands_pending(engine, commands: list[str]) -> bool:
    """OPEN/MODIFY/CLOSE in this response or still waiting for the EA's result -> no HISTX this sync."""
    if any(c.split("|", 1)[0] in ("OPEN", "MODIFY", "CLOSE") for c in commands):
        return True
    try:
        # UNKNOWN too: delivered, no result after 30 s -> the EA may still be busy with it, reconcile in progress
        row = engine.db.query_one("SELECT 1 AS x FROM intents WHERE mode='DEMO' AND state IN (%s) LIMIT 1"
                                  % ",".join("'%s'" % st for st in PENDING_STATES))
    except Exception:
        return True
    return row is not None


def process_sync(engine, payload: dict, now: float | None = None) -> str:
    rt = engine.rt
    now = now or time.time()
    normalize_clock(rt, payload, now)
    first = rt.bridge.get("last_sync_ts") is None or (now - (rt.bridge.get("last_sync_ts") or 0)) > 30
    rt.bridge["last_sync_ts"] = now
    rt.bridge["ea_version"] = str(payload.get("ea_version") or "")
    # EA >= 1.12: its TimeTradeServer()-TimeGMT() (s), lets the archive undo the broker's DST changes per bar
    srv_off = payload.get("srv_offset")
    rt.bridge["srv_offset"] = int(srv_off) if isinstance(srv_off, (int, float)) and not isinstance(srv_off, bool) else None
    rt.bridge["terminal"] = payload.get("terminal") or {}
    rt.bridge["sync_count"] = int(rt.bridge.get("sync_count") or 0) + 1
    rt.bridge["magic"] = payload.get("magic")
    track_instance(rt, payload, now)
    try:
        ea_magic = int(payload.get("magic") or 0)
    except (TypeError, ValueError):
        ea_magic = 0
    magic_ok = ea_magic == int(engine.settings.MT5_MAGIC)
    if not magic_ok and (rt.bridge.get("magic_ok") is not False or first):
        engine.risk_alert(f"Magic EA ({ea_magic}) diferit de MT5_MAGIC ({engine.settings.MT5_MAGIC}) — "
                          f"comenzile DEMO sunt blocate până la corectare", key=f"magic_mismatch:{ea_magic}")
        rt.log("ERROR", "bridge", f"Magic EA {ea_magic} != MT5_MAGIC {engine.settings.MT5_MAGIC}")
    rt.bridge["magic_ok"] = magic_ok
    if isinstance(payload.get("account"), dict):
        prev_login = (rt.mt5_account or {}).get("login")
        new_login = payload["account"].get("login")
        if prev_login is not None and new_login != prev_login:
            # MT5 briefly reports the new login with the old account's balance/equity during a switch
            rt.bridge["account_changed_ts"] = now
            rt.log("WARN", "bridge", f"Cont MT5 schimbat ({prev_login} → {new_login}); reperele de equity se iau după "
                                     f"{int(ACCOUNT_SETTLE_S)} s")
            rt.mt5_account = payload["account"]
            on_account_changed(engine, prev_login, new_login)
        rt.mt5_account = payload["account"]
    if first:
        acc = rt.mt5_account or {}
        rt.log("INFO", "bridge", f"EA conectat (v{rt.bridge['ea_version']}), server {acc.get('server')}, "
                                 f"trade_mode={acc.get('trade_mode')} ({'DEMO' if acc.get('trade_mode') == 0 else 'NU este DEMO'})")

    # research-only symbols (RESEARCH_SYMBOLS) are archive-only: never quoted / analysed even if an EA sends them
    research_only = archive.research_only_names(rt)
    if research_only:
        payload["symbols"] = [s for s in payload.get("symbols") or [] if s.get("symbol") not in research_only]
        payload["bars"] = [b for b in payload.get("bars") or [] if b.get("symbol") not in research_only]
    for s in payload.get("symbols") or []:
        sym = s.get("symbol")
        if not sym:
            continue
        info = {k: v for k, v in s.items() if k != "symbol"}
        rt.ingest_quote(sym, info)
    archive.sample_spreads(rt, [s.get("symbol") for s in payload.get("symbols") or [] if s.get("symbol")], now)
    archive.flush_spreads(rt, now)

    groups: dict[tuple[str, str], list[dict]] = {}
    for b in payload.get("bars") or []:
        try:
            groups.setdefault((b["symbol"], b["tf"]), []).append(b)
        except KeyError:
            continue
    tf_known = set(TF_SECONDS)
    if first:
        # (re)connect after a pause or restart: refill every pair once, gaps are likely
        for s in rt.active_symbols():
            for tf in rt.needed_tfs():
                rt.hist_force[(s, tf)] = {"since": now, "sent": None}
    for (sym, tf), bars in list(groups.items()):
        if tf not in tf_known:
            continue
        future = [b for b in bars if int(b["t"]) > now + 60]
        if future:
            bars = [b for b in bars if int(b["t"]) <= now + 60]
            if now - rt.bridge.get("future_warn_ts", 0) > 60:
                rt.bridge["future_warn_ts"] = now
                rt.log("WARN", "bridge", f"{len(future)} bare {sym} {tf} cu timp în viitor au fost respinse (ceas EA greșit?)")
            if not bars:
                continue
        if len(bars) > 3:
            series = rt.bars.get((sym, tf)) or []
            min_t = min(int(b["t"]) for b in bars)
            if series and series[0]["t"] < min_t:
                # drop stale bars older than the refill so no internal gap survives
                rt.bars[(sym, tf)] = [b for b in series if b["t"] >= min_t]
            rt.ingest_history(sym, tf, bars)
            rt.hist_force.pop((sym, tf), None)
            rt.log("INFO", "bridge", f"Istoric primit {sym} {tf}: {len(bars)} bare")
        else:
            series = rt.bars.get((sym, tf)) or []
            # >5 bars missing (quiet markets legitimately skip a bar or two; outages are caught by `first`)
            if series and min(int(b["t"]) for b in bars) > series[-1]["t"] + 5 * TF_SECONDS[tf]:
                rt.hist_force[(sym, tf)] = {"since": now, "sent": None}
                rt.log("WARN", "bridge", f"Gol în bare {sym} {tf} — cer istoric (analiza este suspendată până la reumplere)")
            for b in sorted(bars, key=lambda x: x["t"]):
                rt.ingest_bar(sym, tf, b)

    ingest_calendar(rt, payload, now)
    if "archive" in payload:
        archive.ingest_payload(rt, payload.get("archive"), now)

    if "positions" in payload:
        engine.set_mt5_positions(payload.get("positions") or [])
    if payload.get("deals"):
        engine.ingest_deals(payload["deals"])
    if payload.get("results"):
        engine.executor.apply_results(payload["results"])
    if "positions" in payload:
        engine.executor.reconcile(rt.mt5_positions, payload.get("deals") or [], now, magic=int(engine.settings.MT5_MAGIC))

    for key, f in list(rt.hist_force.items()):
        if now - f["since"] > HIST_FORCE_MAX_S:
            rt.hist_force.pop(key, None)
            rt.log("WARN", "bridge", f"Istoricul {key[0]} {key[1]} nu a sosit în {int(HIST_FORCE_MAX_S)} s — "
                                     f"analiza se reia cu datele existente")
    symbols = rt.active_symbols()
    tfs = rt.needed_tfs()
    hist = []
    for s in symbols:
        for tf in tfs:
            if hist_needed(rt, s, tf, now):
                rt.hist_requested[(s, tf)] = now
                if (s, tf) in rt.hist_force:
                    rt.hist_force[(s, tf)]["sent"] = now
                hist.append((s, tf))

    commands: list[str] = []
    acc = rt.mt5_account or {}
    if not magic_ok:
        engine.fail_queued_commands(f"Magic EA ({ea_magic}) != MT5_MAGIC ({engine.settings.MT5_MAGIC})")
    elif acc.get("trade_mode") == 0:
        commands = engine.executor.take_commands(now)
    else:
        engine.fail_queued_commands("Contul MT5 nu este DEMO (trade_mode != 0) — comenzile nu sunt livrate")
    if calendar_request_needed(rt, now):
        commands = ["CALENDAR"] + commands
    if archive.ARCHIVE_EA_VERSION <= ea_version_tuple(rt.bridge.get("ea_version")) and engine.settings.ARCHIVE_ENABLED:
        histx = archive.next_request(rt, now, commands_pending=archive_commands_pending(engine, commands),
                                     hist_pending=bool(hist) or bool(rt.hist_force))
        if histx:
            commands = commands + [histx]
    # EA >= 1.14: economic-calendar history (ingests "calendar_hist", at most one CALHIST in flight; never raises)
    commands = commands + calendar_hist.on_sync(engine, payload, commands, now,
                                                hist_pending=bool(hist) or bool(rt.hist_force))
    # keep quoting symbols of open bot positions that are no longer active, so they stay managed (BE/rescue/time stop)
    magic = int(engine.settings.MT5_MAGIC)
    quote_symbols = list(symbols)
    for p in rt.mt5_positions or []:
        sym = p.get("symbol")
        if sym and sym not in quote_symbols and int(p.get("magic") or 0) == magic:
            quote_symbols.append(sym)
    return build_response(int(now), quote_symbols, tfs, hist, commands)


@router.post("/bridge/v1/sync")
async def bridge_sync(request: Request):
    engine = request.app.state.engine
    settings = engine.settings
    code = check_bridge_auth(settings.MT5_BRIDGE_KEY, request.headers.get("x-bridge-key"))
    if code is not None:
        note_auth_failure(engine.rt)
    if code == 403:
        return PlainTextResponse("ERR|Set MT5_BRIDGE_KEY in .env\n", status_code=403)
    if code == 401:
        return PlainTextResponse("ERR|Bad bridge key\n", status_code=401)
    if settings.data_source == "sim":
        return PlainTextResponse("ERR|Server runs with DATA_SOURCE=sim\n", status_code=409)
    cl = request.headers.get("content-length")
    if cl and cl.isdigit() and int(cl) > MAX_BODY:
        return PlainTextResponse("ERR|Body too large\n", status_code=413)
    body = await request.body()
    if len(body) > MAX_BODY:
        return PlainTextResponse("ERR|Body too large\n", status_code=413)
    try:
        payload = json.loads(body.rstrip(b"\x00").decode("utf-8", errors="replace"))
        if not isinstance(payload, dict):
            raise ValueError("not an object")
    except Exception as e:
        engine.rt.log("WARN", "bridge", f"JSON invalid de la EA: {e}")
        return JSONResponse({"detail": "invalid JSON"}, status_code=400)
    try:
        text = process_sync(engine, payload)
    except Exception as e:
        engine.rt.log("ERROR", "bridge", f"sync processing error: {e!r}")
        return PlainTextResponse("ERR|server error\n", status_code=500)
    return PlainTextResponse(text)
