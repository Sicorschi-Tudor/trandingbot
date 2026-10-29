# Plan: de la „infrastructură sigură” la „bot cu avantaj demonstrat”

**Obiectivul cerut:** venit minim de 50 USD pe zi, zilnic, cu acest bot.
**Răspunsul onest, înainte de orice:** nu pot garanta 50 USD pe zi — și nimeni care îți promite asta nu spune adevărul. Mai jos sunt dovezile (cercetare academică, date de la autorități de reglementare, competiții reale cu modele AI, cercetarea proprie a acestui proiect), apoi matematica exactă a ce ar însemna 50 USD/zi, apoi **planul concret, în etape, cu criterii de trecere pre-înregistrate**, care este singurul drum serios către un bot care câștigă bani. Ultima secțiune spune ce am implementat deja în această sesiune.

---

## 1. Ce spune cercetarea despre boți și traderi similari

| Sursă | Ce arată | Ce înseamnă pentru noi |
|---|---|---|
| Autoritățile de reglementare europene (ESMA), analiza națională a conturilor CFD | **74–89 % din conturile retail pierd bani**; brokerii sunt obligați să afișeze procentul (ex. IG: 78 % pierd). Un sondaj pe 28 de brokeri: în medie **76 % pe minus**. | Instrumentele pe care tranzacționează botul (CFD-uri FX) au costuri care consumă marea majoritate a participanților. |
| Barber, Lee, Liu, Odean — day traders din Taiwan, 1992–2006 (date complete de bursă) | **Sub 20 % din day traderi sunt profitabili net de costuri**; în studiul ulterior „Learning, Fast or Slow” **97 % sunt estimați să piardă în viitor**. ~1 % din traderi fac 50 % din volum și majoritatea profitului. | Trading-ul intraday pe termen scurt este un joc cu sumă negativă după costuri; câștigătorii sunt foarte puțini și foarte bine finanțați. |
| Alpha Arena (nof1), competiție cu **bani reali** între modele AI (GPT-5, Claude, Gemini, Grok, DeepSeek, Qwen), oct.–nov. 2025 | Majoritatea modelelor au **pierdut bani**; GPT-5 a pierdut peste 62 %; pierderile au venit din **levier excesiv și control slab al riscului**. Organizatorii au spus explicit că durata scurtă și lipsa controalelor statistice nu permit concluzii. | Un LLM „puternic” care decide singur tranzacțiile **nu** este o sursă de profit. Acest proiect face bine că Claude nu poate deschide ordine. |
| Studii academice pe agenți LLM de trading (StockBench, reproducerea TradingAgents/QuantAgent, CLQT, 2025–2026) | Majoritatea agenților **nu bat buy-and-hold**; după costuri realiste Sharpe-ul unui agent a scăzut de la 0.43 la 0.22 și a pierdut față de buy-and-hold pe 4 din 5 acțiuni. | Valoarea LLM-ului este în **analiză, explicare, disciplină și detectarea erorilor de proces**, nu în „ghicirea” direcției. |
| Piața de EA-uri MQL5 / Myfxbook | Nu există niciun studiu independent care să arate că EA-urile vândute sunt profitabile pe termen lung; backtest-urile vendorilor sunt supra-optimizate; un vânzător recunoaște că din mii de EA-uri, doar „câteva zeci” au performanță live verificabilă. Un backtest pe 43 de EA-uri de aur: **1 din 5 a spart contul**. | „Boții similari” cu venituri afișate sunt, în marea majoritate, marketing. Cei care arată curbe perfecte folosesc de regulă grid/martingale fără SL — câștigă mărunt luni la rând și pierd tot într-o zi. |
| Quantopian: 888 strategii algoritmice, backtest vs. live | **R² = 0.025** între performanța din backtest și cea live; cât mai mult optimiza autorul, cu atât mai prost mergea live. Degradare tipică backtest→live: 30–60 %. | De aceea proiectul folosește walk-forward, protocol pre-înregistrat și test evaluat o singură dată. Trebuie păstrat așa. |
| Firme prop (300 000+ conturi, 10 firme) | ~14 % trec challenge-ul, **~7 % primesc vreodată o plată**. | Chiar și cu capital „împrumutat”, regulile de drawdown elimină 93 % din participanți. |
| Factori FX cu dovezi academice (carry, momentum, trend, value) | Carry: Sharpe ~0.5–0.6 pe termen lung **după costuri**, cu risc de crash; trend multi-asset: Sharpe ~0.6–1.2 în studii, dar randamente în scădere și drawdown-uri de 15–20 %; momentum/value FX: slabe și dependente de regim. | Acestea sunt **singurele** familii cu avantaj documentat. Sunt lente (D1/săptămânal), diversificate pe multe instrumente și au ani proști. Nu produc „50 USD în fiecare zi”. |
| Indicii CTA (SG CTA / Trend) — fonduri profesioniste de trend-following | Ani flat (2015: 0 %), drawdown de ~16 % în 2024–2025; cea mai slabă strategie de hedge fund în 2025. | Chiar profesioniștii cu zeci de PhD și miliarde sub administrare au randamente **anuale de o singură cifră** și ani negativi. |

