import httpx
import os
import logging
from datetime import date, datetime, timezone, timedelta
from supabase import create_client

logger = logging.getLogger(__name__)


def _get_supabase():
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])


def _articles_24h(supa, ticker: str) -> list[dict]:
    now = datetime.now(timezone.utc)
    cutoff_ts = int((now - timedelta(hours=24)).timestamp())
    year_months = {now.strftime("%Y-%m")}
    if now.day == 1:
        prev = now.replace(day=1) - timedelta(days=1)
        year_months.add(prev.strftime("%Y-%m"))

    raw = []
    for ym in sorted(year_months):
        r = supa.table("articles_merged_finn_yahoo").select("articles") \
            .eq("ticker", ticker).eq("year_month", ym).maybe_single().execute()
        if r and r.data and r.data.get("articles"):
            raw.extend(r.data["articles"])

    articles = [
        {"headline": a.get("headline", ""), "source": a.get("source", ""), "url": a.get("url", ""), "datetime": int(a["datetime"])}
        for a in raw
        if a.get("datetime", 0) >= cutoff_ts and a.get("headline")
    ]
    articles.sort(key=lambda x: x["datetime"], reverse=True)
    return articles[:12]


def run_eod_digest_email():
    """18h30 — envoie les digests EOD depuis stock_news_cache (plus de daily_digest)."""
    supa = _get_supabase()

    pins = supa.table("pinned_stocks").select("ticker").execute()
    tickers = list({r["ticker"] for r in (pins.data or [])})

    if not tickers:
        logger.info("EOD digest email — aucun ticker pinné")
        return

    digests = []
    for ticker in tickers:
        row = supa.table("stock_news_cache") \
            .select("ai_summary_24h_en, ai_summary_24h, ai_sentiment_24h") \
            .eq("ticker", ticker).maybe_single().execute()
        if not row or not row.data:
            continue
        summary = row.data.get("ai_summary_24h_en") or row.data.get("ai_summary_24h", "")
        if not summary:
            continue
        articles = _articles_24h(supa, ticker)
        digests.append({
            "ticker": ticker,
            "summary": summary,
            "sentiment": row.data.get("ai_sentiment_24h", "NEUTRAL"),
            "articles": articles,
        })

    if not digests:
        logger.info("EOD digest email — aucun digest disponible")
        return

    logger.info(f"EOD digest email — {len(digests)} tickers à envoyer")
    _send_digest_emails(digests, supa)


def _send_digest_emails(digests: list[dict], supa):
    from services.email_service import send_digest_email

    users = supa.table("user_preferences").select("user_id, digest_tickers").eq("notify_email", True).execute()
    if not users.data:
        return

    for user_pref in users.data:
        uid = user_pref["user_id"]
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
