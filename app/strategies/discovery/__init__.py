"""Discovery research plugins (Stage D discovery): one module per hypothesis, file disc_<id>.py, NAME 'disc-<id>'.

Research only: never listed by app.strategies.names() / research_names() (live engine, UI and API never offer them).
Modules are found automatically (every disc_*.py in this package), so concurrent builders only add files.
discovery_names() lists them; get_discovery(name) returns the module (KeyError when unknown).
app.strategies.get() falls back to get_discovery() so the backtest resolves them by name."""
from __future__ import annotations

import importlib
import pkgutil
from types import ModuleType

_CACHE: dict[str, ModuleType] = {}
_ERRORS: dict[str, str] = {}  # module -> import error (a half-written file of another builder must not break the rest)


def _load() -> dict[str, ModuleType]:
    for info in pkgutil.iter_modules(__path__):
        if not info.name.startswith("disc_") or info.name in _CACHE:
            continue
        try:
            mod = importlib.import_module(f"{__name__}.{info.name}")
        except Exception as e:  # noqa: BLE001
            _ERRORS[info.name] = repr(e)
            continue
        _ERRORS.pop(info.name, None)
        name = getattr(mod, "NAME", None)
        if name:
            _CACHE[info.name] = mod
    return {m.NAME: m for m in _CACHE.values()}


def discovery_names() -> list[str]:
    return sorted(_load())


def get_discovery(name: str) -> ModuleType:
    mod = _load().get(name)
    if mod is None:
        extra = f" (module cu erori de import: {_ERRORS})" if _ERRORS else ""
        raise KeyError(f"Strategie discovery necunoscută: {name}{extra}")
    return mod


__all__ = ["discovery_names", "get_discovery"]
