"""ML live glue (spec §6.4): shadow logging, approved-model gating, nightly retrain scheduling.

Safety contract:
- learning runs separately from trading (subprocess with nice, on a DB snapshot); it only creates 'candidate' models,
  moves the newest candidate to 'shadow' and archives older shadows — it NEVER approves;
- 'shadow' models only write rows to ml_decisions; they never change a trading decision;
- an 'approved' entry model may only (a) veto an entry whose P is below its approved threshold and (b) scale the risk
  by a factor in [ML_SIZE_MIN, ML_SIZE_MAX]; the Risk Engine still applies every cap;
- an 'approved' exit model may only CLOSE a bot position early (reason ML_EXIT): never modifies SL/TP, never adds;
- with no approved model the bot behaves exactly as without this module;
- app.ml (features / infer / registry / train) is imported lazily: when it is missing or broken every hook degrades to
  a no-op (logged once).
"""
from __future__ import annotations

import hashlib
import importlib
import importlib.util
import json
import math
import os
import shlex
import sqlite3
import subprocess
import sys
import threading
import time
from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import ROOT_DIR, iso

# ml_models / ml_decisions are created by app.db (shared schema); these are this module's own, additive tables.
TABLES = """
CREATE TABLE IF NOT EXISTS ml_approvals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    model_id TEXT NOT NULL,
    kind TEXT,
    action TEXT NOT NULL,
    actor TEXT,
    ts REAL NOT NULL,
    prev_model_id TEXT,
    checklist_json TEXT,
    override INTEGER NOT NULL DEFAULT 0,
    notes TEXT
);
CREATE TABLE IF NOT EXISTS ml_retrain_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trigger TEXT,
    started_ts REAL NOT NULL,
    finished_ts REAL,
    status TEXT NOT NULL,
    returncode INTEGER,
    imported_json TEXT,
    log_tail TEXT
);
"""
# fallback when app.db predates the ML tables (same columns as app.db)
FALLBACK_TABLES = """
CREATE TABLE IF NOT EXISTS ml_models (
    id TEXT PRIMARY KEY, kind TEXT NOT NULL, name TEXT NOT NULL, version TEXT, feature_schema TEXT NOT NULL,
    dataset_version TEXT, params_json TEXT, metrics_json TEXT, trained_until_utc TEXT, artifact_path TEXT NOT NULL,
    sha256 TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'candidate', created_at TEXT NOT NULL, approved_by TEXT,
    approved_at TEXT, notes TEXT
);
CREATE TABLE IF NOT EXISTS ml_decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, model_id TEXT NOT NULL, ts REAL NOT NULL, kind TEXT NOT NULL, symbol TEXT,
    ref_id TEXT, features_hash TEXT, score REAL, would_action TEXT, actual_action TEXT
);
"""
ADDED_COLUMNS = [("ml_decisions", "detail_json", "TEXT")]

KINDS = ("entry", "exit")
ACTIVE_STATUSES = ("shadow", "approved")
APPROVE_CONFIRM = "APROB"
OVERRIDE_CONFIRM = "ȘTIU RISCUL"
APPROVER = "admin"
SIZE_FACTOR_HARD_MAX = 2.0  # ML_SIZE_MAX can never exceed this
REFRESH_EVERY_S = 60.0
FLUSH_EVERY_S = 5.0
PRUNE_EVERY_S = 3600.0
EXIT_EVERY_S = 60  # one exit-state row per open bot position per minute (same cadence as trade_paths)
MAX_QUEUE = 5000  # pending ml_decisions rows kept in memory (oldest dropped beyond)
MAX_PENDING_JOBS = 32  # shadow jobs waiting in the ML thread (new ones dropped beyond)
BARS_FOR_FEATURES = 400
HTF_BARS_FOR_FEATURES = 200
# promotion checklist (spec): OOS avg R > 0 and CI90 lower > -0.02 after costs, beats baseline, ECE < 0.05,
# >= 2 weeks or >= 100 shadow decisions with a consistent sign
KEEP_DATASETS = 3  # retrain leaves a ~10-15 MB dataset (ds-*.joblib) per run in ML_MODELS_DIR: keep the newest few
KEEP_SUMMARIES = 30
RETRAIN_MIN_UPTIME_S = 600.0  # no nightly run in the first minutes after a (re)start: avoids CPU spikes on restart loops
CHK_CI_LO_MIN = -0.02
CHK_ECE_MAX = 0.05
# pre-registered Stage D gate (docs/research/STAGE_D_RESULTS.md): beat the baseline by > 0.05R, exit calibration
# < 0.05R, >= 150 OOS trades — the live checklist enforces the same thresholds (never looser)
CHK_EXIT_CALIB_MAX = 0.05  # exit regressor: mean |pred - target| over prediction bins (R)
CHK_BEAT_MIN = 0.05
CHK_MIN_OOS_N = 150
# checklist items an override can never skip; a model approved with an override can veto but never raises the risk
HARD_ITEMS = ("status_shadow", "strategy_match")
OVERRIDE_SIZE_MAX = 1.0
CHK_SHADOW_DAYS = 14
CHK_SHADOW_N = 100
CHK_SHADOW_MIN_LABELED = 20
CHK_SHADOW_MIN_TRADES = 10


# ---- small helpers -------------------------------------------------------
def _f(x) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _loads(s, default):
    if s is None or s == "":
        return default
    if isinstance(s, (dict, list)):
        return s
    try:
        return json.loads(s)
    except (TypeError, ValueError):
        return default


def _get(d, path: str):
    cur = d
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def _first(d, *paths):
    for p in paths:
        v = _get(d, p)
        if v is not None:
            return v
    return None


def _ci(v) -> list[float] | None:
    if isinstance(v, (list, tuple)) and len(v) == 2 and all(_f(x) is not None for x in v):
        return [float(v[0]), float(v[1])]
    return None


def features_hash(feats: dict) -> str:
    items = sorted((str(k), None if _f(v) is None else round(float(v), 8)) for k, v in (feats or {}).items())
    return hashlib.sha1(json.dumps(items, separators=(",", ":")).encode()).hexdigest()[:16]


