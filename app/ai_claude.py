"""Claude review of a computed signal: explanation + optional VETO. Never creates orders or changes levels."""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Callable

from .config import Settings, iso, redact, utcnow

log = logging.getLogger("bot.ai")

# USD per million tokens: (input, output, cache read)
PRICES_PER_MTOK: dict[str, tuple[float, float, float]] = {
    "claude-opus-5-5": (4.00, 20.00, 0.20),
    "claude-sonnet-5-5": (2.00, 10.00, 0.20),
    "claude-haiku-5-5": (0.10, 0.50, 0.01),
}


def model_prices(model: str | None) -> tuple[float, float, float] | None:
    m = (model or "").strip().lower()
    if m in PRICES_PER_MTOK:
        return PRICES_PER_MTOK[m]
    for k, v in PRICES_PER_MTOK.items():  # dated ids, e.g. claude-opus-5-5-20260101
        if m.startswith(k + "-"):
            return v
    return None


def usage_cost(model: str | None, input_tokens: int, output_tokens: int, cache_read_tokens: int = 0) -> float | None:
    """USD cost of one call; None for an unknown model."""
    p = model_prices(model)
    if p is None:
        return None
    pin, pout, pcache = p
    return round((input_tokens * pin + output_tokens * pout + cache_read_tokens * pcache) / 1_000_000, 6)


def usage_record(resp, default_model: str, signal_id: str | None) -> dict | None:
    u = getattr(resp, "usage", None)
    if u is None:
        return None

    def n(name: str) -> int:
        try:
            return int(getattr(u, name, 0) or 0)
        except (TypeError, ValueError):
            return 0

    model = getattr(resp, "model", None) or default_model
    if not isinstance(model, str):
        model = default_model
    tin, tout, tcache = n("input_tokens"), n("output_tokens"), n("cache_read_input_tokens")
    return {"ts": time.time(), "signal_id": signal_id, "model": model, "input_tokens": tin, "output_tokens": tout,
            "cache_read_tokens": tcache, "cost_usd": usage_cost(model, tin, tout, tcache)}

REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["AGREE", "CAUTION", "VETO"]},
        "explanation": {"type": "string", "description": "Explicație în limba română, maxim 600 de caractere."},
        "risks": {"type": "array", "items": {"type": "string"}},
        "invalidation": {"type": "string"},
    },
    "required": ["verdict", "explanation", "risks", "invalidation"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """Ești un evaluator de risc pentru un bot de trading care rulează pe un cont DEMO / PAPER MetaTrader 5.
Primești date structurate deja verificate (cotații, indicatori calculați local, nivelurile calculate de un motor determinist).
Reguli stricte:
- NU poți schimba direcția, intrarea, Stop Loss-ul sau Take Profit-ul. Nu propune alte niveluri și nu creezi ordine.
- Rolul tău: explici pe scurt semnalul și dai un verdict: AGREE (datele susțin setup-ul), CAUTION (există riscuri notabile, dar nu un motiv clar de blocare) sau VETO.
- Folosește VETO numai când datele arată un motiv concret, de exemplu: trend contrar pe timeframe-ul superior, cumpărare în zonă de supracumpărare spre rezistență (sau vânzare în supravânzare spre suport), volatilitate sau spread anormale, date contradictorii.
- Nu inventa știri sau evenimente care nu apar în date. Nu promite profit; probabilitățile sunt estimări euristice, nu garanții.
- Dacă primești în context istoricul recent al botului (tranzacții închise pe simbol, cum s-au comportat verdictele tale anterioare, știri apropiate), folosește-l: un setup identic cu unul pierdut de mai multe ori recent, sau o știre majoră în orele următoare, sunt motive legitime de CAUTION sau VETO.
- Scrie explicația în limba română, clar și concis (maxim 600 de caractere). Lista de riscuri: fraze scurte în română. Câmpul invalidation: condiția concretă care anulează setup-ul.
Răspunde doar cu obiectul JSON cerut."""

EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")

# ---- AI trader (discretionary decisions on the chart; shadow by default, executed only when the owner enables it) --
TRADER_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["NONE", "BUY", "SELL"]},
        "stop_loss": {"type": ["number", "null"], "description": "Preț SL (obligatoriu pentru BUY/SELL)."},
        "take_profit": {"type": ["number", "null"], "description": "Preț TP (obligatoriu pentru BUY/SELL)."},
        "hold_minutes": {"type": ["integer", "null"], "description": "Cât ține poziția cel mult (15–1440 minute)."},
        "confidence": {"type": "number", "description": "0–1, încrederea ta în setup."},
        "setup": {"type": "string", "description": "Numele setup-ului (ex: breakout, pullback, range, fără setup)."},
        "reasoning": {"type": "string", "description": "Raționament în română, max 600 de caractere: ce vezi pe lumânări."},
        "invalidation": {"type": "string", "description": "Condiția care anulează setup-ul."},
    },
    "required": ["action", "stop_loss", "take_profit", "hold_minutes", "confidence", "setup", "reasoning", "invalidation"],
    "additionalProperties": False,
}

