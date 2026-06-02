"""
Translation service: EN → FR
Waterfall: Azure → Yandex (TODO) → FreeTheAI gpt-5.4-mini → Hapuppy gpt-4.1-mini → Groq llama-3.1-8b-instant

- Partage les circuit breakers de llm_service (même état FreeTheAI + Hapuppy)
- Trail + notif TG du même style que la cascade LLM
- Si tout échoue → exception, l'appelant conserve l'EN en DB et retry plus tard
"""
import os
import logging
import time
import httpx
import services.llm_service as _lsvc
from services.llm_service import (
    _freetheai_available, _next_utc_midnight, _notify_telegram, _log_usage,
    FREETHEAI_URL, FREETHEAI_SECONDARY,
    HAPUPPY_URL, HAPUPPY_MODEL, HAPUPPY_QUOTA_FREEZE_SECONDS,
    GROQ_URL,
    _notif_sent_at, NOTIF_THROTTLE,
)

logger = logging.getLogger(__name__)

DUMB_BATCH_SIZE = 25  # articles par appel Azure batch (max 100 supporté)

TRANSLATE_PROMPT = """Translate the following financial text from English to French.
Rules:
- Preserve all ticker symbols (AAPL, AMZN...), percentages, and numbers exactly as-is
- Preserve financial terms accurately (earnings → bénéfices, revenue → chiffre d'affaires, etc.)
- Return ONLY the translated text, no explanation, no quotes

Text:
{text}"""


def _send_translate_notif(reason: str, trail: list[str]) -> None:
    """Notif TG throttlée 30min — même style que cascade LLM."""
    now = time.monotonic()
    key = f"translate_{reason}"
    if now - _notif_sent_at.get(key, 0) < NOTIF_THROTTLE:
        return
    _notif_sent_at[key] = now
    headers = {
        "azure_down":      "🟠 *Azure Translator — down*",
        "freetheai_down":  f"🟠 *FreeTheAI `{FREETHEAI_SECONDARY}` — down (traduction)*",
        "hapuppy_down":    f"🟠 *Hapuppy `{HAPUPPY_MODEL}` — down (traduction)*",
        "all_down":        "🔴 *Traduction — tous les providers en échec*\nEN conservé en DB, retry au prochain batch.",
    }
    header = headers.get(reason, f"🟠 *Traduction — {reason}*")
    _notify_telegram(f"{header}\n\n" + "\n".join(trail) + "\n📍 _translation\\_service_")


# ─── Public API ───────────────────────────────────────────────────────────────

