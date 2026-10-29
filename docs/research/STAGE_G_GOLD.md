# Etapa G — studiu dedicat aurului (XAUUSD): „ar merge botul mai bine doar pe aur?”

Întrebarea proprietarului: aurul are mișcări mai mari — dacă botul tranzacționează **doar** XAUUSD, cu mai multe poziții deodată, rezultatele vor fi mai rapide, mai mari și mai corecte? Răspuns testat pe 9,3 ani de XAUUSD (M15 → D1, 2012-11 → 2022-03), protocol identic cu Etapa F (`STAGE_F_PROTOCOL.md`: TRAIN 2013-06 → 2019-01, VALID 2019–2020 o singură rulare, TEST neatins), cont standard (spread modelat ≈ 30 pct = 0,30 USD) și ECN. Trei agenți, 60 de configurații; detalii în `research/results/GOLD/<agent>/SUMMARY.md`.

## Rezultat: **0 din 3 familii trece poarta 1. Nimic nu se activează.**

| Familie (configurații) | Aleasă pe TRAIN | TRAIN | VALID STANDARD | VALID ECN | Verdict |
|---|---|---|---|---|---|
| **Intraday** (20): trend-v1, meanrev, breakout, pullback, sessmom, orb pe H1/M15 | breakout-v1 H1/H4: breakout al range-ului asiatic, SL la capătul opus, TP 2,5× range, time-stop 24 h | n 513, +0,070 R, PF 1,16 | n 187, **+0,009 R**, CI90 [−0,131; +0,153], PF 1,02; **2019 −0,168 / 2020 +0,188** | +0,031, PF 1,07 | **REJECTED** — tot câștigul e „long aur în 2020”; fără top 5 % din tranzacții avg R = −0,118; direcția câștigătoare s-a inversat față de TRAIN |
| **Trend H4/D1** (20): donchian, ma-trend, tsmom, tsmom2, h06 | donchian-v1 H4, canal 100, SL 3×ATR, ieșire canal 20 (aceeași ca F6) | n 92, +0,196 R, PF 1,42 | n 32, +0,202 R, CI90 [−0,314; +0,764], PF 1,40; **2019 −0,21 / 2020 +0,67** | +0,209 | **REJECTED** — n < 40, CI90 uriaș; fără 2020: −0,21 R, PF 0,62; 2 tranzacții (TP la 20×ATR) = 204 % din net; mediana tranzacției −0,66 R |
| **Sesiuni + descoperire** (20): 10 ipoteze (fix 16:00, NR7, zi cu range extins, PDH/PDL, PWH/PWL, închideri consecutive, numere rotunde la 10 USD, fakey, spike, vineri) | disc-H07 D1: continuare după zi cu range ≥ 1,5×ATR | n 111, +0,037 R, PF 1,09 | n 39, **−0,061 R**, CI90 [−0,378; +0,293], PF 0,89 | +0,085, PF 1,19 | **REJECTED** — 4 din 5 criterii picate; 8 din 10 ipoteze negative pe aur, ca pe FX; numere rotunde: −0,131 R pe 429 tranzacții |

Referință: **`trend-v1` (strategia live a botului) pe XAUUSD H1, VALID: n 1 875, −0,092 R/tranzacție, PF 0,72** — pe aur pierde ca pe perechile FX.

## Ce spun datele despre cele trei presupuneri

1. **„Mișcări mai mari ⇒ mai ușor de câștigat.”** Nu. Mișcările mai mari vin cu spread de 3–5× mai mare în bani și cu stopuri mai largi; în unități de risc, raportul cost/mișcare e similar. Pe toate cele 60 de configurații, avg R pe VALID este între −0,09 și +0,20, cu intervale de încredere care includ zero; singurul an bun este **2020** (raliul aurului), iar 2019 este negativ în toate cele trei familii.
2. **„Un singur grafic ⇒ rezultate mai rapide și mai corecte.”** Invers: un instrument dă de 10–20× mai puține tranzacții (≈ 90/an pe H1, 16/an pe H4), deci CI90 de ±0,14 … ±0,5 R — ar trebui o expectanță de 2× mai mare decât orice s-a văzut ca să fie măsurabilă. Alegerea „doar aur” după ce aurul a ieșit cel mai bun din 12 este o selecție post-hoc (verificatorii F6 au marcat-o la fel).
3. **„Mai multe poziții deodată ⇒ venit mai rapid.”** A doua poziție pe același instrument e același pariu (corelație 1): multiplică viteza în ambele sensuri. Cu expectanța negativă măsurată, dublează pierderea. Funcția există acum în bot (`MAX_POSITIONS_PER_SYMBOL`, `docs/PROFIL_AUR.md`), cu regula „doar în aceeași direcție și doar peste poziții pe profit”, dar **implicit e oprită** și nu o recomand până nu există o strategie cu expectanță pozitivă demonstrată.

## Observații utile pentru viitor (nu dovezi)

