"""/api/v1/* routes for the UI (CONTRACT §2)."""
from __future__ import annotations

import asyncio
import secrets

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from .engine import Engine, EngineError
from .state import format_sse

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


@router.get("/status")
async def get_status(request: Request):
    return get_engine(request).status()


@router.get("/live")
async def get_live(request: Request):
    return get_engine(request).live_status()


@router.get("/symbols")
async def get_symbols(request: Request):
    return get_engine(request).symbols_payload()


@router.get("/positions")
async def get_positions(request: Request):
    return get_engine(request).positions_payload()


@router.get("/trades")
async def get_trades(request: Request, limit: int = Query(100, ge=1, le=1000)):
    return get_engine(request).list_trades(limit)


@router.get("/intents")
async def get_intents(request: Request, limit: int = Query(100, ge=1, le=1000)):
    return get_engine(request).executor.list(limit)


@router.get("/basket")
async def get_basket(request: Request):
    return get_engine(request).basket.settings_public()


@router.put("/basket", dependencies=[Depends(require_admin)])
async def put_basket(request: Request):
    """Positions page: {"max_loss_usd": n} - the basket closes at -n USD per 0.01 of start lot; {"start_lot": x} - lot
    of every new entry (0 = follows the balance). Applied at once, kept across restarts."""
    body = await request.json()
    basket = get_engine(request).basket
    try:
        out = basket.settings_public()
        if "max_loss_usd" in body:
            out = basket.set_loss_limit(body.get("max_loss_usd"))
        if "start_lot" in body:
            out = basket.set_start_lot(body.get("start_lot"))
        return out
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.get("/settings")
async def get_settings_view(request: Request):
    eng = get_engine(request)
    s = eng.settings
    return {
        "schedule": dict(eng.rt.schedule),
        "ai": {"enabled": eng.ai.enabled, "model": s.CLAUDE_MODEL, "key_set": eng.ai.key_set},
    }


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


@router.post("/history/clear", dependencies=[Depends(require_admin)])
async def history_clear(request: Request):
    """Positions page buttons: {"what": "intents" | "trades", "confirm": "DELETE"} (finished rows only, see
    Engine.clear_history; a JSON copy is written to data/backup first)."""
    eng = get_engine(request)
    body = await request.json()
    if body.get("confirm") != "DELETE":
        raise HTTPException(status_code=400, detail='Confirmare necesară: {"confirm":"DELETE"}')
    return _engine_call(eng.clear_history, str(body.get("what") or ""))


@router.post("/control/close-all", dependencies=[Depends(require_admin)])
async def control_close_all(request: Request):
    eng = get_engine(request)
    body = await _json(request)
    if body.get("confirm") != "CLOSE ALL":
        raise HTTPException(status_code=400, detail='Confirmare necesară: {"confirm":"CLOSE ALL"}')
    intents = _engine_call(eng.close_all)
    return {"ok": True, "intents": intents}


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
