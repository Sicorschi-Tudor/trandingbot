# Stage C — cercetare walk-forward: strategii și ieșiri după costuri realiste

Generat automat din `results.json` (scriptul `walkforward.py`, rulat pe o copie read-only a bazei de date). Serverul live nu a fost atins; comportamentul live (trend-v1, ieșiri base) rămâne neschimbat.

## Concluzie pe scurt

**Nicio combinație strategie/ieșire testată nu are speranță pozitivă după costuri realiste out-of-sample. Toate cele 4 strategii sunt REJECTED** conform regulii pre-înregistrate. Diagnosticul fără costuri arată că nici înainte de costuri nu există un avantaj semnificativ statistic: costurile nu "strică" o strategie bună, ci transformă un rezultat ≈ 0R într-unul clar negativ. Recomandare: contul rămâne pe DEMO; fără trecere pe real.

| Strategie | Config selectat (TRAIN) | TEST standard: n / avg R / CI90 / PF | TEST ECN: n / avg R / PF | Decizie |
|---|---|---|---|---|
| trend-v1 | M15, atr_sl_mult=2.0, atr_tp_mult=3.0, min_score=3, sesiune lon_ny_07_17_utc, ieșire combo modes=['trailing', 'stall'] | 516 / -0.099 / [-0.160; -0.037] / 0.735 | 569 / -0.054 / 0.851 | **REJECTED** |
| meanrev-v1 | M15, bb_k=2.0, sl_atr_k=1.0, flat_slope_atr=0.05, sesiune none, ieșire stall stall_minutes=60, stall_band_r=0.3 | 53 / -0.317 / [-0.485; -0.149] / 0.315 | 85 / -0.152 / 0.697 | **REJECTED** |
| breakout-v1 | M15, sl_mode=mid, tp_range_mult=1.0, sesiune none, ieșire trailing trail_start_r=1.0, trail_atr_mult=1.5, trail_min_r=0.5 | 62 / -0.003 / [-0.207; +0.195] / 0.993 | 80 / +0.018 / 1.041 | **REJECTED** |
| pullback-v1 | M15, strong_slope_atr=0.05, tp_r=2.0, swing_n=10, sesiune none, ieșire combo modes=['trailing', 'stall'] | 327 / -0.159 / [-0.237; -0.075] / 0.601 | 495 / -0.154 / 0.637 | **REJECTED** |

## Protocol (pre-înregistrat, neschimbat după vederea rezultatelor TEST)

- Simboluri (11, toate cu ≥ 100 zile M5 în `bars_archive`, ~120–126 zile fiecare): EURUSD, GBPUSD, USDJPY, AUDUSD, USDCAD, EURJPY, GBPJPY, NZDUSD, EURGBP, EURCHF, AUDJPY.
- Perioadă: 2026-07-02T00:00:00Z → 2026-10-28T14:50:00Z (începutul arhivei M5 comune + 400 bare M5 de încălzire, rotunjit la miezul nopții UTC).
- TRAIN: 2026-07-02T00:00:00Z → 2026-09-17T02:26:30Z (primele 65%). Embargo 1 zi. TEST: 2026-09-18T02:26:30Z → 2026-10-28T14:50:00Z (ultimele 35%).
- HTF: H1 pentru toate. Indicatorii folosesc doar bare închise (încălzirea TEST poate citi bare din TRAIN, fără nicio potrivire de parametri).
- Costuri STANDARD (principal): spread fix pe simbol (puncte) EURUSD 10, GBPUSD 14, USDJPY 12, AUDUSD 12, USDCAD 16, EURJPY 18, GBPJPY 28, NZDUSD 16, EURGBP 14, EURCHF 18, AUDJPY 18; slippage 1 pt; comision 0.
- Costuri ECN (secundar): 25% din spread-urile de mai sus + 7 USD/lot comision dus-întors; slippage 1 pt.
- Mărime: `size_mode=r_only` (R fără rotunjirea lotului), risc 0.5% pe 200 USD; pentru candidații finali și rularea `live` (loturi reale, lot minim 0.01) în USD.
- Motorul de risc live rămâne activ în simulare (max 5 poziții, 1 expunere/valută/direcție, pierdere zilnică 5%, pauză după 10 pierderi, limita de semnale/sesiune), ca și break-even la 1R și time stop 240 min.
- Căutare doar pe TRAIN, costuri standard: per strategie ≤ 6 combinații de parametri (TF semnal M5/M15 × 3 seturi) × 2 sesiuni (fără filtru / Londra+NY 07–17 UTC, cu închidere la final de sesiune), ieșire base; cel mai bun după avg R cu minimum 80 tranzacții; apoi 10 politici de ieșire pe acel config; cel mai bun după avg R cu minimum 80 tranzacții.
- TEST: configul selectat, evaluat o singură dată, cu ambele modele de cost.
- Regula de decizie (costuri STANDARD): PROMISING doar dacă avg R > 0, limita inferioară CI90 > −0.02, PF > 1.1, ≥ 150 tranzacții TEST și ≥ 2/3 sub-perioade pozitive. Altfel REJECTED.

