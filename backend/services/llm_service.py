"""
Cascade LLM : FreeTheAI (primary → secondary) → Hapuppy → Groq

Logique :
  1. bbl/gpt-4.1 (FreeTheAI primary)
  2. bbl/gpt-5.4-mini (FreeTheAI secondary) — si 502/503/timeout sur #1
     → circuit breaker : 3 rounds échoués → freeze 30min → probe auto
  3. Hapuppy gpt-4.1-mini — circuit breaker : 3 échecs → freeze 3h → probe auto
  4. Groq — fallback final
"""

import httpx
import time
import os
import json
from datetime import datetime, timezone
from pathlib import Path

_USAGE_LOG = Path.home() / "llm-stack" / "token_usage.jsonl"

def _log_usage(model: str, tokens_in: int, tokens_out: int) -> None:
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
    try:
        with _USAGE_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


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
HAPUPPY_MODEL = "gpt-4.1-mini"
HAPUPPY_FAIL_THRESHOLD = 3   # échecs consécutifs avant freeze
HAPUPPY_FREEZE_SECONDS = 10800  # 3h

# Désactive FreeTheAI jusqu'au prochain minuit UTC quand checkin manquant
_freetheai_skip_until: datetime | None = None
_freetheai_last_attempt: float = 0.0
FREETHEAI_MIN_INTERVAL = float(os.environ.get("FREETHEAI_MIN_INTERVAL", "6.0"))  # 10 RPM → 6s min

# Circuit breaker FreeTheAI 502/503/timeout — par round (pas par tentative modèle)
_freetheai_fail_count: int = 0
_freetheai_fail_freeze_until: float | None = None  # monotonic timestamp
FREETHEAI_FAIL_THRESHOLD = 3       # rounds échoués consécutifs avant freeze
FREETHEAI_FAIL_FREEZE_SECONDS = 1800  # 30 min

# Circuit breaker Hapuppy gpt-4.1-mini
# States : None=CLOSED · float(future)=OPEN · float(past)=HALF-OPEN (probe)
_hapuppy_fail_count: int = 0
_hapuppy_freeze_until: float | None = None  # monotonic timestamp
_hapuppy_last_attempt: float = 0.0
HAPUPPY_MIN_INTERVAL = float(os.environ.get("HAPUPPY_MIN_INTERVAL", "0.0"))  # pas de throttle par défaut

# Throttle notifs Groq/Hapuppy — 1 ping par raison toutes les 30 min max
_notif_sent_at: dict[str, float] = {}
NOTIF_THROTTLE = 1800.0  # 30 min

_groq_last_attempt: float = 0.0
GROQ_MIN_INTERVAL = float(os.environ.get("GROQ_MIN_INTERVAL", "0"))
_groq_consecutive_429: int = 0
_groq_exhausted: bool = False  # True quand 3 x 429 consécutifs → quota daily épuisé
_last_llm_source: str = "unknown"  # mis à jour par llm_call() après chaque succès


class GroqExhaustedError(RuntimeError):
    """Levée quand Groq encaisse 3 x 429 consécutifs — quota daily vraisemblablement épuisé."""


_CASCADE_HEADERS = {
    "checkin":              "🔴 *FreeTheAI — checkin Discord manquant*\nFais `/checkin` pour débloquer. Reset à minuit UTC.",
    "daily_limit":          "🔴 *FreeTheAI — quota journalier épuisé*\nReset automatique à minuit UTC.",
    "rpm_429":              "🟡 *FreeTheAI — rate limit (429 RPM)*",
    "502_timeout":          "🟠 *FreeTheAI — injoignable (502/timeout)*",
    "freetheai_down_frozen": f"🔴 *FreeTheAI — circuit breaker* ({FREETHEAI_FAIL_THRESHOLD} rounds échoués)\nSkip FreeTheAI 30min. Probe auto après.",
    "groq_exhausted":       "🔴 *Groq — quota daily épuisé*\nBatch arrêté proprement. Reset à minuit UTC.",
    "hapuppy_down":         f"🟠 *Hapuppy `{HAPUPPY_MODEL}` — down (502/timeout)*\nVérifier les crédits Hapuppy.",
    "hapuppy_dsk_frozen":   f"🔴 *Hapuppy `{HAPUPPY_MODEL}` — circuit breaker déclenché* ({HAPUPPY_FAIL_THRESHOLD} échecs consécutifs)\nRecheck dans 3h.",
    "hapuppy_dsk_still_down": f"🟠 *Hapuppy `{HAPUPPY_MODEL}` — toujours down* (probe échoué)\nRecheck dans 3h.",
    "hapuppy_dsk_back":     f"✅ *Hapuppy `{HAPUPPY_MODEL}` — réactivé*\nCircuit breaker reset.",
    "all_failed":           "🔴 *Tous les LLM en échec*",
}


