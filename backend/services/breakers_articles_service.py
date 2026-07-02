"""
Breaks — articles par période et condensation IA (weekly / monthly / yearly).

Fonctions principales :
  read_breaks_by_period(ticker, period)      → 1mo/6mo/1y : sections W1-W4 ou mois, résumé AI si dispo
  read_breaks_periods_available(ticker)      → 5Y : mois par mois, breaker si dispo sinon articles bruts

Crons de condensation (appelés depuis main.py) :
  gen_breaks_weekly()      → lundi 3h30 : weekly summaries → breaks_weekly
  gen_breaks_monthly()     → 1er du mois 4h : monthly depuis weeklies → breaks_monthly
  gen_breaks_yearly()      → 1er jan 5h : yearly depuis monthlies → breaks_yearly
  yahoo_articles_scrap()   → */3h : merge Yahoo → articles + trigger OW
  finnhub_articles_scrap() → */6h : merge Finnhub → articles + trigger OW

Source de vérité articles : articles (jamais article_archive).
"""
import httpx
import os
import re
import logging
import xml.etree.ElementTree as ET
from datetime import date, datetime, timezone, timedelta
from calendar import monthrange
from supabase import create_client
import hashlib
from services.yfinance_service import bulk_fetch_company_names as _bulk_fetch_company_names

RSS_UA = "Mozilla/5.0 (compatible; StockApp/1.0)"

logger = logging.getLogger(__name__)

ACTIVE_MONTH_TTL_HOURS = 12

_PAID_P2_SOURCES = {"newsapi", "marketaux", "av"}
_DONE_STATUSES = ("done_more_10", "done_less_10")


def _is_done(status: str | None) -> bool:
    return status in _DONE_STATUSES


def _is_p2_exhausted(ticker: str, year_month: str, supa) -> bool:
    row = supa.table("articles").select("tried_sources").eq("ticker", ticker).eq("year_month", year_month).maybe_single().execute()
    if not row or not row.data:
        return False
    sources = set(row.data.get("tried_sources") or [])
    return _PAID_P2_SOURCES.issubset(sources)

# Densité articles bruts (fallback quand pas encore de summaries)
DENSITY_CONFIG = {
    "1mo": {"slot_days": 7,  "max_per_slot": 10},  # 10/semaine
    "6mo": {"slot_days": 30, "max_per_slot": 5},   # 5/mois
    "1y":  {"slot_days": 30, "max_per_slot": 10},  # 10/mois
    "all": {"slot_days": 30, "max_per_slot": 3},   # 3/mois pour ALL
}


def _get_supabase():
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])


# ─── Helpers dates ────────────────────────────────────────────────────────────

def _year_months_for_period(months_back: int) -> list[str]:
    """year_months couvrant les N derniers mois (+1 pour chevauchement)."""
    today = date.today()
    result = []
    for i in range(months_back + 1):
        month = today.month - i
        year = today.year
        while month <= 0:
            month += 12
            year -= 1
        result.append(f"{year:04d}-{month:02d}")
    return result


def _is_active_month(year_month: str) -> bool:
    today = date.today()
    return year_month == f"{today.year:04d}-{today.month:02d}"


def _condensation_cutoff_ym() -> str:
    """year_month limite : 12 mois en arrière (max Finnhub free)."""
    today = date.today()
    month = today.month - 12
    year = today.year
    while month <= 0:
        month += 12
        year -= 1
    return f"{year:04d}-{month:02d}"


def _week_num(day: int) -> int:
    """Retourne le numéro de semaine fixe intra-mois (1-4)."""
    if day <= 7:
        return 1
    if day <= 14:
        return 2
    if day <= 21:
        return 3
    return 4


def _week_label(year_month: str, week_num: int) -> str:
    year, month = int(year_month[:4]), int(year_month[5:7])
    month_names = ["", "Jan", "Fév", "Mar", "Avr", "Mai", "Jun", "Jul", "Aoû", "Sep", "Oct", "Nov", "Déc"]
    ranges = {1: "1–7", 2: "8–14", 3: "15–21", 4: f"22–{monthrange(year, month)[1]}"}
    return f"W{week_num} {month_names[month]} {year} ({ranges[week_num]})"


def _month_label(year_month: str) -> str:
    year, month = int(year_month[:4]), int(year_month[5:7])
    month_names = ["", "Janvier", "Février", "Mars", "Avril", "Mai", "Juin",
                   "Juillet", "Août", "Septembre", "Octobre", "Novembre", "Décembre"]
    return f"{month_names[month]} {year}"


# ─── Normalisation articles ────────────────────────────────────────────────────

def _article_text(a: dict) -> str:
    """Texte enrichi pour les prompts LLM : dumb > summary > headline seul."""
    headline = a.get("headline", "") or a.get("title", "")
    dumb = (a.get("dumb_en") or a.get("dumb_fr") or "").strip()
    if dumb:
        return f"- {headline}\n  {dumb}"
    summary = (a.get("summary") or "").strip()
    if summary:
        return f"- {headline}\n  {summary}"
    return f"- {headline}"


_GENERIC_CORP_WORDS = {
    "corporation", "incorporated", "inc", "corp", "company", "co",
    "limited", "ltd", "group", "holdings", "holding", "plc", "sa",
    "ag", "nv", "bv", "gmbh", "se", "ab", "asa", "spa", "the",
    "and", "of", "international", "global", "technologies", "technology",
}

def _company_keywords(company_name: str) -> list[str]:
    """Extrait les mots significatifs d'un nom de société."""
    words = re.sub(r"[^\w\s]", " ", company_name.lower()).split()
    return [w for w in words if w not in _GENERIC_CORP_WORDS and len(w) > 2]


def _count_related(articles: list[dict]) -> int:
    return sum(1 for a in articles if a.get("is_related") is True)


def _is_ticker_related(a: dict, ticker: str, company_name: str = "") -> bool:
    """is_related=False → exclu. is_related=True → inclus. null → heuristique texte (setter in-place)."""
    is_rel = a.get("is_related")
    if is_rel is False:
        return False
    if is_rel is True:
        return True
    # is_related absent/null : heuristique texte — exclut les dumb "SKIP" (fix bug ligne 133)
    dumb_en = (a.get("dumb_en") or "").strip().upper()
    dumb_fr = (a.get("dumb_fr") or "").strip().upper()
    if dumb_en == "SKIP" or dumb_fr == "SKIP":
        a["is_related"] = False
        return False
    if dumb_en or dumb_fr:
        a["is_related"] = True
        return True
    text = " ".join(filter(None, [
        a.get("headline") or a.get("title") or "",
        a.get("summary") or "",
    ])).lower()
    keywords = [ticker.lower()] + (_company_keywords(company_name) if company_name else [])
    # Pour les tickers avec suffixe exchange (ex: "7203.T", "005930.KS"),
    # ajouter la base numérique — les headlines utilisent le format "TSE:7203" pas "7203.T"
    base = ticker.split(".")[0].lower()
    if base != ticker.lower() and len(base) >= 3:
        keywords.append(base)
    result = any(kw in text for kw in keywords)
    if result:
        a["is_related"] = True
    return result


def _normalize_finnhub(a: dict) -> dict:
    ts = a.get("datetime", 0)
    pub_date = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat() if ts else ""
    uid = hashlib.md5(f"{a.get('url','')}{ts}".encode()).hexdigest()[:12]
    return {
        "id":            uid,
        "type":          "STORY",
        "title":         a.get("headline", ""),
        "summary":       a.get("summary", ""),
        "source":        a.get("source", ""),
        "url":           a.get("url", ""),
        "thumbnail":     a.get("image", ""),
        "pub_date":      pub_date,
        "brutalised":    None,
        "extra_sources": a.get("extra_sources", []),
    }


# ─── Région / RSS / Company name helpers ──────────────────────────────────────

_EU_SUFFIXES = {
    ".PA", ".AS", ".L", ".MI", ".DE", ".MC", ".SW", ".BR", ".LS",
    ".HE", ".CO", ".ST", ".OL", ".VI", ".PR", ".WA", ".AT", ".BD",
}
_ASIA_SUFFIXES = {
    ".T", ".HK", ".KS", ".SS", ".AX", ".NS", ".BO", ".TW",
    ".SZ", ".SI", ".KL", ".JK", ".BK",
}


def _detect_region(ticker: str) -> str:
    """Retourne 'US', 'EU' ou 'ASIA' selon le suffixe exchange du ticker."""
    if "." not in ticker:
        return "US"
    suffix = "." + ticker.rsplit(".", 1)[1].upper()
    if suffix in _EU_SUFFIXES:
        return "EU"
    if suffix in _ASIA_SUFFIXES:
        return "ASIA"
    return "US"


def _build_rss_query(ticker: str, company_name: str = "") -> str:
    """Construit la query Google RSS optimisée par région.

    US   : keywords[:2] du nom + 'stock'
    EU   : company name obligatoire (tickers lisibles mais ambigus sans nom)
    ASIA : company name obligatoire (tickers numériques 7203.T, 005930.KS)
           fallback : base ticker sans suffixe exchange
    """
    region = _detect_region(ticker)
    if region == "US":
        kw = _company_keywords(company_name)[:2] if company_name else []
        return (" ".join(kw) + " stock") if kw else (ticker + " stock")
    # EU / ASIA : company name brut prioritaire (les keywords filtrent trop pour des noms courts)
    if company_name:
        # Prendre les 3 premiers mots du nom brut (pas filtrés) pour préserver "Holding", "Motors"…
        raw_words = company_name.strip().split()[:3]
        if raw_words:
            return " ".join(raw_words) + " stock"
    # Fallback : base ticker sans suffixe
    base = ticker.rsplit(".", 1)[0]
    return base + " stock"


def _is_month_fresh(ticker: str, year_month: str, supa,
                    min_count: int = 25, max_days: int = 3) -> bool:
    """True si le mois a déjà ≥ min_count articles scrapés depuis < max_days jours."""
    row = supa.table("articles") \
        .select("articles, cached_at") \
        .eq("ticker", ticker) \
        .eq("year_month", year_month) \
        .limit(1) \
        .execute()
    if not row or not row.data:
        return False
    data = row.data[0]
    if len(data.get("articles") or []) < min_count:
        return False
    cached_at = data.get("cached_at")
    if not cached_at:
        return False
    try:
        dt = datetime.fromisoformat(cached_at.replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - dt).days < max_days
    except Exception:
        return False


# ─── Normalisation — nouvelles sources ────────────────────────────────────────

def _normalize_google_rss(a: dict) -> dict:
    """Normalise un item Google RSS vers le format interne articles."""
    pub = a.get("pub", "")
    try:
        dt = datetime.strptime(pub, "%a, %d %b %Y %H:%M:%S %Z")
        ts = int(dt.replace(tzinfo=timezone.utc).timestamp())
    except Exception:
        ts = 0
    return {
        "headline": a.get("title", ""),
        "source":   a.get("source", "google_rss"),
        "url":      a.get("url", ""),
        "datetime": ts,
        "summary":  "",
        "image":    "",
    }


def _normalize_marketaux(a: dict) -> dict:
    """Normalise un item Marketaux API vers le format interne articles."""
    pub = a.get("published_at", "")
    try:
        ts = int(datetime.fromisoformat(pub.replace("Z", "+00:00")).timestamp()) if pub else 0
    except Exception:
        ts = 0
    src = a.get("source", {})
    source_name = src.get("name", "") if isinstance(src, dict) else str(src)
    return {
        "headline": a.get("title", ""),
        "source":   source_name or "marketaux",
        "url":      a.get("url", ""),
        "datetime": ts,
        "summary":  a.get("description", "") or "",
        "image":    a.get("image_url", "") or "",
    }


