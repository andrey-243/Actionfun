"""
Cascade LLM : FreeTheAI (primary → secondary) → Groq (fallback)

Logique :
  1. cat/claude-opus-4-7 (FreeTheAI primary)
  2. cat/claude-4-6-sonnet (FreeTheAI secondary) — si 429/502/503/timeout sur #1
  3. Groq (groq_model passé par l'appelant) — si 403 checkin manquant sur #1 ou #2,
     ou si FreeTheAI complètement injoignable
"""

import httpx
import time
import os
from datetime import datetime, timezone


def _notify_telegram(message: str) -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        return
    try:
        httpx.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": message, "parse_mode": "Markdown"},
            timeout=5,
        )
    except Exception:
        pass

FREETHEAI_URL = "https://api.freetheai.xyz/v1/chat/completions"
HAPUPPY_URL = "https://beta.hapuppy.com/v1/chat/completions"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

FREETHEAI_PRIMARY = "bbl/gpt-4.1"
FREETHEAI_SECONDARY = "bbl/gpt-5.4-mini"
HAPUPPY_MODEL = "deepseek-v3.2"  # happy endpoint — pas de quota partagé, fallback avant Groq

# Désactive FreeTheAI jusqu'au prochain minuit UTC quand checkin manquant
_freetheai_skip_until: datetime | None = None
_freetheai_last_attempt: float = 0.0
FREETHEAI_MIN_INTERVAL = 6.0  # 10 RPM → 1 appel toutes les 6s max

# Throttle notifs Groq/Hapuppy — 1 ping par raison toutes les 30 min max
_notif_sent_at: dict[str, float] = {}
NOTIF_THROTTLE = 1800.0  # 30 min

_GROQ_MESSAGES = {
    "checkin":     "🔴 *Groq fallback* — Check-in Discord FreeTheAI manquant\nFais `/checkin` sur leur serveur pour débloquer.\nGroq actif jusqu'à minuit UTC.",
    "daily_limit": "🔴 *Groq fallback* — Quota journalier FreeTheAI épuisé\nReset automatique à minuit UTC. Groq actif d'ici là.",
    "rpm_429":     "🟡 *Groq fallback* — FreeTheAI rate limit (429 RPM)\nPassage temporaire sur Groq.",
    "502_timeout": "🟠 *Groq fallback* — FreeTheAI injoignable (502/timeout)\nVérifier le service. Groq en backup.",
    "hapuppy_down":"🟠 *Groq fallback* — Hapuppy down (502/timeout)\nVérifier les crédits Hapuppy.",
}
_HAPUPPY_MESSAGE = "🟡 *Hapuppy utilisé* — FreeTheAI indispo, Hapuppy en backup\nCrédits consommés — vérifie ton solde."


def _notify_groq_used(reason: str) -> None:
    now = time.monotonic()
    key = f"groq_{reason}"
    if now - _notif_sent_at.get(key, 0) < NOTIF_THROTTLE:
        return
    _notif_sent_at[key] = now
    _notify_telegram(_GROQ_MESSAGES.get(reason, f"🟠 *Groq fallback* — raison: {reason}"))


def _notify_hapuppy_used() -> None:
    now = time.monotonic()
    if now - _notif_sent_at.get("hapuppy", 0) < NOTIF_THROTTLE:
        return
    _notif_sent_at["hapuppy"] = now
    _notify_telegram(_HAPUPPY_MESSAGE)


def _freetheai_available() -> bool:
    global _freetheai_skip_until, _freetheai_last_attempt
    if _freetheai_skip_until is not None:
        if datetime.now(timezone.utc) >= _freetheai_skip_until:
            _freetheai_skip_until = None
        else:
            return False
    # Respecter le RPM : si dernier appel < 6s → skip sans sleep (Groq prend le relais)
    if time.monotonic() - _freetheai_last_attempt < FREETHEAI_MIN_INTERVAL:
        return False
    return True


def _next_utc_midnight() -> datetime:
    now = datetime.now(timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0).replace(
        day=now.day + 1
    )


def _is_checkin_error(r: httpx.Response) -> bool:
    try:
        err = r.json().get("error", {})
        if isinstance(err, dict):
            return err.get("type") == "daily_checkin_required"
        return False
    except Exception:
        return False


def _is_daily_limit_error(r: httpx.Response) -> bool:
    try:
        msg = r.json().get("error", {}).get("message", "")
        return "daily successful request limit exceeded" in msg
    except Exception:
        return False


