"""CLI: python -m app.ml <command>

train    --db PATH --out DIR [--base-strategy trend-v1] [--tf M5|H1] [--days N] [--symbols A,B] [--folds 4]
         [--seed 7] [--registry-db PATH [--shadow]] [--dataset FILE] [--json]
         Reads --db READ-ONLY (use a snapshot), builds the datasets through the backtest, trains + evaluates (walk-
         forward OOS) the entry and exit models, saves artifacts in --out. Without --registry-db nothing is
         registered: the summary carries the registry records (live process: registry.register_record).
         With --registry-db the models are inserted as 'candidate'; --shadow then moves the newest candidate of each
         kind to 'shadow' (older shadows archived). Never approves.
list     --db PATH [--kind entry|exit] [--status ...]
approve  --db PATH --id ID --by NAME --confirm APROB [--override --confirm-risk "ȘTIU RISCUL"] [--notes TEXT]
         (shadow -> approved; explicit human decision). Runs the same promotion checklist as the UI/API and writes
         the same ml_approvals audit row; refused when the checklist fails (override as in the API).
retire   --db PATH --id ID [--by NAME] [--restore-previous]
shadow   --db PATH --id ID                                 (candidate -> shadow)
"""
from __future__ import annotations

import os

os.environ.setdefault("OMP_NUM_THREADS", "3")  # before scikit-learn/numpy load their thread pools

import argparse  # noqa: E402
import json  # noqa: E402
import sys  # noqa: E402
import time  # noqa: E402

MAX_WORKERS = 3


def _nice() -> None:
    try:
        os.nice(10)  # the live bot keeps priority
    except (AttributeError, OSError):
        pass


def _short(metrics: dict) -> dict:
    o = metrics.get("oos") or {}
    keep = {k: o.get(k) for k in ("policy", "baseline_take_all", "baseline_base_exits", "policy_lr", "coverage")
            if k in o}
    m = o.get("model") or {}
    keep["model"] = {k: m.get(k) for k in ("n", "brier", "brier_climatology", "log_loss", "auc", "ece", "mae", "rmse",
                                          "r2", "corr", "calib_mae") if k in m}
    keep["checklist"] = metrics.get("checklist")
    keep["final"] = metrics.get("final")
    return keep