TRADER_PROMPT = """Ești un trader discreționar care citește graficul unui instrument (lumânări închise pe mai multe timeframe-uri,
indicatori, spread, sesiune, știri, pozițiile deja deschise, istoricul recent) și decide DACĂ merită o tranzacție ACUM, la piață.
Reguli:
- Implicit răspunsul este NONE. Intră doar când vezi un setup concret pe lumânări (structură, nivel, impuls, respingere,
  confluență între timeframe-uri) cu un stop logic și un raport câștig/risc de cel puțin 1,5. Nu tranzacționa zgomot.
- BUY/SELL cer OBLIGATORIU stop_loss și take_profit ca prețuri absolute, de partea corectă a prețului curent
  (BUY: SL < ask < TP; SELL: TP < bid < SL). Distanța SL ≥ constraints.min_sl_distance (dată în date). Intrarea e la piață.
- hold_minutes: cât ții cel mult poziția dacă nu atinge SL/TP (15–1440). Alege-l după timeframe-ul setup-ului.
- Folosește doar datele primite. Nu inventa niveluri, știri sau prețuri. Dacă spread-ul e mare față de stop sau o știre
  majoră e aproape, preferă NONE.
- Dacă există deja o poziție pe simbol, propune o nouă intrare doar în aceeași direcție și doar dacă datele o justifică
  (constraints.pyramiding spune dacă e permisă).
- Ține cont de propriile statistici (self_stats: cum au ieșit deciziile tale anterioare); dacă sunt negative, fii mai selectiv.
- confidence onest: 0,5 = monedă aruncată. Totul în română. Răspunde doar cu obiectul JSON cerut."""

MANAGE_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["HOLD", "TIGHTEN_SL", "CLOSE"]},
        "new_sl": {"type": ["number", "null"], "description": "Noul SL (doar pentru TIGHTEN_SL), mai aproape de preț decât cel curent."},
        "confidence": {"type": "number"},
        "reasoning": {"type": "string", "description": "Română, max 400 de caractere."},
    },
    "required": ["action", "new_sl", "confidence", "reasoning"],
    "additionalProperties": False,
}

MANAGE_PROMPT = """Ești managerul unei poziții DESCHISE a unui bot de trading. Primești poziția (direcție, intrare, SL, TP, profit
curent în R, MFE/MAE, minute deschise, termenul maxim), lumânările recente, cotația, ATR, știrile apropiate.
Poți doar: HOLD (nimic), TIGHTEN_SL (muți SL-ul mai aproape de preț — niciodată mai departe, niciodată dincolo de prețul
curent) sau CLOSE (închizi la piață). Nu poți mări poziția, nu poți muta TP-ul mai departe, nu poți lărgi SL-ul.
Strânge SL-ul când structura o justifică (profit blocat sub un nivel respectat), închide când setup-ul s-a invalidat sau
când o știre majoră e iminentă și poziția e aproape de zero. Nu reacționa la fiecare lumânare: HOLD este răspunsul implicit.
Totul în română. Răspunde doar cu obiectul JSON cerut."""

