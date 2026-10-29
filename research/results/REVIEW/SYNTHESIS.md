# SINTEZĂ REVIEW — bot aur (XAUUSD, PROFILE=gold)

Data: 2026-10-29. Scop: ce e greșit în cod (bug-uri) și ce poate ridica **expectanța** (R mediu / tranzacție, nu rata de câștig) la deschiderea și la urmărirea pozițiilor pe aur.

> **Premisă corectată.** Textul sarcinii spunea „M5 semnal / H1 HTF”. Codul actual (`app/config.py:26-40`, commit 5ed5cc8) fixează pentru PROFILE=gold: **trend-v1 pe H1 / HTF H4, SL 3×ATR, TP 4.5×ATR, hold 1440 min, RESCUE off, sesiune 09:00-19:00 Chișinău (07-17 UTC iarna), MIN_SL 150 puncte, AI trader pe M15 în shadow**. Toate concluziile de mai jos sunt citite pe acest cadru; unde o măsurătoare s-a făcut pe M15/H1 (proxy pentru TF-uri mici), e marcat explicit.
>
> **Fapt de bază (nu se re-demonstrează):** pe 9 ani de aur nicio intrare testată nu are expectanță > 0 după costuri (`docs/research/STAGE_G_GOLD.md`). Nicio idee de mai jos nu schimbă semnul; unele reduc pierderea / tranzacție, altele o cresc. „Toate închise pe plus” nu este un obiectiv atingibil prin management; obiectivul realist este **expectanță ≥ 0 cu varianță controlată**.

---

## 1. Bug-uri confirmate (comportament greșit) și fix-ul exact

Ordonate după impact asupra banilor pe DEMO cu execuția armată.

### B1. Piramidarea pe aur (3 poziții) nu a fost simulată niciodată; distanța se măsoară în ATR(semnal) → cele 3 poziții sunt o singură tranzacție la 3× risc
- **Unde:** `app/backtest.py:1054-1058` (AnalysisContext fără `symbol_positions` → `strategy.py:82` blochează mereu a doua poziție; `book: dict[str, dict]` la :890 nu poate ține două poziții pe simbol), `app/engine.py:275-314` (`_atr14` folosește `signal_timeframe`), `app/strategy.py:50-70`, `app/config.py:37` (`MAX_POSITIONS_PER_SYMBOL=3, PYRAMID_MIN_R=0.5, PYRAMID_MIN_DISTANCE_ATR=1.0`).
- **Dovadă:** run_bt cu `max_positions_per_symbol=3` produce exact aceleași tranzacții ca 1 și numără 9.310 blocaje „Există deja o poziție” (`research/results/REVIEW/verify_pyramid_h1.json`). Fiecare picior e dimensionat la `max_risk_per_trade_pct` independent (`risk.py:270`); singurul plafon agregat e `max_total_open_risk_pct=2.0`.
- **Contradicție internă:** `docs/research/STAGE_G_GOLD.md:19,31` și `docs/PROFIL_AUR.md:92` cer `MAX_POSITIONS_PER_SYMBOL=1` până la ≥100 tranzacții DEMO pozitive; profilul livrează 3.
- **Fix:** (a) `PROFILES['gold']`: `MAX_POSITIONS_PER_SYMBOL=1`, `MAX_OPEN_POSITIONS=1` până există o intrare cu expectanță măsurată > 0. (b) `backtest._simulate`: `book[sym]` → listă de SimPosition; construiește `ctx.symbol_positions=[{side, open_r, distance_atr}]` și pasează-l în AnalysisContext, ca `pyramid_blocker` să ruleze aceeași regulă ca live; actualizează `:960/:992/:1002/:1110/:1116`. Test de regresie: cu `max_positions_per_symbol>1` nu există cheia „Există” în `blocked_context` și apar >1 poziții concurente. (c) Distanța în R (`pyramid_min_distance_r ≥ 1.0`) sau în ATR(htf), nu ATR(semnal); `PYRAMID_MIN_R ≥ BREAK_EVEN_AT_R` (vezi B9). (d) `ARCHIVE_EXTRA` + `M5:365` dacă se mai vrea vreodată M5 live.

