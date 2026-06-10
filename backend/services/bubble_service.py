"""
BUB — cause IA des événements chart (dots peaks/valleys).

get_or_gen_bub(ticker, date, change_pct) :
  - Cherche articles Finnhub T-3→T+3 (fallback T-6→T+6 si 0 articles)
  - Envoie au LLM : prix T-5→T+3 + benchmark (S&P/CAC/DAX/FTSE selon exchange) + headlines
  - Cache permanent dans bub_cache (cause_en + cause_fr)
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


def get_or_gen_bub(ticker: str, date: str, change_pct: float = 0.0) -> dict:
    ticker = ticker.upper()
    supa = _get_supabase()

    # Cache Supabase permanent
    row = supa.table("bub_cache") \
        .select("articles, cause_fr, cause_en") \
        .eq("ticker", ticker) \
        .eq("date", date) \
        .maybe_single() \
        .execute()

    lang = os.environ.get("LLM_LANG", "en")
    cached = row.data if row else None
    if cached:
        cause = cached.get("cause_en" if lang == "en" else "cause_fr")
        if cause:
            return {"date": date, "articles": cached["articles"], "cause": cause}

    # Lire articles (merge Finn+Yahoo) pour la fenêtre T-3→T+3
    dt = datetime.fromisoformat(date[:10])
    from_dt = dt - timedelta(days=3)
    to_dt   = dt + timedelta(days=3)
    from_ts = int(from_dt.replace(tzinfo=timezone.utc).timestamp())
    to_ts   = int((to_dt + timedelta(days=1)).replace(tzinfo=timezone.utc).timestamp())

    # Déterminer les year_months couverts (1 ou 2 selon le mois)
    year_months: set[str] = set()
    cursor = from_dt.replace(day=1)
    while cursor <= to_dt:
        year_months.add(cursor.strftime("%Y-%m"))
        cursor = (cursor.replace(day=28) + timedelta(days=4)).replace(day=1)

    raw_articles: list[dict] = []
    for ym in sorted(year_months):
        r = supa.table("articles") \
            .select("articles") \
            .eq("ticker", ticker) \
            .eq("year_month", ym) \
            .maybe_single() \
            .execute()
        if r and r.data and r.data.get("articles"):
            raw_articles.extend(r.data["articles"])

    # Filtrer T-3→T+3, trier par proximité à la date, max 12
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

    # Fallback ±6j si rien dans ±3j
    if not articles:
        from_ts6 = int((dt - timedelta(days=6)).replace(tzinfo=timezone.utc).timestamp())
        to_ts6   = int((dt + timedelta(days=7)).replace(tzinfo=timezone.utc).timestamp())
        year_months6: set[str] = set()
        cursor = (dt - timedelta(days=6)).replace(day=1)
        while cursor <= dt + timedelta(days=6):
            year_months6.add(cursor.strftime("%Y-%m"))
            cursor = (cursor.replace(day=28) + timedelta(days=4)).replace(day=1)
        for ym in sorted(year_months6 - year_months):
            r = supa.table("articles") \
                .select("articles") \
                .eq("ticker", ticker) \
                .eq("year_month", ym) \
                .maybe_single() \
                .execute()
            if r and r.data and r.data.get("articles"):
                raw_articles.extend(r.data["articles"])
        articles = [
            {
                "headline": a["headline"],
                "source":   a.get("source", ""),
                "url":      a.get("url", ""),
                "datetime": a.get("datetime", 0),
                "summary":  a.get("summary", ""),
            }
            for a in raw_articles
            if a.get("headline") and from_ts6 <= a.get("datetime", 0) <= to_ts6
        ]
    event_ts = int(dt.replace(tzinfo=timezone.utc).timestamp())
    articles.sort(key=lambda a: abs(a["datetime"] - event_ts))
    articles = articles[:12]

    # Contexte prix + benchmark
    benchmark_name, benchmark_ticker = _get_benchmark(ticker)
    price_context = _get_price_context(ticker, date, benchmark_ticker, benchmark_name)

    from services.llm_service import llm_text as _llm_text
    cause_result = None
    llm_source = None
    if articles:
        try:
            from services.llm_service import llm_call as _llm_call
            # _groq_cause retourne du texte — on passe par llm_call pour avoir _llm_source
            cause_result, llm_source = _groq_cause_with_source(ticker, date, change_pct, articles, price_context, lang)
        except Exception:
            cause_result = None

    cause = cause_result
    result = {"date": date, "articles": articles, "cause": cause}

    cause_field = "cause_en" if lang == "en" else "cause_fr"
    upsert_data = {
        "ticker":    ticker,
        "date":      date,
        "articles":  articles,
        cause_field: cause,
        "llm_source": llm_source,
        "cached_at": datetime.now(timezone.utc).isoformat(),
    }
    supa.table("bub_cache").upsert(upsert_data).execute()

    return result


def _groq_cause_with_source(ticker: str, date: str, change_pct: float, articles: list[dict], price_context: str, lang: str) -> tuple[str | None, str | None]:
    """Retourne (cause_text, llm_source)."""
    direction = "hausse" if change_pct >= 0 else "baisse"
    sign = "+" if change_pct >= 0 else ""
    headlines = "\n".join(f"- {a['headline']}" for a in articles[:12])
    price_section = f"\n{price_context}\n" if price_context else ""
    if lang == "en":
        prompt = f"""Ticker: {ticker} | Date: {date} | Move: {sign}{change_pct:.2f}% ({'up' if change_pct >= 0 else 'down'})
{price_section}
Articles published around this date ({len(articles)}):
{headlines}

