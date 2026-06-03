"""
GET /api/articles/{ticker}?period=

Retourne les sections articles + résumés AI (Breaks) selon la période :
  1mo  → semaines W1-W4 (article_week_summaries + articles bruts)
  6mo  → mois (article_month_summaries + articles bruts)
  1y   → mois (article_month_summaries + articles bruts)
  all  → 5Y mois par mois : breaker si dispo, sinon articles bruts
         Source : breakers_articles_service.get_period_articles / get_all_available
"""
from fastapi import APIRouter, HTTPException, Query

router = APIRouter()


@router.get("/articles/{ticker}")
async def period_articles(
    ticker: str,
    period: str = Query(..., regex="^(1mo|6mo|1y|all|max)$"),
):
    try:
        from services.breakers_articles_service import get_period_articles, get_all_available
        if period in ("all", "max"):
            return get_all_available(ticker.upper())
        return get_period_articles(ticker.upper(), period)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
