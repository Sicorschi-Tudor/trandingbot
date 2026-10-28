"""Training + out-of-sample evaluation of the entry and exit models (offline; never touches live trading).

Splits are TIME-ORDERED only. Per walk-forward fold the period before the test block is cut into
  train | calibration | validation | (test)
with PURGE (a sample whose label ends at/after the next segment's start is dropped from the earlier segment) and an
EMBARGO (the first `embargo_s` seconds of every later segment are dropped). Exit rows are assigned to segments by
their trade (entry time / exit time), so a trade's bars never straddle two segments.

Entry model (fs-v1): baseline LogisticRegression (impute + scale) and HistGradientBoostingClassifier for P(R_net>0),
the latter calibrated (isotonic) on the calibration segment with the trained model frozen (= cv='prefit').
Threshold chosen on the validation segment (max avg R with ≥ 25 % coverage; 0 = take all). OOS metrics on the test
blocks: Brier, log loss, AUC, calibration bins + ECE, and the trading policy (take only P > threshold): n, avg R,
CI90, PF vs the baseline policy take-all.
Exit model (xs-v1): HistGradientBoostingRegressor for the remaining R if held; policy = close at the first bar where
the prediction < -exit_margin (margin chosen on validation); OOS avg R, CI90, PF vs the base exits.
Symbol is target-encoded from the TRAIN segment only (smoothed share of winners).
"""
from __future__ import annotations

import math
import time

import numpy as np

from .. import backtest
from . import features as F

SEED = 7
TE_SMOOTH = 50.0
MIN_TRAIN = 200
THRESH_MIN_FRAC = 0.25
THRESH_MIN_N = 30
MARGINS = (0.0, 0.05, 0.1, 0.15, 0.2, 0.3, 0.5, 0.75, 1.0)
ECE_MAX = 0.05
CI_LOWER_MIN = -0.02
EXIT_CALIB_MAX = 0.05  # same thresholds as the pre-registered Stage D gate and the live promotion checklist
BEAT_BASELINE_BY = 0.05
MIN_TEST_N = 150
MAX_THREADS = 3  # CPU budget (the live bot runs on the same machine)
HGB_PARAMS = {"max_iter": 300, "learning_rate": 0.05, "max_leaf_nodes": 15, "min_samples_leaf": 40,
              "l2_regularization": 1.0, "early_stopping": False}


# ---- splits ---------------------------------------------------------------------

def purged_segments(t, t_end, bounds: list[tuple[float, float]], embargo_s: float = 0.0) -> list[np.ndarray]:
    """Indices per time segment bounds[k] = [start, end). Embargo drops the first embargo_s seconds of every
    segment after the first; purge drops samples whose label end (t_end) reaches the next segment's start."""
    t = np.asarray(t, dtype=float)
    t_end = np.asarray(t_end, dtype=float)
    out = []
    for k, (a, b) in enumerate(bounds):
        lo = a + (embargo_s if k > 0 else 0.0)
        m = (t >= lo) & (t < b)
        if k + 1 < len(bounds):
            m &= t_end < bounds[k + 1][0]
        out.append(np.flatnonzero(m))
    return out


def walk_forward(t, t_end, n_folds: int = 4, init_frac: float = 0.5, cal_frac: float = 0.15,
                 val_frac: float = 0.15, embargo_s: float = 86400.0) -> list[dict]:
    """Expanding-window folds: the last (1 - init_frac) of the period is cut into n_folds test blocks; before each
    block: train | cal | val (cal_frac and val_frac of that pre-period). Returns [{train, cal, val, test, bounds}]."""
    t = np.asarray(t, dtype=float)
    if not len(t):
        return []
    t0, t1 = float(t.min()), float(t.max()) + 1
    span = t1 - t0
    edges = [t0 + span * (init_frac + (1 - init_frac) * k / n_folds) for k in range(n_folds + 1)]
    folds = []
    for k in range(n_folds):
        a, b = edges[k], edges[k + 1]
        pre = a - t0
        c0 = t0 + pre * (1 - cal_frac - val_frac)
        v0 = t0 + pre * (1 - val_frac)
        bounds = [(t0, c0), (c0, v0), (v0, a), (a, b)]
        tr, ca, va, te = purged_segments(t, t_end, bounds, embargo_s)
        folds.append({"train": tr, "cal": ca, "val": va, "test": te, "bounds": bounds})
    return folds


