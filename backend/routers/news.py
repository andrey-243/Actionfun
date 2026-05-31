from fastapi import APIRouter, HTTPException
from services.overview_service import get_news

router = APIRouter()


@router.get("/news/{ticker}")
async def news(ticker: str):
    try:
        return get_news(ticker.upper())
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
