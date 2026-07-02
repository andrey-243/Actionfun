"""
Migration articles Supabase → Turso (champs lourds, progressif par phase).

Phases :
  1 (cutoff 1M)  : strip summary, dumb_en, dumb_fr
  2 (cutoff 7M)  : strip url   (+ summary/dumb si pas encore fait)
  3 (cutoff 13M) : strip headline, source (+ tout le reste)

Ce qui reste dans Supabase après phase 3 : id, datetime, is_related (image)

Usage :
    python migrate_articles_cold.py --phase 1 [--dry-run] [--init-db] [--ticker AAPL]
    python migrate_articles_cold.py --phase 2 [--dry-run]
    python migrate_articles_cold.py --phase 3 [--dry-run]

Vars d'env requises : SUPABASE_URL · SUPABASE_SERVICE_KEY · TURSO_URL · TURSO_AUTH_TOKEN
"""

import argparse
import logging
import os
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv(dotenv_path=Path(__file__).resolve().parent.parent / ".env")

from supabase import create_client
import services.articles_store as store

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# Phase → (cutoff_months, champs à stripper de Supabase)
PHASE_CONFIG = {
    1: (1,  store.STRIP_PHASE1),   # summary, dumb_en, dumb_fr
    2: (7,  store.STRIP_PHASE2),   # + url
    3: (13, store.STRIP_PHASE3),   # + headline, source
}


def _cutoff_ym(months: int) -> str:
    today = date.today()
    y = today.year
    m = today.month - months
    while m <= 0:
        m += 12
        y -= 1
    return f"{y:04d}-{m:02d}"


def _strip_fields(articles: list[dict], fields: tuple) -> list[dict]:
    """Vide les champs indiqués dans chaque article (copie)."""
    out = []
    for a in articles:
        stripped = dict(a)
        for f in fields:
            if f in stripped:
                stripped[f] = ""
        out.append(stripped)
    return out


def _ensure_ids(articles: list[dict]) -> list[dict]:
    """Ajoute id = sha1(url)[:16] aux articles qui n'en ont pas encore."""
    out = []
    for a in articles:
        if not a.get("id") and a.get("url"):
            a = {**a, "id": store._article_id(a["url"])}
        out.append(a)
    return out


def migrate(phase: int, dry_run: bool, init_db: bool, ticker_filter: str | None) -> None:
    cutoff_months, strip_fields = PHASE_CONFIG[phase]
    supa = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])
    cutoff = _cutoff_ym(cutoff_months)
    logger.info(f"Phase {phase} | cutoff year_month < {cutoff} | strip: {strip_fields} | dry_run={dry_run}")

    if init_db and not dry_run:
        store.init_db()

    BATCH = 200
    offset = 0
    total_migrated = 0
    total_stripped = 0

    while True:
        q = (
            supa.table("articles")
            .select("ticker, year_month, articles")
            .lt("year_month", cutoff)
            .order("year_month")
            .range(offset, offset + BATCH - 1)
        )
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

            # Vérifier si ce row nécessite encore du travail pour cette phase
            # (au moins un article a encore un champ non vide à stripper)
            needs_work = any(a.get(f) for a in arts for f in strip_fields)
            if not needs_work:
                logger.debug(f"skip {ticker}/{ym} — déjà migré phase {phase}")
                continue

            # Assurer que tous les articles ont un id avant d'écrire dans Turso
            arts_with_id = _ensure_ids(arts)

            logger.info(f"migrate {ticker}/{ym} ({len(arts_with_id)} articles)")
            total_migrated += 1

            if not dry_run:
                # 1. Écrire vers Turso (tous les champs disponibles)
                store.write_heavy(ticker, ym, arts_with_id)

                # 2. Stripper les champs de cette phase dans Supabase
                stripped = _strip_fields(arts_with_id, strip_fields)
                supa.table("articles").update({"articles": stripped}).eq("ticker", ticker).eq("year_month", ym).execute()
                total_stripped += 1

        offset += BATCH
        if len(rows) < BATCH:
            break

    logger.info(
        f"Terminé phase {phase} : {total_migrated} rows traitées, {total_stripped} strippées"
        + (" [DRY RUN]" if dry_run else "")
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Migration articles Supabase → Turso")
    parser.add_argument("--phase", type=int, choices=[1, 2, 3], required=True,
                        help="1=summary/dumb (>1M) | 2=+url (>7M) | 3=+headline/source (>13M)")
    parser.add_argument("--dry-run",  action="store_true", help="Log sans écrire")
    parser.add_argument("--init-db",  action="store_true", help="CREATE TABLE IF NOT EXISTS Turso")
    parser.add_argument("--ticker",   type=str, default=None, help="Restreindre à un ticker")
    args = parser.parse_args()

    required = ["SUPABASE_URL", "SUPABASE_SERVICE_KEY", "TURSO_URL", "TURSO_AUTH_TOKEN"]
    for var in required:
        if not os.environ.get(var):
            logger.error(f"Variable manquante : {var}")
            sys.exit(1)

    migrate(phase=args.phase, dry_run=args.dry_run, init_db=args.init_db, ticker_filter=args.ticker)


if __name__ == "__main__":
    main()