def _normalize_newsapi(a: dict) -> dict:
    """Normalise un item NewsAPI vers le format interne articles."""
    pub = a.get("publishedAt", "")
    try:
        ts = int(datetime.fromisoformat(pub.replace("Z", "+00:00")).timestamp()) if pub else 0
    except Exception:
        ts = 0
    src = a.get("source", {})
    source_name = src.get("name", "") if isinstance(src, dict) else str(src)
    return {
        "headline": a.get("title", ""),
        "source":   source_name or "newsapi",
        "url":      a.get("url", ""),
        "datetime": ts,
        "summary":  a.get("description", "") or "",
        "image":    a.get("urlToImage", "") or "",
    }


def _article_url_hash(url: str) -> str:
    import hashlib as _hl
    return _hl.md5(url.encode()).hexdigest()[:12]


def _upsert_articles_merge(ticker: str, year_month: str, new_articles: list[dict],
                            source_tag: str, supa) -> tuple[int, str]:
    """Merge new_articles avec l'existant, dedup par headline, upsert.

    Retourne (nb_ajoutés, effective_source). effective_source vaut "" si rien ajouté.
    """
    # Blocklist check : exclure les articles déjà marqués hors cible pour ce ticker
    try:
        bl = supa.table("articles_blocklist").select("url_hash").eq("ticker", ticker).execute()
        blocked = {r["url_hash"] for r in (bl.data or [])}
    except Exception:
        blocked = set()
    if blocked:
        new_articles = [a for a in new_articles if _article_url_hash(a.get("url", "")) not in blocked]

    row = supa.table("articles") \
        .select("articles, sources") \
        .eq("ticker", ticker) \
        .eq("year_month", year_month) \
        .limit(1) \
        .execute()
    row_data = (row.data[0] if row and row.data else {})
    existing = row_data.get("articles") or []
    existing_sources = row_data.get("sources") or []

    merged = _dedup_by_headline(new_articles + existing)
    added = len(merged) - len(existing)
    if added <= 0:
        return 0, ""

    new_sources = existing_sources if source_tag in existing_sources else existing_sources + [source_tag]
    supa.table("articles").upsert({
        "ticker":     ticker,
        "year_month": year_month,
        "articles":   merged,
        "sources":    new_sources,
        "cached_at":  datetime.now(timezone.utc).isoformat(),
    }).execute()
    logger.info(f"  {source_tag} {ticker} {year_month}: +{added} articles ({len(merged)} total, sources={new_sources})")
    return added, source_tag


# ─── Nouvelles sources fetch ──────────────────────────────────────────────────

def _fetch_rss_month(query: str, year_month: str) -> list[dict]:
    """Fetch Google News RSS pour un mois donné. Retourne les items bruts."""
    year, month = int(year_month[:4]), int(year_month[5:7])
    last_day = monthrange(year, month)[1]
    after  = f"{year}-{month:02d}-01"
    before = f"{year}-{month:02d}-{last_day:02d}"
    url = (
        "https://news.google.com/rss/search"
        f"?q={query}+after:{after}+before:{before}&num=100&hl=en&gl=US&ceid=US:en"
    )
    try:
        r = httpx.get(url, headers={"User-Agent": RSS_UA}, timeout=15, follow_redirects=True)
        r.raise_for_status()
    except Exception as e:
        logger.warning(f"    RSS {year_month}: {e}")
        return []
    root = ET.fromstring(r.text)
    result = []
    for item in root.findall(".//item"):
        title  = item.findtext("title") or ""
        link   = item.findtext("link") or ""
        pub    = item.findtext("pubDate") or ""
        src_el = item.find("source")
        source = src_el.text if src_el is not None else "google_rss"
        if title and link:
            result.append({"title": title, "url": link, "pub": pub, "source": source})
    return result


def _fetch_newsapi_month(query: str, year_month: str, api_key: str) -> list[dict]:
    """Fetch NewsAPI /v2/everything pour un mois (plan Dev : 1 mois d'historique)."""
    year, month = int(year_month[:4]), int(year_month[5:7])
    last_day = monthrange(year, month)[1]
    params = {
        "q":        query,
        "from":     f"{year}-{month:02d}-01",
        "to":       f"{year}-{month:02d}-{last_day:02d}",
        "sortBy":   "publishedAt",
        "pageSize": 100,
        "language": "en",
        "apiKey":   api_key,
    }
    try:
        r = httpx.get("https://newsapi.org/v2/everything", params=params, timeout=15)
        if r.status_code == 429:
            logger.warning(f"    NewsAPI {year_month}: 429 rate limit")
            return None
        r.raise_for_status()
        data = r.json()
        if data.get("status") != "ok":
            logger.warning(f"    NewsAPI {year_month}: {data.get('message', 'error')}")
            return []
        return data.get("articles") or []
    except Exception as e:
        logger.warning(f"    NewsAPI {year_month}: {e}")
        return []


def _fetch_marketaux_month(query: str, year_month: str, api_token: str) -> list[dict]:
    """Fetch Marketaux (max 3 pages × 50 = 150 articles, plan Basic : 3 mois d'historique)."""
    year, month = int(year_month[:4]), int(year_month[5:7])
    last_day = monthrange(year, month)[1]
    params = {
        "api_token":        api_token,
        "search":           query,
        "published_after":  f"{year}-{month:02d}-01T00:00:00",
        "published_before": f"{year}-{month:02d}-{last_day:02d}T23:59:59",
        "language":         "en",
        "limit":            50,
        "page":             1,
    }
    all_articles: list[dict] = []
    for page in range(1, 4):
        params["page"] = page
        try:
            r = httpx.get("https://api.marketaux.com/v1/news/all", params=params, timeout=15)
            if r.status_code == 429:
                logger.warning(f"    Marketaux {year_month}: 429 rate limit")
                return None
            r.raise_for_status()
            page_arts = r.json().get("data") or []
            all_articles.extend(page_arts)
            if len(page_arts) < 50:
                break
        except Exception as e:
            logger.warning(f"    Marketaux {year_month} page{page}: {e}")
            break
    return all_articles


def _normalize_av(a: dict) -> dict:
    """Normalise un item Alpha Vantage NEWS_SENTIMENT. time_published = '20250601T120000'."""
    raw_time = a.get("time_published", "")
    ts = 0
    if raw_time and len(raw_time) >= 15:
        try:
            dt = datetime.strptime(raw_time[:15], "%Y%m%dT%H%M%S")
            ts = int(dt.replace(tzinfo=timezone.utc).timestamp())
        except Exception:
            pass
    return {
        "headline": a.get("title", ""),
        "source":   a.get("source", "alphavantage"),
        "url":      a.get("url", ""),
        "datetime": ts,
        "summary":  a.get("summary", "") or "",
        "image":    a.get("banner_image", "") or "",
    }


def _fetch_av_month(ticker: str, year_month: str, api_key: str) -> list[dict]:
    """Fetch Alpha Vantage NEWS_SENTIMENT pour un mois (format time_published non-standard)."""
    year, month = int(year_month[:4]), int(year_month[5:7])
    last_day = monthrange(year, month)[1]
    params = {
        "function":  "NEWS_SENTIMENT",
        "tickers":   ticker,
        "time_from": f"{year}{month:02d}01T0000",
        "time_to":   f"{year}{month:02d}{last_day:02d}T2359",
        "limit":     200,
        "apikey":    api_key,
    }
    try:
        r = httpx.get("https://www.alphavantage.co/query", params=params, timeout=20)
        if r.status_code == 429:
            logger.warning(f"    AV {ticker} {year_month}: 429 rate limit")
            return None
        r.raise_for_status()
        data = r.json()
        if "Information" in data:
            logger.warning(f"    AV rate limit: {data['Information'][:80]}")
            return None
        return data.get("feed") or []
    except Exception as e:
        logger.warning(f"    AV {ticker} {year_month}: {e}")
        return []


def scrap_ext_sources(ticker: str, year_month: str, supa, company_name: str = "") -> int:
    """Waterfall RSS → NewsAPI → Marketaux → AV pour un (ticker, year_month).

    S'arrête dès que le mois est fresh (≥25 articles, cached_at <3j).
    Retourne le nombre total d'articles ajoutés.
    """
    if _is_month_fresh(ticker, year_month, supa):
        return 0

    query = _build_rss_query(ticker, company_name)
    total_added = 0

    # 1. Google RSS (gratuit, toujours)
    raw = _fetch_rss_month(query, year_month)
    if raw:
        normalized = [_normalize_google_rss(a) for a in raw]
        n, _ = _upsert_articles_merge(ticker, year_month, normalized, "google_rss", supa)
        total_added += n

    if _is_month_fresh(ticker, year_month, supa):
        return total_added

    # 2. NewsAPI (si clé dispo — plan Dev : mois courant seulement)
    newsapi_key = os.environ.get("NEWSAPI_API_KEY", "")
    if newsapi_key:
        raw = _fetch_newsapi_month(query, year_month, newsapi_key)
        if raw:
            normalized = [_normalize_newsapi(a) for a in raw if a.get("title") and a.get("url")]
            n, _ = _upsert_articles_merge(ticker, year_month, normalized, "newsapi", supa)
            total_added += n

    if _is_month_fresh(ticker, year_month, supa):
        return total_added

    # 3. Marketaux (si clé dispo — plan Basic : 3 mois d'historique)
    mx_token = os.environ.get("MARKETAUX_API_TOKEN", "")
    if mx_token:
        raw = _fetch_marketaux_month(query, year_month, mx_token)
        if raw:
            normalized = [_normalize_marketaux(a) for a in raw if a.get("title") and a.get("url")]
            n, _ = _upsert_articles_merge(ticker, year_month, normalized, "marketaux", supa)
            total_added += n

    if _is_month_fresh(ticker, year_month, supa):
        return total_added

    # 4. Alpha Vantage (si clé dispo — last resort, 5 req/min)
    av_key = os.environ.get("ALPHAVANTAGE_API_KEY", "")
    if av_key:
        raw = _fetch_av_month(ticker, year_month, av_key)
        if raw:
            normalized = [_normalize_av(a) for a in raw if a.get("title") and a.get("url")]
            n, _ = _upsert_articles_merge(ticker, year_month, normalized, "alphavantage", supa)
            total_added += n

    return total_added


# ─── Finnhub fetch & cache ────────────────────────────────────────────────────

def _fetch_finnhub_month(ticker: str, year_month: str) -> list[dict] | None:
    year, month = int(year_month[:4]), int(year_month[5:7])
    from_date = date(year, month, 1)
    last_day = monthrange(year, month)[1]
    to_date = date(year, month, last_day)

    r = httpx.get(
        "https://finnhub.io/api/v1/company-news",
        params={
            "symbol": ticker,
            "from": from_date.isoformat(),
            "to": to_date.isoformat(),
            "token": os.environ["FINNHUB_API_KEY"],
        },
        timeout=15,
    )
    if r.status_code == 429:
        logger.warning(f"    Finnhub {ticker} {year_month}: 429 rate limit")
        return None
    r.raise_for_status()
    raw = r.json()
    return [
        {
            "headline": a["headline"],
            "source":   a["source"],
            "url":      a["url"],
            "datetime": a["datetime"],
            "summary":  a.get("summary", ""),
            "image":    a.get("image", ""),
        }
        for a in raw
        if a.get("headline")
    ]


