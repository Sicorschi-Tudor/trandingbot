"""Claude review of a computed signal: explanation + optional VETO. Never creates orders or changes levels."""
from __future__ import annotations

import asyncio
import json
import logging

from .config import Settings, iso, redact, utcnow

log = logging.getLogger("bot.ai")

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
- Scrie explicația în limba română, clar și concis (maxim 600 de caractere). Lista de riscuri: fraze scurte în română. Câmpul invalidation: condiția concretă care anulează setup-ul.
Răspunde doar cu obiectul JSON cerut."""


class ClaudeReviewer:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.last_ok_utc: str | None = None
        self.last_error: str | None = None
        self._client = None

    @property
    def key_set(self) -> bool:
        return bool(self.settings.ANTHROPIC_API_KEY)

    @property
    def enabled(self) -> bool:
        return bool(self.settings.CLAUDE_ENABLED)

    def status(self) -> dict:
        return {
            "enabled": self.enabled, "model": self.settings.CLAUDE_MODEL, "veto_enabled": self.settings.CLAUDE_VETO_ENABLED,
            "key_set": self.key_set, "last_ok_utc": self.last_ok_utc, "last_error": self.last_error,
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
        import anthropic
        client = self._get_client()
        payload = self.build_payload(signal, context)
        user_msg = ("Evaluează următorul semnal calculat de motorul determinist. Datele sunt verificate.\n\n"
                    + json.dumps(payload, ensure_ascii=False, default=str))
        common = dict(
            model=self.settings.CLAUDE_MODEL,
            max_tokens=4000,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_msg}],
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": REVIEW_SCHEMA}},
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
        return self.parse_response(resp)

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
