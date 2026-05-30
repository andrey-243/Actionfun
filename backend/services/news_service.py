import yfinance as yf
import json
import os
from datetime import datetime, timezone, timedelta
from supabase import create_client
from services.llm_service import llm_json

GROQ_HEAVY = "meta-llama/llama-4-scout-17b-16e-instruct"
GROQ_LIGHT = "llama-3.1-8b-instant"

CACHE_TTL_HOURS = 12
MIN_SUMMARY_CHARS = 80

BRUTALIZE_PROMPT = """Tu es un analyste financier senior qui résume des news boursières pour des investisseurs exigeants.

Ticker analysé : {ticker}
{stock_context}

Voici {count} articles/vidéos récents (dernière semaine). Pour chaque article numéroté :
1. Si l'article concerne directement {ticker} ou son secteur immédiat : donne un résumé analytique en 2-3 phrases (impact financier, chiffres clés, conséquences pour l'action). Connecte l'actualité aux mouvements de cours si pertinent.
2. Si l'article N'EST PAS pertinent pour {ticker} (parle d'une autre entreprise sans lien direct) : mets exactement "SKIP"

Ensuite donne :
RÉSUMÉ_GLOBAL: synthèse en 4-6 phrases couvrant la semaine écoulée — performance, catalyseurs, risques, positionnement. Intègre les données de cours.
SENTIMENT: BULLISH / BEARISH / NEUTRAL avec justification en 2 phrases

IMPORTANT : Réponds UNIQUEMENT en français, même si les articles sont en anglais.

Format JSON strict :
{{
  "articles": [
    {{"id": 0, "brutalised": "résumé en français ou SKIP"}},
    ...
  ],
  "summary": "synthèse 7 jours en français",
  "sentiment": "BULLISH",
  "sentiment_reason": "justification en français"
}}

Articles :
{articles_text}"""

BRUTALIZE_PROMPT_EN = """You are a senior financial analyst summarizing stock news for demanding investors.

Ticker: {ticker}
{stock_context}

Below are {count} recent articles/videos (past week). For each numbered article:
1. If the article directly concerns {ticker} or its immediate sector: provide a 2-3 sentence analytical summary (financial impact, key figures, consequences for the stock). Connect news to price movements where relevant.
2. If the article is NOT relevant to {ticker} (concerns another company with no direct link): output exactly "SKIP"

Then provide:
GLOBAL_SUMMARY: 4-6 sentence synthesis covering the week — performance, catalysts, risks, positioning. Integrate price data.
SENTIMENT: BULLISH / BEARISH / NEUTRAL with 2-sentence justification

Strict JSON format:
{{
  "articles": [
    {{"id": 0, "brutalised": "2-3 sentence summary or SKIP"}},
    ...
  ],
  "summary": "7-day synthesis in English",
  "sentiment": "BULLISH",
  "sentiment_reason": "justification in English"
}}

Articles:
{articles_text}"""

SUMMARY_24H_PROMPT = """Tu es un analyste financier senior.

Ticker : {ticker}
Fenêtre : dernières 24 heures
{stock_context}

Voici les résumés des actualités des dernières 24h :
{articles_text}

Sur la base de ces articles ET des données de cours, donne en français :
- Une synthèse analytique en 3-5 phrases : ce qui s'est passé dans les 24h, lien entre actualité et mouvement de cours, catalyseurs immédiats
- Un sentiment : BULLISH / BEARISH / NEUTRAL avec justification en 1-2 phrases qui intègre la performance du jour

IMPORTANT : Connecte explicitement les nouvelles aux mouvements de cours (ex: "malgré X, l'action a reculé de Y% suite à...").

Format JSON strict :
{{"summary": "synthèse 24h en français", "sentiment": "BULLISH", "sentiment_reason": "justification en français"}}"""

SUMMARY_24H_PROMPT_EN = """You are a senior financial analyst.

Ticker: {ticker}
Window: last 24 hours
{stock_context}

Recent 24h news summaries:
{articles_text}

Based on these articles AND price data, provide in English:
- An analytical synthesis in 3-5 sentences: what happened in the last 24h, link between news and price movement, immediate catalysts
- Sentiment: BULLISH / BEARISH / NEUTRAL with 1-2 sentence justification integrating today's performance

IMPORTANT: Explicitly connect news to price movements.

Strict JSON format:
{{"summary": "24h synthesis in English", "sentiment": "BULLISH", "sentiment_reason": "justification in English"}}"""


