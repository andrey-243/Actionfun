"""
Populate stock_news_cache.website + logo_url pour tous les tickers scrappés (US + EU + ASIA).

Sources :
  - domain  : yfinance ticker.info["website"]
  - logo_url : https://img.logo.dev/{domain}?token={LOGODEV_PUBLIC_KEY}&retina=true

Usage :
    python scripts/populate_logos.py            # tous les tickers manquants
    python scripts/populate_logos.py --all      # force re-run même si déjà rempli
    python scripts/populate_logos.py --ticker AAPL
"""
import os, sys, time, json, argparse
from urllib.parse import urlparse

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from dotenv import load_dotenv
load_dotenv()

import yfinance as yf
from supabase import create_client

supa = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])
LOGODEV_PK = os.environ["LOGODEV_PUBLIC_KEY"]

PROGRESS_FILES = [
    os.path.join(os.path.dirname(__file__), "batch_progress_scrape_us.json"),
    os.path.join(os.path.dirname(__file__), "batch_progress_scrape_eu.json"),
    os.path.join(os.path.dirname(__file__), "batch_progress_scrape_asia.json"),
]


def _all_scraped_tickers() -> list[str]:
    tickers = []
    for f in PROGRESS_FILES:
        try:
            d = json.load(open(f))
            tickers.extend(d.get("scraped", []))
        except Exception:
            pass
    return sorted(set(tickers))


def _get_domain(ticker: str) -> str | None:
    try:
        url = (yf.Ticker(ticker).info or {}).get("website", "") or ""
        if not url:
            return None
        domain = urlparse(url).netloc.lstrip("www.")
        return domain or None
    except Exception:
        return None


def _logo_url(domain: str) -> str:
    return f"https://img.logo.dev/{domain}?token={LOGODEV_PK}&retina=true"


def _already_done(ticker: str, force: bool) -> bool:
    if force:
        return False
    row = supa.table("ow_cache").select("website, logo_url").eq("ticker", ticker).limit(1).execute()
    if not row.data:
        return False
    r = row.data[0]
    return bool(r.get("website") and r.get("logo_url"))


def run(tickers: list[str], force: bool = False):
    print(f"{len(tickers)} tickers à traiter (force={force})")
    ok = skip = no_domain = 0

    for i, ticker in enumerate(tickers, 1):
        if _already_done(ticker, force):
            skip += 1
            continue

        domain = _get_domain(ticker)
        if not domain:
            print(f"  [{i}/{len(tickers)}] — {ticker} : pas de website yfinance")
            no_domain += 1
            # Upsert minimal pour que la row existe
            supa.table("ow_cache").upsert({"ticker": ticker}, on_conflict="ticker").execute()
            time.sleep(0.3)
            continue

        logo = _logo_url(domain)
        supa.table("ow_cache").upsert(
            {"ticker": ticker, "website": domain, "logo_url": logo},
            on_conflict="ticker",
        ).execute()
        print(f"  [{i}/{len(tickers)}] ✓ {ticker} → {domain}")
        ok += 1

        # Pause tous les 20 tickers pour respecter yfinance rate limit
        if i % 20 == 0:
            time.sleep(2)

    print(f"\nDone — {ok} ok · {skip} déjà remplis · {no_domain} sans domain")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticker", help="Un seul ticker")
    parser.add_argument("--all", action="store_true", help="Force re-run même si déjà rempli")
    args = parser.parse_args()

    if args.ticker:
        run([args.ticker.upper()], force=args.all)
    else:
        run(_all_scraped_tickers(), force=args.all)