def llm_call(
    messages: list[dict],
    *,
    groq_model: str = "meta-llama/llama-4-scout-17b-16e-instruct",
    max_tokens: int = 2000,
    temperature: float = 0.3,
    response_format: dict | None = None,
    timeout: int = 45,
    allow_groq: bool = True,
) -> dict:
    """
    Appel LLM avec cascade FreeTheAI → Groq.
    Retourne le dict OpenAI complet (choices[0].message.content accessible).
    """
    global _freetheai_skip_until

    freetheai_key = os.environ.get("FREETHEAI_API_KEY", "")
    payload_base = {
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    if response_format:
        payload_base["response_format"] = response_format

    groq_reason = "502_timeout"  # raison par défaut si on tombe sur Groq

    # ── FreeTheAI ──────────────────────────────────────────────────────────────
    if freetheai_key and _freetheai_available():
        for model in [FREETHEAI_PRIMARY, FREETHEAI_SECONDARY]:
            try:
                _freetheai_last_attempt = time.monotonic()
                r = httpx.post(
                    FREETHEAI_URL,
                    headers={"Authorization": f"Bearer {freetheai_key}", "Content-Type": "application/json"},
                    json={**payload_base, "model": model},
                    timeout=timeout,
                )

                if r.status_code == 403 or _is_checkin_error(r):
                    _freetheai_skip_until = _next_utc_midnight()
                    groq_reason = "checkin"
                    break

                if _is_daily_limit_error(r):
                    _freetheai_skip_until = _next_utc_midnight()
                    groq_reason = "daily_limit"
                    break

                if r.status_code == 200:
                    result = r.json()
                    result["_llm_source"] = "freetheai"
                    return result

                if r.status_code == 429:
                    groq_reason = "rpm_429"
                    break

                # 502 / 503 → essai sur le secondary
                continue

            except (httpx.TimeoutException, httpx.ConnectError, httpx.ReadError):
                continue

    # ── Hapuppy tertiary — happy endpoint, pas de quota partagé ──────────────
    hapuppy_key = os.environ.get("HAPUPPY_API_KEY", "")
    if hapuppy_key:
        try:
            r = httpx.post(
                HAPUPPY_URL,
                headers={"Authorization": f"Bearer {hapuppy_key}", "Content-Type": "application/json"},
                json={**payload_base, "model": HAPUPPY_MODEL},
                timeout=timeout,
            )
            if r.status_code == 200:
                result = r.json()
                result["_llm_source"] = "hapuppy"
                _notify_hapuppy_used()
                return result
            groq_reason = "hapuppy_down"
        except Exception:
            groq_reason = "hapuppy_down"

    # ── Groq (fallback final) ──────────────────────────────────────────────────
    if not allow_groq or os.environ.get("BATCH_MODE") == "1":
        raise RuntimeError("LLM unavailable (FreeTheAI/Hapuppy down, Groq disabled for batch)")

    for attempt in range(3):
        try:
            r = httpx.post(
                GROQ_URL,
                headers={"Authorization": f"Bearer {os.environ['GROQ_API_KEY']}"},
                json={**payload_base, "model": groq_model},
                timeout=timeout,
            )
            if r.status_code == 429 and attempt < 2:
                time.sleep(2 ** attempt)
                continue
            r.raise_for_status()
            result = r.json()
            result["_llm_source"] = "groq"
            _notify_groq_used(groq_reason)
            return result
        except httpx.HTTPStatusError:
            if attempt < 2:
                time.sleep(2 ** attempt)
                continue
            raise
    raise RuntimeError("LLM unreachable after all retries")


def llm_text(
    messages: list[dict],
    *,
    groq_model: str = "meta-llama/llama-4-scout-17b-16e-instruct",
    max_tokens: int = 2000,
    temperature: float = 0.3,
    timeout: int = 45,
    allow_groq: bool = True,
) -> str:
    """Shortcut → retourne directement le texte du message."""
    resp = llm_call(
        messages,
        groq_model=groq_model,
        max_tokens=max_tokens,
        temperature=temperature,
        timeout=timeout,
        allow_groq=allow_groq,
    )
    return resp["choices"][0]["message"]["content"].strip()


def llm_json(
    messages: list[dict],
    *,
    groq_model: str = "meta-llama/llama-4-scout-17b-16e-instruct",
    max_tokens: int = 2000,
    temperature: float = 0.3,
    timeout: int = 45,
    allow_groq: bool = True,
) -> dict:
    """Shortcut → retourne le JSON parsé (response_format: json_object)."""
    import json
    resp = llm_call(
        messages,
        groq_model=groq_model,
        max_tokens=max_tokens,
        temperature=temperature,
        response_format={"type": "json_object"},
        timeout=timeout,
        allow_groq=allow_groq,
    )
    content = resp["choices"][0]["message"]["content"].strip()
    # Strip markdown code blocks if model wrapped the JSON
    if content.startswith("```"):
        content = content.split("```", 2)[1]
        if content.startswith("json"):
            content = content[4:]
        content = content.rsplit("```", 1)[0].strip()
    if not content:
        raise ValueError("LLM returned empty content")
    return json.loads(content)
