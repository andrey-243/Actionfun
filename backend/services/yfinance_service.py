import yfinance as yf
from datetime import datetime, timezone


PERIOD_INTERVAL = {
    "1d":  "5m",
    "5d":  "1h",
    "1mo": "1d",
    "6mo": "1d",
    "1y":  "1d",
    "5y":  "1wk",
    "max": "1mo",
}

def get_stock_data(ticker: str, period: str = "6mo") -> dict:
    t = yf.Ticker(ticker)
    fi = t.fast_info
    interval = PERIOD_INTERVAL.get(period, "1d")

    hist = t.history(period=period, interval=interval)
    if hist.empty:
        raise ValueError(f"Ticker {ticker} not found")

    chart_data = [
        {"date": str(idx.date()), "close": round(row["Close"], 2), "volume": int(row["Volume"])}
        for idx, row in hist.iterrows()
    ]

    current = fi.last_price
    previous = fi.previous_close

    name = ticker.upper()
    pe_ratio = None
    website = None
    try:
        from urllib.parse import urlparse
        info = t.info
        name = info.get("longName") or info.get("shortName", ticker.upper())
        pe_ratio = info.get("trailingPE")
        raw_url = info.get("website", "") or ""
        if raw_url:
            website = urlparse(raw_url).netloc.lstrip("www.") or None
    except Exception:
        pass

    return {
        "ticker": ticker.upper(),
        "name": name,
        "currency": getattr(fi, "currency", "USD"),
        "exchange": getattr(fi, "exchange", ""),
        "current_price": current,
        "previous_close": previous,
        "change_pct": _change_pct(current, previous),
        "market_cap": getattr(fi, "market_cap", None),
        "pe_ratio": pe_ratio,
        "week_52_high": getattr(fi, "year_high", None),
        "week_52_low": getattr(fi, "year_low", None),
        "chart": chart_data,
        "website": website,
    }


def _change_pct(current: float | None, previous: float | None) -> float | None:
    if current and previous and previous != 0:
        return round((current - previous) / previous * 100, 2)
    return None