Politici de ieșire încercate (aceleași pentru fiecare strategie):

- `base`
- `trailing trail_start_r=1.0, trail_atr_mult=1.5, trail_min_r=0.5`
- `trailing trail_start_r=0.7, trail_atr_mult=1.0, trail_min_r=0.4`
- `partial partial_frac=0.5, partial_r=1.0`
- `invalidation min_bars=3`
- `stall stall_minutes=60, stall_band_r=0.3`
- `stall stall_minutes=120, stall_band_r=0.3`
- `giveback gb_arm_r=1.0, gb_frac=0.5`
- `giveback gb_arm_r=0.6, gb_frac=0.6`
- `combo modes=['trailing', 'stall']`

## trend-v1

### TRAIN — grilă de parametri (ieșire base, costuri standard)

| TF | Parametri | Sesiune | n | avg R | CI90 | PF |
|---|---|---|---|---|---|---|
| M5 | atr_sl_mult=1.5, atr_tp_mult=2.5, min_score=3 | none | 1079 | -0.191 | [-0.246; -0.136] | 0.678 |
| M5 | atr_sl_mult=1.5, atr_tp_mult=2.5, min_score=3 | lon_ny_07_17_utc | 695 | -0.204 | [-0.274; -0.137] | 0.647 |
| M5 | atr_sl_mult=2.0, atr_tp_mult=3.0, min_score=3 | none | 1291 | -0.215 | [-0.265; -0.166] | 0.638 |
| M5 | atr_sl_mult=2.0, atr_tp_mult=3.0, min_score=3 | lon_ny_07_17_utc | 859 | -0.201 | [-0.261; -0.143] | 0.647 |
| M5 | atr_sl_mult=1.5, atr_tp_mult=2.5, min_score=4 | none | 943 | -0.181 | [-0.238; -0.115] | 0.693 |
| M5 | atr_sl_mult=1.5, atr_tp_mult=2.5, min_score=4 | lon_ny_07_17_utc | 615 | -0.201 | [-0.271; -0.134] | 0.651 |
| M15 | atr_sl_mult=1.5, atr_tp_mult=2.5, min_score=3 | none | 1302 | -0.185 | [-0.237; -0.134] | 0.678 |
| M15 | atr_sl_mult=1.5, atr_tp_mult=2.5, min_score=3 | lon_ny_07_17_utc | 762 | -0.161 | [-0.223; -0.098] | 0.708 |
| M15 | atr_sl_mult=2.0, atr_tp_mult=3.0, min_score=3 | none | 1325 | -0.170 | [-0.213; -0.127] | 0.667 |
| M15 | atr_sl_mult=2.0, atr_tp_mult=3.0, min_score=3 | lon_ny_07_17_utc | 718 | -0.157 | [-0.215; -0.101] | 0.681 |
| M15 | atr_sl_mult=1.5, atr_tp_mult=2.5, min_score=4 | none | 1131 | -0.192 | [-0.247; -0.145] | 0.657 |
| M15 | atr_sl_mult=1.5, atr_tp_mult=2.5, min_score=4 | lon_ny_07_17_utc | 677 | -0.185 | [-0.244; -0.119] | 0.664 |

### TRAIN — politici de ieșire pe configul cel mai bun