def final_split(t, t_end, cal_frac: float = 0.15, val_frac: float = 0.15, embargo_s: float = 86400.0) -> dict:
    """Split of the whole period for the model that is saved: train | cal | val (the latest data)."""
    t = np.asarray(t, dtype=float)
    t0, t1 = float(t.min()), float(t.max()) + 1
    span = t1 - t0
    bounds = [(t0, t0 + span * (1 - cal_frac - val_frac)), (t0 + span * (1 - cal_frac - val_frac), t0 + span * (1 - val_frac)),
              (t0 + span * (1 - val_frac), t1)]
    tr, ca, va = purged_segments(t, t_end, bounds, embargo_s)
    return {"train": tr, "cal": ca, "val": va, "bounds": bounds}


# ---- metrics ----------------------------------------------------------------------

def policy_stats(r) -> dict:
    r = [float(x) for x in r]
    n = len(r)
    if not n:
        return {"n": 0, "avg_r": None, "ci90": [None, None], "pf": None, "net_r": 0.0, "win_rate": None}
    gp = math.fsum(x for x in r if x > 0)
    gl = -math.fsum(x for x in r if x < 0)
    return {"n": n, "avg_r": round(math.fsum(r) / n, 4), "ci90": backtest.bootstrap_ci(r),
            "pf": round(gp / gl, 3) if gl > 0 else None, "net_r": round(math.fsum(r), 3),
            "win_rate": round(sum(1 for x in r if x > 0) / n, 4)}


def calibration_bins(p, y, n_bins: int = 10) -> tuple[list[dict], float | None]:
    p, y = np.asarray(p, dtype=float), np.asarray(y, dtype=float)
    if not len(p):
        return [], None
    bins, ece = [], 0.0
    edges = np.linspace(0, 1, n_bins + 1)
    for k in range(n_bins):
        m = (p >= edges[k]) & ((p < edges[k + 1]) if k < n_bins - 1 else (p <= 1))
        if m.sum() == 0:
            continue
        mp, fy = float(p[m].mean()), float(y[m].mean())
        bins.append({"lo": round(float(edges[k]), 2), "hi": round(float(edges[k + 1]), 2), "n": int(m.sum()),
                     "mean_p": round(mp, 4), "frac_pos": round(fy, 4)})
        ece += m.sum() / len(p) * abs(mp - fy)
    return bins, round(float(ece), 4)


def classif_metrics(p, y) -> dict:
    from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score
    p = np.clip(np.asarray(p, dtype=float), 1e-6, 1 - 1e-6)
    y = np.asarray(y, dtype=int)
    if not len(p):
        return {"n": 0}
    bins, ece = calibration_bins(p, y)
    out = {"n": int(len(p)), "base_rate": round(float(y.mean()), 4), "mean_p": round(float(p.mean()), 4),
           "brier": round(float(brier_score_loss(y, p)), 5), "log_loss": round(float(log_loss(y, p, labels=[0, 1])), 5),
           "auc": round(float(roc_auc_score(y, p)), 4) if 0 < y.sum() < len(y) else None,
           "ece": ece, "calibration": bins}
    out["brier_climatology"] = round(float(np.mean((y.mean() - y) ** 2)), 5)
    return out


# ---- symbol encoding ---------------------------------------------------------------

def fit_symbol_te(symbols, y, smooth: float = TE_SMOOTH) -> dict:
    symbols = np.asarray(symbols)
    y = np.asarray(y, dtype=float)
    prior = float(y.mean()) if len(y) else 0.5
    te = {}
    for s in sorted(set(symbols.tolist())):
        m = symbols == s
        te[str(s)] = round((float(y[m].sum()) + prior * smooth) / (float(m.sum()) + smooth), 6)
    return {"map": te, "prior": round(prior, 6)}


