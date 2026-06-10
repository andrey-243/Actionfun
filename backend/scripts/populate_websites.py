"""
One-shot script : populate stock_news_cache.website pour tous les tickers en cache.
Lance une seule fois, pas de re-run nécessaire sauf ajout massif de nouveaux tickers.

Usage:
    python scripts/populate_websites.py
    python scripts/populate_websites.py --ticker AMZN   # un seul ticker
"""
import os, sys, time, argparse
from urllib.parse import urlparse

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from dotenv import load_dotenv
load_dotenv()

import yfinance as yf
from supabase import create_client

supa = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])


def get_domain(ticker: str) -> str | None:
    try:
        url = yf.Ticker(ticker).info.get("website", "") or ""
        if not url:
            return None
        domain = urlparse(url).netloc.lstrip("www.")
        return domain or None
    except Exception:
        return None


def run(tickers: list[str]):
    print(f"{len(tickers)} tickers à traiter")
    ok = skip = fail = 0

    for i, ticker in enumerate(tickers, 1):
        domain = get_domain(ticker)
        if domain:
            supa.table("ow_cache").update({"website": domain}).eq("ticker", ticker).execute()
            print(f"  [{i}/{len(tickers)}] ✓ {ticker} → {domain}")
            ok += 1
        else:
            print(f"  [{i}/{len(tickers)}] — {ticker} (pas de website)")
            fail += 1

        if i % 10 == 0:
            time.sleep(1)  # éviter le rate limit yfinance

    print(f"\nDone — {ok} ok · {fail} sans website")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticker", help="Traiter un seul ticker")
    args = parser.parse_args()

    if args.ticker:
        run([args.ticker.upper()])
    else:
        rows = supa.table("ow_cache").select("ticker").execute()
        tickers = [r["ticker"] for r in (rows.data or [])]
        run(tickers)
