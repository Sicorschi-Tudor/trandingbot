"""Stage F / family F5 — ML meta-labeling (fs-v1 entry filter, xs-v1 exit model) on the research DBs with the
pre-registered periods (TRAIN 2013-06-01 -> 2019-01-01, VALID 2019-01-01 -> 2021-01-01; TEST never touched).

The app.ml CLI uses --days relative to the real clock, so it cannot see the 2012-2022 research data; this script calls
app.ml.dataset.build with explicit params and app.ml.train's functions with TIME-CUT folds:

  build  : one dataset (entry + exit rows) per base strategy and DB, candidates 2013-06-01 -> 2021-01-01
           (labels resolved on bars < 2021-01-01; now_ts pinned 2022-03-10).  Saved as joblib under research/results/F5.
  train  : TRAIN rows only (decision t < 2019-01-01 AND label end < 2019-01-01 = purge).  Walk-forward OOS inside
           TRAIN (train.evaluate_entry / evaluate_exit, 4 expanding folds, embargo 48 h) = the selection metric of
           protocol §4; then ONE final fit on TRAIN (train | cal | val = final_split) saved as the bundle that the
           wrapper plugin disc-f5-ml and the `valid` command score with.  Never reads VALID rows.
  valid  : the single VALID evaluation of a saved bundle: rows with 2019-01-01 + 48 h <= t < 2021-01-01, policy
           P > threshold vs take-all, on all candidates and on the candidates the base backtest really traded
           (`taken`), by year, Brier / log loss / AUC / ECE; exit model policy on the VALID trades.

Harness (fixed before the first run, same for every configuration; see SUMMARY.md §1): risk = the task's portfolio
caps + max_spread_points 60 (so GBPJPY/XAUUSD are not permanently market-blocked at 30; F1 precedent) + account
limits off (max_daily_loss_pct 100, max_consecutive_losses 1000: R measures the strategy, not the account);
strategy rescue_enabled false (the live RESCUE exit is an M5 model); exit base with max_hold_hours 24 (Stage D / F1
precedent for H1, so the entry label = the simulated trade).  Base strategy signal parameters = plugin defaults.
"""
from __future__ import annotations

import argparse
import functools
import json
import math
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

os.environ.setdefault("OMP_NUM_THREADS", "2")  # other agents share the 4 CPUs

import numpy as np  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import backtest as bt  # noqa: E402
from app.ml import dataset, train  # noqa: E402
from app.ml import features as F  # noqa: E402

OUT_DIR = ROOT / "research" / "results" / "F5"
SYMBOLS = ["AUDJPY", "AUDUSD", "EURCHF", "EURGBP", "EURJPY", "EURUSD", "GBPJPY", "GBPUSD", "USDCAD", "USDCHF",
           "USDJPY", "XAUUSD"]
HARNESS_RISK = {"max_open_positions": 4, "max_trades_per_day": 20, "max_same_currency_direction": 2,
                "max_spread_points": 60, "max_daily_loss_pct": 100, "max_consecutive_losses": 1000}
HARNESS_STRATEGY = {"rescue_enabled": False}
EMBARGO_S = 48 * 3600
N_FOLDS = 4
train.MAX_THREADS = 2  # _limit_threads reads the module global at call time; other agents share the 4 CPUs


def ts(s: str) -> int:
    return int(datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())


T_TRAIN0, T_VALID0, T_VALID1, T_NOW = ts("2013-06-01"), ts("2019-01-01"), ts("2021-01-01"), ts("2022-03-10")


def dataset_params(base: str, specs: dict, hold_hours: float = 24.0, workers: int = 2) -> dict:
    return {"strategy_name": base, "signal_tf": "H1", "htf_tf": "H4", "symbols": list(SYMBOLS),
            "start_ts": T_TRAIN0, "end_ts": T_VALID1, "now_ts": T_NOW, "size_mode": "r_only", "session": None,
            "specs": specs, "cost": {"mode": "stats"}, "risk": dict(HARNESS_RISK), "strategy": dict(HARNESS_STRATEGY),
            "exit": {"mode": "base", "params": {"max_hold_hours": hold_hours}}, "workers": workers}