**Concluzia cercetării:** nu există nicăieri un bot retail cu venit **zilnic garantat**. Cei care câștigă pe termen lung au: costuri foarte mici, diversificare largă, orizont lung, risc mic pe tranzacție, și acceptă săptămâni sau luni pe minus.

---

## 2. Matematica: ce înseamnă concret „50 USD pe zi”

50 USD/zi × ~21 zile de tranzacționare = **~1 050 USD/lună ≈ 12 600 USD/an**.

Profitul așteptat al unui bot = `număr tranzacții × risc pe tranzacție (USD) × avantaj mediu (R)`.

| Capital | Risc/tranzacție (0.25 % — limita actuală) | Avantaj necesar la 3 tranzacții/zi | Randament anual implicit | Realist? |
|---|---|---|---|---|
| 10 000 USD | 25 USD | **+0.67 R / tranzacție** | ~126 %/an | **Nu.** Cele mai bune strategii din literatura de specialitate au +0.05…+0.15 R după costuri. Nicio strategie testată în acest proiect nu a avut avantaj pozitiv. |
| 10 000 USD, risc 3.3 %/tranzacție | 330 USD | +0.05 R | — | **Nu.** La 3.3 % risc o serie de 10 pierderi (normală statistic) taie 30 % din cont; ruina este aproape sigură. Exact așa pierd boții „profitabili” din marketplace. |
| 50 000 USD | 125 USD | +0.13 R | ~25 %/an | Posibil doar pentru un sistem **excepțional**, diversificat, și doar ca medie pe ani, nu zilnic. |
| 130 000 USD | 325 USD | +0.05 R | ~10 %/an | Acesta e nivelul la care un sistem cu avantaj mic, dar real, produce **în medie** 50 USD/zi — cu luni întregi negative. |
| 250 000 USD | 625 USD | +0.027 R | ~5 %/an | Comparabil cu indicii CTA profesioniști. |

Două lucruri rezultă clar:
1. **„Zilnic” este imposibil pentru orice sistem onest.** Chiar un sistem cu avantaj real are 40–50 % zile negative. Singura cifră care are sens este **media lunară/anuală**.
2. **Variabila dominantă este capitalul, nu codul.** Cu 10 000 USD, un bot bun face câțiva dolari pe zi în medie. Cu 130 000 USD, un bot bun poate face 50 USD/zi în medie. Niciun cod nu schimbă această aritmetică fără a crește riscul la niveluri care distrug contul.

---

## 3. Diagnosticul proiectului: ce avem și ce lipsește

**Ce este deja la nivel profesionist (și trebuie păstrat):**
- Infrastructura de siguranță: SL/TP obligatorii, Risk Engine cu 24 de verificări, idempotență, legare de cont, kill switch, LIVE blocat, AI fără drept de a deschide ordine.
- Metodologia de cercetare: walk-forward, protocol pre-înregistrat, test evaluat o singură dată, costuri realiste, intervale de încredere, atenție la testarea multiplă. Acesta este exact standardul care lipsește la 99 % din boții retail.
- Observabilitate: etichete în umbră (știm ce ar fi făcut și setup-urile neexecutate), traiectorii de tranzacții, statistici de spread pe oră, calendar economic istoric cu cifre.
- 619 teste automate.

