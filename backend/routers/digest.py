from fastapi import APIRouter, HTTPException, Query
from services.digest_service import generate_digest_for_ticker
from supabase import create_client
import os

router = APIRouter()


@router.get("/digest/{ticker}")
async def digest(ticker: str, date: str = Query(default=None)):
    try:
        t = ticker.upper()
        result = generate_digest_for_ticker(t)
        if not result:
            return {"ticker": t, "date": date, "articles": [], "summary": "", "sentiment": "NEUTRAL", "website": None}
        supa = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])
        cache = supa.table("stock_news_cache").select("website").eq("ticker", t).maybe_single().execute()
        result["website"] = (cache.data or {}).get("website") if cache else None
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/weekly/{ticker}")
async def weekly(ticker: str):
    try:
        supa = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])
        t = ticker.upper()
        row = (
            supa.table("article_week_summaries")
            .select("ticker, year_month, week_num, ai_summary, top_articles, article_count")
            .eq("ticker", t)
            .order("year_month", desc=True)
            .order("week_num", desc=True)
            .limit(1)
            .maybe_single()
            .execute()
        )
        if not row or not row.data:
            return {"ticker": t, "year_month": None, "week_num": None, "ai_summary": "", "top_articles": [], "website": None}
        data = row.data
        cache = supa.table("stock_news_cache").select("website").eq("ticker", t).maybe_single().execute()
        data["website"] = (cache.data or {}).get("website") if cache else None
        return data
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