# ---- dataset helpers -------------------------------------------------------------------------------

_ARRAY_KEYS_SKIP = ("feature_names", "schema")


def subset(ds: dict, idx) -> dict:
    idx = np.asarray(idx, dtype=int)
    out = {}
    for k, v in ds.items():
        out[k] = v if k in _ARRAY_KEYS_SKIP else np.asarray(v)[idx]
    return out


def train_rows(ds: dict, t_key: str = "t") -> np.ndarray:
    """TRAIN rows: decision before VALID and label ending before VALID (purge of labels that reach into VALID)."""
    t = np.asarray(ds[t_key], dtype=float)
    t_end = np.asarray(ds["t_end"], dtype=float)
    return np.flatnonzero((t >= T_TRAIN0) & (t < T_VALID0) & (t_end < T_VALID0))


def valid_rows(ds: dict, t_key: str = "t") -> np.ndarray:
    t = np.asarray(ds[t_key], dtype=float)
    return np.flatnonzero((t >= T_VALID0 + EMBARGO_S) & (t < T_VALID1))


def by_year(t, r) -> dict:
    out = {}
    for ti, ri in zip(np.asarray(t), np.asarray(r, dtype=float)):
        y = datetime.fromtimestamp(float(ti), timezone.utc).year
        out.setdefault(str(y), []).append(float(ri))
    return {y: {"n": len(v), "avg_r": round(sum(v) / len(v), 4), "sum_r": round(sum(v), 2)} for y, v in sorted(out.items())}


def clean(o):
    if isinstance(o, dict):
        return {str(k): clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [clean(v) for v in o]
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating, float)):
        return None if (isinstance(o, float) or isinstance(o, np.floating)) and not math.isfinite(float(o)) else float(o)
    if isinstance(o, np.ndarray):
        return clean(o.tolist())
    if isinstance(o, np.bool_):
        return bool(o)
    return o


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(clean(obj), indent=1, ensure_ascii=False, default=str))


# ---- commands ----------------------------------------------------------------------------------------

def cmd_build(a) -> int:
    specs = json.load(open(a.specs))
    params = dataset_params(a.base, specs, hold_hours=a.hold_hours, workers=a.workers)
    db = bt.ReadOnlyDB(a.db)
    t0 = time.time()
    try:
        ds = dataset.build(db, params)
    finally:
        db.close()
    dataset.save(ds, a.out)
    m = ds["meta"]
    info = {"base": a.base, "db": a.db, "out": a.out, "elapsed_s": round(time.time() - t0, 1),
            "n_entry": m["n_entry"], "n_exit": m["n_exit"], "n_trades": m["n_trades"], "exit_dropped": m["exit_dropped"],
            "dataset_version": m["dataset_version"], "exit_policy": m["exit_policy"], "cost": m["cost"],
            "n_entry_train": int(len(train_rows(ds["entry"]))), "n_entry_valid": int(len(valid_rows(ds["entry"]))),
            "n_exit_rows_train": int(len(train_rows(ds["exit"], "t_entry"))) if m["n_exit"] else 0}
    write_json(Path(a.out).with_suffix(".meta.json"), info)
    print(json.dumps(info))  # no VALID performance is printed here: only counts
    return 0


def _set_threshold_rule(min_frac: float) -> None:
    """fit_entry/evaluate_entry call train.choose_threshold through the module global: a partial changes the minimum
    coverage of the validation-chosen threshold without editing app/ml/train.py."""
    orig = train.choose_threshold
    base = getattr(orig, "func", orig)
    train.choose_threshold = functools.partial(base, min_frac=min_frac)