def apply_symbol_te(te: dict, symbols) -> np.ndarray:
    return np.asarray([te["map"].get(str(s), te["prior"]) for s in symbols], dtype=float)


# ---- entry -------------------------------------------------------------------------------

def _hgb_clf(seed: int):
    from sklearn.ensemble import HistGradientBoostingClassifier
    return HistGradientBoostingClassifier(random_state=seed, **HGB_PARAMS)


def _lr(seed: int):
    from sklearn.impute import SimpleImputer
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    return make_pipeline(SimpleImputer(strategy="median", keep_empty_features=True), StandardScaler(),
                         LogisticRegression(C=0.5, max_iter=2000, random_state=seed))


def calibrate_prefit(model, X_cal, y_cal):
    """Isotonic calibration of an already fitted classifier on a separate (later) fold."""
    from sklearn.calibration import CalibratedClassifierCV
    try:
        from sklearn.frozen import FrozenEstimator
        cal = CalibratedClassifierCV(FrozenEstimator(model), method="isotonic")
    except ImportError:  # scikit-learn < 1.6
        cal = CalibratedClassifierCV(model, method="isotonic", cv="prefit")
    cal.fit(X_cal, y_cal)
    return cal


def choose_threshold(p, r, min_frac: float = THRESH_MIN_FRAC, min_n: int = THRESH_MIN_N) -> float:
    """Threshold on P maximizing avg R on validation with enough coverage; 0.0 = take all (ties -> lower)."""
    p, r = np.asarray(p, dtype=float), np.asarray(r, dtype=float)
    if not len(p):
        return 0.0
    need = max(min_n, int(math.ceil(min_frac * len(p))))
    best_thr, best = 0.0, float(r.mean())
    for q in np.arange(0.05, 0.80, 0.05):
        thr = float(np.quantile(p, q))
        m = p > thr
        if m.sum() < need:
            continue
        v = float(r[m].mean())
        if v > best + 1e-9:
            best_thr, best = round(thr, 6), v
    return best_thr


def _entry_X(ds: dict, idx, te: dict) -> np.ndarray:
    return np.column_stack([ds["X"][idx], apply_symbol_te(te, ds["symbol"][idx])])


def fit_entry(ds: dict, tr, ca, va, seed: int = SEED) -> dict:
    y = (np.asarray(ds["r"], dtype=float) > 0).astype(int)
    if len(tr) < MIN_TRAIN or len(set(y[tr].tolist())) < 2 or len(ca) < 20 or len(set(y[ca].tolist())) < 2:
        raise ValueError(f"date insuficiente pentru modelul de intrare (train {len(tr)}, cal {len(ca)})")
    te = fit_symbol_te(ds["symbol"][tr], y[tr])
    Xtr, Xca = _entry_X(ds, tr, te), _entry_X(ds, ca, te)
    hgb = _hgb_clf(seed).fit(Xtr, y[tr])
    cal = calibrate_prefit(hgb, Xca, y[ca])
    lr = _lr(seed).fit(Xtr, y[tr])
    thr, p_hi = 0.0, 1.0
    if len(va):
        p_va = cal.predict_proba(_entry_X(ds, va, te))[:, 1]
        thr = choose_threshold(p_va, ds["r"][va])
        p_hi = float(np.quantile(p_va, 0.9))
    return {"model": cal, "baseline": lr, "te": te, "threshold": thr, "p_hi": round(max(p_hi, thr), 6)}


