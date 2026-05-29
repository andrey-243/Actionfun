from fastapi import APIRouter, HTTPException, Query

router = APIRouter()


@router.get("/articles/{ticker}")
async def period_articles(
    ticker: str,
    period: str = Query(..., regex="^(1mo|6mo|1y|all|max)$"),
):
    try:
        from services.period_articles_service import get_period_articles, get_all_available
        if period in ("all", "max"):
            return get_all_available(ticker.upper())
        return get_period_articles(ticker.upper(), period)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
