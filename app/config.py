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
# runtime-config keys a named profile pins over values saved from the dashboard (symbols, the measured strategy frame,
# the session window and the gold risk caps); everything else saved from the dashboard is still honoured
PROFILE_PINNED: dict[str, tuple[str, ...]] = {
    "strategy": ("symbols", "trade_symbols", "signal_timeframe", "htf_timeframe", "atr_sl_mult", "atr_tp_mult",
                 "max_hold_minutes", "rescue_enabled", "close_at_session_end", "min_sl_points"),
    "schedule": ("enabled", "start", "end", "days"),
    "risk": ("max_spread_points", "max_volume_lots", "news_blackout_minutes", "max_open_positions",
             "max_positions_per_symbol", "pyramid_min_r", "pyramid_min_distance_atr", "max_same_currency_direction"),
}

PROFILES: dict[str, dict] = {
    "gold": {
        "ACTIVE_SYMBOLS": "XAUUSD", "TRADE_SYMBOLS": "XAUUSD", "CHART_TIMEFRAMES": "M1,M5,M15,H1,H4,D1",
        # Strategy frame measured on 9 years of XAUUSD (research/results/GOLDCFG/SYNTHESIS.md): trend-v1 on M5/M15 loses
        # 0.10-0.19 R per trade (the spread eats ~0.18 R of a 1.5xATR stop; the entry itself has no edge), H1/H4 with a
        # 3xATR stop, 24 h time stop, no rescue and the London+New York window is the lowest-cost frame: TRAIN n 1431
        # -0.0025 R [-0.043; +0.039] standard spread, +0.019 R on ECN spread -> expectancy ~0, ~5 trades/week.
        "SIGNAL_TIMEFRAME": "H1", "HTF_TIMEFRAME": "H4", "ATR_SL_MULT": 3.0, "ATR_TP_MULT": 4.5,
        "MAX_HOLD_MINUTES": 1440, "RESCUE_ENABLED": False,  # RESCUE is calibrated for <= M5 bars; on H1 it is noise
        "SESSION_ENABLED": True, "SESSION_START": "09:00", "SESSION_END": "19:00",  # 07-17 UTC in winter (tz below)
        "SESSION_DAYS": "MON,TUE,WED,THU,FRI", "CLOSE_AT_SESSION_END": False,  # 24 h TIME exits carry the result
        # 150 points = 1.50 USD: 3-6 % of a 3xATR(H1) stop (25-45 USD); on a 1.5xATR(M5) stop a demo spread of 1.2 USD
        # was 30-40 % of R - the reason the M5 frame loses. Research cost model: ~40 pts standard, ~17 pts ECN.
        "MAX_SPREAD_POINTS": 150.0, "MIN_SL_POINTS": 150, "MAX_VOLUME_LOTS": 0.05, "NEWS_BLACKOUT_MINUTES": 45,
        "MAX_OPEN_POSITIONS": 3, "MAX_POSITIONS_PER_SYMBOL": 3, "PYRAMID_MIN_R": 0.5, "PYRAMID_MIN_DISTANCE_ATR": 1.0,
        "MAX_SAME_CURRENCY_DIRECTION": 3, "ARCHIVE_EXTRA": "M15:730,H1:1825,H4:3650,D1:7300",
        "ARCHIVE_EXTRA_WITH_POSITIONS": True,  # keep downloading history while gold positions are open (SL/TP sit at the broker)
        "AI_TRADER_ENTRY_MODE": "shadow", "AI_TRADER_MANAGE_MODE": "shadow", "AI_TRADER_TIMEFRAME": "M15",
        "AI_TRADER_SYMBOLS": "XAUUSD",
    },
    # owner's rule (2026-10-29): gold only, continuous M1 scalping — open one position, close it as soon as it is
    # +SCALP_TARGET_USD in net profit (broker TP at the same distance as a backup), then look for the next entry on the
    # next closed M1 bar. No session window, no daily/streak/news/cooldown/rollover limits, no Claude veto. The SL stays
    # (broker-side protection) at max(ATR_SL_MULT x ATR(M1), MIN_SL_POINTS). NOT measured: with a ~1.2 USD demo spread a
    # +1 USD target vs a ~3 USD stop needs a win rate above ~75 % to break even.
    "gold_scalp": {
        "ACTIVE_SYMBOLS": "XAUUSD", "TRADE_SYMBOLS": "XAUUSD", "CHART_TIMEFRAMES": "M1,M5,M15,H1",
        "STRATEGY_NAME": "scalp-v1", "SIGNAL_TIMEFRAME": "M1", "HTF_TIMEFRAME": "M1",
        "SCALP_TARGET_USD": 1.0, "FIXED_VOLUME_LOTS": 0.01,
        # profit ladder measured on 105 days of M1 gold (scratch backtest, spread 0.47): with cap 10 the RSI entry made
        # +0.03 USD/trade, 81 % of trades closed positive; a fixed +1 TP lost -0.44 USD/trade (losses are full SLs)
        "LADDER_ENABLED": True, "LADDER_TRIGGER_USD": 0.3, "LADDER_STEP_USD": 0.5, "LADDER_SL_LAG_USD": 0.6,
        "LADDER_CAP_USD": 10.0, "ATR_SL_MULT": 3.0, "ATR_TP_MULT": 1.0, "MIN_SL_POINTS": 300,
        "MAX_HOLD_MINUTES": 0, "RESCUE_ENABLED": False, "BREAK_EVEN_AT_R": 0.0,
        "TRAILING_ENABLED": False, "PROFIT_LOCK_ENABLED": False, "CLOSE_AT_SESSION_END": False,
        "SESSION_ENABLED": False, "SESSION_START": "00:00", "SESSION_END": "23:59",
        "SESSION_DAYS": "MON,TUE,WED,THU,FRI,SAT,SUN",
        # spread is the main cost: at 0.20 USD every variant was positive, at 0.47 most were negative
        "MAX_SPREAD_POINTS": 60.0, "MAX_VOLUME_LOTS": 0.05, "NEWS_BLACKOUT_MINUTES": 0, "MAX_OPEN_POSITIONS": 1,
        "MAX_POSITIONS_PER_SYMBOL": 1, "MAX_SAME_CURRENCY_DIRECTION": 3, "MAX_TRADES_PER_DAY": 100000,
        "MAX_CONSECUTIVE_LOSSES": 100000, "LOSS_STREAK_PAUSE_MINUTES": 0, "COOLDOWN_AFTER_LOSS_MINUTES": 0,
        "ROLLOVER_BLACKOUT_MINUTES": 0, "MAX_SIGNALS_PER_SYMBOL_PER_SESSION": 100000, "MAX_DAILY_LOSS_PCT": 100.0,
        "MAX_EQUITY_DRAWDOWN_PCT": 100.0, "MAX_TOTAL_OPEN_RISK_PCT": 100.0, "MIN_FREE_MARGIN_PCT": 10.0,
        # Claude (claude-opus-5-5, effort high) decides every entry the RSI rule proposes: only an explicit AGREE trades,
        # no answer = no trade. ~37 proposals/day x ~0.03-0.05 USD. Expiry 3 min covers the 10-60 s analysis.
        "CLAUDE_ENABLED": True, "CLAUDE_VETO_ENABLED": True, "CLAUDE_REQUIRED": True, "CLAUDE_EXECUTE_ON": "AGREE",
        "CLAUDE_REVIEW_EFFORT": "high", "CLAUDE_TIMEOUT_SECONDS": 60, "SIGNAL_EXPIRY_MINUTES": 3,
        "CLAUDE_TRADE_REVIEW_ENABLED": False, "CLAUDE_DAILY_REVIEW_ENABLED": False,
        "AI_TRADER_ENTRY_MODE": "off", "AI_TRADER_MANAGE_MODE": "off", "AI_TRADER_SYMBOLS": "XAUUSD",
        "ARCHIVE_EXTRA": "off",  # no multi-year history downloads competing with the M1 feed
    },
}
# owner's choice (2026-10-29): only the entry step changes = the CLUB 81 SMC check on M15 with H1/H4; steps 2-4 unchanged
# (0.01 lot, SL max(3xATR(M1), 3 USD), TP +1, ladder to +10, Claude AGREE gate). Measured in the scratch backtest at -1 USD/trade with this exit; kept on DEMO by decision.
PROFILES["gold_smc"] = {
    **PROFILES["gold_scalp"], "STRATEGY_NAME": "smc-v1", "SIGNAL_TIMEFRAME": "M15", "HTF_TIMEFRAME": "H1",
    "CHART_TIMEFRAMES": "M1,M5,M15,H1,H4", "SIGNAL_EXPIRY_MINUTES": 10,
}

