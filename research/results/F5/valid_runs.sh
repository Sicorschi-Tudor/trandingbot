#!/bin/bash
# Stage F / F5: the single VALID runner runs of the chosen configuration (base baseline + ML-filtered policy), both DBs.
# usage: valid_runs.sh <base> <bundle_std.joblib> <bundle_ecn.joblib>
cd /home/user/trandingbot
PY=/tmp/claude-0/venv/bin/python
BASE=$1; BSTD=$2; BECN=$3
RISK='{"max_open_positions": 4, "max_trades_per_day": 20, "max_same_currency_direction": 2, "max_spread_points": 60, "max_daily_loss_pct": 100, "max_consecutive_losses": 1000}'
run() { name=$1; db=$2; strat=$3; sjson=$4
  echo "=== $(date -u +%FT%TZ) $name" >&2
  $PY research/run_bt.py --db $db --strategy $strat --tf H1 --htf H4 --split valid --strategy-json "$sjson" --risk-json "$RISK" \
     --exit base --exit-params '{"max_hold_hours": 24}' --workers 1 --json-out research/results/F5/$name.json 2>research/results/F5/$name.err | tee -a research/results/F5/valid_runner_log.jsonl
}
run valid_std_base_$BASE data/research.db $BASE '{"rescue_enabled": false}'
run valid_std_ml_$BASE data/research.db disc-f5-ml "{\"rescue_enabled\": false, \"ml_base\": \"$BASE\", \"ml_bundle\": \"$BSTD\"}"
run valid_ecn_base_$BASE data/research_ecn.db $BASE '{"rescue_enabled": false}'
run valid_ecn_ml_$BASE data/research_ecn.db disc-f5-ml "{\"rescue_enabled\": false, \"ml_base\": \"$BASE\", \"ml_bundle\": \"$BECN\"}"
echo "=== $(date -u +%FT%TZ) DONE" >&2
