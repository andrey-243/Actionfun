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


def reenrich_groq_to_better():
    """
    Point d'entrée cron 22h30.
    Re-enrichit dans l'ordre : ow_cache → breaks_weekly → breaks_monthly.
    S'arrête dès que FreeTheAI n'est plus dispo.
    """
    os.environ["BATCH_MODE"] = "1"
    os.environ["LLM_LANG"] = "en"

    supa = _supa()
    total = 0
    failed = 0

    logger.info("[reenrich] Démarrage re-enrich Groq → FreeTheAI")

    # 1. ow_cache — ai_summary générés par Groq
    if not _reenrich_ow_cache_groq_to_better(supa):
        logger.info(f"[reenrich] FreeTheAI quota atteint sur ow_cache — stop. {total} re-enrichis.")
        return

    # 2. breaks_weekly
    if not _reenrich_break_weekly_groq_to_better(supa):
        logger.info(f"[reenrich] FreeTheAI quota atteint sur breaks_weekly — stop.")
        return

    # 3. breaks_monthly
    _reenrich_break_monthly_groq_to_better(supa)

    logger.info(f"[reenrich] Terminé.")


def _freetheai_ok() -> bool:
    """Vérifie si FreeTheAI est toujours dispo (pas skip_until)."""
    from services.llm_service import _freetheai_skip_until, _freetheai_available
    return _freetheai_available()


def _reenrich_ow_cache_groq_to_better(supa) -> bool:
    """Re-enrichit ow_cache. Retourne False si FreeTheAI quota atteint."""
    rows = (
        supa.table("ow_cache")
        .select("ticker, generated_by_fr, generated_by_en")
        .or_("generated_by_fr.eq.groq,generated_by_en.eq.groq,ai_summary_en.is.null")
        .limit(50)
        .execute()
    )
    if not rows.data:
        return True

    logger.info(f"[reenrich] news_cache: {len(rows.data)} tickers à re-enrichir (Groq ou EN manquant)")

    for row in rows.data:
        if not _freetheai_ok():
            return False
        ticker = row["ticker"]
        try:
            from services.overview_service import gen_ow_both
            gen_ow_both(ticker)
            logger.info(f"[reenrich] ✓ news_cache {ticker}")
        except RuntimeError:
            # BATCH_MODE → FreeTheAI down → stop
            return False
        except Exception as e:
            logger.warning(f"[reenrich] ✗ news_cache {ticker}: {e}")

    return True


def _reenrich_break_weekly_groq_to_better(supa) -> bool:
    """Re-enrichit breaks_weekly. Retourne False si FreeTheAI quota atteint."""
    rows = (
        supa.table("breaks_weekly")
        .select("ticker, year_month, week_num, top_articles, article_count")
        .or_("llm_source.eq.groq,ai_summary_en.is.null")
        .limit(30)
        .execute()
    )
    if not rows.data:
        return True

    logger.info(f"[reenrich] weekly: {len(rows.data)} semaines à re-enrichir (Groq ou EN manquant)")

    from services.breakers_articles_service import (
        _llm_call, WEEK_CONDENSE_PROMPT_EN, _week_label, _summary_col,
    )

    for row in rows.data:
        if not _freetheai_ok():
            return False
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
            logger.info(f"[reenrich] ✓ weekly {ticker} {ym} W{wnum}")
        except RuntimeError:
            return False
        except Exception as e:
            logger.warning(f"[reenrich] ✗ weekly {ticker} {ym} W{wnum}: {e}")

    return True


def _reenrich_break_monthly_groq_to_better(supa) -> bool:
    """Re-enrichit breaks_monthly. Retourne False si FreeTheAI quota atteint."""
    rows = (
        supa.table("breaks_monthly")
        .select("ticker, year_month, article_count")
        .eq("llm_source", "groq")
        .limit(20)
        .execute()
    )
    if not rows.data:
        return True

    logger.info(f"[reenrich] monthly: {len(rows.data)} mois Groq à re-enrichir")

    from services.breakers_articles_service import (
        _llm_call, MONTH_CONDENSE_PROMPT_EN, _month_label, _summary_col,
    )

    for row in rows.data:
        if not _freetheai_ok():
            return False
        ticker = row["ticker"]
        ym = row["year_month"]
        try:
            # Récupérer les weeklies pour reconstruire le monthly
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
            logger.info(f"[reenrich] ✓ monthly {ticker} {ym}")
        except RuntimeError:
            return False
        except Exception as e:
            logger.warning(f"[reenrich] ✗ monthly {ticker} {ym}: {e}")

    return True
