"""
Cold start pour les tickers hors top 250 (jamais scrappés).

Phases progressives déclenchées au premier clic :
  1m  → Finnhub 1 mois  + weekly condensation   (~2 min)
  6m  → Finnhub 6 mois  + weeklies + monthlies  (~7 min)
  1y  → Finnhub 1 an    + weeklies + monthlies + yearly (~15 min)

- Toujours en EN (pas de translation overhead au cold start)
- Si user FR : pending_fr=True → traduit EOD ou au prochain clic FR
- Chaque phase complète → phase suivante se lance automatiquement
"""

import os
import logging
import httpx
from datetime import datetime, timezone, timedelta, date

from supabase import create_client

logger = logging.getLogger(__name__)

PHASES = ["1m", "6m", "1y"]
PHASE_DAYS = {"1m": 31, "6m": 183, "1y": 365}
PHASE_ETA_SECONDS = {"1m": 120, "6m": 420, "1y": 900}
PHASE_LABEL = {
    "1m": "Chargement du dernier mois de données",
    "6m": "Chargement des 6 derniers mois",
    "1y": "Chargement de l'année complète",
}
PHASE_LABEL_EN = {
    "1m": "Loading last month of data",
    "6m": "Loading last 6 months",
    "1y": "Loading full year",
}


def _supa():
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])


def get_init_status(ticker: str) -> dict:
    """Retourne le statut cold start du ticker. Crée une entrée si absente."""
    supa = _supa()
    row = supa.table("ticker_init_status").select("*").eq("ticker", ticker).maybe_single().execute()
    if row and row.data:
        return row.data
    return {"ticker": ticker, "phase": "uninitialized", "status": "idle", "pending_fr": False}


def is_initialized(ticker: str) -> bool:
    status = get_init_status(ticker)
    return status.get("phase") == "1y" and status.get("status") == "done"


def needs_cold_start(ticker: str) -> bool:
    """True si le ticker n'a aucune donnée — ni archive historique ni cache news."""
    supa = _supa()
    archive = supa.table("article_archive").select("ticker").eq("ticker", ticker).limit(1).execute()
    if archive and archive.data:
        return False
    cache = supa.table("stock_news_cache").select("ticker").eq("ticker", ticker).limit(1).execute()
    if cache and cache.data:
        return False
    return True


def trigger_cold_start(ticker: str, lang: str = "en") -> dict:
    """
    Déclenche le cold start si nécessaire. Retourne le statut actuel.
    À appeler depuis un background task FastAPI.
    """
    status = get_init_status(ticker)
    current_phase = status.get("phase", "uninitialized")
    current_status = status.get("status", "idle")

    # Déjà en cours ou terminé
    if current_status == "running":
        return status
    if current_phase == "1y" and current_status == "done":
        if lang == "fr" and not status.get("pending_fr"):
            pass  # déjà tout bon
        return status

    # Marquer pending_fr si user FR
    pending_fr = lang == "fr"

    # Démarrer depuis la première phase non complète
    next_phase = _next_phase(current_phase, current_status)
    if not next_phase:
        return status

    _set_status(ticker, next_phase, "running", pending_fr=pending_fr or status.get("pending_fr", False))
    _run_phase(ticker, next_phase)
    return get_init_status(ticker)


def _next_phase(current_phase: str, current_status: str) -> str | None:
    if current_phase == "uninitialized" or current_phase not in PHASES:
        return "1m"
    if current_status == "done":
        idx = PHASES.index(current_phase)
        if idx + 1 < len(PHASES):
            return PHASES[idx + 1]
        return None  # tout terminé
    # erreur ou idle sur phase courante → retry
    return current_phase


def _set_status(ticker: str, phase: str, status: str, pending_fr: bool = False, error: str = None):
    supa = _supa()
    now = datetime.now(timezone.utc).isoformat()
    data = {
        "ticker": ticker,
        "phase": phase,
        "status": status,
        "pending_fr": pending_fr,
        "updated_at": now,
    }
    if status == "running":
        data["started_at"] = now
        data["error"] = None
    if status == "done":
        data["completed_at"] = now
    if error:
        data["error"] = error
    supa.table("ticker_init_status").upsert(data).execute()