def evaluate_entry(ds: dict, n_folds: int = 4, seed: int = SEED, embargo_s: float = 86400.0) -> dict:
    r = np.asarray(ds["r"], dtype=float)
    y = (r > 0).astype(int)
    folds = walk_forward(ds["t"], ds["t_end"], n_folds=n_folds, embargo_s=embargo_s)
    oos = {"p": [], "p_lr": [], "y": [], "r": [], "take": [], "take_lr": [], "taken_bt": []}
    per_fold = []
    for k, f in enumerate(folds):
        if not len(f["test"]):
            continue
        try:
            m = fit_entry(ds, f["train"], f["cal"], f["val"], seed)
        except ValueError as e:
            per_fold.append({"fold": k, "skipped": str(e)})
            continue
        Xte = _entry_X(ds, f["test"], m["te"])
        p = m["model"].predict_proba(Xte)[:, 1]
        p_lr = m["baseline"].predict_proba(Xte)[:, 1]
        thr_lr = choose_threshold(m["baseline"].predict_proba(_entry_X(ds, f["val"], m["te"]))[:, 1], r[f["val"]]) \
            if len(f["val"]) else 0.0
        take, take_lr = p > m["threshold"], p_lr > thr_lr
        rt = r[f["test"]]
        per_fold.append({"fold": k, "n_train": int(len(f["train"])), "n_cal": int(len(f["cal"])),
                         "n_val": int(len(f["val"])), "n_test": int(len(f["test"])), "threshold": m["threshold"],
                         "test_from": int(f["bounds"][3][0]), "test_to": int(f["bounds"][3][1]),
                         "policy": policy_stats(rt[take]), "baseline": policy_stats(rt)})
        oos["p"] += p.tolist()
        oos["p_lr"] += p_lr.tolist()
        oos["y"] += y[f["test"]].tolist()
        oos["r"] += rt.tolist()
        oos["take"] += take.tolist()
        oos["take_lr"] += take_lr.tolist()
        oos["taken_bt"] += np.asarray(ds["taken"])[f["test"]].astype(bool).tolist()
    rr = np.asarray(oos["r"])
    take = np.asarray(oos["take"], dtype=bool)
    take_lr = np.asarray(oos["take_lr"], dtype=bool)
    tbt = np.asarray(oos["taken_bt"], dtype=bool)
    pol, base = policy_stats(rr[take]) if len(rr) else policy_stats([]), policy_stats(rr)
    clf = classif_metrics(oos["p"], oos["y"])
    out = {"oos": {"model": clf, "baseline_lr": classif_metrics(oos["p_lr"], oos["y"]),
                   "policy": pol, "baseline_take_all": base,
                   "policy_lr": policy_stats(rr[take_lr]) if len(rr) else policy_stats([]),
                   "taken_by_backtest": {"policy": policy_stats(rr[tbt & take]) if len(rr) else policy_stats([]),
                                         "baseline": policy_stats(rr[tbt]) if len(rr) else policy_stats([])},
                   "coverage": round(float(take.mean()), 4) if len(take) else None},
           "folds": per_fold}
    out["checklist"] = checklist(pol, base, clf.get("ece"), ECE_MAX)
    return out


def checklist(pol: dict, base: dict, calib_err, calib_max: float) -> dict:
    avg, lo = pol.get("avg_r"), (pol.get("ci90") or [None])[0]
    items = {
        "oos_avg_r_pos": avg is not None and avg > 0,
        "ci_lower_ok": lo is not None and lo > CI_LOWER_MIN,
        "beats_baseline": avg is not None and base.get("avg_r") is not None and avg - base["avg_r"] > BEAT_BASELINE_BY,
        "calibration_ok": calib_err is not None and calib_err < calib_max,
    }
    items["passed"] = all(items.values()) and (pol.get("n") or 0) >= MIN_TEST_N
    return items


# ---- exit ----------------------------------------------------------------------------------

def _hgb_reg(seed: int):
    from sklearn.ensemble import HistGradientBoostingRegressor
    return HistGradientBoostingRegressor(random_state=seed, **HGB_PARAMS)


def _trade_groups(ds: dict, idx) -> dict[str, np.ndarray]:
    """trade_id -> row indices (time order) among idx."""
    g: dict[str, list[int]] = {}
    for i in idx:
        g.setdefault(str(ds["trade_id"][i]), []).append(int(i))
    return {k: np.asarray(sorted(v, key=lambda j: ds["t"][j])) for k, v in g.items()}