def _get_or_fetch_month(ticker: str, year_month: str, supa) -> tuple[list[dict], str]:
    """
    Retourne (articles_raw, sources_str) depuis articles ou Finnhub.
    Merge les nouveaux articles Finnhub avec l'existant (Yahoo inclus), pas d'écrasement.
    """
    row = supa.table("articles") \
        .select("articles, sources, cached_at") \
        .eq("ticker", ticker) \
        .eq("year_month", year_month) \
        .maybe_single() \
        .execute()

    cached = row.data if row else None

    if cached:
        sources_str = "+".join(cached.get("sources") or [])
        if not _is_active_month(year_month):
            return cached["articles"], sources_str
        cached_at = datetime.fromisoformat(cached["cached_at"].replace("Z", "+00:00"))
        if datetime.now(timezone.utc) - cached_at < timedelta(hours=ACTIVE_MONTH_TTL_HOURS):
            return cached["articles"], sources_str

    existing = (cached or {}).get("articles") or []
    raw = _fetch_finnhub_month(ticker, year_month)

    if not raw:
        if cached:
            supa.table("articles").update({
                "cached_at": datetime.now(timezone.utc).isoformat()
            }).eq("ticker", ticker).eq("year_month", year_month).execute()
            return existing, "+".join(cached.get("sources") or [])
        return [], ""

    # Merge Finnhub + existant (Yahoo ou Finnhub précédent), dedup par headline
    merged = _dedup_by_headline(raw + existing)
    existing_sources = (cached or {}).get("sources") or []
    new_sources = existing_sources if "finnhub" in existing_sources else existing_sources + ["finnhub"]

    supa.table("articles").upsert({
        "ticker":     ticker,
        "year_month": year_month,
        "articles":   merged,
        "sources":    new_sources,
        "cached_at":  datetime.now(timezone.utc).isoformat(),
    }).execute()
    sources_str = "+".join(new_sources)
    logger.info(f"Finnhub merged {len(raw)} new + {len(existing)} existing → {len(merged)} for {ticker} {year_month} (sources={sources_str})")
    return merged, sources_str


def _persist_is_related_writeback(ticker: str, year_month: str, articles: list[dict], supa) -> None:
    """Persiste les champs is_related settés in-place par _is_ticker_related si au moins un est présent."""
    if not any("is_related" in a for a in articles):
        return
    try:
        supa.table("articles").update({"articles": articles}) \
            .eq("ticker", ticker).eq("year_month", year_month).execute()
    except Exception as e:
        logger.warning(f"is_related writeback {ticker} {year_month}: {e}")


# ─── Density ──────────────────────────────────────────────────────────────────

def _apply_density(articles: list[dict], slot_days: int, max_per_slot: int) -> list[dict]:
    if not articles:
        return []
    slot_sec = slot_days * 86_400
    slots: dict[int, list[dict]] = {}
    for a in articles:
        ts = a.get("datetime", 0)
        key = ts // slot_sec
        slots.setdefault(key, []).append(a)

    result = []
    for key in sorted(slots.keys(), reverse=True):
        bucket = sorted(slots[key], key=lambda x: len(x.get("summary", "")), reverse=True)
        result.extend(bucket[:max_per_slot])
    return result


# ─── API principale ───────────────────────────────────────────────────────────

def read_breaks_by_period(ticker: str, period: str, company_name: str = "") -> dict:
    """
    1mo : sections par semaine (W1-W4) avec articles groupés par date.
    6mo / 1y : sections par mois avec articles groupés par year_month.
    Résumé AI affiché si disponible en DB, sinon articles bruts seulement.
    """
    ticker = ticker.upper()
    supa = _get_supabase()

    months_back = {"1mo": 1, "6mo": 6, "1y": 12}[period]
    year_months = _year_months_for_period(months_back)

    if period == "1mo":
        return _get_monthly_with_weeks(ticker, year_months, supa, company_name)

    # 6mo / 1y : récupérer month summaries AI (optionnel)
    month_rows = supa.table("breaks_monthly") \
        .select("year_month, bm_en, bm_fr, article_count") \
        .eq("ticker", ticker) \
        .in_("year_month", year_months) \
        .execute()
    month_ai = {r["year_month"]: r for r in (month_rows.data or [])}

    cutoff_ts = (datetime.now(timezone.utc) - timedelta(days=months_back * 30)).timestamp()

    # Charger les articles bruts par mois et les grouper
    MAX_PER_MONTH = {"6mo": 10, "1y": 8}.get(period, 10)
    summaries = []

    for ym in sorted(year_months, reverse=True):
        ai = month_ai.get(ym)
        try:
            raw, _ = _get_or_fetch_month(ticker, ym, supa)
        except Exception as e:
            logger.error(f"Failed to fetch {ticker} {ym}: {e}")
            raw = []

        # Filtrer : dans la fenêtre globale ET dans les bornes réelles du mois + dedup
        ym_start, ym_end = _ym_bounds(ym)
        raw = _dedup_by_headline(raw)
        filtered = [a for a in raw if a.get("datetime", 0) >= cutoff_ts and ym_start <= a.get("datetime", 0) <= ym_end and _is_ticker_related(a, ticker, company_name)]
        _persist_is_related_writeback(ticker, ym, raw, supa)
        filtered.sort(key=lambda a: a.get("datetime", 0), reverse=True)
        top = [_normalize_finnhub(a) for a in filtered]

        if not top and not ai:
            continue

        summaries.append({
            "type":          "month",
            "year_month":    ym,
            "label":         _month_label(ym),
            "ai_summary":    ai.get(_summary_col("breaks_monthly")) if ai else None,
            "article_count": len(filtered),
            "top_articles":  top,
        })

    return {
        "ticker":    ticker,
        "period":    period,
        "articles":  [],
        "summaries": summaries,
        "condensed": [],
    }


def _dedup_by_headline(articles: list[dict]) -> list[dict]:
    """Déduplique par headline. Garde le meilleur article, fusionne les sources alternatives."""
    seen: dict[str, dict] = {}
    for a in articles:
        key = a.get("headline", "").strip().lower()[:80]
        if not key:
            continue
        if key not in seen:
            seen[key] = {**a, "extra_sources": a.get("extra_sources", [])}
        else:
            existing = seen[key]
            is_proxy = "finnhub.io/api/news" in (existing.get("url") or "")
            new_direct = "finnhub.io/api/news" not in (a.get("url") or "")
            better_summary = len(a.get("summary") or "") > len(existing.get("summary") or "")
            if (is_proxy and new_direct) or (not is_proxy and better_summary):
                extra = existing.get("extra_sources", []) + [{"name": existing.get("source", ""), "url": existing.get("url", "")}]
                seen[key] = {**a, "extra_sources": extra}
            else:
                seen[key]["extra_sources"].append({"name": a.get("source", ""), "url": a.get("url", "")})
    return list(seen.values())


def _ym_bounds(year_month: str) -> tuple[float, float]:
    """Retourne (ts_start, ts_end) pour un year_month donné."""
    year, month = int(year_month[:4]), int(year_month[5:7])
    start = datetime(year, month, 1, tzinfo=timezone.utc)
    last_day = monthrange(year, month)[1]
    end = datetime(year, month, last_day, 23, 59, 59, tzinfo=timezone.utc)
    return start.timestamp(), end.timestamp()


def _get_monthly_with_weeks(ticker: str, year_months: list[str], supa, company_name: str = "") -> dict:
    """Pour 1mo : sections par semaine si distribution ok, sinon section merged avec warning."""
    week_rows = supa.table("breaks_weekly") \
        .select("year_month, week_num, article_count") \
        .eq("ticker", ticker) \
        .in_("year_month", year_months) \
        .execute()
    week_ai: dict[tuple, dict] = {(r["year_month"], r["week_num"]): r for r in (week_rows.data or [])}

    cutoff_ts = (datetime.now(timezone.utc) - timedelta(days=35)).timestamp()
    summaries = []

    for ym in sorted(year_months, reverse=True):
        try:
            raw, _ = _get_or_fetch_month(ticker, ym, supa)
        except Exception as e:
            logger.error(f"Failed to fetch {ticker} {ym}: {e}")
            raw = []

        # Filtrer : dans la fenêtre 35j ET dans les bornes réelles du year_month
        ym_start, ym_end = _ym_bounds(ym)
        raw = _dedup_by_headline(raw)

        # Grouper par semaine selon le jour réel de l'article
        by_week: dict[int, list[dict]] = {1: [], 2: [], 3: [], 4: []}
        for a in raw:
            ts = a.get("datetime", 0)
            if ts < cutoff_ts or ts < ym_start or ts > ym_end:
                continue
            day = datetime.fromtimestamp(ts, tz=timezone.utc).day
            by_week[_week_num(day)].append(a)

        total = sum(len(v) for v in by_week.values())
        weeks_with_articles = [w for w, arts in by_week.items() if len(arts) > 0]
        max_week_count = max((len(v) for v in by_week.values()), default=0)

        # Skewed : une seule semaine concentre >80% ET au moins 2 semaines vides
        is_skewed = (
            total > 0
            and len(weeks_with_articles) <= 1
            or (total > 0 and max_week_count / total > 0.80 and len(weeks_with_articles) <= 2)
        )

        if is_skewed:
            all_articles = []
            for w in range(1, 5):
                all_articles.extend(by_week[w])
            all_articles.sort(key=lambda a: a.get("datetime", 0), reverse=True)
            top = [_normalize_finnhub(a) for a in all_articles if _is_ticker_related(a, ticker, company_name)]
            _persist_is_related_writeback(ticker, ym, raw, supa)
            if top:
                first_w = min(weeks_with_articles) if weeks_with_articles else 1
                last_w = max(weeks_with_articles) if weeks_with_articles else 4
                summaries.append({
                    "type":          "week",
                    "year_month":    ym,
                    "week_num":      None,
                    "label":         _month_label(ym),
                    "skewed":        True,
                    "skewed_range":  f"W{first_w}→W{last_w}",
                    "ai_summary":    None,
                    "article_count": total,
                    "top_articles":  top,
                })
        else:
            for wnum in range(4, 0, -1):
                articles = by_week[wnum]
                ai = week_ai.get((ym, wnum))
                if not articles and not ai:
                    continue
                articles.sort(key=lambda a: a.get("datetime", 0), reverse=True)
                top = [_normalize_finnhub(a) for a in articles if _is_ticker_related(a, ticker, company_name)]
                summaries.append({
                    "type":          "week",
                    "year_month":    ym,
                    "week_num":      wnum,
                    "label":         _week_label(ym, wnum),
                    "skewed":        False,
                    "ai_summary":    ai.get(_summary_col("breaks_weekly")) if ai else None,
                    "article_count": len(articles),
                    "top_articles":  top,
                })
            _persist_is_related_writeback(ticker, ym, raw, supa)

    return {
        "ticker":    ticker,
        "period":    "1mo",
        "articles":  [],
        "summaries": summaries,
        "condensed": [],
    }


def _n_months_ago_ym(n: int) -> str:
    today = date.today()
    month = today.month - n
    year = today.year
    while month <= 0:
        month += 12
        year -= 1
    return f"{year:04d}-{month:02d}"


def read_breaks_periods_available(ticker: str) -> dict:
    """
    5Y : Y breakers + M breakers uniquement — pas d'articles bruts.
    Header année avec yearly summary, puis mois avec monthly summary.
    Mois sans M breaker : ignorés (pas de fallback articles).
    """
    ticker = ticker.upper()
    supa = _get_supabase()

    month_rows = supa.table("breaks_monthly") \
        .select("year_month, bm_en, bm_fr, article_count") \
        .eq("ticker", ticker) \
        .order("year_month", desc=True) \
        .execute()
    month_summary_map = {r["year_month"]: r for r in (month_rows.data or [])}

    year_rows = supa.table("breaks_yearly") \
        .select("year, by_en, by_fr") \
        .eq("ticker", ticker) \
        .order("year", desc=True) \
        .execute()
    year_summary_map = {r["year"]: r for r in (year_rows.data or [])}

    five_years_ago = _n_months_ago_ym(60)
    all_ym = sorted(
        [ym for ym in month_summary_map.keys() if ym >= five_years_ago],
        reverse=True,
    )

    summaries = []
    seen_years: set[str] = set()

    for ym in all_ym:
        year = ym[:4]
        if year not in seen_years:
            seen_years.add(year)
            yr = year_summary_map.get(year)
            summaries.append({
                "type":         "year",
                "year":         year,
                "label":        year,
                "ai_summary":   yr.get(_summary_col("breaks_yearly")) if yr else None,
                "top_articles": [],
            })

        r = month_summary_map[ym]
        summaries.append({
            "type":          "month",
            "year_month":    ym,
            "label":         _month_label(ym),
            "ai_summary":    r.get(_summary_col("breaks_monthly")),
            "article_count": r.get("article_count", 0),
            "top_articles":  [],
        })

    return {
        "ticker":    ticker,
        "period":    "all",
        "articles":  [],
        "summaries": summaries,
        "condensed": [],
    }


