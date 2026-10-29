"""disc-f5-ml: Stage F / F5 research wrapper — a base strategy plugin filtered by a saved fs-v1 entry model.

analyze() runs the base plugin (cfg['ml_base'], default trend-v1) on the same closed bars / quote / cfg, and when it
produces a candidate, scores it with the fs-v1 features (app.ml.features.entry_features on the closed bars it was
given, the quote's spread and the candidate's own levels — exactly what app.ml.dataset computes for the same bar) and
the bundle at cfg['ml_bundle'] (joblib: {model, sym_te, feature_names, threshold}, written by research/f5_ml.py).
P <= threshold (cfg['ml_threshold'] overrides the bundle's) adds a MARKET blocker to the candidate, so the backtest
does not trade it; the base direction is never changed, only vetoed. indicators['ml_p'] carries the score.

No lookahead: the direction comes from the base plugin on closed bars; the model sees only the closed bars, the
current spread and the candidate levels (all available live at the decision). Research only (discovery package: never
offered to the live engine / UI)."""
from __future__ import annotations

from .. import _common as C
from .. import get as get_plugin
from ...ml import features as F
from ...ml import infer

NAME = "disc-f5-ml"
VERSION = "1.0"
DESCRIPTION_RO = ("Cercetare F5: strategia de bază (ml_base) filtrată de modelul de intrare fs-v1 salvat în ml_bundle; "
                  "candidații cu P(R>0) ≤ prag sunt blocați (blocaj de piață), direcția nu se schimbă.")
PARAMS = {"ml_base": "trend-v1", "ml_bundle": "", "ml_threshold": None}

_BUNDLES: dict[str, dict] = {}


def load_bundle(path: str) -> dict:
    b = _BUNDLES.get(path)
    if b is None:
        import joblib
        b = joblib.load(path)
        if b.get("kind") != "entry" or b.get("feature_schema") != F.ENTRY_SCHEMA:
            raise ValueError(f"{path}: nu este un bundle de intrare {F.ENTRY_SCHEMA}")
        _BUNDLES[path] = b
    return b


def analyze(symbol, sig_bars, htf_bars, quote, cfg, risk, ctx) -> dict:
    base = str(C.p(cfg, PARAMS, "ml_base"))
    a = get_plugin(base).analyze(symbol, sig_bars, htf_bars, quote, cfg, risk, ctx)
    cand = a.get("candidate")
    path = C.p(cfg, PARAMS, "ml_bundle")
    if not cand or not path:
        return a
    bundle = load_bundle(str(path))
    feats = F.entry_features(sig_bars, htf_bars, quote,
                             {"side": cand["side"], "entry": cand["entry"], "sl": cand["stop_loss"],
                              "tp": cand["take_profit"], "score": cand["score"]}, bar_sec=C.tf_seconds(cfg))
    p = infer.score_entry(bundle, feats, symbol)
    thr = C.p(cfg, PARAMS, "ml_threshold")
    thr = float(bundle.get("threshold") or 0.0) if thr is None else float(thr)
    a["indicators"]["ml_p"] = round(p, 4)
    a["indicators"]["ml_threshold"] = round(thr, 4)
    if p > thr:
        return a
    blocker = f"ML fs-v1: P(R>0) {p:.3f} ≤ prag {thr:.3f}."
    cand["blockers"] = list(cand.get("blockers") or []) + [blocker]
    if a["direction"] != "HOLD":
        a["direction"] = "HOLD"
        a["entry"] = a["stop_loss"] = a["take_profit"] = a["rr"] = None
        a["reasons"].insert(0, f"Setup {cand['side']} detectat, dar blocat de filtrul ML:")
    a["reasons"].append(blocker)
    return a
