"""Settings loaded from .env (CONTRACT §5) + small shared helpers."""
from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# PROFILE=gold: the owner's "gold only" setup in one line. Applied over .env AND over settings saved from the dashboard
# (Runtime.load_runtime_config keeps symbols / trade_symbols pinned), so a stale saved symbol list can never bring the
# other pairs back. Values follow docs/PROFIL_AUR.md.
PROFILES: dict[str, dict] = {
    "gold": {
        "ACTIVE_SYMBOLS": "XAUUSD", "TRADE_SYMBOLS": "XAUUSD", "CHART_TIMEFRAMES": "M1,M5,M15,H1,H4,D1",
        "MAX_SPREAD_POINTS": 60.0, "MIN_SL_POINTS": 150, "MAX_VOLUME_LOTS": 0.05, "NEWS_BLACKOUT_MINUTES": 45,
        "MAX_OPEN_POSITIONS": 3, "MAX_POSITIONS_PER_SYMBOL": 3, "PYRAMID_MIN_R": 0.5, "PYRAMID_MIN_DISTANCE_ATR": 1.0,
        "MAX_SAME_CURRENCY_DIRECTION": 3, "ARCHIVE_EXTRA": "M15:730,H1:1825,H4:3650,D1:7300",
        "AI_TRADER_ENTRY_MODE": "shadow", "AI_TRADER_MANAGE_MODE": "shadow", "AI_TRADER_TIMEFRAME": "M15",
        "AI_TRADER_SYMBOLS": "XAUUSD",
    },
}

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
    # named profile applied over the values below (and over dashboard-saved symbols): "" | gold — see PROFILES
    PROFILE: str = ""
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
    # symbols the bot may TRADE (subset of ACTIVE_SYMBOLS; empty = all). The others are still quoted, analysed and
    # shadow-labelled (context for Claude / ML), but never get a signal or an order.
    TRADE_SYMBOLS: str = ""
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
    # no new entry on a symbol within N minutes after a bot trade on it closed at a loss (0 = off)
    COOLDOWN_AFTER_LOSS_MINUTES: int = 30
    # no new entries within ±N minutes of the 17:00 America/New_York rollover (DST-safe; 0 = off)
    ROLLOVER_BLACKOUT_MINUTES: int = 20
    # pyramiding: up to N bot positions on one symbol (default 1 = one position per symbol, the historical rule).
    # A further position is allowed only in the SAME direction and only while every open position on the symbol is at
    # least PYRAMID_MIN_R in open profit (R = initial risk); an in-flight OPEN blocks adding. MAX_OPEN_POSITIONS still caps.
    MAX_POSITIONS_PER_SYMBOL: int = 1
    PYRAMID_MIN_R: float = 0.5
    # a further position on the symbol must be at least this many ATR(14) away from every open entry on it (0 = off)
    PYRAMID_MIN_DISTANCE_ATR: float = 1.0
    ATR_SL_MULT: float = 1.5
    ATR_TP_MULT: float = 2.5
    MIN_SL_POINTS: int = 60
    MIN_SCORE_COMPONENTS: int = 4
    SIGNAL_EXPIRY_MINUTES: int = 15
    BREAK_EVEN_AT_R: float = 1.0
    # break-even SL = entry ± max(spread now + 3 points, BE_LOCK_R × initial risk); 0 = old entry ± 2 points
    BE_LOCK_R: float = 0.1
    TRAILING_ENABLED: bool = False
    # profit-lock trailing: once the open profit reaches PROFIT_LOCK_ACTIVATE_R × initial risk, the SL follows the price
    # PROFIT_LOCK_GAP_R × risk behind it (only towards profit, in steps of PROFIT_LOCK_STEP_R × risk). With a 1.50 risk:
    # activate 0.67 (+1.00) -> SL at +0.50; price +1.30 -> SL +0.80. Applies to every bot position (every strategy).
    PROFIT_LOCK_ENABLED: bool = False
    PROFIT_LOCK_ACTIVATE_R: float = 0.67
    PROFIT_LOCK_GAP_R: float = 0.33
    PROFIT_LOCK_STEP_R: float = 0.1
    MAX_HOLD_MINUTES: int = 240
    CLOSE_AT_SESSION_END: bool = False
    # rescue exit: after RESCUE_MINUTES open, MAE ≥ RESCUE_MAE_R and ≥ RESCUE_NEG_FRAC of the 1-min samples under
    # water, close at the first r ≥ RESCUE_CLOSE_R (CLOSE reason RESCUE; never moves the SL)
    RESCUE_ENABLED: bool = True
    RESCUE_MINUTES: int = 30
    RESCUE_MAE_R: float = 0.3
    RESCUE_NEG_FRAC: float = 0.6
    RESCUE_CLOSE_R: float = 0.1
    PAPER_START_BALANCE: float = 10000
    ANTHROPIC_API_KEY: str = ""
    CLAUDE_ENABLED: bool = True
    CLAUDE_MODEL: str = "claude-opus-5-5"
    CLAUDE_VETO_ENABLED: bool = True
    CLAUDE_REQUIRED: bool = False
    CLAUDE_TIMEOUT_SECONDS: float = 45
    # reasoning depth of every Claude call (adaptive thinking is always on): low | medium | high | xhigh | max
    CLAUDE_REVIEW_EFFORT: str = "medium"
    # Claude as trade analyst: post-trade review of every closed bot trade + a daily review at session end.
    # Analysis only — it never changes settings, levels or orders.
    CLAUDE_TRADE_REVIEW_ENABLED: bool = True
    CLAUDE_DAILY_REVIEW_ENABLED: bool = True
    # AI trader: Claude reads the chart on every closed bar of AI_TRADER_TIMEFRAME and decides entry / SL / TP / hold,
    # then manages open bot positions (HOLD / TIGHTEN_SL / CLOSE). Modes: off | shadow (decisions are only recorded and
    # labelled with what would have happened) | live (shadow + executed through the normal signal pipeline and Risk
    # Engine). Defaults are the safe ones; the dashboard can switch modes (settings key 'ai_trader').
    AI_TRADER_ENTRY_MODE: str = "off"
    AI_TRADER_MANAGE_MODE: str = "off"
    AI_TRADER_TIMEFRAME: str = "M15"
    AI_TRADER_SYMBOLS: str = ""  # empty = ACTIVE_SYMBOLS
    AI_TRADER_MANAGE_EVERY_MIN: int = 15
    AI_TRADER_MAX_CALLS_PER_DAY: int = 300
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
    # research-only symbols: archived (D1/H4/H1, ARCHIVE_EXTRA days, same throttling) after the active symbols are
    # complete; NEVER analysed, quoted or traded. Comma list; "off" disables; needs ARCHIVE_EXTRA on.
    RESEARCH_SYMBOLS: str = ("XAUUSD,XAGUSD,US500,US30,NAS100,UK100,DE40,JP225,XTIUSD,XBRUSD,BTCUSD,ETHUSD,"
                             "USDCHF,USDMXN,USDZAR,USDTRY")
    # economic-calendar history with numbers (CALHIST, EA v1.14+, research only: app/calendar_hist.py)
    CALHIST_ENABLED: bool = True
    CALHIST_INTERVAL_S: float = 30
    CALHIST_MIN_IMP: int = 1
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

    @model_validator(mode="after")
    def _apply_profile(self):
        prof = PROFILES.get(str(self.PROFILE or "").strip().lower())
        if prof:
            # the broker's own name for gold (XAUUSD.a, GOLD, XAUUSDm, XAUUSD.cash ...) wins over the profile's "XAUUSD"
            gold = next((s for s in split_symbols(self.TRADE_SYMBOLS) + split_symbols(self.ACTIVE_SYMBOLS)
                         if "XAU" in s.upper() or "GOLD" in s.upper()), None)
            for k, v in prof.items():
                if gold and k in ("ACTIVE_SYMBOLS", "TRADE_SYMBOLS", "AI_TRADER_SYMBOLS"):
                    v = gold
                setattr(self, k, v)
        return self

    @property
    def profile_name(self) -> str:
        return str(self.PROFILE or "").strip().lower() if str(self.PROFILE or "").strip().lower() in PROFILES else ""

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
            "cooldown_after_loss_minutes": self.COOLDOWN_AFTER_LOSS_MINUTES,
            "rollover_blackout_minutes": self.ROLLOVER_BLACKOUT_MINUTES,
            "max_positions_per_symbol": self.MAX_POSITIONS_PER_SYMBOL,
            "pyramid_min_r": self.PYRAMID_MIN_R,
            "pyramid_min_distance_atr": self.PYRAMID_MIN_DISTANCE_ATR,
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
            "symbols": split_symbols(self.ACTIVE_SYMBOLS),
            "trade_symbols": [s for s in split_symbols(self.TRADE_SYMBOLS) if s in split_symbols(self.ACTIVE_SYMBOLS)],
            "atr_sl_mult": self.ATR_SL_MULT,
            "atr_tp_mult": self.ATR_TP_MULT,
            "min_sl_points": self.MIN_SL_POINTS,
            "min_score": self.MIN_SCORE_COMPONENTS,
            "signal_expiry_minutes": self.SIGNAL_EXPIRY_MINUTES,
            "break_even_at_r": self.BREAK_EVEN_AT_R,
            "trailing_enabled": self.TRAILING_ENABLED,
            "profit_lock_enabled": self.PROFIT_LOCK_ENABLED,
            "profit_lock_activate_r": self.PROFIT_LOCK_ACTIVATE_R,
            "profit_lock_gap_r": self.PROFIT_LOCK_GAP_R,
            "profit_lock_step_r": self.PROFIT_LOCK_STEP_R,
            "max_hold_minutes": self.MAX_HOLD_MINUTES,
            "close_at_session_end": self.CLOSE_AT_SESSION_END,
            "be_lock_r": self.BE_LOCK_R,
            "rescue_enabled": self.RESCUE_ENABLED,
            "rescue_minutes": self.RESCUE_MINUTES,
            "rescue_mae_r": self.RESCUE_MAE_R,
            "rescue_neg_frac": self.RESCUE_NEG_FRAC,
            "rescue_close_r": self.RESCUE_CLOSE_R,
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


def split_symbols(s: str) -> list[str]:
    """Symbol lists keep the broker's exact spelling: MT5 symbol names are case-sensitive (XAUUSD.a, EURUSDm)."""
    out = []
    for x in (s or "").split(","):
        x = x.strip()
        if x and x not in out:
            out.append(x)
    return out


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
