"""
Breaks — articles par période et condensation IA (weekly / monthly / yearly).

Fonctions principales :
  get_period_articles(ticker, period)  → 1mo/6mo/1y : sections W1-W4 ou mois, résumé AI si dispo
  get_all_available(ticker)            → 5Y : mois par mois, breaker si dispo sinon articles bruts

Crons de condensation (appelés depuis main.py) :
  run_weekly_condensation()    → lundi 3h30 : weekly summaries → article_week_summaries
  run_monthly_calendaire()     → 1er du mois 4h : monthly depuis weeklies → article_month_summaries
  run_yearly_condensation()    → 1er jan 5h : yearly depuis monthlies → article_year_summaries
  run_yahoo_accumulation()     → */3h : merge Yahoo → articles_merged_finn_yahoo + trigger OW
  run_finnhub_refresh()        → */6h : merge Finnhub → articles_merged_finn_yahoo + trigger OW

Source de vérité articles : articles_merged_finn_yahoo (jamais article_archive).
"""
import httpx
import os
import re
import logging
from datetime import date, datetime, timezone, timedelta
from calendar import monthrange
from supabase import create_client
import hashlib

logger = logging.getLogger(__name__)

ACTIVE_MONTH_TTL_HOURS = 12

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


def _is_ticker_related(a: dict, ticker: str, company_name: str = "") -> bool:
    """Passe si dumb présent, ou si ticker/mots-clés société dans headline+summary."""
    if a.get("dumb_en") or a.get("dumb_fr"):
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
    return any(kw in text for kw in keywords)


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


def _normalize_yahoo(a: dict) -> dict:
    """Yahoo articles sont déjà dans le bon format (stock_news_cache)."""
    return a


# ─── Finnhub fetch & cache ────────────────────────────────────────────────────