def translate_to_fr(text: str) -> str:
    """Traduit un texte EN → FR via le waterfall complet. Lève une exception si tout échoue."""
    if not text or not text.strip():
        return text

    trail: list[str] = []

    # 1. Azure Translator
    azure_key = os.environ.get("AZURE_TRANSLATOR_KEY", "")
    azure_region = os.environ.get("AZURE_TRANSLATOR_REGION", "")
    if azure_key and azure_region:
        try:
            result = _azure_translate_batch([text], azure_key, azure_region)[0]
            return result
        except Exception as e:
            logger.warning(f"Azure Translator failed: {e}")
            trail.append(f"Azure Translator : ✗ {e}")
            _send_translate_notif("azure_down", trail)
    else:
        trail.append("Azure Translator : ⏭ skipped (clé manquante)")

    # 2. Yandex — TODO (YANDEX_API_KEY_ID + YANDEX_SECRET_KEY configurés, pas encore intégré)
    trail.append("Yandex : ⏭ not implemented")

    # 3. FreeTheAI gpt-5.4-mini — partage le circuit breaker de llm_service
    freetheai_key = os.environ.get("FREETHEAI_API_KEY", "")
    if freetheai_key and _freetheai_available():
        _round_failed = False
        try:
            _lsvc._freetheai_last_attempt = time.monotonic()
            r = httpx.post(
                FREETHEAI_URL,
                headers={"Authorization": f"Bearer {freetheai_key}", "Content-Type": "application/json"},
                json={
                    "model": FREETHEAI_SECONDARY,
                    "messages": [{"role": "user", "content": TRANSLATE_PROMPT.format(text=text)}],
                    "max_tokens": 1000,
                    "temperature": 0.1,
                },
                timeout=35,
            )
            if r.status_code == 403 or _is_checkin_error(r):
                _lsvc._freetheai_skip_until = _next_utc_midnight()
                trail.append(f"FreeTheAI `{FREETHEAI_SECONDARY}` : ✗ checkin manquant")
                _notify_telegram("⚠️ *FreeTheAI* — Check-in manquant (traduction)\nFais `/checkin` sur leur serveur.")
            elif _is_daily_limit_error(r):
                _lsvc._freetheai_skip_until = _next_utc_midnight()
                trail.append(f"FreeTheAI `{FREETHEAI_SECONDARY}` : ✗ quota journalier épuisé")
            elif r.status_code == 429:
                trail.append(f"FreeTheAI `{FREETHEAI_SECONDARY}` : ✗ rate limit (429)")
            elif r.status_code == 200:
                content = r.json()["choices"][0]["message"]["content"].strip()
                if content:
                    _lsvc._freetheai_fail_count = 0
                    u = r.json().get("usage", {})
                    _log_usage(FREETHEAI_SECONDARY, u.get("prompt_tokens", 0), u.get("completion_tokens", 0))
                    return content
            else:
                trail.append(f"FreeTheAI `{FREETHEAI_SECONDARY}` : ✗ {r.status_code}")
                _round_failed = True
        except (httpx.TimeoutException, httpx.ConnectError, httpx.ReadError):
            trail.append(f"FreeTheAI `{FREETHEAI_SECONDARY}` : ✗ timeout/connexion")
            _round_failed = True
        except Exception as e:
            trail.append(f"FreeTheAI `{FREETHEAI_SECONDARY}` : ✗ {e}")
            _round_failed = True

        if _round_failed:
            _lsvc._freetheai_fail_count += 1
            if _lsvc._freetheai_fail_count >= _lsvc.FREETHEAI_FAIL_THRESHOLD:
                _lsvc._freetheai_fail_freeze_until = time.monotonic() + _lsvc.FREETHEAI_FAIL_FREEZE_SECONDS
                _lsvc._freetheai_fail_count = 0
            _send_translate_notif("freetheai_down", trail)
    else:
        trail.append(f"FreeTheAI `{FREETHEAI_SECONDARY}` : ⏭ skipped (checkin/quota/RPM/circuit breaker)")

    # 4. Hapuppy gpt-4.1-mini — partage le quota skip de llm_service
    hapuppy_key = os.environ.get("HAPUPPY_API_KEY", "")
    if hapuppy_key:
        now_mono = time.monotonic()
        if _lsvc._hapuppy_quota_skip_until is not None:
            if now_mono < _lsvc._hapuppy_quota_skip_until:
                trail.append(f"Hapuppy `{HAPUPPY_MODEL}` : ⏭ frozen (quota épuisé)")
                hapuppy_key = ""
            else:
                _lsvc._hapuppy_quota_skip_until = None

    if hapuppy_key:
        try:
            _lsvc._hapuppy_last_attempt = time.monotonic()
            r = httpx.post(
                HAPUPPY_URL,
                headers={"Authorization": f"Bearer {hapuppy_key}", "Content-Type": "application/json"},
                json={
                    "model": HAPUPPY_MODEL,
                    "messages": [{"role": "user", "content": TRANSLATE_PROMPT.format(text=text)}],
                    "max_tokens": 1000,
                    "temperature": 0.1,
                },
                timeout=30,
            )
            if r.status_code == 200:
                content = r.json()["choices"][0]["message"]["content"].strip()
                if content:
                    u = r.json().get("usage", {})
                    _log_usage(HAPUPPY_MODEL, u.get("prompt_tokens", 0), u.get("completion_tokens", 0))
                    trail.append(f"Hapuppy `{HAPUPPY_MODEL}` : ✓")
                    return content
            elif r.status_code == 429:
                _lsvc._hapuppy_quota_skip_until = time.monotonic() + HAPUPPY_QUOTA_FREEZE_SECONDS
                trail.append(f"Hapuppy `{HAPUPPY_MODEL}` : ✗ 429 quota épuisé")
                _send_translate_notif("hapuppy_down", trail)
            else:
                trail.append(f"Hapuppy `{HAPUPPY_MODEL}` : ✗ {r.status_code}")
                _send_translate_notif("hapuppy_down", trail)
        except Exception as e:
            trail.append(f"Hapuppy `{HAPUPPY_MODEL}` : ✗ {e}")
            _send_translate_notif("hapuppy_down", trail)
    else:
        trail.append(f"Hapuppy `{HAPUPPY_MODEL}` : ⏭ skipped (clé manquante)")

    # 5. Groq llama-3.1-8b-instant
    groq_key = os.environ.get("GROQ_API_KEY", "")
    if groq_key:
        try:
            r = httpx.post(
                GROQ_URL,
                headers={"Authorization": f"Bearer {groq_key}"},
                json={
                    "model": "llama-3.1-8b-instant",
                    "messages": [{"role": "user", "content": TRANSLATE_PROMPT.format(text=text)}],
                    "max_tokens": 1000,
                    "temperature": 0.1,
                },
                timeout=30,
            )
            r.raise_for_status()
            content = r.json()["choices"][0]["message"]["content"].strip()
            u = r.json().get("usage", {})
            _log_usage("llama-3.1-8b-instant", u.get("prompt_tokens", 0), u.get("completion_tokens", 0))
            trail.append("Groq `llama-3.1-8b-instant` : ✓")
            _send_translate_notif("groq_used", trail)
            return content
        except Exception as e:
            trail.append(f"Groq `llama-3.1-8b-instant` : ✗ {e}")

    _send_translate_notif("all_down", trail)
    raise RuntimeError(f"Translation failed — tous les providers sont down\n" + "\n".join(trail))


