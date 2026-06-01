"""
Overview service — deux TTL indépendants :

  Call 1 (TTL 6h)  : fetch Yahoo → DUMB nouveaux articles → OW 24H
  Call 2 (TTL 24h) : lit abrutis en DB → OW 7J  (pas de re-fetch)

  BATCH_MODE=1 : Call 1 skippé (OW 24H expire vite), Call 2 seulement.
"""
import hashlib
import yfinance as yf
import json
import os
import time
from datetime import datetime, timezone, timedelta
from supabase import create_client
import services.llm_service as _llm_svc
from services.llm_service import llm_json

GROQ_HEAVY = "meta-llama/llama-4-scout-17b-16e-instruct"
GROQ_LIGHT  = "llama-3.1-8b-instant"

CACHE_TTL_24H_HOURS = 3   # Call 1 — OW 24H
CACHE_TTL_7D_HOURS  = 24  # Call 2 — OW 7J
MIN_SUMMARY_CHARS   = 80


# ─── Prompts ──────────────────────────────────────────────────────────────────

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

OW7D_PROMPT = """Tu es un analyste financier senior.

Ticker : {ticker}
{stock_context}

Voici des résumés analytiques d'articles récents (dernière semaine) :
{articles_text}

Synthétise en :
- RÉSUMÉ_GLOBAL : 4-6 phrases couvrant la semaine — performance, catalyseurs, risques, positionnement. Intègre les données de cours.
- SENTIMENT : BULLISH / BEARISH / NEUTRAL avec justification en 2 phrases

Format JSON strict :
{{"summary": "synthèse 7 jours en français", "sentiment": "BULLISH", "sentiment_reason": "justification en français"}}"""

OW7D_PROMPT_EN = """You are a senior financial analyst.

Ticker: {ticker}
{stock_context}

Here are analytical summaries of recent articles (past week):
{articles_text}

Synthesize into:
- GLOBAL_SUMMARY: 4-6 sentences — performance, catalysts, risks, positioning. Integrate price data.
- SENTIMENT: BULLISH / BEARISH / NEUTRAL with 2-sentence justification

Strict JSON:
{{"summary": "7-day synthesis in English", "sentiment": "BULLISH", "sentiment_reason": "justification in English"}}"""

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


# ─── Supabase ─────────────────────────────────────────────────────────────────

def _get_supabase():
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])


# ─── Stock context ────────────────────────────────────────────────────────────

BENCHMARK_BY_EXCHANGE = {
    "NMS": ("S&P 500", "^GSPC"), "NYQ": ("S&P 500", "^GSPC"),
    "NGM": ("S&P 500", "^GSPC"), "PCX": ("S&P 500", "^GSPC"),
    "PAR": ("CAC 40", "^FCHI"),
    "GER": ("DAX", "^GDAXI"), "XETRA": ("DAX", "^GDAXI"),
    "LSE": ("FTSE 100", "^FTSE"),
    "AMS": ("Euro Stoxx 50", "^STOXX50E"), "MIL": ("Euro Stoxx 50", "^STOXX50E"),
    "MCE": ("Euro Stoxx 50", "^STOXX50E"),
}


