#!/bin/bash
# Re-run of the SAME four VALID configurations only to export the trade list (--trades-out) for the by-year table
# (app.backtest keeps the last 300 trades in the result JSON). Deterministic: the summaries must match the first run.
cd /home/user/trandingbot
PY=/tmp/claude-0/venv/bin/python
RISK='{"max_open_positions": 4, "max_trades_per_day": 20, "max_same_currency_direction": 2, "max_spread_points": 60, "max_daily_loss_pct": 100, "max_consecutive_losses": 1000}'
BSTD=research/results/F5/bundle_breakout_taken_cov25_hgb.joblib; BECN=research/results/F5/bundle_ecn_breakout_taken_cov25_hgb.joblib
run() { name=$1; db=$2; strat=$3; sjson=$4
  $PY research/run_bt.py --db $db --strategy $strat --tf H1 --htf H4 --split valid --strategy-json "$sjson" --risk-json "$RISK" \
     --exit base --exit-params '{"max_hold_hours": 24}' --workers 1 --json-out research/results/F5/byyear/$name.json --trades-out research/results/F5/byyear/$name.trades.jsonl 2>/dev/null
}
mkdir -p research/results/F5/byyear
run valid_std_ml_breakout-v1 data/research.db disc-f5-ml "{\"rescue_enabled\": false, \"ml_base\": \"breakout-v1\", \"ml_bundle\": \"$BSTD\"}"
run valid_ecn_ml_breakout-v1 data/research_ecn.db disc-f5-ml "{\"rescue_enabled\": false, \"ml_base\": \"breakout-v1\", \"ml_bundle\": \"$BECN\"}"
run valid_std_base_breakout-v1 data/research.db breakout-v1 '{"rescue_enabled": false}'
run valid_ecn_base_breakout-v1 data/research_ecn.db breakout-v1 '{"rescue_enabled": false}'
echo DONE