| Ieșire | n | avg R | CI90 | PF | win |
|---|---|---|---|---|---|
| base | 718 | -0.157 | [-0.215; -0.101] | 0.681 | 0.4095 |
| trailing trail_start_r=1.0, trail_atr_mult=1.5, trail_min_r=0.5 | 726 | -0.162 | [-0.218; -0.106] | 0.674 | 0.4063 |
| trailing trail_start_r=0.7, trail_atr_mult=1.0, trail_min_r=0.4 | 768 | -0.167 | [-0.220; -0.113] | 0.66 | 0.4219 |
| partial partial_frac=0.5, partial_r=1.0 | 720 | -0.156 | [-0.209; -0.103] | 0.683 | 0.4139 |
| invalidation min_bars=3 | 946 | -0.158 | [-0.201; -0.117] | 0.625 | 0.3224 |
| stall stall_minutes=60, stall_band_r=0.3 | 981 | -0.152 | [-0.196; -0.111] | 0.618 | 0.3843 |
| stall stall_minutes=120, stall_band_r=0.3 | 811 | -0.154 | [-0.206; -0.104] | 0.658 | 0.4069 |
| giveback gb_arm_r=1.0, gb_frac=0.5 | 749 | -0.162 | [-0.221; -0.108] | 0.673 | 0.4072 |
| giveback gb_arm_r=0.6, gb_frac=0.6 | 824 | -0.157 | [-0.208; -0.108] | 0.653 | 0.4345 |
| combo modes=['trailing', 'stall'] | 992 | -0.150 | [-0.193; -0.104] | 0.623 | 0.3821 |

**Config selectat (exact):** `strategy_name=trend-v1`, `signal_tf=M15`, `htf_tf=H1`, `strategy={"atr_sl_mult": 2.0, "atr_tp_mult": 3.0, "min_score": 3}`, `session=lon_ny_07_17_utc`, `exit={"mode": "combo", "params": {"modes": ["trailing", "stall"]}}`. TRAIN: 992 tranzacții, avg R -0.150, CI90 [-0.193; -0.104].

### TEST (evaluat o singură dată)

| Costuri | n | win | avg R | CI90 | PF | max DD (R) | serie max pierderi | net R | sub-perioade (n: avg R) |
|---|---|---|---|---|---|---|---|---|---|
| standard | 516 | 0.4496 | -0.099 | [-0.160; -0.037] | 0.735 | 55.019 | 7 | -50.99 | 173: -0.098 · 147: -0.081 · 196: -0.113 |
| ecn | 569 | 0.413 | -0.054 | [-0.114; +0.008] | 0.851 | 32.867 | 8 | -30.48 | 197: -0.044 · 171: -0.037 · 201: -0.077 |

Pe simbol (TEST, avg R, n):

| Costuri | EURUSD | GBPUSD | USDJPY | AUDUSD | USDCAD | EURJPY | GBPJPY | NZDUSD | EURGBP | EURCHF | AUDJPY |
|---|---|---|---|---|---|---|---|---|---|---|---|
| standard | -0.12 (124) | +0.03 (50) | +0.15 (51) | -0.19 (54) | -0.20 (25) | -0.14 (60) | -0.13 (63) | -0.14 (7) | -0.20 (7) | -0.21 (44) | -0.02 (31) |
| ecn | -0.07 (100) | +0.08 (55) | +0.08 (49) | -0.19 (54) | -0.19 (30) | -0.04 (57) | -0.08 (56) | -0.06 (12) | -0.01 (41) | -0.20 (72) | +0.15 (43) |

USD cu loturi reale (`size_mode=live`, 200 USD, risc 0.5%, lot minim 0.01):

| Costuri | tranzacții | net USD | equity final | avg R | semnale respinse (lot minim) |
|---|---|---|---|---|---|
| standard | 253 | -24.11 | 175.82 | -0.120 | 3239 |
| ecn | 398 | -31.16 | 168.79 | -0.099 | 2826 |

**Decizie: REJECTED** — avg R -0.0988 <= 0; CI90 inferior -0.1598 <= -0.02; PF 0.735 <= 1.1; doar 0/3 sub-perioade pozitive.

## meanrev-v1

### TRAIN — grilă de parametri (ieșire base, costuri standard)

