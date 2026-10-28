"""Settings loaded from .env (CONTRACT §5) + small shared helpers."""
from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parent.parent
VERSION = "0.1.0"
STRATEGY_VERSION = "trend-v1"

TF_SECONDS = {"M1": 60, "M5": 300, "M15": 900, "M30": 1800, "H1": 3600, "H4": 14400, "D1": 86400}
DAYS = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]
MODES = ("SIGNAL_ONLY", "PAPER", "DEMO", "LIVE")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ROOT_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        env_ignore_empty=True,
    )

    APP_ENV: str = "development"
    HOST: str = "127.0.0.1"
    PORT: int = 8787
    ADMIN_TOKEN: str = ""
    DATA_SOURCE: str = "mt5_bridge"
    MT5_BRIDGE_KEY: str = ""
    MT5_MAGIC: int = 552211
    DATABASE_PATH: str = "data/bot.db"
    DEFAULT_TIMEZONE: str = "Europe/Chisinau"
    SESSION_ENABLED: bool = True
    SESSION_START: str = "09:00"
    SESSION_END: str = "13:00"
    SESSION_DAYS: str = "MON,TUE,WED,THU,FRI"
    ACTIVE_SYMBOLS: str = "EURUSD"
    SIGNAL_TIMEFRAME: str = "M5"
    HTF_TIMEFRAME: str = "H1"
    CHART_TIMEFRAMES: str = "M1,M5,M15,H1"
    TRADING_MODE: str = "PAPER"
    EXECUTION_ENABLED: bool = False
    LIVE_TRADING_ALLOWED: bool = False
    MUST_HAVE_SL_TP: bool = True
    MAX_RISK_PER_TRADE_PCT: float = 0.25
    MAX_DAILY_LOSS_PCT: float = 1.0
    MAX_EQUITY_DRAWDOWN_PCT: float = 3.0
    MAX_OPEN_POSITIONS: int = 1
    MAX_TRADES_PER_DAY: int = 5
    MAX_CONSECUTIVE_LOSSES: int = 3
    MAX_SPREAD_POINTS: float = 30
    MAX_SLIPPAGE_POINTS: int = 20
    MAX_VOLUME_LOTS: float = 1.0
    MAX_SAME_CURRENCY_DIRECTION: int = 1
    MIN_FREE_MARGIN_PCT: float = 50
    MAX_TICK_AGE_SECONDS: float = 10
    MAX_SIGNALS_PER_SYMBOL_PER_SESSION: int = 5
    NEWS_BLACKOUT_MINUTES: int = 30
    NEWS_MIN_IMPORTANCE: int = 3
    MAX_TOTAL_OPEN_RISK_PCT: float = 2.0
    LOSS_STREAK_PAUSE_MINUTES: int = 90
    ATR_SL_MULT: float = 1.5
    ATR_TP_MULT: float = 2.5
    MIN_SL_POINTS: int = 60
    MIN_SCORE_COMPONENTS: int = 4
    SIGNAL_EXPIRY_MINUTES: int = 15
    BREAK_EVEN_AT_R: float = 1.0
    TRAILING_ENABLED: bool = False
    MAX_HOLD_MINUTES: int = 240
    CLOSE_AT_SESSION_END: bool = False
    PAPER_START_BALANCE: float = 10000
    ANTHROPIC_API_KEY: str = ""
    CLAUDE_ENABLED: bool = True
    CLAUDE_MODEL: str = "claude-opus-5-5"
    CLAUDE_VETO_ENABLED: bool = True
    CLAUDE_REQUIRED: bool = False
    CLAUDE_TIMEOUT_SECONDS: float = 45
    TELEGRAM_ENABLED: bool = False
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_CHAT_ID: str = ""
    TELEGRAM_MESSAGE_THREAD_ID: str = ""
    PUBLIC_APP_URL: str = "http://127.0.0.1:8787"
    # history archive pulled from the EA (v1.11+) for backtests / outcome labels
    ARCHIVE_ENABLED: bool = True
    ARCHIVE_DAYS: int = 120
    ARCHIVE_BATCH_BARS: int = 5000
    # extended higher-TF history (Stage C2 research): "TF:days,..." walked after the base set; off by default (live
    # unchanged), e.g. ARCHIVE_EXTRA=H1:1095,H4:1825,D1:3650 in .env for the research setup
    ARCHIVE_EXTRA: str = "off"
    ARCHIVE_EXTRA_INTERVAL_S: float = 60
    ARCHIVE_EXTRA_WITH_POSITIONS: bool = False
    # broker server-time DST rule for archive bars (EA v1.12+): auto | EET | NY | fixed | IANA zone name
    MT5_SERVER_TZ: str = "auto"
    # forward tests (PAPER, simulated only — never send orders): JSON list of
    # {name, strategy, signal_tf, htf_tf, symbols, risk_pct, equity, criteria}; settings key 'forward_tests' overrides
    FORWARD_TESTS: str = "[]"
    # ML (spec §6.4): shadow logging is on by default and never changes trading; a model acts only after explicit
    # manual approval. Approved entry model: veto + risk factor in [ML_SIZE_MIN, ML_SIZE_MAX] (max 2.0), every Risk
    # Engine cap still applies; a scaled-up trade never risks more than ML_MAX_RISK_PCT % of equity.
    ML_ENABLED: bool = True
    ML_SIZE_MIN: float = 0.5
    ML_SIZE_MAX: float = 1.0
    ML_MAX_RISK_PCT: float = 0.5
    ML_RETRAIN_ENABLED: bool = True
    ML_RETRAIN_AT: str = "22:30"  # local time in DEFAULT_TIMEZONE
    ML_RETRAIN_TIMEOUT_MIN: float = 120
    ML_RETRAIN_ARGS: str = "train --db {db} --out {models_dir} --registry-db {db}"  # python -m app.ml ...
    ML_MODELS_DIR: str = "data/models"
    ML_DECISIONS_MAX_ROWS: int = 200000

    @property
    def db_path(self) -> Path:
        p = Path(self.DATABASE_PATH)
        return p if p.is_absolute() else ROOT_DIR / p

    @property
    def data_source(self) -> str:
        return "sim" if self.DATA_SOURCE.strip().lower() == "sim" else "mt5_bridge"

    @property
    def feed_label(self) -> str:
        return "SIMULATED" if self.data_source == "sim" else "MT5_FEED"

    def default_risk(self) -> dict:
        return {
            "must_have_sl_tp": True,
            "max_risk_per_trade_pct": self.MAX_RISK_PER_TRADE_PCT,
            "max_daily_loss_pct": self.MAX_DAILY_LOSS_PCT,
            "max_equity_drawdown_pct": self.MAX_EQUITY_DRAWDOWN_PCT,
            "max_open_positions": self.MAX_OPEN_POSITIONS,
            "max_trades_per_day": self.MAX_TRADES_PER_DAY,
            "max_consecutive_losses": self.MAX_CONSECUTIVE_LOSSES,
            "max_spread_points": self.MAX_SPREAD_POINTS,
            "max_slippage_points": self.MAX_SLIPPAGE_POINTS,
            "max_volume_lots": self.MAX_VOLUME_LOTS,
            "max_same_currency_direction": self.MAX_SAME_CURRENCY_DIRECTION,
            "min_free_margin_pct": self.MIN_FREE_MARGIN_PCT,
            "max_tick_age_seconds": self.MAX_TICK_AGE_SECONDS,
            "max_signals_per_symbol_per_session": self.MAX_SIGNALS_PER_SYMBOL_PER_SESSION,
            "news_blackout_minutes": self.NEWS_BLACKOUT_MINUTES,
            "news_min_importance": self.NEWS_MIN_IMPORTANCE,
            "max_total_open_risk_pct": self.MAX_TOTAL_OPEN_RISK_PCT,
            "loss_streak_pause_minutes": self.LOSS_STREAK_PAUSE_MINUTES,
        }

    def default_schedule(self) -> dict:
        return {
            "enabled": self.SESSION_ENABLED,
            "start": self.SESSION_START,
            "end": self.SESSION_END,
            "days": split_csv(self.SESSION_DAYS),
            "tz": self.DEFAULT_TIMEZONE,
        }

    def default_strategy(self) -> dict:
        return {
            "signal_timeframe": self.SIGNAL_TIMEFRAME.upper(),
            "htf_timeframe": self.HTF_TIMEFRAME.upper(),
            "symbols": split_csv(self.ACTIVE_SYMBOLS),
            "atr_sl_mult": self.ATR_SL_MULT,
            "atr_tp_mult": self.ATR_TP_MULT,
            "min_sl_points": self.MIN_SL_POINTS,
            "min_score": self.MIN_SCORE_COMPONENTS,
            "signal_expiry_minutes": self.SIGNAL_EXPIRY_MINUTES,
            "break_even_at_r": self.BREAK_EVEN_AT_R,
            "trailing_enabled": self.TRAILING_ENABLED,
            "max_hold_minutes": self.MAX_HOLD_MINUTES,
            "close_at_session_end": self.CLOSE_AT_SESSION_END,
        }

    def chart_timeframes(self) -> list[str]:
        return [t for t in split_csv(self.CHART_TIMEFRAMES) if t in TF_SECONDS]

    def secret_values(self) -> list[str]:
        return [s for s in (self.ANTHROPIC_API_KEY, self.TELEGRAM_BOT_TOKEN, self.MT5_BRIDGE_KEY, self.ADMIN_TOKEN) if s]

    @property
    def telegram_configured(self) -> bool:
        return bool(self.TELEGRAM_BOT_TOKEN and self.TELEGRAM_CHAT_ID)


def split_csv(s: str) -> list[str]:
    return [x.strip().upper() for x in (s or "").split(",") if x.strip()]


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def set_settings(s: Settings) -> None:
    global _settings
    _settings = s


# ---- helpers -------------------------------------------------------------

_TOKEN_RE = re.compile(r"bot\d{5,}:[A-Za-z0-9_-]{20,}")
_SK_RE = re.compile(r"sk-ant-[A-Za-z0-9_-]{10,}")


def redact(msg: object, secrets: list[str] | None = None) -> str:
    text = str(msg)
    if secrets is None:
        try:
            secrets = get_settings().secret_values()
        except Exception:
            secrets = []
    for s in secrets:
        if s and len(s) >= 4:
            text = text.replace(s, "***")
    text = _TOKEN_RE.sub("bot***", text)
    text = _SK_RE.sub("sk-ant-***", text)
    return text


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | float | int | None) -> str | None:
    if dt is None:
        return None
    if isinstance(dt, (int, float)):
        dt = datetime.fromtimestamp(dt, timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def round_price(x: float | None, digits: int) -> float | None:
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return None
    return round(float(x), int(digits))
