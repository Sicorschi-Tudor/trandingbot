"""research/run_bt.py with the FULL trade list (params all_trades=True) — needed for complete by_year / hour-of-day
diagnostics; aggregate metrics are identical to run_bt.py. No edit to the shared runner."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "research"))
import run_bt  # noqa: E402
from app import backtest as bt  # noqa: E402

_run = bt.run


def run_full(db, params):
    params = dict(params)
    params["all_trades"] = True
    return _run(db, params)


run_bt.bt.run = run_full
raise SystemExit(run_bt.main())
