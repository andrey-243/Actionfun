"""
GET /api/news/{ticker}

Stale-while-revalidate : retourne le cache immédiatement (+ is_refreshing=true si stale),
puis lance gen_ow_both() en background. Premier load sans cache = appel bloquant.
Délègue à overview_service (OW 24H + OW 7J + DUMB).
"""
from fastapi import APIRouter, BackgroundTasks, HTTPException
from services.overview_service import gen_ow_both, get_or_refresh_ow_both

router = APIRouter()


@router.get("/news/{ticker}")
def news(ticker: str, background_tasks: BackgroundTasks):
    try:
        t = ticker.upper()
        cached, needs_refresh = get_or_refresh_ow_both(t)

        if cached is None:
            # Aucun cache — premier chargement, appel bloquant obligatoire
            return gen_ow_both(t)

        if needs_refresh:
            background_tasks.add_task(gen_ow_both, t, None, True)

        return cached
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
