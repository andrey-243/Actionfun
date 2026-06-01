"""
Migration one-shot : copie les dumb_en depuis stock_news_cache.news_raw
vers les objets article de article_archive (sera renommé articles_merged_finn_yahoo).

Matching en cascade : URL → title → summary
Rapport final : matchés, non-matchés, tickers traités.

Usage:
  python scripts/migrate_dumb_to_archive.py           # run migration
  python scripts/migrate_dumb_to_archive.py --dry-run # rapport sans écrire
"""

import os
import sys
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from supabase import create_client

PAGE_SIZE = 1000


def _supa():
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])


def _fetch_all(supa, table, select):
    rows, offset = [], 0
    while True:
        batch = supa.table(table).select(select).range(offset, offset + PAGE_SIZE - 1).execute().data or []
        rows.extend(batch)
        print(f"  {table}: {len(rows)} rows...", end="\r", flush=True)
        if len(batch) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
    print()
    return rows


def _normalize_url(url: str) -> str:
    return url.split("?")[0].rstrip("/").lower()


def _normalize_title(t: str) -> str:
    return t.strip().lower()[:100]


def _normalize_summary(s: str) -> str:
    return s.strip().lower()[:80]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    dry = args.dry_run

    supa = _supa()

    print("Chargement news_raw depuis stock_news_cache...")
    cache_rows = _fetch_all(supa, "stock_news_cache", "ticker,news_raw")

    # Garder seulement les tickers avec au moins 1 dumb dans news_raw
    tickers_with_dumb = {
        r["ticker"]: r["news_raw"]
        for r in cache_rows
        if r.get("news_raw") and any(a.get("brutalised") for a in r["news_raw"])
    }
    print(f"  {len(tickers_with_dumb)} tickers avec dumb_en dans news_raw\n")

    total_matched = 0
    total_unmatched = 0
    total_written = 0

    for i, (ticker, news_raw) in enumerate(tickers_with_dumb.items(), 1):
        # Construire index de recherche depuis news_raw (seulement les articles avec dumb)
        dumb_by_url: dict[str, str] = {}
        dumb_by_title: dict[str, str] = {}
        dumb_by_summary: dict[str, str] = {}

        for a in news_raw:
            dumb = a.get("brutalised")
            if not dumb:
                continue
            url = _normalize_url(a.get("url", ""))
            title = _normalize_title(a.get("title", ""))
            summary = _normalize_summary(a.get("summary", ""))
            if url:
                dumb_by_url[url] = dumb
            if title:
                dumb_by_title[title] = dumb
            if summary and len(summary) > 20:
                dumb_by_summary[summary] = dumb

        # Charger tous les year_months pour ce ticker depuis article_archive
        aa_rows = supa.table("article_archive").select("year_month,articles").eq("ticker", ticker).execute().data or []

        ticker_matched = ticker_unmatched = ticker_written = 0

        for row in aa_rows:
            ym = row["year_month"]
            articles = row.get("articles") or []
            updated = False

            for art in articles:
                # Skip si dumb_en déjà présent
                if art.get("dumb_en"):
                    continue

                url = _normalize_url(art.get("url", ""))
                title = _normalize_title(art.get("headline", ""))
                summary = _normalize_summary(art.get("summary", ""))

                dumb = None
                if url and url in dumb_by_url:
                    dumb = dumb_by_url[url]
                elif title and title in dumb_by_title:
                    dumb = dumb_by_title[title]
                elif summary and len(summary) > 20 and summary in dumb_by_summary:
                    dumb = dumb_by_summary[summary]

                if dumb:
                    art["dumb_en"] = dumb
                    updated = True
                    ticker_matched += 1
                else:
                    ticker_unmatched += 1

            if updated:
                ticker_written += 1
                if not dry:
                    supa.table("article_archive").update({"articles": articles}).eq("ticker", ticker).eq("year_month", ym).execute()

        total_matched += ticker_matched
        total_unmatched += ticker_unmatched
        total_written += ticker_written

        print(f"[{i}/{len(tickers_with_dumb)}] {ticker}: {ticker_matched} matchés, {ticker_unmatched} non-matchés, {ticker_written} year_months mis à jour{'(dry)' if dry else ''}")

    print(f"\n{'='*50}")
    print(f"TOTAL matchés   : {total_matched}")
    print(f"TOTAL non-matchés: {total_unmatched}")
    print(f"year_months écrits: {total_written}")
    if dry:
        print("Dry-run — rien écrit.")
    else:
        print("Migration terminée.")


if __name__ == "__main__":
    main()
