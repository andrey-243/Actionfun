"""
Cascade LLM : FreeTheAI (primary → secondary) → ZenithAI → Hapuppy → Groq

Logique :
  1. bbl/gpt-4.1 (FreeTheAI primary)
  2. bbl/gpt-5.4-mini (FreeTheAI secondary) — si 502/503/timeout sur #1
     → circuit breaker : 3 rounds échoués → freeze 30min → probe auto
  3. ZenithAI gpt-5.5 — freeze 1min après 3 erreurs, flag session sur 401
  4. Hapuppy gpt-4.1-mini — freeze 1min sur quota 403/429
  5. Groq — fallback final
"""

import httpx
import time
import os
import json
import random
import threading
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path

_USAGE_LOG = Path.home() / "llm-stack" / "token_usage.jsonl"
_FAILED_LOG = Path.home() / "llm-stack" / "logs" / "llm_cascade_failures.jsonl"


def _log_cascade_failure(reason: str, caller: str, item_id: str) -> None:
    """Persiste les échecs complets de cascade pour retry au prochain batch."""
    try:
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "reason": reason,
            "caller": caller or "llm_service",
            "item_id": item_id,
        }
        with _FAILED_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _log_usage(model: str, tokens_in: int, tokens_out: int, provider: str = "", vendor: str = "") -> None:
    if not _USAGE_LOG.parent.exists():
        return
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "model": model,
        "tokens_in": tokens_in,
        "tokens_in_cached": 0,
        "tokens_in_uncached": tokens_in,
        "tokens_out": tokens_out,
        "total_tokens": tokens_in + tokens_out,
        "cost": 0.0,
    }
    if provider:
        entry["provider"] = provider
    if vendor:
        entry["vendor"] = vendor
    try:
        with _USAGE_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _notify_telegram(message: str) -> None:
    import re
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        return
    html = re.sub(r'\*([^*]+)\*', r'<b>\1</b>', message)
    html = re.sub(r'`([^`]+)`', r'<code>\1</code>', html)
    html = re.sub(r'(?<!\w)_([^_\n]+)_(?!\w)', r'<i>\1</i>', html)
    try:
        httpx.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": html, "parse_mode": "HTML"},
            timeout=5,
        )
    except Exception:
        pass

FREETHEAI_URL = "https://api.freetheai.xyz/v1/chat/completions"
OPENAI_URL = "https://api.zenllm.org/v1/chat/completions"
HAPUPPY_URL = "https://beta.hapuppy.com/v1/chat/completions"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
GEMINI_MODEL = "gemini-3.1-flash-lite"

FREETHEAI_PRIMARY = "bbl/gpt-4.1"
FREETHEAI_SECONDARY = "bbl/gpt-5.4-mini"
OPENAI_MODEL = "openai/gpt-5.5"
HAPUPPY_MODELS = ["gpt-4.1-mini"]

# Désactive FreeTheAI jusqu'au prochain minuit UTC quand checkin/quota daily
_freetheai_skip_until: datetime | None = None

# Circuit breaker FreeTheAI 502/503/timeout — par round (pas par tentative modèle)
_freetheai_fail_count: int = 0
_freetheai_fail_freeze_until: float | None = None  # monotonic timestamp
FREETHEAI_FAIL_THRESHOLD = 3
FREETHEAI_FAIL_FREEZE_SECONDS = 1800  # 30 min

# Freeze 3h après 3 × 429 consécutifs (RPM saturé)
_freetheai_429_count: int = 0
FREETHEAI_429_THRESHOLD = 3
FREETHEAI_429_FREEZE_SECONDS = int(os.environ.get("FREETHEAI_429_FREEZE_SECONDS", str(3 * 3600)))
_freetheai_429_freeze_until: float | None = None

# 2 concurrents FreeTheAI — non-bloquant → ~7 RPM via latence naturelle (~17s/call)
# MIN_INTERVAL supprimé : semaphore + compteur 429 suffisent
FREETHEAI_CONCURRENCY = int(os.environ.get("FREETHEAI_CONCURRENCY", "1"))
_freetheai_semaphore = threading.Semaphore(FREETHEAI_CONCURRENCY)

# ZenithAI — 2 clés en cascade (key1 = OPENAI_API_KEY, key2 = ZENITH_API_KEY2)
ZENITH_KEYS_ENV = ["OPENAI_API_KEY", "ZENITH_API_KEY2"]
ZENITH_CONCURRENCY = int(os.environ.get("ZENITH_CONCURRENCY", "1"))
_zenith_semaphore = threading.Semaphore(ZENITH_CONCURRENCY)
ZENITH_ERROR_THRESHOLD = 3
ZENITH_FREEZE_SECONDS = 60
_zenith_ratelimit_until: list[float] = [0.0, 0.0]
_zenith_consecutive_errors: list[int] = [0, 0]
_zenith_freeze_until: list[float | None] = [None, None]
_zenith_bad_key: list[bool] = [False, False]