def _entry_bundle(m: dict, model_key: str, thr: float, ds: dict, cfg: dict, trained_until: int) -> dict:
    return {"kind": "entry", "feature_schema": F.ENTRY_SCHEMA, "feature_names": list(ds["feature_names"]),
            "model_inputs": list(ds["feature_names"]) + ["sym_te"], "model": m[model_key], "sym_te": m["te"],
            "threshold": float(thr), "p_hi": m.get("p_hi"), "trained_until": int(trained_until),
            "sklearn": train._sk_version(), "seed": train.SEED, "created_ts": time.time(), "f5_config": cfg}


def cmd_train(a) -> int:
    ds_all = dataset.load(a.dataset)
    ent = ds_all["entry"]
    tr_idx = train_rows(ent)
    if a.taken_only:  # variant: learn only from the candidates the base backtest really traded (non-overlapping rows)
        tr_idx = tr_idx[np.asarray(ent["taken"])[tr_idx].astype(bool)]
    sub = subset(ent, tr_idx)
    _set_threshold_rule(a.min_cov)
    t0 = time.time()
    with train._limit_threads():
        wf = train.evaluate_entry(sub, n_folds=N_FOLDS, seed=train.SEED, embargo_s=EMBARGO_S)
        sp = train.final_split(sub["t"], sub["t_end"], embargo_s=EMBARGO_S)
        m = train.fit_entry(sub, sp["train"], sp["cal"], sp["val"], train.SEED)
        r = np.asarray(sub["r"], dtype=float)
        p_va_lr = m["baseline"].predict_proba(train._entry_X(sub, sp["val"], m["te"]))[:, 1]
        thr_lr = train.choose_threshold(p_va_lr, r[sp["val"]])
    used = np.concatenate([sp["train"], sp["cal"], sp["val"]])
    trained_until = int(np.max(np.asarray(sub["t_end"])[used]))
    cfg = {"base": ds_all["meta"]["strategy_name"], "dataset": a.dataset, "min_cov": a.min_cov, "folds": N_FOLDS,
           "taken_only": bool(a.taken_only),
           "embargo_h": EMBARGO_S / 3600, "hgb": train.HGB_PARAMS}
    tag = a.tag
    bundles = {"hgb": _entry_bundle(m, "model", m["threshold"], sub, {**cfg, "model": "hgb_isotonic"}, trained_until),
               "lr": _entry_bundle(m, "baseline", thr_lr, sub, {**cfg, "model": "logreg"}, trained_until)}
    import joblib
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    paths = {}
    for k, b in bundles.items():
        paths[k] = str(OUT_DIR / f"bundle_{tag}_{k}.joblib")
        joblib.dump(b, paths[k], compress=3)
    oos = wf["oos"]
    out = {"tag": tag, "config": cfg, "elapsed_s": round(time.time() - t0, 1),
           "train_rows": {"n": int(len(tr_idx)), "from": int(sub["t"].min()), "to": int(sub["t"].max()),
                          "base_rate": round(float((r > 0).mean()), 4), "avg_r_all": round(float(r.mean()), 4)},
           "walk_forward_oos": {"folds": wf["folds"],
                                "hgb": {"policy": oos["policy"], "coverage": oos["coverage"], "model": oos["model"],
                                        "taken_by_backtest": oos["taken_by_backtest"]},
                                "lr": {"policy": oos["policy_lr"], "model": oos["baseline_lr"]},
                                "baseline_take_all": oos["baseline_take_all"]},
           "final_fit": {"n_train": int(len(sp["train"])), "n_cal": int(len(sp["cal"])), "n_val": int(len(sp["val"])),
                         "threshold_hgb": m["threshold"], "threshold_lr": thr_lr, "p_hi": m["p_hi"],
                         "trained_until": trained_until, "bounds": sp["bounds"]},
           "bundles": paths}
    # exit model (xs-v1) on the TRAIN trades: walk-forward OOS + final fit (cheap: the rows already exist)
    if a.exit and ds_all["meta"]["n_exit"]:
        ex = ds_all["exit"]
        ex_sub = subset(ex, train_rows(ex, "t_entry"))
        with train._limit_threads():
            wfx = train.evaluate_exit(ex_sub, n_folds=N_FOLDS, seed=train.SEED, embargo_s=EMBARGO_S)
            spx = train.final_split(ex_sub["t_entry"], ex_sub["t_end"], embargo_s=EMBARGO_S)
            trx = np.concatenate([spx["train"], spx["cal"]])
            mx = train.fit_exit(ex_sub, trx, spx["val"], train.SEED)
        margin = None if math.isinf(mx["margin"]) else mx["margin"]
        xb = {"kind": "exit", "feature_schema": F.EXIT_SCHEMA, "feature_names": list(ex_sub["feature_names"]),
              "model_inputs": list(ex_sub["feature_names"]), "model": mx["model"], "exit_margin": margin,
              "sklearn": train._sk_version(), "seed": train.SEED, "created_ts": time.time(), "f5_config": cfg}
        paths["exit"] = str(OUT_DIR / f"bundle_{tag}_exit.joblib")
        joblib.dump(xb, paths["exit"], compress=3)
        out["exit_model"] = {"train_rows": int(len(ex_sub["t"])), "walk_forward_oos": wfx["oos"], "folds": wfx["folds"],
                             "final_margin": margin, "n_train_rows": int(len(trx)), "n_val_rows": int(len(spx["val"]))}
    write_json(OUT_DIR / f"train_{tag}.json", out)
    h, l, b0 = oos["policy"], oos["policy_lr"], oos["baseline_take_all"]
    print(json.dumps(clean({"tag": tag, "train_oos_hgb": {"n": h["n"], "avg_r": h["avg_r"], "ci90": h["ci90"], "pf": h["pf"]},
                            "train_oos_lr": {"n": l["n"], "avg_r": l["avg_r"], "ci90": l["ci90"], "pf": l["pf"]},
                            "train_oos_take_all": {"n": b0["n"], "avg_r": b0["avg_r"], "pf": b0["pf"]},
                            "taken_bt": oos["taken_by_backtest"], "auc": oos["model"].get("auc"),
                            "thr_final_hgb": m["threshold"], "thr_final_lr": thr_lr,
                            "exit": (out.get("exit_model") or {}).get("walk_forward_oos")})))
    return 0


