"""/api/v1/* routes for the UI (CONTRACT §2)."""
from __future__ import annotations

import asyncio
import importlib
import re
import secrets
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from . import indicators as ind
from . import scheduler
from .config import DAYS, TF_SECONDS, iso, utcnow
from .engine import Engine, EngineError
from .state import bar_public, format_sse

router = APIRouter(prefix="/api/v1")


def get_engine(request: Request) -> Engine:
    return request.app.state.engine


async def require_admin(request: Request, x_admin_token: str | None = Header(default=None)) -> None:
    token = get_engine(request).settings.ADMIN_TOKEN
    if not token:
        raise HTTPException(status_code=403, detail="Set ADMIN_TOKEN in .env")
    if not x_admin_token or not secrets.compare_digest(x_admin_token.encode(), token.encode()):
        raise HTTPException(status_code=401, detail="Invalid admin token")


def _engine_call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except EngineError as e:
        raise HTTPException(status_code=e.status, detail=e.detail)


async def _json(request: Request) -> dict:
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Body JSON invalid")
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Body trebuie să fie obiect JSON")
    return body


# ---- validation ----------------------------------------------------------
RISK_RULES: dict[str, tuple[type, float, float]] = {
    "max_risk_per_trade_pct": (float, 0.001, 5),
    "max_daily_loss_pct": (float, 0.01, 50),
    "max_equity_drawdown_pct": (float, 0.01, 100),
    "max_open_positions": (int, 0, 20),
    "max_trades_per_day": (int, 0, 200),
    "max_consecutive_losses": (int, 1, 100),
    "max_spread_points": (float, 0, 100000),
    "max_slippage_points": (int, 0, 10000),
    "max_volume_lots": (float, 0.01, 100),
    "max_same_currency_direction": (int, 0, 20),
    "min_free_margin_pct": (float, 0, 100),
    "max_tick_age_seconds": (float, 1, 3600),
    "max_signals_per_symbol_per_session": (int, 0, 1000),
    "news_blackout_minutes": (int, 0, 240),
    "news_min_importance": (int, 1, 3),
    "max_total_open_risk_pct": (float, 0.1, 20),
    "loss_streak_pause_minutes": (int, 0, 1440),
}
STRATEGY_NUM: dict[str, tuple[type, float, float]] = {
    "atr_sl_mult": (float, 0.1, 20),
    "atr_tp_mult": (float, 0.1, 50),
    "min_sl_points": (int, 0, 100000),
    "min_score": (int, 1, 5),
    "signal_expiry_minutes": (int, 1, 1440),
    "break_even_at_r": (float, 0, 20),
    "max_hold_minutes": (int, 0, 100000),
}
SYMBOL_RE = re.compile(r"^[A-Za-z0-9._#-]{2,20}$")


def _num(key: str, value: Any, typ: type, lo: float, hi: float):
    if isinstance(value, bool):
        raise HTTPException(status_code=422, detail=f"{key}: valoare numerică necesară")
    try:
        v = typ(value)
        if typ is int and float(value) != int(v):
            raise ValueError
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail=f"{key}: valoare invalidă")
    if not (lo <= v <= hi):
        raise HTTPException(status_code=422, detail=f"{key}: trebuie între {lo} și {hi}")
    return v


def _bool(key: str, value: Any) -> bool:
    if not isinstance(value, bool):
        raise HTTPException(status_code=422, detail=f"{key}: true/false")
    return value


def validate_risk_update(body: dict) -> dict:
    out = {}
    for k, v in body.items():
        if k == "must_have_sl_tp":
            if v is not True:
                raise HTTPException(status_code=422, detail="must_have_sl_tp nu poate fi dezactivat în v1")
            out[k] = True
        elif k in RISK_RULES:
            out[k] = _num(k, v, *RISK_RULES[k])
        else:
            raise HTTPException(status_code=422, detail=f"Cheie necunoscută: {k}")
    return out