def translate_batch_to_fr(texts: list[str]) -> list[str]:
    """
    Traduit une liste de textes EN→FR via Azure batch natif.
    Fallback : appels individuels via translate_to_fr (waterfall complet).
    """
    if not texts:
        return texts

    azure_key = os.environ.get("AZURE_TRANSLATOR_KEY", "")
    azure_region = os.environ.get("AZURE_TRANSLATOR_REGION", "")
    if azure_key and azure_region:
        try:
            return _azure_translate_batch(texts, azure_key, azure_region)
        except Exception as e:
            logger.warning(f"Azure batch failed: {e} — fallback appels individuels")

    return [translate_to_fr(t) for t in texts]


# ─── Providers ────────────────────────────────────────────────────────────────

def _azure_translate_batch(texts: list[str], api_key: str, region: str) -> list[str]:
    r = httpx.post(
        "https://api.cognitive.microsofttranslator.com/translate",
        headers={
            "Ocp-Apim-Subscription-Key": api_key,
            "Ocp-Apim-Subscription-Region": region,
            "Content-Type": "application/json",
        },
        params={"api-version": "3.0", "from": "en", "to": "fr"},
        json=[{"Text": t} for t in texts],
        timeout=30,
    )
    r.raise_for_status()
    return [item["translations"][0]["text"] for item in r.json()]


def _is_checkin_error(r: httpx.Response) -> bool:
    try:
        err = r.json().get("error", {})
        return isinstance(err, dict) and err.get("type") == "daily_checkin_required"
    except Exception:
        return False


def _is_daily_limit_error(r: httpx.Response) -> bool:
    try:
        body = r.text
        return "daily successful request limit exceeded" in body.lower()
    except Exception:
        return False
