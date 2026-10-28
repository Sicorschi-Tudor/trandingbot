"""Model registry (table ml_models) + artifacts (joblib) with sha256 verification.

Lifecycle: candidate -> shadow -> approved -> archived (and candidate/shadow -> archived).
- register(): a freshly trained model is ALWAYS a 'candidate' (never trades, never scored live).
- to_shadow(): candidate -> shadow (scored live and logged in ml_decisions, NEVER affects trading).
- approve(): shadow -> approved, ONLY through this explicit function with a non-empty approver string; the
  previously approved model of the same kind is archived (one approved model per kind). Nothing calls it
  automatically (no auto-promotion): the API/CLI call it after a human confirmation.
- retire(): approved/shadow/candidate -> archived; with restore_previous=True the previously approved model of the
  same kind (archived, approved_at set) comes back as approved (rollback) — it was approved by a human before.
load() refuses an artifact whose sha256 differs from the registry (tampered or replaced file).
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from ..config import ROOT_DIR, iso

STATUSES = ("candidate", "shadow", "approved", "archived")
KINDS = ("entry", "exit")
TRANSITIONS = {"candidate": {"shadow", "archived"}, "shadow": {"approved", "archived"},
               "approved": {"archived"}, "archived": set()}
DEFAULT_DIR = ROOT_DIR / "data" / "models"


class RegistryError(ValueError):
    pass


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _now() -> str:
    return iso(datetime.now(timezone.utc))


def _row(db, model_id: str) -> dict:
    r = db.query_one("SELECT * FROM ml_models WHERE id=?", (model_id,))
    if not r:
        raise RegistryError(f"model inexistent: {model_id}")
    return r


def get(db, model_id: str) -> dict | None:
    r = db.query_one("SELECT * FROM ml_models WHERE id=?", (model_id,))
    return _decode(r) if r else None


def _decode(r: dict) -> dict:
    out = dict(r)
    for k in ("params_json", "metrics_json"):
        try:
            out[k[:-5]] = json.loads(r.get(k) or "null")
        except ValueError:
            out[k[:-5]] = None
    return out


def list_models(db, kind: str | None = None, status: str | None = None) -> list[dict]:
    sql, args = "SELECT * FROM ml_models WHERE 1=1", []
    if kind:
        sql += " AND kind=?"
        args.append(kind)
    if status:
        sql += " AND status=?"
        args.append(status)
    return [_decode(r) for r in db.query(sql + " ORDER BY created_at DESC, id DESC", args)]


def save_artifact(bundle: dict, out_dir: str | Path, model_id: str) -> tuple[str, str]:
    import joblib
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{model_id}.joblib"
    tmp = path.with_suffix(".joblib.tmp")
    joblib.dump(bundle, tmp, compress=3)
    os.replace(tmp, path)
    return str(path), sha256_file(path)


def make_record(bundle: dict, *, kind: str, name: str, version: str, feature_schema: str,
                dataset_version: str | None, params: dict | None, metrics: dict | None,
                trained_until_ts: float | None, out_dir: str | Path | None = None, notes: str | None = None,
                created_ts: float | None = None, taken_ids: set | None = None) -> dict:
    """Save the artifact and return the registry record (not inserted): a training subprocess on a DB snapshot
    returns these, the live process inserts them with register_record()."""
    if kind not in KINDS:
        raise RegistryError(f"kind invalid: {kind}")
    ts = datetime.fromtimestamp(created_ts, timezone.utc) if created_ts else datetime.now(timezone.utc)
    stamp = ts.strftime("%Y%m%d%H%M%S")
    digest = hashlib.sha1(json.dumps([kind, name, version, dataset_version, clean_json(params)], sort_keys=True,
                                     default=str).encode()).hexdigest()[:6]
    model_id = f"{kind}-{name}-{stamp}-{digest}"
    n = 1
    while taken_ids and model_id in taken_ids:
        n += 1
        model_id = f"{kind}-{name}-{stamp}-{digest}-{n}"
    path, sha = save_artifact(bundle, out_dir or DEFAULT_DIR, model_id)
    return {"id": model_id, "kind": kind, "name": name, "version": version, "feature_schema": feature_schema,
            "dataset_version": dataset_version, "params_json": json.dumps(clean_json(params or {}), default=str),
            "metrics_json": json.dumps(clean_json(metrics or {}), default=_json_default),
            "trained_until_utc": iso(trained_until_ts) if trained_until_ts else None, "artifact_path": path,
            "sha256": sha, "status": "candidate", "created_at": iso(ts), "notes": notes}


def register(db, bundle: dict, **kw) -> str:
    """Save the artifact and insert a 'candidate' row (make_record + register_record). Returns the model id."""
    taken = {r["id"] for r in db.query("SELECT id FROM ml_models")}
    return register_record(db, make_record(bundle, taken_ids=taken, **kw))


def register_record(db, rec: dict) -> str:
    """Insert a row produced elsewhere (e.g. a training subprocess on a DB snapshot): the artifact must exist and
    match rec['sha256']. Always inserted as 'candidate'."""
    path = rec["artifact_path"]
    if not os.path.isfile(path) or sha256_file(path) != rec["sha256"]:
        raise RegistryError(f"artefact lipsă sau sha256 diferit: {path}")
    if db.query_one("SELECT id FROM ml_models WHERE id=?", (rec["id"],)):
        raise RegistryError(f"model existent: {rec['id']}")
    db.execute(
        "INSERT INTO ml_models(id, kind, name, version, feature_schema, dataset_version, params_json, metrics_json, "
        "trained_until_utc, artifact_path, sha256, status, created_at, notes) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (rec["id"], rec["kind"], rec["name"], rec.get("version"), rec["feature_schema"], rec.get("dataset_version"),
         rec.get("params_json") or "{}", rec.get("metrics_json") or "{}", rec.get("trained_until_utc"), path,
         rec["sha256"], "candidate", rec.get("created_at") or _now(), rec.get("notes")))
    return rec["id"]


def clean_json(o):
    """Recursively: numpy scalars/arrays -> python, NaN/inf -> None (valid JSON for the UI)."""
    import math
    try:
        import numpy as np
        if isinstance(o, np.ndarray):
            o = o.tolist()
        elif isinstance(o, np.generic):
            o = o.item()
    except ImportError:
        pass
    if isinstance(o, float):
        return o if math.isfinite(o) else None
    if isinstance(o, dict):
        return {str(k): clean_json(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [clean_json(v) for v in o]
    return o


def _json_default(o):
    try:
        import numpy as np
        if isinstance(o, np.generic):
            return o.item()
        if isinstance(o, np.ndarray):
            return o.tolist()
    except ImportError:
        pass
    if isinstance(o, float):
        return None
    return str(o)


def load(db, model_id: str) -> dict:
    """The model bundle, after checking the artifact's sha256 against the registry."""
    import joblib
    r = _row(db, model_id)
    path = r["artifact_path"]
    if not os.path.isfile(path):
        raise RegistryError(f"artefact lipsă: {path}")
    sha = sha256_file(path)
    if sha != r["sha256"]:
        raise RegistryError(f"sha256 diferit pentru {model_id}: artefactul a fost modificat")
    bundle = joblib.load(path)
    bundle["_model_id"] = model_id
    bundle["_sha256"] = sha
    return bundle


