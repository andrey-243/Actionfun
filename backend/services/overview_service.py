"""
Overview service — refresh déclenché uniquement sur nouveaux articles :

  Call 1 : fetch articles → si nouveaux → DUMB + OW 24H + OW 7J
  Call 2 : lit dumb en DB → OW 7J  (BATCH_MODE=1 uniquement)

  Pas de TTL — refresh uniquement via force_24h=True (crons Yahoo/Finnhub) ou cache absent.
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
from services.breakers_articles_service import _is_ticker_related

GROQ_HEAVY = "meta-llama/llama-4-scout-17b-16e-instruct"
GROQ_LIGHT  = "llama-3.1-8b-instant"


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

def _lookup_company_name(ticker: str, supa) -> str:
    """Retourne le company_name depuis pinned_stocks (premier résultat)."""
    try:
        r = supa.table("pinned_stocks").select("company_name") \
            .eq("ticker", ticker).limit(1).execute()
        return (r.data[0].get("company_name", "") if r.data else "") or ""
    except Exception:
        return ""


def _fetch_news(ticker: str) -> tuple[list[dict], str | None]:
    import hashlib as _hashlib
    from urllib.parse import urlparse

    # Website via yfinance (inchangé)
    website = None
    try:
        t = yf.Ticker(ticker)
        raw_url = (t.info or {}).get("website", "") or ""
        if raw_url:
            website = urlparse(raw_url).netloc.lstrip("www.") or None
    except Exception:
        pass

    # Articles depuis articles (merge Finn+Yahoo) — 7 derniers jours
    supa = _get_supabase()
    company_name = _lookup_company_name(ticker, supa)
    now = datetime.now(timezone.utc)
    cutoff_ts = int((now - timedelta(days=7)).timestamp())

    year_months: set[str] = {now.strftime("%Y-%m")}
    if now.day <= 7:
        prev = now.replace(day=1) - timedelta(days=1)
        year_months.add(prev.strftime("%Y-%m"))

    raw: list[dict] = []
    for ym in sorted(year_months):
        r = supa.table("articles") \
            .select("articles") \
            .eq("ticker", ticker) \
            .eq("year_month", ym) \
            .maybe_single() \
            .execute()
        if r and r.data and r.data.get("articles"):
            raw.extend(r.data["articles"])

    articles = []
    for a in raw:
        ts = a.get("datetime", 0)
        if ts < cutoff_ts:
            continue
        if not a.get("headline", "").strip():
            continue
        if not _is_ticker_related(a, ticker, company_name):
            continue
        dumb_en = a.get("dumb_en", "")
        dumb_fr = a.get("dumb_fr", "")
        if dumb_en.strip().upper() == "SKIP" or dumb_fr.strip().upper() == "SKIP":
            continue
        url = a.get("url", "")
        articles.append({
            "id":         _hashlib.md5(url.encode()).hexdigest()[:16] if url else "",
            "type":       "STORY",
            "title":      a.get("headline", ""),
            "summary":    a.get("summary", ""),
            "source":     a.get("source", ""),
            "url":        url,
            "thumbnail":  a.get("image", ""),
            "pub_date":   datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(),
            "dumb_en":    a.get("dumb_en", ""),
            "dumb_fr":    a.get("dumb_fr", ""),
            "is_related": a.get("is_related"),
        })

    articles.sort(key=lambda a: a["pub_date"], reverse=True)
    return articles[:100], website


def _filter_24h(articles: list[dict]) -> list[dict]:
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=26)
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

def _brutalise(ticker: str, articles: list[dict], stock_context: str = "", lang: str = "fr", cascade: str = "default") -> dict:
    """Call lourd — abrutis par article + OW 7J."""
    articles_text = "\n\n".join(f"[{i}] {a.get('title') or a.get('headline', '')}\n{a.get('summary', '')}" for i, a in enumerate(articles))
    prompt = (BRUTALIZE_PROMPT_EN if lang == "en" else BRUTALIZE_PROMPT).format(
        ticker=ticker, stock_context=stock_context, count=len(articles), articles_text=articles_text,
    )
    return llm_json([{"role": "user", "content": prompt}], groq_model=GROQ_HEAVY, max_tokens=3000, timeout=60, caller="OW", item_id=ticker, cascade=cascade)


def _ow7d_from_abrutis(ticker: str, articles: list[dict], stock_context: str = "", lang: str = "fr") -> dict:
    """Call léger — génère OW 7J depuis dumb déjà stockés. Pas de re-brutalisation."""
    dumb_field = "dumb_en" if lang == "en" else "dumb_fr"
    articles_text = "\n".join(
        f"- {a.get(dumb_field) or ''}"
        for a in articles
        if a.get("is_related") is not False and (a.get(dumb_field) or "").strip()
    )
    if not articles_text:
        return {"summary": "", "sentiment": "NEUTRAL", "sentiment_reason": ""}
    prompt = (OW7D_PROMPT_EN if lang == "en" else OW7D_PROMPT).format(
        ticker=ticker, stock_context=stock_context, articles_text=articles_text,
    )
    return llm_json([{"role": "user", "content": prompt}], groq_model=GROQ_HEAVY, max_tokens=500, timeout=30, caller="OW", item_id=ticker, cascade="ow7j_prod")


def _ow24h_from_abrutis(ticker: str, articles_24h: list[dict], stock_context: str = "", lang: str = "fr") -> dict:
    """Call léger — génère OW 24H depuis dumb <24h."""
    if not articles_24h:
        return {"summary": "", "sentiment": "NEUTRAL", "sentiment_reason": ""}
    dumb_field = "dumb_en" if lang == "en" else "dumb_fr"
    articles_text = "\n".join(
        f"- {a.get(dumb_field, '')}"
        for a in articles_24h
        if a.get("is_related") is not False and a.get(dumb_field)
    )
    if not articles_text:
        return {"summary": "", "sentiment": "NEUTRAL", "sentiment_reason": ""}
    prompt = (SUMMARY_24H_PROMPT_EN if lang == "en" else SUMMARY_24H_PROMPT).format(
        ticker=ticker, stock_context=stock_context, articles_text=articles_text,
    )
    return llm_json([{"role": "user", "content": prompt}], groq_model=GROQ_LIGHT, max_tokens=500, timeout=25, caller="OW", item_id=ticker, cascade="ow24h_prod")


# ─── LLM2 — hash articles ─────────────────────────────────────────────────────

def _articles_hash(articles: list[dict]) -> str:
    ids = sorted(a.get("id", "") for a in articles if a.get("id"))
    return hashlib.md5(",".join(ids).encode()).hexdigest()[:16]


# ─── TTL checks ───────────────────────────────────────────────────────────────

def _needs_24h_refresh(row: dict | None) -> bool:
    lang = os.environ.get("LLM_LANG", "en")
    col = "ow1d_en" if lang == "en" else "ow1d_fr"
    return not row or not row.get(col)


def _needs_7d_refresh(row: dict | None) -> bool:
    lang = os.environ.get("LLM_LANG", "en")
    col = "ow7d_en" if lang == "en" else "ow7d_fr"
    return not row or not row.get(col)


def _is_stale_6h(row: dict | None, now: datetime) -> bool:
    """Filet de sécurité : si le cache n'a pas été mis à jour depuis 6h, une visite page déclenche un refresh."""
    if not row or not row.get("ow1d_cached_at_en"):
        return False
    try:
        cached_at = datetime.fromisoformat(row["ow1d_cached_at_en"].replace("Z", "+00:00"))
        return (now - cached_at) >= timedelta(hours=6)
    except Exception:
        return True


