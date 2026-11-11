"""Settings loaded from .env (CONTRACT §5) + small shared helpers."""
from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# PROFILE=gold_basket (the only profile): the owner's gold-only Claude scalp bot. Applied over .env AND over settings
# saved from the dashboard, so a stale saved symbol list can never bring other pairs back.
# runtime-config keys the profile pins over values saved from the dashboard
PROFILE_PINNED: dict[str, tuple[str, ...]] = {
    "strategy": ("symbols", "trade_symbols", "signal_timeframe", "htf_timeframe"),
    "schedule": ("enabled", "start", "end", "days"),
    "risk": ("max_spread_points", "max_volume_lots", "news_blackout_minutes", "news_min_importance", "max_open_positions",
             "max_positions_per_symbol", "pyramid_min_r", "pyramid_min_distance_atr", "max_same_currency_direction",
             "fixed_volume_lots", "max_trades_per_day", "max_consecutive_losses", "loss_streak_pause_minutes",
             "cooldown_after_loss_minutes", "rollover_blackout_minutes", "max_signals_per_symbol_per_session",
             "max_daily_loss_pct", "max_equity_drawdown_pct", "max_total_open_risk_pct", "min_free_margin_pct"),
}

PROFILES: dict[str, dict] = {
    # gold, 24/5, one Claude decision per closed M1 bar (app.basket): no session window, no daily / streak / news /
    # cooldown limits - the basket's own rules (loss limit, zone block, young-loss close guard) are the safety net
    "gold_basket": {
        "ACTIVE_SYMBOLS": "XAUUSD", "TRADE_SYMBOLS": "XAUUSD", "CHART_TIMEFRAMES": "M1,M5,M15,H1",
        "SIGNAL_TIMEFRAME": "M1", "HTF_TIMEFRAME": "M1", "FIXED_VOLUME_LOTS": 0.01,
        "SESSION_ENABLED": False, "SESSION_START": "00:00", "SESSION_END": "23:59",
        "SESSION_DAYS": "MON,TUE,WED,THU,FRI,SAT,SUN",
        # 2026-11-03 (owner: fewer wrong entries): no new entry within 5 min of a high-impact news release
        "MAX_SPREAD_POINTS": 80.0, "MAX_VOLUME_LOTS": 1.0, "MAX_OPEN_POSITIONS": 8,
        # 04.11: the ADP release at 13:15 UTC took two SELLs to the SL (one 4 min before, one 2 min after); gold moves
        # 5-8 USD in the release minute, so medium-impact USD data counts too and the window is +-10 min
        "NEWS_BLACKOUT_MINUTES": 10, "NEWS_MIN_IMPORTANCE": 2,
        "MAX_POSITIONS_PER_SYMBOL": 8, "MAX_SAME_CURRENCY_DIRECTION": 3, "MAX_TRADES_PER_DAY": 100000,
        "MAX_CONSECUTIVE_LOSSES": 100000, "LOSS_STREAK_PAUSE_MINUTES": 0, "COOLDOWN_AFTER_LOSS_MINUTES": 0,
        "ROLLOVER_BLACKOUT_MINUTES": 0, "MAX_SIGNALS_PER_SYMBOL_PER_SESSION": 100000, "MAX_DAILY_LOSS_PCT": 100.0,
        "MAX_EQUITY_DRAWDOWN_PCT": 100.0, "MAX_TOTAL_OPEN_RISK_PCT": 0.0, "MIN_FREE_MARGIN_PCT": 10.0,
        "PYRAMID_MIN_R": -1000.0, "PYRAMID_MIN_DISTANCE_ATR": 0.0, "ALLOW_HEDGE_POSITIONS": True,
        "CLAUDE_ENABLED": True, "CLAUDE_REVIEW_EFFORT": "medium", "CLAUDE_TIMEOUT_SECONDS": 30,
        # lot 0.01 per 150 USD of balance when no start lot is set on the Positions page; broker SL/TP of a hedged leg
        # 40 USD away (the code closes the group)
        "BASKET_ENABLED": True, "BASKET_LOT_PER_USD": 150.0, "BASKET_SL_DISTANCE": 40.0,
        "BASKET_RECOVERY_TRIGGER_USD": 2.0, "BASKET_RECOVERY_EXIT_USD": 0.1,
        # owner's rule 2026-11-03 20:30: instead of the 4 USD stop, at -3.5 USD of price the code opens the opposite side
        # with 4 x the lot and the group closes at +0.1 (in code) or at the group cap. Measured on the 69 real entries
        # since 02.11 (M1 bars, spread 0.25): hedge at -4 x4 cap -15 = +41.4 USD per 0.01 base vs +15.5 with the plain SL;
        # a hedge at -2 (x2..x4) = -35..-77, so the trigger stays late. The dashboard group limit should be ~15 per 0.01
        # re-measured 04.11 on 80 entries with the broker SL checked on the wick first: plain SL +26.5, hedge on a wick
        # touch at -3.5 +10.7, hedge on an M1 CLOSE beyond -3.0 +35.8 (7 hedged, 1 capped) -> -3.0 with close confirmation
        # 04.11 10:00 UTC: live, 4 hedged groups made -21.98 USD against -16 with the plain SL (two snapped back within a
        # minute of the hedge: -12.37, -10.98) - the hedge opens at the extreme of the bar that confirmed it. Off, and
        # Claude may not hedge by hand either (BASKET_CLAUDE_HEDGE)
        "BASKET_HEDGE_AT_PRICE": 0.0, "BASKET_CLAUDE_HEDGE": False, "BASKET_ZR_MULT": 4.0, "BASKET_MAX_LOSS_USD": 15.0,
        # exits re-measured 04.11 on 53 real Exness entries: TP 1.5 / SL 4 = +23.0, TP 2 / SL 4 = +26.0, plus a 20-minute
        # time stop = +34.2 (the losers never reached +1.0, the winners that reach +1.5 mostly reach +2)
        "BASKET_SCALP_TP_PRICE": 2.0,
        # 09.11: M15 decisions in the H1 trend (see BASKET_DECISION_TF): exits TP = SL = 1.5 x ATR14 M15 (floors 2 / 4
        # USD only matter below ATR 1.3 / 2.7), 12-bar time stop = 180 min, no hour block, no ATR cap (M15 ATR runs
        # 4-10 USD), pullback look-back 3 bars, same-level re-entry window 45 min, EMA50 distance 0.5-2.0 ATR (the
        # geometry that gave PF 1.33 / +371 on the backtest)
        "BASKET_DECISION_TF": "M15", "BASKET_TREND_TF": "H1",
        "BASKET_SCALP_TP_ATR_MULT": 1.0, "BASKET_SCALP_SL_ATR_MULT": 1.5, "BASKET_SCALP_TIME_STOP_MINUTES": 180,
        # 09.11 (owner: more trades, more of them winners): trend gap 0.2 instead of 0.4 and TP 1.0 x ATR15 instead of
        # 1.5 - on the same 79 weekdays 312 trades (3.9/day) instead of 248 (3.1), 64 % winners instead of 56 %, +393 USD
        # instead of +344, drawdown -95 instead of -112, no losing month
        "BASKET_MIN_M5_GAP_ATR": 0.2,
        # second strategy: Asian-range breakout held for hours (+2.5R target), see SESSION_BREAKOUT_*; the mix of both made
        # +11.2 USD/day at 0.01 on 4.2 trades/day (backtest), at the price of a deeper drawdown (-154 vs -95)
        "SESSION_BREAKOUT_ENABLED": True,
        "BASKET_NO_ENTRY_UTC_HOURS": "", "BASKET_MAX_ATR_M1": 0.0, "BASKET_PULLBACK_LOOKBACK_BARS": 3,
        "BASKET_REENTRY_MINUTES": 45, "BASKET_MIN_DIST_EMA50_ATR": 0.5, "BASKET_MAX_STRETCH_EMA50_ATR": 2.0,
    },
}


