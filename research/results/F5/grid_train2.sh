#!/bin/bash
cd /home/user/trandingbot
PY=/tmp/claude-0/venv/bin/python
LOG=research/results/F5/grid_log.jsonl
run() { tag=$1; ds=$2; cov=$3; extra=$4
  echo "=== $(date -u +%FT%TZ) train $tag cov=$cov $extra" >&2
  t0=$(date +%s)
  out=$($PY research/f5_ml.py train --dataset research/results/F5/$ds.joblib --tag $tag --min-cov $cov $extra 2>research/results/F5/train_$tag.err | tail -1)
  echo "{\"tag\":\"$tag\",\"dataset\":\"$ds\",\"min_cov\":$cov,\"extra\":\"$extra\",\"elapsed_s\":$(( $(date +%s)-t0 )),\"result\":$out}" >> $LOG
  echo "$out" >&2
}
# wait for the trend grid (part 1) and the breakout dataset
# (trend grid part 1 already finished)
run trend_taken_cov25 ds_trend_std 0.25 --taken-only
while [ ! -f research/results/F5/ds_breakout_std.meta.json ]; do sleep 10; done
run breakout_cov25 ds_breakout_std 0.25 --exit
run breakout_cov10 ds_breakout_std 0.10
run breakout_cov50 ds_breakout_std 0.50
run breakout_taken_cov25 ds_breakout_std 0.25 --taken-only
echo "=== $(date -u +%FT%TZ) DONE" >&2