# ---- post-trade analysis (Claude as trade analyst; never changes settings or orders) --------------------------------
TRADE_REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "process_quality": {"type": "string", "enum": ["GOOD", "ACCEPTABLE", "POOR"],
                            "description": "Calitatea PROCESULUI (intrare, gestionare, ieșire), independent de rezultat."},
        "outcome_class": {"type": "string", "enum": ["GOOD_PROCESS_WIN", "GOOD_PROCESS_LOSS", "BAD_PROCESS_WIN",
                                                     "BAD_PROCESS_LOSS", "NEUTRAL"]},
        "summary": {"type": "string", "description": "Rezumat în română, maxim 500 de caractere."},
        "what_worked": {"type": "array", "items": {"type": "string"}},
        "what_failed": {"type": "array", "items": {"type": "string"}},
        "lessons": {"type": "array", "items": {"type": "string"},
                    "description": "Lecții concrete, verificabile, maxim 4."},
        "tags": {"type": "array", "items": {"type": "string"},
                 "description": "Etichete scurte (ex: contra-trend, stiri, spread-mare, iesire-prematura, time-stop)."},
        "exit_assessment": {"type": "string", "enum": ["OPTIMAL", "EARLY", "LATE", "FORCED", "UNKNOWN"]},
    },
    "required": ["process_quality", "outcome_class", "summary", "what_worked", "what_failed", "lessons", "tags",
                 "exit_assessment"],
    "additionalProperties": False,
}

TRADE_REVIEW_PROMPT = """Ești analistul de tranzacții al unui bot de trading determinist care rulează pe un cont DEMO / PAPER MetaTrader 5.
Primești o tranzacție ÎNCHISĂ cu toate datele: semnalul care a generat-o (indicatori, motive, verdictul tău de la intrare), nivelurile, umplerea, traiectoria prețului cât a fost deschisă (MFE/MAE în R), motivul închiderii, rezultatul net în bani și în R, plus istoricul recent al botului.
Reguli:
- Evaluezi PROCESUL, nu rezultatul: o tranzacție pierdută poate fi un proces bun (setup valid, risc respectat, ieșire la SL), iar una câștigată poate fi un proces slab (contra-trend, spread mare, noroc).
- Folosește doar datele primite. Nu inventa evenimente, nu presupune prețuri care nu apar.
- Lecțiile trebuie să fie concrete și verificabile pe date (ex: „3 din ultimele 4 tranzacții SELL pe EURUSD la ora 09 au atins SL înainte de +0.3R”), nu generalități.
- Nu propui modificarea nivelurilor, a volumului sau a setărilor; poți sugera ipoteze de verificat în backtest.
- Totul în limba română, concis. Răspunde doar cu obiectul JSON cerut."""

DAILY_REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {"type": "string", "description": "O propoziție despre ziua de trading."},
        "summary": {"type": "string", "description": "Bilanț în română, maxim 900 de caractere."},
        "patterns": {"type": "array", "items": {"type": "string"},
                     "description": "Tipare observate în tranzacții și în setup-urile neexecutate (umbră)."},
        "veto_assessment": {"type": "string",
                            "description": "Cum s-au comportat verdictele AI (AGREE/CAUTION/VETO) față de rezultatele în umbră."},
        "risk_assessment": {"type": "string", "description": "Respectarea limitelor de risc și expunerea zilei."},
        "hypotheses_to_test": {"type": "array", "items": {"type": "string"},
                               "description": "Ipoteze de verificat în backtest/forward, maxim 5. NU se aplică automat."},
        "confidence": {"type": "string", "enum": ["LOW", "MEDIUM", "HIGH"],
                       "description": "Cât de sigur ești pe concluzii, dat fiind numărul de tranzacții."},
    },
    "required": ["headline", "summary", "patterns", "veto_assessment", "risk_assessment", "hypotheses_to_test",
                 "confidence"],
    "additionalProperties": False,
}

