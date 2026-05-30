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


def run_reenrich():
    """
    Point d'entrée cron 22h30.
    Re-enrichit dans l'ordre : stock_news_cache → article_week_summaries → article_month_summaries.
    S'arrête dès que FreeTheAI n'est plus dispo.
    """
    os.environ["BATCH_MODE"] = "1"
    os.environ["LLM_LANG"] = "en"

    supa = _supa()
    total = 0
    failed = 0

    logger.info("[reenrich] Démarrage re-enrich Groq → FreeTheAI")

    # 1. stock_news_cache — ai_summary générés par Groq
    if not _reenrich_news_cache(supa):
        logger.info(f"[reenrich] FreeTheAI quota atteint sur news_cache — stop. {total} re-enrichis.")
        return

    # 2. article_week_summaries
    if not _reenrich_weekly(supa):
        logger.info(f"[reenrich] FreeTheAI quota atteint sur weekly — stop.")
        return

    # 3. article_month_summaries
    _reenrich_monthly(supa)

    logger.info(f"[reenrich] Terminé.")


def _freetheai_ok() -> bool:
    """Vérifie si FreeTheAI est toujours dispo (pas skip_until)."""
    from services.llm_service import _freetheai_skip_until, _freetheai_available
    return _freetheai_available()


def _reenrich_news_cache(supa) -> bool:
    """Re-enrichit stock_news_cache. Retourne False si FreeTheAI quota atteint."""
    rows = (
        supa.table("stock_news_cache")
        .select("ticker, news_raw, generated_by_fr, generated_by_en")
        .or_("generated_by_fr.eq.groq,generated_by_en.eq.groq")
        .limit(50)
        .execute()
    )
    if not rows.data:
        return True

    logger.info(f"[reenrich] news_cache: {len(rows.data)} tickers Groq à re-enrichir")

    for row in rows.data:
        if not _freetheai_ok():
            return False
        ticker = row["ticker"]
        try:
            from services.news_service import get_news
            get_news(ticker)
            logger.info(f"[reenrich] ✓ news_cache {ticker}")
        except RuntimeError:
            # BATCH_MODE → FreeTheAI down → stop
            return False
        except Exception as e:
            logger.warning(f"[reenrich] ✗ news_cache {ticker}: {e}")

    return True


def _reenrich_weekly(supa) -> bool:
    """Re-enrichit article_week_summaries. Retourne False si FreeTheAI quota atteint."""
    rows = (
        supa.table("article_week_summaries")
        .select("ticker, year_month, week_num, top_articles, article_count")
        .eq("llm_source", "groq")
        .limit(30)
        .execute()
    )
    if not rows.data:
        return True

    logger.info(f"[reenrich] weekly: {len(rows.data)} semaines Groq à re-enrichir")

    from services.period_articles_service import (
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
            summary = _llm_call(WEEK_CONDENSE_PROMPT_EN.format(ticker=ticker, label=label, articles_text=articles_text))
            supa.table("article_week_summaries").update({
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


def _reenrich_monthly(supa) -> bool:
    """Re-enrichit article_month_summaries. Retourne False si FreeTheAI quota atteint."""
    rows = (
        supa.table("article_month_summaries")
        .select("ticker, year_month, article_count")
        .eq("llm_source", "groq")
        .limit(20)
        .execute()
    )
    if not rows.data:
        return True

    logger.info(f"[reenrich] monthly: {len(rows.data)} mois Groq à re-enrichir")

    from services.period_articles_service import (
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
                supa.table("article_week_summaries")
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
            ), max_tokens=500)
            supa.table("article_month_summaries").update({
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
