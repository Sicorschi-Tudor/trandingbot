#!/bin/bash
# GOLD/sessions — grila pre-inregistrata, XAUUSD singur, TRAIN, STANDARD. Runda 1 = cele 10 ipoteze de sesiune /
# ora din zi la RUN_DEFAULTS-ul fiecarui modul (TF/HTF din modul; ieșirea = default_exit al pluginului, emulari
# ca in F3 §1 pentru hook-urile time_exit nearmate de app.backtest). Runda 2 (max 10 vecini ai celei mai bune
# configuratii eligibile) se adauga la sfarsit DUPA runda 1, declarata inainte de a o rula.
set -u
cd /home/user/trandingbot
PY=/tmp/claude-0/venv/bin/python
OUT=research/results/GOLD/sessions
RISK='{"max_open_positions": 1, "max_trades_per_day": 20, "max_spread_points": 100, "max_daily_loss_pct": 100, "max_consecutive_losses": 1000, "cooldown_after_loss_minutes": 0, "rollover_blackout_minutes": 0, "max_total_open_risk_pct": 100}'
SPLIT=${SPLIT:-train}
DB=${DB:-data/research.db}
PFX=${PFX:-train}
run() { # id strategy tf htf strategy_json [session_json] [exit exit_params]
  local id=$1 strat=$2 tf=$3 htf=$4 sj=$5 sess=${6:-} ex=${7:-} ep=${8:-}
  local extra=()
  [ -n "$sess" ] && extra+=(--session-json "$sess")
  [ -n "$ex" ] && extra+=(--exit "$ex" --exit-params "$ep")
  echo "### $id $strat $tf/$htf $sj ${sess:+sess=$sess} ${ex:+exit=$ex $ep}" | tee -a $OUT/_runlog.txt
  $PY $OUT/_run_full.py --db $DB --strategy $strat --tf $tf --htf $htf --symbols XAUUSD --split $SPLIT \
     --swap-mode table --workers 2 --strategy-json "$sj" --risk-json "$RISK" "${extra[@]}" \
     --json-out $OUT/${PFX}_${id}.json --trades-out $OUT/${PFX}_${id}.trades.jsonl | tee -a $OUT/_runlog.txt
}
if [ "${ROUND:-1}" = 1 ]; then
run S01_h02_fix16   disc-h02      H1 D1   '{}'
run S02_H05_nr7     disc-H05      H1 D1   '{}'
run S03_H07_rangeexp disc-H07     D1 NONE '{}'
run S04_h08_pdhpdl  disc-h08      H1 D1   '{"close_at_session_end": true}' '{"start":"07:00","end":"20:00","tz":"UTC","days":["MON","TUE","WED","THU","FRI"]}'
run S05_H09_pwhpwl  disc-H09      H4 D1   '{}'
run S06_H11_consec  disc-H11      H4 NONE '{}'
run S07_gh17_round10 disc-gold-h17 H1 NONE '{"step_price": 10.0}'
run S08_h18_fakey   disc-h18      H4 NONE '{}'
run S09_h20_spike   disc-h20      H1 NONE '{}'
run S10_H15_friday  disc-H15      H1 D1   '{}'
fi
if [ "${ROUND:-1}" = 2 ]; then
# Runda 2 (declarata dupa runda 1, inainte de rulare): vecini ai H07 (cea mai mare avg R, dar n 48 < 60 — slabim
# filtrul / lungim tinerea ca sa ajunga eligibila), ai H15 (+0,006, n 128 < 150) si ai h08 (cea mai buna eligibila).
run S11_H07_m15        disc-H07 D1 NONE '{"h07_m": 1.5}'
run S12_H07_m15_hold2  disc-H07 D1 NONE '{"h07_m": 1.5, "h07_hold_days": 2}'
run S13_H07_m18_hold2  disc-H07 D1 NONE '{"h07_hold_days": 2}'
run S14_H07_m18_hold3  disc-H07 D1 NONE '{"h07_hold_days": 3}'
run S15_H07_m18_clv07  disc-H07 D1 NONE '{"h07_clv": 0.7}'
run S16_H15_k075       disc-H15 H1 D1   '{"h15_k": 0.75}'
run S17_H15_k05        disc-H15 H1 D1   '{"h15_k": 0.5}'
run S18_H15_entry15    disc-H15 H1 D1   '{"h15_entry_hour": 15}'
run S19_h08_tp2R       disc-h08 H1 D1   '{"close_at_session_end": true, "h08_tp_mode": "2R"}' '{"start":"07:00","end":"20:00","tz":"UTC","days":["MON","TUE","WED","THU","FRI"]}'
run S20_h08_x01        disc-h08 H1 D1   '{"close_at_session_end": true, "h08_x": 0.1}' '{"start":"07:00","end":"20:00","tz":"UTC","days":["MON","TUE","WED","THU","FRI"]}'
fi