# ─── Crons de condensation ────────────────────────────────────────────────────

WEEK_CONDENSE_PROMPT = """Tu es un analyste financier senior. Voici des articles sur {ticker} pour la semaine {label}.

Articles :
{articles_text}

Rédige en français un résumé analytique de 4-6 phrases couvrant : les événements majeurs (résultats, annonces, deals), les chiffres clés, l'impact sur l'action et l'évolution du sentiment.
Réponds UNIQUEMENT avec le texte du résumé en français, sans titre ni formatage."""

WEEK_CONDENSE_PROMPT_EN = """You are a senior financial analyst. Here are articles about {ticker} for the week {label}.

Articles:
{articles_text}

Write a 4-6 sentence analytical summary covering: major events (earnings, announcements, deals), key figures, impact on the stock and sentiment evolution.
Reply ONLY with the summary text in English, no title or formatting."""

MONTH_CONDENSE_PROMPT = """Tu es un analyste financier senior. Voici l'actualité sur {ticker} pour {label}.

{weeks_text}

Synthétise en français en 6-8 phrases : événements majeurs du mois, chiffres clés, contexte macro/sectoriel pertinent, catalyseurs et risques identifiés, évolution du sentiment investisseurs.
Réponds UNIQUEMENT avec le texte du résumé en français, sans titre ni formatage."""

MONTH_CONDENSE_PROMPT_EN = """You are a senior financial analyst. Here is the news about {ticker} for {label}.

{weeks_text}

Synthesize in 6-8 sentences in English: major monthly events, key figures, relevant macro/sector context, identified catalysts and risks, investor sentiment evolution.
Reply ONLY with the summary text in English, no title or formatting."""

YEAR_CONDENSE_PROMPT = """Tu es un analyste financier senior. Voici les résumés mensuels sur {ticker} pour l'année {year}.

{months_text}

Synthétise l'année en français en 8-10 phrases : temps forts, tendances de fond, pivots stratégiques majeurs, performance relative, sentiment global et perspectives.
Réponds UNIQUEMENT avec le texte du résumé en français, sans titre ni formatage."""

YEAR_CONDENSE_PROMPT_EN = """You are a senior financial analyst. Here are the monthly summaries for {ticker} for the year {year}.

{months_text}

Synthesize the year in 8-10 sentences in English: highlights, underlying trends, major strategic pivots, relative performance, overall sentiment and outlook.
Reply ONLY with the summary text in English, no title or formatting."""


def _lang() -> str:
    import os
    return os.environ.get("LLM_LANG", "fr")


def _pick(fr_prompt: str, en_prompt: str) -> str:
    return en_prompt if _lang() == "en" else fr_prompt


def _llm_source_col() -> str:
    return "llm_source_en" if _lang() == "en" else "llm_source_fr"


def _summary_col(table: str = "breaks_weekly") -> str:
    lang = _lang()
    cols = {
        "breaks_weekly":  ("bw_en", "bw_fr"),
        "breaks_monthly": ("bm_en", "bm_fr"),
        "breaks_yearly":  ("by_en", "by_fr"),
    }
    en_col, fr_col = cols.get(table, ("bw_en", "bw_fr"))
    return en_col if lang == "en" else fr_col


def _llm_call(prompt: str, max_tokens: int = 400, allow_groq: bool = True, item_id: str = "", cascade: str = "default") -> str:
    from services.llm_service import llm_call
    resp = llm_call(
        [{"role": "user", "content": prompt}],
        groq_model="llama-3.3-70b-versatile",
        max_tokens=max_tokens,
        timeout=45,
        allow_groq=allow_groq,
        caller="Breaks",
        item_id=item_id,
        cascade=cascade,
    )
    _llm_call._last_source = resp.get("_llm_source")
    model = resp.get("model", "")
    src = _llm_call._last_source or "?"
    label = f"[{item_id}] " if item_id else ""
    logger.info(f"    LLM {label}{cascade} → {src}" + (f" ({model})" if model else ""))
    return resp["choices"][0]["message"]["content"].strip()

_llm_call._last_source = None


def gen_breaks_weekly() -> dict:
    """
    Lundi 3h : condense les semaines terminées de la semaine passée.
    Seuil : ticker pinné par ≥ 1 user.
    Retourne {"done": n, "p2_done": n, "skipped": n, "errors": n, "llm": {source: count}}.
    """
    supa = _get_supabase()
    today = date.today()

    done, skipped, errors = 0, 0, 0
    llm_counts: dict = {}

    # Tickers éligibles (≥ 1 user)
    eligible = _get_eligible_tickers(supa, min_users=1)
    if not eligible:
        logger.info("Weekly condensation: no eligible tickers")
        return {"done": 0, "p2_done": 0, "skipped": 0, "errors": 0, "llm": {}}

    # On condense la semaine terminée : la semaine passée dans le mois précédent ou actuel
    # Calcul : semaine actuelle = week_num d'aujourd'hui, on condense week_num - 1
    current_wnum = _week_num(today.day)
    if current_wnum == 1:
        # On est en W1, la semaine à condenser était W4 du mois précédent
        target_month = today.month - 1
        target_year = today.year
        if target_month <= 0:
            target_month += 12
            target_year -= 1
        target_ym = f"{target_year:04d}-{target_month:02d}"
        target_wnum = 4
    else:
        target_ym = f"{today.year:04d}-{today.month:02d}"
        target_wnum = current_wnum - 1

    logger.info(f"Weekly condensation: {target_ym} W{target_wnum} for {len(eligible)} tickers")

    for ticker in eligible:
        # Déjà condensé ? (status NULL = généré — skew/less_3_articles/error = retry)
        exists = supa.table("breaks_weekly") \
            .select("week_num, status") \
            .eq("ticker", ticker) \
            .eq("year_month", target_ym) \
            .eq("week_num", target_wnum) \
            .maybe_single() \
            .execute()
        if exists.data and _is_done(exists.data.get("status")):
            skipped += 1
            continue

        # Récupérer les articles de ce mois
        row = supa.table("articles") \
            .select("articles") \
            .eq("ticker", ticker) \
            .eq("year_month", target_ym) \
            .maybe_single() \
            .execute()
        if not row.data:
            skipped += 1
            supa.table("breaks_weekly").upsert({
                "ticker": ticker, "year_month": target_ym, "week_num": target_wnum,
                "status": "no_articles_row", "article_count": 0,
            }).execute()
            continue

        # Filtrer par semaine
        all_articles = row.data["articles"]
        week_articles = [
            a for a in all_articles
            if _week_num(datetime.fromtimestamp(a.get("datetime", 0), tz=timezone.utc).day) == target_wnum
        ]
        if not week_articles:
            skipped += 1
            supa.table("breaks_weekly").upsert({
                "ticker": ticker, "year_month": target_ym, "week_num": target_wnum,
                "status": "no_articles", "article_count": 0,
            }).execute()
            continue

        try:
            label = _week_label(target_ym, target_wnum)
            articles_text = "\n".join(_article_text(a) for a in week_articles[:30])
            summary = _llm_call(_pick(WEEK_CONDENSE_PROMPT, WEEK_CONDENSE_PROMPT_EN).format(
                ticker=ticker,
                label=label,
                articles_text=articles_text,
            ), item_id=ticker, cascade="weekly_prod")
            top_articles = [_normalize_finnhub(a) for a in sorted(week_articles, key=lambda x: x.get("pub_date", ""), reverse=True)[:10]]
            supa.table("breaks_weekly").upsert({
                "ticker":           ticker,
                "year_month":       target_ym,
                "week_num":         target_wnum,
                _summary_col("breaks_weekly"): summary,
                "top_articles":     top_articles,
                "article_count":    len(week_articles),
                "articles_used":    len(week_articles),
                "articles_related": _count_related(week_articles),
                _llm_source_col(): _llm_call._last_source,
                "condensed_at":     datetime.now(timezone.utc).isoformat(),
            }).execute()
            src = _llm_call._last_source or "unknown"
            llm_counts[src] = llm_counts.get(src, 0) + 1
            done += 1
            logger.info(f"  ✓ Weekly {ticker} {target_ym} W{target_wnum} ({len(week_articles)} articles)")
            # Purger les weeklies excédentaires (max 5)
            _purge_excess_weeklies(ticker, supa, max_weeks=5)
        except Exception as e:
            errors += 1
            logger.error(f"  ✗ Weekly {ticker} {target_ym} W{target_wnum}: {e}")
            supa.table("breaks_weekly").upsert({
                "ticker": ticker, "year_month": target_ym, "week_num": target_wnum,
                "status": "error", "article_count": len(week_articles),
            }).execute()

    # Pool 2: générer la semaine courante pour tous les tickers pinnés sans summary
    now = datetime.now(timezone.utc)
    current_ym = f"{now.year:04d}-{now.month:02d}"
    current_wnum = _week_num(now.day)
    all_pinned = _get_eligible_tickers(supa, min_users=1)
    p2_done = 0

    logger.info(f"Weekly Pool2: semaine courante {current_ym} W{current_wnum} pour {len(all_pinned)} tickers")
    for ticker in all_pinned:
        try:
            exists = supa.table("breaks_weekly") \
                .select("week_num, status") \
                .eq("ticker", ticker) \
                .eq("year_month", current_ym) \
                .eq("week_num", current_wnum) \
                .maybe_single() \
                .execute()
            if exists.data and _is_done(exists.data.get("status")):
                continue
            result = get_weekly_ondemand(ticker)
            if result:
                src = _llm_call._last_source or "unknown"
                llm_counts[src] = llm_counts.get(src, 0) + 1
                p2_done += 1
                logger.info(f"  ✓ Pool2 {ticker} {current_ym} W{current_wnum}")
        except Exception as e:
            errors += 1
            logger.error(f"  ✗ Pool2 {ticker}: {e}")
            supa.table("breaks_weekly").upsert({
                "ticker": ticker, "year_month": current_ym, "week_num": current_wnum,
                "status": "error", "article_count": 0,
            }).execute()

    return {"done": done, "p2_done": p2_done, "skipped": skipped, "errors": errors, "llm": llm_counts}


