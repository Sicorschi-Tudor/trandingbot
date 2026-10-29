#!/bin/bash
# Same four VALID configurations on the two calendar-year sub-intervals of VALID (fixed sub-intervals of the split, not an
# optimisation): per-year n / avg R / CI90 / PF for Gate 1 ("avg R > 0 in at least one year"), because app.backtest keeps
# only the last 300 trades in the result JSON and in --trades-out.
cd /home/user/trandingbot
PY=/tmp/claude-0/venv/bin/python
RISK='{"max_open_positions": 4, "max_trades_per_day": 20, "max_same_currency_direction": 2, "max_spread_points": 60, "max_daily_loss_pct": 100, "max_consecutive_losses": 1000}'
BSTD=research/results/F5/bundle_breakout_taken_cov25_hgb.joblib; BECN=research/results/F5/bundle_ecn_breakout_taken_cov25_hgb.joblib
run() { name=$1; db=$2; strat=$3; sjson=$4; y0=$5; y1=$6
  $PY research/run_bt.py --db $db --strategy $strat --tf H1 --htf H4 --start $y0-01-01 --end $y1-01-01 --strategy-json "$sjson" --risk-json "$RISK" \
     --exit base --exit-params '{"max_hold_hours": 24}' --workers 1 --json-out research/results/F5/byyear/${name}_$y0.json 2>/dev/null
}
for y in 2019 2020; do y1=$((y+1))
run valid_std_base_breakout-v1 data/research.db breakout-v1 '{"rescue_enabled": false}' $y $y1
run valid_std_ml_breakout-v1 data/research.db disc-f5-ml "{\"rescue_enabled\": false, \"ml_base\": \"breakout-v1\", \"ml_bundle\": \"$BSTD\"}" $y $y1
run valid_ecn_base_breakout-v1 data/research_ecn.db breakout-v1 '{"rescue_enabled": false}' $y $y1
run valid_ecn_ml_breakout-v1 data/research_ecn.db disc-f5-ml "{\"rescue_enabled\": false, \"ml_base\": \"breakout-v1\", \"ml_bundle\": \"$BECN\"}" $y $y1
done
echo DONE
