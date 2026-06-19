"""
Données boursières via yfinance (+ curl_cffi pour bypass Yahoo rate limit).

get_stock_data(ticker, period) :
  - Prix courant, previous_close, change_pct, market_cap, PE, 52W high/low
  - Série chart (date → close) selon période/intervalle
  - Labels 24H en HH:MM (bougies 5-min), autres périodes en YYYY-MM-DD

get_company_name(ticker) / bulk_fetch_company_names(tickers) :
  - Source unique ticker → nom de société (EU/ASIA + US)
  - yf.Search → _KNOWN_NAMES fallback (tickers défunts/fusionnés/délisted)
"""
import logging
import yfinance as yf
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_KNOWN_NAMES: dict[str, str] = {
    "AXA.PA":      "AXA SA",
    "SG.PA":       "Société Générale",
    "UG.PA":       "Peugeot SA",
    "FP.PA":       "TotalEnergies",
    "TCFP.PA":     "Thales SA",
    "WDI.DE":      "Wirecard AG",
    "1COV.DE":     "Covestro AG",
    "KNEBV.DE":    "KONE Oyj",
    "RB.L":        "Reckitt Benckiser",
    "AHT.L":       "Ashtead Group",
    "CRH.L":       "CRH plc",
    "MXCT.L":      "MaxCyte Inc.",
    "SKG.L":       "Smurfit Kappa",
    "URW.AS":      "Unibail-Rodamco-Westfield",
    "AEX.AS":      "AEX Index",
    "DSM.AS":      "DSM-Firmenich",
    "TKWY.AS":     "Just Eat Takeaway",
    "ROG.SW":      "Roche Holding AG",
    "CSGN.SW":     "Credit Suisse Group AG",
    "BALN.SW":     "Baloise Holding AG",
    "LUX.MI":      "Luxottica Group SpA",
    "ATL.MI":      "Atlantia SpA",
    "FCA.MI":      "Fiat Chrysler Automobiles",
    "AB-INBEV.BR": "Anheuser-Busch InBev",
    "GBL.BR":      "Groupe Bruxelles Lambert",
    "CRH.IR":      "CRH plc",
    "BHVN.IR":     "Biohaven Pharmaceutical",
    "AIB.IR":      "AIB Group plc",
}


def get_company_name(ticker: str) -> str:
    """Retourne le nom de la société pour un ticker EU/ASIA.

    Ordre : yf.Search (fonctionne même quand t.info retourne 404) → _KNOWN_NAMES fallback.
    Retourne "" si inconnu.
    """
    try:
        quotes = yf.Search(ticker, max_results=1).quotes or []
        if quotes:
            name = quotes[0].get("longname") or quotes[0].get("shortname") or ""
            if name and name.lower() != ticker.lower():
                return name
    except Exception:
        pass
    return _KNOWN_NAMES.get(ticker.upper(), "")


def bulk_fetch_company_names(tickers: list[str]) -> dict[str, str]:
    """Fetch les noms de société en parallèle pour les tickers EU/ASIA (avec '.').

    Remplace _bulk_fetch_company_names depuis breakers_articles_service.
    """
    eu_asia = [t for t in tickers if "." in t]
    if not eu_asia:
        return {}

    names: dict[str, str] = {}

    def _fetch_one(ticker: str) -> None:
        name = get_company_name(ticker)
        if name:
            names[ticker] = name

    with ThreadPoolExecutor(max_workers=8) as ex:
        list(ex.map(_fetch_one, eu_asia))

    logger.info(f"company names fetched: {len(names)}/{len(eu_asia)} tickers (dot-suffixed)")
    return names


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

    def _fmt(idx):
        if period == "1d":
            return idx.strftime("%H:%M")
        return str(idx.date())

    chart_data = [
        {"date": _fmt(idx), "close": round(row["Close"], 2), "volume": int(row["Volume"])}
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
        name = info.get("longName") or info.get("shortName") or _KNOWN_NAMES.get(ticker.upper(), ticker.upper())
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
