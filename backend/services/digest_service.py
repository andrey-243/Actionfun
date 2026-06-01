import httpx
import os
import logging
from datetime import date, datetime, timezone
from supabase import create_client

logger = logging.getLogger(__name__)

DIGEST_PROMPT = """Tu es un analyste financier. Voici les news du jour pour {ticker}.

Articles :
{articles_text}

Rédige un résumé complet en 4-6 phrases :
- Ce qui s'est passé aujourd'hui pour ce titre
- Les éléments macro ou sectoriels pertinents
- L'impact probable sur le cours à court terme
- Le sentiment global : BULLISH, BEARISH ou NEUTRAL

Format JSON strict :
{{
  "summary": "texte du résumé",
  "sentiment": "BULLISH"
}}"""

DIGEST_PROMPT_EN = """You are a financial analyst. Here are today's news for {ticker}.

Articles:
{articles_text}

Write a complete 4-6 sentence summary covering:
- What happened today for this stock
- Relevant macro or sector elements
- Probable short-term impact on the stock price
- Overall sentiment: BULLISH, BEARISH or NEUTRAL

Strict JSON format:
{{
  "summary": "summary text",
  "sentiment": "BULLISH"
}}"""


def _get_supabase():
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])


def _fetch_today_news(ticker: str) -> list[dict]:
    today = date.today()
    r = httpx.get(
        "https://finnhub.io/api/v1/company-news",
        params={
            "symbol": ticker,
            "from": today.isoformat(),
            "to": today.isoformat(),
            "token": os.environ["FINNHUB_API_KEY"],
        },
        timeout=10,
    )
    r.raise_for_status()
    raw = r.json()[:15]
    return [
        {
            "headline": a["headline"],
            "source": a["source"],
            "url": a["url"],
            "datetime": a["datetime"],
        }
        for a in raw
        if a.get("headline")
    ]


def _groq_digest(ticker: str, articles: list[dict], lang: str = "fr") -> dict:
    from services.llm_service import llm_json
    articles_text = "\n".join(f"- {a['headline']}" for a in articles[:12])
    prompt = (DIGEST_PROMPT_EN if lang == "en" else DIGEST_PROMPT).format(
        ticker=ticker, articles_text=articles_text
    )
    return llm_json(
        [{"role": "user", "content": prompt}],
        groq_model="meta-llama/llama-4-scout-17b-16e-instruct",
        max_tokens=500,
        temperature=0.3,
        timeout=30,
    )


def _fetch_from_yahoo_cache(ticker: str, supa) -> dict | None:
    """Fallback : utilise le summary 24h déjà généré dans stock_news_cache.
    Si absent, déclenche un refresh actif via overview_service."""
    row = (
        supa.table("stock_news_cache")
        .select("ai_summary_24h, ai_summary_24h_en, ai_sentiment_24h, news_raw")
        .eq("ticker", ticker)
        .maybe_single()
        .execute()
    )
    has_summary = row and row.data and (row.data.get("ai_summary_24h_en") or row.data.get("ai_summary_24h"))
    if not has_summary:
        try:
            from services.overview_service import get_news
            get_news(ticker)
            row = (
                supa.table("stock_news_cache")
                .select("ai_summary_24h, ai_summary_24h_en, ai_sentiment_24h, news_raw")
                .eq("ticker", ticker)
                .maybe_single()
                .execute()
            )
        except Exception as e:
            logger.warning(f"  overview_service fallback failed for {ticker}: {e}")
    if not row or not row.data or not (row.data.get("ai_summary_24h_en") or row.data.get("ai_summary_24h")):
        return None

    cutoff = (datetime.now(timezone.utc).timestamp()) - 86400
    articles = []
    for a in (row.data.get("news_raw") or []):
        try:
            pub = a.get("pub_date", "")
            if pub:
                ts = datetime.fromisoformat(pub.replace("Z", "+00:00")).timestamp()
            else:
                ts = float(a.get("datetime", 0) or 0)
            if not ts:
                continue
        except Exception:
            continue
        if ts >= cutoff:
            articles.append({"headline": a.get("title", "") or a.get("headline", ""), "source": a.get("source", ""), "url": a.get("url", ""), "datetime": int(ts)})

    summary = row.data.get("ai_summary_24h_en") or row.data.get("ai_summary_24h", "")
    return {
        "summary": summary,
        "sentiment": row.data.get("ai_sentiment_24h") or "NEUTRAL",
        "articles": articles[:12],
        "source": "yahoo",
    }


