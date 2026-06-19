"""
scrap_alphavantage.py — Alpha Vantage NEWS_SENTIMENT, source last resort (M<25 après RSS+NewsAPI+Marketaux).

Priorité :
  P1 : SKWD/vides EU+ASIA, mois courant
  P2 : mois vides EU+ASIA (passés)
  P3 : US mois courant si <25 arts
  P4 : US mois passés si <25 arts

Budget  : 25 req/j (plan gratuit AV). Le script s'arrête dès ce seuil atteint.
Scope   : tous tickers pinnés · skip is_fresh · sans cap articles.

Usage :
  python scripts/scrap_alphavantage.py [--months 12] [--ticker AAPL] [--budget 25]
"""

import argparse
import logging
import os
import re
import sys
import time
from datetime import date
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
    _normalize_av, _upsert_articles_merge, _is_month_fresh,
    _fetch_av_month, scrap_gen_weekly, scrap_gen_monthly,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

PAUSE_S    = 12.0  # AV gratuit = 5 req/min → 12s entre calls
MAX_MONTHS = 12
MIN_M      = 25
BUDGET     = 25


def _telegram(msg: str) -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        return
    html = re.sub(r'\*([^*]+)\*', r'<b>\1</b>', msg)
    html = re.sub(r'(?<!\w)_([^_]+)_(?!\w)', r'<i>\1</i>', html)
    html += "\n📍 <i>scrap_alphavantage</i>"
    try:
        httpx.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": html, "parse_mode": "HTML"},
            timeout=8,
        )
    except Exception:
        pass


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


def _build_targets(tickers: list[str], year_months: list[str], supa) -> list[tuple[str, str]]:
    today_ym = year_months[0]
    past_yms = year_months[1:]
    p1, p2, p3, p4 = [], [], [], []
    for ticker in tickers:
        region = _detect_region(ticker)
        for ym in [today_ym] + past_yms:
            if _is_month_fresh(ticker, ym, supa, min_count=MIN_M, max_days=3):
                continue
            if region in ("EU", "ASIA"):
                (p1 if ym == today_ym else p2).append((ticker, ym))
            else:
                (p3 if ym == today_ym else p4).append((ticker, ym))
    return p1 + p2 + p3 + p4


def run(months: int = MAX_MONTHS, ticker_filter: str = "", budget: int = BUDGET) -> None:
    api_key = os.environ.get("ALPHAVANTAGE_API_KEY", "")
    if not api_key:
        logger.error("ALPHAVANTAGE_API_KEY manquante dans .env")
        return

    supa = _get_supabase()
    tickers = [ticker_filter.upper()] if ticker_filter else _pinned_tickers(supa)
    year_months = _year_months_back(months)
    company_names = _bulk_fetch_company_names(tickers)

    targets = _build_targets(tickers, year_months, supa)
    logger.info(f"scrap_alphavantage: {len(targets)} (ticker, mois) cibles · budget={budget}")

    total_added, calls = 0, 0
    source_counts: dict[str, int] = {}

    for ticker, ym in targets:
        if calls >= budget:
            logger.info(f"Budget AV épuisé ({budget} req)")
            break

        if calls > 0:
            time.sleep(PAUSE_S)

        raw = _fetch_av_month(ticker, ym, api_key)
        calls += 1
        if raw is None:
            logger.info(f"  {ticker} {ym}: 429 rate limit (call {calls}/{budget})")
            continue
        logger.info(f"  {ticker} {ym}: {len(raw)} raw articles (call {calls}/{budget})")
        if not raw:
            continue

        normalized = [_normalize_av(a) for a in raw if a.get("title") and a.get("url")]
        added, src = _upsert_articles_merge(ticker, ym, normalized, "alphavantage", supa)
        total_added += added
        if added > 0:
            source_counts[src] = source_counts.get(src, 0) + added

        if added > 0:
            try:
                from services.overview_service import gen_dumb
                gen_dumb(ticker, cascade="dumb_scrap")
            except Exception as e:
                logger.warning(f"  gen_dumb {ticker}: {e}")
            scrap_gen_weekly(ticker, ym, supa, caller="alphavantage")
            scrap_gen_monthly(ticker, ym, supa, caller="alphavantage")

    src_line = " | ".join(f"{k}: {v}" for k, v in sorted(source_counts.items(), key=lambda x: -x[1]))
    logger.info(f"scrap_alphavantage done: {calls} calls, {total_added} articles added" + (f" ({src_line})" if src_line else ""))
    _telegram(
        f"✅ *scrap_alphavantage terminé*\n"
        f"📞 {calls} calls · 📰 +{total_added} articles\n"
        + (f"📊 {src_line}" if src_line else "")
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--months", type=int, default=MAX_MONTHS)
    parser.add_argument("--ticker", default="")
    parser.add_argument("--budget", type=int, default=BUDGET, help="Max requêtes AV (défaut: 25 = quota gratuit)")
    args = parser.parse_args()
    run(months=args.months, ticker_filter=args.ticker, budget=args.budget)