def _get_supabase():
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])


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


def _get_stock_context(ticker: str) -> dict:
    """Récupère les stats cours + benchmark pour contextualiser les résumés IA."""
    try:
        t = yf.Ticker(ticker)
        fi = t.fast_info

        current = fi.last_price
        prev_close = fi.previous_close
        change_pct = ((current - prev_close) / prev_close * 100) if prev_close else None
        day_high = fi.day_high
        day_low = fi.day_low
        volume = fi.last_volume
        avg_volume = fi.three_month_average_volume
        vol_ratio = (volume / avg_volume) if avg_volume and volume else None

        # Perf 7J
        hist = t.history(period="5d", interval="1d")
        week_open = float(hist["Close"].iloc[0]) if len(hist) > 1 else None
        week_close = float(hist["Close"].iloc[-1]) if len(hist) > 0 else None
        week_high = float(hist["High"].max()) if len(hist) > 0 else None
        week_low = float(hist["Low"].min()) if len(hist) > 0 else None
        week_pct = ((week_close - week_open) / week_open * 100) if week_open and week_close else None

        # Benchmark adapté à l'exchange
        exchange = getattr(fi, "exchange", "") or ""
        bench_name, bench_ticker = BENCHMARK_BY_EXCHANGE.get(exchange, ("Euro Stoxx 50", "^STOXX50E"))
        bench_hist = yf.Ticker(bench_ticker).history(period="5d", interval="1d")
        bench_day_pct = None
        bench_week_pct = None
        if not bench_hist.empty:
            last = bench_hist.iloc[-1]
            bench_day_pct = (last["Close"] - last["Open"]) / last["Open"] * 100
            if len(bench_hist) > 1:
                b_open = float(bench_hist["Close"].iloc[0])
                b_close = float(bench_hist["Close"].iloc[-1])
                bench_week_pct = (b_close - b_open) / b_open * 100

        return {
            "current": current,
            "change_pct": change_pct,
            "day_high": day_high,
            "day_low": day_low,
            "vol_ratio": vol_ratio,
            "week_pct": week_pct,
            "week_high": week_high,
            "week_low": week_low,
            "bench_name": bench_name,
            "bench_day_pct": bench_day_pct,
            "bench_week_pct": bench_week_pct,
        }
    except Exception:
        return {}


def _format_stock_context_24h(ctx: dict) -> str:
    if not ctx:
        return ""
    lines = []
    if ctx.get("current") and ctx.get("change_pct") is not None:
        sign = "+" if ctx["change_pct"] >= 0 else ""
        lines.append(f"Cours : ${ctx['current']:.2f} ({sign}{ctx['change_pct']:.2f}% vs clôture précédente)")
    if ctx.get("day_high") and ctx.get("day_low"):
        lines.append(f"Range du jour : ${ctx['day_low']:.2f} – ${ctx['day_high']:.2f}")
    if ctx.get("vol_ratio") is not None:
        lines.append(f"Volume : {ctx['vol_ratio']:.1f}× la moyenne 3 mois")
    if ctx.get("bench_name") and ctx.get("bench_day_pct") is not None:
        sign = "+" if ctx["bench_day_pct"] >= 0 else ""
        lines.append(f"{ctx['bench_name']} aujourd'hui : {sign}{ctx['bench_day_pct']:.2f}%")
    return "\nDonnées de cours :\n" + "\n".join(lines) if lines else ""


def _format_stock_context_7d(ctx: dict) -> str:
    if not ctx:
        return ""
    lines = []
    if ctx.get("week_pct") is not None:
        sign = "+" if ctx["week_pct"] >= 0 else ""
        lines.append(f"Performance semaine : {sign}{ctx['week_pct']:.2f}%")
    if ctx.get("current") and ctx.get("change_pct") is not None:
        sign = "+" if ctx["change_pct"] >= 0 else ""
        lines.append(f"Cours actuel : ${ctx['current']:.2f} (J: {sign}{ctx['change_pct']:.2f}%)")
    if ctx.get("week_high") and ctx.get("week_low"):
        lines.append(f"Range semaine : ${ctx['week_low']:.2f} – ${ctx['week_high']:.2f}")
    if ctx.get("vol_ratio") is not None:
        lines.append(f"Volume moyen semaine : {ctx['vol_ratio']:.1f}× la moyenne 3 mois")
    if ctx.get("bench_name") and ctx.get("bench_week_pct") is not None:
        sign = "+" if ctx["bench_week_pct"] >= 0 else ""
        lines.append(f"{ctx['bench_name']} sur la semaine : {sign}{ctx['bench_week_pct']:.2f}%")
    return "\nDonnées de cours :\n" + "\n".join(lines) if lines else ""


