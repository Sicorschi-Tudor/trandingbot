#!/bin/bash
cd /home/user/trandingbot
PY=/tmp/claude-0/venv/bin/python
for spec in "trend-v1 data/research.db ds_trend_std" "breakout-v1 data/research.db ds_breakout_std" "trend-v1 data/research_ecn.db ds_trend_ecn" "breakout-v1 data/research_ecn.db ds_breakout_ecn"; do
  set -- $spec
  echo "=== $(date -u +%FT%TZ) build $1 $2 -> $3" 
  $PY research/f5_ml.py build --db $2 --base $1 --workers 2 --out research/results/F5/$3.joblib 2>&1 | tail -5
done
echo "=== $(date -u +%FT%TZ) DONE"
