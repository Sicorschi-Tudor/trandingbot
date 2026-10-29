#!/bin/bash
# GOLD/trend — grila pre-inregistrata (20 configuratii, scrisa INAINTE de rulare), XAUUSD singur, TRAIN, STANDARD.
# Toate: --htf NONE --swap-mode table --workers 2, risc = limitele de cont dezactivate (vezi SUMMARY).
set -u
cd /home/user/trandingbot
PY=/tmp/claude-0/venv/bin/python
OUT=research/results/GOLD/trend
RISK='{"max_open_positions": 1, "max_trades_per_day": 20, "max_spread_points": 100, "max_daily_loss_pct": 100, "max_consecutive_losses": 1000, "cooldown_after_loss_minutes": 0, "rollover_blackout_minutes": 0, "max_total_open_risk_pct": 100}'
SPLIT=${SPLIT:-train}
DB=${DB:-data/research.db}
PFX=${PFX:-train}
run() { # id strategy tf strategy_json [exit exit_params]
  local id=$1 strat=$2 tf=$3 sj=$4 ex=${5:-} ep=${6:-}
  local extra=()
  [ -n "$ex" ] && extra=(--exit "$ex" --exit-params "$ep")
  echo "### $id $strat $tf $sj ${ex:+exit=$ex $ep}" | tee -a $OUT/_runlog.txt
  $PY research/run_bt.py --db $DB --strategy $strat --tf $tf --htf NONE --symbols XAUUSD --split $SPLIT \
     --swap-mode table --workers 2 --strategy-json "$sj" --risk-json "$RISK" "${extra[@]}" \
     --json-out $OUT/${PFX}_${id}.json | tee -a $OUT/_runlog.txt
}
# donchian-v1 (7)
run D01_donchian_H4_e20_sl3_ch10   donchian-v1 H4 '{"entry_n": 20, "sl_atr": 3.0}'  channel '{"channel_n": 10}'
run D03_donchian_H4_e55_sl3_ch20   donchian-v1 H4 '{"entry_n": 55, "sl_atr": 3.0}'
[ "$SPLIT" = train ] && [ -f $OUT/train_D04_donchian_H4_e100_sl3_ch20.json ] || run D04_donchian_H4_e100_sl3_ch20 donchian-v1 H4 '{"entry_n": 100, "sl_atr": 3.0}'
run D05_donchian_H4_e100_sl3_trail donchian-v1 H4 '{"entry_n": 100, "sl_atr": 3.0}' trailing '{"trail_start_r": 1.0, "trail_atr_mult": 3.0, "trail_min_r": 0.0}'
run D06_donchian_D1_e20_sl3_ch10   donchian-v1 D1 '{"entry_n": 20, "sl_atr": 3.0}'  channel '{"channel_n": 10}'
run D07_donchian_D1_e55_sl3_ch20   donchian-v1 D1 '{"entry_n": 55, "sl_atr": 3.0}'
run D08_donchian_D1_e100_sl3_ch20  donchian-v1 D1 '{"entry_n": 100, "sl_atr": 3.0}'
# ma-trend-v1 (4)
run M01_matrend_H4_ema50_100       ma-trend-v1 H4 '{"ema_fast_n": 50, "ema_slow_n": 100, "sl_atr": 3.0, "trail_atr": 3.0}'
run M02_matrend_H4_ema50_200       ma-trend-v1 H4 '{"ema_fast_n": 50, "ema_slow_n": 200, "sl_atr": 3.0, "trail_atr": 3.0}'
run M03_matrend_D1_ema50_100       ma-trend-v1 D1 '{"ema_fast_n": 50, "ema_slow_n": 100, "sl_atr": 3.0, "trail_atr": 3.0}'
run M04_matrend_D1_ema20_100       ma-trend-v1 D1 '{"ema_fast_n": 20, "ema_slow_n": 100, "sl_atr": 3.0, "trail_atr": 3.0}'
# tsmom-v1 (5)
run T01_tsmom_H4_lb63_z05          tsmom-v1 H4 '{"lookback": 63, "min_z": 0.5, "sl_atr": 3.0}'
run T02_tsmom_H4_lb126_z0          tsmom-v1 H4 '{"lookback": 126, "min_z": 0.0, "sl_atr": 3.0}'
run T03_tsmom_H4_lb252_z05         tsmom-v1 H4 '{"lookback": 252, "min_z": 0.5, "sl_atr": 3.0}'
run T04_tsmom_D1_lb126_z05         tsmom-v1 D1 '{"lookback": 126, "min_z": 0.5, "sl_atr": 3.0}'
run T05_tsmom_D1_lb63_z0           tsmom-v1 D1 '{"lookback": 63, "min_z": 0.0, "sl_atr": 3.0}'
# disc-f2-tsmom2 (2)
run E01_tsmom2_H4_k2               disc-f2-tsmom2 H4 '{"k": 2}'
run E02_tsmom2_H4_k3               disc-f2-tsmom2 H4 '{"k": 3}'
# disc-h06 (2)
run H01_h06_H4_c06                 disc-h06 H4 '{"c": 0.6}'
run H02_h06_H4_c07                 disc-h06 H4 '{"c": 0.7}'