def gen_breaks_monthly() -> dict:
    """
    1er du mois à 4h : condense le mois terminé depuis les weekly summaries.
    Si les weekly summaries n'existent pas, condense depuis raw.
    Seuil : ticker pinné par ≥ 3 users.
    Retourne {"done": n, "skipped": n, "errors": n, "llm": {source: count}}.
    """
    supa = _get_supabase()
    today = date.today()

    done, skipped, errors = 0, 0, 0
    llm_counts: dict = {}

    eligible = _get_eligible_tickers(supa, min_users=3)
    if not eligible:
        logger.info("Monthly condensation: no eligible tickers")
        return {"done": 0, "skipped": 0, "errors": 0, "llm": {}}

    # Mois à condenser : le mois précédent
    target_month = today.month - 1
    target_year = today.year
    if target_month <= 0:
        target_month += 12
        target_year -= 1
    target_ym = f"{target_year:04d}-{target_month:02d}"

    logger.info(f"Monthly condensation: {target_ym} for {len(eligible)} tickers")

    for ticker in eligible:
        exists = supa.table("breaks_monthly") \
            .select("year_month, status") \
            .eq("ticker", ticker) \
            .eq("year_month", target_ym) \
            .maybe_single() \
            .execute()
        if exists.data and _is_done(exists.data.get("status")):
            skipped += 1
            continue

        article_count = 0
        try:
            # Essayer depuis les weekly summaries d'abord
            week_rows = supa.table("breaks_weekly") \
                .select("week_num, bw_en, bw_fr, article_count, articles_related, status") \
                .eq("ticker", ticker) \
                .eq("year_month", target_ym) \
                .order("week_num") \
                .execute()

            month_prompt = _pick(MONTH_CONDENSE_PROMPT, MONTH_CONDENSE_PROMPT_EN)
            sc = _summary_col("breaks_monthly")
            ok_weeks = [r for r in (week_rows.data or []) if _is_done(r.get("status"))]
            articles_related_count = 0
            if ok_weeks and len(ok_weeks) >= 3:
                src_col = "bw_en" if _lang() == "en" else "bw_fr"
                weeks_text = "\n".join(
                    f"W{r['week_num']}: {r.get(src_col) or r.get('bw_fr', '')}"
                    for r in ok_weeks
                )
                article_count = sum(r["article_count"] for r in ok_weeks)
                articles_related_count = sum(r.get("articles_related") or 0 for r in ok_weeks)
                summary = _llm_call(month_prompt.format(
                    ticker=ticker,
                    label=_month_label(target_ym),
                    weeks_text=weeks_text,
                ), max_tokens=500, item_id=ticker, cascade="monthly_prod")
            else:
                row = supa.table("articles") \
                    .select("articles") \
                    .eq("ticker", ticker) \
                    .eq("year_month", target_ym) \
                    .maybe_single() \
                    .execute()
                if not row.data:
                    skipped += 1
                    supa.table("breaks_monthly").upsert({
                        "ticker": ticker, "year_month": target_ym,
                        "status": "no_articles_row", "article_count": 0,
                    }).execute()
                    continue
                if not row.data["articles"]:
                    skipped += 1
                    supa.table("breaks_monthly").upsert({
                        "ticker": ticker, "year_month": target_ym,
                        "status": "no_articles", "article_count": 0,
                    }).execute()
                    continue
                raw = row.data["articles"]
                article_count = len(raw)
                articles_related_count = _count_related(raw)
                articles_text = "\n".join(_article_text(a) for a in raw[:40])
                summary = _llm_call(month_prompt.format(
                    ticker=ticker,
                    label=_month_label(target_ym),
                    weeks_text=articles_text,
                ), max_tokens=500, item_id=ticker, cascade="monthly_prod")

            supa.table("breaks_monthly").upsert({
                "ticker":            ticker,
                "year_month":        target_ym,
                sc:                  summary,
                "article_count":     article_count,
                "articles_used":     article_count,
                "articles_related":  articles_related_count,
                _llm_source_col():   _llm_call._last_source,
                "condensed_at":      datetime.now(timezone.utc).isoformat(),
            }).execute()
            src = _llm_call._last_source or "unknown"
            llm_counts[src] = llm_counts.get(src, 0) + 1
            done += 1
            logger.info(f"  ✓ Monthly {ticker} {target_ym} ({article_count} articles)")
        except Exception as e:
            errors += 1
            logger.error(f"  ✗ Monthly {ticker} {target_ym}: {e}")
            supa.table("breaks_monthly").upsert({
                "ticker": ticker, "year_month": target_ym,
                "status": "error", "article_count": article_count,
            }).execute()

    return {"done": done, "skipped": skipped, "errors": errors, "llm": llm_counts}


def gen_breaks_yearly() -> dict:
    """
    1er janvier à 5h : condense l'année terminée depuis les monthly summaries.
    Seuil : ticker pinné par ≥ 3 users.
    Retourne {"done": n, "skipped": n, "errors": n, "llm": {source: count}}.
    """
    supa = _get_supabase()
    today = date.today()
    target_year = str(today.year - 1)

    done, skipped, errors = 0, 0, 0
    llm_counts: dict = {}

    eligible = _get_eligible_tickers(supa, min_users=3)
    if not eligible:
        return {"done": 0, "skipped": 0, "errors": 0, "llm": {}}

    logger.info(f"Yearly condensation: {target_year} for {len(eligible)} tickers")

    for ticker in eligible:
        exists = supa.table("breaks_yearly") \
            .select("year") \
            .eq("ticker", ticker) \
            .eq("year", target_year) \
            .maybe_single() \
            .execute()
        if exists.data:
            skipped += 1
            continue

        month_rows = supa.table("breaks_monthly") \
            .select("year_month, bm_en, bm_fr") \
            .eq("ticker", ticker) \
            .like("year_month", f"{target_year}-%") \
            .order("year_month") \
            .execute()

        if not month_rows.data or len(month_rows.data) < 6:
            skipped += 1
            continue

        try:
            src_col = "bm_en" if _lang() == "en" else "bm_fr"
            months_text = "\n".join(
                f"{_month_label(r['year_month'])}: {r.get(src_col) or r.get('bm_fr', '')}"
                for r in month_rows.data
            )
            summary = _llm_call(_pick(YEAR_CONDENSE_PROMPT, YEAR_CONDENSE_PROMPT_EN).format(
                ticker=ticker,
                year=target_year,
                months_text=months_text,
            ), max_tokens=600, item_id=ticker, cascade="yearly_prod")

            supa.table("breaks_yearly").upsert({
                "ticker":       ticker,
                "year":         target_year,
                _summary_col("breaks_yearly"): summary,
                _llm_source_col(): _llm_call._last_source,
                "condensed_at": datetime.now(timezone.utc).isoformat(),
            }).execute()
            src = _llm_call._last_source or "unknown"
            llm_counts[src] = llm_counts.get(src, 0) + 1
            done += 1
            logger.info(f"  ✓ Yearly {ticker} {target_year}")

            # Purger les monthly calendaires de l'année générée
            supa.table("breaks_monthly") \
                .delete() \
                .eq("ticker", ticker) \
                .like("year_month", f"{target_year}-%") \
                .execute()
            logger.info(f"  Purged monthly summaries {ticker} {target_year}")

        except Exception as e:
            errors += 1
            logger.error(f"  ✗ Yearly {ticker} {target_year}: {e}")

    return {"done": done, "skipped": skipped, "errors": errors, "llm": llm_counts}


def yahoo_articles_scrap() -> dict[str, int]:
    """
    Cron 6h : accumule les articles Yahoo pour tous les tickers pinnés ≥ 3 users.
    Merge avec l'existant (Finnhub ou Yahoo), dedup par headline.
    Retourne {ticker: nb_articles_ajoutés} pour les tickers avec ≥1 ajout.
    """
    supa = _get_supabase()

    eligible = _get_eligible_tickers(supa, min_users=1)
    if not eligible:
        return {}

    today = date.today()
    current_ym = f"{today.year:04d}-{today.month:02d}"

    logger.info(f"Yahoo accumulation: {len(eligible)} tickers")

    results: dict[str, int] = {}
    for ticker in eligible:
        try:
            added = _yahoo_scrap_accumulate(ticker, current_ym, supa)
            if added > 0:
                results[ticker] = added
                try:
                    from services.overview_service import gen_dumb
                    n = gen_dumb(ticker, cascade="dumb_prod")
                    logger.info(f"  DUMB gen {ticker} ({n} articles brutalised)")
                    supa.table("ow_cache").update({"needs_ow_regen": True}).eq("ticker", ticker).execute()
                    logger.info(f"  needs_ow_regen flagged {ticker} (+{added} articles)")
                except Exception as ow_err:
                    logger.warning(f"  DUMB+flag failed {ticker}: {ow_err}")
        except Exception as e:
            logger.error(f"  ✗ Yahoo accumulation {ticker}: {e}")

    return results


def _yahoo_scrap_accumulate(ticker: str, year_month: str, supa) -> int:
    """Fetch Yahoo news, merge avec l'existant (Finnhub ou Yahoo), dedup par headline."""
    import yfinance as yf
    t = yf.Ticker(ticker)
    raw = t.get_news(count=100)

    new_articles = []
    for n in raw:
        c = n.get("content", {})
        pub_date = c.get("pubDate", "")
        try:
            ts = int(datetime.fromisoformat(pub_date.replace("Z", "+00:00")).timestamp()) if pub_date else 0
        except Exception:
            ts = 0
        url = (c.get("clickThroughUrl") or {}).get("url", "") or (c.get("canonicalUrl") or {}).get("url", "")
        if not url or not ts:
            continue
        new_articles.append({
            "headline": c.get("title", ""),
            "source":   c.get("provider", {}).get("displayName", "Yahoo Finance"),
            "url":      url,
            "datetime": ts,
            "summary":  c.get("summary", ""),
            "image":    (c.get("thumbnail") or {}).get("resolutions", [{}])[-1].get("url", "") if c.get("thumbnail") else "",
        })

    # Company name search for EU/ASIA tickers (identified by suffix e.g. .HK .T .PA)
    # US tickers have no suffix — get_news() coverage is sufficient
    if "." in ticker:
        try:
            info = t.info or {}
            company_name = info.get("shortName") or info.get("longName") or ""
            if company_name and company_name.lower() != ticker.lower():
                search_raw = yf.Search(company_name, news_count=30).news or []
                seen_urls = {a["url"] for a in new_articles}
                for n in search_raw:
                    c = n.get("content", {})
                    pub_date = c.get("pubDate", "")
                    try:
                        ts = int(datetime.fromisoformat(pub_date.replace("Z", "+00:00")).timestamp()) if pub_date else 0
                    except Exception:
                        ts = 0
                    url = (c.get("clickThroughUrl") or {}).get("url", "") or (c.get("canonicalUrl") or {}).get("url", "")
                    if not url or not ts or url in seen_urls:
                        continue
                    seen_urls.add(url)
                    new_articles.append({
                        "headline": c.get("title", ""),
                        "source":   c.get("provider", {}).get("displayName", "Yahoo Finance"),
                        "url":      url,
                        "datetime": ts,
                        "summary":  c.get("summary", ""),
                        "image":    (c.get("thumbnail") or {}).get("resolutions", [{}])[-1].get("url", "") if c.get("thumbnail") else "",
                    })
        except Exception:
            pass

    if not new_articles:
        return 0

    row = supa.table("articles") \
        .select("articles, sources") \
        .eq("ticker", ticker) \
        .eq("year_month", year_month) \
        .maybe_single() \
        .execute()

    existing = (row.data or {}).get("articles") or []
    existing_sources = (row.data or {}).get("sources") or []

    # Merge + dedup par headline (Yahoo en tête pour avoir les résumés Yahoo)
    merged = _dedup_by_headline(new_articles + existing)
    if len(merged) == len(existing):
        logger.info(f"  Yahoo {ticker} {year_month}: 0 nouveaux articles")
        return 0

    new_sources = existing_sources if "yahoo" in existing_sources else existing_sources + ["yahoo"]
    added = len(merged) - len(existing)
    supa.table("articles").upsert({
        "ticker":     ticker,
        "year_month": year_month,
        "articles":   merged,
        "sources":    new_sources,
        "cached_at":  datetime.now(timezone.utc).isoformat(),
    }).execute()
    logger.info(f"  Yahoo {ticker} {year_month}: +{added} articles ({len(merged)} total, sources={new_sources})")
    return added