# Hapuppy — concurrence limitée (semaphore partagé entre threads)
HAPUPPY_CONCURRENCY = int(os.environ.get("HAPUPPY_CONCURRENCY", "15"))
_hapuppy_semaphore = threading.Semaphore(HAPUPPY_CONCURRENCY)

# Hapuppy — freeze quota (1min) vs rate-limit (backoff global partagé entre threads)
HAPUPPY_QUOTA_FREEZE_SECONDS = int(os.environ.get("HAPUPPY_QUOTA_FREEZE_SECONDS", "0"))
HAPUPPY_RATELIMIT_BACKOFF_SECONDS = int(os.environ.get("HAPUPPY_RATELIMIT_BACKOFF_SECONDS", "12"))
_hapuppy_quota_skip_until: float | None = None  # monotonic timestamp
_hapuppy_ratelimit_until: float = 0.0            # backoff global partagé entre threads
_hapuppy_last_attempt: float = 0.0
HAPUPPY_MIN_INTERVAL = float(os.environ.get("HAPUPPY_MIN_INTERVAL", "0.0"))  # pas de throttle par défaut

_HAPUPPY_QUOTA_KEYWORDS = ("quota", "credit", "budget", "exhausted", "daily limit")

# Throttle notifs Groq/Hapuppy — 1 ping par raison toutes les 30 min max
_notif_sent_at: dict[str, float] = {}
NOTIF_THROTTLE = 1800.0  # 30 min

_groq_last_attempt: float = 0.0
GROQ_MIN_INTERVAL = float(os.environ.get("GROQ_MIN_INTERVAL", "0"))
_groq_consecutive_429: int = 0
_groq_exhausted: bool = False  # True quand 3 x 429 consécutifs → quota daily épuisé
_groq_bad_key: bool = False    # 403 → clé invalide/expirée, skip pour toute la session
_last_llm_source: str = "unknown"  # mis à jour par llm_call() après chaque succès

_gemini_bad_key: bool = False
_gemini_daily_exhausted: bool = False
_gemini_ratelimit_until: float = 0.0


class GroqExhaustedError(RuntimeError):
    """Levée quand Groq encaisse 3 x 429 consécutifs — quota daily vraisemblablement épuisé."""


class FreeTheAIExhaustedError(RuntimeError):
    """Levée en cascade='monthly_scrap' quand FreeTheAI atteint sa limite daily/checkin."""


_freetheai_exhausted: bool = False  # True dès daily/checkin limit hit — session-wide


_CASCADE_HEADERS = {
    "checkin":              "🔴 *FreeTheAI — checkin Discord manquant*\nFais `/checkin` pour débloquer. Reset à minuit UTC.",
    "daily_limit":          "🔴 *FreeTheAI — quota journalier épuisé*\nReset automatique à minuit UTC.",
    "rpm_429":              "🟡 *FreeTheAI — rate limit (429 RPM)*",
    "502_timeout":          "🟠 *FreeTheAI — injoignable (502/timeout)*",
    "freetheai_down_frozen": f"🔴 *FreeTheAI — circuit breaker* ({FREETHEAI_FAIL_THRESHOLD} rounds 502/timeout)\nSkip FreeTheAI 30min. Probe auto après.",
    "freetheai_429_frozen":  f"🔴 *FreeTheAI — rate limit* ({FREETHEAI_429_THRESHOLD}× 429 RPM)\nFreeze {FREETHEAI_429_FREEZE_SECONDS//3600}h.",
    "groq_exhausted":       "🔴 *Groq — quota daily épuisé*\nBatch arrêté proprement. Reset à minuit UTC.",
    "openai_ratelimit":     f"🟡 *ZenithAI `{OPENAI_MODEL}` — rate limit (429)*",
    "openai_down":          f"🟠 *ZenithAI `{OPENAI_MODEL}` — down (× {ZENITH_ERROR_THRESHOLD})*\nFreeze 1min.",
    "hapuppy_down":         "🟠 *Hapuppy — down (502/timeout)*\nVérifier les crédits Hapuppy.",
    "hapuppy_quota":        "🔴 *Hapuppy — crédits épuisés (429/403)*\nRecharger les crédits. Freeze 1min.",
    "both_quota":           f"🔴 *FreeTheAI + Hapuppy hors quota*\nGroq seul actif. Vérifier les crédits Hapuppy + reset FreeTheAI à minuit UTC.",
    "all_failed":           "🔴 *Tous les LLM en échec*",
}


