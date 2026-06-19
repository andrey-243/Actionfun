"""
BUB — cause IA des événements chart (dots peaks/valleys).

get_or_gen_bub(ticker, date, change_pct) :
  - Cherche articles Finnhub T-3→T+3 (fallback T-6→T+6 si 0 articles)
  - Envoie au LLM : prix T-5→T+3 + benchmark (S&P/CAC/DAX/FTSE selon exchange) + headlines
  - Cache permanent dans bub_cache (bub_en + bub_fr)
  - Benchmark déterminé depuis ow_cache.exchange
"""
import yfinance as yf
import os
from datetime import datetime, timedelta, timezone
from supabase import create_client
from services.llm_service import llm_text

GROQ_LIGHT = "llama-3.1-8b-instant"

BENCHMARK_BY_EXCHANGE = {
    "NMS": ("S&P 500", "^GSPC"),
    "NYQ": ("S&P 500", "^GSPC"),
    "NGM": ("S&P 500", "^GSPC"),
    "PCX": ("S&P 500", "^GSPC"),
    "PAR": ("CAC 40", "^FCHI"),
    "GER": ("DAX", "^GDAXI"),
    "XETRA": ("DAX", "^GDAXI"),
    "LSE": ("FTSE 100", "^FTSE"),
    "AMS": ("Euro Stoxx 50", "^STOXX50E"),
    "MIL": ("Euro Stoxx 50", "^STOXX50E"),
    "MCE": ("Euro Stoxx 50", "^STOXX50E"),
}


def _get_supabase():
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])


def _get_benchmark(ticker: str) -> tuple[str, str]:
    """Retourne (nom, ticker_yfinance) du benchmark selon l'exchange."""
    try:
        info = yf.Ticker(ticker).fast_info
        exchange = getattr(info, "exchange", "") or ""
        return BENCHMARK_BY_EXCHANGE.get(exchange, ("Euro Stoxx 50", "^STOXX50E"))
    except Exception:
        return ("S&P 500", "^GSPC")


def _get_price_context(ticker: str, date: str, benchmark_ticker: str, benchmark_name: str) -> str:
    """Retourne le contexte prix T-5→T+3 + benchmark pour le prompt Groq."""
    try:
        dt = datetime.fromisoformat(date[:10])
        start = (dt - timedelta(days=7)).strftime("%Y-%m-%d")
        end = (dt + timedelta(days=5)).strftime("%Y-%m-%d")

        hist = yf.Ticker(ticker).history(start=start, end=end, interval="1d")
        bench = yf.Ticker(benchmark_ticker).history(start=start, end=end, interval="1d")

        if hist.empty:
            return ""

        # Formatter l'historique prix
        price_lines = []
        for i, (idx, row) in enumerate(hist.iterrows()):
            day_str = idx.strftime("%d %b")
            open_p, close_p = row["Open"], row["Close"]
            pct = (close_p - open_p) / open_p * 100
            sign = "+" if pct >= 0 else ""
            marker = "  ← événement" if idx.date() == dt.date() else ""
            price_lines.append(f"  {day_str} : ${open_p:.2f} → ${close_p:.2f} ({sign}{pct:.1f}%){marker}")

        # Benchmark le jour J
        bench_line = ""
        if not bench.empty:
            target_date = dt.date()
            for idx, row in bench.iterrows():
                if idx.date() == target_date:
                    b_open, b_close = row["Open"], row["Close"]
                    b_pct = (b_close - b_open) / b_open * 100
                    sign = "+" if b_pct >= 0 else ""
                    bench_line = f"\n{benchmark_name} ce jour : {sign}{b_pct:.1f}%"
                    if abs(b_pct) < 0.5:
                        bench_line += " → move spécifique au titre, pas macro"
                    elif b_pct * (1 if b_close > b_open else -1) > 0:
                        bench_line += " → move en partie corrélé au marché"
                    break

        return "Historique cours (T-5 → T+3) :\n" + "\n".join(price_lines) + bench_line

    except Exception:
        return ""


