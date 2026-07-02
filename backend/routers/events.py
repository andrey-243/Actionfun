"""
GET  /api/events/{ticker}?date=&change_pct=&lang=
GET  /api/bub-dates/{ticker}?lang=
POST /api/translate-bub/{ticker}/{date}
"""
import logging
from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from services.bubble_service import get_or_gen_bub, _get_supabase, BUB_NO_ARTICLES
from services.translation_service import translate_to_fr

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/events/{ticker}")
async def events(
    ticker: str,
    date: str = Query(...),
    change_pct: float = Query(default=0.0),
    lang: str = Query(default="en"),
):
    try:
        return get_or_gen_bub(ticker.upper(), date, change_pct, lang=lang)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/bub-dates/{ticker}")
async def bub_dates(ticker: str, lang: str = Query(default="en")):
    try:
        supa = _get_supabase()
        res = supa.table("bub_cache").select("date, bub_en, bub_fr").eq("ticker", ticker.upper()).execute()
        _no = (None, "", BUB_NO_ARTICLES)
        if lang == "fr":
            dates = [r["date"] for r in res.data if r.get("bub_fr") not in _no or r.get("bub_en") not in _no]
        else:
            dates = [r["date"] for r in res.data if r.get("bub_en") not in _no]
        return {"dates": dates}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/translate-bub/{ticker}/{date}")
async def translate_bub(ticker: str, date: str, background_tasks: BackgroundTasks):
    background_tasks.add_task(_do_translate_bub, ticker.upper(), date)
    return {"status": "queued"}


def _do_translate_bub(ticker: str, date: str) -> None:
    try:
        supa = _get_supabase()
        row = supa.table("bub_cache").select("bub_en, bub_fr").eq("ticker", ticker).eq("date", date).single().execute()
        if not row.data:
            return
        bub_en = row.data.get("bub_en")
        bub_fr = row.data.get("bub_fr")
        if not bub_en or bub_en == BUB_NO_ARTICLES or bub_fr:
            return
        translated = translate_to_fr(bub_en)
        if translated:
            supa.table("bub_cache").update({"bub_fr": translated}).eq("ticker", ticker).eq("date", date).execute()
            logger.info(f"translate-bub OK: {ticker} {date}")
    except Exception as e:
        logger.warning(f"translate-bub failed: {ticker} {date}: {e}")
