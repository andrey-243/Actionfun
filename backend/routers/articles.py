"""
GET /api/articles/{ticker}?period=

Retourne les sections articles + résumés AI (Breaks) selon la période :
  1mo  → semaines W1-W4 (breaks_weekly + articles bruts)
  6mo  → mois (breaks_monthly + articles bruts)
  1y   → mois (breaks_monthly + articles bruts)
  all  → 5Y mois par mois : breaker si dispo, sinon articles bruts
         Source : breakers_articles_service.read_breaks_by_period / read_breaks_periods_available
"""
from fastapi import APIRouter, HTTPException, Query

router = APIRouter()


@router.get("/articles/{ticker}")
async def period_articles(
    ticker: str,
    period: str = Query(..., regex="^(1mo|6mo|1y|all|max)$"),
    company_name: str = Query("", alias="company_name"),
):
    try:
        from services.breakers_articles_service import read_breaks_by_period, read_breaks_periods_available
        if period in ("all", "max"):
            return read_breaks_periods_available(ticker.upper())
        return read_breaks_by_period(ticker.upper(), period, company_name)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
