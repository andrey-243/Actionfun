"""
Migration articles Supabase → Turso (champs lourds > 6 mois).

Usage :
    python migrate_articles_cold.py [--dry-run] [--init-db] [--ticker AAPL] [--months 6]

    --init-db   : CREATE TABLE IF NOT EXISTS dans Turso avant migration
    --dry-run   : log uniquement, aucune écriture
    --ticker    : migrer un seul ticker (debug)
    --months    : seuil de rétention en Supabase (défaut : 6)

Logique :
  1. Récupère tous les (ticker, year_month) dans `articles` dont year_month
     est plus vieux que N mois.
  2. Pour chaque row, extrait summary/dumb_en/dumb_fr depuis le JSON `articles`.
  3. Écrit sur Turso via articles_store.write_heavy().
  4. Strip les champs lourds dans Supabase (summary/dumb_en/dumb_fr mis à "" dans le JSON).

Vars d'env requises : SUPABASE_URL · SUPABASE_SERVICE_KEY · TURSO_URL · TURSO_AUTH_TOKEN
"""

import argparse
import json
import logging
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path

# Ajoute le répertoire parent (backend/) au path pour les imports services.*
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv(dotenv_path=Path(__file__).resolve().parent.parent / ".env")

from supabase import create_client
import services.articles_store as store

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def _cutoff_ym(months: int) -> str:
    """Retourne le year_month limite (exclu) : tout ce qui est < ce ym sera migré."""
    today = date.today()
    y = today.year
    m = today.month - months
    while m <= 0:
        m += 12
        y -= 1
    return f"{y:04d}-{m:02d}"


def _strip_heavy(articles: list[dict]) -> list[dict]:
    """Vide summary/dumb_en/dumb_fr dans les dicts article (in-place copy)."""
    out = []
    for a in articles:
        stripped = dict(a)
        for f in store.HEAVY_FIELDS:
            if f in stripped:
                stripped[f] = ""
        out.append(stripped)
    return out


def migrate(dry_run: bool, init_db: bool, ticker_filter: str | None, months: int) -> None:
    supa = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])
    cutoff = _cutoff_ym(months)
    logger.info(f"Cutoff: year_month < {cutoff} | dry_run={dry_run}")

    if init_db and not dry_run:
        store.init_db()
        logger.info("[turso] init_db OK")

    # Pagination : 200 rows par batch (articles JSON peut être gros)
    BATCH = 200
    offset = 0
    total_migrated = 0
    total_stripped = 0

    while True:
        q = supa.table("articles") \
            .select("ticker, year_month, articles") \
            .lt("year_month", cutoff) \
            .order("year_month") \
            .range(offset, offset + BATCH - 1)
        if ticker_filter:
            q = q.eq("ticker", ticker_filter.upper())
        resp = q.execute()
        rows = resp.data or []
        if not rows:
            break

        for row in rows:
            ticker = row["ticker"]
            ym = row["year_month"]
            arts = row.get("articles") or []
            if not arts:
                continue

            has_heavy = any(a.get(f) for a in arts for f in store.HEAVY_FIELDS)
            if not has_heavy:
                logger.debug(f"skip {ticker}/{ym} — pas de champs lourds")
                continue

            logger.info(f"migrate {ticker}/{ym} ({len(arts)} articles)")

            if not dry_run:
                store.write_heavy(ticker, ym, arts)
                stripped = _strip_heavy(arts)
                supa.table("articles") \
                    .update({"articles": stripped}) \
                    .eq("ticker", ticker) \
                    .eq("year_month", ym) \
                    .execute()
                total_stripped += 1

            total_migrated += 1

        offset += BATCH
        if len(rows) < BATCH:
            break

    logger.info(
        f"Terminé : {total_migrated} rows traitées, {total_stripped} strippées en Supabase"
        + (" [DRY RUN]" if dry_run else "")
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Migre les articles froids vers Turso")
    parser.add_argument("--dry-run", action="store_true", help="Log sans écrire")
    parser.add_argument("--init-db", action="store_true", help="CREATE TABLE IF NOT EXISTS Turso")
    parser.add_argument("--ticker", type=str, default=None, help="Restreindre à un ticker")
    parser.add_argument("--months", type=int, default=6, help="Seuil de rétention (mois, défaut 6)")
    args = parser.parse_args()

    for var in ("SUPABASE_URL", "SUPABASE_SERVICE_KEY", "TURSO_URL", "TURSO_AUTH_TOKEN"):
        if not os.environ.get(var):
            logger.error(f"Variable d'env manquante : {var}")
            sys.exit(1)

    migrate(dry_run=args.dry_run, init_db=args.init_db, ticker_filter=args.ticker, months=args.months)


if __name__ == "__main__":
    main()
