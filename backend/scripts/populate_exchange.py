"""
One-shot : peuple la colonne exchange dans stock_news_cache pour tous les tickers connus.
Source : articles_merged_finn_yahoo (tous les tickers scrappés) + stock_news_cache existants.
"""
import os, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "../.env"))

import yfinance as yf
from supabase import create_client

supa = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])

# Tickers déjà dans stock_news_cache avec exchange
cache_rows = supa.table("ow_cache").select("ticker, exchange").execute().data or []
already_done = {r["ticker"] for r in cache_rows if r.get("exchange")}

# Tous les tickers connus depuis articles_merged_finn_yahoo
all_tickers_rows = supa.table("articles").select("ticker").execute().data or []
all_tickers = {r["ticker"] for r in all_tickers_rows}

to_fill = sorted(all_tickers - already_done)
print(f"{len(to_fill)} tickers à traiter sur {len(all_tickers)} total ({len(already_done)} déjà OK)")

ok, fail = 0, 0
for ticker in to_fill:
    try:
        exchange = yf.Ticker(ticker).fast_info.exchange or ""
        if exchange:
            supa.table("ow_cache").upsert(
                {"ticker": ticker, "exchange": exchange},
                on_conflict="ticker"
            ).execute()
            print(f"  ✓ {ticker} → {exchange}")
            ok += 1
        else:
            print(f"  - {ticker} → exchange vide")
            fail += 1
        time.sleep(0.3)
    except Exception as e:
        print(f"  ✗ {ticker} : {e}")
        fail += 1

print(f"\nDone : {ok} OK, {fail} échecs")