- Pe trend H4, **toate câștigurile VALID vin din intrări în sesiunea asiatică (01–05 UTC)**, unde spread-ul modelat e 42–78 pct — peste pragul live de 30; cu filtrele live actuale botul ar fi luat exact tranzacțiile pierzătoare (n 16, −0,37 R) și le-ar fi ratat pe cele câștigătoare. Dacă se reia ideea, pragul de spread pentru aur trebuie setat după spread-ul **real** al brokerului pe ore, nu după modelul nostru.
- Breakout-ul range-ului asiatic cu SL la capătul opus este pozitiv în 5 din 6 ani de TRAIN pe aur și are CI90 > 0 pe ECN/TRAIN (+0,119), dar cade la ≈ 0 pe VALID din cauza lui 2019. Merită o ipoteză **pre-înregistrată** nouă, pe date noi (2022 →), cu calendar economic real — nu activare.
- Ora de intrare: nicio oră/sesiune nu e semnificativ pozitivă pe aur (3 377 tranzacții pooled); orele cele mai negative (09–11, 16–18 UTC) au spread-ul **cel mai mic** — pierderea e lipsă de avantaj, nu cost.

## Decizia pentru bot (luată de coordonator, cerută de proprietar)

- **Aurul intră în bot** (`ACTIVE_SYMBOLS=EURUSD,XAUUSD` sau doar `XAUUSD`, cu setările din `docs/PROFIL_AUR.md`), pe **DEMO, execuție dezarmată**: botul analizează, Claude revizuiește, etichetele în umbră și analistul de tranzacții strâng dovezi reale de la brokerul proprietarului (spread real pe ore, calendar economic cu cifre) — exact datele care lipsesc acestui studiu.
- **Nicio strategie nu e armată pe aur**: toate cele 60 de configurații + strategia live sunt ≤ 0 sau nemăsurabile. Armarea cu `trend-v1` pe aur înseamnă o pierdere așteptată de ≈ 0,09 R pe tranzacție.
- **Piramidarea rămâne oprită** (`MAX_POSITIONS_PER_SYMBOL=1`).
- Când arhiva reală a proprietarului acoperă ≥ 6 luni de XAUUSD cu spread real și calendar, se re-rulează acest studiu pe datele reale cu ipoteza breakout-asiatic pre-înregistrată.

## Profit lock (trailing cu blocare de profit) — efect măsurat

Regula cerută de proprietar (SL urmărește prețul de la +0,67 R, la 0,33 R în urmă, doar în sus) a fost implementată live (`position_manager.profit_lock_sl`) și în simulator (`--exit profit_lock`) și măsurată pe TRAIN (2013-06 → 2019-01), cont standard:

| Strategie / univers | ieșire `base` | ieșire `profit_lock` | Efect |
|---|---|---|---|
| breakout-v1 H1, XAUUSD | n 513, **+0,070 R**, PF 1,16 | n 516, **+0,036 R**, PF 1,08 | **−0,034 R** — taie câștigătorii mari (2017: +0,29 → +0,26; 2014: +0,07 → +0,02) |
| breakout-v1 H1, EURUSD | n 626, −0,023 R, PF 0,95 | n 633, −0,036 R, PF 0,92 | −0,013 R |
| trend-v1 H1, 12 instrumente | n 8 933, −0,061 R, PF 0,85 | n 9 328, −0,058 R, PF 0,85 | +0,004 R (nesemnificativ) |

Concluzie: blocarea rapidă a profitului **scade** expectanța acolo unde există câștigători mari (aur) și nu salvează o strategie pierzătoare. Face exact ce promite — mai multe închideri mici pe plus, mai puține pierderi complete — dar prețul este că retrage poziția înainte de mișcările mari, care sunt singura sursă de profit a strategiilor de breakout/trend. Rămâne disponibilă (implicit oprită); dacă se folosește, parametrii trebuie testați în backtest (`--exit profit_lock --exit-params '{"profit_lock_activate_r": 1.0, "profit_lock_gap_r": 0.5}'`), nu aleși după intuiție.

## Etapa H — configurația profilului gold (studiu pre-înregistrat, 38 de rulări)

Întrebarea: ce cadru determinist (strategie, TF, SL/TP, sesiune, ieșire) minimizează pierderea / maximizează expectanța pe aur, cu o singură confirmare pe VALID. Rezultate complete: `research/results/GOLDCFG/SYNTHESIS.md` (+ `grid_log.jsonl` cu comenzile exacte).

- `trend-v1` M15/H1 (profilul live de până acum): TRAIN n 6 623 **−0,18 R**; VALID n 2 289 **−0,19 R** [−0,22; −0,16] — robust negativ, confirmă cele −0,10 R pe 90 de zile M5 ale proprietarului. La cost zero +0,002 R: toată pierderea e spread/stop.
- `trend-v1` H1/H4, SL 3×ATR, TP 4,5×ATR, hold 1440, rescue off, 07–17 UTC: TRAIN n 1 429 **−0,0025 R** [−0,043; +0,039], PF 0,99; ECN +0,019 R → cadrul adoptat în `PROFILES["gold"]` (opresc sângerarea, nu profit).
- Nicio ieșire suplimentară nu îmbunătățește un candidat pe H1: base > giveback > trailing > partial > profit_lock > stall > combo (combo întoarce semnul).
- Candidatul cu cel mai mare R pe TRAIN (`breakout-v1` H1/H4 cu SL plafonat 1,5×ATR, +0,100 R) pe VALID: −0,059 R standard / −0,020 ECN, n 193; versiunea fără plafon (G15) +0,009 / +0,031 — verdict pre-înregistrat **ADOPT_AS_LEAST_BAD** (bate baseline-ul cu +0,135 R/tranz., nu e > 0).

