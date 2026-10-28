# MT5 AI Bot — v1 (demo)

Prima versiune, simplă și rapidă, a botului descris în specificația V3. Scopul ei: s-o conectăm la un **cont demo MetaTrader 5** și să verificăm împreună tot fluxul (date live → analiză → Claude → Risk Engine → ordin → poziție → închidere → Telegram).

> Semnalele sunt informative. Probabilitățile din v1 sunt **euristice, necalibrate**. Nu există garanție de profit. Modul LIVE este blocat în v1.

## Cum funcționează

```
MT5 (cont demo) ──EA AIBridgeEA──HTTP 127.0.0.1──▶ Server Python (FastAPI)
                                                    ├─ strategie deterministă trend-v1 (EMA/RSI/MACD/ATR, M5 + H1)
                                                    ├─ Claude (doar explică / poate pune VETO, nu poate deschide ordine)
                                                    ├─ Risk Engine (16 verificări) ──▶ comenzi către EA
                                                    ├─ Position manager (break-even, time-stop)
                                                    ├─ Telegram (outbox cu retry)
                                                    └─ Dashboard web http://127.0.0.1:8787
```

- **De ce EA și nu pachetul Python `MetaTrader5`?** Pachetul Python merge doar pe Windows; tu ești pe macOS. EA-ul MQL5 merge și în MT5 pentru Mac, și pe Windows/VPS.
- Moduri: `SIGNAL_ONLY` (doar semnale), `PAPER` (simulator intern), `DEMO` (ordine reale în contul demo; botul verifică singur că este cont demo). `LIVE` e refuzat.

## Pasul 1 — completează `.env`

Fișierul `.env` există deja. `ADMIN_TOKEN` și `MT5_BRIDGE_KEY` sunt deja generate. Tu adaugi:

| Variabilă | Ce pui |
|---|---|
| `ANTHROPIC_API_KEY` | cheia ta de la console.anthropic.com |
| `TELEGRAM_BOT_TOKEN` | (opțional) tokenul botului existent |
| `TELEGRAM_CHAT_ID` | (opțional) ID-ul grupului |
| `TELEGRAM_ENABLED` | `true` după ce ai pus token + chat_id |
| `ACTIVE_SYMBOLS` | simbolurile exacte ale brokerului, ex. `EURUSD` sau `EURUSD.a,GBPUSD.a` |
| `SESSION_START` / `SESSION_END` | fereastra de 4 ore (ora Chișinău), implicit 09:00–13:00 |

Nu trimite niciodată cheile în chat.

## Pasul 2 — pornește serverul

```bash
./run.sh
```

Deschide http://127.0.0.1:8787 → iconița ⚙ → lipește `ADMIN_TOKEN` din `.env` (necesar pentru butoanele de control).

Vrei să vezi interfața fără MT5? Pune `DATA_SOURCE=sim` în `.env` (date SIMULATE, marcate clar; ordine doar PAPER).

## Pasul 3 — conectează MT5 (cont demo)

Pașii detaliați sunt în [mt5/README_EA.md](mt5/README_EA.md). Pe scurt:
1. Copiază `mt5/AIBridgeEA.mq5` în `MQL5/Experts` (File → Open Data Folder), compilează în MetaEditor (F7).
2. Tools → Options → Expert Advisors: bifează *Allow algorithmic trading* și *Allow WebRequest for listed URL* → adaugă `http://127.0.0.1:8787`.
3. Pune EA-ul pe un grafic, la `InpBridgeKey` lipește `MT5_BRIDGE_KEY` din `.env`, apasă butonul **Algo Trading**.
4. În dashboard, „Bridge EA” devine ONLINE și apare contul demo.

## Pasul 4 — testăm împreună

1. Rămâi în **PAPER** câteva minute: verifică graficul live, analiza, semnalele.
2. Pagina *Control & Risc* → mod **DEMO** → **Execuție: armată**.
3. **Ordin de test** (BUY/SELL, volum minim, cu SL/TP) → verifici în MT5 că apare poziția, apoi o închizi din dashboard.
4. Lași botul să lucreze singur în fereastra de sesiune și urmărim semnalele, verdictele Claude și execuțiile.

Kill switch: *Pauză intrări noi* (pozițiile rămân protejate de SL/TP). *Închide TOATE pozițiile botului* cere dublă confirmare și atinge doar pozițiile cu magic-ul botului.

## Teste

```bash
.venv/bin/python -m pytest -q
```

## Ce urmează în v2 (după testul pe demo)
Model ML calibrat + backtest walk-forward, calendar economic, mai multe timeframe-uri/strategii, Analytics, PostgreSQL, autentificare completă, deploy pe VPS cu HTTPS.

Detalii tehnice: [docs/CONTRACT.md](docs/CONTRACT.md).