def generate_digest_for_ticker(ticker: str, lang: str = None, force: bool = False) -> dict | None:
    """Génère et stocke le digest du jour pour un ticker. Retourne None si pas de news."""
    lang = lang or os.environ.get("LLM_LANG", "en")
    today = date.today().isoformat()
    supa = _get_supabase()

    if not force:
        existing = supa.table("daily_digest").select("*").eq("ticker", ticker).eq("date", today).maybe_single().execute()
        if existing and existing.data:
            if lang == "en" and existing.data.get("summary_en"):
                return existing.data
            if lang == "fr":
                return existing.data

    # 1. Lire le cache Yahoo (stock_news_cache) — déjà enrichi par overview_service, zéro LLM
    yahoo = _fetch_from_yahoo_cache(ticker, supa)
    llm_source = "cache"
    if yahoo:
        articles = yahoo["articles"]
        summary_key = "summary_en" if lang == "en" else "summary"
        summary = yahoo.get(summary_key) or yahoo.get("summary", "")
        sentiment = yahoo.get("sentiment", "NEUTRAL")
    else:
        # 2. Fallback : fetch Finnhub + LLM si le cache est vide (ticker jamais enrichi)
        articles = _fetch_today_news(ticker)
        if not articles:
            return None
        ai = _groq_digest(ticker, articles, lang=lang)
        summary = ai.get("summary", "")
        sentiment = ai.get("sentiment", "NEUTRAL")
        llm_source = ai.get("_llm_source", "unknown")

    if lang == "en":
        row = {
            "ticker": ticker,
            "date": today,
            "summary_en": summary,
            "sentiment": sentiment,
            "llm_source": llm_source,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
        supa.table("daily_digest").upsert(row).execute()
    else:
        row = {
            "ticker": ticker,
            "date": today,
            "summary": summary,
            "sentiment": sentiment,
            "llm_source": llm_source,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
        supa.table("daily_digest").upsert(row).execute()

    return row


def run_eod_digest_generation():
    """18h00 — génère les digests du jour pour tous les tickers pinnés."""
    supa = _get_supabase()
    pins = supa.table("pinned_stocks").select("ticker").execute()
    tickers = list({r["ticker"] for r in (pins.data or [])})

    logger.info(f"EOD digest generation — {len(tickers)} tickers à traiter")
    for ticker in tickers:
        try:
            result = generate_digest_for_ticker(ticker)
            if result:
                logger.info(f"  ✓ {ticker}")
            else:
                logger.info(f"  — {ticker} (pas de news)")
        except Exception as e:
            logger.error(f"  ✗ {ticker} : {e}")


def run_eod_digest_email():
    """18h30 — envoie les emails avec les digests déjà générés."""
    supa = _get_supabase()
    today = date.today().isoformat()

    rows = supa.table("daily_digest").select("*").eq("date", today).execute()
    digests = rows.data or []

    if not digests:
        logger.info("EOD digest email — aucun digest disponible aujourd'hui")
        return

    logger.info(f"EOD digest email — {len(digests)} digests à envoyer")
    _send_digest_emails(digests, supa)


def _send_digest_emails(digests: list[dict], supa):
    from services.email_service import send_digest_email

    users = supa.table("user_preferences").select("user_id, digest_tickers").eq("notify_email", True).execute()
    if not users.data:
        return

    for user_pref in users.data:
        uid = user_pref["user_id"]
        # digest_tickers=None → tous les tickers pinnés ; sinon filtre sur la sélection
        allowed = set(user_pref["digest_tickers"]) if user_pref.get("digest_tickers") else None

        pins = supa.table("pinned_stocks").select("ticker").eq("user_id", uid).execute()
        user_tickers = {r["ticker"] for r in (pins.data or [])}

        if allowed is not None:
            user_tickers = user_tickers & allowed

        user_digests = [d for d in digests if d["ticker"] in user_tickers]
        if not user_digests:
            continue

        try:
            r = httpx.get(
                f"https://api.supabase.com/v1/projects/qxfbqzjkianxsuurgckd/auth/users/{uid}",
                headers={"Authorization": f"Bearer {os.environ['SUPABASE_MANAGEMENT_TOKEN']}"},
                timeout=10,
            )
            email = r.json().get("email")
            if email:
                send_digest_email(email, user_digests)
        except Exception as e:
            logger.error(f"Email failed for user {uid}: {e}")