def get_or_gen_bub(ticker: str, date: str, change_pct: float = 0.0, cascade: str = "bub_prod",
                   prefetched_cache: dict | None = None,
                   in_1w: bool | None = None, in_1m: bool | None = None,
                   in_6m: bool | None = None, in_1y: bool | None = None,
                   force_regen: bool = False) -> dict:
    ticker = ticker.upper()
    supa = _get_supabase()

    lang = os.environ.get("LLM_LANG", "en")
    cause_field = "bub_en" if lang == "en" else "bub_fr"

    # Cache : pre-fetché par le batch ou SELECT individuel
    if prefetched_cache is not None:
        cached = prefetched_cache
    else:
        row = supa.table("bub_cache") \
            .select("bub_fr, bub_en, in_6m, in_1y") \
            .eq("ticker", ticker) \
            .eq("date", date) \
            .maybe_single() \
            .execute()
        cached = row.data if row else None

    # Toujours fetcher les articles depuis articles table (bub_cache.articles supprimé)
    dt = datetime.fromisoformat(date[:10])
    from_dt = dt - timedelta(days=6)
    to_dt   = dt + timedelta(days=6)
    from_ts = int(from_dt.replace(tzinfo=timezone.utc).timestamp())
    to_ts   = int((to_dt + timedelta(days=1)).replace(tzinfo=timezone.utc).timestamp())

    year_months: set[str] = set()
    cursor = from_dt.replace(day=1)
    while cursor <= to_dt:
        year_months.add(cursor.strftime("%Y-%m"))
        cursor = (cursor.replace(day=28) + timedelta(days=4)).replace(day=1)

    _turso_enabled = bool(os.environ.get("TURSO_URL"))

    raw_articles: list[dict] = []
    for ym in sorted(year_months):
        r = supa.table("articles") \
            .select("articles") \
            .eq("ticker", ticker) \
            .eq("year_month", ym) \
            .maybe_single() \
            .execute()
        if r and r.data and r.data.get("articles"):
            ym_articles = r.data["articles"]
            if _turso_enabled and ym_articles and not ym_articles[0].get("summary"):
                try:
                    from services.articles_store import fetch_heavy, merge_heavy_into_articles
                    heavy = fetch_heavy(ticker, ym)
                    if heavy:
                        ym_articles = merge_heavy_into_articles(ym_articles, heavy)
                except Exception:
                    pass
            raw_articles.extend(ym_articles)

    articles = [
        {
            "headline": a["headline"],
            "source":   a.get("source", ""),
            "url":      a.get("url", ""),
            "datetime": a.get("datetime", 0),
            "summary":  a.get("summary", ""),
        }
        for a in raw_articles
        if a.get("headline") and from_ts <= a.get("datetime", 0) <= to_ts
    ]
    event_ts = int(dt.replace(tzinfo=timezone.utc).timestamp())
    articles.sort(key=lambda a: abs(a["datetime"] - event_ts))

    if cached and not force_regen:
        cause = cached.get(cause_field)
        if cause:
            needs_update: dict = {}
            for col, val in [("in_1w", in_1w), ("in_1m", in_1m), ("in_6m", in_6m), ("in_1y", in_1y)]:
                if val is not None and cached.get(col) != val:
                    needs_update[col] = val
            if needs_update:
                supa.table("bub_cache").update(needs_update).eq("ticker", ticker).eq("date", date).execute()
            return {"date": date, "articles": articles, "cause": cause, "llm_source": None}

    # Contexte prix + benchmark
    benchmark_name, benchmark_ticker = _get_benchmark(ticker)
    price_context = _get_price_context(ticker, date, benchmark_ticker, benchmark_name)

    from services.llm_service import llm_text as _llm_text
    weekly_breaker = _get_weekly_breaker(supa, ticker, date, lang)

    cause_result = None
    llm_source = None
    if articles:
        try:
            from services.llm_service import llm_call as _llm_call
            cause_result, llm_source = _groq_cause_with_source(ticker, date, change_pct, articles, price_context, lang, weekly_breaker, cascade=cascade)
        except Exception:
            cause_result = None

    cause = cause_result
    result = {"date": date, "articles": articles, "cause": cause, "llm_source": llm_source}

    upsert_data: dict = {
        "ticker":    ticker,
        "date":      date,
        cause_field: cause,
        ("llm_source_en" if lang == "en" else "llm_source_fr"): llm_source,
        "bub_cached_at_en": datetime.now(timezone.utc).isoformat(),
    }
    for col, val in [("in_1w", in_1w), ("in_1m", in_1m), ("in_6m", in_6m), ("in_1y", in_1y)]:
        if val is not None:
            upsert_data[col] = val
    supa.table("bub_cache").upsert(upsert_data).execute()

    return result


