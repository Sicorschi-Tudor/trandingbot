#!/bin/bash
# Stage F / F5 grid on TRAIN (STANDARD): walk-forward OOS inside TRAIN per (base, threshold coverage rule); each run
# reports the HGB+isotonic and the LogisticRegression filter (2 configurations) and, with --exit, the xs-v1 exit model.
cd /home/user/trandingbot
PY=/tmp/claude-0/venv/bin/python
LOG=research/results/F5/grid_log.jsonl
run() { tag=$1; ds=$2; cov=$3; extra=$4
  echo "=== $(date -u +%FT%TZ) train $tag cov=$cov $extra" >&2
  t0=$(date +%s)
  out=$($PY research/f5_ml.py train --dataset research/results/F5/$ds.joblib --tag $tag --min-cov $cov $extra 2>research/results/F5/train_$tag.err | tail -1)
  echo "{\"tag\":\"$tag\",\"dataset\":\"$ds\",\"min_cov\":$cov,\"elapsed_s\":$(( $(date +%s)-t0 )),\"result\":$out}" >> $LOG
  echo "$out" >&2
}
run trend_cov25 ds_trend_std 0.25 --exit
run trend_cov10 ds_trend_std 0.10
run trend_cov50 ds_trend_std 0.50