# owner's choice (2026-10-29): many quick trades. gold_scalp's M1 RSI entry + ladder exit (the only fast frame that did not
# lose in the scratch backtest: ~37 trades/day, 81 % positive, +0.03 USD/trade; SMC aggressive M1 was -0.29), with
# Claude as a safety check only: everything but a VETO executes, no answer = trade anyway, medium effort for speed.
PROFILES["gold_scalp_fast"] = {
    **PROFILES["gold_scalp"], "CHART_TIMEFRAMES": "M1,M5,M15,H1,H4", "CLAUDE_EXECUTE_ON": "VETO",
    "CLAUDE_REQUIRED": False, "CLAUDE_REVIEW_EFFORT": "medium", "CLAUDE_TIMEOUT_SECONDS": 30,
}

# owner's EXPERIMENT (2026-10-29): the basket scheme exactly as in the owner's video: 5 x 0.01 lot on gold
# opened together in one direction, +1 every 0.50 USD against (max 8), all closed together once they average +1 USD
# each; safety stop 30 % of the balance (not in the video); lot and target scale with the balance as in the video.
# Scratch backtest (15 weeks M1): every week ended with the account lost - an experiment, not a measured edge.
PROFILES["gold_basket"] = {
    **PROFILES["gold_scalp"], "BASKET_ENABLED": True, "BASKET_START_POSITIONS": 5, "BASKET_STEP_USD": 0.5,
    "BASKET_MAX_POSITIONS": 3, "BASKET_TP_PER_POSITION_USD": 0.8, "BASKET_POSITION_TP_USD": 0.8,
    # scaled with the balance like the video: 0.01 lot per 150 USD (0.01 at 188, 0.02 at 300 ...), target 1 USD per
    # 0.01 lot per position
    "BASKET_LOT_PER_USD": 150.0, "MAX_VOLUME_LOTS": 1.0,
    # 2026-10-30: entries from the 4-timeframe analysis, one position per closed M1 bar, max 3 (the demo margin limit)
    "BASKET_ENTRY_MODE": "mtf", "BASKET_MTF_MIN_VOTES": 3,
    # 2026-10-30 (owner): small basket stop -3 USD instead of 30 % of the balance; broker SL 3 USD per position as the
    # offline protection. Scratch backtest of these rules (76 trading days, spread 0.30): -0.50 USD per close at -3 vs
    # -0.42 at -51 - smaller losses, more of them, still negative.
    "BASKET_MAX_LOSS_PCT": 0.0, "BASKET_MAX_LOSS_USD": 3.0,
    # 2026-10-30 (owner): recovery hedge instead of the -3 stop: down 2 USD and the 4-TF analysis flipped -> 2 positions
    # the other way; the group closes at +0.10 (about zero) or -15 USD. Broker SL 15 USD = offline protection only.
    "BASKET_RECOVERY_ENABLED": True, "BASKET_RECOVERY_TRIGGER_USD": 2.0, "BASKET_RECOVERY_POSITIONS": 2,
    "BASKET_RECOVERY_EXIT_USD": 0.1,
    # 2026-10-30 (owner): let wrong positions run while the hedges work -> group cap -40 USD, broker SL 40 USD each
    "BASKET_RECOVERY_MAX_LOSS_USD": 40.0, "BASKET_SL_DISTANCE": 40.0,
    "ALLOW_HEDGE_POSITIONS": True, "BASKET_RECOVERY_FLIP": "fast",
    # 2026-10-30 (owner): the trades are minutes long and close at +0.80 -> the direction comes from M1 + M5 only
    "BASKET_ENTRY_VOTE": "short",
    "LADDER_ENABLED": False, "SCALP_TARGET_USD": 0.0, "MAX_OPEN_POSITIONS": 8, "MAX_POSITIONS_PER_SYMBOL": 8,
    "PYRAMID_MIN_R": -1000.0, "PYRAMID_MIN_DISTANCE_ATR": 0.0, "MAX_SPREAD_POINTS": 80.0,
    # Claude is on so the "Claude ia deciziile" switch on the Positions page can use it (one call per M1 bar when on);
    # with the switch off the basket rules decide and Claude is never called
    "CLAUDE_ENABLED": True, "CLAUDE_REVIEW_EFFORT": "medium", "CLAUDE_TIMEOUT_SECONDS": 30,
}