### B2. Pe cont **netting** piramidarea topește piciorul 2 în poziția 1: SL/TP nou suprascriu SL-ul de break-even (stop lărgit), R se calculează din intent-ul greșit
- **Unde:** EA trimite `margin_mode` (`mt5/AIBridgeEA.mq5:884`) dar `app/` nu îl citește nicăieri; `executor.open_intent_for_ticket` (`executor.py:178-182`, `ORDER BY created_at DESC`) întoarce intent-ul CEL MAI NOU pentru același ticket; `position_manager.py:236-237` și `engine._path_state` iau `initial_sl` de acolo.
- **Dovadă (repro cu broker netting simulat):** BE la 2003.3 → piciorul 2 trimis cu SL 2000.9 → SL-ul poziției unite devine 2000.9 (încalcă „Never widens SL”); risk_dist 14.7 în loc de 30; `pyramid_status` continuă să vadă 1 poziție, deci nici plafoanele nu se declanșează; `_trade_perf_fields` înregistrează r_multiple -1.0 pe un risc dublu.
- **Fix:** `Engine.pyramid_status` (`engine.py:341`): în DEMO/LIVE întoarce `(False, 'cont netting')` dacă `rt.mt5_account['margin_mode'] != 2`; EA `ExecOpen`: dacă `!IsHedging()` și există deja poziție cu `InpMagic` pe simbol → `PushFail(..., 'netting: pozitie existenta')`. Persistă `initial_sl` per ticket la prima umplere (tabel nou sau coloană) și citește-l din `PositionManager.run`, `_path_state`, `symbol_positions`, `_trade_perf_fields`; fallback `open_intent_for_ticket` cu `ORDER BY ... ASC`. Test cu fake EA `margin_mode=0`.

### B3. `try_execute` păstrează SL/TP absolute ale semnalului în timp ce fill-ul alunecă până la 0.5×SL → RR se prăbușește la chase, stopul ajunge sub 1×ATR la mișcare adversă
- **Unde:** `app/engine.py:1297-1324` (`entry = ask/bid` live, `sl/tp = sig[...]` absolute; gardă simetrică `abs(entry - sig['entry']) > 0.5*sl_dist0`), `risk.py:261,288` (volum pe distanța live), `paper.py:57-64`, EA `:1282-1304` (doar normalizare). Întârzierea reală: `await self.ai.review(...)` cu `CLAUDE_TIMEOUT_SECONDS=45` înaintea execuției.
- **Dovadă (repro PAPER):** semnal RR 1.67; drift +0.49×SL → fill cu SL dist 2.23 ATR / TP dist 2.12 → RR 0.79; drift −0.49×SL → SL dist 0.77 ATR (sub podeaua MIN_SL_POINTS), loturi ×2.08. Backtest-ul umple la deschiderea barei următoare cu sl0/tp0 fixe, deci nicio cifră măsurată nu conține acest efect.
- **Fix:** re-ancorare pe fill păstrând DISTANȚELE: `d=|sig.entry−sig.sl|; tpd=|sig.tp−sig.entry|; sl=round(entry−sign·d, digits); tp=round(entry+sign·tpd, digits)`; gardă asimetrică: `drift=(entry−sig.entry)·sign`; refuz dacă `drift > 0.2·d` (chase); dacă `drift < −0.2·d` refuz sau re-rulează `strategy.analyze`. Salvează `sig['fill_drift_r']` pentru shadow stats. Fă garda EA (`REF_DRIFT_SL_FRACTION`) relativă la entry-ul semnalului, nu la cotația din try_execute (altfel se cumulează). Măsurare: avg R pe DEMO grupat pe bucket de drift.

### B4. Simbol broker „GOLD” / „GOLDm” / „GOLD.a” dezactivează SILENȚIOS blackout-ul de știri, NEWS_BE și regula de expunere valutară
- **Unde:** `app/risk.py:13-19` (`currency_legs` → `[]` sub 6 litere; „GOLD.cash” → `[('GOL',1),('DCA',-1)]`), `risk.py:40-51`, `engine.py:785-826`, `config.py:211-217` acceptă explicit „GOLD”.
- **Dovadă:** `news_blocker('GOLD', [NFP USD imp 3 în 10 min], now, 45, 3) → None`; `currency_conflict('GOLD','BUY',[('EURUSD','BUY')],1) → None`; dashboard: „fără știri”. Nu afectează numele care conțin „XAUUSD”.
- **Fix:** în `currency_legs` normalizează alias-urile înaintea regulii de 6 litere (`GOLD*→XAUUSD`, `SILVER*→XAGUSD`), sau EA trimite `SYMBOL_CURRENCY_BASE/PROFIT` în SymbolJson (`AIBridgeEA.mq5:898-915`) și `symbol_currencies` le preferă. WARN la pornire când un simbol tranzacționat dă `symbol_currencies(sym)==[]`: „filtrele de știri/expunere sunt inactive pentru X”. Teste: `currency_legs('GOLD','BUY')==[('XAU',1),('USD',-1)]`, `news_blocker('Gold.a', ...) is not None`.

### B5. R derivat din SL-ul CURENT când intent-ul OPEN nu se găsește → după break-even, profit-lock-ul moare silențios, BE se declanșează prematur, rescue off, piramidare blocată cu mesaj fals
- **Unde:** `position_manager.py:118-119` (`ref_sl = initial_sl if initial_sl else sl`), `:236-237`, `engine.py:952-984`, `:294-298`.
- **Dovadă:** BUY 2000, SL inițial 1997, SL la BE 2000.30: cu `initial_sl=None` risk_dist=0.30, profit-lock gap 0.10 < spread → `[]` la orice preț; după NEWS_BE (SL = entry+2 pct) risk_dist=0.02 → BE +0.1R nu mai vine niciodată; `pyramid_status → 'Un ordin pe acest simbol este încă în curs'` deși nu e nimic în curs. Declanșatoare realiste: `data/bot.db` resetat/mutat cu poziții magic deschise, OPEN cu rezultat pierdut >90 s și comentariu nepăstrat de broker.
- **Fix:** `risk_dist = abs(open_px − initial_sl) if initial_sl else 0.0` (regulile R-based se sar explicit); înregistrare „first sighting” per ticket (SL + price_open) în `set_mt5_positions` persistată în DB (`known_mt5_positions` e suprascris la fiecare snapshot, nu e suficient); WARN o dată per ticket; mesaj de blocker distinct „R necunoscut” vs „ordin în curs”.