def _transition(db, model_id: str, new: str, note: str | None = None, **fields) -> dict:
    r = _row(db, model_id)
    old = r["status"]
    if new not in TRANSITIONS.get(old, set()):
        raise RegistryError(f"tranziție nepermisă {old} -> {new} pentru {model_id}")
    sets, args = ["status=?"], [new]
    for k, v in fields.items():
        sets.append(f"{k}=?")
        args.append(v)
    if note:
        sets.append("notes=?")
        args.append(((r.get("notes") or "") + ("\n" if r.get("notes") else "") + note)[-4000:])
    cur = db.execute(f"UPDATE ml_models SET {', '.join(sets)} WHERE id=? AND status=?", (*args, model_id, old))
    if getattr(cur, "rowcount", 1) == 0:
        raise RegistryError(f"starea modelului {model_id} s-a schimbat între timp")
    return _row(db, model_id)


def to_shadow(db, model_id: str, archive_others: bool = True) -> dict:
    """candidate -> shadow; by default the other shadow models of the same kind are archived."""
    r = _row(db, model_id)
    if archive_others:
        for o in db.query("SELECT id FROM ml_models WHERE kind=? AND status='shadow' AND id<>?", (r["kind"], model_id)):
            _transition(db, o["id"], "archived", f"{_now()} arhivat: înlocuit în shadow de {model_id}")
    return _transition(db, model_id, "shadow", f"{_now()} trecut în shadow")