**Ce lipsește (și e singurul lucru care contează pentru bani):** un **avantaj statistic (edge) demonstrat după costuri**. Stadiul actual, documentat în `docs/research/`: 4 strategii intraday, 9 combinații pe H1/H4/D1, 4 modele ML și 20 de ipoteze noi — **toate respinse**, majoritatea cu rezultat ≈ 0 R chiar fără costuri. Diagnosticul este că pe **prețul simplu al perechilor FX majore, pe un cont standard cu spread**, nu există avantaj exploatabil la această scară.

**Ce NU vom face (chiar dacă „boții similari” o fac):** grid/martingale, mărirea volumului după pierderi, tranzacții fără SL, levier mare, optimizarea parametrilor pe setul de test, mutarea SL-ului mai departe. Toate produc curbe frumoase luni la rând și apoi pierderea totală a contului.

---

## 4. Planul în etape, cu criterii de trecere pre-înregistrate

Fiecare etapă are o **poartă** (gate): dacă nu se trece, nu se merge mai departe și nu se riscă bani reali. Porțile sunt scrise acum, înainte de rezultate — exact ca în etapele C/C2/D/E.

### Etapa 0 — Claude devine analist de tranzacții ✅ *(implementat în această sesiune)*
- Analiză automată după **fiecare tranzacție închisă**: calitatea procesului (nu a rezultatului), ce a mers, ce nu, lecții verificabile, evaluarea ieșirii (prematură / târzie / forțată), etichete.
- **Bilanț zilnic** la sfârșitul sesiunii: tipare în tranzacții și în setup-urile neexecutate (umbră), cum s-au comportat verdictele AI (dacă VETO-urile au blocat tranzacții bune, o spune), respectarea riscului, **ipoteze de testat în backtest** (nu se aplică automat).
- Revizuirea semnalului primește acum **istoricul recent** (tranzacțiile pe simbol, performanța verdictelor sale în umbră pe 30 de zile, următoarea știre, ora locală), iar profunzimea raționamentului este configurabilă (`CLAUDE_REVIEW_EFFORT`, implicit `medium`, înainte era `low`).
- Totul este stocat (`trade_reviews`), trimis pe Telegram și afișat în dashboard (pagina *Telegram & AI*), cu API (`/api/v1/trade-reviews`).
- **Poarta:** după 4 săptămâni în PAPER/DEMO, bilanțurile zilnice trebuie să fi produs ≥ 10 ipoteze concrete, dintre care cel puțin una să fie testabilă în backtest-ul existent. Rolul lui Claude este să scurteze ciclul **observație → ipoteză → test**, care până acum era 100 % manual.

### Etapa 1 — Costurile (4–6 săptămâni)
Costul este motivul numărul 1 pentru care strategiile ≈ 0 R devin negative.
- Cont **ECN/raw** la un broker cu comision fix (spread aproape zero + ~3–3.5 USD/lot/direcție) în loc de cont standard; măsurarea reală a spread-ului pe ore cu `spread_stats` (există deja).
- Filtru de oră bazat pe date: tranzacționare doar în orele în care spread-ul mediu < 15 % din distanța SL (parametru nou în Risk Engine, derivat din `spread_stats`, nu fixat manual).
- Re-rularea backtest-urilor C/C2 cu costurile **măsurate** ale noului cont (modul de cost `stats` există).
- **Poarta:** cost total mediu pe tranzacție (spread + comision + slippage) **< 0.12 R**; cel puțin o strategie trece de la negativ la ≥ 0 R pe TEST. Dacă nu, costurile nu erau problema și se merge direct la Etapa 2.

