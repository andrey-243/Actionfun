from fastapi import APIRouter, HTTPException, Query
from services.yfinance_service import get_stock_data

router = APIRouter()

VALID_PERIODS = {"1d", "5d", "1mo", "1y", "5y", "max"}

@router.get("/stock/{ticker}")
async def stock(ticker: str, period: str = Query(default="6mo")):
    if period not in VALID_PERIODS | {"6mo"}:
        period = "6mo"
    try:
        return get_stock_data(ticker.upper(), period=period)
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"Ticker {ticker} not found: {str(e)}")
