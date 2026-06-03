"""
GET /api/news/{ticker}

Stale-while-revalidate : retourne le cache immédiatement (+ is_refreshing=true si stale),
puis lance get_news() en background. Premier load sans cache = appel bloquant.
Délègue à overview_service (OW 24H + OW 7J + DUMB).
"""
from fastapi import APIRouter, BackgroundTasks, HTTPException
from services.overview_service import get_news, get_news_stale

router = APIRouter()


@router.get("/news/{ticker}")
async def news(ticker: str, background_tasks: BackgroundTasks):
    try:
        t = ticker.upper()
        cached, needs_refresh = get_news_stale(t)

        if cached is None:
            # Aucun cache — premier chargement, appel bloquant obligatoire
            return get_news(t)

        if needs_refresh:
            background_tasks.add_task(get_news, t, None, True)

        return cached
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
