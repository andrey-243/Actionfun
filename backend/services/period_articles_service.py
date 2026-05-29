import httpx
import os
import logging
from datetime import date, datetime, timezone, timedelta
from calendar import monthrange
from supabase import create_client
import hashlib

logger = logging.getLogger(__name__)

ACTIVE_MONTH_TTL_HOURS = 24

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
    Retourne (articles_raw, source) depuis article_archive ou Finnhub.
    source = 'finnhub' | 'yahoo'
    """
    row = supa.table("article_archive") \
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

    # Fetch Finnhub → dedup+merge extra_sources avant stockage
    raw = _fetch_finnhub_month(ticker, year_month)
    source = "finnhub" if raw else "yahoo"
    articles = _dedup_by_headline(raw)  # stocke propre avec extra_sources

    supa.table("article_archive").upsert({
        "ticker":     ticker,
        "year_month": year_month,
        "articles":   articles,
        "source":     source,
        "cached_at":  datetime.now(timezone.utc).isoformat(),
    }).execute()
    logger.info(f"Finnhub fetched {len(raw)}→{len(articles)} articles for {ticker} {year_month} (source={source})")
    return articles, source


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

def get_period_articles(ticker: str, period: str) -> dict:
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
        return _get_monthly_with_weeks(ticker, year_months, supa)

    # 6mo / 1y : récupérer month summaries AI (optionnel)
    month_rows = supa.table("article_month_summaries") \
        .select("year_month, ai_summary, article_count") \
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
        filtered = [a for a in raw if a.get("datetime", 0) >= cutoff_ts and ym_start <= a.get("datetime", 0) <= ym_end]
        filtered.sort(key=lambda a: a.get("datetime", 0), reverse=True)
        top = [_normalize_finnhub(a) for a in filtered[:MAX_PER_MONTH]]

        if not top and not ai:
            continue

        summaries.append({
            "type":          "month",
            "year_month":    ym,
            "label":         _month_label(ym),
            "ai_summary":    ai["ai_summary"] if ai else None,
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
            seen[key] = {**a, "extra_sources": []}
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


def _get_monthly_with_weeks(ticker: str, year_months: list[str], supa) -> dict:
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
            top = [_normalize_finnhub(a) for a in all_articles[:15]]
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
                top = [_normalize_finnhub(a) for a in articles[:10]]
                summaries.append({
                    "type":          "week",
                    "year_month":    ym,
                    "week_num":      wnum,
                    "label":         _week_label(ym, wnum),
                    "skewed":        False,
                    "ai_summary":    ai["ai_summary"] if ai else None,
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


def get_all_available(ticker: str) -> dict:
    """
    ALL : year summaries → month summaries → raw articles récents.
    Density 3/mois pour les raw.
    """
    ticker = ticker.upper()
    supa = _get_supabase()

    year_rows = supa.table("article_year_summaries") \
        .select("year, ai_summary, condensed_at") \
        .eq("ticker", ticker) \
        .order("year", desc=True) \
        .execute()

    month_rows = supa.table("article_month_summaries") \
        .select("year_month, ai_summary, article_count, condensed_at") \
        .eq("ticker", ticker) \
        .order("year_month", desc=True) \
        .execute()

    raw_rows = supa.table("article_archive") \
        .select("year_month, articles, source, cached_at") \
        .eq("ticker", ticker) \
        .order("year_month", desc=True) \
        .execute()

    # Mois avec month summary → pas de raw
    month_summary_set = {r["year_month"] for r in (month_rows.data or [])}

    all_raw = []
    for row in (raw_rows.data or []):
        if row["year_month"] in month_summary_set:
            continue
        selected = _apply_density(row["articles"], slot_days=30, max_per_slot=3)
        all_raw.extend([_normalize_finnhub(a) for a in selected])

    all_raw.sort(key=lambda a: a.get("pub_date", ""), reverse=True)

    year_summaries = [
        {"type": "year", "year": r["year"], "label": r["year"], "ai_summary": r["ai_summary"]}
        for r in (year_rows.data or [])
    ]
    month_summaries = [
        {
            "type":          "month",
            "year_month":    r["year_month"],
            "label":         _month_label(r["year_month"]),
            "ai_summary":    r["ai_summary"],
            "article_count": r["article_count"],
        }
        for r in (month_rows.data or [])
    ]

    summaries = year_summaries + month_summaries

    return {
        "ticker":    ticker,
        "period":    "all",
        "articles":  all_raw,
        "summaries": summaries,
        "condensed": [{"year_month": s.get("year_month", s.get("year", "")), "summary": s["ai_summary"], "article_count": s.get("article_count", 0)} for s in month_summaries],
    }


# ─── Crons de condensation ────────────────────────────────────────────────────

WEEK_CONDENSE_PROMPT = """Tu es un analyste financier senior. Voici des articles sur {ticker} pour la semaine {label}.

Articles :
{articles_text}

