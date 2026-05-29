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
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

FREETHEAI_PRIMARY = "bbl/gpt-4.1"
FREETHEAI_SECONDARY = "bbl/gpt-5.4-mini"

# Désactive FreeTheAI jusqu'au prochain minuit UTC quand checkin manquant
_freetheai_skip_until: datetime | None = None


def _freetheai_available() -> bool:
    global _freetheai_skip_until
    if _freetheai_skip_until is None:
        return True
    if datetime.now(timezone.utc) >= _freetheai_skip_until:
        _freetheai_skip_until = None
        return True
    return False


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


def llm_call(
    messages: list[dict],
    *,
    groq_model: str = "meta-llama/llama-4-scout-17b-16e-instruct",
    max_tokens: int = 2000,
    temperature: float = 0.3,
    response_format: dict | None = None,
    timeout: int = 45,
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

    # ── FreeTheAI ──────────────────────────────────────────────────────────────
    if freetheai_key and _freetheai_available():
        for model in [FREETHEAI_PRIMARY, FREETHEAI_SECONDARY]:
            try:
                r = httpx.post(
                    FREETHEAI_URL,
                    headers={
                        "Authorization": f"Bearer {freetheai_key}",
                        "Content-Type": "application/json",
                    },
                    json={**payload_base, "model": model},
                    timeout=timeout,
                )

                # Checkin Discord manquant → désactive jusqu'au prochain minuit UTC
                if r.status_code == 403 or _is_checkin_error(r):
                    _freetheai_skip_until = _next_utc_midnight()
                    _notify_telegram("⚠️ *FreeTheAI* — Check-in Discord manquant\nFais `/checkin` sur leur serveur pour débloquer la clé.\nGroq utilisé en fallback jusqu'à minuit UTC.")
                    break  # → Groq directement

                if r.status_code == 200:
                    return r.json()

                # 429 / 502 / 503 → essai sur le secondary, puis Groq
                continue

            except (httpx.TimeoutException, httpx.ConnectError, httpx.ReadError):
                continue

    # ── Groq (fallback) ────────────────────────────────────────────────────────
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
            return r.json()
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
) -> str:
    """Shortcut → retourne directement le texte du message."""
    resp = llm_call(
        messages,
        groq_model=groq_model,
        max_tokens=max_tokens,
        temperature=temperature,
        timeout=timeout,
    )
    return resp["choices"][0]["message"]["content"].strip()


def llm_json(
    messages: list[dict],
    *,
    groq_model: str = "meta-llama/llama-4-scout-17b-16e-instruct",
    max_tokens: int = 2000,
    temperature: float = 0.3,
    timeout: int = 45,
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