def _send_cascade_notif(reason: str, trail: list[str], caller: str = "", header_override: str = "", item_id: str = "") -> None:
    """Notif unique avec le trail complet. Throttle 30min par raison."""
    now = time.monotonic()
    key = f"cascade_{reason}"
    if now - _notif_sent_at.get(key, 0) < NOTIF_THROTTLE:
        return
    _notif_sent_at[key] = now
    header = header_override or _CASCADE_HEADERS.get(reason, f"🟠 *Cascade LLM — {reason}*")
    source = caller if caller else "llm_service"
    if reason in ("all_failed", "groq_exhausted"):
        _log_cascade_failure(reason, source, item_id)
    _notify_telegram(f"{header}\n\n" + "\n".join(trail) + f"\n📍 <i>{source}</i>")


def _freetheai_available() -> bool:
    global _freetheai_skip_until, _freetheai_fail_freeze_until, _freetheai_fail_count
    global _freetheai_429_count, _freetheai_429_freeze_until
    now_mono = time.monotonic()
    # Checkin / quota daily → minuit UTC
    if _freetheai_skip_until is not None:
        if datetime.now(timezone.utc) >= _freetheai_skip_until:
            _freetheai_skip_until = None
        else:
            return False
    # Circuit breaker 502/503/timeout → 30min
    if _freetheai_fail_freeze_until is not None:
        if now_mono < _freetheai_fail_freeze_until:
            return False
        _freetheai_fail_freeze_until = None
        _freetheai_fail_count = 0
    # 429 × 3 → freeze 3h
    if _freetheai_429_freeze_until is not None:
        if now_mono < _freetheai_429_freeze_until:
            return False
        _freetheai_429_freeze_until = None
        _freetheai_429_count = 0
    # Semaphore non-bloquant : max FREETHEAI_CONCURRENCY=2 calls simultanés
    if not _freetheai_semaphore.acquire(blocking=False):
        return False
    return True


def _seconds_until_hapuppy_reset() -> float:
    """Secondes jusqu'au prochain reset Hapuppy : 18h00 heure de Paris."""
    paris = ZoneInfo("Europe/Paris")
    now = datetime.now(paris)
    target = now.replace(hour=18, minute=0, second=0, microsecond=0)
    if now >= target:
        target += timedelta(days=1)
    return (target - now).total_seconds()


def _next_utc_midnight() -> datetime:
    from datetime import timedelta
    now = datetime.now(timezone.utc)
    return (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)


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


def _try_gemini(payload_base: dict, timeout: int, trail: list[str]) -> dict | None:
    """Tente Gemini natif. Retourne dict OpenAI-compatible ou None."""
    global _gemini_bad_key, _gemini_daily_exhausted, _gemini_ratelimit_until, _last_llm_source
    gemini_key = os.environ.get("GEMINI_API_KEY", "")
    if not gemini_key or _gemini_bad_key or _gemini_daily_exhausted:
        if gemini_key and _gemini_bad_key:
            trail.append("Gemini : ⏭ skipped (clé invalide)")
        elif gemini_key and _gemini_daily_exhausted:
            trail.append("Gemini : ⏭ skipped (quota daily épuisé)")
        return None

    wait = _gemini_ratelimit_until - time.monotonic()
    if wait > 0:
        time.sleep(wait)

    # Convertir messages OpenAI → format Gemini
    contents = []
    system_instruction = None
    for msg in payload_base.get("messages", []):
        role, text = msg["role"], msg["content"]
        if role == "system":
            system_instruction = {"parts": [{"text": text}]}
        elif role == "user":
            contents.append({"role": "user", "parts": [{"text": text}]})
        elif role == "assistant":
            contents.append({"role": "model", "parts": [{"text": text}]})

    gen_config: dict = {
        "maxOutputTokens": payload_base.get("max_tokens", 2000),
        "temperature": payload_base.get("temperature", 0.3),
    }
    if (payload_base.get("response_format") or {}).get("type") == "json_object":
        gen_config["responseMimeType"] = "application/json"

    body: dict = {"contents": contents, "generationConfig": gen_config}
    if system_instruction:
        body["system_instruction"] = system_instruction

    url = GEMINI_URL.format(model=GEMINI_MODEL)
    try:
        r = httpx.post(url, params={"key": gemini_key}, json=body, timeout=timeout)
        if r.status_code == 200:
            data = r.json()
            text = data["candidates"][0]["content"]["parts"][0]["text"]
            usage = data.get("usageMetadata", {})
            _log_usage(GEMINI_MODEL, usage.get("promptTokenCount", 0), usage.get("candidatesTokenCount", 0), provider="google", vendor="gemini")
            _last_llm_source = "gemini"
            trail.append(f"Gemini `{GEMINI_MODEL}` : ✓")
            return {
                "choices": [{"message": {"content": text, "role": "assistant"}}],
                "_llm_source": "gemini",
                "usage": {"prompt_tokens": usage.get("promptTokenCount", 0), "completion_tokens": usage.get("candidatesTokenCount", 0)},
            }
        if r.status_code == 429:
            err = str(r.json()).lower()
            if any(k in err for k in ("daily", "per_day", "per_day_free_tier")):
                _gemini_daily_exhausted = True
                trail.append("Gemini : ✗ 429 quota daily épuisé")
            else:
                _gemini_ratelimit_until = time.monotonic() + 90
                trail.append("Gemini : ✗ 429 rate-limit (backoff 90s)")
            return None
        if r.status_code in (400, 403):
            _gemini_bad_key = True
            trail.append(f"Gemini : ✗ {r.status_code} clé invalide")
            return None
        trail.append(f"Gemini : ✗ {r.status_code}")
        return None
    except (httpx.TimeoutException, httpx.ConnectError, httpx.ReadError):
        trail.append("Gemini : ✗ timeout/connexion")
        return None


