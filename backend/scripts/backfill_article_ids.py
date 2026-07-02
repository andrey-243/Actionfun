"""
Backfill article ids dans le JSONB Supabase.

Ajoute id = sha1(url)[:16] à chaque article qui n'en a pas encore.
À lancer UNE FOIS avant d'activer la migration vers Turso.

Usage :
    python scripts/backfill_article_ids.py [--dry-run] [--ticker AAPL]
"""

import argparse
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv(dotenv_path=Path(__file__).resolve().parent.parent / ".env")

from supabase import create_client
from services.articles_store import _article_id

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

BATCH = 200


def backfill(dry_run: bool, ticker_filter: str | None) -> None:
    supa = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])

    # Étape 1 : récupérer tous les (ticker, year_month) sans le JSONB (rapide)
    logger.info("Chargement des (ticker, year_month)...")
    pairs: list[tuple[str, str]] = []
    offset = 0
    while True:
        q = supa.table("articles").select("ticker, year_month").order("ticker").order("year_month").range(offset, offset + BATCH - 1)
        if ticker_filter:
            q = q.eq("ticker", ticker_filter.upper())
        resp = q.execute()
        rows = resp.data or []
        pairs.extend((r["ticker"], r["year_month"]) for r in rows)
        offset += BATCH
        if len(rows) < BATCH:
            break
    logger.info(f"{len(pairs)} rows à traiter")

    # Étape 2 : traiter chaque row individuellement (fetch JSONB + patch)
    total_updated = 0
    total_articles_patched = 0

    for i, (ticker, ym) in enumerate(pairs):
        row = supa.table("articles").select("articles").eq("ticker", ticker).eq("year_month", ym).maybe_single().execute()
        arts = (row.data or {}).get("articles") or []
        if not arts:
            continue

        missing = sum(1 for a in arts if not a.get("id"))
        if missing == 0:
            continue

        patched = []
        for a in arts:
            if a.get("id"):
                patched.append(a)
            else:
                url = a.get("url", "")
                patched.append({**a, "id": _article_id(url)} if url else a)

        logger.info(f"[{i+1}/{len(pairs)}] {ticker}/{ym} : {missing}/{len(arts)} articles patchés")

        if not dry_run:
            supa.table("articles").update({"articles": patched}).eq("ticker", ticker).eq("year_month", ym).execute()

        total_updated += 1
        total_articles_patched += missing

    suffix = " [DRY RUN]" if dry_run else ""
    logger.info(f"Terminé{suffix} : {len(pairs)} rows lues, {total_updated} mises à jour, {total_articles_patched} articles patchés")


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill article ids dans Supabase")
    parser.add_argument("--dry-run", action="store_true", help="Log sans écrire")
    parser.add_argument("--ticker", type=str, default=None, help="Restreindre à un ticker")
    args = parser.parse_args()

    for var in ("SUPABASE_URL", "SUPABASE_SERVICE_KEY"):
        if not os.environ.get(var):
            logger.error(f"Variable manquante : {var}")
            sys.exit(1)

    backfill(dry_run=args.dry_run, ticker_filter=args.ticker)


if __name__ == "__main__":
    main()