def validate_strategy_update(body: dict, current: dict) -> dict:
    out = {}
    for k, v in body.items():
        if k in STRATEGY_NUM:
            out[k] = _num(k, v, *STRATEGY_NUM[k])
        elif k in ("trailing_enabled", "close_at_session_end"):
            out[k] = _bool(k, v)
        elif k in ("signal_timeframe", "htf_timeframe"):
            tf = str(v).upper()
            if tf not in TF_SECONDS:
                raise HTTPException(status_code=422, detail=f"{k}: timeframe invalid")
            out[k] = tf
        elif k == "symbols":
            if isinstance(v, str):
                v = [x for x in v.split(",")]
            if not isinstance(v, list) or not (1 <= len(v) <= 20):
                raise HTTPException(status_code=422, detail="symbols: listă cu 1–20 simboluri")
            syms = []
            for s in v:
                s = str(s).strip().upper()
                if not SYMBOL_RE.match(s):
                    raise HTTPException(status_code=422, detail=f"simbol invalid: {s}")
                if s not in syms:
                    syms.append(s)
            out[k] = syms
        else:
            raise HTTPException(status_code=422, detail=f"Cheie necunoscută: {k}")
    merged = {**current, **out}
    if TF_SECONDS[merged["htf_timeframe"]] <= TF_SECONDS[merged["signal_timeframe"]]:
        raise HTTPException(status_code=422, detail="htf_timeframe trebuie să fie mai mare decât signal_timeframe")
    return out


def validate_schedule_update(body: dict, current: dict) -> dict:
    out = {}
    for k, v in body.items():
        if k == "enabled":
            out[k] = _bool(k, v)
        elif k in ("start", "end"):
            try:
                t = scheduler.parse_hhmm(str(v))
            except Exception:
                raise HTTPException(status_code=422, detail=f"{k}: format HH:MM")
            out[k] = f"{t.hour:02d}:{t.minute:02d}"
        elif k == "days":
            if isinstance(v, str):
                v = v.split(",")
            if not isinstance(v, list):
                raise HTTPException(status_code=422, detail="days: listă")
            days = [str(d).strip().upper() for d in v if str(d).strip()]
            if any(d not in DAYS for d in days):
                raise HTTPException(status_code=422, detail="days: valori permise MON..SUN")
            out[k] = [d for d in DAYS if d in days]
        elif k == "tz":
            try:
                from zoneinfo import ZoneInfo
                ZoneInfo(str(v))
            except Exception:
                raise HTTPException(status_code=422, detail="tz: timezone IANA invalid")
            out[k] = str(v)
        else:
            raise HTTPException(status_code=422, detail=f"Cheie necunoscută: {k}")
    merged = {**current, **out}
    if merged["start"] == merged["end"]:
        raise HTTPException(status_code=422, detail="start și end nu pot fi egale")
    return out


# ---- read endpoints ------------------------------------------------------
@router.get("/status")
async def get_status(request: Request):
    return get_engine(request).status()


@router.get("/symbols")
async def get_symbols(request: Request):
    return get_engine(request).symbols_payload()


@router.get("/candles")
async def get_candles(request: Request, symbol: str, tf: str = "M5", limit: int = Query(300, ge=1, le=2000)):
    eng = get_engine(request)
    tf = tf.upper()
    symbol = symbol.upper()
    if tf not in TF_SECONDS:
        raise HTTPException(status_code=422, detail="tf invalid")
    series = list(eng.rt.bars.get((symbol, tf), []))
    closes = [b["c"] for b in series]
    ef, es = ind.ema(closes, 20), ind.ema(closes, 50)
    start = max(0, len(series) - limit)
    bars = series[start:]
    return {
        "symbol": symbol, "tf": tf, "feed_label": eng.settings.feed_label,
        "bars": [bar_public(b) for b in bars],
        "ema_fast": [{"time": series[i]["t"], "value": ef[i]} for i in range(start, len(series)) if ef[i] is not None],
        "ema_slow": [{"time": series[i]["t"], "value": es[i]} for i in range(start, len(series)) if es[i] is not None],
    }


@router.get("/analysis")
async def get_analysis(request: Request, symbol: str):
    eng = get_engine(request)
    symbol = symbol.upper()
    a = eng.rt.last_analysis.get(symbol)
    if a is None:
        a = eng.analyze_symbol(symbol, allow_signal=False)
    return a