def _run_phase(ticker: str, phase: str):
    """Exécute une phase complète : scrape Finnhub + condensation. Synchrone (appelé en background)."""
    logger.info(f"[cold_start] {ticker} — phase {phase} start")
    try:
        days = PHASE_DAYS[phase]
        articles = _fetch_finnhub(ticker, days)
        if articles:
            _store_articles(ticker, articles)
            _condense(ticker, phase)
        _set_status(ticker, phase, "done")
        logger.info(f"[cold_start] {ticker} — phase {phase} done ({len(articles)} articles)")

        # Lancer la phase suivante automatiquement
        next_phase = _next_phase(phase, "done")
        if next_phase:
            _set_status(ticker, next_phase, "running", pending_fr=_get_pending_fr(ticker))
            _run_phase(ticker, next_phase)

    except Exception as e:
        logger.error(f"[cold_start] {ticker} — phase {phase} error: {e}")
        _set_status(ticker, phase, "error", error=str(e))


def _get_pending_fr(ticker: str) -> bool:
    supa = _supa()
    row = supa.table("ticker_init_status").select("pending_fr").eq("ticker", ticker).maybe_single().execute()
    return bool(row and row.data and row.data.get("pending_fr"))


def _fetch_finnhub(ticker: str, days: int) -> list[dict]:
    today = date.today()
    from_date = (today - timedelta(days=days)).isoformat()
    to_date = today.isoformat()

    r = httpx.get(
        "https://finnhub.io/api/v1/company-news",
        params={"symbol": ticker, "from": from_date, "to": to_date, "token": os.environ["FINNHUB_API_KEY"]},
        timeout=20,
    )
    r.raise_for_status()
    raw = r.json()
    if not isinstance(raw, list):
        return []

    return [
        {
            "headline": a["headline"],
            "source": a["source"],
            "url": a["url"],
            "datetime": a["datetime"],
            "summary": a.get("summary", ""),
            "image": a.get("image", ""),
        }
        for a in raw
        if a.get("headline") and a.get("url")
    ]


def _store_articles(ticker: str, articles: list[dict]):
    supa = _supa()
    by_month: dict[str, list[dict]] = {}
    for a in articles:
        ts = a.get("datetime", 0)
        if not ts:
            continue
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        ym = f"{dt.year:04d}-{dt.month:02d}"
        by_month.setdefault(ym, []).append(a)

    for ym, month_articles in by_month.items():
        row = supa.table("article_archive").select("articles").eq("ticker", ticker).eq("year_month", ym).limit(1).execute()
        existing = row.data[0]["articles"] if row.data else []
        existing_urls = {a.get("url") for a in existing}
        new_ones = [a for a in month_articles if a.get("url") not in existing_urls]
        if not new_ones and existing:
            continue
        merged = sorted(existing + new_ones, key=lambda a: a.get("datetime", 0), reverse=True)[:50]
        supa.table("article_archive").upsert({
            "ticker": ticker,
            "year_month": ym,
            "articles": merged,
            "source": "finnhub",
            "cached_at": datetime.now(timezone.utc).isoformat(),
        }).execute()