def exit_policy_r(ds: dict, idx, pred, margin: float) -> tuple[list[float], list[float]]:
    """(policy R per trade, base R per trade) on the trades of idx: close at the first bar with pred < -margin
    (realized = close-now R net = final_r - target), else the trade's own exit (final_r)."""
    pmap = {int(i): float(v) for i, v in zip(idx, pred)}
    pol, base = [], []
    for _, rows in _trade_groups(ds, idx).items():
        fr = float(ds["final_r"][rows[0]])
        base.append(fr)
        got = fr
        for i in rows:
            if pmap[int(i)] < -margin:
                got = fr - float(ds["target"][i])
                break
        pol.append(got)
    return pol, base


def choose_margin(ds: dict, idx, pred) -> float:
    """Margin maximizing avg R per trade on validation; ties -> the larger margin (fewer early exits)."""
    best_m, best = math.inf, None
    for m in sorted(MARGINS, reverse=True):
        pol, _ = exit_policy_r(ds, idx, pred, m)
        if not pol:
            return math.inf
        v = float(np.mean(pol))
        if best is None or v > best + 1e-9:
            best_m, best = m, v
    _, base = exit_policy_r(ds, idx, pred, math.inf)
    return best_m if best is not None and best > float(np.mean(base)) + 1e-9 else math.inf


def regress_metrics(pred, y) -> dict:
    pred, y = np.asarray(pred, dtype=float), np.asarray(y, dtype=float)
    if not len(y):
        return {"n": 0}
    err = pred - y
    ss_tot = float(((y - y.mean()) ** 2).sum())
    bins = []
    calib = 0.0
    qs = np.quantile(pred, np.linspace(0, 1, 11))
    for k in range(10):
        m = (pred >= qs[k]) & ((pred < qs[k + 1]) if k < 9 else (pred <= qs[k + 1]))
        if not m.any():
            continue
        mp, my = float(pred[m].mean()), float(y[m].mean())
        bins.append({"n": int(m.sum()), "mean_pred": round(mp, 4), "mean_target": round(my, 4)})
        calib += m.sum() / len(y) * abs(mp - my)
    corr = float(np.corrcoef(pred, y)[0, 1]) if len(y) > 2 and pred.std() > 0 and y.std() > 0 else None
    return {"n": int(len(y)), "mae": round(float(np.abs(err).mean()), 5), "rmse": round(float(np.sqrt((err ** 2).mean())), 5),
            "r2": round(1 - float((err ** 2).sum()) / ss_tot, 5) if ss_tot > 0 else None,
            "corr": None if corr is None else round(corr, 4), "calib_mae": round(float(calib), 4), "calibration": bins}


def fit_exit(ds: dict, tr, va, seed: int = SEED) -> dict:
    if len(tr) < MIN_TRAIN:
        raise ValueError(f"date insuficiente pentru modelul de ieșire (train {len(tr)})")
    reg = _hgb_reg(seed).fit(ds["X"][tr], np.asarray(ds["target"], dtype=float)[tr])
    margin = choose_margin(ds, va, reg.predict(ds["X"][va])) if len(va) else math.inf
    return {"model": reg, "margin": margin}


def evaluate_exit(ds: dict, n_folds: int = 4, seed: int = SEED, embargo_s: float = 86400.0) -> dict:
    # the calibration segment is merged into training (a regressor needs no probability calibration)
    folds = walk_forward(ds["t_entry"], ds["t_end"], n_folds=n_folds, embargo_s=embargo_s)
    pol_all, base_all, pred_all, y_all, per_fold = [], [], [], [], []
    tgt = np.asarray(ds["target"], dtype=float)
    for k, f in enumerate(folds):
        if not len(f["test"]):
            continue
        tr = np.concatenate([f["train"], f["cal"]])
        try:
            m = fit_exit(ds, tr, f["val"], seed)
        except ValueError as e:
            per_fold.append({"fold": k, "skipped": str(e)})
            continue
        pred = m["model"].predict(ds["X"][f["test"]])
        pol, base = exit_policy_r(ds, f["test"], pred, m["margin"])
        per_fold.append({"fold": k, "n_train_rows": int(len(tr)), "n_test_rows": int(len(f["test"])),
                         "margin": None if math.isinf(m["margin"]) else m["margin"],
                         "test_from": int(f["bounds"][3][0]), "test_to": int(f["bounds"][3][1]),
                         "policy": policy_stats(pol), "baseline": policy_stats(base)})
        pol_all += pol
        base_all += base
        pred_all += pred.tolist()
        y_all += tgt[f["test"]].tolist()
    pol, base = policy_stats(pol_all), policy_stats(base_all)
    reg = regress_metrics(pred_all, y_all)
    out = {"oos": {"model": reg, "policy": pol, "baseline_base_exits": base}, "folds": per_fold}
    out["checklist"] = checklist(pol, base, reg.get("calib_mae"), EXIT_CALIB_MAX)
    return out


