from fastapi import APIRouter, HTTPException, Query
from services.digest_service import generate_digest_for_ticker
from supabase import create_client
from datetime import datetime, timezone, timedelta
import os

router = APIRouter()


def _articles_from_archive(supa, ticker: str, hours: int = 48) -> list[dict]:
    """Lit articles depuis articles_merged_finn_yahoo pour la fenêtre demandée."""
    now = datetime.now(timezone.utc)
    cutoff_ts = int((now.timestamp()) - hours * 3600)
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

    articles = []
    for a in raw:
        ts = a.get("datetime", 0)
        if not ts or ts < cutoff_ts:
            continue
        articles.append({
            "headline": a.get("headline", ""),
            "source": a.get("source", ""),
            "url": a.get("url", ""),
            "datetime": int(ts),
        })
    articles.sort(key=lambda x: x["datetime"], reverse=True)
    return articles[:15]


@router.get("/digest/{ticker}")
async def digest(ticker: str, date: str = Query(default=None), force: bool = Query(default=False)):
    try:
        t = ticker.upper()
        result = generate_digest_for_ticker(t, force=force)
        supa = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])
        cache = supa.table("stock_news_cache").select("website").eq("ticker", t).maybe_single().execute()
        website = (cache.data or {}).get("website") if cache else None
        if not result:
            return {"ticker": t, "date": date, "articles": [], "summary": "", "sentiment": "NEUTRAL", "website": website, "generated_at": None}
        result["website"] = website
        # EN first : normalise summary pour le frontend
        if not result.get("summary") and result.get("summary_en"):
            result["summary"] = result["summary_en"]
        # Fallback : si le digest n'a pas d'articles, les lire directement depuis le cache
        if not result.get("articles"):
            result["articles"] = _articles_from_archive(supa, t, hours=48)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


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
