import httpx
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


def get_event_news(ticker: str, date: str, change_pct: float = 0.0) -> dict:
    ticker = ticker.upper()
    supa = _get_supabase()

    # Cache Supabase permanent
    row = supa.table("event_causes") \
        .select("articles, cause, cause_en") \
        .eq("ticker", ticker) \
        .eq("date", date) \
        .maybe_single() \
        .execute()

    lang = os.environ.get("LLM_LANG", "en")
    cached = row.data if row else None
    if cached:
        cause = cached.get("cause_en" if lang == "en" else "cause")
        if cause:
            return {"date": date, "articles": cached["articles"], "cause": cause}

    # Fetch Finnhub T-3 → T+3
    dt = datetime.fromisoformat(date[:10])
    from_date = (dt - timedelta(days=3)).strftime("%Y-%m-%d")
    to_date   = (dt + timedelta(days=3)).strftime("%Y-%m-%d")

    r = httpx.get(
        "https://finnhub.io/api/v1/company-news",
        params={"symbol": ticker, "from": from_date, "to": to_date, "token": os.environ["FINNHUB_API_KEY"]},
        timeout=10,
    )
    r.raise_for_status()
    raw = r.json()[:12]

    articles = [
        {
            "headline": a["headline"],
            "source":   a["source"],
            "url":      a["url"],
            "datetime": a["datetime"],
            "summary":  a.get("summary", ""),
        }
        for a in raw
        if a.get("headline")
    ]

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

    cause_field = "cause_en" if lang == "en" else "cause"
    upsert_data = {
        "ticker":    ticker,
        "date":      date,
        "articles":  articles,
        cause_field: cause,
        "llm_source": llm_source,
        "cached_at": datetime.now(timezone.utc).isoformat(),
    }
    supa.table("event_causes").upsert(upsert_data).execute()

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
        resp = llm_call([{"role": "user", "content": prompt}], groq_model=GROQ_LIGHT, max_tokens=280, timeout=25)
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
        )
    except Exception:
        return None