# ---- top level --------------------------------------------------------------------------------

def _limit_threads():
    try:
        from threadpoolctl import threadpool_limits
        return threadpool_limits(limits=MAX_THREADS)
    except Exception:  # threadpoolctl missing: OMP_NUM_THREADS (set by the CLI) still applies
        import contextlib
        return contextlib.nullcontext()


def train_entry(ds: dict, n_folds: int = 4, seed: int = SEED, embargo_s: float = 86400.0) -> tuple[dict, dict]:
    """(bundle to save, metrics) — metrics from walk-forward OOS, bundle fitted on the whole period."""
    with _limit_threads():
        return _train_entry(ds, n_folds, seed, embargo_s)


def _train_entry(ds: dict, n_folds: int, seed: int, embargo_s: float) -> tuple[dict, dict]:
    metrics = evaluate_entry(ds, n_folds, seed, embargo_s)
    sp = final_split(ds["t"], ds["t_end"], embargo_s=embargo_s)
    m = fit_entry(ds, sp["train"], sp["cal"], sp["val"], seed)
    used = np.concatenate([sp["train"], sp["cal"], sp["val"]])
    bundle = {"kind": "entry", "feature_schema": F.ENTRY_SCHEMA, "feature_names": list(ds["feature_names"]),
              "model_inputs": list(ds["feature_names"]) + ["sym_te"], "model": m["model"], "baseline": m["baseline"],
              "sym_te": m["te"], "threshold": m["threshold"], "p_hi": m["p_hi"],
              "trained_until": int(np.max(np.asarray(ds["t_end"])[used])) if len(used) else None,
              "sklearn": _sk_version(), "seed": seed, "created_ts": time.time()}
    metrics["final"] = {"n_train": int(len(sp["train"])), "n_cal": int(len(sp["cal"])), "n_val": int(len(sp["val"])),
                        "threshold": m["threshold"], "p_hi": m["p_hi"]}
    return bundle, metrics


def train_exit(ds: dict, n_folds: int = 4, seed: int = SEED, embargo_s: float = 86400.0) -> tuple[dict, dict]:
    with _limit_threads():
        return _train_exit(ds, n_folds, seed, embargo_s)


def _train_exit(ds: dict, n_folds: int, seed: int, embargo_s: float) -> tuple[dict, dict]:
    metrics = evaluate_exit(ds, n_folds, seed, embargo_s)
    sp = final_split(ds["t_entry"], ds["t_end"], embargo_s=embargo_s)
    tr = np.concatenate([sp["train"], sp["cal"]])
    m = fit_exit(ds, tr, sp["val"], seed)
    used = np.concatenate([tr, sp["val"]])
    margin = None if math.isinf(m["margin"]) else m["margin"]
    bundle = {"kind": "exit", "feature_schema": F.EXIT_SCHEMA, "feature_names": list(ds["feature_names"]),
              "model_inputs": list(ds["feature_names"]), "model": m["model"], "exit_margin": margin,
              "trained_until": int(np.max(np.asarray(ds["t_end"])[used])) if len(used) else None,
              "sklearn": _sk_version(), "seed": seed, "created_ts": time.time()}
    metrics["final"] = {"n_train_rows": int(len(tr)), "n_val_rows": int(len(sp["val"])), "exit_margin": margin}
    return bundle, metrics


def _sk_version() -> str:
    import sklearn
    return sklearn.__version__