### B6. Deciziile AI eșuate (timeout / 429 / răspuns ne-JSON) nu sunt stocate și nu consumă bugetul
- **Unde:** `app/ai_trader.py:481` — cheile `"action": "NONE", "side": None, "entry": None, "sl": None, "tp": None` sunt înghițite de comentariul de pe linia anterioară (verificat cu `ast`); `db.py:213` `action TEXT NOT NULL`; `_store` (:401-410) folosește `INSERT OR IGNORE`, care transformă NOT NULL într-un rowcount 0 tăcut.
- **Dovadă (repro cu client Anthropic fals):** 3 cicluri cu 429 → 3 apeluri facturate, `calls_today()==0`, `_budget_ok()` rămâne True → `AI_TRADER_MAX_CALLS_PER_DAY` nu plafonează un outage; API întoarce 409 „Fără decizie (date insuficiente...)”.
- **Fix:** mută cele 5 chei pe linia lor; `_store` cu `INSERT` simplu + `except sqlite3.IntegrityError`: UNIQUE → return False tăcut (dedup pe bară), altfel WARN și return False/raise. Test: fake 429 → `decide()` întoarce rând `NONE/ERROR`, `calls_today()==1`. Opțional contor în memorie incrementat înaintea fiecărui apel.

### B7. Eticheta shadow a AI-ului simulează ALTĂ tranzacție decât cea pe care ar executa-o live (time stop, BE, rescue, spread)
- **Unde:** `ai_trader.py:686-700` (cfg `{max_hold: hold_minutes (15-1440), be 0, be_lock 0}`, spread brut din bară, fără `_stops_px`), vs `position_manager.py:228-243` care aplică tuturor pozițiilor cu magic bot `rt.strategy` (BE 1.0/0.1, TIME_STOP la `max_hold_minutes`), `hold_deadline_closes` gardat pe `entry_mode=='live'` (:634).
- **Dovadă:** decizie hold 1440, preț −0.3R la minutul 240 apoi TP la 600: eticheta scrie +2.0R, live închide −0.3R la TIME_STOP. Mărimea componentei time-stop (proxy breakout-v1 H1/H4): hold 240 → −0.052R, hold 1440 → +0.004R. Poarta de promovare din `docs/AI_TRADER.md:34` e deci evaluată pe o tranzacție care nu se execută. (Pe profilul gold `max_hold=1440`, deci pe aur efectul time-stop e mic; BE/spread/rescue rămân diferite.)
- **Fix:** `label_pending` construiește cfg din `engine.labeler_cfg()` (be_at_r, be_lock_r, `_stops_px`, `spread_fn=labeler._spread_fn(stats, sym, point)`, slip), `max_hold=min(hold, rt.strategy.max_hold)`; alege o singură regulă de hold: (a) PM sare TIME_STOP pentru semnale `strategy_version=='ai-trader-v1'` (recomandat, cf. 1440 vs 240) sau (b) `validate_entry` plafonează hold la `max_hold_minutes`; scoate garda `entry_mode=='live'` din `hold_deadline_closes` (SQL-ul filtrează deja `mode='live'`); persistă `deadline_ts`. Re-etichetează rândurile existente și compară avg R / CI90 vechi vs nou.

