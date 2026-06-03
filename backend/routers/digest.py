"""
GET /api/weekly/{ticker}  → weekly summary + articles 7j pour la page "Mes news"

Lit depuis :
  - article_week_summaries (résumé AI hebdo si disponible)
  - articles_merged_finn_yahoo (articles 7j bruts, normalisés)
  - stock_news_cache (website pour logo)
Zéro appel LLM à la volée.
"""
from fastapi import APIRouter, HTTPException
from supabase import create_client
from datetime import datetime, timezone, timedelta
import os

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
        r = supa.table("articles_merged_finn_yahoo").select("articles") \
            .eq("ticker", ticker).eq("year_month", ym).maybe_single().execute()
        if r and r.data and r.data.get("articles"):
            raw.extend(r.data["articles"])

    articles = [
        {"headline": a.get("headline", ""), "source": a.get("source", ""), "url": a.get("url", ""), "datetime": int(a["datetime"])}
        for a in raw
        if a.get("datetime", 0) > cutoff_ts and a.get("headline")
    ]
    articles.sort(key=lambda x: x["datetime"], reverse=True)
    return articles[:15]


def _week_articles_from_archive(supa, ticker: str) -> list[dict]:
    """Retourne les articles de articles_merged_finn_yahoo des 7 derniers jours, triés desc."""
    raw = _articles_from_archive(supa, ticker, hours=7 * 24)
    return [
        {
            "title": a.get("headline", ""),
            "source": a.get("source", ""),
            "url": a.get("url", ""),
            "pub_date": datetime.fromtimestamp(a["datetime"], tz=timezone.utc).isoformat(),
        }
        for a in raw[:20]
    ]


@router.get("/weekly/{ticker}")
async def weekly(ticker: str):
    try:
        supa = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])
        t = ticker.upper()
        row = (
            supa.table("article_week_summaries")
            .select("ticker, year_month, week_num, ai_summary, ai_summary_en, article_count, condensed_at")
            .eq("ticker", t)
            .order("year_month", desc=True)
            .order("week_num", desc=True)
            .limit(1)
            .maybe_single()
            .execute()
        )
        cache = supa.table("stock_news_cache").select("website").eq("ticker", t).maybe_single().execute()
        website = (cache.data or {}).get("website") if cache else None
        current_articles = _week_articles_from_archive(supa, t)

        row_data = (row.data or {}) if row and row.data else {}
        ai_summary = row_data.get("ai_summary_en") or row_data.get("ai_summary", "")
        year_month = row_data.get("year_month")
        week_num = row_data.get("week_num")
        condensed_at = row_data.get("condensed_at")

        return {
            "ticker": t,
            "year_month": year_month,
            "week_num": week_num,
            "ai_summary": ai_summary,
            "top_articles": current_articles,
            "website": website,
            "condensed_at": condensed_at,
            "generating": not ai_summary and bool(current_articles),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