@router.get("/signals")
async def get_signals(request: Request, limit: int = Query(100, ge=1, le=1000), symbol: str | None = None,
                direction: str | None = None):
    return get_engine(request).list_signals(limit, symbol or None, direction or None)


@router.get("/signals/{signal_id}")
async def get_signal(request: Request, signal_id: str):
    eng = get_engine(request)
    sig = eng.get_signal(signal_id)
    if not sig:
        raise HTTPException(status_code=404, detail="Semnal inexistent")
    sig["events"] = eng.signal_events(signal_id)
    return sig


@router.get("/positions")
async def get_positions(request: Request):
    return get_engine(request).positions_payload()


@router.get("/trades")
async def get_trades(request: Request, limit: int = Query(100, ge=1, le=1000)):
    return get_engine(request).list_trades(limit)


@router.get("/intents")
async def get_intents(request: Request, limit: int = Query(100, ge=1, le=1000)):
    return get_engine(request).executor.list(limit)


@router.get("/risk")
async def get_risk(request: Request):
    return dict(get_engine(request).rt.risk)


@router.put("/risk", dependencies=[Depends(require_admin)])
async def put_risk(request: Request):
    eng = get_engine(request)
    upd = validate_risk_update(await _json(request))
    eng.rt.risk.update(upd)
    eng.db.set_setting("risk", eng.rt.risk)
    eng.rt.log("WARN", "control", f"Setări risc actualizate: {upd}")
    return dict(eng.rt.risk)


@router.get("/settings")
async def get_settings_view(request: Request):
    eng = get_engine(request)
    s = eng.settings
    return {
        "schedule": dict(eng.rt.schedule),
        "strategy": {**dict(eng.rt.strategy), "name": eng.active_strategy_name()},
        "telegram": {"enabled": bool(s.TELEGRAM_ENABLED), "configured": s.telegram_configured,
                     "thread_id_set": bool((s.TELEGRAM_MESSAGE_THREAD_ID or "").strip())},
        "ai": {"enabled": eng.ai.enabled, "model": s.CLAUDE_MODEL, "veto_enabled": s.CLAUDE_VETO_ENABLED,
               "required": s.CLAUDE_REQUIRED, "key_set": eng.ai.key_set},
    }


@router.put("/schedule", dependencies=[Depends(require_admin)])
async def put_schedule(request: Request):
    eng = get_engine(request)
    upd = validate_schedule_update(await _json(request), eng.rt.schedule)
    eng.rt.schedule.update(upd)
    eng.db.set_setting("schedule", eng.rt.schedule)
    eng.rt.log("WARN", "control", f"Program actualizat: {upd}")
    return dict(eng.rt.schedule)


@router.get("/strategies")
async def get_strategies(request: Request):
    return get_engine(request).strategy_catalog()


@router.put("/strategy", dependencies=[Depends(require_admin)])
async def put_strategy(request: Request):
    eng = get_engine(request)
    body = await _json(request)
    name = None
    if "name" in body:
        name = str(body.pop("name") or "").strip()
        if name not in eng.strategy_names():
            raise HTTPException(status_code=422, detail=f"name: strategie necunoscută {name!r} "
                                                        f"(disponibile: {', '.join(eng.strategy_names())})")
    upd = validate_strategy_update(body, eng.rt.strategy)
    if "signal_timeframe" in upd or "symbols" in upd:
        eng.last_analyzed_bar.clear()
    prev = eng.active_strategy_name()
    eng.rt.strategy.update(upd)
    if name is not None:
        eng.rt.strategy["name"] = name  # used from the next closed bar
    eng.db.set_setting("strategy", eng.rt.strategy)
    if name is not None and name != prev:
        eng.rt.log("WARN", "control", f"Strategia live schimbată: {prev} → {name} (se aplică de la următoarea bară)")
    if upd:
        eng.rt.log("WARN", "control", f"Strategie actualizată: {upd}")
    return {**dict(eng.rt.strategy), "name": eng.active_strategy_name()}


# ---- control -------------------------------------------------------------
@router.post("/control/mode", dependencies=[Depends(require_admin)])
async def control_mode(request: Request):
    eng = get_engine(request)
    body = await _json(request)
    _engine_call(eng.set_mode, str(body.get("mode") or ""))
    return eng.status()["trading"]