DAILY_REVIEW_PROMPT = """Ești analistul de performanță al unui bot de trading determinist (cont DEMO / PAPER MetaTrader 5).
Primești bilanțul unei zile: tranzacțiile închise (cu R, motiv de ieșire, durată, MFE/MAE), statistici ale setup-urilor NEexecutate (etichete „umbră”: ce ar fi făcut semnalele blocate de filtre, respinse de risc sau cu VETO), performanța pe verdictele AI, limitele de risc și bilanțul de ieri.
Reguli:
- Separă zgomotul de semnal: cu 1–5 tranzacții pe zi, aproape nimic nu este semnificativ statistic; spune explicit asta și setează confidence corespunzător.
- Caută tipare verificabile (oră, simbol, direcție, regim, motiv de ieșire, spread, știri) și compară-le cu etichetele în umbră.
- Evaluează onest verdictele AI: dacă setup-urile cu VETO au avut rezultate în umbră mai bune decât cele executate, spune-o.
- Ipotezele trebuie formulate ca teste concrete (ce filtru, ce prag, ce metrică ar trebui să se îmbunătățească); ele NU se aplică automat, le decide omul după backtest.
- Nu promite profit, nu extrapola o zi bună. Totul în limba română, concis. Răspunde doar cu obiectul JSON cerut."""