def finnhub_articles_scrap() -> dict[str, int]:
    """
    Cron 12h : refresh Finnhub pour le mois courant sur tous les tickers pinnés ≥ 3 users.
    Merge avec l'existant via _get_or_fetch_month (TTL 12h).
    Retourne {ticker: nb_articles} pour les tickers avec ≥1 article.
    """
    supa = _get_supabase()
    today = date.today()
    current_ym = f"{today.year:04d}-{today.month:02d}"

    eligible = _get_eligible_tickers(supa, min_users=1)
    if not eligible:
        return {}

    logger.info(f"Finnhub refresh: {len(eligible)} tickers pour {current_ym}")

    results: dict[str, int] = {}
    for ticker in eligible:
        try:
            articles_before = _current_article_count(ticker, current_ym, supa)
            articles, source = _get_or_fetch_month(ticker, current_ym, supa)
            added = len(articles) - articles_before
            logger.info(f"  ✓ Finnhub {ticker}: {len(articles)} articles (source={source})")
            if articles:
                results[ticker] = len(articles)
            if added > 0:
                try:
                    from services.overview_service import gen_dumb
                    n = gen_dumb(ticker, cascade="dumb_prod")
                    logger.info(f"  DUMB gen {ticker} ({n} articles brutalised)")
                    supa.table("ow_cache").update({"needs_ow_regen": True}).eq("ticker", ticker).execute()
                    logger.info(f"  needs_ow_regen flagged {ticker} (+{added} articles)")
                except Exception as ow_err:
                    logger.warning(f"  DUMB+flag failed {ticker}: {ow_err}")
        except Exception as e:
            logger.error(f"  ✗ Finnhub refresh {ticker}: {e}")

    return results


def google_rss_articles_scrap() -> dict[str, int]:
    """
    Cron */3h : accumule les articles Google RSS pour tous les tickers pinnés ≥ 1 user.
    Feed sans filtre date (articles récents du moment).
    Merge avec l'existant, dedup par headline.
    Retourne {ticker: nb_articles_ajoutés} pour les tickers avec ≥1 ajout.
    """
    import xml.etree.ElementTree as ET
    import httpx as _httpx

    supa = _get_supabase()
    eligible = _get_eligible_tickers(supa, min_users=1)
    if not eligible:
        return {}

    today = date.today()
    current_ym = f"{today.year:04d}-{today.month:02d}"

    tickers = sorted(eligible)
    company_names = _bulk_fetch_company_names(tickers)

    logger.info(f"Google RSS prod: {len(tickers)} tickers pour {current_ym}")
    results: dict[str, int] = {}

    for ticker in tickers:
        cname = company_names.get(ticker, "")
        query = _build_rss_query(ticker, cname)
        url = f"https://news.google.com/rss/search?q={query}&hl=en&gl=US&ceid=US:en"
        try:
            import time as _time
            _time.sleep(0.5)
            r = _httpx.get(url, headers={"User-Agent": "Mozilla/5.0 (compatible; StockApp/1.0)"}, timeout=10, follow_redirects=True)
            r.raise_for_status()
            root = ET.fromstring(r.text)
            raw = []
            for item in root.findall(".//item"):
                title  = item.findtext("title") or ""
                link   = item.findtext("link") or ""
                pub    = item.findtext("pubDate") or ""
                src_el = item.find("source")
                source = src_el.text if src_el is not None else "google_rss"
                if title and link:
                    raw.append({"title": title, "url": link, "pub": pub, "source": source})

            if not raw:
                continue

            normalized = [_normalize_google_rss(a) for a in raw]
            added, _ = _upsert_articles_merge(ticker, current_ym, normalized, "google_rss", supa)
            if added > 0:
                results[ticker] = added
                try:
                    from services.overview_service import gen_dumb
                    gen_dumb(ticker, cascade="dumb_prod")
                    supa.table("ow_cache").update({"needs_ow_regen": True}).eq("ticker", ticker).execute()
                except Exception as ow_err:
                    logger.warning(f"  DUMB+flag failed {ticker}: {ow_err}")
            logger.info(f"  Google RSS {ticker}: +{added}")
        except Exception as e:
            logger.error(f"  ✗ Google RSS {ticker}: {e}")

    return results


def marketaux_articles_scrap() -> dict[str, int]:
    """
    Cron */6h : accumule les articles Marketaux pour les tickers pinnés EU+ASIA (≥ 1 user).
    Mois courant uniquement. Merge + dedup par headline.
    Retourne {ticker: nb_articles_ajoutés} pour les tickers avec ≥1 ajout.
    """
    import httpx as _httpx

    api_token = os.environ.get("MARKETAUX_API_TOKEN", "")
    if not api_token:
        logger.warning("MARKETAUX_API_TOKEN manquant — skip marketaux_articles_scrap")
        return {}

    supa = _get_supabase()
    eligible = _get_eligible_tickers(supa, min_users=1)
    if not eligible:
        return {}

    today = date.today()
    current_ym = f"{today.year:04d}-{today.month:02d}"
    last_day = monthrange(today.year, today.month)[1]

    eu_asia = sorted(t for t in eligible if _detect_region(t) in ("EU", "ASIA"))
    company_names = _bulk_fetch_company_names(eu_asia)

    logger.info(f"Marketaux prod: {len(eu_asia)} EU+ASIA tickers pour {current_ym}")
    results: dict[str, int] = {}

    for ticker in eu_asia:
        cname = company_names.get(ticker, "")
        query = _build_rss_query(ticker, cname)
        params = {
            "api_token":        api_token,
            "search":           query,
            "published_after":  f"{today.year}-{today.month:02d}-01T00:00:00",
            "published_before":  f"{today.year}-{today.month:02d}-{last_day:02d}T23:59:59",
            "language":         "en",
            "limit":            50,
        }
        try:
            import time as _time
            _time.sleep(1.0)
            r = _httpx.get("https://api.marketaux.com/v1/news/all", params=params, timeout=15)
            r.raise_for_status()
            raw = r.json().get("data") or []
            if not raw:
                continue
            normalized = [_normalize_marketaux(a) for a in raw if a.get("title") and a.get("url")]
            added, _ = _upsert_articles_merge(ticker, current_ym, normalized, "marketaux", supa)
            if added > 0:
                results[ticker] = added
                try:
                    from services.overview_service import gen_dumb
                    gen_dumb(ticker, cascade="dumb_prod")
                    supa.table("ow_cache").update({"needs_ow_regen": True}).eq("ticker", ticker).execute()
                except Exception as ow_err:
                    logger.warning(f"  DUMB+flag failed {ticker}: {ow_err}")
            logger.info(f"  Marketaux {ticker}: +{added}")
        except Exception as e:
            logger.error(f"  ✗ Marketaux {ticker}: {e}")

    return results


def newsapi_articles_scrap() -> dict[str, int]:
    """
    Cron */6h : accumule les articles NewsAPI pour les tickers pinnés EU+ASIA (≥ 1 user).
    Mois courant uniquement. Merge + dedup par headline.
    Retourne {ticker: nb_articles_ajoutés} pour les tickers avec ≥1 ajout.
    """
    import httpx as _httpx

    api_key = os.environ.get("NEWSAPI_API_KEY", "")
    if not api_key:
        logger.warning("NEWSAPI_API_KEY manquante — skip newsapi_articles_scrap")
        return {}

    supa = _get_supabase()
    eligible = _get_eligible_tickers(supa, min_users=1)
    if not eligible:
        return {}

    today = date.today()
    current_ym = f"{today.year:04d}-{today.month:02d}"
    last_day = monthrange(today.year, today.month)[1]

    eu_asia = sorted(t for t in eligible if _detect_region(t) in ("EU", "ASIA"))
    company_names = _bulk_fetch_company_names(eu_asia)

    logger.info(f"NewsAPI prod: {len(eu_asia)} EU+ASIA tickers pour {current_ym}")
    results: dict[str, int] = {}

    for ticker in eu_asia:
        cname = company_names.get(ticker, "")
        query = _build_rss_query(ticker, cname)
        params = {
            "q":        query,
            "from":     f"{today.year}-{today.month:02d}-01",
            "to":       f"{today.year}-{today.month:02d}-{last_day:02d}",
            "sortBy":   "publishedAt",
            "pageSize": 100,
            "language": "en",
            "apiKey":   api_key,
        }
        try:
            import time as _time
            _time.sleep(1.0)
            r = _httpx.get("https://newsapi.org/v2/everything", params=params, timeout=15)
            r.raise_for_status()
            data = r.json()
            if data.get("status") != "ok":
                logger.warning(f"  NewsAPI {ticker}: {data.get('message', 'error')}")
                continue
            raw = data.get("articles") or []
            if not raw:
                continue
            normalized = [_normalize_newsapi(a) for a in raw if a.get("title") and a.get("url")]
            added, _ = _upsert_articles_merge(ticker, current_ym, normalized, "newsapi", supa)
            if added > 0:
                results[ticker] = added
                try:
                    from services.overview_service import gen_dumb
                    gen_dumb(ticker, cascade="dumb_prod")
                    supa.table("ow_cache").update({"needs_ow_regen": True}).eq("ticker", ticker).execute()
                except Exception as ow_err:
                    logger.warning(f"  DUMB+flag failed {ticker}: {ow_err}")
            logger.info(f"  NewsAPI {ticker}: +{added}")
        except Exception as e:
            logger.error(f"  ✗ NewsAPI {ticker}: {e}")

    return results


def _current_article_count(ticker: str, year_month: str, supa) -> int:
    row = supa.table("articles") \
        .select("articles") \
        .eq("ticker", ticker) \
        .eq("year_month", year_month) \
        .limit(1) \
        .execute()
    arts = (row.data[0].get("articles") or []) if row.data else []
    return len(arts)


def _get_eligible_tickers(supa, min_users: int = 3) -> set[str]:
    """Retourne les tickers pinnés par au moins min_users users distincts."""
    rows = supa.table("pinned_stocks") \
        .select("ticker") \
        .execute()
    from collections import Counter
    counts = Counter(r["ticker"] for r in (rows.data or []))
    return {ticker for ticker, count in counts.items() if count >= min_users}


# ─── Rotation / Purge ─────────────────────────────────────────────────────────

def _purge_weeklies_for_month(ticker: str, year_month: str, supa) -> None:
    """Supprime tous les weekly summaries d'un year_month donné."""
    supa.table("breaks_weekly") \
        .delete() \
        .eq("ticker", ticker) \
        .eq("year_month", year_month) \
        .execute()
    logger.info(f"  Purged weeklies {ticker} {year_month}")


def _purge_excess_weeklies(ticker: str, supa, max_weeks: int = 5) -> None:
    """Garde les max_weeks weeklies les plus récents, supprime le reste."""
    rows = supa.table("breaks_weekly") \
        .select("year_month, week_num, condensed_at, status") \
        .eq("ticker", ticker) \
        .order("year_month", desc=True) \
        .order("week_num", desc=True) \
        .execute()

    all_weeks = [w for w in (rows.data or []) if _is_done(w.get("status"))]
    if len(all_weeks) <= max_weeks:
        return

    to_delete = all_weeks[max_weeks:]
    for w in to_delete:
        supa.table("breaks_weekly") \
            .delete() \
            .eq("ticker", ticker) \
            .eq("year_month", w["year_month"]) \
            .eq("week_num", w["week_num"]) \
            .execute()
    logger.info(f"  Purged {len(to_delete)} excess weeklies for {ticker}")


# ─── Monthly calendaire (1er du mois) ────────────────────────────────────────