# ─── Write dumb back to articles ───────────────────────────

def _url_hash(url: str) -> str:
    import hashlib as _hl
    return _hl.md5(url.encode()).hexdigest()[:12]


def _write_dumb_to_archive(ticker: str, articles: list[dict], lang: str, supa) -> None:
    """Écrit dumb_en/dumb_fr + is_related dans les articles. Purge les is_related=False."""
    dumb_field = "dumb_en" if lang == "en" else "dumb_fr"
    # url -> (dumb_value, is_related)
    by_ym: dict[str, dict[str, tuple[str, bool]]] = {}
    for a in articles:
        dumb = a.get(dumb_field, "")
        if not dumb:
            continue
        url = a.get("url", "")
        pub_date = a.get("pub_date", "")
        if not pub_date and a.get("datetime"):
            pub_date = datetime.fromtimestamp(a["datetime"], tz=timezone.utc).isoformat()
        if not url or not pub_date:
            continue
        try:
            ym = datetime.fromisoformat(pub_date.replace("Z", "+00:00")).strftime("%Y-%m")
        except Exception:
            continue
        is_related = dumb.strip().upper() != "SKIP"
        by_ym.setdefault(ym, {})[url] = (dumb, is_related)

    blocklist_rows: list[dict] = []

    for ym, url_data in by_ym.items():
        r = supa.table("articles").select("articles") \
            .eq("ticker", ticker).eq("year_month", ym).maybe_single().execute()
        if not r or not r.data:
            continue
        arts = r.data.get("articles") or []
        updated = False
        kept: list[dict] = []
        for art in arts:
            url = art.get("url", "")
            if url in url_data and not art.get(dumb_field):
                dumb_val, is_rel = url_data[url]
                art[dumb_field] = dumb_val
                art["is_related"] = is_rel
                updated = True
                if not is_rel:
                    blocklist_rows.append({"ticker": ticker, "url_hash": _url_hash(url)})
                    continue  # exclure du JSON final
            kept.append(art)
        if updated:
            supa.table("articles").update({"articles": kept}) \
                .eq("ticker", ticker).eq("year_month", ym).execute()

    if blocklist_rows:
        supa.table("articles_blocklist").upsert(blocklist_rows, on_conflict="ticker,url_hash").execute()


