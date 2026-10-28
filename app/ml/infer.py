"""Live scoring helpers (pure functions + a small sha-verified cache). Nothing here places, closes or sizes orders:
the engine decides what to do with a score, and only for an APPROVED model (shadow models are logged only).

score_entry(bundle, features, symbol) -> P(R_net > 0) of an fs-v1 feature dict.
score_exit(bundle, state_features) -> predicted remaining R (xs-v1) if the position is held.
entry_action(bundle, p, size_min, size_max) -> {"veto", "size_factor"}: veto when p < the approved threshold,
  otherwise a risk factor in [size_min, size_max] rising linearly from the threshold to the validation P90.
exit_action(bundle, remaining_r) -> True when remaining R < -exit_margin (close early, reason ML_EXIT).
"""
from __future__ import annotations

import threading
import time

import numpy as np

from . import features as F
from . import registry

SIZE_MIN = 0.5
SIZE_MAX_CAP = 2.0


class ModelCache:
    """Bundles by model id, re-verified when the registry row's sha256 changes."""

    def __init__(self):
        self._lock = threading.Lock()
        self._items: dict[str, tuple[str, dict]] = {}

    def get(self, db, model_id: str) -> dict:
        row = registry.get(db, model_id)
        if row is None:
            raise registry.RegistryError(f"model inexistent: {model_id}")
        with self._lock:
            hit = self._items.get(model_id)
            if hit and hit[0] == row["sha256"]:
                return hit[1]
        bundle = registry.load(db, model_id)
        with self._lock:
            self._items[model_id] = (row["sha256"], bundle)
        return bundle

    def active(self, db, kind: str) -> list[tuple[dict, dict]]:
        """[(registry row, bundle)] for the shadow/approved models of a kind; models that fail to load are skipped
        (returned in .errors)."""
        out, self.errors = [], []
        for row in registry.active(db, kind):
            try:
                out.append((row, self.get(db, row["id"])))
            except Exception as e:  # missing/tampered artifact: never scored
                self.errors.append((row["id"], repr(e)))
        return out

    def clear(self) -> None:
        with self._lock:
            self._items.clear()


def _check_schema(bundle: dict, kind: str) -> None:
    want = F.ENTRY_SCHEMA if kind == "entry" else F.EXIT_SCHEMA
    if bundle.get("kind") != kind or bundle.get("feature_schema") != want:
        raise ValueError(f"model {bundle.get('_model_id')} nu este un model {kind} {want}")


def score_entry(bundle: dict, features: dict, symbol: str | None = None) -> float:
    _check_schema(bundle, "entry")
    te = bundle.get("sym_te") or {"map": {}, "prior": 0.5}
    x = F.vector(features, bundle["feature_names"]) + [te["map"].get(str(symbol or "").upper(), te["prior"])]
    return float(bundle["model"].predict_proba(np.asarray([x], dtype=float))[0, 1])


def score_exit(bundle: dict, state: dict) -> float:
    _check_schema(bundle, "exit")
    x = F.vector(state, bundle["feature_names"])
    return float(bundle["model"].predict(np.asarray([x], dtype=float))[0])


def entry_action(bundle: dict, p: float, size_min: float = SIZE_MIN, size_max: float = 1.0) -> dict:
    thr = float(bundle.get("threshold") or 0.0)
    lo = max(0.0, min(float(size_min), SIZE_MAX_CAP))
    hi = max(lo, min(float(size_max), SIZE_MAX_CAP))
    if p < thr:
        return {"veto": True, "size_factor": 0.0, "threshold": thr}
    p_hi = max(float(bundle.get("p_hi") or thr), thr)
    frac = 1.0 if p_hi <= thr else min(1.0, max(0.0, (p - thr) / (p_hi - thr)))
    return {"veto": False, "size_factor": round(lo + (hi - lo) * frac, 4), "threshold": thr}


def exit_action(bundle: dict, remaining_r: float) -> bool:
    m = bundle.get("exit_margin")
    return m is not None and remaining_r < -float(m)


def log_decision(db, model_id: str, kind: str, symbol: str | None, ref_id: str | None, features_hash: str | None,
                 score: float | None, would_action: str | None, actual_action: str | None = None,
                 ts: float | None = None) -> None:
    db.execute("INSERT INTO ml_decisions(model_id, ts, kind, symbol, ref_id, features_hash, score, would_action, "
               "actual_action) VALUES(?,?,?,?,?,?,?,?,?)",
               (model_id, float(ts if ts is not None else time.time()), kind, symbol, ref_id, features_hash,
                None if score is None else float(score), would_action, actual_action))