def cmd_train(a) -> int:
    _nice()
    from ..backtest import ReadOnlyDB
    from . import dataset, registry, train
    t0 = time.time()
    out_dir = os.path.abspath(a.out)
    os.makedirs(out_dir, exist_ok=True)
    if a.dataset:
        ds = dataset.load(a.dataset)
        ds_path = a.dataset
    else:
        params = dataset.default_params(a.base_strategy, a.tf, days=a.days,
                                        workers=min(MAX_WORKERS, max(1, a.workers)))
        if a.symbols:
            params["symbols"] = [x.strip().upper() for x in a.symbols.split(",") if x.strip()]
        if a.strategy_params:
            params["strategy"] = json.loads(a.strategy_params)
        if a.cost:
            params["cost"] = json.loads(a.cost)
        if a.no_session:
            params["session"] = None
        db = ReadOnlyDB(a.db)
        try:
            ds = dataset.build(db, params, exit_cost_r=a.exit_cost_r)
        finally:
            db.close()
        ds_path = os.path.join(out_dir, f"{ds['meta']['dataset_version']}.joblib")
        dataset.save(ds, ds_path)
    meta = ds["meta"]
    summary = {"dataset": {"path": ds_path, "version": meta["dataset_version"], "n_entry": meta["n_entry"],
                           "n_exit": meta["n_exit"], "n_trades": meta["n_trades"], "exit_dropped": meta.get("exit_dropped"),
                           "strategy": meta["strategy_name"], "signal_tf": meta["signal_tf"],
                           "backtest_summary": {k: meta["backtest_summary"].get(k)
                                                for k in ("trades", "avg_r", "ci90_avg_r", "profit_factor")}},
               "models": [], "errors": {}, "records": []}
    rdb = None
    if a.registry_db:
        from ..db import DB
        rdb = DB(a.registry_db)
    common = {"dataset_version": meta["dataset_version"], "out_dir": out_dir}
    for kind in [k.strip() for k in a.kinds.split(",") if k.strip()]:
        try:
            fn = train.train_entry if kind == "entry" else train.train_exit
            bundle, metrics = fn(ds[kind], n_folds=a.folds, seed=a.seed, embargo_s=a.embargo_hours * 3600)
        except ValueError as e:
            summary["errors"][kind] = str(e)
            continue
        bundle["dataset_meta"] = {k: meta.get(k) for k in ("strategy_name", "strategy_version", "signal_tf", "htf_tf",
                                                            "dataset_version", "exit_policy", "cost")}
        name = f"{meta['strategy_name']}-{meta['signal_tf'].lower()}"
        params_rec = {"base_strategy": meta["strategy_name"], "signal_tf": meta["signal_tf"], "htf_tf": meta["htf_tf"],
                      "folds": a.folds, "seed": a.seed, "embargo_hours": a.embargo_hours, "hgb": train.HGB_PARAMS,
                      "dataset_params": meta.get("params"), "exit_cost_r": meta.get("exit_cost_r")}
        kw = dict(kind=kind, name=name, version="1", feature_schema=bundle["feature_schema"], params=params_rec,
                  metrics=metrics, trained_until_ts=bundle.get("trained_until"), **common)
        if rdb is not None:
            mid = registry.register(rdb, bundle, **kw)
            rec = registry.get(rdb, mid)
        else:
            rec = registry.make_record(bundle, **kw)
            summary["records"].append(rec)
        summary["models"].append({"id": rec["id"], "kind": kind, "artifact": rec["artifact_path"],
                                  "sha256": rec["sha256"], "metrics": _short(metrics)})
    if rdb is not None and a.shadow:
        summary["shadow"] = {k: registry.promote_newest_candidate(rdb, k) for k in ("entry", "exit")}
    summary["elapsed_s"] = round(time.time() - t0, 1)
    from .registry import clean_json
    summary = clean_json(summary)
    with open(os.path.join(out_dir, f"summary-{int(time.time())}.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=1)
    if a.json:
        print(json.dumps(summary, ensure_ascii=False))
    else:
        _print(summary)
    return 0 if summary["models"] else 2


def _print(s: dict) -> None:
    d = s["dataset"]
    print(f"Dataset {d['version']}: {d['n_entry']} candidați, {d['n_exit']} rânduri ieșire ({d['n_trades']} tranzacții)")
    for m in s["models"]:
        mm = m["metrics"]
        pol = mm.get("policy") or {}
        base = mm.get("baseline_take_all") or mm.get("baseline_base_exits") or {}
        print(f"- {m['id']}: OOS politică n={pol.get('n')} avgR={pol.get('avg_r')} CI90={pol.get('ci90')} "
              f"PF={pol.get('pf')} | bază n={base.get('n')} avgR={base.get('avg_r')} | model {mm.get('model')}")
        print(f"  checklist {mm.get('checklist')}")
    for k, e in s["errors"].items():
        print(f"! {k}: {e}")


def _db(path: str):
    from ..db import DB
    return DB(path)


class _CliRuntime:
    """Just enough of the engine runtime for MLLive.approve: logs go to stderr and system_events."""

    def __init__(self, db) -> None:
        self.db = db
        saved = None
        try:
            saved = db.get_setting("strategy")
        except Exception:
            pass
        # timeframes are not known outside the live process (env settings): the live gate re-checks them
        self.strategy = {"name": (saved or {}).get("name") if isinstance(saved, dict) else None}

    def log(self, level: str, component: str, message: str) -> None:
        print(f"[{level}] {message}", file=sys.stderr)
        try:
            from ..config import iso, utcnow
            self.db.execute("INSERT INTO system_events(time_utc, level, component, message) VALUES(?,?,?,?)",
                            (iso(utcnow()), level, component, message))
        except Exception:
            pass


class _CliEngine:
    def __init__(self, db) -> None:
        self.db = db
        self.settings = None
        self.rt = _CliRuntime(db)

    def active_strategy_name(self):
        return self.rt.strategy.get("name") or "trend-v1"


def cmd_approve(db, a) -> int:
    """Same path as POST /api/v1/models/{id}/approve: checklist + ml_approvals audit row (MLLive.approve)."""
    from ..ml_live import OVERRIDE_CONFIRM, MLLive
    if a.override and a.confirm_risk != OVERRIDE_CONFIRM:
        print(f'Override necesită și --confirm-risk "{OVERRIDE_CONFIRM}".', file=sys.stderr)
        return 2
    who = (a.by or "").strip()
    if not who:
        print("--by este obligatoriu (cine aprobă).", file=sys.stderr)
        return 2
    ml = MLLive(_CliEngine(db))
    if not ml.ensure_tables():
        print("Eroare: tabelele ML nu pot fi create.", file=sys.stderr)
        return 2
    try:
        res = ml.approve(a.id, who, override=a.override, notes=a.notes or "")
    except LookupError as e:
        print(f"Eroare: {e}", file=sys.stderr)
        return 2
    except PermissionError as e:
        try:
            chk = json.loads(str(e))
        except ValueError:
            chk = {}
        for i in chk.get("items", []) + ([chk["strategy"]] if chk.get("strategy") else []):
            print(f"  {'OK ' if i.get('ok') else 'NU '} {i.get('label')}: {i.get('value')} (cerut {i.get('need')})",
                  file=sys.stderr)
        if chk.get("hard_failed"):
            print("Refuzat: criterii care nu pot fi forțate: " + ", ".join(chk.get("hard_labels") or []), file=sys.stderr)
        else:
            print(f'Refuzat: checklist de promovare nepromovat. Aprobarea forțată cere --override --confirm-risk '
                  f'"{OVERRIDE_CONFIRM}".', file=sys.stderr)
        return 3
    except ValueError as e:
        print(f"Eroare: {e}", file=sys.stderr)
        return 2
    tag = "" if res["checklist"]["passed"] else " (OVERRIDE)"
    print(f"Aprobat: {a.id} ({who}){tag}" + (f"; înlocuiește {res['previous']}" if res.get("previous") else ""))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.ml", description="Modele ML (shadow/aprobare manuală)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("train")
    t.add_argument("--db", required=True, help="baza de date (deschisă doar pentru citire; folosește o copie)")
    t.add_argument("--out", required=True, help="director pentru dataset/artefacte/rezumat")
    t.add_argument("--base-strategy", default="trend-v1")
    t.add_argument("--tf", default="M5")
    t.add_argument("--days", type=int, default=120)
    t.add_argument("--symbols", default="")
    t.add_argument("--strategy-params", default="", help="JSON suprascrieri strategie")
    t.add_argument("--cost", default="", help='JSON cost backtest, ex. {"mode": "stats"}')
    t.add_argument("--no-session", action="store_true")
    t.add_argument("--workers", type=int, default=MAX_WORKERS)
    t.add_argument("--folds", type=int, default=4)
    t.add_argument("--seed", type=int, default=7)
    t.add_argument("--embargo-hours", type=float, default=24.0)
    t.add_argument("--exit-cost-r", type=float, default=0.0)
    t.add_argument("--kinds", default="entry,exit")
    t.add_argument("--dataset", default="", help="dataset salvat anterior (sare peste backtest)")
    t.add_argument("--registry-db", default="", help="înregistrează modelele (candidate) în această bază")
    t.add_argument("--shadow", action="store_true", help="cu --registry-db: cel mai nou candidat -> shadow")
    t.add_argument("--json", action="store_true")
    ls = sub.add_parser("list")
    ls.add_argument("--db", required=True)
    ls.add_argument("--kind", default=None)
    ls.add_argument("--status", default=None)
    ap_ = sub.add_parser("approve")
    ap_.add_argument("--db", required=True)
    ap_.add_argument("--id", required=True)
    ap_.add_argument("--by", required=True)
    ap_.add_argument("--confirm", required=True)
    ap_.add_argument("--override", action="store_true", help="aprobare forțată (checklist nepromovat)")
    ap_.add_argument("--confirm-risk", default="", help='cu --override: exact "ȘTIU RISCUL"')
    ap_.add_argument("--notes", default="")
    rt = sub.add_parser("retire")
    rt.add_argument("--db", required=True)
    rt.add_argument("--id", required=True)
    rt.add_argument("--by", default=None)
    rt.add_argument("--restore-previous", action="store_true")
    sh = sub.add_parser("shadow")
    sh.add_argument("--db", required=True)
    sh.add_argument("--id", required=True)
    a = ap.parse_args(argv)
    from . import registry
    if a.cmd == "train":
        return cmd_train(a)
    db = _db(a.db)
    if a.cmd == "list":
        for r in registry.list_models(db, a.kind, a.status):
            print(f"{r['id']}  {r['kind']:<5} {r['status']:<9} {r['feature_schema']}  {r['created_at']}  "
                  f"aprobat: {r['approved_by'] or '-'}")
        return 0
    try:
        if a.cmd == "approve":
            if a.confirm != "APROB":
                print("Confirmarea trebuie să fie exact APROB.", file=sys.stderr)
                return 2
            return cmd_approve(db, a)
        elif a.cmd == "retire":
            r = registry.retire(db, a.id, a.by, a.restore_previous)
            print(f"Retras: {r['retired']}; restaurat: {r['restored'] or '-'}")
        elif a.cmd == "shadow":
            registry.to_shadow(db, a.id)
            print(f"Shadow: {a.id}")
    except registry.RegistryError as e:
        print(f"Eroare: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