### Etapa 2 — Date și univers (6–8 săptămâni, în paralel cu Etapa 1)
Cercetarea proprie a concluzionat că pe prețul simplu, pe 1–3 perechi FX, nu există nimic. Dovezile academice spun că avantajul vine din **diversificare** și din **informație dincolo de preț**.
- Univers de **15–25 instrumente** tranzacționabile (nu doar de cercetare): FX majore + minore, aur, argint, 4–6 indici, petrol. Codul suportă deja arhiva pentru ele; trebuie trecute în `ACTIVE_SYMBOLS` pe DEMO și verificate costurile per instrument.
- Istoric intraday lung: M15/H1 pe **5+ ani** pentru tot universul (`ARCHIVE_EXTRA` există; crește ținta).
- Informație dincolo de preț: **surpriza știrilor** (actual − prognoză, deja arhivată prin CALHIST), **poziționarea COT** (import săptămânal CFTC, modul nou), **carry** (diferențiale de dobândă, import).
- **Poarta:** arhivă completă (≥ 95 % din barele așteptate) pe tot universul; cele trei surse non-preț disponibile ca feature-uri în `app/ml/features.py` și în plugin-uri de strategie.

### Etapa 3 — Cercetarea cu șanse reale (8–12 săptămâni)
Familii cu dovezi academice, testate exact cu protocolul existent (walk-forward, costuri reale, test o singură dată):
1. **Trend/momentum pe D1 multi-asset** (TSMOM a fost respins pe 1–3 perechi; literatura arată că funcționează doar **diversificat pe 20+ instrumente** cu dimensionare după volatilitate).
2. **Carry filtrat de trend** (carry-ul este cel mai bine documentat factor FX; filtrarea cu trend reduce riscul de crash).
3. **Drift post-surpriză de știri** (direcția în orele după o surpriză mare la NFP/CPI/dobândă — folosind CALHIST cu cifre).
4. **Poziționare extremă COT** ca filtru contrarian pe D1.
5. Mean-reversion intraday pe indici (unde există dovezi mai bune decât pe FX) — doar dacă costurile din Etapa 1 permit.
- Corecție pentru testare multiplă: cu ≥ 5 familii × parametri, pragul pre-înregistrat devine mai strict: **avg R > 0, limita inferioară CI90 > 0 (nu −0.02), PF > 1.2, ≥ 200 tranzacții TEST, pozitiv în ≥ 3 din 4 ani.**
- **Poarta:** cel puțin o strategie **PROMISING** după regula de mai sus. Dacă niciuna nu trece, concluzia onestă rămâne cea de azi și contul rămâne pe DEMO.

### Etapa 4 — Bucla „Claude propune → backtest-ul verifică → omul decide” (4 săptămâni)
- Ipotezele din bilanțurile zilnice ale lui Claude (Etapa 0) devin **job-uri de backtest** cu un click din dashboard: Claude formulează ipoteza ca parametri (`strategy`, `params`, `exit`, filtru), serverul rulează backtest-ul walk-forward pe arhivă și scrie rezultatul în bilanțul următor.
- **Bilanț săptămânal** separat, cu statistici pe 5 zile (acolo numerele încep să însemne ceva).
- Nimic nu se aplică automat: Claude **nu primește** niciodată drepturi de a schimba setări. Omul aprobă în dashboard, ca la modelele ML.
- **Poarta:** ≥ 20 ipoteze testate automat; raportul „ipoteze respinse / confirmate” documentat.

### Etapa 5 — Validare forward și decizia pentru bani reali (minimum 3 luni)
- Strategia PROMISING rulează **forward în PAPER** (runner-ul există: `FORWARD_TESTS`) și apoi pe **DEMO** cu execuție reală la broker: ≥ 200 tranzacții sau ≥ 12 săptămâni.
- Criterii pre-înregistrate pentru trecerea pe real: forward avg R > 0, CI90 inferior > 0, PF > 1.2, drawdown maxim < 10 % din equity, fără încălcări ale Risk Engine, rezultatul live în interiorul benzii prezise de backtest (nu mai slab cu > 50 %).
- Dimensionarea capitalului **după** cifrele reale: capital = (venit lunar dorit) / (avantaj mediu lunar măsurat). Dacă 1 050 USD/lună cere 150 000 USD, asta este cifra; dacă nu există capitalul, obiectivul se ajustează, nu riscul.
- **Poarta:** toate criteriile îndeplinite **și** capitalul disponibil acoperă obiectivul la risc ≤ 0.5 %/tranzacție. Altfel, rămâne DEMO.