def _policy_block(p, thr, r, t, taken) -> dict:
    take = p > thr
    out = {"threshold": float(thr), "coverage": round(float(take.mean()), 4) if len(take) else None,
           "all_candidates": {"policy": train.policy_stats(r[take]), "take_all": train.policy_stats(r),
                              "policy_by_year": by_year(t[take], r[take]), "take_all_by_year": by_year(t, r)},
           "taken_by_backtest": {"policy": train.policy_stats(r[taken & take]), "take_all": train.policy_stats(r[taken]),
                                 "policy_by_year": by_year(t[taken & take], r[taken & take]),
                                 "take_all_by_year": by_year(t[taken], r[taken])}}
    return out


def cmd_valid(a) -> int:
    """ONE evaluation of the saved bundles on the VALID rows of the dataset (run once per chosen configuration)."""
    import joblib
    ds_all = dataset.load(a.dataset)
    ent = ds_all["entry"]
    va = valid_rows(ent)
    sub = subset(ent, va)
    r = np.asarray(sub["r"], dtype=float)
    y = (r > 0).astype(int)
    t = np.asarray(sub["t"], dtype=float)
    taken = np.asarray(sub["taken"]).astype(bool)
    out = {"tag": a.tag, "dataset": a.dataset, "valid_rows": {"n": int(len(va)), "from": int(t.min()), "to": int(t.max()),
                                                              "base_rate": round(float(y.mean()), 4)},
           "take_all": {"all_candidates": train.policy_stats(r), "taken_by_backtest": train.policy_stats(r[taken]),
                        "by_year_all": by_year(t, r), "by_year_taken": by_year(t[taken], r[taken])}}
    for key, path in (("hgb", a.bundle_hgb), ("lr", a.bundle_lr)):
        if not path:
            continue
        b = joblib.load(path)
        X = np.column_stack([sub["X"], train.apply_symbol_te(b["sym_te"], sub["symbol"])])
        p = b["model"].predict_proba(X)[:, 1]
        out[key] = {"bundle": path, "classification": train.classif_metrics(p, y),
                    **_policy_block(p, b["threshold"], r, t, taken)}
        out[key]["gate1_checklist_rowlevel"] = gate1(out[key]["taken_by_backtest"]["policy"],
                                                     out[key]["taken_by_backtest"]["policy_by_year"])
    if a.bundle_exit and ds_all["meta"]["n_exit"]:
        bx = joblib.load(a.bundle_exit)
        ex = ds_all["exit"]
        exv = subset(ex, valid_rows(ex, "t_entry"))
        pred = bx["model"].predict(exv["X"])
        margin = bx["exit_margin"]
        pol, base = train.exit_policy_r(exv, np.arange(len(exv["t"])), pred, math.inf if margin is None else margin)
        out["exit"] = {"bundle": a.bundle_exit, "margin": margin, "n_rows": int(len(exv["t"])),
                       "regression": train.regress_metrics(pred, exv["target"]),
                       "policy": train.policy_stats(pol), "base_exits": train.policy_stats(base)}
    write_json(OUT_DIR / f"valid_{a.tag}.json", out)
    short = {"tag": a.tag, "take_all_taken": out["take_all"]["taken_by_backtest"],
             "take_all_cands": out["take_all"]["all_candidates"]}
    for key in ("hgb", "lr"):
        if key in out:
            short[key] = {"taken": out[key]["taken_by_backtest"]["policy"], "cands": out[key]["all_candidates"]["policy"],
                          "auc": out[key]["classification"].get("auc"), "brier": out[key]["classification"].get("brier"),
                          "ece": out[key]["classification"].get("ece"), "gate1_rowlevel": out[key]["gate1_checklist_rowlevel"]}
    if "exit" in out:
        short["exit"] = {"policy": out["exit"]["policy"], "base": out["exit"]["base_exits"], "margin": out["exit"]["margin"]}
    print(json.dumps(clean(short)))
    return 0