def _fetch_news(ticker: str) -> tuple[list[dict], str | None]:
    """Returns (articles, website_domain)."""
    from urllib.parse import urlparse
    t = yf.Ticker(ticker)
    raw = t.get_news(count=100)

    website = None
    try:
        raw_url = (t.info or {}).get("website", "") or ""
        if raw_url:
            website = urlparse(raw_url).netloc.lstrip("www.") or None
    except Exception:
        pass

    articles = []
    for n in raw:
        c = n.get("content", {})
        summary = c.get("summary", "").strip()
        if len(summary) < MIN_SUMMARY_CHARS:
            continue

        articles.append({
            "id": c.get("id", ""),
            "type": c.get("contentType", "STORY"),
            "title": c.get("title", ""),
            "summary": summary,
            "source": c.get("provider", {}).get("displayName", ""),
            "url": (c.get("clickThroughUrl") or {}).get("url", "") or (c.get("canonicalUrl") or {}).get("url", ""),
            "thumbnail": (c.get("thumbnail") or {}).get("resolutions", [{}])[-1].get("url", "") if c.get("thumbnail") else "",
            "pub_date": c.get("pubDate", ""),
        })

    return articles, website


def _filter_24h(articles: list[dict]) -> list[dict]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    result = []
    for a in articles:
        try:
            pub = datetime.fromisoformat(a["pub_date"].replace("Z", "+00:00"))
            if pub >= cutoff:
                result.append(a)
        except Exception:
            pass
    return result


def _brutalise_with_groq(ticker: str, articles: list[dict], stock_context: str = "", lang: str = "fr") -> dict:
    articles_text = "\n\n".join(
        f"[{i}] {a['title']}\n{a['summary']}"
        for i, a in enumerate(articles)
    )
    prompt = (BRUTALIZE_PROMPT_EN if lang == "en" else BRUTALIZE_PROMPT).format(
        ticker=ticker,
        stock_context=stock_context,
        count=len(articles),
        articles_text=articles_text,
    )
    return llm_json(
        [{"role": "user", "content": prompt}],
        groq_model=GROQ_HEAVY,
        max_tokens=3000,
        timeout=60,
    )


def _summary_24h_with_groq(ticker: str, articles_24h: list[dict], stock_context: str = "", lang: str = "fr") -> dict:
    if not articles_24h:
        return {"summary": "", "sentiment": "NEUTRAL", "sentiment_reason": "No news in the last 24 hours." if lang == "en" else "Aucune actualité dans les 24 dernières heures."}

    articles_text = "\n".join(
        f"- {a['brutalised']}" for a in articles_24h if a.get("brutalised")
    )
    if not articles_text:
        return {"summary": "", "sentiment": "NEUTRAL", "sentiment_reason": ""}

    prompt = (SUMMARY_24H_PROMPT_EN if lang == "en" else SUMMARY_24H_PROMPT).format(
        ticker=ticker,
        stock_context=stock_context,
        articles_text=articles_text,
    )
    return llm_json(
        [{"role": "user", "content": prompt}],
        groq_model=GROQ_LIGHT,
        max_tokens=500,
        timeout=25,
    )


def get_news(ticker: str, lang: str = None) -> dict:
    ticker = ticker.upper()
    lang = lang or os.environ.get("LLM_LANG", "fr")
    supa = _get_supabase()

    cache = supa.table("stock_news_cache").select("*").eq("ticker", ticker).limit(1).execute()
    row = cache.data[0] if cache.data else None

    # EN mode: check if already enriched in EN, skip if so
    if lang == "en" and row and row.get("ai_summary_en"):
        return _build_response(ticker, row.get("news_raw") or [], row)

    if row and lang == "fr":
        cached_at = datetime.fromisoformat(row["cached_at"].replace("Z", "+00:00"))
        age = datetime.now(timezone.utc) - cached_at
        missing_24h = not row.get("ai_summary_24h")

        if age < timedelta(hours=CACHE_TTL_HOURS) and not missing_24h:
            return _format_response(row)

        fresh_articles, website = _fetch_news(ticker)

        if not missing_24h and fresh_articles and fresh_articles[0]["id"] == row["latest_news_id"]:
            update = {"cached_at": datetime.now(timezone.utc).isoformat()}
            if website and not row.get("website"):
                update["website"] = website
            supa.table("stock_news_cache").update(update).eq("ticker", ticker).execute()
            return _format_response(row)

        return _generate_and_cache(ticker, fresh_articles, supa, website=website, lang=lang)

    articles, website = _fetch_news(ticker)
    return _generate_and_cache(ticker, articles, supa, website=website, lang=lang)