### Etapa 6 — Operare pe real (dacă s-a trecut Etapa 5)
- VPS cu MT5 24/5, HTTPS, monitorizare de disponibilitate, alertă la deconectare, backup zilnic al bazei.
- Pornire cu **jumătate** din volumul calculat primele 2 luni.
- Raport lunar automat (Claude) cu comparația live vs. backtest; **regulă de oprire pre-înregistrată**: drawdown > 12 % sau 3 luni consecutive sub banda backtest-ului → înapoi pe DEMO.

---

## 5. Ce poate și ce nu poate face Claude în acest plan

| Poate (și face deja, de azi) | Nu poate și nu va putea |
|---|---|
| Să explice fiecare semnal și să blocheze (VETO) setup-uri cu motive concrete | Să prezică direcția prețului mai bine decât piața (dovezile din §1) |
| Să analizeze fiecare tranzacție închisă și să separe procesul de rezultat | Să deschidă, să modifice sau să închidă ordine |
| Să găsească tipare în zeci de tranzacții și setup-uri în umbră și să le formuleze ca ipoteze testabile | Să schimbe setări, volume sau niveluri |
| Să-și evalueze onest propriile verdicte față de rezultatele în umbră | Să „învețe” profitabil doar din 3–5 tranzacții pe zi (zgomot) |
| Să scrie bilanțuri zilnice/săptămânale și să țină memoria lecțiilor | Să garanteze un venit |

---

## 6. Ce am implementat în această sesiune (Etapa 0 + corecții)

1. **Claude analist de tranzacții** (`app/trade_review.py`, `app/ai_claude.py`): analiză post-tranzacție și bilanț zilnic, schema JSON strictă, stocare în `trade_reviews`, mesaje Telegram, rute API (`GET /api/v1/trade-reviews`, `POST /trade-reviews/daily`, `POST /trade-reviews/trade/{id}`), card în dashboard cu buton „Bilanț zilnic acum”.
2. **Revizuirea semnalului cu context istoric** (`Engine._ai_context`): tranzacții recente pe simbol, sumarul ultimelor 20 de tranzacții, performanța verdictelor AI în umbră pe 30 de zile, următoarea știre, ora locală; promptul instruiește modelul să le folosească.
3. **Profunzimea raționamentului configurabilă** (`CLAUDE_REVIEW_EFFORT`, implicit `medium`; înainte `low`), `max_tokens` 8000, același apel structurat pentru toate cele trei tipuri de analiză.
4. **Corecții:** portul din `.env.example` (8765 → 8787, conform codului și EA-ului); `numpy`, `scikit-learn`, `joblib` adăugate în `requirements.txt` (ML-ul nu funcționa pe o instalare nouă).
5. **11 teste noi** (`tests/test_trade_review.py`); suita completă trece.

Costul estimat al analistului: la `claude-opus-5-5`, o analiză de tranzacție ≈ 3–6 k tokeni intrare + 1–2 k ieșire ≈ **0.03–0.06 USD**; un bilanț zilnic ≈ 0.08–0.15 USD. La 5 tranzacții/zi: **sub 10 USD/lună**, înregistrat în `claude_usage`.

---

## 7. Răspunsul final la „sigur 50 USD pe zi”

Nu. Nici acest bot, nici altul, nu poate promite 50 USD **în fiecare zi**. Ce poate promite acest plan este altceva, mai valoros: **să afle adevărul repede și ieftin** — dacă există un avantaj exploatabil pe costurile, datele și universul potrivit, protocolul îl va găsi și îl va valida forward înainte de a risca un dolar real; dacă nu există, botul rămâne pe DEMO și banii rămân în cont. Pe baza tuturor dovezilor din §1, scenariul realist pentru un sistem care trece toate porțile este un randament **mediu** de 5–20 % pe an, cu luni negative — ceea ce înseamnă că 50 USD/zi în medie cere un capital de ordinul **50 000–150 000 USD**, nu un cod mai bun.

