"""FastAPI app: routers, background tasks lifecycle, static dashboard."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from . import api, bridge
from .config import ROOT_DIR, VERSION, get_settings, redact
from .db import DB
from .engine import Engine


class RedactFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
            clean = redact(msg)
            if clean != msg:
                record.msg, record.args = clean, ()
        except Exception:
            pass
        return True


def setup_logging() -> None:
    root = logging.getLogger()
    if not any(isinstance(f, RedactFilter) for f in root.filters):
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
        for h in root.handlers:
            h.addFilter(RedactFilter())
        root.addFilter(RedactFilter())
    # httpx logs full request URLs (Telegram URL contains the bot token)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("anthropic").setLevel(logging.WARNING)


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    settings = get_settings()
    db = DB(settings.db_path)
    engine = Engine(settings, db)
    engine.startup()
    app.state.engine = engine
    await engine.start()
    try:
        yield
    finally:
        await engine.stop()
        db.close()


app = FastAPI(title="MT5 AI Trading Bot", version=VERSION, lifespan=lifespan)
app.include_router(api.router)
app.include_router(bridge.router)


@app.get("/healthz")
async def healthz():
    return {"ok": True, "version": VERSION}


WEB_DIR = ROOT_DIR / "web"
if WEB_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")
else:
    @app.get("/", response_class=HTMLResponse)
    async def index_missing():
        return ("<!doctype html><meta charset='utf-8'><title>MT5 AI Bot</title>"
                "<p>Dashboard-ul (web/) lipsește. API-ul rulează: <a href='/api/v1/status'>/api/v1/status</a></p>")