# ─── Brutalise standalone — appelé par les crons dès nouveaux articles ────────

def gen_dumb(ticker: str, lang: str = None, cascade: str = "default", cutoff_days: int = 7) -> int:
    """
    Génère dumb_en/fr pour tous les articles sans DUMB dans articles.
    Appelé par les crons Yahoo/Finnhub immédiatement après détection de nouveaux articles,
    indépendamment de la regen OW.
    cutoff_days : fenêtre en jours depuis maintenant (défaut 7j). Passer 35 pour la fenêtre display www.
    Retourne le nombre d'articles brutalised.
    """
    lang = lang or os.environ.get("LLM_LANG", "en")
    dumb_field = "dumb_en" if lang == "en" else "dumb_fr"
    supa = _get_supabase()

    if cutoff_days == 7:
        fresh_articles, _ = _fetch_news(ticker)
    else:
        # Fenêtre custom : query directe sans passer par _fetch_news (hardcodé 7j)
        now = datetime.now(timezone.utc)
        cutoff_ts = int((now - timedelta(days=cutoff_days)).timestamp())
        year_months: set[str] = set()
        for d in range(0, cutoff_days + 7, 28):
            ym = (now - timedelta(days=d)).strftime("%Y-%m")
            year_months.add(ym)
        raw: list[dict] = []
        for ym in sorted(year_months):
            r = supa.table("articles").select("articles") \
                .eq("ticker", ticker).eq("year_month", ym).maybe_single().execute()
            if r and r.data and r.data.get("articles"):
                raw.extend(r.data["articles"])
        fresh_articles = [
            a for a in raw
            if (a.get("datetime") or 0) >= cutoff_ts and (a.get("summary") or "").strip()
        ]

    to_brutalise = [a for a in fresh_articles[:30] if not a.get(dumb_field)]
    if not to_brutalise:
        return 0

    ctx = _get_stock_context(ticker)
    ctx_7d = _format_stock_context_7d(ctx)
    ai_result = _brutalise(ticker, to_brutalise, stock_context=ctx_7d, lang=lang, cascade=cascade)

    brutalised_map = {item["id"]: item["brutalised"] for item in ai_result.get("articles", [])}
    for i, a in enumerate(to_brutalise):
        b = brutalised_map.get(i, "")
        if b:
            a[dumb_field] = b

    _write_dumb_to_archive(ticker, to_brutalise, lang, supa)
    return len(to_brutalise)


# ─── Call 1 — DUMB + OW 24H ───────────────────────────────────────────────────

