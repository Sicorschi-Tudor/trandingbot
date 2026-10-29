# AI trader — Claude decide pe grafic (umbră → live)

Componenta cerută de proprietar: Claude primește graficul și decide singur când intră, unde pune SL/TP, cât ține și cum gestionează fiecare poziție. Este construită astfel încât să poată fi **măsurată înainte de a risca ceva** și să nu poată ocoli nicio protecție a botului.

## Ce primește Claude la fiecare bară închisă

- lumânările închise pe timeframe-ul ales (`AI_TRADER_TIMEFRAME`, implicit M15: ultimele 60), pe H1 (30) și H4 (20);
- **contextul de termen lung** calculat din arhivă (ani de bare D1 descărcate din MT5): 120 de lumânări zilnice, 104 săptămânale, maximul/minimul pe 52 de săptămâni și poziția prețului în acest interval, EMA50/EMA200 pe D1, randamentele pe 1 săptămână … 12 luni, ATR zilnic și percentila volatilității, ultimii 8 pivoți majori — ca modelul să „știe tot graficul”;
- cotația (bid/ask/spread/digits/stops level), ATR(14), ce vede strategia deterministă (direcție, scor, regim), sesiunea, ora locală, următoarea știre importantă;
- pozițiile deschise pe simbol (direcție, R deschis, distanța în ATR), ultimele 6 tranzacții pe simbol;
- **propriile statistici** (câte decizii etichetate, rata de câștig, R mediu) — ca să devină mai selectiv când greșește;
- constrângerile: distanța minimă de SL (stops level + 5 puncte sau 0,3×ATR), RR minim 1,2, durata 15–1440 min, dacă piramidarea e permisă.

Răspunsul este JSON strict: `action` (NONE/BUY/SELL), `stop_loss`, `take_profit`, `hold_minutes`, `confidence`, `setup`, `reasoning`, `invalidation`. Implicit este **NONE**.

## Validare (înainte de orice)

O decizie este `INVALID` (înregistrată, dar niciodată executată) dacă: SL/TP lipsesc sau sunt de partea greșită a prețului; SL e mai aproape decât minimul; RR < 1,2; durata e în afara 15–1440 min.

## Moduri

| Mod | Intrări (`entry_mode`) | Gestionare (`manage_mode`) |
|---|---|---|
| `off` | nimic | nimic |
| `shadow` | decizia e înregistrată în `ai_trader_decisions` și **etichetată automat** cu ce s-ar fi întâmplat (umplere la bara următoare, SL primul, spread + slippage, time-stop = `hold_minutes`) | la fiecare `AI_TRADER_MANAGE_EVERY_MIN` minute, per poziție deschisă: HOLD / TIGHTEN_SL / CLOSE, doar înregistrat |
| `live` | umbră **+** decizia devine un semnal normal (`ai-trader-v1`) care trece prin **același pipeline**: armare, expirare, Risk Engine (inclusiv piramidarea), EA; revizuirea Claude e sărită (decizia are deja raționamentul) | acțiunile se aplică, dar **numai cele care reduc riscul**: SL mai strâns (niciodată mai larg, niciodată dincolo de preț) sau închidere la piață; pozițiile deschise de AI se închid la expirarea `hold_minutes` (`AI_TIME`) |

Trecerea pe `live` cere confirmarea `ACCEPT AI LIVE` (dashboard sau API). Apelurile sunt plafonate (`AI_TRADER_MAX_CALLS_PER_DAY`, implicit 300). Cost orientativ la `claude-opus-5-5`, efort mediu: ~0,05–0,10 USD per decizie (contextul de termen lung e mare); pe M15 într-o sesiune de 4 ore ≈ 16 decizii/simbol ≈ 1–2 USD/zi/simbol.

## Cum se citește rezultatul

Pagina *Telegram & AI → AI trader*: decizii etichetate, rată de câștig, **R mediu cu CI90**, profit factor, ΣR, lista deciziilor cu raționament, stare și rezultat. API: `GET /api/v1/ai-trader`, `PUT /api/v1/ai-trader/settings`, `POST /api/v1/ai-trader/decide/{symbol}`.

Regula de promovare (aceeași ca pentru modelele ML și pentru strategii): umbră cel puțin **4 săptămâni și ≥ 100 de decizii etichetate**; `live` doar dacă R mediu > 0 cu limita inferioară CI90 > −0,02 și PF > 1,1. Dovezile publice (competiția Alpha Arena, benchmark-urile academice) arată că modelele AI care decid singure tranzacțiile **pierd**; acest modul există ca să verificăm asta pe botul tău, cu cifre, nu ca să-l credem pe cuvânt.

## Ce nu poate face

Nu schimbă setări, nu mărește riscul pe tranzacție, nu lărgește stopuri, nu mută TP-ul mai departe, nu adaugă poziții în afara regulii de piramidare, nu lucrează pe simboluri din afara `TRADE_SYMBOLS`, nu trimite ordine fără Risk Engine.