Based on the price movement, market context and articles, explain in 3-4 sentences what drove this move.
Note whether the move is stock-specific or correlated with the broader market.
Start directly with the facts, no introduction."""
    else:
        prompt = f"""Ticker : {ticker} | Date : {date} | Mouvement : {sign}{change_pct:.2f}% ({direction})
{price_section}
Articles publiés autour de cette date ({len(articles)}) :
{headlines}

En te basant sur le mouvement de cours, le contexte du marché et les articles, explique en 3-4 phrases ce qui explique ce mouvement.
Intègre si le move est spécifique au titre ou corrélé au marché général.
Commence directement par les faits, pas d'introduction."""
    try:
        from services.llm_service import llm_call
        resp = llm_call([{"role": "user", "content": prompt}], groq_model=GROQ_LIGHT, max_tokens=280, timeout=25, caller="BUB", item_id=ticker, cascade="bub_prod")
        return resp["choices"][0]["message"]["content"].strip(), resp.get("_llm_source")
    except Exception:
        return None, None


def _groq_cause(ticker: str, date: str, change_pct: float, articles: list[dict], price_context: str) -> str | None:
    direction = "hausse" if change_pct >= 0 else "baisse"
    sign = "+" if change_pct >= 0 else ""
    headlines = "\n".join(f"- {a['headline']}" for a in articles[:12])

    price_section = f"\n{price_context}\n" if price_context else ""

    lang = os.environ.get("LLM_LANG", "fr")
    if lang == "en":
        prompt = f"""Ticker: {ticker} | Date: {date} | Move: {sign}{change_pct:.2f}% ({'up' if change_pct >= 0 else 'down'})
{price_section}
Articles published around this date ({len(articles)}):
{headlines}

Based on the price movement, market context and articles, explain in 3-4 sentences what drove this move.
Note whether the move is stock-specific or correlated with the broader market.
Start directly with the facts, no introduction."""
    else:
        prompt = f"""Ticker : {ticker} | Date : {date} | Mouvement : {sign}{change_pct:.2f}% ({direction})
{price_section}
Articles publiés autour de cette date ({len(articles)}) :
{headlines}

En te basant sur le mouvement de cours, le contexte du marché et les articles, explique en 3-4 phrases ce qui explique ce mouvement.
Intègre si le move est spécifique au titre ou corrélé au marché général.
Commence directement par les faits, pas d'introduction."""

    try:
        return llm_text(
            [{"role": "user", "content": prompt}],
            groq_model=GROQ_LIGHT,
            max_tokens=280,
            timeout=25,
            caller="BUB",
            item_id=ticker,
            cascade="bub_prod",
        )
    except Exception:
        return None
