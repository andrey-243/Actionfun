"""
scrap_google_rss.py — Google News RSS, source top-1.

Pour chaque ticker × mois (12 derniers mois) :
  1. Skip si is_fresh (≥25 arts ET cached_at <3j)
  2. Fetch RSS avec filtres after:/before: pour le mois (num=100)
  3. Normalise + merge + upsert dans articles
  4. Post-scrap : gen_dumb → si W≥10 : W breaker → si M≥25 : M breaker

Usage :
  python scripts/scrap_google_rss.py [--months 12] [--ticker AAPL]
"""

import argparse
import logging
import os
import re
import sys
import time
from calendar import monthrange
from datetime import date, datetime, timezone
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parents[1] / ".env")
os.environ.setdefault("BATCH_MODE", "1")

from supabase import create_client
from services.yfinance_service import bulk_fetch_company_names as _bulk_fetch_company_names
from services.breakers_articles_service import (
    _build_rss_query, _detect_region,
    _normalize_google_rss, _upsert_articles_merge, _is_month_fresh,
    _fetch_rss_month, scrap_gen_weekly, scrap_gen_monthly,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

PAUSE_S = 1.0


def _telegram(msg: str) -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        return
    html = re.sub(r'\*([^*]+)\*', r'<b>\1</b>', msg)
    html = re.sub(r'(?<!\w)_([^_]+)_(?!\w)', r'<i>\1</i>', html)
    html += "\n📍 <i>scrap_google_rss</i>"
    try:
        httpx.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": html, "parse_mode": "HTML"},
            timeout=8,
        )
    except Exception:
        pass
MAX_MONTHS = 12
MIN_M = 25


def _get_supabase():
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])


def _pinned_tickers(supa) -> list[str]:
    from collections import Counter
    rows = supa.table("pinned_stocks").select("ticker").execute()
    counts = Counter(r["ticker"] for r in (rows.data or []))
    return sorted(t for t, c in counts.items() if c >= 1)


def _year_months_back(n: int) -> list[str]:
    today = date.today()
    result = []
    for i in range(n):
        m, y = today.month - i, today.year
        while m <= 0:
            m += 12
            y -= 1
        result.append(f"{y:04d}-{m:02d}")
    return result


def run(months: int = MAX_MONTHS, ticker_filter: str = "") -> None:
    supa = _get_supabase()
    tickers = [ticker_filter.upper()] if ticker_filter else _pinned_tickers(supa)
    year_months = _year_months_back(months)
    company_names = _bulk_fetch_company_names(tickers)

    logger.info(f"scrap_google_rss: {len(tickers)} tickers × {len(year_months)} months")
    total_added, calls = 0, 0
    source_counts: dict[str, int] = {}

    today = date.today()
    today_ym = today.strftime("%Y-%m")
    _d = today.day
    current_w = 1 if _d <= 7 else 2 if _d <= 14 else 3 if _d <= 21 else 4

    for ticker in tickers:
        cname  = company_names.get(ticker, "")
        query  = _build_rss_query(ticker, cname)
        region = _detect_region(ticker)
        logger.info(f"[{ticker}] region={region} query={query!r}")

        for ym in year_months:
            if _is_month_fresh(ticker, ym, supa, min_count=MIN_M, max_days=3):
                if ym == today_ym:
                    row = supa.table("articles").select("articles").eq("ticker", ticker).eq("year_month", ym).limit(1).execute()
                    arts = (row.data[0].get("articles") or []) if row and row.data else []
                    week_count = 0
                    for a in arts:
                        ts = a.get("datetime", 0)
                        if not ts:
                            continue
                        d = datetime.fromtimestamp(ts, tz=timezone.utc).day
                        w = 1 if d <= 7 else 2 if d <= 14 else 3 if d <= 21 else 4
                        if w == current_w:
                            week_count += 1
                    if week_count >= 10:
                        logger.info(f"  {ticker} {ym}: fresh ({len(arts)} arts mois · {week_count} arts semaine) → skip")
                        continue
                    logger.info(f"  {ticker} {ym}: mois OK ({len(arts)} arts) mais semaine KO ({week_count} arts) → autres sources")
                else:
                    logger.info(f"  {ticker} {ym}: fresh → skip")
                    continue

            if calls > 0:
                time.sleep(PAUSE_S)

            raw = _fetch_rss_month(query, ym)
            calls += 1
            logger.info(f"  {ticker} {ym}: {len(raw)} raw articles from RSS")

            if not raw:
                continue

            normalized = [_normalize_google_rss(a) for a in raw]
            added, src = _upsert_articles_merge(ticker, ym, normalized, "google_rss", supa)
            total_added += added
            if added > 0:
                source_counts[src] = source_counts.get(src, 0) + added

            if added > 0:
                try:
                    from services.overview_service import gen_dumb
                    gen_dumb(ticker, cascade="dumb_scrap")
                except Exception as e:
                    logger.warning(f"  gen_dumb {ticker}: {e}")
                scrap_gen_weekly(ticker, ym, supa, caller="google_rss")
                scrap_gen_monthly(ticker, ym, supa, caller="google_rss")

    src_line = " | ".join(f"{k}: {v}" for k, v in sorted(source_counts.items(), key=lambda x: -x[1]))
    logger.info(f"scrap_google_rss done: {calls} calls, {total_added} articles added" + (f" ({src_line})" if src_line else ""))
    _telegram(
        f"✅ *scrap_google_rss terminé*\n"
        f"📞 {calls} calls · 📰 +{total_added} articles\n"
        + (f"📊 {src_line}" if src_line else "")
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--months", type=int, default=MAX_MONTHS, help="Nb de mois à scraper (défaut: 12)")
    parser.add_argument("--ticker", default="", help="Ticker unique (optionnel)")
    args = parser.parse_args()
    run(months=args.months, ticker_filter=args.ticker)