def run_monthly_calendaire(tickers: list[str] | None = None):
    """
    1er du mois à 4h : génère le résumé calendaire du mois précédent
    depuis S1+S2+S3+S4 dans l'ordre, puis purge ces weeklies.
    Si tickers=None, utilise les tickers éligibles (≥3 users).
    """
    supa = _get_supabase()
    today = date.today()

    if tickers is None:
        tickers = list(_get_eligible_tickers(supa, min_users=3))
    if not tickers:
        return

    target_month = today.month - 1
    target_year = today.year
    if target_month <= 0:
        target_month += 12
        target_year -= 1
    target_ym = f"{target_year:04d}-{target_month:02d}"

    logger.info(f"Monthly calendaire: {target_ym} for {len(tickers)} tickers")

    for ticker in tickers:
        exists = supa.table("breaks_monthly") \
            .select("year_month, status") \
            .eq("ticker", ticker) \
            .eq("year_month", target_ym) \
            .maybe_single() \
            .execute()
        if exists.data and _is_done(exists.data.get("status")):
            # Purge weeklies même si déjà généré
            _purge_weeklies_for_month(ticker, target_ym, supa)
            continue

        week_rows = supa.table("breaks_weekly") \
            .select("week_num, bw_en, bw_fr, article_count, articles_related, status") \
            .eq("ticker", ticker) \
            .eq("year_month", target_ym) \
            .order("week_num") \
            .execute()

        ok_weeks = [r for r in (week_rows.data or []) if _is_done(r.get("status"))]
        if len(ok_weeks) < 2:
            continue

        try:
            src_col = "bw_en" if _lang() == "en" else "bw_fr"
            weeks_text = "\n".join(
                f"S{r['week_num']}: {r.get(src_col) or r.get('bw_fr', '')}" for r in ok_weeks
            )
            article_count = sum(r["article_count"] for r in ok_weeks)
            articles_related_count = sum(r.get("articles_related") or 0 for r in ok_weeks)
            summary = _llm_call(_pick(MONTH_CONDENSE_PROMPT, MONTH_CONDENSE_PROMPT_EN).format(
                ticker=ticker,
                label=_month_label(target_ym),
                weeks_text=weeks_text,
            ), max_tokens=500, item_id=ticker, cascade="monthly_prod")

            supa.table("breaks_monthly").upsert({
                "ticker":            ticker,
                "year_month":        target_ym,
                _summary_col("breaks_monthly"): summary,
                "article_count":     article_count,
                "articles_used":     article_count,
                "articles_related":  articles_related_count,
                "condensed_at":      datetime.now(timezone.utc).isoformat(),
            }).execute()
            logger.info(f"  ✓ Monthly calendaire {ticker} {target_ym}")

            # Purger les weeklies de ce mois
            _purge_weeklies_for_month(ticker, target_ym, supa)

        except Exception as e:
            logger.error(f"  ✗ Monthly calendaire {ticker} {target_ym}: {e}")


# ─── Monthly on-demand (rolling 4 semaines) ───────────────────────────────────

def get_weekly_ondemand(ticker: str) -> str | None:
    """
    Génère un résumé IA de la semaine en cours depuis ow_cache.news_raw (Yahoo 7j).
    Stocke dans breaks_weekly et retourne le texte.
    """
    ticker = ticker.upper()
    supa = _get_supabase()

    row = supa.table("ow_cache").select("news_raw").eq("ticker", ticker).maybe_single().execute()
    if not row or not row.data:
        return None

    cutoff = datetime.now(timezone.utc).timestamp() - 7 * 24 * 3600
    articles = []
    for a in (row.data.get("news_raw") or []):
        try:
            pub = a.get("pub_date", "")
            ts = datetime.fromisoformat(pub.replace("Z", "+00:00")).timestamp() if pub else float(a.get("datetime", 0) or 0)
            if not ts or ts < cutoff:
                continue
        except Exception:
            continue
        articles.append({"headline": a.get("title", "") or a.get("headline", ""), "pub_date": pub})

    if not articles:
        return None

    now = datetime.now(timezone.utc)
    year_month = now.strftime("%Y-%m")
    day = now.day
    week_num = 1 if day <= 7 else 2 if day <= 14 else 3 if day <= 21 else 4
    label = _week_label(year_month, week_num)

    articles_text = "\n".join(f"- {a['headline']}" for a in articles[:25])
    try:
        summary = _llm_call(_pick(WEEK_CONDENSE_PROMPT, WEEK_CONDENSE_PROMPT_EN).format(
            ticker=ticker, label=label, articles_text=articles_text,
        ), max_tokens=400, item_id=ticker, cascade="weekly_prod")
    except Exception as e:
        logger.error(f"Weekly ondemand {ticker}: {e}")
        return None

    supa.table("breaks_weekly").upsert({
        "ticker":       ticker,
        "year_month":   year_month,
        "week_num":     week_num,
        _summary_col(): summary,
        "top_articles": [],
        "article_count": len(articles),
        "condensed_at": now.isoformat(),
    }).execute()
    return summary


def get_or_gen_breaks_monthly_ondemand(ticker: str) -> str | None:
    """
    Génère un résumé mensuel rolling depuis les 4 derniers weekly summaries.
    Appelé à la demande (premium CTA). Pas de cache persistant.
    """
    ticker = ticker.upper()
    supa = _get_supabase()

    rows = supa.table("breaks_weekly") \
        .select("year_month, week_num, bw_en, bw_fr") \
        .eq("ticker", ticker) \
        .order("year_month", desc=True) \
        .order("week_num", desc=True) \
        .limit(4) \
        .execute()

    weeks = rows.data or []
    if not weeks:
        return None

    src_col = "bw_en" if _lang() == "en" else "bw_fr"
    label = "the last 4 weeks" if _lang() == "en" else "ces 4 dernières semaines"
    weeks_text = "\n".join(
        f"S{w['week_num']} {w['year_month']}: {w.get(src_col) or w.get('bw_fr', '')}"
        for w in reversed(weeks)
    )

    try:
        return _llm_call(_pick(MONTH_CONDENSE_PROMPT, MONTH_CONDENSE_PROMPT_EN).format(
            ticker=ticker,
            label=label,
            weeks_text=weeks_text,
        ), max_tokens=500, item_id=ticker, cascade="monthly_prod")
    except Exception as e:
        logger.error(f"Monthly ondemand {ticker}: {e}")
        return None


# ─── Orchestration post-scrap (partagée entre les scripts de scrapping) ───────

MIN_W_SCRAP = 10   # seuil min articles par semaine pour générer un W summary
MIN_M_SCRAP = 25   # seuil min articles par mois pour générer un M summary


def scrap_gen_weekly(ticker: str, year_month: str, supa, caller: str = "scrap", cascade: str = "weekly_scrap") -> bool:
    """Génère le W breaker de la semaine courante si ≥ MIN_W_SCRAP articles.

    N'écrase pas un summary existant (done_more_10/done_less_10 → skip).
    Retourne True si généré.
    """
    today = date.today()
    w = _week_num(today.day)

    exists = supa.table("breaks_weekly") \
        .select("status") \
        .eq("ticker", ticker) \
        .eq("year_month", year_month) \
        .eq("week_num", w) \
        .limit(1) \
        .execute()
    if exists and exists.data and _is_done(exists.data[0].get("status")):
        return False

    row = supa.table("articles").select("articles").eq("ticker", ticker).eq("year_month", year_month).limit(1).execute()
    if not row or not row.data:
        return False
    week_arts = [
        a for a in (row.data[0].get("articles") or [])
        if _week_num(datetime.fromtimestamp(a.get("datetime", 0), tz=timezone.utc).day) == w
        and datetime.fromtimestamp(a.get("datetime", 0), tz=timezone.utc).strftime("%Y-%m") == year_month
    ]
    if len(week_arts) < MIN_W_SCRAP:
        supa.table("breaks_weekly").upsert({
            "ticker":        ticker,
            "year_month":    year_month,
            "week_num":      w,
            "status":        "less_10_articles",
            "article_count": len(week_arts),
        }).execute()
        return False

    status_val = "done_more_10"
    label = _week_label(year_month, w)
    articles_text = "\n".join(_article_text(a) for a in week_arts)
    try:
        summary = _llm_call(_pick(WEEK_CONDENSE_PROMPT, WEEK_CONDENSE_PROMPT_EN).format(
            ticker=ticker, label=label, articles_text=articles_text,
        ), max_tokens=400, item_id=ticker, cascade=cascade)
        supa.table("breaks_weekly").upsert({
            "ticker":            ticker,
            "year_month":        year_month,
            "week_num":          w,
            _summary_col("breaks_weekly"): summary,
            "article_count":     len(week_arts),
            "articles_used":     len(week_arts),
            "articles_related":  _count_related(week_arts),
            _llm_source_col():   _llm_call._last_source,
            "condensed_at":      datetime.now(timezone.utc).isoformat(),
            "status":            status_val,
        }).execute()
        src_counts: dict[str, int] = {}
        for a in week_arts:
            s = a.get("source", "unknown")
            src_counts[s] = src_counts.get(s, 0) + 1
        src_str = " ".join(f"{s}={c}" for s, c in sorted(src_counts.items()))
        logger.info(f"  W scrap {ticker} {year_month} W{w} ({len(week_arts)} arts · {src_str}) [{caller}]")
        return True
    except Exception as e:
        logger.warning(f"  W scrap {ticker} {year_month} W{w}: {e}")
        return False


def _gen_monthly_from_weeklies(ticker: str, year_month: str, week_rows: list[dict], supa, cascade: str = "monthly_scrap") -> str | bool:
    """Génère M depuis les W summaries (status=null). Retourne str=source LLM si succès, False sinon."""
    sc = _summary_col("breaks_monthly")
    src_col = "bw_en" if _lang() == "en" else "bw_fr"
    weeks_text = "\n".join(
        f"W{r['week_num']}: {r.get(src_col) or r.get('bw_fr', '')}"
        for r in week_rows
    )
    article_count = sum(r.get("article_count", 0) for r in week_rows)
    articles_related_count = sum(r.get("articles_related") or 0 for r in week_rows)
    label = _month_label(year_month)
    try:
        summary = _llm_call(
            _pick(MONTH_CONDENSE_PROMPT, MONTH_CONDENSE_PROMPT_EN).format(
                ticker=ticker, label=label, weeks_text=weeks_text,
            ),
            max_tokens=500, item_id=ticker, cascade=cascade
        )
        supa.table("breaks_monthly").upsert({
            "ticker":            ticker,
            "year_month":        year_month,
            sc:                  summary,
            "article_count":     article_count,
            "articles_used":     article_count,
            "articles_related":  articles_related_count,
            _llm_source_col():   _llm_call._last_source,
            "condensed_at":      datetime.now(timezone.utc).isoformat(),
            "status":            "done_more_10",
        }).execute()
        src = _llm_call._last_source or "unknown"
        logger.info(f"  ✓ M weeklies {ticker} {year_month} ({len(week_rows)} W) [{src}]")
        return src
    except Exception as e:
        logger.warning(f"  M weeklies {ticker} {year_month}: {e}")
        return False