def _call1_dumb_ow24h(ticker: str, fresh_articles: list[dict], row: dict | None,
                      supa, website: str | None, lang: str, now: datetime,
                      ctx: dict | None = None) -> dict:
    dumb_field = "dumb_en" if lang == "en" else "dumb_fr"

    # LLM2: skip brutalise si les articles n'ont pas changé depuis le dernier run
    new_hash = _articles_hash(fresh_articles[:30])
    cached_hash = (row or {}).get("articles_hash", "")
    has_summary = bool((row or {}).get("ow1d_fr") or (row or {}).get("ow1d_en"))
    if cached_hash and cached_hash == new_hash and has_summary:
        update = {"ow1d_cached_at_en": now.isoformat()}
        if website and not (row or {}).get("website"):
            update["website"] = website
        supa.table("ow_cache").update(update).eq("ticker", ticker).execute()
        return {**(row or {}), **update}

    if ctx is None:
        ctx = _get_stock_context(ticker)
    ctx_7d = _format_stock_context_7d(ctx)
    ctx_24h = _format_stock_context_24h(ctx)

    # Articles sans dumb → à brutaliser
    to_brutalise = [a for a in fresh_articles[:30] if not a.get(dumb_field)]
    if to_brutalise:
        ai_result = _brutalise(ticker, to_brutalise, stock_context=ctx_7d, lang=lang, cascade="dumb_prod")
        llm_source = _llm_svc._last_llm_source

        brutalised_map = {item["id"]: item["brutalised"] for item in ai_result.get("articles", [])}
        for i, a in enumerate(to_brutalise):
            b = brutalised_map.get(i, "")
            if b:
                a[dumb_field] = b

        _write_dumb_to_archive(ticker, to_brutalise, lang, supa)

        _inter_delay = float(os.environ.get("LLM_INTER_CALL_DELAY", "0"))
        if _inter_delay > 0:
            time.sleep(_inter_delay)
    else:
        # Tous les articles ont déjà leur DUMB — OW 7J depuis dumb existants, sans re-brutaliser
        ow7d = _ow7d_from_abrutis(ticker, fresh_articles[:30], stock_context=ctx_7d, lang=lang)
        llm_source = _llm_svc._last_llm_source
        ai_result = {
            "summary": ow7d.get("summary", ""),
            "sentiment": ow7d.get("sentiment", "NEUTRAL"),
            "sentiment_reason": ow7d.get("sentiment_reason", ""),
            "articles": [],
        }

    articles_24h = _filter_24h(fresh_articles)
    if articles_24h:
        result_24h = _ow24h_from_abrutis(ticker, articles_24h, stock_context=ctx_24h, lang=lang)
        no_24h = False
    else:
        bench_line = _bench_context_line(ctx, lang)
        no_news_note = "No news in the last 24 hours." if lang == "en" else "Aucune actualité dans les dernières 24 heures."
        existing_7d = (row.get("ow7d_en") or row.get("ow7d_fr") or "") if row else ""
        result_24h = {
            "summary": f"{no_news_note}{(' ' + bench_line) if bench_line else ''}".strip(),
            "sentiment": ai_result.get("sentiment", "NEUTRAL"),
            "sentiment_reason": ai_result.get("sentiment_reason", ""),
            "_no_24h_news": True,
        }
        no_24h = True

    if lang == "en":
        update = {
            "ticker": ticker,
            "ow1d_en": result_24h.get("summary", ""),
            "ow1d_sentiment": result_24h.get("sentiment", "NEUTRAL"),
            "no_24h_news": no_24h,
            "ow1d_cached_at_en": now.isoformat(),
            "llm_source_en": llm_source,
            "articles_hash": new_hash,
            "ow7d_en": ai_result.get("summary", ""),
            "ow7d_sentiment": ai_result.get("sentiment", "NEUTRAL"),
            "ow7d_cached_at_en": now.isoformat(),
        }
        if website:
            update["website"] = website
        supa.table("ow_cache").upsert({"ticker": ticker, **update}).execute()
    else:
        update = {
            "ticker": ticker,
            "ow1d_fr": result_24h.get("summary", ""),
            "ow1d_sentiment": result_24h.get("sentiment", "NEUTRAL"),
            "no_24h_news": no_24h,
            "ow1d_cached_at_fr": now.isoformat(),
            "website": website,
            "llm_source_fr": llm_source,
            "articles_hash": new_hash,
            "ow7d_fr": ai_result.get("summary", ""),
            "ow7d_sentiment": ai_result.get("sentiment", "NEUTRAL"),
            "ow7d_cached_at_fr": now.isoformat(),
        }
        supa.table("ow_cache").upsert(update).execute()
    return {**(row or {}), **update}


# ─── Call 2 — OW 7J (TTL 24h) ─────────────────────────────────────────────────

