"""Strategy plugin registry (Stage C research).

Each plugin module exposes NAME, VERSION, DESCRIPTION_RO, PARAMS (defaults) and
analyze(symbol, sig_bars, htf_bars, quote, cfg, risk, ctx) with the exact output shape of app.strategy.analyze.
Plugins read their own keys with cfg.get(key, PARAMS[key]), so a bare live cfg works too.
Config precedence (merged_cfg): PARAMS < live strategy settings < per-run overrides — the live values always
win over plugin defaults, so trend-v1 run through the registry sees exactly the live settings."""
from __future__ import annotations

from types import ModuleType

from . import breakout_v1, meanrev_v1, pullback_v1, trend_v1

DEFAULT = "trend-v1"
_REGISTRY: dict[str, ModuleType] = {m.NAME: m for m in (trend_v1, meanrev_v1, breakout_v1, pullback_v1)}


def names() -> list[str]:
    return list(_REGISTRY)


def get(name: str | None) -> ModuleType:
    key = name or DEFAULT
    if key not in _REGISTRY:
        raise KeyError(f"Strategie necunoscută: {key} (disponibile: {', '.join(_REGISTRY)})")
    return _REGISTRY[key]


def merged_cfg(name: str | None, live: dict | None, overrides: dict | None = None) -> dict:
    return {**get(name).PARAMS, **(live or {}), **(overrides or {})}


def describe() -> list[dict]:
    """[{name, version, description, params}] for GET /api/v1/strategies."""
    return [{"name": m.NAME, "version": m.VERSION, "description": m.DESCRIPTION_RO, "params": dict(m.PARAMS)}
            for m in _REGISTRY.values()]


__all__ = ["DEFAULT", "names", "get", "merged_cfg", "describe"]