| TF | Parametri | Sesiune | n | avg R | CI90 | PF |
|---|---|---|---|---|---|---|
| M5 | bb_k=2.0, sl_atr_k=1.0, flat_slope_atr=0.05 | none | 96 | -0.208 | [-0.366; -0.058] | 0.603 |
| M5 | bb_k=2.0, sl_atr_k=1.0, flat_slope_atr=0.05 | lon_ny_07_17_utc | 55 | -0.196 | [-0.416; +0.013] | 0.616 |
| M5 | bb_k=2.5, sl_atr_k=1.0, flat_slope_atr=0.05 | none | 58 | -0.299 | [-0.500; -0.098] | 0.482 |
| M5 | bb_k=2.5, sl_atr_k=1.0, flat_slope_atr=0.05 | lon_ny_07_17_utc | 28 | -0.428 | [-0.697; -0.138] | 0.366 |
| M5 | bb_k=2.0, sl_atr_k=0.5, flat_slope_atr=0.1 | none | 97 | -0.233 | [-0.429; +0.013] | 0.633 |
| M5 | bb_k=2.0, sl_atr_k=0.5, flat_slope_atr=0.1 | lon_ny_07_17_utc | 54 | -0.180 | [-0.472; +0.145] | 0.716 |
| M15 | bb_k=2.0, sl_atr_k=1.0, flat_slope_atr=0.05 | none | 97 | -0.074 | [-0.273; +0.126] | 0.854 |
| M15 | bb_k=2.0, sl_atr_k=1.0, flat_slope_atr=0.05 | lon_ny_07_17_utc | 60 | -0.003 | [-0.241; +0.254] | 0.994 |
| M15 | bb_k=2.5, sl_atr_k=1.0, flat_slope_atr=0.05 | none | 53 | -0.136 | [-0.378; +0.125] | 0.742 |
| M15 | bb_k=2.5, sl_atr_k=1.0, flat_slope_atr=0.05 | lon_ny_07_17_utc | 34 | -0.050 | [-0.381; +0.294] | 0.901 |
| M15 | bb_k=2.0, sl_atr_k=0.5, flat_slope_atr=0.1 | none | 101 | -0.160 | [-0.347; +0.036] | 0.715 |
| M15 | bb_k=2.0, sl_atr_k=0.5, flat_slope_atr=0.1 | lon_ny_07_17_utc | 59 | -0.047 | [-0.308; +0.230] | 0.912 |

### TRAIN — politici de ieșire pe configul cel mai bun

| Ieșire | n | avg R | CI90 | PF | win |
|---|---|---|---|---|---|
| base | 97 | -0.074 | [-0.273; +0.126] | 0.854 | 0.4536 |
| trailing trail_start_r=1.0, trail_atr_mult=1.5, trail_min_r=0.5 | 97 | -0.116 | [-0.317; +0.068] | 0.771 | 0.4536 |
| trailing trail_start_r=0.7, trail_atr_mult=1.0, trail_min_r=0.4 | 97 | -0.122 | [-0.307; +0.052] | 0.758 | 0.4639 |
| partial partial_frac=0.5, partial_r=1.0 | 97 | -0.081 | [-0.263; +0.089] | 0.841 | 0.4536 |
| invalidation min_bars=3 | 97 | -0.101 | [-0.293; +0.100] | 0.801 | 0.4536 |
| stall stall_minutes=60, stall_band_r=0.3 | 97 | -0.040 | [-0.234; +0.146] | 0.907 | 0.4845 |
| stall stall_minutes=120, stall_band_r=0.3 | 97 | -0.057 | [-0.247; +0.139] | 0.881 | 0.4639 |
| giveback gb_arm_r=1.0, gb_frac=0.5 | 98 | -0.102 | [-0.279; +0.068] | 0.798 | 0.449 |
| giveback gb_arm_r=0.6, gb_frac=0.6 | 100 | -0.086 | [-0.238; +0.078] | 0.798 | 0.51 |
| combo modes=['trailing', 'stall'] | 97 | -0.081 | [-0.262; +0.089] | 0.812 | 0.4845 |

**Config selectat (exact):** `strategy_name=meanrev-v1`, `signal_tf=M15`, `htf_tf=H1`, `strategy={"bb_k": 2.0, "sl_atr_k": 1.0, "flat_slope_atr": 0.05}`, `session=none`, `exit={"mode": "stall", "params": {"stall_minutes": 60, "stall_band_r": 0.3}}`. TRAIN: 97 tranzacții, avg R -0.040, CI90 [-0.234; +0.146].

### TEST (evaluat o singură dată)

| Costuri | n | win | avg R | CI90 | PF | max DD (R) | serie max pierderi | net R | sub-perioade (n: avg R) |
|---|---|---|---|---|---|---|---|---|---|
| standard | 53 | 0.3774 | -0.317 | [-0.485; -0.149] | 0.315 | 17.192 | 6 | -16.82 | 9: -0.108 · 27: -0.346 · 17: -0.383 |
| ecn | 85 | 0.2471 | -0.152 | [-0.340; +0.047] | 0.697 | 22.954 | 11 | -12.89 | 15: +0.053 · 48: -0.247 · 22: -0.082 |

Pe simbol (TEST, avg R, n):