def _generate_and_cache(ticker: str, articles: list[dict], supa, website: str | None = None, lang: str = "fr") -> dict:
    if not articles:
        return {
            "ticker": ticker, "articles": [],
            "summary": "", "sentiment": "NEUTRAL", "sentiment_reason": "",
            "summary_24h": "", "sentiment_24h": "NEUTRAL", "sentiment_reason_24h": "",
        }

    ctx = _get_stock_context(ticker)
    ctx_7d = _format_stock_context_7d(ctx)
    ctx_24h = _format_stock_context_24h(ctx)

    ai_result = _brutalise_with_groq(ticker, articles[:30], stock_context=ctx_7d, lang=lang)
    llm_source = ai_result.get("_llm_source", "unknown")

    brutalised_map = {item["id"]: item["brutalised"] for item in ai_result.get("articles", [])}
    filtered = []
    for i, article in enumerate(articles):
        b = brutalised_map.get(i, "")
        if b.strip().upper() == "SKIP":
            continue
        article["brutalised"] = b
        filtered.append(article)
    articles = filtered

    # Délai entre les 2 calls LLM pour respecter le RPM FreeTheAI (1 concurrent)
    _inter_call_delay = float(os.environ.get("LLM_INTER_CALL_DELAY", "0"))
    if _inter_call_delay > 0:
        import time as _time; _time.sleep(_inter_call_delay)

    articles_24h = _filter_24h(articles)
    result_24h = _summary_24h_with_groq(ticker, articles_24h, stock_context=ctx_24h, lang=lang)

    if lang == "en":
        row = {
            "ticker": ticker,
            "ai_summary_en": ai_result.get("summary", ""),
            "ai_summary_24h_en": result_24h.get("summary", ""),
            "ai_sentiment": ai_result.get("sentiment", "NEUTRAL"),
            "ai_sentiment_24h": result_24h.get("sentiment", "NEUTRAL"),
            "generated_by_en": llm_source,
            "cached_at": datetime.now(timezone.utc).isoformat(),
        }
        if website:
            row["website"] = website
        supa.table("stock_news_cache").upsert({"ticker": ticker, **row}).execute()

        # FR translation is done in a separate batch pass (phase translate-fr)

        return _build_response(ticker, articles, {**row, "news_raw": articles})
    else:
        # FR mode: existing behavior
        row = {
            "ticker": ticker,
            "news_raw": articles,
            "ai_summary": ai_result.get("summary", ""),
            "ai_sentiment": ai_result.get("sentiment", "NEUTRAL"),
            "ai_sentiment_reason": ai_result.get("sentiment_reason", ""),
            "ai_summary_24h": result_24h.get("summary", ""),
            "ai_sentiment_24h": result_24h.get("sentiment", "NEUTRAL"),
            "ai_sentiment_reason_24h": result_24h.get("sentiment_reason", ""),
            "latest_news_id": articles[0]["id"] if articles else "",
            "cached_at": datetime.now(timezone.utc).isoformat(),
            "website": website,
            "generated_by_fr": llm_source,
        }
        supa.table("stock_news_cache").upsert(row).execute()
        return _build_response(ticker, articles, row)


def _format_response(row: dict) -> dict:
    return _build_response(row["ticker"], row["news_raw"], row)


def _build_response(ticker: str, articles: list, row: dict) -> dict:
    return {
        "ticker": ticker,
        "articles": articles,
        "summary": row.get("ai_summary", ""),
        "sentiment": row.get("ai_sentiment", "NEUTRAL"),
        "sentiment_reason": row.get("ai_sentiment_reason", ""),
        "summary_24h": row.get("ai_summary_24h", ""),
        "sentiment_24h": row.get("ai_sentiment_24h", "NEUTRAL"),
        "sentiment_reason_24h": row.get("ai_sentiment_reason_24h", ""),
        "cached_at": row.get("cached_at", ""),
    }