def _get_stock_context(ticker: str) -> dict:
    try:
        t = yf.Ticker(ticker)
        fi = t.fast_info
        current = fi.last_price
        prev_close = fi.previous_close
        change_pct = ((current - prev_close) / prev_close * 100) if prev_close else None
        vol_ratio = (fi.last_volume / fi.three_month_average_volume) if fi.three_month_average_volume and fi.last_volume else None
        hist = t.history(period="5d", interval="1d")
        week_open = float(hist["Close"].iloc[0]) if len(hist) > 1 else None
        week_close = float(hist["Close"].iloc[-1]) if len(hist) > 0 else None
        week_pct = ((week_close - week_open) / week_open * 100) if week_open and week_close else None
        exchange = getattr(fi, "exchange", "") or ""
        bench_name, bench_ticker = BENCHMARK_BY_EXCHANGE.get(exchange, ("Euro Stoxx 50", "^STOXX50E"))
        bench_hist = yf.Ticker(bench_ticker).history(period="5d", interval="1d")
        bench_day_pct = bench_week_pct = None
        if not bench_hist.empty:
            last = bench_hist.iloc[-1]
            bench_day_pct = (last["Close"] - last["Open"]) / last["Open"] * 100
            if len(bench_hist) > 1:
                bench_week_pct = (float(bench_hist["Close"].iloc[-1]) - float(bench_hist["Close"].iloc[0])) / float(bench_hist["Close"].iloc[0]) * 100
        return {
            "current": current, "change_pct": change_pct,
            "day_high": fi.day_high, "day_low": fi.day_low,
            "vol_ratio": vol_ratio, "week_pct": week_pct,
            "week_high": float(hist["High"].max()) if len(hist) > 0 else None,
            "week_low": float(hist["Low"].min()) if len(hist) > 0 else None,
            "bench_name": bench_name, "bench_day_pct": bench_day_pct, "bench_week_pct": bench_week_pct,
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


# ─── Fetch ────────────────────────────────────────────────────────────────────

def _fetch_news(ticker: str) -> tuple[list[dict], str | None]:
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


def _bench_context_line(ctx: dict, lang: str) -> str:
    if not ctx.get("bench_name") or ctx.get("bench_day_pct") is None:
        return ""
    sign = "+" if ctx["bench_day_pct"] >= 0 else ""
    if lang == "en":
        return f"{ctx['bench_name']} today: {sign}{ctx['bench_day_pct']:.2f}%."
    return f"{ctx['bench_name']} aujourd'hui : {sign}{ctx['bench_day_pct']:.2f}%."


# ─── LLM calls ────────────────────────────────────────────────────────────────

def _brutalise(ticker: str, articles: list[dict], stock_context: str = "", lang: str = "fr") -> dict:
    """Call lourd — abrutis par article + OW 7J."""
    articles_text = "\n\n".join(f"[{i}] {a['title']}\n{a['summary']}" for i, a in enumerate(articles))
    prompt = (BRUTALIZE_PROMPT_EN if lang == "en" else BRUTALIZE_PROMPT).format(
        ticker=ticker, stock_context=stock_context, count=len(articles), articles_text=articles_text,
    )
    return llm_json([{"role": "user", "content": prompt}], groq_model=GROQ_HEAVY, max_tokens=3000, timeout=60)


def _ow7d_from_abrutis(ticker: str, articles: list[dict], stock_context: str = "", lang: str = "fr") -> dict:
    """Call léger — génère OW 7J depuis abrutis déjà stockés. Pas de re-brutalisation."""
    articles_text = "\n".join(
        f"- {a['brutalised']}" for a in articles
        if a.get("brutalised") and a["brutalised"].strip().upper() != "SKIP"
    )
    if not articles_text:
        return {"summary": "", "sentiment": "NEUTRAL", "sentiment_reason": ""}
    prompt = (OW7D_PROMPT_EN if lang == "en" else OW7D_PROMPT).format(
        ticker=ticker, stock_context=stock_context, articles_text=articles_text,
    )
    return llm_json([{"role": "user", "content": prompt}], groq_model=GROQ_HEAVY, max_tokens=500, timeout=30)


def _ow24h_from_abrutis(ticker: str, articles_24h: list[dict], stock_context: str = "", lang: str = "fr") -> dict:
    """Call léger — génère OW 24H depuis abrutis <24h."""
    if not articles_24h:
        return {"summary": "", "sentiment": "NEUTRAL", "sentiment_reason": ""}
    articles_text = "\n".join(f"- {a['brutalised']}" for a in articles_24h if a.get("brutalised"))
    if not articles_text:
        return {"summary": "", "sentiment": "NEUTRAL", "sentiment_reason": ""}
    prompt = (SUMMARY_24H_PROMPT_EN if lang == "en" else SUMMARY_24H_PROMPT).format(
        ticker=ticker, stock_context=stock_context, articles_text=articles_text,
    )
    return llm_json([{"role": "user", "content": prompt}], groq_model=GROQ_LIGHT, max_tokens=500, timeout=25)


# ─── LLM2 — hash articles ─────────────────────────────────────────────────────

def _articles_hash(articles: list[dict]) -> str:
    ids = sorted(a.get("id", "") for a in articles if a.get("id"))
    return hashlib.md5(",".join(ids).encode()).hexdigest()[:16]


# ─── TTL checks ───────────────────────────────────────────────────────────────

def _needs_24h_refresh(row: dict | None, now: datetime) -> bool:
    lang = os.environ.get("LLM_LANG", "en")
    col = "ai_summary_24h_en" if lang == "en" else "ai_summary_24h"
    if not row or not row.get(col):
        return True
    try:
        cached_at = datetime.fromisoformat(row["cached_at"].replace("Z", "+00:00"))
        return (now - cached_at) >= timedelta(hours=CACHE_TTL_24H_HOURS)
    except Exception:
        return True


def _needs_7d_refresh(row: dict | None, now: datetime) -> bool:
    lang = os.environ.get("LLM_LANG", "en")
    col = "ai_summary_en" if lang == "en" else "ai_summary"
    if not row or not row.get(col):
        return True
    ow7d_str = row.get("ow7d_cached_at")
    if not ow7d_str:
        return True
    try:
        ow7d_at = datetime.fromisoformat(ow7d_str.replace("Z", "+00:00"))
        return (now - ow7d_at) >= timedelta(hours=CACHE_TTL_7D_HOURS)
    except Exception:
        return True


# ─── Call 1 — DUMB + OW 24H (TTL 3h) ─────────────────────────────────────────

def _call1_dumb_ow24h(ticker: str, fresh_articles: list[dict], row: dict | None,
                      supa, website: str | None, lang: str, now: datetime) -> dict:
    existing_raw = (row.get("news_raw") or []) if row else []
    existing_ids = {a.get("id", "") for a in existing_raw}

    # LLM2: skip brutalise si les articles n'ont pas changé depuis le dernier run
    new_hash = _articles_hash(fresh_articles[:30])
    cached_hash = (row or {}).get("articles_hash", "")
    has_summary = bool((row or {}).get("ai_summary_24h") or (row or {}).get("ai_summary_24h_en"))
    if existing_raw and cached_hash and cached_hash == new_hash and has_summary:
        update = {"cached_at": now.isoformat()}
        if website and not (row or {}).get("website"):
            update["website"] = website
        supa.table("stock_news_cache").update(update).eq("ticker", ticker).execute()
        return {**(row or {}), **update}

    new_articles = [a for a in fresh_articles if a.get("id", "") not in existing_ids]
    to_brutalise = (new_articles or fresh_articles)[:30]

    ctx = _get_stock_context(ticker)
    ctx_7d = _format_stock_context_7d(ctx)
    ctx_24h = _format_stock_context_24h(ctx)

    ai_result = _brutalise(ticker, to_brutalise, stock_context=ctx_7d, lang=lang)
    llm_source = _llm_svc._last_llm_source

    brutalised_map = {item["id"]: item["brutalised"] for item in ai_result.get("articles", [])}
    new_brutalised = []
    for i, a in enumerate(to_brutalise):
        b = brutalised_map.get(i, "")
        if b.strip().upper() == "SKIP":
            continue
        new_brutalised.append({**a, "brutalised": b})

    if not new_brutalised:
        new_brutalised = to_brutalise[:15]

    # Merge : nouveaux devant, anciens derrière, cap 30
    merged = new_brutalised + [a for a in existing_raw if a.get("id") not in {x["id"] for x in new_brutalised}]
    merged = merged[:30]

    _inter_delay = float(os.environ.get("LLM_INTER_CALL_DELAY", "0"))
    if _inter_delay > 0:
        time.sleep(_inter_delay)

    articles_24h = _filter_24h(merged)
    if articles_24h:
        result_24h = _ow24h_from_abrutis(ticker, articles_24h, stock_context=ctx_24h, lang=lang)
        no_24h = False
    else:
        bench_line = _bench_context_line(ctx, lang)
        no_news_note = "No news in the last 24 hours." if lang == "en" else "Aucune actualité dans les dernières 24 heures."
        existing_7d = (row.get("ai_summary_en") if lang == "en" else row.get("ai_summary", "")) if row else ""
        result_24h = {
            "summary": f"{no_news_note}{(' ' + bench_line) if bench_line else ''} {existing_7d}".strip(),
            "sentiment": ai_result.get("sentiment", "NEUTRAL"),
            "sentiment_reason": ai_result.get("sentiment_reason", ""),
            "_no_24h_news": True,
        }
        no_24h = True

    if lang == "en":
        update = {
            "ticker": ticker,
            "ai_summary_24h_en": result_24h.get("summary", ""),
            "ai_sentiment_24h": result_24h.get("sentiment", "NEUTRAL"),
            "no_24h_news": no_24h,
            "cached_at": now.isoformat(),
            "generated_by_en": llm_source,
            "articles_hash": new_hash,
            # LLM4: stocker le résumé 7J produit par BRUTALIZE → évite un call séparé
            "ai_summary_en": ai_result.get("summary", ""),
            "ai_sentiment": ai_result.get("sentiment", "NEUTRAL"),
            "ow7d_cached_at": now.isoformat(),
        }
        if website:
            update["website"] = website
        supa.table("stock_news_cache").upsert({"ticker": ticker, **update}).execute()
        return {**(row or {}), **update, "news_raw": merged}
    else:
        update = {
            "ticker": ticker,
            "news_raw": merged,
            "ai_summary_24h": result_24h.get("summary", ""),
            "ai_sentiment_24h": result_24h.get("sentiment", "NEUTRAL"),
            "ai_sentiment_reason_24h": result_24h.get("sentiment_reason", ""),
            "no_24h_news": no_24h,
            "cached_at": now.isoformat(),
            "latest_news_id": merged[0]["id"] if merged else "",
            "website": website,
            "generated_by_fr": llm_source,
            "articles_hash": new_hash,
            # LLM4: stocker le résumé 7J produit par BRUTALIZE → évite un call séparé
            "ai_summary": ai_result.get("summary", ""),
            "ai_sentiment": ai_result.get("sentiment", "NEUTRAL"),
            "ai_sentiment_reason": ai_result.get("sentiment_reason", ""),
            "ow7d_cached_at": now.isoformat(),
        }
        supa.table("stock_news_cache").upsert(update).execute()
        return {**(row or {}), **update}


# ─── Call 2 — OW 7J (TTL 24h) ─────────────────────────────────────────────────

def _call2_ow7d(ticker: str, fresh_articles: list[dict], row: dict | None,
                supa, lang: str, now: datetime) -> dict:
    existing_raw = (row.get("news_raw") or []) if row else []
    has_abrutis = any(a.get("brutalised") for a in existing_raw)

    ctx = _get_stock_context(ticker)
    ctx_7d = _format_stock_context_7d(ctx)
    llm_source = _llm_svc._last_llm_source

    if has_abrutis:
        abrutis = [a for a in existing_raw if a.get("brutalised")]
        ai_result = _ow7d_from_abrutis(ticker, abrutis, stock_context=ctx_7d, lang=lang)
        llm_source = _llm_svc._last_llm_source
    else:
        # Pas d'abrutis (ex: batch mode, première visite sans Call 1)
        to_brutalise = fresh_articles[:30]
        ai_result = _brutalise(ticker, to_brutalise, stock_context=ctx_7d, lang=lang)
        llm_source = _llm_svc._last_llm_source
        brutalised_map = {item["id"]: item["brutalised"] for item in ai_result.get("articles", [])}
        articles_with_b = []
        for i, a in enumerate(to_brutalise):
            b = brutalised_map.get(i, "")
            if b.strip().upper() != "SKIP":
                articles_with_b.append({**a, "brutalised": b})
        existing_raw = articles_with_b or to_brutalise[:15]

    if lang == "en":
        update = {
            "ticker": ticker,
            "ai_summary_en": ai_result.get("summary", ""),
            "ai_sentiment": ai_result.get("sentiment", "NEUTRAL"),
            "ow7d_cached_at": now.isoformat(),
            "generated_by_en": llm_source,
        }
        supa.table("stock_news_cache").upsert({"ticker": ticker, **update}).execute()
        return {**(row or {}), **update, "news_raw": existing_raw}
    else:
        update = {
            "ticker": ticker,
            "news_raw": existing_raw,
            "ai_summary": ai_result.get("summary", ""),
            "ai_sentiment": ai_result.get("sentiment", "NEUTRAL"),
            "ai_sentiment_reason": ai_result.get("sentiment_reason", ""),
            "ow7d_cached_at": now.isoformat(),
            "generated_by_fr": llm_source,
        }
        supa.table("stock_news_cache").upsert(update).execute()
        return {**(row or {}), **update}


# ─── Entry point ──────────────────────────────────────────────────────────────

def get_news(ticker: str, lang: str = None) -> dict:
    ticker = ticker.upper()
    lang = lang or os.environ.get("LLM_LANG", "en")
    batch_mode = os.environ.get("BATCH_MODE") == "1"
    supa = _get_supabase()

    cache = supa.table("stock_news_cache").select("*").eq("ticker", ticker).limit(1).execute()
    row = cache.data[0] if cache.data else None

    now = datetime.now(timezone.utc)
    needs_24h = _needs_24h_refresh(row, now) and not batch_mode
    needs_7d  = _needs_7d_refresh(row, now)

    if not needs_24h and not needs_7d:
        return _format_response(row)

    fresh_articles, website = _fetch_news(ticker)

    if not fresh_articles:
        if row:
            return _format_response(row)
        return {
            "ticker": ticker, "articles": [],
            "summary": "", "sentiment": "NEUTRAL", "sentiment_reason": "",
            "summary_24h": "", "sentiment_24h": "NEUTRAL", "sentiment_reason_24h": "",
        }

    if needs_24h:
        row = _call1_dumb_ow24h(ticker, fresh_articles, row, supa, website, lang, now)

    if needs_7d:
        row = _call2_ow7d(ticker, fresh_articles, row, supa, lang, now)

    return _build_response(ticker, row.get("news_raw") or [], row)


# ─── Response builders ────────────────────────────────────────────────────────

def _format_response(row: dict) -> dict:
    return _build_response(row["ticker"], row.get("news_raw") or [], row)


def _build_response(ticker: str, articles: list, row: dict) -> dict:
    return {
        "ticker": ticker,
        "articles": articles,
        "summary": row.get("ai_summary_en", "") or row.get("ai_summary", ""),
        "sentiment": row.get("ai_sentiment", "NEUTRAL"),
        "sentiment_reason": row.get("ai_sentiment_reason", ""),
        "summary_24h": row.get("ai_summary_24h_en", "") or row.get("ai_summary_24h", ""),
        "sentiment_24h": row.get("ai_sentiment_24h", "NEUTRAL"),
        "sentiment_reason_24h": row.get("ai_sentiment_reason_24h", ""),
        "no_24h_news": row.get("no_24h_news", False),
        "cached_at": row.get("cached_at", ""),
        "llm_source": row.get("generated_by_fr") or row.get("generated_by_en") or "cache",
    }


# ─── Cron hourly refresh ──────────────────────────────────────────────────────

def _maybe_regen_weekly(ticker: str, supa, logger) -> None:
    """Si le résumé IA de la semaine courante est absent, le génère (1 call LLM max/semaine/ticker)."""
    try:
        now = datetime.now(timezone.utc)
        current_ym = now.strftime("%Y-%m")
        day = now.day
        current_wnum = 1 if day <= 7 else 2 if day <= 14 else 3 if day <= 21 else 4
        row = (
            supa.table("article_week_summaries")
            .select("year_month, week_num, ai_summary")
            .eq("ticker", ticker)
            .order("year_month", desc=True)
            .order("week_num", desc=True)
            .limit(1)
            .maybe_single()
            .execute()
        )
        if row and row.data and row.data.get("ai_summary") \
                and row.data["year_month"] == current_ym and row.data["week_num"] == current_wnum:
            return  # résumé de la semaine courante déjà là
        from services.period_articles_service import get_weekly_ondemand
        get_weekly_ondemand(ticker)
        logger.info(f"[hourly_refresh] ✓ weekly regen {ticker} W{current_wnum} {current_ym}")
    except Exception as e:
        logger.warning(f"[hourly_refresh] weekly regen {ticker}: {e}")


def run_hourly_news_refresh():
    """
    Cron :15 chaque heure.
    Cible : tickers pinnés + visités récemment avec no_24h_news=True.
    Si nouveaux articles 24h trouvés → get_news() déclenche Call 1 (DUMB + OW 24H).
    """
    import logging
    logger = logging.getLogger(__name__)

    supa = _get_supabase()
    now = datetime.now(timezone.utc)
    six_h_ago = (now - timedelta(hours=6)).isoformat()

    pins = supa.table("pinned_stocks").select("ticker").execute()
    pinned = {r["ticker"] for r in (pins.data or [])}

    recent = supa.table("stock_news_cache") \
        .select("ticker").eq("no_24h_news", True).gte("cached_at", six_h_ago).execute()
    recent_tickers = {r["ticker"] for r in (recent.data or [])}

    pinned_no_news = supa.table("stock_news_cache") \
        .select("ticker").eq("no_24h_news", True).in_("ticker", list(pinned)).execute()
    pinned_no_news_tickers = {r["ticker"] for r in (pinned_no_news.data or [])}

    candidates = pinned_no_news_tickers | recent_tickers
    if not candidates:
        return

    logger.info(f"[hourly_refresh] {len(candidates)} tickers no_24h_news à vérifier")

    for ticker in candidates:
        try:
            fresh_articles, _ = _fetch_news(ticker)
            articles_24h = _filter_24h(fresh_articles)
            if len(articles_24h) == 0:
                supa.table("stock_news_cache").update({"cached_at": now.isoformat()}).eq("ticker", ticker).execute()
                logger.info(f"[hourly_refresh] {ticker} — toujours 0 news 24h")
                continue
            get_news(ticker)
            logger.info(f"[hourly_refresh] ✓ {ticker} — {len(articles_24h)} articles 24h")
            _maybe_regen_weekly(ticker, supa, logger)
        except Exception as e:
            logger.warning(f"[hourly_refresh] ✗ {ticker}: {e}")