| Costuri | EURUSD | GBPUSD | USDJPY | AUDUSD | USDCAD | EURJPY | GBPJPY | NZDUSD | EURGBP | EURCHF | AUDJPY |
|---|---|---|---|---|---|---|---|---|---|---|---|
| standard | -0.67 (8) | -0.60 (7) | -0.34 (10) | -0.16 (5) | -0.39 (5) | +0.26 (5) | -0.25 (5) | -0.10 (2) | — | +0.21 (1) | -0.24 (5) |
| ecn | -0.74 (8) | -0.13 (10) | -0.33 (9) | -0.10 (13) | -0.15 (12) | +0.99 (7) | +0.15 (7) | -0.61 (6) | +1.00 (1) | -0.58 (5) | -0.28 (7) |

USD cu loturi reale (`size_mode=live`, 200 USD, risc 0.5%, lot minim 0.01):

| Costuri | tranzacții | net USD | equity final | avg R | semnale respinse (lot minim) |
|---|---|---|---|---|---|
| standard | 41 | -10.10 | 189.90 | -0.333 | 30 |
| ecn | 75 | -5.99 | 194.02 | -0.141 | 22 |

**Decizie: REJECTED** — avg R -0.3173 <= 0; CI90 inferior -0.485 <= -0.02; PF 0.315 <= 1.1; 53 tranzacții < 150; doar 0/3 sub-perioade pozitive.

## breakout-v1

### TRAIN — grilă de parametri (ieșire base, costuri standard)

| TF | Parametri | Sesiune | n | avg R | CI90 | PF |
|---|---|---|---|---|---|---|
| M5 | sl_mode=mid, tp_range_mult=1.5 | none | 88 | -0.153 | [-0.351; +0.054] | 0.729 |
| M5 | sl_mode=mid, tp_range_mult=1.5 | lon_ny_07_17_utc | 61 | -0.272 | [-0.481; -0.036] | 0.547 |
| M5 | sl_mode=opposite, tp_range_mult=1.5 | none | 93 | -0.136 | [-0.335; +0.073] | 0.76 |
| M5 | sl_mode=opposite, tp_range_mult=1.5 | lon_ny_07_17_utc | 64 | -0.233 | [-0.462; +0.016] | 0.614 |
| M5 | sl_mode=mid, tp_range_mult=1.0 | none | 89 | -0.161 | [-0.361; +0.032] | 0.718 |
| M5 | sl_mode=mid, tp_range_mult=1.0 | lon_ny_07_17_utc | 61 | -0.215 | [-0.434; +0.034] | 0.642 |
| M15 | sl_mode=mid, tp_range_mult=1.5 | none | 111 | -0.049 | [-0.234; +0.131] | 0.9 |
| M15 | sl_mode=mid, tp_range_mult=1.5 | lon_ny_07_17_utc | 71 | +0.027 | [-0.180; +0.256] | 1.062 |
| M15 | sl_mode=opposite, tp_range_mult=1.5 | none | 114 | -0.070 | [-0.234; +0.081] | 0.853 |
| M15 | sl_mode=opposite, tp_range_mult=1.5 | lon_ny_07_17_utc | 74 | -0.031 | [-0.218; +0.181] | 0.927 |
| M15 | sl_mode=mid, tp_range_mult=1.0 | none | 114 | -0.046 | [-0.217; +0.113] | 0.903 |
| M15 | sl_mode=mid, tp_range_mult=1.0 | lon_ny_07_17_utc | 75 | -0.007 | [-0.186; +0.173] | 0.985 |

### TRAIN — politici de ieșire pe configul cel mai bun

| Ieșire | n | avg R | CI90 | PF | win |
|---|---|---|---|---|---|
| base | 114 | -0.046 | [-0.217; +0.113] | 0.903 | 0.4298 |
| trailing trail_start_r=1.0, trail_atr_mult=1.5, trail_min_r=0.5 | 116 | -0.037 | [-0.196; +0.121] | 0.923 | 0.431 |
| trailing trail_start_r=0.7, trail_atr_mult=1.0, trail_min_r=0.4 | 116 | -0.045 | [-0.206; +0.112] | 0.905 | 0.431 |
| partial partial_frac=0.5, partial_r=1.0 | 114 | -0.078 | [-0.235; +0.069] | 0.836 | 0.4298 |
| invalidation min_bars=3 | 116 | -0.087 | [-0.216; +0.050] | 0.796 | 0.3534 |
| stall stall_minutes=60, stall_band_r=0.3 | 127 | -0.080 | [-0.198; +0.032] | 0.767 | 0.4173 |
| stall stall_minutes=120, stall_band_r=0.3 | 115 | -0.082 | [-0.227; +0.051] | 0.815 | 0.4174 |
| giveback gb_arm_r=1.0, gb_frac=0.5 | 116 | -0.074 | [-0.225; +0.072] | 0.845 | 0.431 |
| giveback gb_arm_r=0.6, gb_frac=0.6 | 120 | -0.097 | [-0.235; +0.037] | 0.778 | 0.4667 |
| combo modes=['trailing', 'stall'] | 127 | -0.072 | [-0.192; +0.038] | 0.788 | 0.4173 |