def gate1(pol: dict, years: dict, n_min: int = 100) -> dict:
    avg, lo, pf, n = pol.get("avg_r"), (pol.get("ci90") or [None])[0], pol.get("pf"), pol.get("n") or 0
    items = {"avg_r_pos": avg is not None and avg > 0, "ci90_lo_gt_-0.02": lo is not None and lo > -0.02,
             "pf_gt_1.10": pf is not None and pf > 1.10, "n_ge_min": n >= n_min,
             "one_year_pos": any(v["avg_r"] > 0 for v in years.values())}
    items["passed"] = all(items.values())
    return items


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--db", default=str(ROOT / "data" / "research.db"))
    b.add_argument("--specs", default=str(ROOT / "data" / "research_specs.json"))
    b.add_argument("--base", required=True)
    b.add_argument("--hold-hours", type=float, default=24.0)
    b.add_argument("--workers", type=int, default=2)
    b.add_argument("--out", required=True)
    t = sub.add_parser("train")
    t.add_argument("--dataset", required=True)
    t.add_argument("--tag", required=True)
    t.add_argument("--min-cov", type=float, default=train.THRESH_MIN_FRAC)
    t.add_argument("--exit", action="store_true")
    t.add_argument("--taken-only", action="store_true")
    v = sub.add_parser("valid")
    v.add_argument("--dataset", required=True)
    v.add_argument("--tag", required=True)
    v.add_argument("--bundle-hgb", default="")
    v.add_argument("--bundle-lr", default="")
    v.add_argument("--bundle-exit", default="")
    a = ap.parse_args(argv)
    return {"build": cmd_build, "train": cmd_train, "valid": cmd_valid}[a.cmd](a)


if __name__ == "__main__":
    raise SystemExit(main())