def _get_weekly_breaker(supa, ticker: str, date: str, lang: str) -> str | None:
    """Retourne le breaker weekly couvrant la date du dot, ou None si absent."""
    try:
        dt = datetime.fromisoformat(date[:10])
        year_month = dt.strftime("%Y-%m")
        day = dt.day
        week_num = 1 if day <= 7 else 2 if day <= 14 else 3 if day <= 21 else 4
        col = "bw_en" if lang == "en" else "bw_fr"
        row = supa.table("breaks_weekly") \
            .select(col) \
            .eq("ticker", ticker) \
            .eq("year_month", year_month) \
            .eq("week_num", week_num) \
            .maybe_single() \
            .execute()
        if row and row.data:
            return row.data.get(col)
    except Exception:
        pass
    return None


def _groq_cause_with_source(
    ticker: str, date: str, change_pct: float, articles: list[dict],
    price_context: str, lang: str, weekly_breaker: str | None = None,
    cascade: str = "bub_prod",
) -> tuple[str | None, str | None]:
    """Retourne (cause_text, llm_source)."""
    direction = "hausse" if change_pct >= 0 else "baisse"
    sign = "+" if change_pct >= 0 else ""

    with_sum = [a for a in articles if a.get("summary")]
    no_sum = [a for a in articles if not a.get("summary")]

    price_section = f"\n{price_context}\n" if price_context else ""

    def _articles_block(rich_label: str, lean_label: str) -> str:
        parts = []
        if with_sum:
            rich = "\n".join(f"- {a['headline']} | {a['summary']}" for a in with_sum)
            parts.append(f"{rich_label} ({len(with_sum)}):\n{rich}")
        if no_sum:
            lean = "\n".join(f"- {a['headline']}" for a in no_sum)
            parts.append(f"{lean_label} ({len(no_sum)}):\n{lean}")
        return "\n\n".join(parts)

    if lang == "en":
        weekly_section = f"\nWeekly summary for this period:\n{weekly_breaker}\n" if weekly_breaker else ""
        articles_block = _articles_block("Articles with context", "Additional headlines")
        prompt = f"""Ticker: {ticker} | Date: {date} | Move: {sign}{change_pct:.2f}% ({'up' if change_pct >= 0 else 'down'})
{price_section}{weekly_section}
{articles_block}

Based on the price movement, market context and articles, explain in 3-4 sentences what drove this move.
Note whether the move is stock-specific or correlated with the broader market.
Start directly with the facts, no introduction."""
    else:
        weekly_section = f"\nRésumé hebdomadaire de la période :\n{weekly_breaker}\n" if weekly_breaker else ""
        articles_block = _articles_block("Articles avec contexte", "Titres supplémentaires")
        prompt = f"""Ticker : {ticker} | Date : {date} | Mouvement : {sign}{change_pct:.2f}% ({direction})
{price_section}{weekly_section}
{articles_block}

En te basant sur le mouvement de cours, le contexte du marché et les articles, explique en 3-4 phrases ce qui explique ce mouvement.
Intègre si le move est spécifique au titre ou corrélé au marché général.
Commence directement par les faits, pas d'introduction."""

    try:
        from services.llm_service import llm_call
        kw: dict = dict(timeout=25, caller="BUB", item_id=ticker, cascade=cascade)
        if cascade != "promo":
            kw["max_tokens"] = 280
            kw["groq_model"] = GROQ_LIGHT
        resp = llm_call([{"role": "user", "content": prompt}], **kw)
        return resp["choices"][0]["message"]["content"].strip(), resp.get("_llm_source")
    except Exception:
        return None, None