def _call2_ow7d(ticker: str, fresh_articles: list[dict], row: dict | None,
                supa, lang: str, now: datetime, ctx: dict | None = None) -> dict:
    dumb_field = "dumb_en" if lang == "en" else "dumb_fr"
    has_dumb = any(a.get(dumb_field) for a in fresh_articles)

    if ctx is None:
        ctx = _get_stock_context(ticker)
    ctx_7d = _format_stock_context_7d(ctx)

    if has_dumb:
        ai_result = _ow7d_from_abrutis(ticker, fresh_articles, stock_context=ctx_7d, lang=lang)
    else:
        # Pas de dumb → brutaliser d'abord
        to_brutalise = fresh_articles[:30]
        ai_result = _brutalise(ticker, to_brutalise, stock_context=ctx_7d, lang=lang, cascade="dumb_prod")
        brutalised_map = {item["id"]: item["brutalised"] for item in ai_result.get("articles", [])}
        for i, a in enumerate(to_brutalise):
            b = brutalised_map.get(i, "")
            if b and b.strip().upper() != "SKIP":
                a[dumb_field] = b
        _write_dumb_to_archive(ticker, to_brutalise, lang, supa)

    llm_source = _llm_svc._last_llm_source

    if lang == "en":
        update = {
            "ticker": ticker,
            "ow7d_en": ai_result.get("summary", ""),
            "ow7d_sentiment": ai_result.get("sentiment", "NEUTRAL"),
            "ow7d_cached_at_en": now.isoformat(),
            "llm_source_en": llm_source,
        }
        supa.table("ow_cache").upsert({"ticker": ticker, **update}).execute()
    else:
        update = {
            "ticker": ticker,
            "ow7d_fr": ai_result.get("summary", ""),
            "ow7d_sentiment": ai_result.get("sentiment", "NEUTRAL"),
            "ow7d_cached_at_fr": now.isoformat(),
            "llm_source_fr": llm_source,
        }
        supa.table("ow_cache").upsert(update).execute()
    return {**(row or {}), **update}


# ─── Entry point ──────────────────────────────────────────────────────────────

def get_or_refresh_ow_both(ticker: str, lang: str = None) -> tuple[dict | None, bool]:
    """
    Retourne (cached_result, needs_refresh) sans jamais appeler le LLM.
    None si aucun cache — l'appelant doit faire un appel bloquant.
    """
    ticker = ticker.upper()
    lang = lang or os.environ.get("LLM_LANG", "en")
    batch_mode = os.environ.get("BATCH_MODE") == "1"
    supa = _get_supabase()

    cache = supa.table("ow_cache").select("*").eq("ticker", ticker).limit(1).execute()
    row = cache.data[0] if cache.data else None

    if row is None or not row.get("ow1d_cached_at_en"):
        return None, True

    now = datetime.now(timezone.utc)
    needs_refresh = (_needs_24h_refresh(row) and not batch_mode) or _needs_7d_refresh(row) or _is_stale_6h(row, now)

    fresh_articles, _ = _fetch_news(ticker)

    if row.get("no_24h_news") and not needs_refresh and not batch_mode:
        if _filter_24h(fresh_articles):
            needs_refresh = True

    result = _build_response(ticker, fresh_articles, row)
    result["is_refreshing"] = needs_refresh
    return result, needs_refresh


def gen_ow_both(ticker: str, lang: str = None, force_24h: bool = False) -> dict:
    ticker = ticker.upper()
    lang = lang or os.environ.get("LLM_LANG", "en")
    batch_mode = os.environ.get("BATCH_MODE") == "1"
    supa = _get_supabase()

    cache = supa.table("ow_cache").select("*").eq("ticker", ticker).limit(1).execute()
    row = cache.data[0] if cache.data else None

    now = datetime.now(timezone.utc)
    needs_24h = (_needs_24h_refresh(row) or force_24h) and not batch_mode
    needs_7d  = _needs_7d_refresh(row)

    fresh_articles, website = _fetch_news(ticker)

    if not needs_24h and not needs_7d:
        return _build_response(ticker, fresh_articles, row)

    if not fresh_articles:
        if row:
            return _build_response(ticker, [], row)
        return {
            "ticker": ticker, "articles": [],
            "summary": "", "sentiment": "NEUTRAL", "sentiment_reason": "",
            "summary_24h": "", "sentiment_24h": "NEUTRAL", "sentiment_reason_24h": "",
        }

    ctx = _get_stock_context(ticker) if (needs_24h or needs_7d) else None

    if needs_24h:
        row = _call1_dumb_ow24h(ticker, fresh_articles, row, supa, website, lang, now, ctx=ctx)

    if needs_7d:
        row = _call2_ow7d(ticker, fresh_articles, row, supa, lang, now, ctx=ctx)

    return _build_response(ticker, fresh_articles, row)


