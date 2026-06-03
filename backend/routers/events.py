"""
GET /api/events/{ticker}?date=&change_pct=

Retourne la cause IA d'un événement chart (BUB) pour une date donnée.
Cache permanent dans event_causes. Délègue à bubble_service.get_event_news.
"""
from fastapi import APIRouter, HTTPException, Query
from services.bubble_service import get_event_news

router = APIRouter()


@router.get("/events/{ticker}")
async def events(ticker: str, date: str = Query(...), change_pct: float = Query(default=0.0)):
    try:
        return get_event_news(ticker.upper(), date, change_pct)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
