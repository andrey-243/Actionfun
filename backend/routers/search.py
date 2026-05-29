from fastapi import APIRouter, HTTPException, Query
import httpx

router = APIRouter()

ALLOWED_TYPES = {"EQUITY", "ETF", "CRYPTOCURRENCY"}


@router.get("/search")
async def search(q: str = Query(..., min_length=1, max_length=50)):
    if not q.strip():
        return []
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                "https://query1.finance.yahoo.com/v1/finance/search",
                params={"q": q, "newsCount": 0, "enableFuzzyQuery": False, "quotesCount": 10},
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=6,
            )
        r.raise_for_status()
        quotes = r.json().get("quotes", [])

        results = []
        for q_item in quotes:
            if q_item.get("quoteType") not in ALLOWED_TYPES:
                continue
            results.append({
                "ticker": q_item.get("symbol", ""),
                "name": q_item.get("shortname") or q_item.get("longname", ""),
                "type": q_item.get("quoteType", ""),
                "exchange": q_item.get("exchange", ""),
            })

        return results[:8]

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