### B8. Managementul AI rulează pe TOATE pozițiile bot (inclusiv trend-v1) și pe PAPER în sesiune DEMO, contrar docstring-ului de siguranță
- **Unde:** `ai_trader.py:185-194` (`bot_positions("ALL")`, fără filtru pe strategie/mod), `:611-622`, docstring `:17` („never changes ... other strategies' positions”) vs `:10`.
- **Dovadă (repro):** poziție trend-v1 PAPER → `_tick_manage` cu `manage_mode=live` o închide (`AI_MANAGE`) sau îi mută SL (`AI_SL`); rândul MANAGE nu are `strategy_version`, deci statisticile nu se pot separa. În shadow: buget API irosit și statistici MANAGE amestecate; în live: exit-ul trend-v1 forward-testat nu mai e atribuibil.
- **Fix:** `bot_positions(engine.mode())` + filtru `sig.strategy_version == STRATEGY_VERSION` prin `open_intent_for_ticket → signal_id`; setare `manage_scope: ai_only|all` (default `ai_only`); coloană `strategy_version` în rândul MANAGE; aliniază docstring + `docs/AI_TRADER.md:25`. Test: poziție trend-v1 → zero apeluri/intenții.

### B9. Picioarele piramidei sunt gestionate independent și `PYRAMID_MIN_R (0.5) < BREAK_EVEN_AT_R (1.0)` → „adaugă peste câștigători” poate dubla pierderea pe un retrace banal
- **Unde:** `strategy.py:50-70`, `position_manager.py:238-279` (plan per ticket, TIME_STOP per `opened_ts`), `config.py:37,123`.
- **Dovadă:** leg1 BUY 2000 SL 1970; la 2015 (+0.5R) leg2 permis cu SL 1985; BE pentru leg1 abia la 2030. Retrace la 1985 → leg2 −1R, leg1 −0.5R deschis; la 1970 net −2R unde o poziție pierdea −1R. Parțial documentat în `PROFIL_AUR.md:69` („pierzi de două ori mai repede”), dar fereastra 0.5R-1.0R neprotejată contrazice intenția.
- **Fix:** `PYRAMID_MIN_R ≥ BREAK_EVEN_AT_R` (validator în Settings) sau „pyramid lock” în PM: la umplerea unui picior nou, MODIFY BREAK_EVEN pe toate picioarele mai vechi de pe același simbol+sens; TIME_STOP pe ceasul primului picior. Măsurabil doar după B1(b).

### B10. TRAILING live (dacă e armat) urmărește de la primul tick pe profit, cu ATR tick; simulatorul armează la +1R pe închideri; backtest-ul declară „nu este simulat”
- **Unde:** `position_manager.py:139-149` (`profit_dist > 0`, SL = cur − atr_sl_mult×ATR, re-ancorat la tick, pas 0.1 ATR), `exits.py:369-378` (trail_start_r 1.0, best close, max(1.5 ATR, 0.5R)), `backtest.py:765` (warning), lipsă `with_trailing`. Pe profilul gold distanța ar fi 3×ATR(H1) de la 0R. Flag accesibil din dashboard (`api.py:136`), implicit off.
- **Fix:** `exits.with_trailing(policy, strat)` în oglindă cu `with_profit_lock`; `backtest.py` aplică overlay-ul și șterge avertismentul; `plan()` adoptă semantica simulată (armare la `trail_start_r×R`, ancoră = cel mai bun close pe bară închisă, distanță `max(trail_atr_mult×ATR, trail_min_r×R)`); expune cei 3 parametri în `STRATEGY_NUM`. Măsoară `--exit trailing` vs `base` pe H1/H4 înainte de armare (STAGE_G: trailing < base pe H1 → flag-ul rămâne probabil off).

### B11. Profit lock: live pe fiecare tick, simulator doar pe închiderea barei → cifra măsurată (−0.034R breakout) NU este regula live
- **Unde:** `position_manager.py:145-151` + `engine.py:2078/2112` (pm.run la 1 s), `exits.py:363-368` (`cpx` = close; BE și rescue folosesc deja `fav_px`).
- **Dovadă:** spike +0.80R intra-bară cu close +0.20R: live armează și e scos la +0.41/+0.47R pe bara următoare, sim nu armează și atinge TP +3R. Re-măsurare (G15 breakout H1/H4 TRAIN): base +0.0697; profit_lock close-model +0.0356 (167 TRAIL); extreme-model +0.0575 (206); extreme+same-bar-stop +0.0728 (276). Deci direcția bias-ului NU este „limită inferioară a daunei” — modelul fidel tick-ului dă rezultate mai bune, nu mai rele; pe alt config diferența își schimbă semnul în zgomot (±0.005-0.02R).
- **Fix:** parametru `profit_lock_intrabar` (default 1 în overlay-ul live `with_profit_lock`, 0 pentru modul de cercetare explicit): armare pe `fav_px`, `new_sl = fav_px − sg·gap·R`, dacă close-ul e deja dincolo de new_sl → ieșire TRAIL în aceeași bară la `new_sl − sg·slip`. Aceeași tratare pentru armarea rescue (`exits.py:26-29`). Re-rulează tabelul profit_lock din STAGE_G cu ambele modele; CI90 care include 0 = nicio decizie.

### B12. `_tighten_ok` (AI TIGHTEN_SL) ignoră stops_level, freeze, spread, rotunjirea la digits și pasul minim; validează pe un preț vechi de până la 45 s; nu verifică intenții pendinte
- **Unde:** `ai_trader.py:597-609`, vs `position_manager.py:113-129` care are regula corectă; `ai_claude.py:120,130` nu dau modelului niciun număr. (Sub-afirmația „bid vs ask” NU e bug: `POSITION_PRICE_CURRENT` și `paper.py:63/70` sunt deja pe partea de ieșire.)
- **Dovadă:** gold SELL ask 2350.50, stops_level 30 pct: `new_sl=2350.51` → True (MT5 ar răspunde 10016 INVALID_STOPS, iar rândul rămâne „SL mutat la ... (QUEUED)”); pe PAPER un SL la 1 punct e scos la următorul tick = închidere la preț mai prost decât CLOSE.
- **Fix:** `_tighten_ok(symbol, pos, new_sl)` pe cotația PROASPĂTĂ după `await decide_manage`: `cur` pe partea de ieșire, `new_sl=round(new_sl, digits)`, `(cur−new_sl)·sign ≥ max(spread, stops_level·pt, freeze·pt)+pt`, refuz în zona freeze, îmbunătățire ≥ `max(0.1·initial_risk, 2·pt)`, skip dacă `executor.has_pending_for_ticket`; pune `min_sl_gap_price` în payload și în MANAGE_PROMPT; `executor.apply_results` scrie FAILED înapoi în nota deciziei.

### B13. Payload-ul MANAGE omite ce promite prompt-ul (termen maxim, setup/invalidare, H1) și deciziile MANAGE nu sunt niciodată etichetate → `manage_mode=shadow` nu măsoară nimic
- **Unde:** `ai_trader.py:341-366` (`build_manage_payload`: doar position/quote/atr/candele semnal 40/news), `ai_claude.py:128-134` („termenul maxim”, „setup-ul s-a invalidat”), `label_pending` :671-673 (`kind='ENTRY'` only), `stats()` :431-457 (doar contoare pentru MANAGE).
- **Dovadă (repro):** după închiderea tranzacției, rândurile MANAGE rămân `outcome=None`; `stats()['manage'] == {'CLOSE/VALID':1,'TIGHTEN_SL/VALID':1}` — niciun R, niciun ΔR.
- **Fix:** payload: `hold {hold_minutes, deadline_utc, minutes_left}`, `entry_decision {setup, reasoning, invalidation (de persistat), sl/tp inițial, planned_rr}`, `candles.H1` (24), `constraints.min_sl_gap_price`, `recent_manage_outcomes`. Etichetare contrafactuală: pentru fiecare MANAGE VALID cu ticket închis, `R_cf` prin `labeler.simulate` de la ts-ul deciziei (TIGHTEN_SL: cu SL-ul propus; CLOSE: ieșire imediată la prețul de atunci − costuri), `outcome_r = R_actual − R_cf` (pozitiv = acțiunea ar fi ajutat); `stats()['manage_shadow']` per acțiune {n, avg ΔR, CI90}; regulă: live doar dacă CI90 inferior al ΔR > 0 (priorul pe aur este negativ: profit-lock −0.034R, stall −0.012R).

### B14. `market_context` AI: candelele săptămânale și datele D1 sunt decalate cu o zi (barele D1 sunt la miezul nopții serverului = 21/22 UTC ziua precedentă)
- **Unde:** `ai_trader.py:278` (cheie `%G-W%V` pe t brut), `:271/:273/:287/:296` (`iso(t)[:10]`). Date: research.db XAUUSD D1 — 100% din bare la 21h/22h UTC; zile UTC = Dum..Joi, zero Vineri.
- **Dovadă:** 2020-W38 emis = {o 1957.68, l 1882.71, c 1911.92} (Marți-Vineri + Luni următoare); real = {o 1937.74, l 1932.87, c 1950.42}; `daily_candles_120[-1].t = '2020-09-20'` (duminică) pentru sesiunea de luni 21 sep.
- **Fix:** `day_ts = b['t'] + 12*3600` (sau `+ rt.bridge['srv_offset']`) pentru cheia săptămânii, datele pivoților, `since` și datele candelelor zilnice. Test: bară la Dum 21:00 UTC → etichetă Luni, săptămâna N+1.

### B15. Contor global de concordanță cod ↔ documentație profil
- `config.py:37` livrează piramidare 3 vs recomandarea 1 din propriile documente (vezi B1/B9) — un singur `MAX_POSITIONS_PER_SYMBOL=1` rezolvă B1, B2, B9 pentru starea curentă.

---

## 2. Idei care ÎMBUNĂTĂȚESC expectanța pe aur (cu cifre)

Toate pe TRAIN 2013-06..2019-01, cost standard, `max_open_positions=1`, cu VALID 2019-2020 doar unde e marcat. Niciuna nu face semnul pozitiv.

| # | Idee | Cadru măsurat | Baseline avg R [CI90] | Variantă avg R [CI90] | Δ avg R | Observații |
|---|------|---------------|------------------------|------------------------|---------|------------|
| I1 | **Hold 24 h în loc de 240 min**, fără stall/invalidation/giveback | breakout-v1 H1/H4, rescue off (= profil) | −0.0278 [−0.087;+0.030], n 518, PF 0.92 | +0.0182 [−0.066;+0.102], n 516, PF 1.04 | **+0.046** | Rescue on: −0.052 → +0.004 (+0.055). VALID: −0.174 → −0.157 (+0.017; ΔR pereche +0.013 [−0.07;+0.10]). 48 h = 24 h (−0.0003). Invalidation −0.003, stall/giveback/60 min ≤ base. Rata de câștig neschimbată (48.7 → 49.8%) → efect real pe expectanță, nu pe WR. **Profilul deja are 1440**; rămâne de aplicat în calea AI (`ai_trader.py:499,686` fallback 240; `forward.py:9,199` 240). Varianța crește (ani bimodali). |
| I2 | **Poartă de volatilitate scăzută**: blocare când ATR14/mediana(ATR14,100) < 1.0 (`disc-rev-volgate`) | trend-v1 M15/H1, min_sl 150 | −0.1858 [−0.2035;−0.1657], n 6682, PF 0.61 | −0.1558 [−0.1762;−0.1357], n 6032, PF 0.66, DD −24% | **+0.030** | Câștig în 4/6 ani; CI se suprapun marginal. Nu e doar cost: gross +0.026R, cost doar +0.004R; avg R pe bucket ATR-ratio <0.8: −0.27 … >2.0: −0.08, persistent în fiecare bucket de spread/SL → bara cu ATR mic este o INTRARE proastă, nu doar scumpă. `max_atr_ratio=2.0` are semn greșit (HIGH_VOL e bucket-ul cel mai puțin rău) — nu adopta. `regime_gate RANGE` +0.0016 = neutru. VALID **nu** a fost rulat — obligatoriu înainte de orice folosire; nemăsurat pe H1/H4. |
| I3 | **Poartă de cost relativă**: blocare când spread > X × distanța SL finală (`disc-rev-costgate`) | trend-v1 M15/H1, min_sl 150 | −0.1858, n 6682 | X=0.10: −0.1048 [−0.135;−0.074], n 2398, PF 0.75; X=0.08: −0.0896, n 1137 | **+0.081** (VALID: −0.2077 → −0.0921, **+0.116**) | CI nu se suprapun; câștigul e eliminare reală de cost (cost/tranzacție 0.034 → 0.020R, spread/SL median 15% → 8.5%). DAR elimină 64-83% din tranzacții și aproape toate în 2017-2018; rămâne clar negativ. Pe H1/H4 X=0.06: −0.078 → −0.040 (+0.039), n 5438 → 1589. Pe cadrul profilului (3×ATR, spread/SL ~3%) poarta rar s-ar declanșa → efect mic. Valoare: **șină de siguranță** (`max_spread_sl_frac` 0.06-0.10), nu edge. |
| I4 | Fereastră 13-17 UTC în loc de 06-10 UTC | trend-v1 M15/H1 | −0.2052 [−0.236;−0.172], n 2410 | −0.1317 [−0.157;−0.106], n 3705 | +0.0735 (VALID +0.032) | **Doar pe M15.** Pe cadrul profilului H1/H4 (07-17 UTC): −0.0025 → −0.0171 cu 13-17 UTC = **mai rău** (în CI). Pierderea totală e identică (−488 vs −495 R) pentru că apar 50% mai multe tranzacții. Concluzie: nu schimba fereastra; **scrie-o în UTC** (`schedule.tz='UTC'`) pentru a elimina deriva DST de 1 h. |

**Concluzie secțiune:** singurele efecte robuste (semn stabil TRAIN+VALID, CI care nu se suprapun) sunt cele care **scot cost** (I3) sau **nu mai taie câștigătorii** (I1). Niciuna nu produce expectanță > 0 pe aur; deficitul pe M15/M5 e ~0.19R/tranzacție de costuri (`entry_gold150_zerocost.json`: +0.002R fără costuri), ceea ce niciun filtru nu recuperează. Cadrul H1/H4 3×ATR al profilului este deja cel mai puțin costisitor (−0.0025R [−0.043;+0.039]).

---

## 3. Idei neutre sau dăunătoare (și de ce)

| Idee | Rezultat | De ce nu |
|------|----------|----------|
| **Veto „intrare în 10% superior/inferior al range-ului H1 pe 20 bare”** (`disc-rev-h1range`) | edge 0.10: −0.1858 → −0.1945 (**−0.0087**); edge 0.25: −0.2041; invers (păstrează DOAR extremele): −0.1582 (cel mai puțin rău, dar CI se suprapun) | Elimină tranzacțiile GREȘITE: cele 384 vetoate au −0.043R, cele păstrate −0.195R. Pe aur intrarea cu trendul la extrema H1 se comportă ca breakout. Intuiția „supracumpărat spre rezistență” din `ai_claude.py:76` este anti-informativă aici — **nu o codifica și nu împinge modelul spre ea**. |
| **Confirmare HTF structurală** (pantă EMA50 pe 10 bare > 0.3/0.5×ATR, ± close pe partea corectă; `disc-rev-htfslope`) | M15/H1: −0.006..−0.007R; H1/H4: +0.007R; toate în CI (half-width ~0.02R) | Regula live e FLAT pe 13.8% din barele H1 (nu „câțiva cenți”: prag ~0.14 USD vs mediană 0.19 ATR). Filtrul scoate pe M15 tranzacții mai bune decât media (−0.164 vs −0.192). `htf_require_close_side` redundant (2-4 tranzacții). **Renunță la „confirmare HTF mai bună” ca direcție.** |
| **Cutoff intrări în ultimele N min ale sesiunii** (`disc-rev-cutoff`) | M15 c60: +0.0010; c90: −0.0051; H1 c60 = no-op (decizia e exact la 60 min); H1 c120: −0.0018 | Premisa e falsă: ieșirile SESSION_END au +0.006R (M15) / +0.029R (H1) — cel mai puțin rău rezultat al strategiei; intrările din PRIMELE 15 min pierd cel mai mult (−0.185). Profilul are `CLOSE_AT_SESSION_END=False`, deci parametrul ar fi inactiv oricum. |
| **Plafon volatilitate înaltă** (`max_atr_ratio=2.0`) | +0.002 (zgomot, n −70) | Candidații HIGH_VOL sunt bucket-ul cel mai bun (−0.084R, spread/SL 9.3%). Semn greșit. |
| **regime_gate RANGE** | +0.0016, −13% tranzacții | Zero câștig / tranzacție; nu merită parametru live. |
| **RR minim 1.2 → 1.5** (sub-item determinist al ideii de prompt AI) | −0.1927 → −0.1877 (+0.005, CI aproape identice) | Neutru pe M15; TP 1569 → 1207, BE 294 → 593. Nu schimbă semnul. Totuși rezolvă inconsecvența prompt (1.5) vs `MIN_RR=1.2` (`ai_trader.py:38,389`). |
| **Hold 48 h** | = 24 h (−0.0003) | Nimic peste 24 h. |
| **Invalidation / stall / giveback / 60 min peste 240** | toate ≤ base (invalidation −0.003, hold 60: −0.094) | Orice regulă de închidere timpurie taie câștigătorii pe aur. |
| **Trailing / profit-lock mecanic** (STAGE_G + re-măsurare B11) | trailing de la +1R −0.0008; chandelier +0.5R −0.0016; profit-lock −0.034 (close-model) .. +0.003 (tick-model) | Interval care include 0; simulatorul actual nu poate semna regula live. Rămân **off** până la fix-ul B11 și re-măsurare. |
| **Piramidare 3 poziții** | nemăsurabil azi (B1) ; așteptat: varianță ×2-3, medie neschimbată | Pe o strategie cu expectanță ~0, piramidarea multiplică DD-ul fără a adăuga R mediu. |

---

## 4. Îmbunătățiri AI / prompt recomandate (nemăsurabile offline) + plan de măsurare în umbră

Nu există plugin `ai-trader-v1` în `app/strategies`, research.db nu conține decizii AI și nu se poate reda 2013-2019 prin Claude. Singurul test valid: **A/B în shadow** pe DEMO.

### 4.1 Onestitatea payload-ului de intrare (reviewer + arbitraj)
- `app/strategy.py:33-45` generează „probabilități” euristice (p_dir = 0.5 + 0.25·|score| → 0.70-0.75) pe care `ai_claude.py:302` le prezintă ca „Datele sunt verificate”. **Scoate-le sau redenumește-le `heuristic_score`**; adaugă în `_ai_context` (`engine.py:1256-1267`, azi 12 M15 + 6 H1) structura H1 (swing-uri 20 bare, poziție în range, pantă EMA50/ATR), percentila ATR, spread/SL curent în %, R realizat recent pe setup.
- **Nu** cere veto pe „top/bottom 10% al range-ului H1” (secțiunea 3 arată că e anti-informativ). Veto-ul pe spread/SL > 6-10% e acoperit mai bine determinist (I3).
- `build_entry_payload` (`ai_trader.py:300-339`): un singur RR (prompt și `MIN_RR`), toate timpii în UTC cu zonă explicită (azi candele UTC, `local_time` fără zonă, sesiune în tz dashboard), `strategy_view` etichetat cu TF-ul lui (vine de pe H1 live, nu M15) sau eliminat, scoate nota „Cont DEMO / PAPER: nu sunt bani reali” (:339), hold default **1440** pentru aur (I1), schema cu `sl_atr_mult/tp_atr_mult/rr/key_level` + checklist obligatoriu; `validate_entry` respinge rr inconsistent > 5% și `sl_atr_mult` în afara 0.5-3.

### 4.2 Managementul AI
- Payload MANAGE complet (B13) + gap minim numeric (B12) + scope `ai_only` (B8) + etichetă contrafactuală ΔR (B13). **Default HOLD**; nu adăuga reguli mecanice de închidere timpurie (secțiunea 3).

### 4.3 Planul de măsurare în umbră
1. Coloană `prompt_variant` (și `strategy_version` pentru MANAGE) în `ai_trader_decisions`; etichetare cu cfg-ul live (B7).
2. A/B pe săptămâni ISO alternante, ≥ 8 săptămâni, **n ≥ 100 BUY/SELL per braț**; metrică: avg R [CI90] prin `mean_ci90`, bootstrap pereche p < 0.1; defalcare pe setup și pe completitudinea checklist-ului; `claude_usage` cost per −1R evitat.
3. Pentru arbitrajul trend-v1: `engine.shadow_stats(days=90).by_verdict` — succes = VETO avg R < AGREE avg R cu ≥ 0.10R și CI90 disjuncte, n ≥ 100 per verdict.
4. Pentru MANAGE: `stats()['manage_shadow']` per acțiune; live doar dacă CI90 inferior al ΔR > 0 (≥ 100 rânduri etichetate).
5. Pentru filtrele deterministe în producție: `shadow_stats.by_blocker` („Volatilitate scăzută”, „Spread-ul depășește …”) cu n ≥ 100 etichete per blocker înainte de a crede cifra TRAIN.
6. Pentru B3: avg R pe bucket `fill_drift_r` pe DEMO.

---

## 5. Plan de implementare clasat (max 8 puncte)

1. **Oprește piramidarea și netting-ul riscant (B1a, B2, B9) — o linie + două garduri.** `PROFILES['gold']: MAX_POSITIONS_PER_SYMBOL=1, MAX_OPEN_POSITIONS=1`; `pyramid_status` refuză pe `margin_mode != 2`; EA refuză OPEN pe simbol cu poziție bot pe netting. Efect: elimină singurul mecanism care poate transforma o pierdere −1R în −2R/−3R fără nicio dovadă de câștig. Zero cost de măsurare.
2. **Re-ancorare SL/TP pe fill + gardă asimetrică de drift (B3).** Restaurează geometria 3×ATR / RR 1.5 măsurată; `fill_drift_r` în semnal. Nu poate scădea expectanța; aliniază live cu backtest-ul.
3. **R inițial imuabil per ticket (B5 + B2 partea 2).** Înregistrare first-sighting persistată; `risk_dist=0` când necunoscut; mesaje de blocker corecte. Precondiție pentru orice regulă R-based (BE, profit-lock, rescue) să însemne ce credem.
4. **Alias metale + WARN la filtre inactive (B4).** Mic, dar fără el 45 min de blackout NFP/FOMC și NEWS_BE pot fi inactive pe brokeri cu „GOLD”. Teste unitare incluse.
5. **AI trader: stocare ERROR + buget (B6), scope `ai_only` (B8), etichetă = tranzacția live (B7), hold default 1440 pe aur (I1 în calea AI), un singur RR, payload onest în UTC (4.1).** Fără acestea, modul shadow nu produce dovezi pe care să se poată decide promovarea.
6. **Management AI măsurabil (B12, B13):** `_tighten_ok` pe cotație proaspătă cu gap broker + pas minim; payload MANAGE complet; etichetare contrafactuală ΔR; `manage_shadow` în stats/dashboard. Manage live rămâne blocat până CI90(ΔR) > 0.
7. **Paritate simulator ↔ live pentru ieșiri (B10, B11) + simulator cu piramidare (B1b-c).** `with_trailing`, `profit_lock_intrabar`, book listă + `symbol_positions` în backtest. Apoi re-rulează tabelul de exit-uri STAGE_G pe H1/H4 cu profilul gold; decizia pentru TRAILING/PROFIT_LOCK/piramidare se ia pe cifre, nu pe intuiție (prior: toate ≤ base).
8. **Șine de siguranță deterministe la intrare, după confirmare VALID:** `max_spread_sl_frac` (0.06-0.10) ca parametru de strategie (I3, confirmat VALID) și `min_atr_ratio=1.0` (I2, **rulează întâi VALID 2019-2020 și cadrul H1/H4**); sesiunea scrisă în UTC (I4); corectează `market_context` D1/săptămânal (B14). Așteptare realistă: pierdere / tranzacție mai mică, nu edge; cadrul H1/H4 3×ATR rămâne cel de producție, M5/M15 nu se reactivează pe aur.

**Ce NU se implementează:** veto pe range-ul H1, confirmare HTF „structurală”, cutoff la final de sesiune, `max_atr_ratio`, `regime_gate RANGE`, hold 48 h, invalidation/stall/giveback în bucla de management, fereastra 13-17 UTC pe H1/H4.

---

### Fișiere produse de review (nu editează nimic existent)
- Plugin-uri de cercetare (doar `app/strategies/discovery/`, nelistate motorului live): `disc_rev_costgate.py`, `disc_rev_volgate.py`, `disc_rev_h1range.py`, `disc_rev_htfslope.py`, `disc_rev_cutoff.py`.
- Rezultate: `research/results/REVIEW/{costgate_*, volgate_*, h1range_*, htfslope_*, cutoff_*, sess_rev_*, rrfloor_*, hold_*, ai_*, entry_gold150_VALID, verify_pyramid_h1}.json` (+ .log / .trades.jsonl).
- `--split test` nu a fost folosit nicăieri; VALID doar pentru costgate X=0.10, sesiune 13-17 și hold 1440 (rescue off).