Rédige en français un résumé analytique de 4-6 phrases couvrant : les événements majeurs (résultats, annonces, deals), les chiffres clés, l'impact sur l'action et l'évolution du sentiment.
Réponds UNIQUEMENT avec le texte du résumé en français, sans titre ni formatage."""

MONTH_CONDENSE_PROMPT = """Tu es un analyste financier senior. Voici l'actualité sur {ticker} pour {label}.

{weeks_text}

Synthétise en français en 6-8 phrases : événements majeurs du mois, chiffres clés, contexte macro/sectoriel pertinent, catalyseurs et risques identifiés, évolution du sentiment investisseurs.
Réponds UNIQUEMENT avec le texte du résumé en français, sans titre ni formatage."""

YEAR_CONDENSE_PROMPT = """Tu es un analyste financier senior. Voici les résumés mensuels sur {ticker} pour l'année {year}.

{months_text}

Synthétise l'année en français en 8-10 phrases : temps forts, tendances de fond, pivots stratégiques majeurs, performance relative, sentiment global et perspectives.
Réponds UNIQUEMENT avec le texte du résumé en français, sans titre ni formatage."""


def _llm_call(prompt: str, max_tokens: int = 400) -> str:
    from services.llm_service import llm_text
    return llm_text(
        [{"role": "user", "content": prompt}],
        groq_model="llama-3.3-70b-versatile",
        max_tokens=max_tokens,
        timeout=45,
    )


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
        row = supa.table("article_archive") \
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
            articles_text = "\n".join(f"- {a['headline']}" for a in week_articles[:30])
            summary = _llm_call(WEEK_CONDENSE_PROMPT.format(
                ticker=ticker,
                label=label,
                articles_text=articles_text,
            ))
            top_articles = [_normalize_finnhub(a) for a in sorted(week_articles, key=lambda x: len(x.get("summary", "")), reverse=True)[:10]]
            supa.table("article_week_summaries").upsert({
                "ticker":        ticker,
                "year_month":    target_ym,
                "week_num":      target_wnum,
                "ai_summary":    summary,
                "top_articles":  top_articles,
                "article_count": len(week_articles),
                "condensed_at":  datetime.now(timezone.utc).isoformat(),
            }).execute()
            logger.info(f"  ✓ Weekly {ticker} {target_ym} W{target_wnum} ({len(week_articles)} articles)")
            # Purger les weeklies excédentaires (max 5)
            _purge_excess_weeklies(ticker, supa, max_weeks=5)
        except Exception as e:
            logger.error(f"  ✗ Weekly {ticker} {target_ym} W{target_wnum}: {e}")


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
                .select("week_num, ai_summary, article_count") \
                .eq("ticker", ticker) \
                .eq("year_month", target_ym) \
                .order("week_num") \
                .execute()

            if week_rows.data and len(week_rows.data) >= 3:
                weeks_text = "\n".join(
                    f"W{r['week_num']}: {r['ai_summary']}"
                    for r in week_rows.data
                )
                article_count = sum(r["article_count"] for r in week_rows.data)
                summary = _llm_call(MONTH_CONDENSE_PROMPT.format(
                    ticker=ticker,
                    label=_month_label(target_ym),
                    weeks_text=weeks_text,
                ), max_tokens=500)
            else:
                # Fallback : condenser depuis raw
                row = supa.table("article_archive") \
                    .select("articles") \
                    .eq("ticker", ticker) \
                    .eq("year_month", target_ym) \
                    .maybe_single() \
                    .execute()
                if not row.data or not row.data["articles"]:
                    continue
                raw = row.data["articles"]
                article_count = len(raw)
                articles_text = "\n".join(f"- {a['headline']}" for a in raw[:40])
                summary = _llm_call(MONTH_CONDENSE_PROMPT.format(
                    ticker=ticker,
                    label=_month_label(target_ym),
                    weeks_text=articles_text,
                ), max_tokens=500)

            supa.table("article_month_summaries").upsert({
                "ticker":        ticker,
                "year_month":    target_ym,
                "ai_summary":    summary,
                "article_count": article_count,
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
            .select("year_month, ai_summary") \
            .eq("ticker", ticker) \
            .like("year_month", f"{target_year}-%") \
            .order("year_month") \
            .execute()

        if not month_rows.data or len(month_rows.data) < 6:
            continue  # Pas assez de data pour une année complète

        try:
            months_text = "\n".join(
                f"{_month_label(r['year_month'])}: {r['ai_summary']}"
                for r in month_rows.data
            )
            summary = _llm_call(YEAR_CONDENSE_PROMPT.format(
                ticker=ticker,
                year=target_year,
                months_text=months_text,
            ), max_tokens=600)

            supa.table("article_year_summaries").upsert({
                "ticker":       ticker,
                "year":         target_year,
                "ai_summary":   summary,
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


def run_yahoo_accumulation():
    """
    Cron daily (2h) : accumule les articles Yahoo pour les tickers
    dont Finnhub ne couvre pas (source='yahoo') et pinnés ≥ 3 users.
    Déduplique par URL.
    """
    supa = _get_supabase()

    eligible = _get_eligible_tickers(supa, min_users=3)
    if not eligible:
        return

    today = date.today()
    current_ym = f"{today.year:04d}-{today.month:02d}"

    # Tickers yahoo seulement
    yahoo_tickers_rows = supa.table("article_archive") \
        .select("ticker, source") \
        .eq("source", "yahoo") \
        .in_("ticker", list(eligible)) \
        .execute()
    yahoo_tickers = {r["ticker"] for r in (yahoo_tickers_rows.data or [])}

    # Si un ticker n'a jamais été fetché, vérifier Finnhub d'abord
    for ticker in eligible - yahoo_tickers:
        try:
            articles, source = _get_or_fetch_month(ticker, current_ym, supa)
            if source == "yahoo":
                yahoo_tickers.add(ticker)
        except Exception:
            pass

    logger.info(f"Yahoo accumulation: {len(yahoo_tickers)} tickers à accumuler")

    for ticker in yahoo_tickers:
        try:
            _accumulate_yahoo(ticker, current_ym, supa)
        except Exception as e:
            logger.error(f"  ✗ Yahoo accumulation {ticker}: {e}")


def _accumulate_yahoo(ticker: str, year_month: str, supa) -> None:
    """Fetch Yahoo news, déduplique par URL, upsert dans article_archive."""
    import yfinance as yf
    t = yf.Ticker(ticker)
    raw = t.get_news(count=100)

    # Format normalisé pour archive (compatible _normalize_finnhub via pub_date → datetime)
    new_articles = []
    for n in raw:
        c = n.get("content", {})
        pub_date = c.get("pubDate", "")
        try:
            ts = int(datetime.fromisoformat(pub_date.replace("Z", "+00:00")).timestamp()) if pub_date else 0
        except Exception:
            ts = 0
        url = (c.get("clickThroughUrl") or {}).get("url", "") or (c.get("canonicalUrl") or {}).get("url", "")
        if not url:
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
        return

    # Lire le cache existant
    row = supa.table("article_archive") \
        .select("articles") \
        .eq("ticker", ticker) \
        .eq("year_month", year_month) \
        .maybe_single() \
        .execute()

    existing = row.data["articles"] if row.data else []
    existing_urls = {a.get("url") for a in existing}

    # Ajouter seulement les nouveaux
    added = [a for a in new_articles if a.get("url") not in existing_urls]
    if not added:
        logger.info(f"  Yahoo {ticker} {year_month}: 0 nouveaux articles")
        return

    merged = existing + added
    supa.table("article_archive").upsert({
        "ticker":     ticker,
        "year_month": year_month,
        "articles":   merged,
        "source":     "yahoo",
        "cached_at":  datetime.now(timezone.utc).isoformat(),
    }).execute()
    logger.info(f"  Yahoo {ticker} {year_month}: +{len(added)} articles ({len(merged)} total)")


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
            .select("week_num, ai_summary, article_count") \
            .eq("ticker", ticker) \
            .eq("year_month", target_ym) \
            .order("week_num") \
            .execute()

        if not week_rows.data or len(week_rows.data) < 2:
            continue

        try:
            weeks_text = "\n".join(
                f"S{r['week_num']}: {r['ai_summary']}" for r in week_rows.data
            )
            article_count = sum(r["article_count"] for r in week_rows.data)
            summary = _llm_call(MONTH_CONDENSE_PROMPT.format(
                ticker=ticker,
                label=_month_label(target_ym),
                weeks_text=weeks_text,
            ), max_tokens=500)

            supa.table("article_month_summaries").upsert({
                "ticker":        ticker,
                "year_month":    target_ym,
                "ai_summary":    summary,
                "article_count": article_count,
                "condensed_at":  datetime.now(timezone.utc).isoformat(),
            }).execute()
            logger.info(f"  ✓ Monthly calendaire {ticker} {target_ym}")

            # Purger les weeklies de ce mois
            _purge_weeklies_for_month(ticker, target_ym, supa)

        except Exception as e:
            logger.error(f"  ✗ Monthly calendaire {ticker} {target_ym}: {e}")


# ─── Monthly on-demand (rolling 4 semaines) ───────────────────────────────────

def get_monthly_ondemand(ticker: str) -> str | None:
    """
    Génère un résumé mensuel rolling depuis les 4 derniers weekly summaries.
    Appelé à la demande (premium CTA). Pas de cache persistant.
    """
    ticker = ticker.upper()
    supa = _get_supabase()

    rows = supa.table("article_week_summaries") \
        .select("year_month, week_num, ai_summary") \
        .eq("ticker", ticker) \
        .order("year_month", desc=True) \
        .order("week_num", desc=True) \
        .limit(4) \
        .execute()

    weeks = rows.data or []
    if not weeks:
        return None

    weeks_text = "\n".join(
        f"S{w['week_num']} {w['year_month']}: {w['ai_summary']}"
        for w in reversed(weeks)
    )

    try:
        return _llm_call(MONTH_CONDENSE_PROMPT.format(
            ticker=ticker,
            label="ces 4 dernières semaines",
            weeks_text=weeks_text,
        ), max_tokens=500)
    except Exception as e:
        logger.error(f"Monthly ondemand {ticker}: {e}")
        return None
