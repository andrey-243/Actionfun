"""
Digest emails — Daily (18h30) & Weekly (vendredi 22h30).

run_eod_digest_email()    : lit stock_news_cache (ai_summary_24h_en/fr) + articles 24h
run_weekly_digest_email() : lit stock_news_cache (ai_summary_en/fr) + articles 7j (5 max)

Langue par user : user_preferences.lang ('en' | 'fr', default 'fr').
Zéro appel LLM — tout lu depuis le cache existant.
"""
import os
import logging
from datetime import date, datetime, timezone, timedelta
from supabase import create_client

logger = logging.getLogger(__name__)


def _get_supabase():
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])


def _articles_window(supa, ticker: str, hours: int) -> list[dict]:
    now = datetime.now(timezone.utc)
    cutoff_ts = int((now - timedelta(hours=hours)).timestamp())
    year_months = {now.strftime("%Y-%m")}
    if now.day <= (hours // 24 + 1):
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
    return articles


def _ensure_fr_translations(supa, tickers: list[str], col_en: str, col_fr: str) -> None:
    """Traduit les summaries EN→FR manquants avant snapshot. Écrit en DB."""
    from services.translation_service import translate_batch_to_fr

    rows = supa.table("stock_news_cache").select(f"ticker, {col_en}, {col_fr}") \
        .in_("ticker", tickers).execute()

    to_translate = [
        (r["ticker"], r[col_en])
        for r in (rows.data or [])
        if r.get(col_en) and not r.get(col_fr)
    ]
    if not to_translate:
        return

    tickers_needed, texts = zip(*to_translate)
    try:
        translated = translate_batch_to_fr(list(texts))
        for ticker, fr_text in zip(tickers_needed, translated):
            if fr_text:
                supa.table("stock_news_cache").update({col_fr: fr_text}) \
                    .eq("ticker", ticker).execute()
        logger.info(f"Pre-digest FR translations: {len(tickers_needed)} tickers ({col_fr})")
    except Exception as e:
        logger.warning(f"Pre-digest FR translation failed ({col_fr}): {e} — fallback EN")


def _build_digests_daily(supa, tickers: list[str]) -> list[dict]:
    """Charge les données daily pour tous les tickers (les deux langues)."""
    result = []
    for ticker in tickers:
        row = supa.table("stock_news_cache") \
            .select("ai_summary_24h_en, ai_summary_24h_fr, ai_sentiment_24h") \
            .eq("ticker", ticker).maybe_single().execute()
        if not row or not row.data:
            continue
        if not row.data.get("ai_summary_24h_en") and not row.data.get("ai_summary_24h_fr"):
            continue
        articles = _articles_window(supa, ticker, hours=24)[:12]
        result.append({
            "ticker": ticker,
            "summary_en": row.data.get("ai_summary_24h_en", ""),
            "summary_fr": row.data.get("ai_summary_24h_fr", ""),
            "sentiment": row.data.get("ai_sentiment_24h", "NEUTRAL"),
            "articles": articles,
        })
    return result


def _build_digests_weekly(supa, tickers: list[str]) -> list[dict]:
    """Charge les données weekly pour tous les tickers (les deux langues)."""
    result = []
    for ticker in tickers:
        row = supa.table("stock_news_cache") \
            .select("ai_summary_en, ai_summary_fr") \
            .eq("ticker", ticker).maybe_single().execute()
        if not row or not row.data:
            continue
        if not row.data.get("ai_summary_en") and not row.data.get("ai_summary_fr"):
            continue
        articles = _articles_window(supa, ticker, hours=7 * 24)[:5]
        result.append({
            "ticker": ticker,
            "summary_en": row.data.get("ai_summary_en", ""),
            "summary_fr": row.data.get("ai_summary_fr", ""),
            "sentiment": None,
            "articles": articles,
        })
    return result


def _user_digest(data: dict, lang: str) -> dict:
    """Retourne le dict final pour un user avec la bonne langue."""
    summary = data["summary_en"] if lang == "en" else (data["summary_fr"] or data["summary_en"])
    return {
        "ticker": data["ticker"],
        "summary": summary,
        "sentiment": data["sentiment"],
        "articles": data["articles"],
    }


def _send_emails(all_data: list[dict], supa, notify_field: str, period: str):
    from services.email_service import send_digest_email

    users = supa.table("user_preferences") \
        .select("user_id, digest_tickers, lang") \
        .eq(notify_field, True).execute()
    if not users.data:
        return

    for user_pref in users.data:
        uid = user_pref["user_id"]
        lang = user_pref.get("lang") or "en"
        allowed = set(user_pref["digest_tickers"]) if user_pref.get("digest_tickers") else None

        pins = supa.table("pinned_stocks").select("ticker").eq("user_id", uid).execute()
        user_tickers = {r["ticker"] for r in (pins.data or [])}
        if allowed is not None:
            user_tickers = user_tickers & allowed

        user_digests = [
            _user_digest(d, lang)
            for d in all_data
            if d["ticker"] in user_tickers
        ]
        if not user_digests:
            continue

        try:
            user_data = supa.auth.admin.get_user_by_id(uid)
            email = user_data.user.email if user_data.user else None
            if email:
                send_digest_email(email, user_digests, lang=lang, period=period)
        except Exception as e:
            logger.error(f"Email failed for user {uid}: {e}")


def run_eod_digest_email():
    """18h30 — envoie les digests daily depuis stock_news_cache."""
    supa = _get_supabase()

    pins = supa.table("pinned_stocks").select("ticker").execute()
    tickers = list({r["ticker"] for r in (pins.data or [])})
    if not tickers:
        logger.info("Daily digest — aucun ticker pinné")
        return

    _ensure_fr_translations(supa, tickers, "ai_summary_24h_en", "ai_summary_24h_fr")
    all_data = _build_digests_daily(supa, tickers)  # snapshot post-translation
    if not all_data:
        logger.info("Daily digest — aucun summary disponible")
        return

    logger.info(f"Daily digest — {len(all_data)} tickers")
    _send_emails(all_data, supa, notify_field="notify_email", period="daily")


def run_weekly_digest_email():
    """Vendredi 22h30 — envoie les digests weekly depuis stock_news_cache."""
    supa = _get_supabase()

    pins = supa.table("pinned_stocks").select("ticker").execute()
    tickers = list({r["ticker"] for r in (pins.data or [])})
    if not tickers:
        logger.info("Weekly digest — aucun ticker pinné")
        return

    _ensure_fr_translations(supa, tickers, "ai_summary_en", "ai_summary_fr")
    all_data = _build_digests_weekly(supa, tickers)  # snapshot post-translation
    if not all_data:
        logger.info("Weekly digest — aucun summary disponible")
        return

    logger.info(f"Weekly digest — {len(all_data)} tickers")
    _send_emails(all_data, supa, notify_field="notify_weekly", period="weekly")