class ClaudeReviewer:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.last_ok_utc: str | None = None
        self.last_error: str | None = None
        self._client = None
        # called with a usage record (see usage_record) after every API response
        self.on_usage: Callable[[dict], None] | None = None

    @property
    def key_set(self) -> bool:
        return bool(self.settings.ANTHROPIC_API_KEY)

    @property
    def enabled(self) -> bool:
        return bool(self.settings.CLAUDE_ENABLED)

    @property
    def effort(self) -> str:
        e = str(getattr(self.settings, "CLAUDE_REVIEW_EFFORT", "medium") or "medium").strip().lower()
        return e if e in EFFORT_LEVELS else "medium"

    def status(self) -> dict:
        return {
            "enabled": self.enabled, "model": self.settings.CLAUDE_MODEL, "veto_enabled": self.settings.CLAUDE_VETO_ENABLED,
            "key_set": self.key_set, "last_ok_utc": self.last_ok_utc, "last_error": self.last_error,
            "effort": self.effort,
            "trade_review_enabled": bool(getattr(self.settings, "CLAUDE_TRADE_REVIEW_ENABLED", True)),
            "daily_review_enabled": bool(getattr(self.settings, "CLAUDE_DAILY_REVIEW_ENABLED", True)),
        }

    def _get_client(self):
        if self._client is None:
            import anthropic
            self._client = anthropic.AsyncAnthropic(api_key=self.settings.ANTHROPIC_API_KEY, max_retries=2)
        return self._client

    @staticmethod
    def build_payload(signal: dict, context: dict | None = None) -> dict:
        keys = ("symbol", "timeframe", "direction", "entry", "stop_loss", "take_profit", "rr", "spread_points",
                "probabilities", "reasons", "indicators", "horizon_minutes", "expires_at_utc", "feed_label", "mode")
        data = {k: signal.get(k) for k in keys}
        if context:
            data["context"] = context
        return data

    async def review(self, signal: dict, context: dict | None = None) -> dict:
        model = self.settings.CLAUDE_MODEL
        base = {"status": "DISABLED", "verdict": None, "explanation": "", "risks": [], "model": model}
        if not self.enabled or not self.key_set:
            base["explanation"] = "Analiza Claude este dezactivată (lipsește ANTHROPIC_API_KEY sau CLAUDE_ENABLED=false)."
            return base
        try:
            parsed = await asyncio.wait_for(self._call(signal, context), timeout=float(self.settings.CLAUDE_TIMEOUT_SECONDS))
        except asyncio.TimeoutError:
            return self._error(base, f"timeout după {self.settings.CLAUDE_TIMEOUT_SECONDS:g} s")
        except Exception as e:
            return self._error(base, self._describe(e))
        verdict = parsed.get("verdict")
        if verdict not in ("AGREE", "CAUTION", "VETO"):
            return self._error(base, f"verdict invalid: {verdict!r}")
        self.last_ok_utc = iso(utcnow())
        self.last_error = None
        risks = [str(r) for r in (parsed.get("risks") or [])][:8]
        inv = str(parsed.get("invalidation") or "").strip()
        if inv:
            risks.append(f"Invalidare (Claude): {inv}")
        return {
            "status": "DONE", "verdict": verdict,
            "explanation": str(parsed.get("explanation") or "")[:600],
            "risks": risks,
            "model": model,
        }

    def _error(self, base: dict, msg: str) -> dict:
        msg = redact(msg, self.settings.secret_values())
        self.last_error = msg
        log.warning("Claude review failed: %s", msg)
        out = dict(base)
        out["status"] = "ERROR"
        out["explanation"] = f"Analiza Claude a eșuat: {msg}"
        return out

    @staticmethod
    def _describe(e: Exception) -> str:
        try:
            import anthropic
        except Exception:  # pragma: no cover
            return repr(e)
        if isinstance(e, anthropic.AuthenticationError):
            return "cheie API invalidă (401)"
        if isinstance(e, anthropic.PermissionDeniedError):
            return "acces refuzat (403)"
        if isinstance(e, anthropic.RateLimitError):
            return "limită de rată atinsă (429)"
        if isinstance(e, anthropic.APIStatusError):
            return f"eroare API {e.status_code}: {getattr(e, 'message', '')}"[:300]
        if isinstance(e, anthropic.APIConnectionError):
            return "eroare de conexiune la API"
        if isinstance(e, (ValueError, json.JSONDecodeError)):
            return f"răspuns invalid: {e}"[:300]
        return repr(e)[:300]

    async def _call(self, signal: dict, context: dict | None) -> dict:
        payload = self.build_payload(signal, context)
        user_msg = ("Evaluează următorul semnal calculat de motorul determinist. Datele sunt verificate.\n\n"
                    + json.dumps(payload, ensure_ascii=False, default=str))
        return await self._call_json(SYSTEM_PROMPT, user_msg, REVIEW_SCHEMA, signal.get("id"))

    # ---- trade analyst ------------------------------------------------------------------------------------------
    async def review_trade(self, payload: dict) -> dict:
        """Post-trade analysis of a CLOSED trade (payload built by app.trade_review). Same status contract as review():
        DISABLED (no key / disabled) · ERROR (timeout, API error, invalid JSON) · DONE (+ the schema fields)."""
        return await self._analysis(TRADE_REVIEW_PROMPT, TRADE_REVIEW_SCHEMA,
                                    "Analizează următoarea tranzacție ÎNCHISĂ a botului.\n\n", payload,
                                    (payload.get("trade") or {}).get("signal_id"))

    async def decide_entry(self, payload: dict) -> dict:
        """AI trader: discretionary entry decision on the chart (payload built by app.ai_trader)."""
        return await self._analysis(TRADER_PROMPT, TRADER_SCHEMA,
                                    "Decide dacă intri ACUM pe acest instrument.\n\n", payload, None)

    async def decide_manage(self, payload: dict) -> dict:
        """AI trader: management decision for one open bot position (HOLD / TIGHTEN_SL / CLOSE)."""
        return await self._analysis(MANAGE_PROMPT, MANAGE_SCHEMA,
                                    "Decide ce faci cu această poziție deschisă.\n\n", payload, None)

    async def review_day(self, payload: dict) -> dict:
        """Daily performance review (trades + shadow labels of one local day)."""
        return await self._analysis(DAILY_REVIEW_PROMPT, DAILY_REVIEW_SCHEMA,
                                    "Fă bilanțul următoarei zile de trading a botului.\n\n", payload, None)

    async def _analysis(self, system: str, schema: dict, lead: str, payload: dict, signal_id: str | None) -> dict:
        model = self.settings.CLAUDE_MODEL
        base = {"status": "DISABLED", "model": model}
        if not self.enabled or not self.key_set:
            base["error"] = "Analiza Claude este dezactivată (lipsește ANTHROPIC_API_KEY sau CLAUDE_ENABLED=false)."
            return base
        user_msg = lead + json.dumps(payload, ensure_ascii=False, default=str)
        try:
            parsed = await asyncio.wait_for(self._call_json(system, user_msg, schema, signal_id),
                                            timeout=float(self.settings.CLAUDE_TIMEOUT_SECONDS) * 2)
        except asyncio.TimeoutError:
            return self._analysis_error(base, f"timeout după {self.settings.CLAUDE_TIMEOUT_SECONDS * 2:g} s")
        except Exception as e:
            return self._analysis_error(base, self._describe(e))
        missing = [k for k in schema["required"] if k not in parsed]
        if missing:
            return self._analysis_error(base, f"răspuns incomplet: lipsesc {missing}")
        self.last_ok_utc = iso(utcnow())
        self.last_error = None
        out = {"status": "DONE", "model": model}
        for k, spec in schema["properties"].items():
            v = parsed.get(k)
            if spec.get("type") == "array":
                out[k] = [str(x)[:300] for x in (v or []) if x is not None][:8]
            elif isinstance(v, bool) or isinstance(v, (int, float)) or v is None and spec.get("type") in ("number", "integer"):
                out[k] = v
            else:
                out[k] = str(v or "")[:1200]
        return out

    def _analysis_error(self, base: dict, msg: str) -> dict:
        msg = redact(msg, self.settings.secret_values())
        self.last_error = msg
        log.warning("Claude trade analysis failed: %s", msg)
        return {**base, "status": "ERROR", "error": msg}

    async def _call_json(self, system: str, user_msg: str, schema: dict, signal_id: str | None) -> dict:
        """One structured-output call: adaptive thinking (always on for this model family) at CLAUDE_REVIEW_EFFORT, strict JSON
        schema, server-side refusal fallback when the API offers it."""
        import anthropic
        client = self._get_client()
        common = dict(
            model=self.settings.CLAUDE_MODEL,
            max_tokens=8000,
            system=system,
            messages=[{"role": "user", "content": user_msg}],
            output_config={"effort": self.effort, "format": {"type": "json_schema", "schema": schema}},
        )
        try:
            resp = await client.beta.messages.create(
                betas=["server-side-fallback-2026-07-01"],
                extra_body={"fallbacks": "default"},
                **common,
            )
        except anthropic.BadRequestError as e:
            msg = str(e).lower()
            if "fallback" in msg or "beta" in msg:
                resp = await client.messages.create(**common)
            else:
                raise
        self._record_usage(resp, signal_id)
        return self.parse_response(resp)

    def _record_usage(self, resp, signal_id: str | None) -> None:
        if not self.on_usage:
            return
        try:
            rec = usage_record(resp, self.settings.CLAUDE_MODEL, signal_id)
            if rec:
                self.on_usage(rec)
        except Exception as e:  # accounting must never break the review
            log.warning("Claude usage not recorded: %r", e)

    @staticmethod
    def parse_response(resp) -> dict:
        if getattr(resp, "stop_reason", None) == "refusal":
            raise ValueError("modelul a refuzat cererea (stop_reason=refusal)")
        text = None
        for block in getattr(resp, "content", None) or []:
            if getattr(block, "type", None) == "text":
                text = block.text
                break
        if not text:
            raise ValueError("răspuns fără bloc text")
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError("JSON-ul nu este obiect")
        return data