def llm_call(
    messages: list[dict],
    *,
    groq_model: str = "meta-llama/llama-4-scout-17b-16e-instruct",
    max_tokens: int = 2000,
    temperature: float = 0.3,
    response_format: dict | None = None,
    timeout: int = 45,
    allow_groq: bool = True,
    caller: str = "",
    item_id: str = "",
    cascade: str = "default",
) -> dict:
    """
    Appel LLM avec cascade selon le mode.

    Prod  : ow24h → Gemini → Hapuppy | dumb → Gemini → Hapuppy → Groq
            bub/weekly → Hapuppy → Gemini | ow7j → Hapuppy
            monthly → FreeTheAI → Hapuppy | yearly → FreeTheAI
    Scrap : weekly → Gemini → Hapuppy | dumb → Groq → Hapuppy | monthly → FreeTheAI

    Retourne le dict OpenAI complet (choices[0].message.content accessible).
    """
    global _freetheai_skip_until, _freetheai_exhausted
    # _zenith_* sont des listes mutées en place, pas besoin de global

    # ── monthly_scrap : exit rapide si déjà exhausted ─────────────────────────
    if cascade == "monthly_scrap" and _freetheai_exhausted:
        raise FreeTheAIExhaustedError("FreeTheAI exhausted (flag session actif)")

    freetheai_key = os.environ.get("FREETHEAI_API_KEY", "")
    payload_base = {
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    if response_format:
        payload_base["response_format"] = response_format

    groq_reason = "502_timeout"
    trail: list[str] = []  # trail complet de la cascade pour la notif TG
    global _last_llm_source
    _freetheai_tried = False  # True ssi FreeTheAI tenté ce call (pas pre-frozen)
    _hapuppy_quota_notif_sent = False  # True ssi hapuppy_quota envoyé dans ce call → supprime both_quota et hapuppy_down

    # ── FreeTheAI ──────────────────────────────────────────────────────────────
    # _freetheai_available() acquiert _freetheai_semaphore(1) si dispo — relâché en fin de bloc
    if cascade in ("monthly_prod", "yearly_prod", "monthly_scrap") and freetheai_key and _freetheai_available():
        _freetheai_tried = True
        global _freetheai_fail_count, _freetheai_fail_freeze_until
        global _freetheai_429_count, _freetheai_429_freeze_until
        _round_failed = False
        try:
            for model in [FREETHEAI_PRIMARY, FREETHEAI_SECONDARY]:
                try:
                    r = httpx.post(
                        FREETHEAI_URL,
                        headers={"Authorization": f"Bearer {freetheai_key}", "Content-Type": "application/json"},
                        json={**payload_base, "model": model},
                        timeout=timeout,
                    )

                    if r.status_code == 403 or _is_checkin_error(r):
                        _freetheai_skip_until = _next_utc_midnight()
                        _freetheai_exhausted = True
                        groq_reason = "checkin"
                        trail.append(f"FreeTheAI `{model}` : ✗ checkin manquant")
                        break

                    if _is_daily_limit_error(r):
                        _freetheai_skip_until = _next_utc_midnight()
                        _freetheai_exhausted = True
                        groq_reason = "daily_limit"
                        trail.append(f"FreeTheAI `{model}` : ✗ quota journalier épuisé")
                        break

                    if r.status_code == 200:
                        _freetheai_fail_count = 0
                        _freetheai_429_count = 0
                        result = r.json()
                        result["_llm_source"] = "freetheai"
                        _last_llm_source = "freetheai"
                        u = result.get("usage", {})
                        _log_usage(model, u.get("prompt_tokens", 0), u.get("completion_tokens", 0))
                        return result

                    if r.status_code == 429:
                        _freetheai_429_count += 1
                        groq_reason = "rpm_429"
                        trail.append(f"FreeTheAI `{model}` : ✗ 429 ({_freetheai_429_count}/{FREETHEAI_429_THRESHOLD})")
                        if _freetheai_429_count >= FREETHEAI_429_THRESHOLD:
                            _freetheai_429_freeze_until = time.monotonic() + FREETHEAI_429_FREEZE_SECONDS
                            _freetheai_429_count = 0
                            trail.append(f"FreeTheAI : freeze {FREETHEAI_429_FREEZE_SECONDS//3600}h (429 × {FREETHEAI_429_THRESHOLD})")
                            _send_cascade_notif("freetheai_429_frozen", trail, caller)
                        break

                    # 502 / 503 → essai sur le secondary puis marque round échoué
                    trail.append(f"FreeTheAI `{model}` : ✗ {r.status_code}")
                    _round_failed = True
                    continue

                except (httpx.TimeoutException, httpx.ConnectError, httpx.ReadError):
                    trail.append(f"FreeTheAI `{model}` : ✗ timeout/connexion")
                    _round_failed = True
                    continue
        finally:
            _freetheai_semaphore.release()

        # Circuit breaker : 3 rounds échoués → freeze 30min
        if _round_failed:
            _freetheai_fail_count += 1
            if _freetheai_fail_count >= FREETHEAI_FAIL_THRESHOLD:
                _freetheai_fail_freeze_until = time.monotonic() + FREETHEAI_FAIL_FREEZE_SECONDS
                _freetheai_fail_count = 0
                _send_cascade_notif("freetheai_down_frozen", trail, caller)
    elif cascade in ("monthly_prod", "yearly_prod", "monthly_scrap"):
        trail.append("FreeTheAI : ⏭ skipped (checkin/quota/RPM/circuit breaker)")

    # monthly_scrap : ne jamais fallback vers d'autres providers
    if cascade == "monthly_scrap":
        if _freetheai_exhausted:
            raise FreeTheAIExhaustedError("FreeTheAI exhausted (daily/checkin limit hit)")
        raise RuntimeError(f"FreeTheAI unavailable ({groq_reason}) — monthly_scrap mode")

    # ── ZenithAI key1 → key2 (waterfall, 3 retries chacune) ──────────────────────
    for zen_idx in range(len(ZENITH_KEYS_ENV) if os.environ.get("ZENITH_ENABLED", "1") != "0" and cascade == "default" else 0):
        zen_key = os.environ.get(ZENITH_KEYS_ENV[zen_idx], "")
        if not zen_key or _zenith_bad_key[zen_idx]:
            if zen_key:
                trail.append(f"ZenithAI key{zen_idx + 1} : ⏭ skipped (401)")
            continue
        now_mono = time.monotonic()
        if _zenith_freeze_until[zen_idx] is not None:
            if now_mono < _zenith_freeze_until[zen_idx]:
                trail.append(f"ZenithAI key{zen_idx + 1} : ⏭ frozen")
                continue
            else:
                _zenith_freeze_until[zen_idx] = None
                _zenith_consecutive_errors[zen_idx] = 0

        for attempt in range(3):
            try:
                with _zenith_semaphore:
                    r = httpx.post(
                        OPENAI_URL,
                        headers={"Authorization": f"Bearer {zen_key}", "Content-Type": "application/json"},
                        json={**payload_base, "model": OPENAI_MODEL, "temperature": 1},
                        timeout=timeout,
                    )
                if r.status_code == 200:
                    result = r.json()
                    result["_llm_source"] = f"zenith_key{zen_idx + 1}"
                    _last_llm_source = f"zenith_key{zen_idx + 1}"
                    u = result.get("usage", {})
                    _log_usage(OPENAI_MODEL, u.get("prompt_tokens", 0), u.get("completion_tokens", 0), provider="openai", vendor="zenith_ai")
                    trail.append(f"ZenithAI key{zen_idx + 1} `{OPENAI_MODEL}` : ✓")
                    if _freetheai_tried:
                        _send_cascade_notif(groq_reason, trail, caller)
                    return result
                elif r.status_code == 401:
                    _zenith_bad_key[zen_idx] = True
                    trail.append(f"ZenithAI key{zen_idx + 1} : ✗ 401 clé invalide")
                    break
                else:
                    _zenith_consecutive_errors[zen_idx] += 1
                    trail.append(f"ZenithAI key{zen_idx + 1} : ✗ {r.status_code} (attempt {attempt + 1}/3)")
                    if _zenith_consecutive_errors[zen_idx] >= ZENITH_ERROR_THRESHOLD:
                        _zenith_freeze_until[zen_idx] = time.monotonic() + ZENITH_FREEZE_SECONDS
                        _zenith_consecutive_errors[zen_idx] = 0
                        _send_cascade_notif("openai_down", trail, caller)
                    if attempt < 2:
                        time.sleep(2 ** attempt)
                    continue
            except (httpx.TimeoutException, httpx.ConnectError, httpx.ReadError):
                _zenith_consecutive_errors[zen_idx] += 1
                trail.append(f"ZenithAI key{zen_idx + 1} : ✗ timeout (attempt {attempt + 1}/3)")
                if _zenith_consecutive_errors[zen_idx] >= ZENITH_ERROR_THRESHOLD:
                    _zenith_freeze_until[zen_idx] = time.monotonic() + ZENITH_FREEZE_SECONDS
                    _zenith_consecutive_errors[zen_idx] = 0
                    _send_cascade_notif("openai_down", trail, caller)
                if attempt < 2:
                    time.sleep(2 ** attempt)
                continue
            break

    groq_reason = "openai_down"

    # ── Gemini — position "early" (ow24h, dumb_prod, dumb_scrap : avant Hapuppy) ──
    if cascade in ("ow24h_prod", "dumb_prod", "weekly_scrap"):
        result = _try_gemini(payload_base, timeout, trail)
        if result:
            return result

    # ── Hapuppy gpt-4.1-mini ──────────────────────────────────────────────────────
    hapuppy_key = (
        os.environ.get("HAPUPPY_API_KEY", "")
        if cascade in ("default", "weekly_scrap", "hapuppy_only", "bub_prod", "ow7j_prod", "ow24h_prod", "weekly_prod", "monthly_prod", "dumb_prod") and os.environ.get("HAPUPPY_ENABLED", "1") != "0"
        else ""
    )
    if hapuppy_key:
        global _hapuppy_quota_skip_until, _hapuppy_last_attempt
        now_mono = time.monotonic()

        # Freeze quota : skip jusqu'à expiration du rolling window (~4h)
        if _hapuppy_quota_skip_until is not None:
            if now_mono >= _hapuppy_quota_skip_until:
                _hapuppy_quota_skip_until = None
            else:
                trail.append("Hapuppy : ⏭ frozen (quota épuisé)")
                hapuppy_key = ""

    if hapuppy_key:
        global _hapuppy_ratelimit_until
        if HAPUPPY_MIN_INTERVAL > 0:
            wait = HAPUPPY_MIN_INTERVAL - (time.monotonic() - _hapuppy_last_attempt)
            if wait > 0:
                trail.append(f"Hapuppy : ⏳ RPM throttle ({wait:.1f}s)")
                time.sleep(wait)
        _hap_quota_break = False
        for hap_model in HAPUPPY_MODELS:
            if _hap_quota_break:
                break
            for attempt in range(2):
                try:
                    wait = _hapuppy_ratelimit_until - time.monotonic()
                    if wait > 0:
                        time.sleep(wait + random.uniform(0, 2))
                    _hapuppy_last_attempt = time.monotonic()
                    _hapuppy_timeout = httpx.Timeout(connect=5.0, read=15.0, write=5.0, pool=5.0)
                    chunks: list[bytes] = []
                    status_code = None
                    with _hapuppy_semaphore:
                        with httpx.stream(
                            "POST",
                            HAPUPPY_URL,
                            headers={"Authorization": f"Bearer {hapuppy_key}", "Content-Type": "application/json"},
                            json={**payload_base, "model": hap_model},
                            timeout=_hapuppy_timeout,
                        ) as r:
                            status_code = r.status_code
                            if status_code == 200:
                                for chunk in r.iter_bytes():
                                    chunks.append(chunk)
                            else:
                                r.read()
                                chunks = [r.content]

                    if status_code == 200:
                        result = json.loads(b"".join(chunks))
                        u = result.get("usage", {})
                        _log_usage(hap_model, u.get("prompt_tokens", 0), u.get("completion_tokens", 0), provider="openai", vendor="hapuppy")
                        result["_llm_source"] = "hapuppy"
                        _last_llm_source = "hapuppy"
                        trail.append(f"Hapuppy `{hap_model}` : ✓")
                        if _freetheai_tried:
                            _send_cascade_notif(groq_reason, trail)
                        return result
                    elif status_code == 403:
                        _hap_secs = _seconds_until_hapuppy_reset()
                        _hapuppy_quota_skip_until = time.monotonic() + _hap_secs
                        trail.append(f"Hapuppy `{hap_model}` : ✗ 403 crédits épuisés")
                        _hap_dt = datetime.now(ZoneInfo("Europe/Paris")) + timedelta(seconds=_hap_secs)
                        _hap_h, _hap_m = int(_hap_secs // 3600), int((_hap_secs % 3600) // 60)
                        _hap_header = (f"🔴 *Hapuppy `{hap_model}` — crédits épuisés (403)*\n"
                                       f"Recharger les crédits. Reset {_hap_dt.strftime('%Hh%M')} Paris (dans {_hap_h}h{_hap_m:02d}m).")
                        _send_cascade_notif("hapuppy_quota", trail, caller, header_override=_hap_header)
                        _hapuppy_quota_notif_sent = True
                        _hap_quota_break = True
                        break
                    elif status_code == 429:
                        try:
                            body_text = json.loads(b"".join(chunks)).get("error", {}).get("message", "").lower()
                        except Exception:
                            body_text = ""
                        is_quota = any(k in body_text for k in _HAPUPPY_QUOTA_KEYWORDS)
                        if is_quota:
                            _hap_secs = _seconds_until_hapuppy_reset()
                            _hapuppy_quota_skip_until = time.monotonic() + _hap_secs
                            trail.append(f"Hapuppy `{hap_model}` : ✗ 429 quota épuisé")
                            _hap_dt = datetime.now(ZoneInfo("Europe/Paris")) + timedelta(seconds=_hap_secs)
                            _hap_h, _hap_m = int(_hap_secs // 3600), int((_hap_secs % 3600) // 60)
                            _hap_header = (f"🔴 *Hapuppy `{hap_model}` — quota épuisé (429)*\n"
                                           f"Reset {_hap_dt.strftime('%Hh%M')} Paris (dans {_hap_h}h{_hap_m:02d}m).")
                            _send_cascade_notif("hapuppy_quota", trail, caller, header_override=_hap_header)
                            _hapuppy_quota_notif_sent = True
                            _hap_quota_break = True
                            break
                        else:
                            backoff = HAPUPPY_RATELIMIT_BACKOFF_SECONDS * (attempt + 1)
                            _hapuppy_ratelimit_until = time.monotonic() + backoff
                            trail.append(f"Hapuppy `{hap_model}` : ✗ 429 rate-limit (attempt {attempt + 1}/2, backoff {backoff}s)")
                            if attempt < 1:
                                time.sleep(backoff + random.uniform(0, 3))
                            continue
                    else:
                        trail.append(f"Hapuppy `{hap_model}` : ✗ {status_code} → modèle suivant")
                        break
                except Exception:
                    trail.append(f"Hapuppy `{hap_model}` : ✗ timeout/connexion (attempt {attempt + 1}/2)")
                    if attempt < 1:
                        time.sleep(2 ** attempt)
                    continue
                break

        groq_reason = "hapuppy_down"

    # Trigger global : FreeTheAI quota ET Hapuppy quota simultanément épuisés
    # (supprimé si hapuppy_quota vient d'être envoyé dans ce même call)
    if _freetheai_skip_until is not None and _hapuppy_quota_skip_until is not None and not _hapuppy_quota_notif_sent:
        _now_mono = time.monotonic()
        _hap_secs = max(0.0, _hapuppy_quota_skip_until - _now_mono)
        _hap_dt = datetime.now(ZoneInfo("Europe/Paris")) + timedelta(seconds=_hap_secs)
        _fte_utc = _freetheai_skip_until.strftime("%Hh%M UTC")
        _both_header = (f"🔴 *FreeTheAI + Hapuppy hors quota*\nGroq seul actif.\n"
                        f"Hapuppy reset ~{_hap_dt.strftime('%Hh%M')} Paris · FreeTheAI reset {_fte_utc}.")
        _send_cascade_notif("both_quota", trail, caller, header_override=_both_header)

    # weekly_scrap / hapuppy_only : on s'arrête ici (Groq pas autorisé)
    if cascade in ("weekly_scrap", "hapuppy_only"):
        raise RuntimeError(f"LLM unavailable — {cascade} cascade exhausted")

    # ── Gemini — position "late" (bub, weekly_prod : après Hapuppy) ──────────────
    if cascade in ("bub_prod", "weekly_prod"):
        result = _try_gemini(payload_base, timeout, trail)
        if result:
            return result

    # ── yearly_prod : FreeTheAI seulement, pas de fallback ───────────────────────
    if cascade == "yearly_prod":
        if _freetheai_exhausted:
            raise RuntimeError("FreeTheAI exhausted — yearly sera généré demain")
        raise RuntimeError(f"FreeTheAI unavailable ({groq_reason}) — yearly_prod mode")

    # Cascades prod sans Groq : FreeTheAI → Hapuppy [→ Gemini] → raise
    if cascade in ("bub_prod", "weekly_prod", "ow7j_prod", "monthly_prod"):
        raise RuntimeError(f"LLM unavailable — cascade {cascade} exhausted")

    # ── Groq (fallback final, ou premier essai pour dumb_scrap) ───────────────
    # TODO PROD : réactiver la restriction BATCH_MODE pour préserver le quota Groq prod
    # if not allow_groq or os.environ.get("BATCH_MODE") == "1":
    if cascade in ("bub_prod", "weekly_prod", "ow7j_prod", "monthly_prod", "yearly_prod", "ow24h_prod"):
        raise RuntimeError(f"LLM unavailable — cascade {cascade} exhausted (no Groq in prod)")
    if not allow_groq or os.environ.get("GROQ_ENABLED", "1") == "0":
        raise RuntimeError("LLM unavailable (FreeTheAI/Hapuppy down, Groq disabled)")

    global _groq_consecutive_429, _groq_exhausted, _groq_last_attempt, _groq_bad_key

    if _groq_exhausted:
        if cascade == "dumb_scrap":
            pass  # fall through to Hapuppy fallback below
        else:
            raise GroqExhaustedError("Groq quota daily épuisé (flag actif)")
    elif _groq_bad_key:
        if cascade == "dumb_scrap":
            pass
        else:
            raise RuntimeError("Groq clé invalide 403 (désactivé pour la session)")
    else:
        groq_err = None
        _groq_ok = False
        for attempt in range(3):
            try:
                if GROQ_MIN_INTERVAL > 0:
                    elapsed = time.monotonic() - _groq_last_attempt
                    if elapsed < GROQ_MIN_INTERVAL:
                        time.sleep(GROQ_MIN_INTERVAL - elapsed)
                _groq_last_attempt = time.monotonic()
                r = httpx.post(
                    GROQ_URL,
                    headers={"Authorization": f"Bearer {os.environ['GROQ_API_KEY']}"},
                    json={**payload_base, "model": groq_model},
                    timeout=timeout,
                )
                if r.status_code == 429:
                    _groq_consecutive_429 += 1
                    if _groq_consecutive_429 >= 3:
                        _groq_exhausted = True
                        trail.append(f"Groq `{groq_model}` : ✗ 429 x3 → quota daily épuisé")
                        _send_cascade_notif("groq_exhausted", trail, caller, item_id=item_id)
                        if cascade == "dumb_scrap":
                            break  # fall through to Hapuppy fallback
                        raise GroqExhaustedError("Groq 429 x3 consécutifs")
                    if attempt < 2:
                        time.sleep(20 * (attempt + 1))
                        continue
                    if cascade == "dumb_scrap":
                        break
                r.raise_for_status()
                _groq_consecutive_429 = 0
                result = r.json()
                result["_llm_source"] = "groq"
                _last_llm_source = "groq"
                u = result.get("usage", {})
                _log_usage(groq_model, u.get("prompt_tokens", 0), u.get("completion_tokens", 0))
                trail.append(f"Groq `{groq_model}` : ✓")
                if (_freetheai_tried or groq_reason == "hapuppy_down") and not _hapuppy_quota_notif_sent and _hapuppy_quota_skip_until is None:
                    _send_cascade_notif(groq_reason, trail, caller)
                return result
            except GroqExhaustedError:
                if cascade == "dumb_scrap":
                    break
                raise
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 403:
                    _groq_bad_key = True
                    trail.append(f"Groq `{groq_model}` : ✗ 403 clé invalide (désactivé pour la session)")
                    if cascade == "dumb_scrap":
                        break
                    _send_cascade_notif("all_failed", trail, caller, item_id=item_id)
                    raise RuntimeError("Groq clé invalide 403") from e
                groq_err = str(e)
                if attempt < 2:
                    time.sleep(2 ** attempt)
                    continue
                if cascade == "dumb_scrap":
                    trail.append(f"Groq `{groq_model}` : ✗ {groq_err}")
                    break
                trail.append(f"Groq `{groq_model}` : ✗ {groq_err}")
                _send_cascade_notif("all_failed", trail, caller, item_id=item_id)
                raise
            except Exception as e:
                groq_err = str(e)
                trail.append(f"Groq `{groq_model}` : ✗ {groq_err}")
                if cascade == "dumb_scrap":
                    break
                _send_cascade_notif("all_failed", trail, caller, item_id=item_id)
                raise

    # dumb_scrap : Groq a échoué → Hapuppy comme fallback (sans Gemini)
    if cascade == "dumb_scrap":
        trail.append("Groq ✗ dumb_scrap → Hapuppy fallback")
        return llm_call(
            messages,
            groq_model=groq_model,
            max_tokens=max_tokens,
            temperature=temperature,
            response_format=response_format,
            timeout=timeout,
            allow_groq=False,
            caller=caller,
            item_id=item_id,
            cascade="hapuppy_only",
        )

    raise RuntimeError("LLM unreachable after all retries")


def llm_text(
    messages: list[dict],
    *,
    groq_model: str = "meta-llama/llama-4-scout-17b-16e-instruct",
    max_tokens: int = 2000,
    temperature: float = 0.3,
    timeout: int = 45,
    allow_groq: bool = True,
    caller: str = "",
    item_id: str = "",
    cascade: str = "default",
) -> str:
    """Shortcut → retourne directement le texte du message."""
    resp = llm_call(
        messages,
        groq_model=groq_model,
        max_tokens=max_tokens,
        temperature=temperature,
        timeout=timeout,
        allow_groq=allow_groq,
        caller=caller,
        item_id=item_id,
        cascade=cascade,
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
    caller: str = "",
    item_id: str = "",
    cascade: str = "default",
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
        caller=caller,
        item_id=item_id,
        cascade=cascade,
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