def _build_response(ticker: str, articles: list, row: dict) -> dict:
    return {
        "ticker": ticker,
        "articles": articles,
        "summary": row.get("ow7d_en", "") or row.get("ow7d_fr", ""),
        "sentiment": row.get("ow7d_sentiment", "NEUTRAL"),
        "summary_24h": row.get("ow1d_en", "") or row.get("ow1d_fr", ""),
        "sentiment_24h": row.get("ow1d_sentiment", "NEUTRAL"),
        "no_24h_news": row.get("no_24h_news", False),
        "cached_at": row.get("ow1d_cached_at_en", ""),
        "website": row.get("website", ""),
    }


# ─── Cron hourly refresh ──────────────────────────────────────────────────────

def _regen_break_weekly_if_missing(ticker: str, supa, logger) -> None:
    """Si le résumé IA de la semaine courante est absent, le génère (1 call LLM max/semaine/ticker)."""
    try:
        now = datetime.now(timezone.utc)
        current_ym = now.strftime("%Y-%m")
        day = now.day
        current_wnum = 1 if day <= 7 else 2 if day <= 14 else 3 if day <= 21 else 4
        row = (
            supa.table("breaks_weekly")
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
        from services.breakers_articles_service import get_weekly_ondemand
        get_weekly_ondemand(ticker)
        logger.info(f"[hourly_refresh] ✓ weekly regen {ticker} W{current_wnum} {current_ym}")
    except Exception as e:
        logger.warning(f"[hourly_refresh] weekly regen {ticker}: {e}")


def hourly_ow_regen() -> list[str]:
    """
    Cron :15 chaque heure.
    Cible : tickers pinnés + visités récemment avec no_24h_news=True.
    Si nouveaux articles 24h trouvés → gen_ow_both() déclenche Call 1 (DUMB + OW 24H).
    Retourne la liste des tickers qui ont trouvé de nouvelles news.
    """
    import logging
    logger = logging.getLogger(__name__)

    supa = _get_supabase()
    now = datetime.now(timezone.utc)
    six_h_ago = (now - timedelta(hours=6)).isoformat()

    pins = supa.table("pinned_stocks").select("ticker").execute()
    pinned = {r["ticker"] for r in (pins.data or [])}

    recent = supa.table("ow_cache") \
        .select("ticker").eq("no_24h_news", True).gte("ow1d_cached_at_en", six_h_ago).execute()
    recent_tickers = {r["ticker"] for r in (recent.data or [])}

    pinned_no_news = supa.table("ow_cache") \
        .select("ticker").eq("no_24h_news", True).in_("ticker", list(pinned)).execute()
    pinned_no_news_tickers = {r["ticker"] for r in (pinned_no_news.data or [])}

    regen_flagged = supa.table("ow_cache") \
        .select("ticker").eq("needs_ow_regen", True).execute()
    regen_tickers = {r["ticker"] for r in (regen_flagged.data or [])}

    candidates = pinned_no_news_tickers | recent_tickers | regen_tickers
    if not candidates:
        return []

    logger.info(f"[hourly_refresh] {len(candidates)} tickers à traiter ({len(regen_tickers)} needs_ow_regen, {len(pinned_no_news_tickers | recent_tickers)} no_24h_news)")

    refreshed: list[str] = []
    for ticker in candidates:
        try:
            # Fetch Yahoo avant de lire la DB
            try:
                from services.breakers_articles_service import _yahoo_scrap_accumulate
                current_ym = now.strftime("%Y-%m")
                _yahoo_scrap_accumulate(ticker, current_ym, supa)
            except Exception as acc_err:
                logger.debug(f"[hourly_refresh] yahoo accumulate {ticker}: {acc_err}")

            fresh_articles, _ = _fetch_news(ticker)
            articles_24h = _filter_24h(fresh_articles)

            needs_gen = ticker in regen_tickers or len(articles_24h) > 0
            if not needs_gen:
                supa.table("ow_cache").update({"ow1d_cached_at_en": now.isoformat()}).eq("ticker", ticker).execute()
                logger.info(f"[hourly_refresh] {ticker} — toujours 0 news 24h")
                continue

            gen_ow_both(ticker, force_24h=True)

            if ticker in regen_tickers:
                supa.table("ow_cache").update({"needs_ow_regen": False}).eq("ticker", ticker).execute()

            logger.info(f"[hourly_refresh] ✓ {ticker} — {len(articles_24h)} articles 24h")
            _regen_break_weekly_if_missing(ticker, supa, logger)
            refreshed.append(ticker)
        except Exception as e:
            logger.warning(f"[hourly_refresh] ✗ {ticker}: {e}")

    return refreshed