def profile_pins(name: str) -> dict[str, tuple[str, ...]]:
    return dict(PROFILE_PINNED) if name in PROFILES else {}

ROOT_DIR = Path(__file__).resolve().parent.parent
VERSION = "0.1.0"

TF_SECONDS = {"M1": 60, "M5": 300, "M15": 900, "M30": 1800, "H1": 3600, "H4": 14400, "D1": 86400}
DAYS = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]
MODES = ("SIGNAL_ONLY", "PAPER", "DEMO", "LIVE")


def spread_cap(max_points, symbol: str, point) -> float:
    """A points setting (MAX_SPREAD_POINTS, MIN_SL_POINTS) in the symbol's own points. Gold settings are measured in
    0.01 USD points (2-digit quotes); a broker quoting gold with 3 digits (Exness XAUUSDm, point 0.001) needs x10."""
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
    # calendar fallback when the EA sends none (Exness): the public ForexFactory weekly JSON, fetched in a background
    # thread every 4 h; empty = off
    NEWS_FEED_URL: str = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
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
    # basket / grid EXPERIMENT (app/basket.py): replaces the signal entries while on; see that module for the rules
    BASKET_ENABLED: bool = False
    # > 0: lot = 0.01 per this much balance (the profit target per position scales with it); 0 = fixed lot
    BASKET_LOT_PER_USD: float = 0.0
    BASKET_RECOVERY_TRIGGER_USD: float = 2.0
    BASKET_RECOVERY_EXIT_USD: float = 0.1
    BASKET_ZR_MULT: float = 2.0
    # Claude manager (basket.claude on the Positions page): max lot per position and max open positions it may use
    # 2026-11-02 evening (owner): Claude sizes entries by confidence up to 0.5 lot; the free margin caps it in code
    CLAUDE_MAX_LOT: float = 0.1
    BASKET_MAX_POSITIONS_CLAUDE: int = 8
    # model for the basket calls while no position is open (only (re)arming entries): half the price of Opus 5.5 per token;
    # empty = CLAUDE_MODEL for every call (owner, 2026-11-03: API cost must stay below the trading profit)
    BASKET_CLAUDE_FLAT_MODEL: str = "claude-sonnet-5-5"
    # smart routing (owner, 2026-11-03: Opus for the important decisions, Sonnet for the light ones): the light model
    # above watches the routine bars (flat with M1/M5 not aligned, an open position on a plain new bar); CLAUDE_MODEL
    # gets flat bars where M1 and M5 agree (a direction decision), investigations, profit checks, hedged groups and the
    # first BASKET_CLAUDE_HEAVY_FIRST_MINUTES of a position. A light answer that wants to OPEN / ARM / CLOSE / CLOSE_ALL is
    # not executed: the same bar is asked again on CLAUDE_MODEL, so every entry direction and every exit is Opus's.
    # Off = the old split (light model only while flat)
    BASKET_CLAUDE_SMART_ROUTE: bool = True
    BASKET_CLAUDE_HEAVY_FIRST_MINUTES: float = 2.0
    # after this many losing closes in a row the basket opens nothing (and asks Claude nothing) for
    # BASKET_STREAK_PAUSE_MINUTES - 03.11 10:42-11:05: five small losses in a row in a sideways market after London open
    # Claude manager: CLOSE / CLOSE_ALL on a losing basket that is not hedged is refused while its newest position is
    # younger than this (minutes); the broker SL (and Claude's PROTECT invalidation stop) does the job. 30.10-03.11: 16
    # losing closes in the first 10 minutes realised -103.67 USD; held 30 more minutes on their own SL they made -14.57
    BASKET_CLAUDE_LOSS_CLOSE_MIN_MINUTES: float = 10.0
    # Claude manager: after BASKET_ZONE_BLOCK_LOSSES losing closes in a row on one side (a win on that side resets it,
    # the other side is ignored), a new entry on that side inside their entry zone +- 1 x ATR M1 is refused for
    # BASKET_ZONE_BLOCK_MINUTES after the last of them. 03.11 10:42-11:40: five SELLs at 3903.8-3905.2 lost -17.22 USD;
    # the rule would have refused the last three (-13.58) and nothing that won that day. 0 = off
    BASKET_ZONE_BLOCK_LOSSES: int = 2
    BASKET_ZONE_BLOCK_MINUTES: float = 90.0
    # spread above MULT x the symbol's MAX_SPREAD cap = broker quote at rollover / weekly close (30.10: 58 USD on gold at
    # 20:59 UTC): the basket neither opens, hedges nor closes on such prices
    BASKET_ABNORMAL_SPREAD_MULT: float = 3.0
    # no NEW basket entry within ±this many minutes of the 17:00 New York rollover (also the weekly close on Friday)
    BASKET_ROLLOVER_BLACKOUT_MINUTES: int = 20
    BASKET_SCALP_TP_PRICE: float = 1.5
    # a lone scalp position still open after this many minutes (neither TP nor SL hit) is closed at market in code;
    # 0 = off. 04.11 on 53 entries: +4.6 with TP 1.5, +8.2 with TP 2 - the winners come in the first minutes
    BASKET_SCALP_TIME_STOP_MINUTES: int = 0
    # whether Claude may open the opposite side of a losing position by hand (the code sizes it as a hedge); off on the
    # gold profile since 04.11
    BASKET_CLAUDE_HEDGE: bool = True
    # price distance against a lone position at which the code opens the hedge (BASKET_ZR_MULT x the lot, no TP of its
    # own); must sit below BASKET_SCALP_SL_PRICE or the broker SL fires first. 0 = off (the SL closes the position)
    BASKET_HEDGE_AT_PRICE: float = 0.0
    # the hedge waits for a CLOSED M1 bar beyond the trigger, not a wick touch (23:11 on 03.11: the hedge opened on the
    # exact low of a wick at the Asia reopen, the next bar jumped 5 USD and the group closed at -12.37; the bar had closed
    # only 2.2 against and the plain TP would have been hit a minute later)
    BASKET_HEDGE_ON_CLOSE: bool = True
    # no new entry (and no Claude call) this many minutes after the market reopens from a tick gap longer than
    # BASKET_REOPEN_GAP_MINUTES (the Exness daily break 22:00-23:05 UTC): the first minutes are thin and whip both ways
    BASKET_REOPEN_BLACKOUT_MINUTES: float = 15.0
    BASKET_REOPEN_GAP_MINUTES: float = 20.0
    # entry guards (2026-11-03, measured on 56 live entries since 02.11): a new entry against the M5 trend (EMA9 vs EMA21)
    # won 5 of 12 and lost 14.0 USD per 0.01 lot, with it 35 of 44; a PULLBACK that fills while the current M1 bar is an
    # impulse against the entry of >= this x ATR14 M1 waits for the impulse to stall (13:34 and 14:23: BUY filled inside
    # a 5-ATR drop and stopped out within a minute)
    BASKET_M5_GATE: bool = True
    # 09.11: timeframe of the basket's decisions. "M1" = the original scalper (guards, ATR14 and Claude's cadence on M1
    # bars, trend from BASKET_TREND_TF = M5). The gold profile runs "M15" with the trend from H1: a mechanical replay of
    # the M1 rules over 104,642 M1 bars (21.07-09.11.2026, spread 0.24) lost -254.8 USD per 0.01 lot (1451 trades, PF
    # 0.89, every month negative, no SL/TP/time-stop/hour/ADX variant positive), while the same pullback on M15 bars in
    # the H1 EMA9/21 trend with TP = SL = 1.5 x ATR14 M15 and a 12-bar time stop made +261.5 (297 trades, 54 %, PF 1.19,
    # every month positive, PF 1.12 at spread 0.40). On M15 the 09-16 UTC block HURTS (+64 instead of +262) and the
    # 12-15 UTC hours are the best of the day. Claude is called only at the close of a decision bar on which the
    # mechanical setup exists (_setup_side); the fast loop (fills, stops, time stop) stays in code on every M1 bar.
    BASKET_DECISION_TF: str = "M1"
    BASKET_TREND_TF: str = "M5"
    # "no pullback yet" looks at the last closed decision bar and the net move over this many bars (10 on M1, 3 on M15)
    BASKET_PULLBACK_LOOKBACK_BARS: int = 10
    # Asian-range breakout (app.session_breakout, 09.11): a second, mechanical strategy next to the basket - range of the
    # M1 bars RANGE_START-RANGE_END UTC, first break of its high/low after that opens at market, SL = range height capped
    # at SL_CAP_PCT % of price, TP = TP_R x risk, SL to entry at BE_R x risk, flat at FLAT_UTC, one trade a day, days with
    # a range outside MIN-MAX_RANGE_PCT % skipped. 0.01 lot on 21.07-09.11: 70 trades, 47 %, +510 USD, PF 1.69, DD -126
    SESSION_BREAKOUT_ENABLED: bool = False
    SESSION_BREAKOUT_RANGE_START_UTC: int = 0
    SESSION_BREAKOUT_RANGE_END_UTC: int = 5
    SESSION_BREAKOUT_FLAT_UTC: int = 21
    SESSION_BREAKOUT_TP_R: float = 2.5
    SESSION_BREAKOUT_BE_R: float = 1.5
    SESSION_BREAKOUT_MIN_RANGE_PCT: float = 0.30
    SESSION_BREAKOUT_MAX_RANGE_PCT: float = 1.60
    SESSION_BREAKOUT_SL_CAP_PCT: float = 0.6
    # 06.11, 140 live entries since 03.11: with the M5 EMA9-EMA21 gap (in the entry's direction) under 0.4 x ATR14 M1 -
    # a flat / glued M5, a range - entries won 17 of 35 and lost 28.4 USD per 0.01 lot; the other 105 won 74 (-2.3). Same
    # split on the all-rules period (3 of 6, -7.5 vs 13 of 22, -4.4). 06.11 06:20 and 07:40: two SELLs at a 0.3-0.4 USD
    # gap inside a 3-hour range, both stopped out by the upside breakout (-39.8 USD). 0 = off
    BASKET_MIN_M5_GAP_ATR: float = 0.4
    # API cost (03.11: ~30 USD/day pace against a 84 USD account at 0.01 lot): while an armed entry is live and the price
    # is still within ARMED_MOVE_ATR x ATR M1 of it, the next M1 bars do not call Claude (the code fills the entry itself);
    # with a position open (fixed SL/TP, not hedged) Claude is called on the first bar, then every OPEN_EVERY_BARS bars;
    # investigations (the group sinking) still call at once
    BASKET_CLAUDE_ARMED_SKIP: bool = True
    BASKET_CLAUDE_ARMED_MOVE_ATR: float = 1.5
    BASKET_CLAUDE_OPEN_EVERY_BARS: int = 3
    # flat, nothing armed and the last decision was "wait": the next call comes on the M5 bar close (every N M1 bars) or
    # as soon as the price moved this many ATR14 M1 from where that decision was taken (04.11: 27-37 flat calls/hour,
    # 1.3 USD/h of API against ~1.5 USD/h of trading; the research consensus is decisions on M5+/events, 1-min logic in
    # code); 0 = call every bar
    BASKET_CLAUDE_FLAT_WAIT_EVERY_BARS: int = 5
    BASKET_CLAUDE_FLAT_WAIT_MOVE_ATR: float = 1.0
    # the "investigation" call on a sinking position is skipped while every CLOSE at a loss would be refused anyway
    # (position younger than BASKET_CLAUDE_LOSS_CLOSE_MIN_MINUTES, hedging off): nothing it answers can be executed
    BASKET_INVESTIGATE_ONLY_WHEN_ACTIONABLE: bool = True
    BASKET_PULLBACK_IMPULSE_ATR: float = 1.0
    # no entry while the last N closed M1 bars all closed in the trade direction (chasing a run; 03-04.11: 6 such
    # entries won 3, -8.33 USD, the other 45 won 38, +27 USD); a PULLBACK waits, a direct OPEN is refused; 0 = off
    BASKET_MAX_RUN_BARS: int = 3
    # a lot above the start lot (Claude's "very_sure" tier) needs its decision confidence at least this high, M5 (EMA9/21)
    # and M15 (EMA20/50) pointing with the entry, and this UTC hour's 7-day record not negative; otherwise the start lot
    # (owner 04.11: bigger volume only on the entries Claude is sure of); 0 = any confidence
    BASKET_SURE_MIN_CONFIDENCE: float = 0.8
    # no entry while the price sits this many ATR14 M1 beyond the EMA50 M1 in the trade direction (an extended move:
    # 04.11 on 53 entries, >= 2 ATR won 12 of 17, -5.57 USD; < 1 ATR won 17 of 18, +21.64); a PULLBACK waits; 0 = off
    BASKET_MAX_STRETCH_EMA50_ATR: float = 2.0
    # no PULLBACK fill / market OPEN until a pullback actually happened: the last closed M1 bar or the last 10 minutes net
    # must have moved AGAINST the trade. 04.11 on 57 real entries (TP 2 / SL 4 / 20-min stop): entries after a counter
    # move won 31 of 35 (+41.6 USD), entries straight after a bar in their own direction won 13 of 22 (-15.4)
    BASKET_ENTRY_NEEDS_PULLBACK: bool = True
    # an armed PULLBACK is dropped (not filled) once the price has run past its level by more than this many ATR14 M1:
    # the level failed and the fill would come inside a reversal. 04.11 on 62 armed fills: past the level by > 0.3 ATR
    # 0 of 4 won (-14.9 USD); within 0.3 ATR 47 of 58 (+26.3); 0 = off
    BASKET_PULLBACK_MAX_OVERSHOOT_ATR: float = 0.3
    # UTC hours with no new entries (and no flat Claude calls): the London/New York overlap. Measured 02-04.11 on the
    # real log: 12:00-15:59 UTC 16 trades, 6 wins, -33.73 USD; the other hours 108 trades, 77 wins; on 04.11 alone
    # 7 trades there, 2 wins, -15.53. Gold's 1-minute range there is 2.2-3.1 USD, so the 4 USD stop sits inside the
    # noise. 06.11, 141 live entries since 02.11: 09:00-11:59 UTC lost on every single day (36 trades, 17 wins, -33.4 USD
    # per 0.01 lot; 03.11 -14.8, 04.11 -8.1, 05.11 -2.4, 06.11 -8.0) under every exit variant simulated, while the
    # other hours won 61 of 84 with the M5 filter; the same hours burned 24% of the Claude spend. Comma-separated hours,
    # empty = off
    BASKET_NO_ENTRY_UTC_HOURS: str = "9,10,11,12,13,14,15"
    # a same-side entry within REENTRY_MINUTES of the previous close must sit at least REENTRY_MIN_ATR x ATR14 M1 away
    # from the previous entry price - a new level, not the same one again. Measured 02-04.11 (126 entries, sim TP 2 /
    # SL 4): re-entries within 0.5 ATR of the last entry won 24 of 36 (-1.1 USD), entries > 1.5 ATR away 19 of 24
    # (+21.2); 04.11 16:14 BUY 4045.5 three minutes after a +1.99 from 4045.4 -> SL. 0 = off
    # 04.11 20:40: with 0.5 a BUY 0.9 USD (0.65 ATR) under the previous entry still went through and hit the SL; the
    # 0.5-1.5 ATR bucket was just as flat (23 of 35, -2.1), only > 1.5 ATR carried the edge -> 1.5
    BASKET_REENTRY_MIN_ATR: float = 1.5
    BASKET_REENTRY_MINUTES: int = 15
    # no new entry (and no flat Claude calls) while ATR14 M1 is at or above this many USD: the fixed 4 USD stop sits
    # inside one bar's noise. 05.11 01:26 UTC: BUY at ATR 6.2 stopped out in 60 s; entries at ATR >= 3.5 won 0 of 2,
    # ATR 2.5-3 won 13 of 15. 0 = off
    BASKET_MAX_ATR_M1: float = 3.5
    # volatility-scaled exits: TP = max(BASKET_SCALP_TP_PRICE, TP_ATR_MULT x ATR14 M1), SL = max(BASKET_SCALP_SL_PRICE,
    # SL_ATR_MULT x ATR14 M1). 05.11 on 135 entries (sim, 20-min stop): ATR < 2 the fixed 2/4 is best (+13.5); ATR 2-3
    # fixed +2.0 vs 1xATR/2xATR +12.0; ATR >= 3 fixed -8.0 vs +14.9 -> the fixed stop sits inside the noise once ATR
    # passes 2 USD. 06.11 re-measured on the real entries (sim, floors 2/4): SL 2xATR vs 2.5xATR - all-rules period
    # (29) -18.7 vs +3.0, since 03.11 (103) +1.7 vs +16.1, with the M5 filter (82) +27.3 vs +48.7; SL 3xATR similar
    # (+7.9 / +25.1 / +41.9). A 30-minute time stop beats 20 on every sample (+9.1 vs +4.1, +20.8 vs +14.7); a
    # break-even stop after +0.5 ATR cuts the winners (46 of 103 instead of 71). 0 = fixed exits only
    # a pullback that went too far is no pullback: no fill while the price is more than MAX_DEPTH x ATR14 M1 beyond the
    # EMA20 M1 against the trade, or within MIN_DIST_EMA50 x ATR of the EMA50 M1 (the move came all the way back to the
    # slow mean = consumed). 05.11 on 152 entries (sim, current exits): the two cases together 27 of 47 (-21.4 USD), the
    # rest 75 of 105 (+64.1); on the all-rules period 4 of 10 (-14.8) vs 10 of 12 (+24.6). 0 = off
    BASKET_PULLBACK_MAX_DEPTH_ATR: float = 0.7
    BASKET_MIN_DIST_EMA50_ATR: float = 0.5
    BASKET_SCALP_TP_ATR_MULT: float = 1.0
    BASKET_SCALP_SL_ATR_MULT: float = 2.5
    # share of the free margin an entry may use; the rest stays free for the SL and the spread (03.11: 0.02 lot on 84 USD
    # left ~4 USD free and the broker cut the lot anyway)
    BASKET_MARGIN_USE_FRAC: float = 0.5
    # the lot of an entry may never put more than this % of the balance at its fixed SL (scalp_sl USD per 0.01 lot):
    # on a 1:2000 broker the margin alone would allow 0.5 lot on 200 USD, where one SL wipes the account (03.11, Exness)
    BASKET_MAX_RISK_PCT_PER_ENTRY: float = 5.0
    BASKET_SCALP_SL_PRICE: float = 4.0
    BASKET_MAX_LOSS_USD: float = 60.0
    BASKET_SL_DISTANCE: float = 30.0
    PAPER_START_BALANCE: float = 10000
    ANTHROPIC_API_KEY: str = ""
    CLAUDE_ENABLED: bool = True
    CLAUDE_MODEL: str = "claude-opus-5-5"
    CLAUDE_TIMEOUT_SECONDS: float = 45
    # reasoning depth of every Claude call (adaptive thinking is always on): low | medium | high | xhigh | max
    CLAUDE_REVIEW_EFFORT: str = "medium"
    # broker server-time DST rule for archive bars (EA v1.12+): auto | EET | NY | fixed | IANA zone name
    MT5_SERVER_TZ: str = "auto"

    @model_validator(mode="after")
    def _apply_profile(self):
        prof = PROFILES.get(str(self.PROFILE or "").strip().lower())
        if prof:
            # the broker's own name for gold (XAUUSD.a, GOLD, XAUUSDm, XAUUSD.cash ...) wins over the profile's "XAUUSD"
            gold = next((s for s in split_symbols(self.TRADE_SYMBOLS) + split_symbols(self.ACTIVE_SYMBOLS)
                         if "XAU" in s.upper() or "GOLD" in s.upper()), None)
            for k, v in prof.items():
                if gold and k in ("ACTIVE_SYMBOLS", "TRADE_SYMBOLS"):
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
        }

    def chart_timeframes(self) -> list[str]:
        return [t for t in split_csv(self.CHART_TIMEFRAMES) if t in TF_SECONDS]

    def secret_values(self) -> list[str]:
        return [s for s in (self.ANTHROPIC_API_KEY, self.MT5_BRIDGE_KEY, self.ADMIN_TOKEN) if s]


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