# keys pinned by a profile on top of PROFILE_PINNED (the scalp profile also owns the limits it switches off)
PROFILE_EXTRA_PINNED: dict[str, dict[str, tuple[str, ...]]] = {
    "gold_scalp": {
        "strategy": ("scalp_target_usd", "ladder_enabled", "ladder_trigger_usd", "ladder_step_usd",
                     "ladder_sl_lag_usd", "ladder_cap_usd", "signal_expiry_minutes", "break_even_at_r", "trailing_enabled",
                     "profit_lock_enabled"),
        "risk": ("fixed_volume_lots", "max_trades_per_day", "max_consecutive_losses", "loss_streak_pause_minutes",
                 "cooldown_after_loss_minutes", "rollover_blackout_minutes", "max_signals_per_symbol_per_session",
                 "max_daily_loss_pct", "max_equity_drawdown_pct", "max_total_open_risk_pct", "min_free_margin_pct"),
    },
}


PROFILE_EXTRA_PINNED["gold_smc"] = PROFILE_EXTRA_PINNED["gold_scalp"]
PROFILE_EXTRA_PINNED["gold_scalp_fast"] = PROFILE_EXTRA_PINNED["gold_scalp"]
PROFILE_EXTRA_PINNED["gold_basket"] = PROFILE_EXTRA_PINNED["gold_scalp"]