Dacă vrei, următorul pas practic este Etapa 1: deschiderea unui cont DEMO la un broker ECN/raw și re-rularea backtest-urilor pe costurile reale ale acelui cont. Este cel mai ieftin test cu cel mai mare potențial de a schimba concluzia.

### Surse
- ESMA, măsuri CFD și avertismente standardizate: [Finance Magnates](https://www.financemagnates.com/forex/brokers/esma-renews-cfds-restrictions-for-retail-clients/), [Babypips — 76 % din conturi pe minus](https://www.babypips.com/news/almost-80-percent-of-retail-traders-are-unprofitable), [IG — 78 % pierd](https://www.ig.com/en-ch/tag/Monetary%20Policy%20Committee%20%28United%20Kingdom%29)
- Barber, Lee, Liu, Odean: [Do Individual Day Traders Make Money? (Taiwan)](https://faculty.haas.berkeley.edu/odean/papers/day%20traders/Day%20Trading%20Skill%20110523.pdf), [Learning, Fast or Slow (2020)](https://ideas.repec.org/a/oup/rasset/v10y2020i1p61-93..html), [CXO Advisory — sinteză](https://www.cxoadvisory.com/individual-investing/do-day-traders-make-money/)
- Alpha Arena (nof1): [4 din 6 modele pe pierdere](https://forklog.com/en/four-out-of-six-ai-models-suffer-losses-in-trading-tournament/), [SCMP — DeepSeek/GPT-5](https://www.scmp.com/tech/tech-trends/article/3329784/deepseek-outperforms-ai-rivals-real-money-real-market-crypto-showdown), [analiză](https://medium.com/@farhat.hanne/alpha-arena-when-ais-fight-who-will-survive-3b179c84f7fa)
- Agenți LLM de trading: [StockBench](https://arxiv.org/pdf/2510.02209), [When Agents Trade](https://arxiv.org/pdf/2510.11695), [The Landscape of LLM Trading Agents (reproducere cu costuri)](https://www.alphaxiv.org/overview/2605.16895), [CLQT](https://arxiv.org/pdf/2606.29771)
- EA-uri și backtest vs. live: [MQL5 — 43 EA-uri de aur](https://www.mql5.com/en/blogs/post/773167), [MQL5 — Robustness Testing](https://www.mql5.com/en/articles/16957), [Quantopian 888 strategii / overfitting](https://medium.com/@johnmunn/the-overfitting-crisis-4be8716370f4), [backtest vs live](https://traderssecondbrain.com/guides/backtest-vs-live-trading)
- Firme prop: [Finance Magnates — 1 din 20](https://www.financemagnates.com/forex/only-1-in-20-traders-pass-prop-firm-challenges-reports-the-funded-trader/), [Track360 — statistici 2026](https://track360.io/blog/prop-trading-industry-statistics-2026), [The Prop Firm Guide](https://thepropfirmguide.com/prop-firm-statistics/)
- Factori FX și CTA: [Quantpedia — carry](https://quantpedia.com/Blog/page/107/?Page=33), [CXO — carry/momentum/value](https://www.cxoadvisory.com/value-premium/when-carry-momentum-and-value-work), [Trend factor FX 1995–2024](https://gupea.ub.gu.se/items/3e29c713-83ac-468f-8482-c9172501a4bd), [SG CTA 2015 flat](https://autumngold.com/SubLeaf/gold_report.php?id=114703), [CTA 2025](https://www.opalesque.com/OFI1345/html)
- Cercetarea proprie: `docs/research/STAGE_C_RESULTS.md`, `STAGE_C2_RESULTS.md`, `STAGE_D_RESULTS.md`, `STAGE_E_DISCOVERY.md`
