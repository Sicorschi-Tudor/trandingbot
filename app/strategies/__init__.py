"""Strategy plugin registry (Stage C research).

Each plugin module exposes NAME, VERSION, DESCRIPTION_RO, PARAMS (defaults) and
analyze(symbol, sig_bars, htf_bars, quote, cfg, risk, ctx) with the exact output shape of app.strategy.analyze.
Plugins read their own keys with cfg.get(key, PARAMS[key]), so a bare live cfg works too.
Config precedence (merged_cfg): PARAMS < live strategy settings < per-run overrides — the live values always
win over plugin defaults, so trend-v1 run through the registry sees exactly the live settings.

Stage C2 research plugins (donchian-v1, ma-trend-v1, tsmom-v1; higher timeframes, long holds) are registered for
get()/merged_cfg()/describe() and the backtest, but names() lists them only with include_research=True: the live
engine and the UI keep offering just the intraday set (they run on the live signal timeframe and session, which
these plugins are not built for). Optional module hooks used by app.backtest: RUN_DEFAULTS (backtest-only
defaults), default_exit(cfg) (exit policy when the run gives none) and bias(symbol, sig_bars, htf_bars, cfg)
(direction on closed bars, for the 'signal_flip' exit)."""
from __future__ import annotations

from types import ModuleType

from . import breakout_v1, donchian_v1, ma_trend_v1, meanrev_v1, pullback_v1, trend_v1, tsmom_v1

DEFAULT = "trend-v1"
_REGISTRY: dict[str, ModuleType] = {m.NAME: m for m in (trend_v1, meanrev_v1, breakout_v1, pullback_v1)}
_RESEARCH: dict[str, ModuleType] = {m.NAME: m for m in (donchian_v1, ma_trend_v1, tsmom_v1)}


def names(include_research: bool = False) -> list[str]:
    return list(_REGISTRY) + (list(_RESEARCH) if include_research else [])


def research_names() -> list[str]:
    return list(_RESEARCH)


def get(name: str | None) -> ModuleType:
    key = name or DEFAULT
    mod = _REGISTRY.get(key) or _RESEARCH.get(key)
    if mod is None:
        raise KeyError(f"Strategie necunoscută: {key} (disponibile: {', '.join(names(True))})")
    return mod


def merged_cfg(name: str | None, live: dict | None, overrides: dict | None = None) -> dict:
    return {**get(name).PARAMS, **(live or {}), **(overrides or {})}


def describe() -> list[dict]:
    """[{name, version, description, params}] for GET /api/v1/strategies."""
    return [{"name": m.NAME, "version": m.VERSION, "description": m.DESCRIPTION_RO, "params": dict(m.PARAMS)}
            for m in (*_REGISTRY.values(), *_RESEARCH.values())]


__all__ = ["DEFAULT", "names", "research_names", "get", "merged_cfg", "describe"]