def _fetch_finnhub_month(ticker: str, year_month: str) -> list[dict]:
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
    Retourne (articles_raw, source) depuis articles_merged_finn_yahoo ou Finnhub.
    Merge les nouveaux articles Finnhub avec l'existant (Yahoo inclus), pas d'écrasement.
    source = 'finnhub' | 'yahoo' | 'mixed'
    """
    row = supa.table("articles_merged_finn_yahoo") \
        .select("articles, source, cached_at") \
        .eq("ticker", ticker) \
        .eq("year_month", year_month) \
        .maybe_single() \
        .execute()

    cached = row.data if row else None

    if cached:
        if not _is_active_month(year_month):
            return cached["articles"], cached["source"]
        cached_at = datetime.fromisoformat(cached["cached_at"].replace("Z", "+00:00"))
        if datetime.now(timezone.utc) - cached_at < timedelta(hours=ACTIVE_MONTH_TTL_HOURS):
            return cached["articles"], cached["source"]

    existing = (cached or {}).get("articles") or []
    raw = _fetch_finnhub_month(ticker, year_month)

    if not raw:
        # Pas de données Finnhub : bump cached_at pour éviter des refetches constants
        if cached:
            supa.table("articles_merged_finn_yahoo").update({
                "cached_at": datetime.now(timezone.utc).isoformat()
            }).eq("ticker", ticker).eq("year_month", year_month).execute()
            return existing, cached["source"]
        return [], "yahoo"

    # Merge Finnhub + existant (Yahoo ou Finnhub précédent), dedup par headline
    merged = _dedup_by_headline(raw + existing)
    source = "mixed" if existing else "finnhub"

    supa.table("articles_merged_finn_yahoo").upsert({
        "ticker":     ticker,
        "year_month": year_month,
        "articles":   merged,
        "source":     source,
        "cached_at":  datetime.now(timezone.utc).isoformat(),
    }).execute()
    logger.info(f"Finnhub merged {len(raw)} new + {len(existing)} existing → {len(merged)} for {ticker} {year_month} (source={source})")
    return merged, source


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

def get_period_articles(ticker: str, period: str, company_name: str = "") -> dict:
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
    month_rows = supa.table("article_month_summaries") \
        .select("year_month, ai_summary, ai_summary_en, ai_summary_fr, article_count") \
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
        filtered.sort(key=lambda a: a.get("datetime", 0), reverse=True)
        top = [_normalize_finnhub(a) for a in filtered]

        if not top and not ai:
            continue

        summaries.append({
            "type":          "month",
            "year_month":    ym,
            "label":         _month_label(ym),
            "ai_summary":    (ai.get(_summary_col()) or ai.get("ai_summary")) if ai else None,
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
    week_rows = supa.table("article_week_summaries") \
        .select("year_month, week_num, ai_summary, article_count") \
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
                    "ai_summary":    (ai.get(_summary_col()) or ai.get("ai_summary")) if ai else None,
                    "article_count": len(articles),
                    "top_articles":  top,
                })

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


def get_all_available(ticker: str) -> dict:
    """
    5Y : Y breakers + M breakers uniquement — pas d'articles bruts.
    Header année avec yearly summary, puis mois avec monthly summary.
    Mois sans M breaker : ignorés (pas de fallback articles).
    """
    ticker = ticker.upper()
    supa = _get_supabase()

    month_rows = supa.table("article_month_summaries") \
        .select("year_month, ai_summary, ai_summary_en, ai_summary_fr, article_count") \
        .eq("ticker", ticker) \
        .order("year_month", desc=True) \
        .execute()
    month_summary_map = {r["year_month"]: r for r in (month_rows.data or [])}

    year_rows = supa.table("article_year_summaries") \
        .select("year, ai_summary, ai_summary_en, ai_summary_fr") \
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
                "ai_summary":   (yr.get(_summary_col()) or yr.get("ai_summary")) if yr else None,
                "top_articles": [],
            })

        r = month_summary_map[ym]
        summaries.append({
            "type":          "month",
            "year_month":    ym,
            "label":         _month_label(ym),
            "ai_summary":    r.get(_summary_col()) or r.get("ai_summary"),
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


def _summary_col() -> str:
    return "ai_summary_en" if _lang() == "en" else "ai_summary_fr"


def _llm_call(prompt: str, max_tokens: int = 400, allow_groq: bool = True) -> str:
    from services.llm_service import llm_call
    resp = llm_call(
        [{"role": "user", "content": prompt}],
        groq_model="llama-3.3-70b-versatile",
        max_tokens=max_tokens,
        timeout=45,
        allow_groq=allow_groq,
        caller="Breaks",
    )
    _llm_call._last_source = resp.get("_llm_source")
    return resp["choices"][0]["message"]["content"].strip()

_llm_call._last_source = None


def run_weekly_condensation():
    """
    Lundi 3h : condense les semaines terminées de la semaine passée.
    Seuil : ticker pinné par ≥ 3 users.
    """
    supa = _get_supabase()
    today = date.today()

    # Tickers éligibles (≥ 3 users)
    eligible = _get_eligible_tickers(supa, min_users=3)
    if not eligible:
        logger.info("Weekly condensation: no eligible tickers")
        return

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
        # Déjà condensé ?
        exists = supa.table("article_week_summaries") \
            .select("week_num") \
            .eq("ticker", ticker) \
            .eq("year_month", target_ym) \
            .eq("week_num", target_wnum) \
            .maybe_single() \
            .execute()
        if exists.data:
            continue

        # Récupérer les articles de ce mois
        row = supa.table("articles_merged_finn_yahoo") \
            .select("articles") \
            .eq("ticker", ticker) \
            .eq("year_month", target_ym) \
            .maybe_single() \
            .execute()
        if not row.data:
            continue

        # Filtrer par semaine
        all_articles = row.data["articles"]
        week_articles = [
            a for a in all_articles
            if _week_num(datetime.fromtimestamp(a.get("datetime", 0), tz=timezone.utc).day) == target_wnum
        ]
        if not week_articles:
            continue

        try:
            label = _week_label(target_ym, target_wnum)
            articles_text = "\n".join(_article_text(a) for a in week_articles[:30])
            summary = _llm_call(_pick(WEEK_CONDENSE_PROMPT, WEEK_CONDENSE_PROMPT_EN).format(
                ticker=ticker,
                label=label,
                articles_text=articles_text,
            ))
            top_articles = [_normalize_finnhub(a) for a in sorted(week_articles, key=lambda x: x.get("pub_date", ""), reverse=True)[:10]]
            supa.table("article_week_summaries").upsert({
                "ticker":        ticker,
                "year_month":    target_ym,
                "week_num":      target_wnum,
                _summary_col():  summary,
                "top_articles":  top_articles,
                "article_count": len(week_articles),
                "llm_source":    _llm_call._last_source,
                "condensed_at":  datetime.now(timezone.utc).isoformat(),
            }).execute()
            logger.info(f"  ✓ Weekly {ticker} {target_ym} W{target_wnum} ({len(week_articles)} articles)")
            # Purger les weeklies excédentaires (max 5)
            _purge_excess_weeklies(ticker, supa, max_weeks=5)
        except Exception as e:
            logger.error(f"  ✗ Weekly {ticker} {target_ym} W{target_wnum}: {e}")

    # Pool 2: générer la semaine courante pour tous les tickers pinnés sans summary
    now = datetime.now(timezone.utc)
    current_ym = f"{now.year:04d}-{now.month:02d}"
    current_wnum = _week_num(now.day)
    all_pinned = _get_eligible_tickers(supa, min_users=1)

    logger.info(f"Weekly Pool2: semaine courante {current_ym} W{current_wnum} pour {len(all_pinned)} tickers")
    for ticker in all_pinned:
        try:
            exists = supa.table("article_week_summaries") \
                .select("week_num") \
                .eq("ticker", ticker) \
                .eq("year_month", current_ym) \
                .eq("week_num", current_wnum) \
                .maybe_single() \
                .execute()
            if exists.data:
                continue
            result = get_weekly_ondemand(ticker)
            if result:
                logger.info(f"  ✓ Pool2 {ticker} {current_ym} W{current_wnum}")
        except Exception as e:
            logger.error(f"  ✗ Pool2 {ticker}: {e}")


def run_monthly_condensation():
    """
    1er du mois à 4h : condense le mois terminé depuis les weekly summaries.
    Si les weekly summaries n'existent pas, condense depuis raw.
    Seuil : ticker pinné par ≥ 3 users.
    """
    supa = _get_supabase()
    today = date.today()

    eligible = _get_eligible_tickers(supa, min_users=3)
    if not eligible:
        logger.info("Monthly condensation: no eligible tickers")
        return

    # Mois à condenser : le mois précédent
    target_month = today.month - 1
    target_year = today.year
    if target_month <= 0:
        target_month += 12
        target_year -= 1
    target_ym = f"{target_year:04d}-{target_month:02d}"

    logger.info(f"Monthly condensation: {target_ym} for {len(eligible)} tickers")

    for ticker in eligible:
        exists = supa.table("article_month_summaries") \
            .select("year_month") \
            .eq("ticker", ticker) \
            .eq("year_month", target_ym) \
            .maybe_single() \
            .execute()
        if exists.data:
            continue

        try:
            # Essayer depuis les weekly summaries d'abord
            week_rows = supa.table("article_week_summaries") \
                .select("week_num, ai_summary, ai_summary_en, ai_summary_fr, article_count") \
                .eq("ticker", ticker) \
                .eq("year_month", target_ym) \
                .order("week_num") \
                .execute()

            month_prompt = _pick(MONTH_CONDENSE_PROMPT, MONTH_CONDENSE_PROMPT_EN)
            sc = _summary_col()
            if week_rows.data and len(week_rows.data) >= 3:
                src_col = "ai_summary_en" if _lang() == "en" else "ai_summary_fr"
                weeks_text = "\n".join(
                    f"W{r['week_num']}: {r.get(src_col) or r.get('ai_summary_fr') or r.get('ai_summary', '')}"
                    for r in week_rows.data
                )
                article_count = sum(r["article_count"] for r in week_rows.data)
                summary = _llm_call(month_prompt.format(
                    ticker=ticker,
                    label=_month_label(target_ym),
                    weeks_text=weeks_text,
                ), max_tokens=500)
            else:
                row = supa.table("articles_merged_finn_yahoo") \
                    .select("articles") \
                    .eq("ticker", ticker) \
                    .eq("year_month", target_ym) \
                    .maybe_single() \
                    .execute()
                if not row.data or not row.data["articles"]:
                    continue
                raw = row.data["articles"]
                article_count = len(raw)
                articles_text = "\n".join(_article_text(a) for a in raw[:40])
                summary = _llm_call(month_prompt.format(
                    ticker=ticker,
                    label=_month_label(target_ym),
                    weeks_text=articles_text,
                ), max_tokens=500)

            supa.table("article_month_summaries").upsert({
                "ticker":        ticker,
                "year_month":    target_ym,
                sc:              summary,
                "article_count": article_count,
                "llm_source":    _llm_call._last_source,
                "condensed_at":  datetime.now(timezone.utc).isoformat(),
            }).execute()
            logger.info(f"  ✓ Monthly {ticker} {target_ym} ({article_count} articles)")
        except Exception as e:
            logger.error(f"  ✗ Monthly {ticker} {target_ym}: {e}")


def run_yearly_condensation():
    """
    1er janvier à 5h : condense l'année terminée depuis les monthly summaries.
    Seuil : ticker pinné par ≥ 3 users.
    """
    supa = _get_supabase()
    today = date.today()
    target_year = str(today.year - 1)

    eligible = _get_eligible_tickers(supa, min_users=3)
    if not eligible:
        return

    logger.info(f"Yearly condensation: {target_year} for {len(eligible)} tickers")

    for ticker in eligible:
        exists = supa.table("article_year_summaries") \
            .select("year") \
            .eq("ticker", ticker) \
            .eq("year", target_year) \
            .maybe_single() \
            .execute()
        if exists.data:
            continue

        month_rows = supa.table("article_month_summaries") \
            .select("year_month, ai_summary, ai_summary_en, ai_summary_fr") \
            .eq("ticker", ticker) \
            .like("year_month", f"{target_year}-%") \
            .order("year_month") \
            .execute()

        if not month_rows.data or len(month_rows.data) < 6:
            continue  # Pas assez de data pour une année complète

        try:
            src_col = "ai_summary_en" if _lang() == "en" else "ai_summary_fr"
            months_text = "\n".join(
                f"{_month_label(r['year_month'])}: {r.get(src_col) or r.get('ai_summary_fr') or r.get('ai_summary', '')}"
                for r in month_rows.data
            )
            summary = _llm_call(_pick(YEAR_CONDENSE_PROMPT, YEAR_CONDENSE_PROMPT_EN).format(
                ticker=ticker,
                year=target_year,
                months_text=months_text,
            ), max_tokens=600)

            supa.table("article_year_summaries").upsert({
                "ticker":       ticker,
                "year":         target_year,
                _summary_col(): summary,
                "llm_source":   _llm_call._last_source,
                "condensed_at": datetime.now(timezone.utc).isoformat(),
            }).execute()
            logger.info(f"  ✓ Yearly {ticker} {target_year}")

            # Purger les monthly calendaires de l'année générée
            supa.table("article_month_summaries") \
                .delete() \
                .eq("ticker", ticker) \
                .like("year_month", f"{target_year}-%") \
                .execute()
            logger.info(f"  Purged monthly summaries {ticker} {target_year}")

        except Exception as e:
            logger.error(f"  ✗ Yearly {ticker} {target_year}: {e}")


def run_yahoo_accumulation() -> dict[str, int]:
    """
    Cron 6h : accumule les articles Yahoo pour tous les tickers pinnés ≥ 3 users.
    Merge avec l'existant (Finnhub ou Yahoo), dedup par headline.
    Retourne {ticker: nb_articles_ajoutés} pour les tickers avec ≥1 ajout.
    """
    supa = _get_supabase()

    eligible = _get_eligible_tickers(supa, min_users=3)
    if not eligible:
        return {}

    today = date.today()
    current_ym = f"{today.year:04d}-{today.month:02d}"

    logger.info(f"Yahoo accumulation: {len(eligible)} tickers")

    results: dict[str, int] = {}
    for ticker in eligible:
        try:
            added = _accumulate_yahoo(ticker, current_ym, supa)
            if added > 0:
                results[ticker] = added
                try:
                    from services.overview_service import brutalise_new_articles, get_news
                    n = brutalise_new_articles(ticker)
                    logger.info(f"  DUMB gen {ticker} ({n} articles brutalised)")
                    get_news(ticker, force_24h=True)
                    logger.info(f"  OW refresh triggered {ticker} (+{added} articles)")
                except Exception as ow_err:
                    logger.warning(f"  DUMB+OW refresh failed {ticker}: {ow_err}")
        except Exception as e:
            logger.error(f"  ✗ Yahoo accumulation {ticker}: {e}")

    return results


def _accumulate_yahoo(ticker: str, year_month: str, supa) -> int:
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

    if not new_articles:
        return 0

    row = supa.table("articles_merged_finn_yahoo") \
        .select("articles, source") \
        .eq("ticker", ticker) \
        .eq("year_month", year_month) \
        .maybe_single() \
        .execute()

    existing = (row.data or {}).get("articles") or []
    existing_source = (row.data or {}).get("source", "yahoo")

    # Merge + dedup par headline (Yahoo en tête pour avoir les résumés Yahoo)
    merged = _dedup_by_headline(new_articles + existing)
    if len(merged) == len(existing):
        logger.info(f"  Yahoo {ticker} {year_month}: 0 nouveaux articles")
        return 0

    new_source = "mixed" if existing_source == "finnhub" else "yahoo"
    added = len(merged) - len(existing)
    supa.table("articles_merged_finn_yahoo").upsert({
        "ticker":     ticker,
        "year_month": year_month,
        "articles":   merged,
        "source":     new_source,
        "cached_at":  datetime.now(timezone.utc).isoformat(),
    }).execute()
    logger.info(f"  Yahoo {ticker} {year_month}: +{added} articles ({len(merged)} total, source={new_source})")
    return added


def run_finnhub_refresh() -> dict[str, int]:
    """
    Cron 12h : refresh Finnhub pour le mois courant sur tous les tickers pinnés ≥ 3 users.
    Merge avec l'existant via _get_or_fetch_month (TTL 12h).
    Retourne {ticker: nb_articles} pour les tickers avec ≥1 article.
    """
    supa = _get_supabase()
    today = date.today()
    current_ym = f"{today.year:04d}-{today.month:02d}"

    eligible = _get_eligible_tickers(supa, min_users=3)
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
                    from services.overview_service import brutalise_new_articles, get_news
                    n = brutalise_new_articles(ticker)
                    logger.info(f"  DUMB gen {ticker} ({n} articles brutalised)")
                    get_news(ticker, force_24h=True)
                    logger.info(f"  OW refresh triggered {ticker} (+{added} articles)")
                except Exception as ow_err:
                    logger.warning(f"  DUMB+OW refresh failed {ticker}: {ow_err}")
        except Exception as e:
            logger.error(f"  ✗ Finnhub refresh {ticker}: {e}")

    return results


def _current_article_count(ticker: str, year_month: str, supa) -> int:
    row = supa.table("articles_merged_finn_yahoo") \
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
    supa.table("article_week_summaries") \
        .delete() \
        .eq("ticker", ticker) \
        .eq("year_month", year_month) \
        .execute()
    logger.info(f"  Purged weeklies {ticker} {year_month}")


def _purge_excess_weeklies(ticker: str, supa, max_weeks: int = 5) -> None:
    """Garde les max_weeks weeklies les plus récents, supprime le reste."""
    rows = supa.table("article_week_summaries") \
        .select("year_month, week_num, condensed_at") \
        .eq("ticker", ticker) \
        .order("year_month", desc=True) \
        .order("week_num", desc=True) \
        .execute()

    all_weeks = rows.data or []
    if len(all_weeks) <= max_weeks:
        return

    to_delete = all_weeks[max_weeks:]
    for w in to_delete:
        supa.table("article_week_summaries") \
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
        exists = supa.table("article_month_summaries") \
            .select("year_month") \
            .eq("ticker", ticker) \
            .eq("year_month", target_ym) \
            .maybe_single() \
            .execute()
        if exists.data:
            # Purge weeklies même si déjà généré
            _purge_weeklies_for_month(ticker, target_ym, supa)
            continue

        week_rows = supa.table("article_week_summaries") \
            .select("week_num, ai_summary, ai_summary_en, ai_summary_fr, article_count") \
            .eq("ticker", ticker) \
            .eq("year_month", target_ym) \
            .order("week_num") \
            .execute()

        if not week_rows.data or len(week_rows.data) < 2:
            continue

        try:
            src_col = "ai_summary_en" if _lang() == "en" else "ai_summary_fr"
            weeks_text = "\n".join(
                f"S{r['week_num']}: {r.get(src_col) or r.get('ai_summary_fr') or r.get('ai_summary', '')}" for r in week_rows.data
            )
            article_count = sum(r["article_count"] for r in week_rows.data)
            summary = _llm_call(_pick(MONTH_CONDENSE_PROMPT, MONTH_CONDENSE_PROMPT_EN).format(
                ticker=ticker,
                label=_month_label(target_ym),
                weeks_text=weeks_text,
            ), max_tokens=500)

            supa.table("article_month_summaries").upsert({
                "ticker":        ticker,
                "year_month":    target_ym,
                _summary_col():  summary,
                "article_count": article_count,
                "condensed_at":  datetime.now(timezone.utc).isoformat(),
            }).execute()
            logger.info(f"  ✓ Monthly calendaire {ticker} {target_ym}")

            # Purger les weeklies de ce mois
            _purge_weeklies_for_month(ticker, target_ym, supa)

        except Exception as e:
            logger.error(f"  ✗ Monthly calendaire {ticker} {target_ym}: {e}")


# ─── Monthly on-demand (rolling 4 semaines) ───────────────────────────────────

def get_weekly_ondemand(ticker: str) -> str | None:
    """
    Génère un résumé IA de la semaine en cours depuis stock_news_cache.news_raw (Yahoo 7j).
    Stocke dans article_week_summaries et retourne le texte.
    """
    ticker = ticker.upper()
    supa = _get_supabase()

    row = supa.table("stock_news_cache").select("news_raw").eq("ticker", ticker).maybe_single().execute()
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
        ), max_tokens=400)
    except Exception as e:
        logger.error(f"Weekly ondemand {ticker}: {e}")
        return None

    supa.table("article_week_summaries").upsert({
        "ticker":       ticker,
        "year_month":   year_month,
        "week_num":     week_num,
        _summary_col(): summary,
        "top_articles": [],
        "article_count": len(articles),
        "condensed_at": now.isoformat(),
    }).execute()
    return summary


def get_monthly_ondemand(ticker: str) -> str | None:
    """
    Génère un résumé mensuel rolling depuis les 4 derniers weekly summaries.
    Appelé à la demande (premium CTA). Pas de cache persistant.
    """
    ticker = ticker.upper()
    supa = _get_supabase()

    rows = supa.table("article_week_summaries") \
        .select("year_month, week_num, ai_summary, ai_summary_en, ai_summary_fr") \
        .eq("ticker", ticker) \
        .order("year_month", desc=True) \
        .order("week_num", desc=True) \
        .limit(4) \
        .execute()

    weeks = rows.data or []
    if not weeks:
        return None

    src_col = "ai_summary_en" if _lang() == "en" else "ai_summary_fr"
    label = "the last 4 weeks" if _lang() == "en" else "ces 4 dernières semaines"
    weeks_text = "\n".join(
        f"S{w['week_num']} {w['year_month']}: {w.get(src_col) or w.get('ai_summary_fr') or w.get('ai_summary', '')}"
        for w in reversed(weeks)
    )

    try:
        return _llm_call(_pick(MONTH_CONDENSE_PROMPT, MONTH_CONDENSE_PROMPT_EN).format(
            ticker=ticker,
            label=label,
            weeks_text=weeks_text,
        ), max_tokens=500)
    except Exception as e:
        logger.error(f"Monthly ondemand {ticker}: {e}")
        return None