def _condense(ticker: str, phase: str):
    """Condense les données disponibles selon la phase. Toujours en EN (BATCH_MODE)."""
    os.environ["BATCH_MODE"] = "1"
    os.environ["LLM_LANG"] = "en"

    from services.period_articles_service import (
        _get_supabase as _pas_supa, _week_num, _week_label, _month_label,
        _normalize_finnhub, _llm_call,
        WEEK_CONDENSE_PROMPT_EN, MONTH_CONDENSE_PROMPT_EN,
        _purge_excess_weeklies,
    )

    supa = _supa()
    today = date.today()
    days = PHASE_DAYS[phase]
    start = today - timedelta(days=days)
    months_to_condense = _months_in_range(start, today)

    # Weeklies pour chaque mois couvert
    for ym in months_to_condense:
        archive = supa.table("article_archive").select("articles").eq("ticker", ticker).eq("year_month", ym).maybe_single().execute()
        if not archive or not archive.data:
            continue
        articles = archive.data.get("articles") or []
        if not articles:
            continue

        # Grouper par semaine
        from collections import defaultdict
        by_week: dict[int, list[dict]] = defaultdict(list)
        for a in articles:
            ts = a.get("datetime", 0)
            if not ts:
                continue
            day = datetime.fromtimestamp(ts, tz=timezone.utc).day
            wnum = _week_num(day)
            by_week[wnum].append(a)

        for wnum, week_articles in by_week.items():
            exists = supa.table("article_week_summaries").select("week_num").eq("ticker", ticker).eq("year_month", ym).eq("week_num", wnum).maybe_single().execute()
            if exists and exists.data:
                continue
            articles_text = "\n".join(f"- {a['headline']}" for a in week_articles[:30])
            label = _week_label(ym, wnum)
            try:
                summary = _llm_call(WEEK_CONDENSE_PROMPT_EN.format(ticker=ticker, label=label, articles_text=articles_text))
                top_articles = sorted(week_articles, key=lambda a: len(a.get("summary", "")), reverse=True)[:10]
                supa.table("article_week_summaries").upsert({
                    "ticker": ticker, "year_month": ym, "week_num": wnum,
                    "ai_summary": summary, "ai_summary_en": summary,
                    "top_articles": top_articles, "article_count": len(week_articles),
                    "condensed_at": datetime.now(timezone.utc).isoformat(),
                }).execute()
            except Exception as e:
                logger.warning(f"[cold_start] weekly {ticker} {ym} W{wnum}: {e}")

        _purge_excess_weeklies(ticker, supa, max_weeks=5)

    # Monthlies à partir de 6M
    if phase in ("6m", "1y"):
        for ym in months_to_condense:
            exists = supa.table("article_month_summaries").select("year_month").eq("ticker", ticker).eq("year_month", ym).maybe_single().execute()
            if exists and exists.data:
                continue
            week_rows = supa.table("article_week_summaries").select("week_num, ai_summary_en, article_count").eq("ticker", ticker).eq("year_month", ym).order("week_num").execute()
            if not week_rows.data or len(week_rows.data) < 2:
                continue
            weeks_text = "\n".join(f"W{r['week_num']}: {r.get('ai_summary_en') or r.get('ai_summary', '')}" for r in week_rows.data)
            article_count = sum(r.get("article_count", 0) for r in week_rows.data)
            try:
                summary = _llm_call(MONTH_CONDENSE_PROMPT_EN.format(ticker=ticker, label=_month_label(ym), weeks_text=weeks_text), max_tokens=500)
                supa.table("article_month_summaries").upsert({
                    "ticker": ticker, "year_month": ym,
                    "ai_summary": summary, "ai_summary_en": summary,
                    "article_count": article_count,
                    "condensed_at": datetime.now(timezone.utc).isoformat(),
                }).execute()
            except Exception as e:
                logger.warning(f"[cold_start] monthly {ticker} {ym}: {e}")


def _months_in_range(start: date, end: date) -> list[str]:
    months = []
    current = start.replace(day=1)
    while current <= end:
        months.append(f"{current.year:04d}-{current.month:02d}")
        if current.month == 12:
            current = current.replace(year=current.year + 1, month=1)
        else:
            current = current.replace(month=current.month + 1)
    return months


def translate_pending_fr():
    """Cron EOD — traduit les tickers cold-startés avec pending_fr=True."""
    supa = _supa()
    rows = supa.table("ticker_init_status").select("ticker").eq("pending_fr", True).execute()
    tickers = [r["ticker"] for r in (rows.data or [])]
    if not tickers:
        return

    logger.info(f"[cold_start] translate_pending_fr — {len(tickers)} tickers")
    from services.translation_service import translate_to_fr

    for ticker in tickers:
        try:
            _translate_ticker_fr(ticker, supa, translate_to_fr)
            supa.table("ticker_init_status").update({"pending_fr": False}).eq("ticker", ticker).execute()
            logger.info(f"[cold_start] FR translated: {ticker}")
        except Exception as e:
            logger.warning(f"[cold_start] FR translation failed {ticker}: {e}")


def _translate_ticker_fr(ticker: str, supa, translate_fn):
    """Traduit ai_summary + ai_summary_24h dans stock_news_cache et article summaries."""
    # stock_news_cache
    row = supa.table("stock_news_cache").select("ai_summary_en, ai_summary_24h_en").eq("ticker", ticker).maybe_single().execute()
    if row and row.data:
        updates = {}
        if row.data.get("ai_summary_en") and not row.data.get("ai_summary"):
            updates["ai_summary"] = translate_fn(row.data["ai_summary_en"])
        if row.data.get("ai_summary_24h_en") and not row.data.get("ai_summary_24h"):
            updates["ai_summary_24h"] = translate_fn(row.data["ai_summary_24h_en"])
        if updates:
            supa.table("stock_news_cache").update(updates).eq("ticker", ticker).execute()

    # article_week_summaries — traduire ai_summary_en → ai_summary
    weeks = supa.table("article_week_summaries").select("year_month, week_num, ai_summary_en").eq("ticker", ticker).execute()
    for w in (weeks.data or []):
        if w.get("ai_summary_en") and not w.get("ai_summary"):
            fr = translate_fn(w["ai_summary_en"])
            supa.table("article_week_summaries").update({"ai_summary": fr}).eq("ticker", ticker).eq("year_month", w["year_month"]).eq("week_num", w["week_num"]).execute()