def promote_newest_candidate(db, kind: str) -> str | None:
    """Nightly retrain: newest candidate of a kind -> shadow (older shadows archived). NEVER approves."""
    r = db.query_one("SELECT id FROM ml_models WHERE kind=? AND status='candidate' ORDER BY created_at DESC, id DESC "
                     "LIMIT 1", (kind,))
    if not r:
        return None
    to_shadow(db, r["id"])
    return r["id"]


def approve(db, model_id: str, approved_by: str, note: str | None = None) -> dict:
    """shadow -> approved (explicit human decision). The previous approved model of the same kind is archived."""
    who = (approved_by or "").strip() if isinstance(approved_by, str) else ""
    if not who:
        raise RegistryError("aprobarea necesită numele celui care aprobă")
    r = _row(db, model_id)
    if r["status"] != "shadow":
        raise RegistryError(f"doar un model în shadow poate fi aprobat (starea: {r['status']})")
    load(db, model_id)  # the artifact must exist and verify before it can trade
    for o in db.query("SELECT id FROM ml_models WHERE kind=? AND status='approved'", (r["kind"],)):
        _transition(db, o["id"], "archived", f"{_now()} arhivat: înlocuit de {model_id} (aprobat de {who})")
    return _transition(db, model_id, "approved", f"{_now()} aprobat de {who}" + (f": {note}" if note else ""),
                       approved_by=who, approved_at=_now())


def retire(db, model_id: str, by: str | None = None, restore_previous: bool = False) -> dict:
    """Archive a model (rollback). restore_previous: the most recent previously approved model of the same kind
    goes back to approved. Returns {"retired": id, "restored": id|None}."""
    r = _row(db, model_id)
    _transition(db, model_id, "archived", f"{_now()} retras" + (f" de {by}" if by else ""))
    restored = None
    if restore_previous and r["status"] == "approved":
        prev = db.query_one(
            "SELECT id FROM ml_models WHERE kind=? AND status='archived' AND approved_at IS NOT NULL AND id<>? "
            "AND approved_at <= ? ORDER BY approved_at DESC LIMIT 1", (r["kind"], model_id, r["approved_at"] or _now()))
        if prev:
            load(db, prev["id"])
            db.execute("UPDATE ml_models SET status='approved', notes=COALESCE(notes,'') || ? WHERE id=? AND status='archived'",
                       (f"\n{_now()} restaurat (rollback după retragerea {model_id}" + (f" de {by}" if by else "") + ")",
                        prev["id"]))
            restored = prev["id"]
    return {"retired": model_id, "restored": restored}


def active(db, kind: str | None = None) -> list[dict]:
    """Models scored live: status shadow or approved."""
    sql = "SELECT * FROM ml_models WHERE status IN ('shadow','approved')"
    args: list = []
    if kind:
        sql += " AND kind=?"
        args.append(kind)
    return [_decode(r) for r in db.query(sql + " ORDER BY kind, status, created_at DESC", args)]
