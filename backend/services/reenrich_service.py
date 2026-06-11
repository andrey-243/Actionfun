"""
Re-enrich background — 22h30 chaque soir.

Trouve les records générés par Groq (llm_source='groq') et tente de les
re-enrichir avec FreeTheAI uniquement (BATCH_MODE=1, allow_groq=False).

Priorité absolue aux tâches live du jour — ce service ne tourne qu'à 22h30,
bien après digest (18h) et cold_start_translate_fr (19h).

Si FreeTheAI quota atteint → stop proprement, retry le lendemain.
"""

import os
import logging
from datetime import datetime, timezone

from supabase import create_client

logger = logging.getLogger(__name__)


def _supa():
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])


def reenrich_groq_to_better() -> dict:
    """
    Point d'entrée cron 22h30.
    Re-enrichit dans l'ordre : ow_cache → breaks_weekly → breaks_monthly.
    S'arrête dès que FreeTheAI n'est plus dispo.
    Retourne {"ow_cache": n, "weekly": n, "monthly": n, "stopped_at": str|None}.
    """
    os.environ["BATCH_MODE"] = "1"
    os.environ["LLM_LANG"] = "en"

    supa = _supa()
    logger.info("[reenrich] Démarrage re-enrich Groq → FreeTheAI")

    ok, n_ow = _reenrich_ow_cache_groq_to_better(supa)
    if not ok:
        logger.info(f"[reenrich] FreeTheAI quota atteint sur ow_cache — stop. {n_ow} re-enrichis.")
        return {"ow_cache": n_ow, "weekly": 0, "monthly": 0, "stopped_at": "ow_cache"}

    ok, n_weekly = _reenrich_break_weekly_groq_to_better(supa)
    if not ok:
        logger.info(f"[reenrich] FreeTheAI quota atteint sur breaks_weekly — stop.")
        return {"ow_cache": n_ow, "weekly": n_weekly, "monthly": 0, "stopped_at": "breaks_weekly"}

    _, n_monthly = _reenrich_break_monthly_groq_to_better(supa)
    logger.info(f"[reenrich] Terminé. ow_cache={n_ow} weekly={n_weekly} monthly={n_monthly}")
    return {"ow_cache": n_ow, "weekly": n_weekly, "monthly": n_monthly, "stopped_at": None}


def _freetheai_ok() -> bool:
    """Vérifie si FreeTheAI est toujours dispo (pas skip_until)."""
    from services.llm_service import _freetheai_skip_until, _freetheai_available
    return _freetheai_available()


def _reenrich_ow_cache_groq_to_better(supa) -> tuple[bool, int]:
    """Re-enrichit ow_cache. Retourne (True, n) si terminé, (False, n) si quota atteint."""
    rows = (
        supa.table("ow_cache")
        .select("ticker, generated_by_fr, generated_by_en")
        .or_("generated_by_fr.eq.groq,generated_by_en.eq.groq,ai_summary_en.is.null")
        .limit(50)
        .execute()
    )
    if not rows.data:
        return True, 0

    logger.info(f"[reenrich] ow_cache: {len(rows.data)} tickers à re-enrichir (Groq ou EN manquant)")
    done = 0
    for row in rows.data:
        if not _freetheai_ok():
            return False, done
        ticker = row["ticker"]
        try:
            from services.overview_service import gen_ow_both
            gen_ow_both(ticker)
            done += 1
            logger.info(f"[reenrich] ✓ ow_cache {ticker}")
        except RuntimeError:
            return False, done
        except Exception as e:
            logger.warning(f"[reenrich] ✗ ow_cache {ticker}: {e}")

    return True, done


def _reenrich_break_weekly_groq_to_better(supa) -> tuple[bool, int]:
    """Re-enrichit breaks_weekly. Retourne (True, n) si terminé, (False, n) si quota atteint."""
    rows = (
        supa.table("breaks_weekly")
        .select("ticker, year_month, week_num, top_articles, article_count")
        .or_("llm_source.eq.groq,ai_summary_en.is.null")
        .limit(30)
        .execute()
    )
    if not rows.data:
        return True, 0

    logger.info(f"[reenrich] weekly: {len(rows.data)} semaines à re-enrichir (Groq ou EN manquant)")

    from services.breakers_articles_service import (
        _llm_call, WEEK_CONDENSE_PROMPT_EN, _week_label, _summary_col,
    )

    done = 0
    for row in rows.data:
        if not _freetheai_ok():
            return False, done
        ticker = row["ticker"]
        ym = row["year_month"]
        wnum = row["week_num"]
        articles = row.get("top_articles") or []
        if not articles:
            continue
        try:
            articles_text = "\n".join(f"- {a.get('headline', '')}" for a in articles[:30])
            label = _week_label(ym, wnum)
            summary = _llm_call(WEEK_CONDENSE_PROMPT_EN.format(ticker=ticker, label=label, articles_text=articles_text), cascade="weekly_prod")
            supa.table("breaks_weekly").update({
                _summary_col(): summary,
                "ai_summary_en": summary,
                "llm_source": _llm_call._last_source,
                "condensed_at": datetime.now(timezone.utc).isoformat(),
            }).eq("ticker", ticker).eq("year_month", ym).eq("week_num", wnum).execute()
            done += 1
            logger.info(f"[reenrich] ✓ weekly {ticker} {ym} W{wnum}")
        except RuntimeError:
            return False, done
        except Exception as e:
            logger.warning(f"[reenrich] ✗ weekly {ticker} {ym} W{wnum}: {e}")

    return True, done


def _reenrich_break_monthly_groq_to_better(supa) -> tuple[bool, int]:
    """Re-enrichit breaks_monthly. Retourne (True, n) si terminé, (False, n) si quota atteint."""
    rows = (
        supa.table("breaks_monthly")
        .select("ticker, year_month, article_count")
        .eq("llm_source", "groq")
        .limit(20)
        .execute()
    )
    if not rows.data:
        return True, 0

    logger.info(f"[reenrich] monthly: {len(rows.data)} mois Groq à re-enrichir")

    from services.breakers_articles_service import (
        _llm_call, MONTH_CONDENSE_PROMPT_EN, _month_label, _summary_col,
    )

    done = 0
    for row in rows.data:
        if not _freetheai_ok():
            return False, done
        ticker = row["ticker"]
        ym = row["year_month"]
        try:
            week_rows = (
                supa.table("breaks_weekly")
                .select("week_num, ai_summary_en, ai_summary")
                .eq("ticker", ticker)
                .eq("year_month", ym)
                .order("week_num")
                .execute()
            )
            if not week_rows.data or len(week_rows.data) < 2:
                continue
            weeks_text = "\n".join(
                f"W{r['week_num']}: {r.get('ai_summary_en') or r.get('ai_summary', '')}"
                for r in week_rows.data
            )
            summary = _llm_call(MONTH_CONDENSE_PROMPT_EN.format(
                ticker=ticker, label=_month_label(ym), weeks_text=weeks_text,
            ), max_tokens=500, cascade="monthly_prod")
            supa.table("breaks_monthly").update({
                _summary_col(): summary,
                "ai_summary_en": summary,
                "llm_source": _llm_call._last_source,
                "condensed_at": datetime.now(timezone.utc).isoformat(),
            }).eq("ticker", ticker).eq("year_month", ym).execute()
            done += 1
            logger.info(f"[reenrich] ✓ monthly {ticker} {ym}")
        except RuntimeError:
            return False, done
        except Exception as e:
            logger.warning(f"[reenrich] ✗ monthly {ticker} {ym}: {e}")

    return True, done