**Config selectat (exact):** `strategy_name=breakout-v1`, `signal_tf=M15`, `htf_tf=H1`, `strategy={"sl_mode": "mid", "tp_range_mult": 1.0}`, `session=none`, `exit={"mode": "trailing", "params": {"trail_start_r": 1.0, "trail_atr_mult": 1.5, "trail_min_r": 0.5}}`. TRAIN: 116 tranzacții, avg R -0.037, CI90 [-0.196; +0.121].

### TEST (evaluat o singură dată)

| Costuri | n | win | avg R | CI90 | PF | max DD (R) | serie max pierderi | net R | sub-perioade (n: avg R) |
|---|---|---|---|---|---|---|---|---|---|
| standard | 62 | 0.5 | -0.003 | [-0.207; +0.195] | 0.993 | 9.223 | 7 | -0.16 | 26: -0.166 · 17: +0.110 · 19: +0.120 |
| ecn | 80 | 0.45 | +0.018 | [-0.183; +0.210] | 1.041 | 12.631 | 7 | +1.40 | 32: -0.235 · 22: +0.117 · 26: +0.243 |

Pe simbol (TEST, avg R, n):

| Costuri | EURUSD | GBPUSD | USDJPY | AUDUSD | USDCAD | EURJPY | GBPJPY | NZDUSD | EURGBP | EURCHF | AUDJPY |
|---|---|---|---|---|---|---|---|---|---|---|---|
| standard | +0.29 (9) | +0.53 (10) | -0.27 (10) | +0.12 (5) | -0.80 (6) | +0.47 (6) | -0.18 (8) | -0.58 (2) | +0.33 (1) | -0.42 (4) | +0.01 (1) |
| ecn | +0.45 (8) | +0.52 (9) | -0.29 (10) | +0.14 (5) | -0.52 (7) | +0.47 (6) | -0.26 (9) | -0.13 (3) | +0.28 (7) | -0.19 (15) | -0.07 (1) |

USD cu loturi reale (`size_mode=live`, 200 USD, risc 0.5%, lot minim 0.01):

| Costuri | tranzacții | net USD | equity final | avg R | semnale respinse (lot minim) |
|---|---|---|---|---|---|
| standard | 23 | -5.12 | 194.88 | -0.290 | 100 |
| ecn | 50 | -2.93 | 197.03 | -0.065 | 92 |

**Decizie: REJECTED** — avg R -0.0026 <= 0; CI90 inferior -0.2066 <= -0.02; PF 0.993 <= 1.1; 62 tranzacții < 150.

## pullback-v1

### TRAIN — grilă de parametri (ieșire base, costuri standard)

| TF | Parametri | Sesiune | n | avg R | CI90 | PF |
|---|---|---|---|---|---|---|
| M5 | strong_slope_atr=0.1, tp_r=2.0, swing_n=10 | none | 573 | -0.265 | [-0.338; -0.192] | 0.563 |
| M5 | strong_slope_atr=0.1, tp_r=2.0, swing_n=10 | lon_ny_07_17_utc | 350 | -0.307 | [-0.393; -0.213] | 0.482 |
| M5 | strong_slope_atr=0.05, tp_r=2.0, swing_n=10 | none | 647 | -0.261 | [-0.328; -0.194] | 0.563 |
| M5 | strong_slope_atr=0.05, tp_r=2.0, swing_n=10 | lon_ny_07_17_utc | 402 | -0.284 | [-0.366; -0.208] | 0.511 |
| M5 | strong_slope_atr=0.1, tp_r=1.5, swing_n=6 | none | 567 | -0.259 | [-0.329; -0.190] | 0.561 |
| M5 | strong_slope_atr=0.1, tp_r=1.5, swing_n=6 | lon_ny_07_17_utc | 345 | -0.284 | [-0.366; -0.204] | 0.501 |
| M15 | strong_slope_atr=0.1, tp_r=2.0, swing_n=10 | none | 423 | -0.250 | [-0.327; -0.178] | 0.539 |
| M15 | strong_slope_atr=0.1, tp_r=2.0, swing_n=10 | lon_ny_07_17_utc | 218 | -0.241 | [-0.336; -0.130] | 0.546 |
| M15 | strong_slope_atr=0.05, tp_r=2.0, swing_n=10 | none | 502 | -0.194 | [-0.264; -0.120] | 0.624 |
| M15 | strong_slope_atr=0.05, tp_r=2.0, swing_n=10 | lon_ny_07_17_utc | 267 | -0.201 | [-0.293; -0.105] | 0.598 |
| M15 | strong_slope_atr=0.1, tp_r=1.5, swing_n=6 | none | 436 | -0.275 | [-0.350; -0.203] | 0.516 |
| M15 | strong_slope_atr=0.1, tp_r=1.5, swing_n=6 | lon_ny_07_17_utc | 226 | -0.243 | [-0.346; -0.150] | 0.556 |

