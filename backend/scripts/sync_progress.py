"""
Sync progress files from Supabase state.

Generates batch_progress_{phase}_{region}[_{lang}].json for every combo,
reflecting what's actually in the DB rather than local tracking state.

Usage:
  python scripts/sync_progress.py           # sync all combos
  python scripts/sync_progress.py --dry-run # print counts without writing
"""

import json
import os
import sys
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from supabase import create_client
from data.tickers import US, EU, ASIA

SCRIPTS_DIR = Path(__file__).parent

REGIONS = {"us": US, "eu": EU, "asia": ASIA}
LANGS   = ["en", "fr"]
BUBBLES_DONE_THRESHOLD = 8  # cause_en/cause count >= 8 → ticker considéré done (min observé ~11)


def _supa():
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])


def _fetch_all(supa, table: str, select: str, page_size: int = 1000) -> list[dict]:
    """Paginate through a table and return all rows."""
    rows, offset = [], 0
    while True:
        resp = supa.table(table).select(select).range(offset, offset + page_size - 1).execute()
        batch = resp.data or []
        rows.extend(batch)
        print(f"  {table}: {len(rows)} rows fetched...", end="\r", flush=True)
        if len(batch) < page_size:
            break
        offset += page_size
    print()
    return rows


def build_scrape_state(supa) -> set[str]:
    """Tickers with at least one row in article_archive (source of truth for all regions)."""
    rows = _fetch_all(supa, "article_archive", "ticker")
    return {r["ticker"] for r in rows}


def build_enrich_state(supa, lang: str) -> set[str]:
    """Tickers with ai_summary_en / ai_summary not null."""
    col = "ai_summary_en" if lang == "en" else "ai_summary"
    rows = _fetch_all(supa, "stock_news_cache", f"ticker,{col}")
    return {r["ticker"] for r in rows if r.get(col)}


def build_bubbles_state(supa, lang: str) -> set[str]:
    """Tickers with >= THRESHOLD cause_en / cause rows (proxy for 'all dots done')."""
    col = "cause_en" if lang == "en" else "cause"
    rows = _fetch_all(supa, "event_causes", f"ticker,{col}")

    counts: dict[str, int] = {}
    for r in rows:
        if r.get(col):
            counts[r["ticker"]] = counts.get(r["ticker"], 0) + 1

    return {ticker for ticker, n in counts.items() if n >= BUBBLES_DONE_THRESHOLD}


def write_progress(path: Path, data: dict, dry_run: bool):
    if dry_run:
        print(f"  [dry-run] would write {path.name}")
        for k, v in data.items():
            print(f"    {k}: {len(v)}")
    else:
        path.write_text(json.dumps(data, indent=2))
        print(f"  wrote {path.name}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    supa = _supa()
    dry = args.dry_run

    print("Fetching Supabase state...")
    scraped_all    = build_scrape_state(supa)
    enrich_en_all  = build_enrich_state(supa, "en")
    enrich_fr_all  = build_enrich_state(supa, "fr")
    bubbles_en_all = build_bubbles_state(supa, "en")
    bubbles_fr_all = build_bubbles_state(supa, "fr")

    print(f"  stock_news_cache: {len(scraped_all)} scraped  |  "
          f"{len(enrich_en_all)} enriched_en  |  {len(enrich_fr_all)} enriched_fr")
    print(f"  event_causes:     {len(bubbles_en_all)} bubbles_done_en  |  "
          f"{len(bubbles_fr_all)} bubbles_done_fr  (threshold={BUBBLES_DONE_THRESHOLD})")
    print()

    for region_slug, tickers in REGIONS.items():
        ticker_set = set(tickers)

        # ── scrape (no lang) ──────────────────────────────────────────────────
        scraped = sorted(ticker_set & scraped_all)
        path = SCRIPTS_DIR / f"batch_progress_scrape_{region_slug}.json"
        print(f"scrape/{region_slug}: {len(scraped)}/{len(tickers)} scraped")
        write_progress(path, {
            "scraped":       scraped,
            "failed_scrape": [],
        }, dry)

        # ── enrich (per lang) ─────────────────────────────────────────────────
        for lang, enrich_all in [("en", enrich_en_all), ("fr", enrich_fr_all)]:
            enriched = sorted(ticker_set & enrich_all)
            path = SCRIPTS_DIR / f"batch_progress_enrich_{region_slug}_{lang}.json"
            print(f"enrich/{region_slug}/{lang}: {len(enriched)}/{len(tickers)} enriched")
            write_progress(path, {
                "enriched":       enriched,
                "failed_enrich":  [],
            }, dry)

        # ── bubbles (per lang) ────────────────────────────────────────────────
        for lang, bub_all in [("en", bubbles_en_all), ("fr", bubbles_fr_all)]:
            done = sorted(ticker_set & bub_all)
            path = SCRIPTS_DIR / f"batch_progress_bubbles_{region_slug}_{lang}.json"
            print(f"bubbles/{region_slug}/{lang}: {len(done)}/{len(tickers)} done")
            write_progress(path, {
                "bubbles_done": done,
            }, dry)

        print()

    if not dry:
        print("Done. All progress files up to date.")
    else:
        print("Dry-run complete — no files written.")


if __name__ == "__main__":
    main()