def _send_cascade_notif(reason: str, trail: list[str]) -> None:
    """Notif unique avec le trail complet. Throttle 30min par raison."""
    now = time.monotonic()
    key = f"cascade_{reason}"
    if now - _notif_sent_at.get(key, 0) < NOTIF_THROTTLE:
        return
    _notif_sent_at[key] = now
    header = _CASCADE_HEADERS.get(reason, f"🟠 *Cascade LLM — {reason}*")
    _notify_telegram(f"{header}\n\n" + "\n".join(trail))


def _freetheai_available() -> bool:
    global _freetheai_skip_until, _freetheai_last_attempt, _freetheai_fail_freeze_until, _freetheai_fail_count
    if _freetheai_skip_until is not None:
        if datetime.now(timezone.utc) >= _freetheai_skip_until:
            _freetheai_skip_until = None
        else:
            return False
    # Circuit breaker 502/503/timeout : freeze 30min après 3 rounds échoués
    if _freetheai_fail_freeze_until is not None:
        now_mono = time.monotonic()
        if now_mono < _freetheai_fail_freeze_until:
            return False
        else:
            # Probe : reset freeze, on retente
            _freetheai_fail_freeze_until = None
            _freetheai_fail_count = 0
    # Respecter le RPM : si dernier appel < 6s → skip sans sleep (Groq prend le relais)
    if time.monotonic() - _freetheai_last_attempt < FREETHEAI_MIN_INTERVAL:
        return False
    return True


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

    groq_reason = "502_timeout"
    trail: list[str] = []  # trail complet de la cascade pour la notif TG
    global _last_llm_source

    # ── FreeTheAI ──────────────────────────────────────────────────────────────
    if freetheai_key and _freetheai_available():
        global _freetheai_fail_count, _freetheai_fail_freeze_until
        _round_failed = False  # True si primary+secondary échouent (502/503/timeout)
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
                    trail.append(f"FreeTheAI `{model}` : ✗ checkin manquant")
                    break

                if _is_daily_limit_error(r):
                    _freetheai_skip_until = _next_utc_midnight()
                    groq_reason = "daily_limit"
                    trail.append(f"FreeTheAI `{model}` : ✗ quota journalier épuisé")
                    break

                if r.status_code == 200:
                    _freetheai_fail_count = 0  # reset sur succès
                    result = r.json()
                    result["_llm_source"] = "freetheai"
                    _last_llm_source = "freetheai"
                    u = result.get("usage", {})
                    _log_usage(model, u.get("prompt_tokens", 0), u.get("completion_tokens", 0))
                    return result

                if r.status_code == 429:
                    groq_reason = "rpm_429"
                    trail.append(f"FreeTheAI `{model}` : ✗ rate limit (429)")
                    break

                # 502 / 503 → essai sur le secondary puis marque round échoué
                trail.append(f"FreeTheAI `{model}` : ✗ {r.status_code}")
                _round_failed = True
                continue

            except (httpx.TimeoutException, httpx.ConnectError, httpx.ReadError):
                trail.append(f"FreeTheAI `{model}` : ✗ timeout/connexion")
                _round_failed = True
                continue

        # Circuit breaker : 3 rounds échoués → freeze 30min
        if _round_failed:
            _freetheai_fail_count += 1
            if _freetheai_fail_count >= FREETHEAI_FAIL_THRESHOLD:
                _freetheai_fail_freeze_until = time.monotonic() + FREETHEAI_FAIL_FREEZE_SECONDS
                _freetheai_fail_count = 0
                _send_cascade_notif("freetheai_down_frozen", trail)
    else:
        trail.append("FreeTheAI : ⏭ skipped (checkin/quota/RPM/circuit breaker)")

    # ── Hapuppy gpt-4.1-mini — circuit breaker ───────────────────────────────────
    hapuppy_key = os.environ.get("HAPUPPY_API_KEY", "")
    if hapuppy_key:
        global _hapuppy_fail_count, _hapuppy_freeze_until, _hapuppy_last_attempt
        now_mono = time.monotonic()

        if HAPUPPY_MIN_INTERVAL > 0 and now_mono - _hapuppy_last_attempt < HAPUPPY_MIN_INTERVAL:
            trail.append("Hapuppy : ⏭ skipped (RPM throttle)")
            hapuppy_key = ""  # force skip du bloc entier

    if hapuppy_key:
        now_mono = time.monotonic()
        frozen  = _hapuppy_freeze_until is not None and now_mono < _hapuppy_freeze_until
        is_probe = _hapuppy_freeze_until is not None and now_mono >= _hapuppy_freeze_until

        if frozen:
            trail.append(f"Hapuppy `{HAPUPPY_MODEL}` : ⏭ frozen (circuit breaker)")
        else:
            ok = False
            try:
                _hapuppy_last_attempt = time.monotonic()
                r = httpx.post(
                    HAPUPPY_URL,
                    headers={"Authorization": f"Bearer {hapuppy_key}", "Content-Type": "application/json"},
                    json={**payload_base, "model": HAPUPPY_MODEL},
                    timeout=timeout,
                )
                if r.status_code == 200:
                    ok = True
                    result = r.json()
                    u = result.get("usage", {})
                    _log_usage(HAPUPPY_MODEL, u.get("prompt_tokens", 0), u.get("completion_tokens", 0))
                    if is_probe or _hapuppy_fail_count > 0:
                        _hapuppy_fail_count = 0
                        _hapuppy_freeze_until = None
                        if is_probe:
                            trail.append(f"Hapuppy `{HAPUPPY_MODEL}` : ✓ (probe — réactivé)")
                            _send_cascade_notif("hapuppy_dsk_back", trail)
                    result["_llm_source"] = "hapuppy"
                    _last_llm_source = "hapuppy"
                    trail.append(f"Hapuppy `{HAPUPPY_MODEL}` : ✓")
                    _send_cascade_notif(groq_reason, trail)
                    return result
                else:
                    trail.append(f"Hapuppy `{HAPUPPY_MODEL}` : ✗ {r.status_code}")
            except Exception:
                trail.append(f"Hapuppy `{HAPUPPY_MODEL}` : ✗ timeout/connexion")

            if not ok:
                if is_probe:
                    _hapuppy_freeze_until = time.monotonic() + HAPUPPY_FREEZE_SECONDS
                    _send_cascade_notif("hapuppy_dsk_still_down", trail)
                else:
                    _hapuppy_fail_count += 1
                    if _hapuppy_fail_count >= HAPUPPY_FAIL_THRESHOLD:
                        _hapuppy_freeze_until = time.monotonic() + HAPUPPY_FREEZE_SECONDS
                        _send_cascade_notif("hapuppy_dsk_frozen", trail)

        groq_reason = "hapuppy_down"

    # ── Groq (fallback final) ──────────────────────────────────────────────────
    # TODO PROD : réactiver la restriction BATCH_MODE pour préserver le quota Groq prod
    # if not allow_groq or os.environ.get("BATCH_MODE") == "1":
    if not allow_groq:
        raise RuntimeError("LLM unavailable (FreeTheAI/Hapuppy down, Groq disabled)")

    global _groq_consecutive_429, _groq_exhausted, _groq_last_attempt

    if _groq_exhausted:
        raise GroqExhaustedError("Groq quota daily épuisé (flag actif)")

    groq_err = None
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
                    _send_cascade_notif("groq_exhausted", trail)
                    raise GroqExhaustedError("Groq 429 x3 consécutifs")
                if attempt < 2:
                    time.sleep(20 * (attempt + 1))
                    continue
            r.raise_for_status()
            _groq_consecutive_429 = 0  # reset sur succès
            result = r.json()
            result["_llm_source"] = "groq"
            _last_llm_source = "groq"
            u = result.get("usage", {})
            _log_usage(groq_model, u.get("prompt_tokens", 0), u.get("completion_tokens", 0))
            trail.append(f"Groq `{groq_model}` : ✓")
            _send_cascade_notif(groq_reason, trail)
            return result
        except GroqExhaustedError:
            raise
        except httpx.HTTPStatusError as e:
            groq_err = str(e)
            if attempt < 2:
                time.sleep(2 ** attempt)
                continue
            trail.append(f"Groq `{groq_model}` : ✗ {groq_err}")
            _send_cascade_notif("all_failed", trail)
            raise
        except Exception as e:
            groq_err = str(e)
            trail.append(f"Groq `{groq_model}` : ✗ {groq_err}")
            _send_cascade_notif("all_failed", trail)
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