def scrap_gen_monthly(ticker: str, year_month: str, supa, caller: str = "scrap", cascade: str | None = None) -> str | bool:
    """Génère le M breaker si ≥ MIN_M_SCRAP articles pour ce mois.

    Cascade : si fournie, utilisée directement. Sinon : monthly_scrap si is_fresh, sinon monthly_scrap_ext.
    N'écrase pas un summary existant (done_more_10/done_less_10 → skip).
    Retourne str=source LLM si généré, False sinon.
    """
    exists = supa.table("breaks_monthly") \
        .select("status") \
        .eq("ticker", ticker) \
        .eq("year_month", year_month) \
        .limit(1) \
        .execute()
    if exists and exists.data and _is_done(exists.data[0].get("status")):
        return False

    # Chemin weeklies : >= 3 W done pour ce mois
    week_rows = supa.table("breaks_weekly") \
        .select("week_num, bw_en, bw_fr, article_count, articles_related, status") \
        .eq("ticker", ticker) \
        .eq("year_month", year_month) \
        .order("week_num") \
        .execute()
    ok_weeks = [r for r in (week_rows.data or []) if _is_done(r.get("status"))]
    if len(ok_weeks) >= 3:
        return _gen_monthly_from_weeklies(ticker, year_month, ok_weeks, supa, cascade=cascade or "monthly_scrap")

    # Chemin raw : >= MIN_M_SCRAP articles
    row = supa.table("articles").select("articles, cached_at").eq("ticker", ticker).eq("year_month", year_month).limit(1).execute()
    if not row or not row.data:
        return False
    all_arts = row.data[0].get("articles") or []
    if len(all_arts) < MIN_M_SCRAP:
        return False

    if cascade is None:
        fresh = _is_month_fresh(ticker, year_month, supa, min_count=MIN_M_SCRAP, max_days=3)
        cascade = "monthly_scrap" if fresh else "monthly_scrap_ext"

    articles_text = "\n".join(_article_text(a) for a in all_arts)
    try:
        summary = _llm_call(_pick(MONTH_CONDENSE_PROMPT, MONTH_CONDENSE_PROMPT_EN).format(
            ticker=ticker, label=_month_label(year_month), weeks_text=articles_text,
        ), max_tokens=500, item_id=ticker, cascade=cascade)
        supa.table("breaks_monthly").upsert({
            "ticker":            ticker,
            "year_month":        year_month,
            _summary_col("breaks_monthly"): summary,
            "article_count":     len(all_arts),
            "articles_used":     len(all_arts),
            "articles_related":  _count_related(all_arts),
            _llm_source_col():   _llm_call._last_source,
            "condensed_at":      datetime.now(timezone.utc).isoformat(),
            "status":            "done_more_10",
        }).execute()
        src_counts: dict[str, int] = {}
        for a in all_arts:
            s = a.get("source", "unknown")
            src_counts[s] = src_counts.get(s, 0) + 1
        src_str = " ".join(f"{s}={c}" for s, c in sorted(src_counts.items()))
        src = _llm_call._last_source or "unknown"
        logger.info(f"  M scrap {ticker} {year_month} ({len(all_arts)} arts · {src_str}, cascade={cascade}) [{caller}] [{src}]")
        return src
    except Exception as e:
        logger.warning(f"  M scrap {ticker} {year_month}: {e}")
        return False


# ─── T1 / T2 / T3 — scrapers P2 tiers ───────────────────────────────────────

AV_DAILY_LIMIT = 25
_av_daily_count: int = 0
_av_last_reset_date: date | None = None


def _is_t3_day() -> bool:
    """True si aujourd'hui est le dernier jour du mois."""
    today = date.today()
    return today.day == monthrange(today.year, today.month)[1]


def _article_count_this_week(ticker: str, supa) -> int:
    """Compte les articles dans la semaine intra-mois courante (découpage W1-W4)."""
    now = datetime.now(timezone.utc)
    current_ym = now.strftime("%Y-%m")
    current_wnum = _week_num(now.day)
    row = supa.table("articles").select("articles") \
        .eq("ticker", ticker).eq("year_month", current_ym).maybe_single().execute()
    if not row or not row.data:
        return 0
    articles = row.data.get("articles") or []
    return sum(
        1 for a in articles
        if _week_num(datetime.fromtimestamp(a.get("datetime", 0), tz=timezone.utc).day) == current_wnum
    )


def _mark_tried_source(ticker: str, ym: str, source: str, supa) -> None:
    """Ajoute source dans tried_sources pour (ticker, ym). Crée la row si absente."""
    resp = supa.table("articles").select("tried_sources") \
        .eq("ticker", ticker).eq("year_month", ym).maybe_single().execute()
    current = set((resp.data or {}).get("tried_sources") or []) if resp else set()
    current.add(source)
    ordered = sorted(current)
    up = supa.table("articles").update({"tried_sources": ordered}) \
        .eq("ticker", ticker).eq("year_month", ym).execute()
    if not up.data:
        supa.table("articles").insert({
            "ticker": ticker, "year_month": ym,
            "articles": [], "sources": [], "tried_sources": ordered,
        }).execute()


def _t1_enrich_ticker(ticker: str, current_ym: str, cname: str, supa,
                      mx_token: str, newsapi_key: str, av_key: str,
                      mx_budget: list, newsapi_budget: list) -> int:
    """Tente d'enrichir un ticker 0-article. Retourne le nb d'articles ajoutés."""
    global _av_daily_count

    region = _detect_region(ticker)
    query = _build_rss_query(ticker, cname)

    tried_resp = supa.table("articles").select("tried_sources") \
        .eq("ticker", ticker).eq("year_month", current_ym).maybe_single().execute()
    tried = set((tried_resp.data or {}).get("tried_sources") or []) if tried_resp else set()

    total_added = 0

    if region in ("EU", "ASIA"):
        if newsapi_key and newsapi_budget[0] > 0 and "newsapi" not in tried:
            raw = _fetch_newsapi_month(query, current_ym, newsapi_key)
            newsapi_budget[0] -= 1
            _mark_tried_source(ticker, current_ym, "newsapi", supa)
            if raw:
                normalized = [_normalize_newsapi(a) for a in raw if a.get("title") and a.get("url")]
                n, _ = _upsert_articles_merge(ticker, current_ym, normalized, "newsapi", supa)
                total_added += n

        if mx_token and mx_budget[0] > 0 and "marketaux" not in tried:
            raw = _fetch_marketaux_month(query, current_ym, mx_token)
            mx_budget[0] -= 1
            _mark_tried_source(ticker, current_ym, "marketaux", supa)
            if raw:
                normalized = [_normalize_marketaux(a) for a in raw if a.get("title") and a.get("url")]
                n, _ = _upsert_articles_merge(ticker, current_ym, normalized, "marketaux", supa)
                total_added += n

    elif region == "US":
        if av_key and _av_daily_count < AV_DAILY_LIMIT and "av" not in tried:
            raw = _fetch_av_month(ticker, current_ym, av_key)
            _av_daily_count += 1
            _mark_tried_source(ticker, current_ym, "av", supa)
            if raw:
                normalized = [_normalize_av(a) for a in raw if a.get("title") and a.get("url")]
                n, _ = _upsert_articles_merge(ticker, current_ym, normalized, "alphavantage", supa)
                total_added += n

    return total_added


def t1_p2_scrap() -> dict[str, int]:
    """
    Cron */6h :55 — enrichit les tickers pinnés avec 0 articles ce mois.
    EU+ASIA : NewsAPI + Marketaux. US : Alpha Vantage (budget 25 req/j).
    Idempotent via tried_sources. Budget cap : 20 NewsAPI + 20 Marketaux par run.
    """
    global _av_daily_count, _av_last_reset_date

    today = date.today()
    if _av_last_reset_date != today:
        _av_daily_count = 0
        _av_last_reset_date = today

    supa = _get_supabase()
    current_ym = today.strftime("%Y-%m")

    eligible = _get_eligible_tickers(supa, min_users=1)
    if not eligible:
        return {}

    zero_art = [t for t in eligible if _current_article_count(t, current_ym, supa) == 0]
    if not zero_art:
        logger.info("T1: aucun ticker à 0 articles")
        return {}

    logger.info(f"T1: {len(zero_art)} tickers à 0 articles")

    mx_token = os.environ.get("MARKETAUX_API_TOKEN", "")
    newsapi_key = os.environ.get("NEWSAPI_API_KEY", "")
    av_key = os.environ.get("ALPHAVANTAGE_API_KEY", "")
    company_names = _bulk_fetch_company_names(zero_art)

    mx_budget = [20]
    newsapi_budget = [20]
    results: dict[str, int] = {}

    for ticker in zero_art:
        if mx_budget[0] <= 0 and newsapi_budget[0] <= 0 and _av_daily_count >= AV_DAILY_LIMIT:
            logger.info("T1: tous les budgets épuisés")
            break
        try:
            added = _t1_enrich_ticker(ticker, current_ym, company_names.get(ticker, ""),
                                       supa, mx_token, newsapi_key, av_key, mx_budget, newsapi_budget)
            if added > 0:
                results[ticker] = added
                try:
                    from services.overview_service import gen_dumb
                    gen_dumb(ticker, cascade="dumb_prod")
                    supa.table("ow_cache").update({"needs_ow_regen": True}).eq("ticker", ticker).execute()
                except Exception as e:
                    logger.warning(f"  T1 DUMB+flag {ticker}: {e}")
            logger.info(f"  T1 {ticker} ({_detect_region(ticker)}): +{added}")
        except Exception as e:
            logger.error(f"  ✗ T1 {ticker}: {e}")

    logger.info(f"T1: {len(results)} tickers enrichis | AV {_av_daily_count}/{AV_DAILY_LIMIT} | mx_budget={mx_budget[0]} newsapi_budget={newsapi_budget[0]}")
    return results


def t2_w_threshold_scrap() -> dict[str, int]:
    """
    Cron dim 22h — enrichit les tickers pinnés avec < 10 articles cette semaine.
    Skip si c'est le dernier jour du mois (T3 prend le relais).
    Cascade : RSS → NewsAPI → Marketaux → AV. Idempotent via tried_sources.
    """
    if _is_t3_day():
        logger.info("T2: dernier jour du mois — skip (T3 couvre)")
        return {}

    supa = _get_supabase()
    eligible = _get_eligible_tickers(supa, min_users=1)
    if not eligible:
        return {}

    W_THRESHOLD = 10
    targets = [t for t in eligible if _article_count_this_week(t, supa) < W_THRESHOLD]
    if not targets:
        logger.info("T2: tous les tickers au-dessus du seuil W")
        return {}

    logger.info(f"T2: {len(targets)} tickers sous seuil W ({W_THRESHOLD})")

    today = date.today()
    current_ym = today.strftime("%Y-%m")
    company_names = _bulk_fetch_company_names(targets)
    mx_token = os.environ.get("MARKETAUX_API_TOKEN", "")
    newsapi_key = os.environ.get("NEWSAPI_API_KEY", "")
    av_key = os.environ.get("ALPHAVANTAGE_API_KEY", "")

    results: dict[str, int] = {}
    for ticker in targets:
        try:
            added = scrap_ext_sources(ticker, current_ym, supa, company_names.get(ticker, ""))
            if added > 0:
                results[ticker] = added
                try:
                    from services.overview_service import gen_dumb
                    gen_dumb(ticker, cascade="dumb_prod")
                    supa.table("ow_cache").update({"needs_ow_regen": True}).eq("ticker", ticker).execute()
                except Exception as e:
                    logger.warning(f"  T2 DUMB+flag {ticker}: {e}")
            logger.info(f"  T2 {ticker}: +{added}")
        except Exception as e:
            logger.error(f"  ✗ T2 {ticker}: {e}")

    logger.info(f"T2: {len(results)} tickers enrichis")
    return results


def t3_m_threshold_scrap() -> dict[str, int]:
    """
    Cron dernier jour du mois 23h — enrichit les tickers pinnés avec < 25 articles ce mois.
    Cascade : RSS → NewsAPI → Marketaux → AV. Idempotent via tried_sources.
    """
    supa = _get_supabase()
    eligible = _get_eligible_tickers(supa, min_users=1)
    if not eligible:
        return {}

    M_THRESHOLD = 25
    today = date.today()
    current_ym = today.strftime("%Y-%m")

    targets = [t for t in eligible if _current_article_count(t, current_ym, supa) < M_THRESHOLD]
    if not targets:
        logger.info("T3: tous les tickers au-dessus du seuil M")
        return {}

    logger.info(f"T3: {len(targets)} tickers sous seuil M ({M_THRESHOLD})")

    company_names = _bulk_fetch_company_names(targets)
    results: dict[str, int] = {}

    for ticker in targets:
        try:
            added = scrap_ext_sources(ticker, current_ym, supa, company_names.get(ticker, ""))
            if added > 0:
                results[ticker] = added
                try:
                    from services.overview_service import gen_dumb
                    gen_dumb(ticker, cascade="dumb_prod")
                    supa.table("ow_cache").update({"needs_ow_regen": True}).eq("ticker", ticker).execute()
                except Exception as e:
                    logger.warning(f"  T3 DUMB+flag {ticker}: {e}")
            logger.info(f"  T3 {ticker}: +{added}")
        except Exception as e:
            logger.error(f"  ✗ T3 {ticker}: {e}")

    logger.info(f"T3: {len(results)} tickers enrichis")
    return results
