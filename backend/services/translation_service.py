"""
Translation service: EN → FR
Cascade: FreeTheAI → DeepL → Groq

- FreeTheAI: primary (free, ~16s). Si 403 checkin manquant → skip immédiat vers DeepL
- DeepL: secondary (fast, ~200ms, ~$7/mois paid tier)
- Groq: fallback final (free, toujours dispo)
"""
import os
import logging
import time
import httpx
from services.llm_service import _freetheai_available, _freetheai_skip_until, _next_utc_midnight, _notify_telegram, FREETHEAI_URL, FREETHEAI_PRIMARY

logger = logging.getLogger(__name__)

_last_freetheai_call_at: float | None = None

TRANSLATE_PROMPT = """Translate the following financial analysis text from English to French.
Rules:
- Preserve all ticker symbols (AAPL, AMZN...), percentages, and numbers exactly as-is
- Preserve financial terms accurately (earnings → bénéfices, revenue → chiffre d'affaires, etc.)
- Return ONLY the translated text, no explanation, no quotes

Text:
{text}"""


def translate_to_fr(text: str) -> str:
    if not text or not text.strip():
        return text

    # Respecter le RPM FreeTheAI en mode batch (1 concurrent, ~16s/call)
    batch_delay = float(os.environ.get("FREETHEAI_BATCH_DELAY", "0"))
    if batch_delay > 0:
        time.sleep(batch_delay)

    # Délai partagé inter-appels FreeTheAI pour éviter les bursts
    min_interval = float(os.environ.get("FREETHEAI_MIN_INTERVAL", "0"))
    global _last_freetheai_call_at
    now = time.monotonic()
    if min_interval > 0 and _last_freetheai_call_at is not None:
        elapsed = now - _last_freetheai_call_at
        if elapsed < min_interval:
            time.sleep(min_interval - elapsed)

    # 1. FreeTheAI — primary (skip si 403 checkin)
    freetheai_key = os.environ.get("FREETHEAI_API_KEY", "")
    if freetheai_key and _freetheai_available():
        try:
            r = httpx.post(
                FREETHEAI_URL,
                headers={"Authorization": f"Bearer {freetheai_key}", "Content-Type": "application/json"},
                json={
                    "model": FREETHEAI_PRIMARY,
                    "messages": [{"role": "user", "content": TRANSLATE_PROMPT.format(text=text)}],
                    "max_tokens": 800,
                    "temperature": 0.1,
                },
                timeout=35,
            )
            if r.status_code == 403 or _is_checkin_error(r):
                import services.llm_service as _lsvc
                _lsvc._freetheai_skip_until = _next_utc_midnight()
                _notify_telegram("⚠️ *FreeTheAI* — Check-in Discord manquant\nFais `/checkin` sur leur serveur pour débloquer.\nDeepL/Groq utilisés pour la traduction.")
                logger.warning("FreeTheAI 403 — skip vers DeepL pour traductions")
            elif r.status_code == 200:
                content = r.json()["choices"][0]["message"]["content"].strip()
                if content:
                    return content
        except Exception as e:
            logger.warning(f"FreeTheAI translation failed: {e}")

    # 2. DeepL — secondary (si clé disponible)
    deepl_key = os.environ.get("DEEPL_API_KEY", "")
    if deepl_key:
        try:
            return _deepl(text, deepl_key)
        except Exception as e:
            logger.warning(f"DeepL failed: {e}")

    # 3. Groq — fallback final (désactivé en BATCH_MODE pour préserver le quota site)
    if os.environ.get("BATCH_MODE") == "1":
        raise RuntimeError("Translation unavailable (FreeTheAI/DeepL down, Groq disabled for batch)")
    return _groq_translate(text)


def _is_checkin_error(r: httpx.Response) -> bool:
    try:
        err = r.json().get("error", {})
        return isinstance(err, dict) and err.get("type") == "daily_checkin_required"
    except Exception:
        return False


def _deepl(text: str, api_key: str) -> str:
    base = "api-free.deepl.com" if api_key.endswith(":fx") else "api.deepl.com"
    r = httpx.post(
        f"https://{base}/v2/translate",
        headers={"Authorization": f"DeepL-Auth-Key {api_key}"},
        json={"text": [text], "target_lang": "FR", "source_lang": "EN"},
        timeout=15,
    )
    r.raise_for_status()
    return r.json()["translations"][0]["text"]


def _groq_translate(text: str) -> str:
    r = httpx.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {os.environ['GROQ_API_KEY']}"},
        json={
            "model": "llama-3.1-8b-instant",
            "messages": [{"role": "user", "content": TRANSLATE_PROMPT.format(text=text)}],
            "max_tokens": 800,
            "temperature": 0.1,
        },
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"].strip()
