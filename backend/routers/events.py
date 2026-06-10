"""
GET /api/events/{ticker}?date=&change_pct=

Retourne la cause IA d'un événement chart (BUB) pour une date donnée.
Cache permanent dans bub_cache. Délègue à bubble_service.get_or_gen_bub.
"""
from fastapi import APIRouter, HTTPException, Query
from services.bubble_service import get_or_gen_bub

router = APIRouter()


@router.get("/events/{ticker}")
async def events(ticker: str, date: str = Query(...), change_pct: float = Query(default=0.0)):
    try:
        return get_or_gen_bub(ticker.upper(), date, change_pct)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
