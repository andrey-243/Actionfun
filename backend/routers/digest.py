"""
GET /api/weekly/{ticker}  → OW 7J + articles 7j pour la page "Mes news"

Lit depuis :
  - ow_cache (même OW 7J que la page stock + cached_at + website)
  - articles (articles 7j bruts, normalisés)
Zéro appel LLM à la volée.
"""
from fastapi import APIRouter, HTTPException
from supabase import create_client
from datetime import datetime, timezone, timedelta
import os
from services.breakers_articles_service import _is_ticker_related

router = APIRouter()


def _articles_from_archive(supa, ticker: str, hours: int = 48) -> list[dict]:
    now = datetime.now(timezone.utc)
    cutoff_ts = int(now.timestamp()) - hours * 3600
    year_months = {now.strftime("%Y-%m")}
    if now.day <= (hours // 24 + 1):
        prev = now.replace(day=1) - timedelta(days=1)
        year_months.add(prev.strftime("%Y-%m"))

    raw = []
    for ym in sorted(year_months):
        r = supa.table("articles").select("articles") \
            .eq("ticker", ticker).eq("year_month", ym).maybe_single().execute()
        if r and r.data and r.data.get("articles"):
            raw.extend(r.data["articles"])

    articles = [
        {
            "headline": a.get("headline", ""),
            "source":   a.get("source", ""),
            "url":      a.get("url", ""),
            "datetime": int(a["datetime"]),
            "dumb_en":  a.get("dumb_en", ""),
            "dumb_fr":  a.get("dumb_fr", ""),
        }
        for a in raw
        if a.get("datetime", 0) > cutoff_ts
        and a.get("headline")
        and a.get("is_related") is not False
    ]
    articles.sort(key=lambda x: x["datetime"], reverse=True)
    return articles[:100]


def _week_articles_from_archive(supa, ticker: str, company_name: str = "") -> list[dict]:
    """Retourne les articles de articles des 7 derniers jours, triés desc."""
    raw = _articles_from_archive(supa, ticker, hours=7 * 24)
    return [
        {
            "title":    a.get("headline", ""),
            "source":   a.get("source", ""),
            "url":      a.get("url", ""),
            "pub_date": datetime.fromtimestamp(a["datetime"], tz=timezone.utc).isoformat(),
            "dumb_en":  a.get("dumb_en", ""),
            "dumb_fr":  a.get("dumb_fr", ""),
        }
        for a in raw[:20]
        if _is_ticker_related(a, ticker, company_name)
    ]


@router.get("/weekly/{ticker}")
def weekly(ticker: str):
    try:
        supa = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])
        t = ticker.upper()

        cache = supa.table("ow_cache") \
            .select("ow7d_en, ow7d_fr, ow1d_cached_at_en, website") \
            .eq("ticker", t).maybe_single().execute()
        row = (cache.data or {}) if cache and cache.data else {}

        ai_summary = row.get("ow7d_en") or row.get("ow7d_fr", "")
        cached_at = row.get("ow1d_cached_at_en")
        website = row.get("website")

        # company_name depuis pinned_stocks pour enrichir _is_ticker_related
        cn_row = supa.table("pinned_stocks").select("company_name") \
            .eq("ticker", t).limit(1).execute()
        company_name = (cn_row.data[0].get("company_name", "") if cn_row.data else "") or ""

        current_articles = _week_articles_from_archive(supa, t, company_name)

        return {
            "ticker": t,
            "ai_summary": ai_summary,
            "top_articles": current_articles,
            "website": website,
            "cached_at": cached_at,
            "generating": not ai_summary and bool(current_articles),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
