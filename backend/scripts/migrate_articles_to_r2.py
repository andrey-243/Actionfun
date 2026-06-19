"""
Migration one-shot : articles > 6 mois → champs lourds vers R2, strip dans Supabase.

⚠ NE PAS LANCER tant que les batch scrap/llm ne sont pas terminés.
   Ce script rend les champs summary/dumb_en/dumb_fr indisponibles dans Supabase
   pour les year_month migrés — bubble_service doit être mis à jour en parallèle.

Usage :
    python scripts/migrate_articles_to_r2.py --dry-run        # compte sans écrire
    python scripts/migrate_articles_to_r2.py --region US      # région seule
    python scripts/migrate_articles_to_r2.py                  # toutes régions
    python scripts/migrate_articles_to_r2.py --purge-old      # purge > 13 mois (R2 + Supa)
"""

import argparse
import gzip
import json
import logging
import os
import sys
from datetime import date, timezone
from dateutil.relativedelta import relativedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from supabase import create_client
from services.r2_articles import write_heavy, delete_heavy, HEAVY_FIELDS

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger(__name__)

MIGRATE_MONTHS = 6
PURGE_MONTHS   = 13
PAGE_SIZE      = 100


def _supa():
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])


def _cutoff_ym(months: int) -> str:
    d = date.today() - relativedelta(months=months)
    return f"{d.year:04d}-{d.month:02d}"


def _strip_heavy(articles: list[dict]) -> list[dict]:
    """Retire summary/dumb_en/dumb_fr de chaque article."""
    stripped = []
    for a in articles:
        a2 = {k: v for k, v in a.items() if k not in HEAVY_FIELDS}
        stripped.append(a2)
    return stripped


def _has_heavy_content(articles: list[dict]) -> bool:
    return any(a.get(f) for a in articles for f in HEAVY_FIELDS)


def run_migrate(dry_run: bool, region: str | None):
    supa = _supa()
    migrate_cutoff = _cutoff_ym(MIGRATE_MONTHS)
    purge_cutoff   = _cutoff_ym(PURGE_MONTHS)

    logger.info(f"Migration articles > {MIGRATE_MONTHS}M (< {migrate_cutoff}) vers R2")
    logger.info(f"Dry-run: {dry_run}")

    migrated, skipped, errors = 0, 0, 0
    offset = 0

    while True:
        q = supa.table("articles") \
            .select("ticker, year_month, articles") \
            .lt("year_month", migrate_cutoff) \
            .gte("year_month", purge_cutoff) \
            .order("year_month") \
            .range(offset, offset + PAGE_SIZE - 1)

        rows = q.execute().data or []
        if not rows:
            break

        for row in rows:
            ticker    = row["ticker"]
            year_month = row["year_month"]
            articles  = row.get("articles") or []

            if not articles or not _has_heavy_content(articles):
                skipped += 1
                continue

            if dry_run:
                logger.info(f"  [dry] {ticker} {year_month} — {len(articles)} articles à migrer")
                migrated += 1
                continue

            try:
                write_heavy(ticker, year_month, articles)
                stripped = _strip_heavy(articles)
                supa.table("articles").update({"articles": stripped}) \
                    .eq("ticker", ticker) \
                    .eq("year_month", year_month) \
                    .execute()
                migrated += 1
                logger.info(f"  ✓ {ticker} {year_month}")
            except Exception as e:
                errors += 1
                logger.error(f"  ✗ {ticker} {year_month}: {e}")

        offset += PAGE_SIZE
        if len(rows) < PAGE_SIZE:
            break

    logger.info(f"Migration terminée — migrés: {migrated} · skippés: {skipped} · erreurs: {errors}")


def run_purge(dry_run: bool):
    """Purge totale des rows > 13 mois : DELETE Supabase + DELETE R2."""
    supa = _supa()
    purge_cutoff = _cutoff_ym(PURGE_MONTHS)

    logger.info(f"Purge articles > {PURGE_MONTHS}M (< {purge_cutoff})")

    rows = supa.table("articles") \
        .select("ticker, year_month") \
        .lt("year_month", purge_cutoff) \
        .execute().data or []

    purged, errors = 0, 0
    for row in rows:
        ticker, year_month = row["ticker"], row["year_month"]
        if dry_run:
            logger.info(f"  [dry] purge {ticker} {year_month}")
            purged += 1
            continue
        try:
            delete_heavy(ticker, year_month)
            supa.table("articles") \
                .delete() \
                .eq("ticker", ticker) \
                .eq("year_month", year_month) \
                .execute()
            purged += 1
            logger.info(f"  ✓ purged {ticker} {year_month}")
        except Exception as e:
            errors += 1
            logger.error(f"  ✗ {ticker} {year_month}: {e}")

    logger.info(f"Purge terminée — purgés: {purged} · erreurs: {errors}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run",    action="store_true")
    parser.add_argument("--region",     choices=["US", "EU", "ASIA"])
    parser.add_argument("--purge-old",  action="store_true", help="Purge rows > 13 mois")
    args = parser.parse_args()

    if args.purge_old:
        run_purge(dry_run=args.dry_run)
    else:
        run_migrate(dry_run=args.dry_run, region=args.region)