def profile_pins(name: str) -> dict[str, tuple[str, ...]]:
    extra = PROFILE_EXTRA_PINNED.get(name, {})
    return {attr: keys + extra.get(attr, ()) for attr, keys in PROFILE_PINNED.items()}

ROOT_DIR = Path(__file__).resolve().parent.parent
VERSION = "0.1.0"
STRATEGY_VERSION = "trend-v1"

TF_SECONDS = {"M1": 60, "M5": 300, "M15": 900, "M30": 1800, "H1": 3600, "H4": 14400, "D1": 86400}
DAYS = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]
MODES = ("SIGNAL_ONLY", "PAPER", "DEMO", "LIVE")


def spread_cap(max_points, symbol: str, point) -> float:
    """MAX_SPREAD_POINTS in the symbol's own points. Gold caps are measured in 0.01 USD points (2-digit quotes);
    a broker quoting gold with 3 digits (Exness XAUUSDm, point 0.001) needs the cap x10 for the same USD spread."""
    cap = float(max_points)
    sym = (symbol or "").upper()
    if ("XAU" in sym or "GOLD" in sym) and point and 0 < float(point) < 0.01:
        cap *= 0.01 / float(point)
    return round(cap, 6)


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
    # live strategy plugin pinned by a profile ("" = the one saved from the dashboard, default trend-v1)
    STRATEGY_NAME: str = ""
    SIGNAL_TIMEFRAME: str = "M5"
    HTF_TIMEFRAME: str = "H1"
    CHART_TIMEFRAMES: str = "M1,M5,M15,H1"
    TRADING_MODE: str = "PAPER"
    EXECUTION_ENABLED: bool = False
    LIVE_TRADING_ALLOWED: bool = True
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
    # > 0: every bot entry uses exactly this lot size (broker min/max/step apply) instead of risk-based sizing
    FIXED_VOLUME_LOTS: float = 0.0
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
    # allow bot positions in BOTH directions on one symbol (basket recovery hedge only); default False
    ALLOW_HEDGE_POSITIONS: bool = False
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
    # > 0: the position manager closes a bot position once its net open profit (price + swap + commission) reaches
    # this many account-currency units (CLOSE reason PROFIT_TARGET); 0 = off
    SCALP_TARGET_USD: float = 0.0
    # profit ladder (with SCALP_TARGET_USD as the first TP): once the open profit is within LADDER_TRIGGER_USD of the
    # current TP, the TP moves LADDER_STEP_USD further and the SL locks max(0, trigger - LADDER_SL_LAG_USD) of profit
    # (never backwards); at LADDER_CAP_USD the position is closed. Example 0.3 / 0.5 / 0.6 / 10: +0.70 -> TP 1.50,
    # SL +0.10; +1.20 -> TP 2.00, SL +0.60; ... up to +10.
    LADDER_ENABLED: bool = False
    # basket / grid EXPERIMENT (app/basket.py): replaces the signal entries while on; see that module for the rules
    BASKET_ENABLED: bool = False
    BASKET_START_POSITIONS: int = 1
    BASKET_STEP_USD: float = 3.0
    BASKET_MAX_POSITIONS: int = 5
    BASKET_TP_USD: float = 10.0
    # > 0: the basket closes at this net profit per position (per 0.01 lot) instead of BASKET_TP_USD
    BASKET_TP_PER_POSITION_USD: float = 0.0
    # > 0: lot = 0.01 per this much balance (the profit target per position scales with it); 0 = fixed lot
    BASKET_LOT_PER_USD: float = 0.0
    # > 0: broker TP of every basket position at this profit per 0.01 lot (closes on its own); 0 = far TP
    BASKET_POSITION_TP_USD: float = 0.0
    # basket entries: momentum (burst of START positions + averaging every STEP) | mtf (one position per closed M1 bar
    # when M1/M5/M15/H1 agree on >= BASKET_MTF_MIN_VOTES, re-analysis after every close)
    BASKET_ENTRY_MODE: str = "momentum"
    BASKET_MTF_MIN_VOTES: int = 3
    # basket entry direction: short = M1 + M5 (EMA9 vs EMA21) both agree (M15/H1 only shown) | mtf = 4-TF consensus
    BASKET_ENTRY_VOTE: str = "mtf"
    # recovery hedge (app/basket.py): basket down TRIGGER and the 4-TF analysis flipped -> POSITIONS the other way, then
    # the whole group closes at +EXIT_USD or -MAX_LOSS_USD
    BASKET_RECOVERY_ENABLED: bool = False
    BASKET_RECOVERY_TRIGGER_USD: float = 2.0
    BASKET_RECOVERY_POSITIONS: int = 2
    BASKET_RECOVERY_EXIT_USD: float = 0.1
    BASKET_RECOVERY_MAX_LOSS_USD: float = 15.0
    # a new hedge round also opens when the price moved this much further against the losers since the last round
    BASKET_RECOVERY_STEP_USD: float = 1.0
    # wait while the losers come back: hedge only once they worsened again by this much from their best level since the
    # last round (with M1 against), or by BASKET_RECOVERY_STEP_USD without M1
    BASKET_RECOVERY_TURN_USD: float = 0.5
    # Strategy 2 (zone recovery, chosen on the Positions page): zone width, lot multiplier, max levels, group exit
    BASKET_ZR_ZONE_USD: float = 2.0
    BASKET_ZR_MULT: float = 2.0
    BASKET_ZR_MAX_LEVELS: int = 4
    BASKET_ZR_EXIT_USD: float = 1.0
    # Claude manager (basket.claude on the Positions page): max lot per position and max open positions it may use
    CLAUDE_MAX_LOT: float = 0.08
    BASKET_MAX_POSITIONS_CLAUDE: int = 8
    # how the "chart turned" is read: fast = M1 + M5 (EMA9 vs EMA21) both against the basket | mtf = the 4-TF consensus
    BASKET_RECOVERY_FLIP: str = "fast"
    # > 0: basket stop = this % of the balance at the basket's first entry, instead of BASKET_MAX_LOSS_USD
    BASKET_MAX_LOSS_PCT: float = 0.0
    BASKET_MAX_LOSS_USD: float = 60.0
    BASKET_SL_DISTANCE: float = 30.0
    BASKET_MOMENTUM_BARS: int = 20
    LADDER_TRIGGER_USD: float = 0.3
    LADDER_STEP_USD: float = 0.5
    LADDER_SL_LAG_USD: float = 0.6
    LADDER_CAP_USD: float = 10.0
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
    # which verdict lets a signal execute: VETO (default: everything but a VETO) | AGREE (only an explicit AGREE)
    CLAUDE_EXECUTE_ON: str = "VETO"
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
            "fixed_volume_lots": self.FIXED_VOLUME_LOTS,
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
            "allow_hedge": self.ALLOW_HEDGE_POSITIONS,
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
            "scalp_target_usd": self.SCALP_TARGET_USD,
            "ladder_enabled": self.LADDER_ENABLED,
            "ladder_trigger_usd": self.LADDER_TRIGGER_USD,
            "ladder_step_usd": self.LADDER_STEP_USD,
            "ladder_sl_lag_usd": self.LADDER_SL_LAG_USD,
            "ladder_cap_usd": self.LADDER_CAP_USD,
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