@router.post("/control/execution", dependencies=[Depends(require_admin)])
async def control_execution(request: Request):
    eng = get_engine(request)
    body = await _json(request)
    if not isinstance(body.get("enabled"), bool):
        raise HTTPException(status_code=422, detail="enabled: true/false")
    _engine_call(eng.set_execution, body["enabled"])
    return eng.status()["trading"]


@router.post("/control/pause-entries", dependencies=[Depends(require_admin)])
async def control_pause(request: Request):
    eng = get_engine(request)
    eng.set_paused(True)
    return eng.status()["trading"]


@router.post("/control/resume-entries", dependencies=[Depends(require_admin)])
async def control_resume(request: Request):
    eng = get_engine(request)
    eng.set_paused(False)
    return eng.status()["trading"]


@router.post("/control/session", dependencies=[Depends(require_admin)])
async def control_session(request: Request):
    eng = get_engine(request)
    body = await _json(request)
    action = str(body.get("action") or "").lower()
    if action not in ("start", "stop", "auto"):
        raise HTTPException(status_code=422, detail="action: start|stop|auto")
    eng.rt.session_override = None if action == "auto" else action
    eng.rt.log("WARN", "control", f"Sesiune: override manual = {action}")
    return eng.session()


@router.post("/control/close-position", dependencies=[Depends(require_admin)])
async def control_close_position(request: Request):
    eng = get_engine(request)
    body = await _json(request)
    if body.get("confirm") is not True:
        raise HTTPException(status_code=400, detail="Confirmare necesară: confirm=true")
    try:
        ticket = int(body.get("ticket"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail="ticket invalid")
    found = eng.find_position(ticket)
    source = found[0] if found else "PAPER"
    return _engine_call(eng.close_position, source, ticket, "MANUAL")


@router.post("/control/close-all", dependencies=[Depends(require_admin)])
async def control_close_all(request: Request):
    eng = get_engine(request)
    body = await _json(request)
    if body.get("confirm") != "CLOSE ALL":
        raise HTTPException(status_code=400, detail='Confirmare necesară: {"confirm":"CLOSE ALL"}')
    intents = _engine_call(eng.close_all)
    return {"ok": True, "intents": intents}


@router.post("/control/test-order", dependencies=[Depends(require_admin)])
async def control_test_order(request: Request):
    eng = get_engine(request)
    body = await _json(request)
    return _engine_call(eng.test_order, str(body.get("symbol") or ""), str(body.get("side") or ""))


@router.post("/control/analyze-now", dependencies=[Depends(require_admin)])
async def control_analyze_now(request: Request):
    eng = get_engine(request)
    body = await _json(request)
    symbol = str(body.get("symbol") or "").upper()
    if symbol not in eng.rt.active_symbols():
        raise HTTPException(status_code=404, detail="Simbol inactiv")
    return eng.analyze_symbol(symbol, allow_signal=True)


@router.post("/telegram/test", dependencies=[Depends(require_admin)])
async def telegram_test(request: Request):
    eng = get_engine(request)
    tg = eng.telegram
    from .telegram import DISCLAIMER, e
    text = (f"✅ <b>Test conexiune</b> — MT5 AI Bot ({e(eng.settings.feed_label)}, mod {e(eng.mode())}).\n"
            f"<i>{e(DISCLAIMER)}</i>")
    res = await tg.send(text, tg.button_url("test"))
    if res["ok"]:
        tg.last_ok_utc = iso(utcnow())
        tg.last_error = None
        eng.rt.log("INFO", "telegram", "Mesaj de test trimis")
        return {"ok": True}
    tg.last_error = res["error"]
    eng.rt.log("WARN", "telegram", f"Mesaj de test eșuat: {res['error']}")
    return {"ok": False, "error": res["error"]}


@router.get("/logs")
async def get_logs(request: Request, limit: int = Query(200, ge=1, le=2000), level: str | None = None):
    eng = get_engine(request)
    if level:
        lv = level.upper()
        if lv == "WARNING":
            lv = "WARN"
        return eng.db.query("SELECT id, time_utc, level, component, message FROM system_events WHERE level=? "
                            "ORDER BY id DESC LIMIT ?", (lv, limit))
    return eng.db.query("SELECT id, time_utc, level, component, message FROM system_events ORDER BY id DESC LIMIT ?", (limit,))


@router.get("/stream")
async def stream(request: Request):
    eng = get_engine(request)
    bus = eng.rt.bus
    q = bus.subscribe()

    async def gen():
        try:
            yield "retry: 3000\n\n"
            yield format_sse(bus.seq, "status", eng.status())
            yield format_sse(bus.seq, "positions", eng.positions_payload())
            while True:
                if await request.is_disconnected():
                    break
                try:
                    msg = await asyncio.wait_for(q.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                if msg is None:
                    break
                yield format_sse(*msg)
        finally:
            bus.unsubscribe(q)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"})


# ---- performance / shadow / archive / backtest ---------------------------
# sync handlers: FastAPI runs them in its threadpool, so aggregation never blocks the event loop
@router.get("/stats")
def get_stats(request: Request, account_key: str | None = None, days: int = Query(30, ge=0, le=3650)):
    return _engine_call(get_engine(request).perf_stats, account_key or None, days)


@router.get("/shadow")
def get_shadow(request: Request, days: int = Query(30, ge=0, le=3650)):
    return _engine_call(get_engine(request).shadow_stats, days)


@router.get("/archive/status")
async def get_archive_status(request: Request):
    return get_engine(request).archive_status()


BT_COST_MODES = ("stats", "bar", "fixed")
BT_COST_NUM: dict[str, tuple[type, float, float]] = {
    "fixed_spread_pts": (float, 0, 1000),
    "commission_per_lot_usd": (float, 0, 100),
    "slippage_pts": (float, 0, 100),
}
BT_STRATEGY_KEYS = ("atr_sl_mult", "atr_tp_mult", "min_sl_points", "min_score", "break_even_at_r", "max_hold_minutes")
BT_RISK_KEYS = ("max_same_currency_direction", "max_spread_points", "max_open_positions", "max_signals_per_symbol_per_session")
# exit policies (app/exits.py); defaults shown in the UI when the module does not publish its own
EXIT_DEFAULTS: dict[str, dict] = {
    "base": {},
    "trailing": {"trail_start_r": 1.0, "trail_atr_mult": 1.5, "trail_min_r": 0.5},
    "partial": {"partial_frac": 0.5, "partial_r": 1.0},
    "invalidation": {"min_bars": 3},
    "stall": {"stall_minutes": 60, "stall_band_r": 0.3},
    "giveback": {"gb_arm_r": 1.0, "gb_frac": 0.5},
    "combo": {"modes": ["trailing", "stall"]},
}


def exit_catalog() -> dict[str, dict]:
    try:
        ex = importlib.import_module("app.exits")
    except ImportError:
        return {k: dict(v) for k, v in EXIT_DEFAULTS.items()}
    published = getattr(ex, "DEFAULT_PARAMS", None) or getattr(ex, "DEFAULTS", None)
    modes = list(getattr(ex, "MODES", None) or EXIT_DEFAULTS)
    out = {}
    for m in modes:
        d = (published or {}).get(m) if isinstance(published, dict) else None
        out[str(m)] = dict(d) if isinstance(d, dict) else dict(EXIT_DEFAULTS.get(m, {}))
    return out


def validate_exit(body) -> dict:
    if body in (None, {}):
        return {"mode": "base", "params": {}}
    if not isinstance(body, dict):
        raise HTTPException(status_code=422, detail="exit: obiect {mode, params}")
    cat = exit_catalog()
    mode = str(body.get("mode") or "base").lower()
    if mode not in cat:
        raise HTTPException(status_code=422, detail=f"exit.mode: unul din {', '.join(cat)}")
    raw = body.get("params") or {}
    if not isinstance(raw, dict):
        raise HTTPException(status_code=422, detail="exit.params: obiect JSON")
    params: dict = {}
    for k, v in raw.items():
        k = str(k)
        if not re.match(r"^[a-z_]{1,40}$", k):
            raise HTTPException(status_code=422, detail=f"exit.params: cheie invalidă {k}")
        if k == "modes":
            if not isinstance(v, list) or not v or any(str(m) not in cat or str(m) == "combo" for m in v):
                raise HTTPException(status_code=422, detail="exit.params.modes: listă de moduri (fără combo)")
            params[k] = [str(m) for m in v]
        else:
            params[k] = _num(f"exit.{k}", v, float, 0, 100000)
    if mode == "combo" and not params.get("modes"):
        raise HTTPException(status_code=422, detail="exit combo: params.modes necesar")
    return {"mode": mode, "params": params}


@router.get("/exits")
async def get_exits():
    return [{"mode": m, "params": p} for m, p in exit_catalog().items()]


def research_strategy_names(eng: Engine) -> list[str]:
    """Stage C2 research plugins (donchian-v1, ma-trend-v1, tsmom-v1): allowed in backtests, not in names()."""
    reg = eng.strategy_registry()
    try:
        return [str(n) for n in reg.research_names()] if reg is not None and hasattr(reg, "research_names") else []
    except Exception:
        return []


def validate_backtest_params(body: dict, eng: Engine) -> dict:
    st = eng.rt.strategy
    syms = body.get("symbols") or eng.rt.active_symbols()
    if isinstance(syms, str):
        syms = syms.split(",")
    if not isinstance(syms, list) or not (1 <= len(syms) <= 20):
        raise HTTPException(status_code=422, detail="symbols: listă cu 1–20 simboluri")
    symbols = []
    for s in syms:
        s = str(s).strip().upper()
        if not SYMBOL_RE.match(s):
            raise HTTPException(status_code=422, detail=f"simbol invalid: {s}")
        if s not in symbols:
            symbols.append(s)
    out: dict = {"symbols": symbols}
    name = str(body.get("strategy_name") or "trend-v1").strip()
    research = name in research_strategy_names(eng)  # C2 plugins: backtest only, never selectable live
    if name not in eng.strategy_names() and not research:
        raise HTTPException(status_code=422, detail=f"strategy_name: strategie necunoscută {name!r}")
    if body.get("start_ts") is not None or body.get("end_ts") is not None:
        start = _num("start_ts", body.get("start_ts"), int, 946684800, 4102444800)
        end = _num("end_ts", body.get("end_ts", int(utcnow().timestamp())), int, 946684800, 4102444800)
        if end <= start:
            raise HTTPException(status_code=422, detail="end_ts trebuie să fie după start_ts")
        out.update(start_ts=start, end_ts=end)
    else:
        out["days"] = _num("days", body.get("days", 30), int, 1, 3650 if research else 730)
    for k, default in (("signal_tf", st["signal_timeframe"]), ("htf_tf", st["htf_timeframe"])):
        if research and not body.get(k):
            continue  # research plugins (C2): RUN_DEFAULTS of the plugin (D1, fără HTF), not the live timeframes
        tf = str(body.get(k) or default).upper()
        if research and k == "htf_tf" and tf == "NONE":
            out[k] = tf
            continue
        if tf not in TF_SECONDS:
            raise HTTPException(status_code=422, detail=f"{k}: timeframe invalid")
        out[k] = tf
    if out.get("htf_tf") in TF_SECONDS and out.get("signal_tf") in TF_SECONDS and \
            TF_SECONDS[out["htf_tf"]] <= TF_SECONDS[out["signal_tf"]]:
        raise HTTPException(status_code=422, detail="htf_tf trebuie să fie mai mare decât signal_tf")
    strat = body.get("strategy") or {}
    risk = body.get("risk") or {}
    cost = body.get("cost") or {}
    if not all(isinstance(x, dict) for x in (strat, risk, cost)):
        raise HTTPException(status_code=422, detail="strategy/risk/cost: obiecte JSON")
    out["strategy_name"] = name
    if research:
        plugin_params = dict(getattr(eng.strategy_registry().get(name), "PARAMS", {}) or {})
    else:
        plugin_params = next((c.get("params") or {} for c in eng.strategy_catalog() if c["name"] == name), {})
    out["strategy"] = {}
    for k, v in strat.items():
        if k in BT_STRATEGY_KEYS:
            out["strategy"][k] = _num(k, v, *STRATEGY_NUM[k])
        elif k in plugin_params:
            d = plugin_params[k]
            if isinstance(d, bool):
                out["strategy"][k] = _bool(k, v)
            elif isinstance(d, (int, float)):
                out["strategy"][k] = _num(k, v, int if isinstance(d, int) else float, -1e6, 1e6)
            else:
                raise HTTPException(status_code=422, detail=f"strategy: parametrul {k} nu poate fi modificat")
        else:
            raise HTTPException(status_code=422, detail=f"strategy: cheie necunoscută {k}")
    out["exit"] = validate_exit(body.get("exit"))
    out["risk"] = {}
    for k, v in risk.items():
        if k not in BT_RISK_KEYS:
            raise HTTPException(status_code=422, detail=f"risk: cheie necunoscută {k}")
        out["risk"][k] = _num(k, v, *RISK_RULES[k])
    mode = str(cost.get("mode") or "stats").lower()
    if mode not in BT_COST_MODES:
        raise HTTPException(status_code=422, detail="cost.mode: stats|bar|fixed")
    out["cost"] = {"mode": mode, "commission_per_lot_usd": 7.0, "slippage_pts": 1.0}
    for k, v in cost.items():
        if k == "mode":
            continue
        if k == "per_symbol_spread_pts":
            if not isinstance(v, dict) or len(v) > 50:
                raise HTTPException(status_code=422, detail="cost.per_symbol_spread_pts: obiect {simbol: puncte}")
            sp = {}
            for sym, pts in v.items():
                sym = str(sym).strip().upper()
                if not SYMBOL_RE.match(sym):
                    raise HTTPException(status_code=422, detail=f"simbol invalid: {sym}")
                sp[sym] = _num(f"spread {sym}", pts, float, 0, 10000)
            if sp:
                out["cost"][k] = sp
            continue
        if k == "swap_mode":  # C2: off | table | zero (implicit după strategie)
            sm = str(v or "").lower()
            if sm not in ("off", "table", "zero"):
                raise HTTPException(status_code=422, detail="cost.swap_mode: off|table|zero")
            out["cost"][k] = sm
            continue
        if k not in BT_COST_NUM:
            raise HTTPException(status_code=422, detail=f"cost: cheie necunoscută {k}")
        out["cost"][k] = _num(k, v, *BT_COST_NUM[k])
    if mode == "fixed" and "fixed_spread_pts" not in out["cost"] and \
            any(s not in out["cost"].get("per_symbol_spread_pts", {}) for s in symbols):
        raise HTTPException(status_code=422, detail="cost.fixed_spread_pts necesar pentru modul fixed")
    sess = body.get("session", "default")
    if sess == "default" or sess is True:
        sch = eng.rt.schedule
        out["session"] = {"start": sch["start"], "end": sch["end"], "tz": sch["tz"], "days": list(sch["days"])}
    elif sess in (None, False):
        out["session"] = None
    elif isinstance(sess, dict):
        upd = validate_schedule_update({k: v for k, v in sess.items() if k in ("start", "end", "days", "tz")}, eng.rt.schedule)
        out["session"] = {**{k: eng.rt.schedule[k] for k in ("start", "end", "tz", "days")}, **upd}
    else:
        raise HTTPException(status_code=422, detail="session: obiect, true sau null")
    out["initial_equity"] = _num("initial_equity", body.get("initial_equity", 200), float, 1, 1e9)
    out["risk_pct"] = _num("risk_pct", body.get("risk_pct", eng.rt.risk.get("max_risk_per_trade_pct", 0.25)), float, 0.001, 5)
    return out


@router.post("/backtest/run", dependencies=[Depends(require_admin)])
async def backtest_run(request: Request):
    eng = get_engine(request)
    params = validate_backtest_params(await _json(request), eng)
    return _engine_call(eng.start_backtest, params)


@router.get("/backtest/runs")
def backtest_runs(request: Request, limit: int = Query(30, ge=1, le=200)):
    return get_engine(request).list_backtests(limit)


@router.get("/backtest/runs/{run_id}")
def backtest_run_detail(request: Request, run_id: str):
    r = get_engine(request).get_backtest(run_id)
    if not r:
        raise HTTPException(status_code=404, detail="Backtest inexistent")
    return r