def file_sha256(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def calibration_bins(rows) -> list[dict]:
    """[{p, obs, n}] from app.ml.train's bins (entry: mean_p/frac_pos, exit: mean_pred/mean_target)."""
    out = []
    for b in rows or []:
        if not isinstance(b, dict):
            continue
        p = _f(_first(b, "mean_p", "mean_pred", "p"))
        o = _f(_first(b, "frac_pos", "mean_target", "obs"))
        if p is None or o is None:
            continue
        out.append({"p": p, "obs": o, "n": _f(b.get("n"))})
    return out


def model_summary(row: dict) -> dict:
    """OOS policy vs baseline + calibration figures from app.ml.train's metrics_json (oos.policy / oos.baseline_* /
    oos.model / final)."""
    m = _loads(row.get("metrics_json"), {}) or {}
    p = _loads(row.get("params_json"), {}) or {}
    kind = row.get("kind")
    pol = _first(m, "oos.policy") or {}
    base = (_first(m, "oos.baseline_take_all") if kind == "entry" else _first(m, "oos.baseline_base_exits")) \
        or _first(m, "oos.baseline", "oos.baseline_take_all", "oos.baseline_base_exits") or {}
    model = _first(m, "oos.model") or {}
    bins = calibration_bins(model.get("calibration"))
    return {
        "avg_r": _f(pol.get("avg_r")), "ci90": _ci(pol.get("ci90")), "pf": _f(pol.get("pf")), "n": _f(pol.get("n")),
        "win_rate": _f(pol.get("win_rate")),
        "baseline_avg_r": _f(base.get("avg_r")), "baseline_ci90": _ci(base.get("ci90")), "baseline_pf": _f(base.get("pf")),
        "baseline_n": _f(base.get("n")),
        "brier": _f(model.get("brier")), "brier_climatology": _f(model.get("brier_climatology")),
        "log_loss": _f(model.get("log_loss")), "auc": _f(model.get("auc")), "ece": _f(model.get("ece")),
        "calib_mae": _f(model.get("calib_mae")), "calibration": bins,
        "coverage": _f(_get(m, "oos.coverage")),
        "threshold": _f(_first(m, "final.threshold") if _first(m, "final.threshold") is not None
                        else p.get("threshold")),
        "exit_margin": _f(_first(m, "final.exit_margin")),
        "trainer_checklist": m.get("checklist"),
    }


def model_strategy(row: dict, bundle=None) -> dict:
    """{strategy_name, signal_tf, htf_tf} the model was trained on (bundle dataset_meta, else the registry params);
    missing keys are None."""
    meta = (bundle.get("dataset_meta") if isinstance(bundle, dict) else None) or {}
    p = _loads(row.get("params_json"), {}) or {}
    out = {"strategy_name": meta.get("strategy_name") or p.get("base_strategy"),
           "signal_tf": meta.get("signal_tf") or p.get("signal_tf"),
           "htf_tf": meta.get("htf_tf") or p.get("htf_tf")}
    return {k: (str(v).upper() if k != "strategy_name" and v else (str(v) if v else None)) for k, v in out.items()}


class LoadedModel:
    """A verified shadow/approved model: registry row + bundle (dict from app.ml.registry.load)."""

    def __init__(self, row: dict, bundle) -> None:
        self.row = row
        self.id = row["id"]
        self.kind = row["kind"]
        self.status = row["status"]
        self.bundle = bundle
        b = bundle if isinstance(bundle, dict) else {}
        self.threshold = _f(b.get("threshold")) or 0.0  # 0.0 = take every candidate (no veto)
        self.p_hi = _f(b.get("p_hi"))
        m = _f(b.get("exit_margin"))
        self.exit_margin = abs(m) if m is not None else None  # None = the model never closes early
        self.trained_on = model_strategy(row, bundle)
        self.overridden = False  # approved with a checklist override (ml_approvals.override = 1)


# ---- the live glue -------------------------------------------------------
class MLLive:
    def __init__(self, engine) -> None:
        self.engine = engine
        self.db = engine.db
        self.settings = engine.settings
        self.models: dict[str, LoadedModel] = {}
        self._mods: dict[str, object] = {}
        self._mod_missing: set[str] = set()
        self._warned: set[str] = set()
        self._pool: ThreadPoolExecutor | None = None
        self._futures: set[Future] = set()
        self._fut_lock = threading.Lock()
        self._queue: deque = deque(maxlen=MAX_QUEUE)
        self._q_lock = threading.Lock()
        self._last_refresh = 0.0
        self._last_flush = 0.0
        self._last_prune = 0.0
        self._last_exit_ts = 0
        self._exit_cache: dict[tuple, dict] = {}
        self._refresh_lock = threading.Lock()
        self._retrain_lock = threading.Lock()
        self.retrain_running = False
        self.retrain_thread: threading.Thread | None = None
        self.dropped_jobs = 0
        self.has_detail = False
        self.tables_ok = False
        # injectable (tests / alternative implementations); default = app.ml
        self.entry_featurizer = None  # callable(ctx) -> dict
        self.exit_featurizer = None  # callable(ctx) -> dict
        self.loader = None  # callable(row) -> bundle (artifact verified)
        self.entry_scorer = None  # callable(bundle, feats, symbol) -> P
        self.exit_scorer = None  # callable(bundle, feats) -> remaining R
        self.retrain_runner = None  # callable(cmd, env, timeout) -> (returncode, output)

    # ---- setup -------------------------------------------------------------
    def startup(self) -> None:
        if not self.ensure_tables():
            return
        try:
            self.refresh_models()
        except Exception as e:
            self._warn_once("refresh", f"modele ML neîncărcate: {e!r}")
        if self.models:
            self.engine.rt.log("INFO", "ml", "Modele ML active: " + ", ".join(
                f"{m.kind}:{m.id}({m.status})" for m in self.models.values()))

    def ensure_tables(self) -> bool:
        """Additive ML tables/columns (also used by the CLI approve, which has no engine)."""
        try:
            with self.db.lock:
                self.db.conn.executescript(FALLBACK_TABLES + TABLES)
                for table, col, typ in ADDED_COLUMNS:
                    cols = {r[1] for r in self.db.conn.execute(f"PRAGMA table_info({table})").fetchall()}
                    if cols and col not in cols:
                        self.db.conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
                cols = {r[1] for r in self.db.conn.execute("PRAGMA table_info(ml_decisions)").fetchall()}
            self.has_detail = "detail_json" in cols
            self.tables_ok = True
        except sqlite3.Error as e:
            self._warn_once("tables", f"tabele ML necreate: {e!r}")
            return False
        return True

    def stop(self) -> None:
        try:
            self.drain(timeout=5.0)
        except Exception:
            pass
        if self._pool is not None:
            self._pool.shutdown(wait=False, cancel_futures=True)
            self._pool = None

    def _warn_once(self, key: str, msg: str, level: str = "WARN") -> None:
        if key not in self._warned:
            self._warned.add(key)
            try:
                self.engine.rt.log(level, "ml", msg)
            except Exception:
                pass

    def _mod(self, name: str):
        """app.ml.<name> imported lazily; None (logged once) when missing or broken."""
        if name in self._mods:
            return self._mods[name]
        if name in self._mod_missing:
            return None
        try:
            mod = importlib.import_module(f"app.ml.{name}")
        except Exception as e:
            self._mod_missing.add(name)
            self._warn_once(f"mod:{name}", f"modulul app.ml.{name} indisponibil — funcția ML aferentă este inactivă ({e!r})",
                            level="INFO")
            return None
        self._mods[name] = mod
        return mod

    @staticmethod
    def ml_available() -> bool:
        try:
            return importlib.util.find_spec("app.ml") is not None
        except (ImportError, ValueError):
            return False

    # ---- settings ----------------------------------------------------------
    def runtime_overrides(self) -> dict:
        try:
            v = self.db.get_setting("ml", {})
        except sqlite3.Error:
            v = {}
        return v if isinstance(v, dict) else {}

    @property
    def enabled(self) -> bool:
        o = self.runtime_overrides()
        return bool(o["enabled"]) if isinstance(o.get("enabled"), bool) else bool(self.settings.ML_ENABLED)

    def size_bounds(self) -> tuple[float, float]:
        lo = _clamp(float(self.settings.ML_SIZE_MIN), 0.1, 1.0)
        hi_raw = _f(self.runtime_overrides().get("size_max"))
        hi = float(hi_raw if hi_raw is not None else self.settings.ML_SIZE_MAX)
        return lo, _clamp(hi, lo, SIZE_FACTOR_HARD_MAX)

    def settings_public(self) -> dict:
        lo, hi = self.size_bounds()
        return {"enabled": self.enabled, "size_min": lo, "size_max": hi, "size_max_limit": SIZE_FACTOR_HARD_MAX,
                "max_risk_pct_cap": float(self.settings.ML_MAX_RISK_PCT),
                "retrain_enabled": bool(self.settings.ML_RETRAIN_ENABLED), "retrain_at": self.settings.ML_RETRAIN_AT,
                "retrain_tz": self.settings.DEFAULT_TIMEZONE, "ml_package": self.ml_available()}

    def update_settings(self, upd: dict) -> dict:
        cur = self.runtime_overrides()
        cur.update(upd)
        self.db.set_setting("ml", cur)
        return self.settings_public()

    # ---- models ------------------------------------------------------------
    def model_rows(self, statuses: tuple[str, ...] | None = None) -> list[dict]:
        try:
            if statuses:
                q = ",".join("?" * len(statuses))
                return self.db.query(f"SELECT * FROM ml_models WHERE status IN ({q}) ORDER BY created_at DESC, id DESC",
                                     statuses)
            return self.db.query("SELECT * FROM ml_models ORDER BY created_at DESC, id DESC")
        except sqlite3.Error:
            return []

    def model_row(self, model_id: str) -> dict | None:
        try:
            return self.db.query_one("SELECT * FROM ml_models WHERE id=?", (model_id,))
        except sqlite3.Error:
            return None

    def load_bundle(self, row: dict):
        """sha256-verified bundle (app.ml.registry.load); raises when missing/tampered."""
        if self.loader is not None:
            return self.loader(row)
        reg = self._mod("registry")
        if reg is None:
            raise RuntimeError("app.ml.registry indisponibil")
        return reg.load(self.db, row["id"])

    def refresh_models(self) -> None:
        """(Re)load every shadow/approved model; one that fails verification is skipped (logged once)."""
        with self._refresh_lock:
            new: dict[str, LoadedModel] = {}
            for r in self.model_rows(ACTIVE_STATUSES):
                if r.get("kind") not in KINDS:
                    continue
                old = self.models.get(r["id"])
                if old is not None and old.row.get("sha256") == r.get("sha256"):
                    new[r["id"]] = LoadedModel(r, old.bundle)  # status may have changed
                    continue
                try:
                    new[r["id"]] = LoadedModel(r, self.load_bundle(r))
                except Exception as e:
                    lvl = "ERROR" if r.get("status") == "approved" else "WARN"
                    self._warn_once(f"load:{r['id']}:{r.get('sha256')}", f"model {r['id']} neîncărcat: {e!r}", level=lvl)
            self._mark_overrides(new)
            self.models = new
            self._last_refresh = time.time()

    def _mark_overrides(self, models: dict) -> None:
        ids = [mid for mid, m in models.items() if m.status == "approved"]
        if not ids:
            return
        try:
            q = ",".join("?" * len(ids))
            rows = self.db.query(f"SELECT model_id, override FROM ml_approvals WHERE action='approve' AND model_id IN ({q}) "
                                 "ORDER BY id ASC", tuple(ids))
        except sqlite3.Error:
            return
        for r in rows:  # the latest approval of each model wins
            models[r["model_id"]].overridden = bool(r["override"])

    def live_strategy(self) -> dict | None:
        """{strategy_name, signal_tf, htf_tf} the engine trades right now (None when unknown, e.g. the CLI)."""
        eng = self.engine
        try:
            st = eng.rt.strategy
            name = eng.active_strategy_name() if hasattr(eng, "active_strategy_name") else st.get("name")
            return {"strategy_name": str(name) if name else None,
                    "signal_tf": str(st.get("signal_timeframe") or "").upper() or None,
                    "htf_tf": str(st.get("htf_timeframe") or "").upper() or None}
        except Exception:
            return None

    @staticmethod
    def strategy_diff(trained: dict, live: dict | None) -> list[str]:
        """Keys whose training value is known and differs from the live one (empty = compatible / unknown)."""
        if not live:
            return []
        return [k for k in ("strategy_name", "signal_tf", "htf_tf")
                if trained.get(k) and live.get(k) and trained[k] != live[k]]

    def _strategy_ok(self, m: LoadedModel) -> bool:
        """An approved model acts only on the strategy/timeframes its OOS evidence came from; otherwise base behaviour."""
        live = self.live_strategy()
        bad = self.strategy_diff(m.trained_on, live)
        if not bad:
            return True
        tr = "/".join(str(m.trained_on.get(k)) for k in ("strategy_name", "signal_tf", "htf_tf"))
        lv = "/".join(str((live or {}).get(k)) for k in ("strategy_name", "signal_tf", "htf_tf"))
        self._warn_once(f"strat_mismatch:{m.id}:{lv}",
                        f"model ML aprobat {m.id} antrenat pe {tr}, dar botul rulează {lv} — modelul este ignorat "
                        "(comportament de bază) până la un model antrenat pe strategia activă")
        return False

    def approved(self, kind: str) -> LoadedModel | None:
        for m in list(self.models.values()):
            if m.kind == kind and m.status == "approved":
                return m
        return None

    def active(self, kind: str) -> list[LoadedModel]:
        return [m for m in list(self.models.values()) if m.kind == kind]

    # ---- features / scoring (app.ml.features + app.ml.infer) -----------------
    def compute_features(self, kind: str, ctx: dict) -> dict | None:
        try:
            if kind == "entry":
                if self.entry_featurizer is not None:
                    out = self.entry_featurizer(ctx)
                else:
                    F = self._mod("features")
                    if F is None:
                        return None
                    out = F.entry_features(ctx["sig_bars"], ctx["htf_bars"], ctx["quote"], ctx["candidate"])
            else:
                if self.exit_featurizer is not None:
                    out = self.exit_featurizer(ctx)
                else:
                    F = self._mod("features")
                    if F is None:
                        return None
                    out = F.exit_features(ctx["sig_bars"], ctx["pos"])
        except Exception as e:
            self._warn_once(f"feat_err:{kind}:{type(e).__name__}", f"caracteristici ML {kind} eșuate: {e!r}")
            return None
        return out if isinstance(out, dict) else None

    def _hash(self, feats: dict) -> str:
        F = self._mods.get("features")
        fn = getattr(F, "features_hash", None) if F is not None else None
        if callable(fn):
            try:
                return fn(feats)
            except Exception:
                pass
        return features_hash(feats)

    def score(self, model: LoadedModel, feats: dict, symbol: str | None = None) -> float | None:
        if model.kind == "entry":
            if self.entry_scorer is not None:
                return _f(self.entry_scorer(model.bundle, feats, symbol))
            infer = self._mod("infer")
            if infer is None:
                raise RuntimeError("app.ml.infer indisponibil")
            return _f(infer.score_entry(model.bundle, feats, symbol))
        if self.exit_scorer is not None:
            return _f(self.exit_scorer(model.bundle, feats))
        infer = self._mod("infer")
        if infer is None:
            raise RuntimeError("app.ml.infer indisponibil")
        return _f(infer.score_exit(model.bundle, feats))

    def size_factor(self, p: float, model: LoadedModel) -> float:
        """Linear from ML_SIZE_MIN at the threshold to ML_SIZE_MAX at the validation P90 (bundle p_hi)."""
        lo, hi = self.size_bounds()
        if getattr(model, "overridden", False):
            lo, hi = min(lo, OVERRIDE_SIZE_MAX), min(hi, OVERRIDE_SIZE_MAX)
        thr = model.threshold
        p_hi = max(model.p_hi if model.p_hi is not None else thr, thr)
        frac = 1.0 if p_hi <= thr else _clamp((p - thr) / (p_hi - thr), 0.0, 1.0)
        return round(_clamp(lo + (hi - lo) * frac, lo, hi), 4)

    # ---- context builders (closed bars only: no lookahead) --------------------
    def _bars_until(self, symbol: str, tf: str, until_t: int | None, n: int) -> list[dict]:
        bars = self.engine.rt.closed_bars(symbol, tf)
        if until_t is not None:
            bars = [b for b in bars if b["t"] <= until_t]
        return [dict(b) for b in bars[-n:]]

    def entry_context(self, symbol: str, side: str, bar_time: int | None, entry, sl, tp, score,
                      quote: dict | None) -> dict:
        st = self.engine.rt.strategy
        return {
            "symbol": symbol, "bar_time": bar_time,
            "sig_bars": self._bars_until(symbol, st["signal_timeframe"], bar_time, BARS_FOR_FEATURES),
            "htf_bars": self._bars_until(symbol, st["htf_timeframe"], bar_time, HTF_BARS_FOR_FEATURES),
            "quote": dict(quote or {}),
            "candidate": {"side": side, "entry": _f(entry), "stop_loss": _f(sl), "take_profit": _f(tp), "score": _f(score)},
        }

    def exit_context(self, source: str, pos: dict, path_state: dict, now: float) -> dict:
        rt = self.engine.rt
        sym = pos["symbol"]
        q = dict(rt.quotes.get(sym) or {})
        side = path_state["side"]
        sign = 1 if side == "BUY" else -1
        price = _f(q.get("bid") if sign > 0 else q.get("ask"))
        risk, entry = float(path_state["risk"]), float(path_state["entry"])
        r_now = (price - entry) * sign / risk if price is not None and risk > 0 else None
        return {
            "symbol": sym, "source": source, "ticket": int(pos["ticket"]), "r_now": r_now,
            "sig_bars": self._bars_until(sym, rt.strategy["signal_timeframe"], None, BARS_FOR_FEATURES),
            "pos": {"side": side, "entry": entry, "sl0": entry - sign * risk, "sl": _f(pos.get("sl")),
                    "tp": _f(pos.get("tp")), "opened_ts": _f(path_state.get("opened_ts") or pos.get("opened_ts")),
                    "mfe_r": float(path_state.get("mfe") or 0.0), "mae_r": float(path_state.get("mae") or 0.0),
                    "price_now": price, "spread_points": _f(q.get("spread_points")), "point": _f(q.get("point")),
                    "now_ts": float(now)},
        }

    # ---- ML thread / batched writes ----------------------------------------------
    def _submit(self, fn, *args) -> bool:
        with self._fut_lock:
            self._futures = {f for f in self._futures if not f.done()}
            if len(self._futures) >= MAX_PENDING_JOBS:
                self.dropped_jobs += 1
                return False
            if self._pool is None:
                self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ml-shadow")
            self._futures.add(self._pool.submit(self._guard, fn, *args))
            return True

    def _guard(self, fn, *args) -> None:
        try:
            fn(*args)
        except Exception as e:
            self._warn_once(f"job:{getattr(fn, '__name__', fn)}:{type(e).__name__}", f"job ML eșuat: {e!r}")

    def _enqueue(self, row: tuple) -> None:
        with self._q_lock:
            self._queue.append(row)

    def flush(self) -> int:
        with self._q_lock:
            rows = list(self._queue)
            self._queue.clear()
        if not rows or not self.tables_ok:
            return 0
        try:
            if self.has_detail:
                self.db.executemany("INSERT INTO ml_decisions(model_id, ts, kind, symbol, ref_id, features_hash, score, "
                                    "would_action, actual_action, detail_json) VALUES(?,?,?,?,?,?,?,?,?,?)", rows)
            else:
                self.db.executemany("INSERT INTO ml_decisions(model_id, ts, kind, symbol, ref_id, features_hash, score, "
                                    "would_action, actual_action) VALUES(?,?,?,?,?,?,?,?,?)", [r[:9] for r in rows])
        except sqlite3.Error as e:
            self._warn_once("flush", f"decizii ML nesalvate: {e!r}")
            return 0
        return len(rows)

    def prune(self) -> int:
        cap = int(self.settings.ML_DECISIONS_MAX_ROWS)
        try:
            r = self.db.query_one("SELECT MAX(id) AS m FROM ml_decisions")
            if not r or r["m"] is None or r["m"] <= cap:
                return 0
            return self.db.execute("DELETE FROM ml_decisions WHERE id <= ?", (int(r["m"]) - cap,)).rowcount
        except sqlite3.Error as e:
            self._warn_once("prune", f"curățare ml_decisions eșuată: {e!r}")
            return 0

    def drain(self, timeout: float = 10.0) -> None:
        """Wait for queued ML jobs, then write the pending rows (tests / shutdown)."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self._fut_lock:
                pending = [f for f in self._futures if not f.done()]
            if not pending:
                break
            try:
                pending[0].result(timeout=max(0.01, deadline - time.time()))
            except Exception:
                pass
        self.flush()

    # ---- entry: shadow logging ---------------------------------------------------
    def on_analysis(self, a: dict) -> None:
        """Every analysed bar with a BUY/SELL candidate (taken or blocked by context): score each shadow/approved entry
        model in the ML thread and log one row per model. Never mutates `a`, never changes the decision."""
        if not self.tables_ok or not self.enabled:
            return
        models = self.active("entry")
        if not models:
            return
        cand = a.get("candidate") or {}
        side = cand.get("side") or (a.get("direction") if a.get("direction") in ("BUY", "SELL") else None)
        if side not in ("BUY", "SELL") or a.get("bar_time") is None:
            return
        actual = "SIGNAL" if a.get("direction") in ("BUY", "SELL") else "BLOCKED"
        ctx = self.entry_context(a["symbol"], side, int(a["bar_time"]), cand.get("entry", a.get("entry")),
                                 cand.get("stop_loss", a.get("stop_loss")), cand.get("take_profit", a.get("take_profit")),
                                 cand.get("score", a.get("score")), self.engine.rt.quotes.get(a["symbol"]))
        self._submit(self._entry_job, ctx, models, f"{a.get('timeframe')}|{int(a['bar_time'])}", actual)

    def _entry_job(self, ctx: dict, models: list[LoadedModel], ref: str, actual: str) -> None:
        feats = self.compute_features("entry", ctx)
        if feats is None:
            return
        fh = self._hash(feats)
        now = time.time()
        for m in models:
            try:
                p = self.score(m, feats, ctx["symbol"])
            except Exception as e:
                self._warn_once(f"score:{m.id}", f"scor model {m.id} eșuat: {e!r}")
                continue
            if p is None:
                continue
            would = "SKIP" if p < m.threshold else "TAKE"
            self._enqueue((m.id, now, "entry", ctx["symbol"], ref, fh, round(p, 6), would, actual,
                           json.dumps({"side": ctx["candidate"]["side"], "threshold": m.threshold, "status": m.status})))

    # ---- entry: approved gate ----------------------------------------------------
    def entry_gate(self, sig: dict) -> dict | None:
        """Only an APPROVED entry model acts. None = no change (no approved model, ML off, or any failure: the base
        strategy's behaviour). Otherwise {veto, size_factor, p, threshold, model_id}."""
        if not self.enabled:
            return None
        m = self.approved("entry")
        if m is None or not self._strategy_ok(m):
            return None
        score = sig.get("score")
        if score is None and sig.get("bar_time") is not None:
            try:
                r = self.db.query_one("SELECT score FROM analyses WHERE symbol=? AND tf=? AND bar_time=?",
                                      (sig["symbol"], sig.get("timeframe"), int(sig["bar_time"])))
                score = (r or {}).get("score")
            except sqlite3.Error:
                score = None
        ctx = self.entry_context(sig["symbol"], sig["direction"], sig.get("bar_time"), sig.get("entry"),
                                 sig.get("stop_loss"), sig.get("take_profit"), score, self.engine.rt.quotes.get(sig["symbol"]))
        feats = self.compute_features("entry", ctx)
        if feats is None:
            self._warn_once("gate_nofeat", "model ML aprobat: caracteristici indisponibile — intrarea continuă fără ML")
            return None
        try:
            p = self.score(m, feats, sig["symbol"])
        except Exception as e:
            self._warn_once(f"gate_score:{m.id}", f"model ML aprobat {m.id}: scor eșuat ({e!r}) — intrarea continuă fără ML")
            return None
        if p is None:
            return None
        veto = p < m.threshold
        factor = 0.0 if veto else self.size_factor(p, m)
        self._enqueue((m.id, time.time(), "entry_gate", sig["symbol"], sig.get("id"), self._hash(feats), round(p, 6),
                       "SKIP" if veto else "TAKE", "VETO" if veto else "TAKE",
                       json.dumps({"size_factor": factor, "threshold": m.threshold})))
        return {"veto": veto, "size_factor": factor, "p": round(p, 4), "threshold": m.threshold, "model_id": m.id}

    # ---- exit: shadow logging + approved early close ------------------------------
    def _exit_tick(self, now: float) -> None:
        ts = int(now // EXIT_EVERY_S * EXIT_EVERY_S)
        if ts == self._last_exit_ts:
            return
        self._last_exit_ts = ts
        models = [m for m in self.active("exit") if m.status != "approved"]  # the approved one logs in exit_action
        if not models:
            return
        eng = self.engine
        ctxs = []
        for source, pos in eng._path_positions():
            st = eng._paths.get((source, str(pos["ticket"])))
            if st:
                ctxs.append(self.exit_context(source, pos, st, now))
        if ctxs:
            self._submit(self._exit_job, ctxs, models, ts)

    def _exit_job(self, ctxs: list[dict], models: list[LoadedModel], ts: int) -> None:
        for ctx in ctxs:
            if ctx.get("r_now") is None:
                continue
            feats = self.compute_features("exit", ctx)
            if feats is None:
                continue
            fh = self._hash(feats)
            for m in models:
                try:
                    rem = self.score(m, feats)
                except Exception as e:
                    self._warn_once(f"score:{m.id}", f"scor model {m.id} eșuat: {e!r}")
                    continue
                if rem is None:
                    continue
                would = "CLOSE" if m.exit_margin is not None and rem < -m.exit_margin else "HOLD"
                self._enqueue((m.id, float(ts), "exit", ctx["symbol"], f"{ctx['source']}:{ctx['ticket']}", fh,
                               round(rem, 6), would, "HOLD",
                               json.dumps({"r_now": round(ctx["r_now"], 4), "margin": m.exit_margin, "status": m.status})))

    def exit_action(self, source: str, pos: dict, now: float) -> dict | None:
        """Approved exit model only: {'type': 'CLOSE', 'reason': 'ML_EXIT'} or None. At most one evaluation per position
        per minute; it can only ever ask for a CLOSE (never an SL/TP change, never an add)."""
        if not self.enabled:
            return None
        m = self.approved("exit")
        if m is None or m.exit_margin is None or not self._strategy_ok(m):
            return None
        st = self.engine._paths.get((source, str(pos["ticket"])))
        if not st:
            return None
        minute = int(now // EXIT_EVERY_S)
        key = (source, int(pos["ticket"]), m.id)
        hit = self._exit_cache.get(key)
        if hit is not None and hit["minute"] == minute:
            return hit["action"]
        ctx = self.exit_context(source, pos, st, now)
        action, rem = None, None
        if ctx.get("r_now") is not None:
            feats = self.compute_features("exit", ctx)
            if feats is not None:
                try:
                    rem = self.score(m, feats)
                except Exception as e:
                    self._warn_once(f"score:{m.id}", f"model ML de ieșire {m.id}: scor eșuat ({e!r})")
                    rem = None
                if rem is not None:
                    close = rem < -m.exit_margin
                    action = {"type": "CLOSE", "reason": "ML_EXIT"} if close else None
                    self._enqueue((m.id, float(minute * EXIT_EVERY_S), "exit", ctx["symbol"], f"{source}:{pos['ticket']}",
                                   self._hash(feats), round(rem, 6), "CLOSE" if close else "HOLD",
                                   "ML_EXIT" if close else "HOLD",
                                   json.dumps({"r_now": round(ctx["r_now"], 4), "margin": m.exit_margin,
                                               "status": m.status})))
        self._exit_cache[key] = {"minute": minute, "action": action, "remaining_r": rem}
        if len(self._exit_cache) > 500:
            for k in list(self._exit_cache)[:250]:
                self._exit_cache.pop(k, None)
        return action

    # ---- loop tick (from the engine's 1 s loop) -----------------------------------
    def tick(self, now: float) -> None:
        if not self.tables_ok:
            return
        if now - self._last_refresh >= REFRESH_EVERY_S:
            self._last_refresh = now
            self._submit(self.refresh_models)
        if self.enabled:
            try:
                self._exit_tick(now)
            except Exception as e:
                self._warn_once(f"exit_tick:{type(e).__name__}", f"stare ieșire ML: {e!r}")
        if now - self._last_flush >= FLUSH_EVERY_S:
            self._last_flush = now
            with self._q_lock:
                has = bool(self._queue)
            if has:
                self._submit(self.flush)
        if now - self._last_prune >= PRUNE_EVERY_S:
            self._last_prune = now
            self._submit(self.prune)
        try:
            self.nightly_check(now)
        except Exception as e:
            self._warn_once(f"nightly:{type(e).__name__}", f"planificare reantrenare: {e!r}")

    # ---- nightly retrain ----------------------------------------------------------
    def _local_now(self, now: float) -> datetime:
        try:
            tz = ZoneInfo(self.settings.DEFAULT_TIMEZONE or "Europe/Chisinau")
        except Exception:
            tz = ZoneInfo("UTC")
        return datetime.fromtimestamp(now, timezone.utc).astimezone(tz)

    def train_available(self) -> bool:
        if self.retrain_runner is not None:
            return True
        try:
            return importlib.util.find_spec("app.ml.train") is not None
        except (ImportError, ValueError):
            return False

    def nightly_check(self, now: float) -> bool:
        """Once per local day at/after ML_RETRAIN_AT: start a retrain (single flight)."""
        if not self.settings.ML_RETRAIN_ENABLED:
            return False
        try:
            hh, mm = (int(x) for x in str(self.settings.ML_RETRAIN_AT).strip().split(":")[:2])
        except ValueError:
            self._warn_once("retrain_at", f"ML_RETRAIN_AT invalid: {self.settings.ML_RETRAIN_AT!r}")
            return False
        if now - float(getattr(self.engine.rt, "started_ts", 0) or 0) < RETRAIN_MIN_UPTIME_S:
            return False
        local = self._local_now(now)
        if (local.hour, local.minute) < (hh, mm):
            return False
        day = local.date().isoformat()
        if self.db.get_setting("ml_retrain_day") == day:
            return False
        self.db.set_setting("ml_retrain_day", day)
        if not self.train_available():
            self._warn_once("retrain_missing", "reantrenare nocturnă sărită: modulul app.ml.train lipsește", level="INFO")
            return False
        return bool(self.start_retrain("nightly").get("started"))

    def retrain_status(self) -> dict:
        last = None
        try:
            last = self.db.query_one("SELECT * FROM ml_retrain_runs ORDER BY id DESC LIMIT 1")
        except sqlite3.Error:
            pass
        if last:
            last["imported"] = _loads(last.pop("imported_json", None), [])
            last["started_utc"] = iso(last["started_ts"])
            last["finished_utc"] = iso(last["finished_ts"]) if last.get("finished_ts") else None
        return {"running": self.retrain_running, "last": last, "at": self.settings.ML_RETRAIN_AT,
                "enabled": bool(self.settings.ML_RETRAIN_ENABLED), "available": self.train_available()}

    def start_retrain(self, trigger: str = "manual") -> dict:
        """Single flight: {'started': False, 'reason': ...} while another run is in progress."""
        with self._retrain_lock:
            if self.retrain_running:
                return {"started": False, "reason": "O reantrenare este deja în curs"}
            if not self.train_available():
                return {"started": False, "reason": "Modulul app.ml.train lipsește"}
            self.retrain_running = True
        try:
            cur = self.db.execute("INSERT INTO ml_retrain_runs(trigger, started_ts, status) VALUES(?,?,?)",
                                  (trigger, time.time(), "RUNNING"))
            run_id = int(cur.lastrowid)
        except sqlite3.Error as e:
            with self._retrain_lock:
                self.retrain_running = False
            return {"started": False, "reason": f"eroare DB: {e!r}"}
        self.engine.rt.log("INFO", "ml", f"Reantrenare ML pornită ({trigger}) — subproces separat, prioritate redusă")
        t = threading.Thread(target=self._retrain_job, args=(run_id, trigger), name="ml-retrain", daemon=True)
        self.retrain_thread = t
        t.start()
        return {"started": True, "run_id": run_id}

    def _snapshot(self, dest: Path) -> None:
        """Consistent copy of the live DB with the sqlite backup API (WAL: writers are not blocked)."""
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.unlink(missing_ok=True)
        src = sqlite3.connect(self.db.path, timeout=30)
        try:
            dst = sqlite3.connect(str(dest))
            try:
                src.backup(dst, pages=4096, sleep=0.005)
            finally:
                dst.close()
        finally:
            src.close()

    def models_dir(self) -> Path:
        p = Path(self.settings.ML_MODELS_DIR)
        return p if p.is_absolute() else ROOT_DIR / p

    def retrain_command(self, snap: Path) -> list[str]:
        args = str(self.settings.ML_RETRAIN_ARGS or "").format(db=str(snap), models_dir=str(self.models_dir()))
        cmd = [sys.executable, "-m", "app.ml", *shlex.split(args)]
        if sys.platform != "win32":
            cmd = ["nice", "-n", "10", *cmd]
        return cmd

    def _run_subprocess(self, cmd: list[str], env: dict, timeout: float) -> tuple[int, str]:
        if self.retrain_runner is not None:
            return self.retrain_runner(cmd, env, timeout)
        p = subprocess.run(cmd, cwd=str(ROOT_DIR), env=env, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout or "") + (p.stderr or "")

    def _retrain_job(self, run_id: int, trigger: str) -> None:
        status, rc, out, imported, shadowed = "ERROR", None, "", [], []
        snap = Path(self.settings.db_path).parent / "ml" / f"snapshot-{run_id}.db"
        try:
            self._snapshot(snap)
            env = dict(os.environ)
            for k in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "LOKY_MAX_CPU_COUNT", "ML_MAX_WORKERS"):
                env[k] = "3"
            rc, out = self._run_subprocess(self.retrain_command(snap), env, float(self.settings.ML_RETRAIN_TIMEOUT_MIN) * 60)
            if rc == 0:
                imported = self.import_candidates(snap)
                shadowed = self.promote_newest_to_shadow()
                self.refresh_models()
                status = "OK"
            else:
                status = "FAILED"
        except subprocess.TimeoutExpired:
            status, out = "TIMEOUT", f"depășit {self.settings.ML_RETRAIN_TIMEOUT_MIN:g} min"
        except Exception as e:
            status, out = "ERROR", (str(out) + "\n" + repr(e)).strip()
        finally:
            for suffix in ("", "-wal", "-shm", "-journal"):
                try:
                    Path(str(snap) + suffix).unlink(missing_ok=True)
                except OSError:
                    pass
            try:
                self.db.execute("UPDATE ml_retrain_runs SET finished_ts=?, status=?, returncode=?, imported_json=?, "
                                "log_tail=? WHERE id=?",
                                (time.time(), status, rc, json.dumps(imported), (out or "")[-4000:], run_id))
            except sqlite3.Error:
                pass
            msg = f"Reantrenare ML ({trigger}) terminată: {status}"
            if imported:
                msg += f"; candidați noi: {', '.join(imported)}"
            if shadowed:
                msg += f"; în umbră: {', '.join(shadowed)} (nu tranzacționează până la aprobare manuală)"
            if status != "OK":
                msg += f" — {(out or '')[-300:]}"
            self.engine.rt.log("INFO" if status == "OK" else "WARN", "ml", msg)
            try:
                if self.retrain_runner is None:  # a real `python -m app.ml train` run wrote into ML_MODELS_DIR
                    self.prune_retrain_files()
            except Exception as e:
                self._warn_once("prune_files", f"curățare fișiere reantrenare: {e!r}", level="INFO")
            with self._retrain_lock:
                self.retrain_running = False

    def prune_retrain_files(self) -> list[str]:
        """Old retrain datasets/summaries in ML_MODELS_DIR (never a file a registered model points to)."""
        d = self.models_dir()
        if not d.is_dir():
            return []
        used = {os.path.realpath(r["artifact_path"]) for r in self.model_rows() if r.get("artifact_path")}
        removed = []
        for pattern, keep in (("ds-*.joblib", KEEP_DATASETS), ("summary-*.json", KEEP_SUMMARIES)):
            files = sorted(d.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
            for p in files[keep:]:
                if os.path.realpath(p) in used:
                    continue
                p.unlink(missing_ok=True)
                removed.append(p.name)
        return removed

    def import_candidates(self, snap: Path) -> list[str]:
        """Model rows the training run registered in the snapshot -> live registry, always as 'candidate' (artifact
        must exist and match its sha256)."""
        src = sqlite3.connect(str(snap))
        src.row_factory = sqlite3.Row
        try:
            try:
                rows = [dict(r) for r in src.execute("SELECT * FROM ml_models").fetchall()]
            except sqlite3.Error:
                return []
        finally:
            src.close()
        reg = self._mod("registry")
        out = []
        for r in rows:
            if r.get("kind") not in KINDS or self.model_row(r["id"]) is not None:
                continue
            try:
                if reg is not None and self.loader is None:
                    reg.register_record(self.db, r)
                else:
                    if self.loader is None:
                        p = r.get("artifact_path")
                        if not p or not os.path.isfile(p) or file_sha256(p) != r.get("sha256"):
                            raise ValueError("artefact lipsă sau sha256 diferit")
                    self.db.execute(
                        "INSERT INTO ml_models(id, kind, name, version, feature_schema, dataset_version, params_json, "
                        "metrics_json, trained_until_utc, artifact_path, sha256, status, created_at, notes) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?,?,'candidate',?,?)",
                        (r["id"], r["kind"], r.get("name") or r["id"], r.get("version"), r.get("feature_schema") or "",
                         r.get("dataset_version"), r.get("params_json") or "{}", r.get("metrics_json") or "{}",
                         r.get("trained_until_utc"), r.get("artifact_path") or "", r.get("sha256") or "",
                         r.get("created_at") or iso(time.time()), r.get("notes")))
                out.append(r["id"])
            except Exception as e:
                self._warn_once(f"import:{r['id']}", f"model {r['id']} neimportat: {e!r}")
        return out

    def promote_newest_to_shadow(self) -> list[str]:
        """Newest candidate of each kind -> shadow, older shadows -> archived. NEVER approves."""
        reg = self._mod("registry")
        moved = []
        for kind in KINDS:
            if reg is not None:
                mid = reg.promote_newest_candidate(self.db, kind)
            else:
                c = self.db.query_one("SELECT id FROM ml_models WHERE kind=? AND status='candidate' "
                                      "ORDER BY created_at DESC, id DESC LIMIT 1", (kind,))
                mid = c["id"] if c else None
                if mid:
                    self.db.execute("UPDATE ml_models SET status='archived' WHERE kind=? AND status='shadow' AND id<>?",
                                    (kind, mid))
                    self.db.execute("UPDATE ml_models SET status='shadow' WHERE id=? AND status='candidate'", (mid,))
            if mid:
                moved.append(mid)
        return moved

    # ---- shadow statistics ----------------------------------------------------------
    def shadow_stats(self, model_id: str, kind: str) -> dict:
        try:
            agg = self.db.query_one("SELECT MIN(ts) AS t0, COUNT(*) AS n FROM ml_decisions WHERE model_id=? AND kind=?",
                                    (model_id, kind)) or {}
        except sqlite3.Error:
            agg = {}
        n, t0 = int(agg.get("n") or 0), agg.get("t0")
        days = (time.time() - float(t0)) / 86400.0 if t0 else 0.0
        out = {"n_decisions": n, "first_utc": iso(t0) if t0 else None, "days": round(days, 2)}
        out.update(self._entry_shadow(model_id) if kind == "entry" else self._exit_shadow(model_id))
        return out

    def _entry_shadow(self, model_id: str) -> dict:
        """Realized outcome (labeler, analyses.outcome_r, net R) of every candidate the model scored live."""
        try:
            rows = self.db.query(
                "SELECT d.would_action AS would, a.outcome_r AS r FROM ml_decisions d JOIN analyses a "
                "ON a.symbol = d.symbol AND (a.tf || '|' || a.bar_time) = d.ref_id "
                "WHERE d.model_id=? AND d.kind='entry' AND a.outcome_r IS NOT NULL ORDER BY d.id DESC LIMIT 20000",
                (model_id,))
        except sqlite3.Error:
            rows = []
        take = [float(r["r"]) for r in rows if r["would"] == "TAKE"]
        skip = [float(r["r"]) for r in rows if r["would"] != "TAKE"]
        allr = take + skip

        def avg(xs):
            return round(sum(xs) / len(xs), 4) if xs else None
        edge = round(avg(take) - avg(allr), 4) if take and allr else None
        return {"n_labeled": len(allr), "take": {"n": len(take), "avg_r": avg(take)},
                "skip": {"n": len(skip), "avg_r": avg(skip)}, "all": {"n": len(allr), "avg_r": avg(allr)},
                "edge_r": edge,
                "consistent": edge is not None and edge > 0 and (avg(take) or 0) > 0 and len(allr) >= CHK_SHADOW_MIN_LABELED}

    def _exit_shadow(self, model_id: str) -> dict:
        """For each trade the model would have closed: R at its first CLOSE call minus the trade's realized R."""
        if not self.has_detail:
            return {"n_trades": 0, "would_close_trades": 0, "avg_gain_r": None, "edge_r": None, "consistent": False}
        try:
            rows = self.db.query("SELECT ref_id, detail_json FROM ml_decisions WHERE model_id=? AND kind='exit' AND "
                                 "would_action='CLOSE' ORDER BY ts ASC, id ASC LIMIT 20000", (model_id,))
        except sqlite3.Error:
            rows = []
        first: dict[str, float] = {}
        for r in rows:
            if r["ref_id"] in first:
                continue
            v = _f(_loads(r["detail_json"], {}).get("r_now"))
            if v is not None:
                first[r["ref_id"]] = v
        gains = []
        for ref, r_now in first.items():
            try:
                ticket = int(str(ref).split(":", 1)[1])
                t = self.db.query_one("SELECT r_multiple FROM trades WHERE ticket=? AND r_multiple IS NOT NULL "
                                      "ORDER BY id DESC LIMIT 1", (ticket,))
            except (ValueError, IndexError, sqlite3.Error):
                continue
            if t and t.get("r_multiple") is not None:
                gains.append(r_now - float(t["r_multiple"]))
        avg = round(sum(gains) / len(gains), 4) if gains else None
        return {"n_trades": len(gains), "would_close_trades": len(first), "avg_gain_r": avg, "edge_r": avg,
                "consistent": avg is not None and avg > 0 and len(gains) >= CHK_SHADOW_MIN_TRADES}

    # ---- promotion checklist ---------------------------------------------------------
    def checklist(self, row: dict, shadow: dict | None = None, bundle=None) -> dict:
        s = model_summary(row)
        kind = row.get("kind")
        shadow = shadow if shadow is not None else self.shadow_stats(row["id"], kind)
        items = []

        def add(key, label, ok, value, need):
            items.append({"key": key, "label": label, "ok": bool(ok), "value": value, "need": need})
        n_ok = s["n"] is not None and s["n"] >= CHK_MIN_OOS_N
        add("oos_avg_r", f"OOS: R mediu > 0 după costuri, pe ≥ {CHK_MIN_OOS_N} tranzacții de test",
            s["avg_r"] is not None and s["avg_r"] > 0 and n_ok, s["avg_r"] if n_ok else {"avg_r": s["avg_r"], "n": s["n"]},
            f"> 0 (n ≥ {CHK_MIN_OOS_N})")
        lo = s["ci90"][0] if s["ci90"] else None
        add("oos_ci_lo", f"OOS: limita inferioară IC90% > {CHK_CI_LO_MIN:g}", lo is not None and lo > CHK_CI_LO_MIN, lo,
            f"> {CHK_CI_LO_MIN:g}")
        diff = None if s["avg_r"] is None or s["baseline_avg_r"] is None else round(s["avg_r"] - s["baseline_avg_r"], 4)
        add("beats_baseline", "OOS: bate baseline-ul (" + ("ia tot" if kind == "entry" else "ieșirile de bază")
            + f") cu > {CHK_BEAT_MIN:g}R", diff is not None and diff > CHK_BEAT_MIN, diff, f"> {CHK_BEAT_MIN:g}")
        if kind == "entry":
            add("calibration", f"Calibrare: ECE < {CHK_ECE_MAX:g}", s["ece"] is not None and s["ece"] < CHK_ECE_MAX,
                s["ece"], f"< {CHK_ECE_MAX:g}")
        else:
            add("calibration", f"Calibrare: eroare medie pe intervale < {CHK_EXIT_CALIB_MAX:g}R",
                s["calib_mae"] is not None and s["calib_mae"] < CHK_EXIT_CALIB_MAX, s["calib_mae"], f"< {CHK_EXIT_CALIB_MAX:g}")
        enough = shadow.get("days", 0) >= CHK_SHADOW_DAYS or shadow.get("n_decisions", 0) >= CHK_SHADOW_N
        add("shadow_period", f"Umbră: ≥ {CHK_SHADOW_DAYS} zile sau ≥ {CHK_SHADOW_N} decizii", enough,
            {"days": shadow.get("days"), "n": shadow.get("n_decisions")}, f"≥ {CHK_SHADOW_DAYS} zile / ≥ {CHK_SHADOW_N}")
        add("shadow_consistent", "Umbră: rezultat live cu același semn (avantaj > 0)", bool(shadow.get("consistent")),
            shadow.get("edge_r"), "> 0")
        add("status_shadow", "Modelul este în umbră (status shadow)", row.get("status") == "shadow", row.get("status"),
            "shadow")
        trained = model_strategy(row, bundle)
        live = self.live_strategy()
        strat = {"key": "strategy_match", "label": "Antrenat pe strategia și timeframe-urile active",
                 "ok": not self.strategy_diff(trained, live), "value": {"antrenat": trained, "activ": live},
                 "need": "aceeași strategie / TF"}
        hard = [i for i in items + [strat] if not i["ok"] and i["key"] in HARD_ITEMS]
        return {"passed": all(i["ok"] for i in items) and strat["ok"], "items": items, "strategy": strat,
                "hard_failed": [i["key"] for i in hard], "hard_labels": [i["label"] for i in hard]}

    # ---- approval / rollback -----------------------------------------------------------
    def approve(self, model_id: str, actor: str = APPROVER, override: bool = False, notes: str = "") -> dict:
        """Explicit manual approval only. Raises LookupError (404), ValueError (409), PermissionError (checklist failed
        without override; message = checklist JSON)."""
        row = self.model_row(model_id)
        if row is None:
            raise LookupError("Model inexistent")
        if row["status"] != "shadow":
            raise ValueError(f"Doar un model în umbră (shadow) poate fi aprobat — starea actuală: {row['status']}")
        try:
            bundle = self.load_bundle(row)  # the artifact must verify before it can act on trading
        except Exception as e:
            raise ValueError(f"Artefactul nu poate fi verificat/încărcat: {e!r}")
        chk = self.checklist(row, bundle=bundle)
        # status + strategy match can never be overridden; an overridden model is capped at size factor 1.0
        if not chk["passed"] and (not override or chk["hard_failed"]):
            raise PermissionError(json.dumps(chk, ensure_ascii=False))
        prev = self.db.query_one("SELECT id FROM ml_models WHERE kind=? AND status='approved'", (row["kind"],))
        note = ("OVERRIDE checklist; " if not chk["passed"] else "") + (notes or "")
        reg = self._mod("registry")
        if reg is not None and self.loader is None:
            reg.approve(self.db, model_id, actor, note.strip() or None)
        else:
            now_iso = iso(time.time())
            if prev:
                self.db.execute("UPDATE ml_models SET status='archived' WHERE id=?", (prev["id"],))
            self.db.execute("UPDATE ml_models SET status='approved', approved_by=?, approved_at=? WHERE id=? AND status='shadow'",
                            (actor, now_iso, model_id))
        self.db.execute("INSERT INTO ml_approvals(model_id, kind, action, actor, ts, prev_model_id, checklist_json, "
                        "override, notes) VALUES(?,?,?,?,?,?,?,?,?)",
                        (model_id, row["kind"], "approve", actor, time.time(), prev["id"] if prev else None,
                         json.dumps(chk, ensure_ascii=False), 0 if chk["passed"] else 1, notes or None))
        if not chk["passed"]:
            self.engine.rt.log("WARN", "ml", f"Model ML {model_id} aprobat cu OVERRIDE ({actor}): checklist nepromovat — "
                                             + ", ".join(i["key"] for i in chk["items"] if not i["ok"]))
        self.engine.rt.log("WARN", "ml", f"Model ML de {'intrare' if row['kind'] == 'entry' else 'ieșire'} {model_id} "
                                         f"APROBAT de {actor}" + (f" (înlocuiește {prev['id']})" if prev else ""))
        with self._refresh_lock:
            fresh = self.model_row(model_id)
            lm = LoadedModel(fresh, bundle)
            lm.overridden = not chk["passed"]
            self.models[model_id] = lm
            if prev:
                self.models.pop(prev["id"], None)
        return {"model": self.model_row(model_id), "checklist": chk, "previous": prev["id"] if prev else None}

    def retire(self, model_id: str, actor: str = APPROVER) -> dict:
        """Withdraw a model; when it was the approved one, the previously approved model of the same kind comes back
        (rollback) — or none."""
        row = self.model_row(model_id)
        if row is None:
            raise LookupError("Model inexistent")
        if row["status"] == "archived":
            raise ValueError("Modelul este deja retras (arhivat)")
        was_approved = row["status"] == "approved"
        reg = self._mod("registry")
        restored = None
        if reg is not None and self.loader is None:
            restored = reg.retire(self.db, model_id, by=actor, restore_previous=True).get("restored")
        else:
            self.db.execute("UPDATE ml_models SET status='archived' WHERE id=?", (model_id,))
            if was_approved:
                ap = self.db.query_one("SELECT prev_model_id FROM ml_approvals WHERE model_id=? AND action='approve' "
                                       "ORDER BY id DESC LIMIT 1", (model_id,))
                prev = self.model_row((ap or {}).get("prev_model_id") or "")
                if prev and prev["status"] == "archived":
                    try:
                        self.load_bundle(prev)
                        self.db.execute("UPDATE ml_models SET status='approved' WHERE id=?", (prev["id"],))
                        restored = prev["id"]
                    except Exception as e:
                        self._warn_once(f"restore:{prev['id']}", f"modelul anterior {prev['id']} nu poate fi restaurat: {e!r}")
        self.db.execute("INSERT INTO ml_approvals(model_id, kind, action, actor, ts, prev_model_id) VALUES(?,?,?,?,?,?)",
                        (model_id, row["kind"], "retire", actor, time.time(), restored))
        self.engine.rt.log("WARN", "ml", f"Model ML {model_id} retras de {actor}"
                                         + (f"; restaurat modelul aprobat anterior {restored}" if restored else
                                            ("; niciun model aprobat activ — comportament de bază" if was_approved else "")))
        with self._refresh_lock:
            self.models.pop(model_id, None)
        self.refresh_models()
        return {"model": self.model_row(model_id), "restored": restored}

    def approvals(self, model_id: str) -> list[dict]:
        try:
            rows = self.db.query("SELECT * FROM ml_approvals WHERE model_id=? OR prev_model_id=? ORDER BY id DESC LIMIT 50",
                                 (model_id, model_id))
        except sqlite3.Error:
            return []
        for r in rows:
            r["checklist"] = _loads(r.pop("checklist_json", None), None)
            r["ts_utc"] = iso(r["ts"])
        return rows

    # ---- API payloads --------------------------------------------------------------------
    def public_row(self, row: dict, full: bool = False) -> dict:
        s = model_summary(row)
        out = {k: row.get(k) for k in ("id", "kind", "name", "version", "feature_schema", "dataset_version",
                                       "trained_until_utc", "status", "created_at", "approved_by", "approved_at", "sha256")}
        out["loaded"] = row["id"] in self.models
        out["oos"] = s if full else {k: v for k, v in s.items() if k not in ("calibration", "trainer_checklist")}
        if full:
            out["params"] = _loads(row.get("params_json"), {})
            out["metrics"] = _loads(row.get("metrics_json"), {})
            out["artifact_path"] = row.get("artifact_path")
            out["notes"] = row.get("notes")
        return out

    def list_payload(self) -> dict:
        models = []
        for r in self.model_rows():
            p = self.public_row(r)
            p["shadow"] = None
            if r["status"] in ACTIVE_STATUSES:
                try:
                    sh = self.shadow_stats(r["id"], r["kind"])
                    p["shadow"] = {k: sh.get(k) for k in ("n_decisions", "days", "edge_r", "consistent")}
                except Exception:
                    pass
            models.append(p)
        return {"settings": self.settings_public(),
                "active": {k: (self.approved(k).id if self.approved(k) else None) for k in KINDS},
                "retrain": self.retrain_status(), "dropped_jobs": self.dropped_jobs, "models": models}

    def detail_payload(self, model_id: str) -> dict | None:
        row = self.model_row(model_id)
        if row is None:
            return None
        out = self.public_row(row, full=True)
        sh = self.shadow_stats(row["id"], row["kind"])
        out["shadow"] = sh
        lm = self.models.get(model_id)
        out["checklist"] = self.checklist(row, sh, bundle=lm.bundle if lm is not None else None)
        out["approvals"] = self.approvals(model_id)
        return out