### TRAIN — politici de ieșire pe configul cel mai bun

| Ieșire | n | avg R | CI90 | PF | win |
|---|---|---|---|---|---|
| base | 502 | -0.194 | [-0.264; -0.120] | 0.624 | 0.3984 |
| trailing trail_start_r=1.0, trail_atr_mult=1.5, trail_min_r=0.5 | 508 | -0.200 | [-0.267; -0.128] | 0.614 | 0.3957 |
| trailing trail_start_r=0.7, trail_atr_mult=1.0, trail_min_r=0.4 | 518 | -0.207 | [-0.272; -0.139] | 0.594 | 0.4131 |
| partial partial_frac=0.5, partial_r=1.0 | 504 | -0.194 | [-0.257; -0.129] | 0.622 | 0.4048 |
| invalidation min_bars=3 | 641 | -0.192 | [-0.244; -0.137] | 0.546 | 0.273 |
| stall stall_minutes=60, stall_band_r=0.3 | 593 | -0.175 | [-0.235; -0.119] | 0.578 | 0.3693 |
| stall stall_minutes=120, stall_band_r=0.3 | 551 | -0.187 | [-0.253; -0.125] | 0.596 | 0.3975 |
| giveback gb_arm_r=1.0, gb_frac=0.5 | 514 | -0.198 | [-0.266; -0.127] | 0.618 | 0.3969 |
| giveback gb_arm_r=0.6, gb_frac=0.6 | 541 | -0.209 | [-0.273; -0.150] | 0.565 | 0.4214 |
| combo modes=['trailing', 'stall'] | 602 | -0.174 | [-0.228; -0.117] | 0.578 | 0.3688 |

**Config selectat (exact):** `strategy_name=pullback-v1`, `signal_tf=M15`, `htf_tf=H1`, `strategy={"strong_slope_atr": 0.05, "tp_r": 2.0, "swing_n": 10}`, `session=none`, `exit={"mode": "combo", "params": {"modes": ["trailing", "stall"]}}`. TRAIN: 602 tranzacții, avg R -0.174, CI90 [-0.228; -0.117].

### TEST (evaluat o singură dată)

| Costuri | n | win | avg R | CI90 | PF | max DD (R) | serie max pierderi | net R | sub-perioade (n: avg R) |
|---|---|---|---|---|---|---|---|---|---|
| standard | 327 | 0.4098 | -0.159 | [-0.237; -0.075] | 0.601 | 55.514 | 11 | -52.12 | 111: -0.115 · 104: -0.188 · 112: -0.176 |
| ecn | 495 | 0.3414 | -0.154 | [-0.223; -0.085] | 0.637 | 78.328 | 15 | -76.03 | 177: -0.148 · 144: -0.231 · 174: -0.095 |

Pe simbol (TEST, avg R, n):

| Costuri | EURUSD | GBPUSD | USDJPY | AUDUSD | USDCAD | EURJPY | GBPJPY | NZDUSD | EURGBP | EURCHF | AUDJPY |
|---|---|---|---|---|---|---|---|---|---|---|---|
| standard | -0.20 (43) | -0.15 (34) | -0.00 (48) | -0.29 (33) | +0.10 (26) | -0.39 (41) | -0.05 (38) | -0.26 (13) | -0.65 (9) | -0.23 (21) | +0.02 (21) |
| ecn | -0.09 (43) | -0.26 (35) | -0.09 (42) | -0.20 (47) | +0.17 (41) | -0.40 (38) | -0.07 (50) | -0.31 (35) | -0.15 (63) | -0.13 (68) | -0.29 (33) |

USD cu loturi reale (`size_mode=live`, 200 USD, risc 0.5%, lot minim 0.01):

| Costuri | tranzacții | net USD | equity final | avg R | semnale respinse (lot minim) |
|---|---|---|---|---|---|
| standard | 166 | -23.32 | 176.67 | -0.164 | 350 |
| ecn | 343 | -29.60 | 170.37 | -0.127 | 346 |

**Decizie: REJECTED** — avg R -0.1594 <= 0; CI90 inferior -0.2374 <= -0.02; PF 0.601 <= 1.1; doar 0/3 sub-perioade pozitive.

## Diagnostic post-hoc: aceleași configuri fără costuri

Nu intră în decizie (rulat după TEST, doar ca explicație). Spread 0, slippage 0, comision 0. Reprodus de `walkforward.py` (pasul final) sau separat cu `walkforward.py --diag-zero`.

| Strategie / perioadă | n | avg R | CI90 | PF |
|---|---|---|---|---|
| trend-v1/train | 1202 | -0.014 | [-0.051; +0.026] | 0.957 |
| trend-v1/test | 588 | +0.039 | [-0.018; +0.095] | 1.124 |
| meanrev-v1/train | 163 | +0.005 | [-0.129; +0.153] | 1.014 |
| meanrev-v1/test | 84 | -0.024 | [-0.215; +0.183] | 0.941 |
| breakout-v1/train | 162 | +0.026 | [-0.105; +0.163] | 1.057 |
| breakout-v1/test | 80 | +0.097 | [-0.089; +0.281] | 1.267 |
| pullback-v1/train | 899 | -0.038 | [-0.084; +0.006] | 0.884 |
| pullback-v1/test | 496 | -0.036 | [-0.101; +0.034] | 0.895 |

Interpretare: fără costuri, toate configurile sunt în jur de 0R (CI90 include 0). Singurele cu PF > 1 fără costuri pe TEST (trend-v1 M15 și breakout-v1) pierd tot avantajul la costurile standard. Spread-ul de 10–28 puncte este mare față de stopurile tipice M5/M15 (6–20 pips), deci costul este ~0.05–0.15R pe tranzacție (diferența TRAIN cost zero vs. standard: trend-v1 0.14R, pullback-v1 0.14R, breakout-v1 0.06R, meanrev-v1 0.05R; numărul de tranzacții diferă, filtrul de spread blochează mai puține semnale fără costuri).

## Testare multiplă și limite

- Configuri încercate pe TRAIN: **84** (4 strategii × (12 parametri/sesiune + 9 ieșiri suplimentare)). Cu atâtea încercări, cel mai bun rezultat TRAIN este optimist prin construcție (selection bias); de aceea decide doar TEST.
- Toate cele 4 configuri selectate au avut avg R negativ chiar pe TRAIN (cel mai bun: breakout-v1 −0.037R, meanrev-v1 −0.040R). Singurul config cu avg R > 0 pe TRAIN (breakout-v1 M15 Londra+NY, +0.027R) avea doar 71 tranzacții < 80 și a fost exclus de regulă.
- Eșantion scurt (~4 luni, un singur regim de piață); TEST are ~41 de zile. meanrev-v1 și breakout-v1 generează prea puține tranzacții (53–85 în TEST) pentru a putea fi validate chiar dacă ar fi pozitive.
- Specificațiile simbolurilor sunt estimate (point, contract 100000, lot 0.01); fără calendar economic; execuție simulată pe bare (SL înainte de TP în aceeași bară).
- Cu loturi reale la 200 USD și risc 0.5%, multe semnale sunt respinse de lotul minim 0.01 (vezi tabelele USD); rezultatele în USD sunt și ele negative pentru toate.

## Concluzie

Pe datele disponibile, nici trend-v1 (strategia live), nici cele trei alternative simple (revenire la medie, spargerea intervalului asiatic, pullback în trend), cu oricare dintre cele 10 politici de ieșire, nu au speranță pozitivă după costurile unui cont standard. Ieșirile alternative mută rezultatul cu cel mult ~0.03–0.04R pe TRAIN — sub costul de ~0.05–0.15R/tranzacție. Recomandarea onestă: botul rămâne pe DEMO, fără bani reali; o eventuală continuare are sens doar cu (a) costuri mult mai mici (cont ECN real, verificat), (b) timeframe-uri mai mari unde costul devine o fracțiune mică din R, și (c) mai mult istoric înainte de orice nouă selecție. Setările live nu au fost modificate.
