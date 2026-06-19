"""
translate_bulk.py — Traduction bulk EN→FR via cascade promo (Gemini → raise).

Usage :
  python scripts/translate_bulk.py --region EU
  python scripts/translate_bulk.py --region US --dry-run
  python scripts/translate_bulk.py --region ASIA --only bub,y,m
  python scripts/translate_bulk.py --ticker AAPL
  python scripts/translate_bulk.py --since 2h   # uniquement les lignes récentes
  python scripts/translate_bulk.py --since 30m  # 30 minutes
  python scripts/translate_bulk.py --since 3d   # 3 jours
  python scripts/translate_bulk.py --batch 10   # taille batch (défaut: 20)
  python scripts/translate_bulk.py              # toutes régions

--region : US | EU | ASIA (défaut: toutes)
--ticker : ticker unique (override --region)
--since  : filtre sur cached_at/condensed_at — ex: 30m, 2h, 1d (défaut: tout)
--only   : bub,y,m,w (défaut: tous dans cet ordre)

Ordre de priorité : BUB → Y breakers → M breakers → W breakers
"""

import argparse
import logging
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from supabase import create_client
from data.tickers import get_by_region, ALL_TICKERS
from services.llm_service import llm_call
from services.translation_service import TRANSLATE_PROMPT

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("translate_bulk")

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_KEY"]

PAGE_SIZE = 1000

_SINCE_RE = re.compile(r"^(\d+)(m|h|d)$")

def _parse_since(value: str) -> str:
    m = _SINCE_RE.match(value.strip())
    if not m:
        raise argparse.ArgumentTypeError(f"Format --since invalide : '{value}' (ex: 30m, 2h, 3d)")
    n, unit = int(m.group(1)), m.group(2)
    delta = {"m": timedelta(minutes=n), "h": timedelta(hours=n), "d": timedelta(days=n)}[unit]
    cutoff = datetime.now(timezone.utc) - delta
    return cutoff.isoformat()


def _translate_one(text: str) -> str:
    resp = llm_call(
        [{"role": "user", "content": TRANSLATE_PROMPT.format(text=text)}],
        max_tokens=2000,
        temperature=0.1,
        timeout=120,
        caller="translate_bulk",
        cascade="promo",
    )
    return resp["choices"][0]["message"]["content"].strip()


def _translate_batch(texts: list[str], workers: int = 10) -> list[str | None]:
    """Traduit N textes en parallèle. Retourne None pour les échecs (pas de raise)."""
    results = [None] * len(texts)
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(_translate_one, t): i for i, t in enumerate(texts)}
        for fut in as_completed(futures):
            idx = futures[fut]
            try:
                results[idx] = fut.result()
            except Exception as e:
                log.warning(f"  ✗ traduction idx={idx} : {e}")
    return results


def _fetch_all(
    supa,
    table: str,
    select: str,
    en_col: str,
    fr_col: str,
    tickers: list[str] | None,
    since_col: str | None = None,
    since_iso: str | None = None,
) -> list[dict]:
    rows = []
    offset = 0
    while True:
        q = (
            supa.table(table)
            .select(select)
            .not_.is_(en_col, "null")
            .is_(fr_col, "null")
        )
        if tickers:
            q = q.in_("ticker", tickers)
        if since_col and since_iso:
            q = q.gte(since_col, since_iso)
        res = q.range(offset, offset + PAGE_SIZE - 1).execute()
        batch = res.data or []
        rows.extend(batch)
        if len(batch) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
    return rows


def process_bub(supa, batch_size: int, dry: bool, tickers: list[str] | None, since_iso: str | None, workers: int = 10) -> int:
    log.info("=== BUB (bub_cache) ===")
    rows = _fetch_all(supa, "bub_cache", "ticker,date,bub_en", "bub_en", "bub_fr", tickers, "cached_at", since_iso)
    log.info(f"  {len(rows)} lignes à traduire")
    if dry or not rows:
        return len(rows)

    done = 0
    errors = 0
    for i in range(0, len(rows), batch_size):
        chunk = rows[i : i + batch_size]
        translated = _translate_batch([r["bub_en"] for r in chunk], workers)
        batch_err = sum(1 for t in translated if t is None)
        for row, fr in zip(chunk, translated):
            if fr is not None:
                supa.table("bub_cache").update({"bub_fr": fr}).eq("ticker", row["ticker"]).eq("date", row["date"]).execute()
        done += len(chunk) - batch_err
        errors += batch_err
        tickers_str = " ".join(r["ticker"] for r in chunk)
        err_str = f" | ✗ {batch_err}" if batch_err else ""
        log.info(f"  BUB {done}/{len(rows)}{err_str} — {tickers_str}")
    if errors:
        log.warning(f"  BUB total erreurs : {errors}/{len(rows)}")
    return done


def process_yearly(supa, batch_size: int, dry: bool, tickers: list[str] | None, since_iso: str | None, workers: int = 10) -> int:
    log.info("=== Y breakers (breaks_yearly) ===")
    rows = _fetch_all(supa, "breaks_yearly", "ticker,year,by_en", "by_en", "by_fr", tickers, "condensed_at", since_iso)
    log.info(f"  {len(rows)} lignes à traduire")
    if dry or not rows:
        return len(rows)

    done = 0
    errors = 0
    for i in range(0, len(rows), batch_size):
        chunk = rows[i : i + batch_size]
        translated = _translate_batch([r["by_en"] for r in chunk], workers)
        batch_err = sum(1 for t in translated if t is None)
        for row, fr in zip(chunk, translated):
            if fr is not None:
                supa.table("breaks_yearly").update({"by_fr": fr}).eq("ticker", row["ticker"]).eq("year", row["year"]).execute()
        done += len(chunk) - batch_err
        errors += batch_err
        tickers_str = " ".join(r["ticker"] for r in chunk)
        err_str = f" | ✗ {batch_err}" if batch_err else ""
        log.info(f"  Y {done}/{len(rows)}{err_str} — {tickers_str}")
    if errors:
        log.warning(f"  Y total erreurs : {errors}/{len(rows)}")
    return done


def process_monthly(supa, batch_size: int, dry: bool, tickers: list[str] | None, since_iso: str | None, workers: int = 10) -> int:
    log.info("=== M breakers (breaks_monthly) ===")
    rows = _fetch_all(supa, "breaks_monthly", "ticker,year_month,bm_en", "bm_en", "bm_fr", tickers, "condensed_at", since_iso)
    log.info(f"  {len(rows)} lignes à traduire")
    if dry or not rows:
        return len(rows)

    done = 0
    errors = 0
    for i in range(0, len(rows), batch_size):
        chunk = rows[i : i + batch_size]
        translated = _translate_batch([r["bm_en"] for r in chunk], workers)
        batch_err = sum(1 for t in translated if t is None)
        for row, fr in zip(chunk, translated):
            if fr is not None:
                supa.table("breaks_monthly").update({"bm_fr": fr}).eq("ticker", row["ticker"]).eq("year_month", row["year_month"]).execute()
        done += len(chunk) - batch_err
        errors += batch_err
        tickers_str = " ".join(r["ticker"] for r in chunk)
        err_str = f" | ✗ {batch_err}" if batch_err else ""
        log.info(f"  M {done}/{len(rows)}{err_str} — {tickers_str}")
    if errors:
        log.warning(f"  M total erreurs : {errors}/{len(rows)}")
    return done


def process_weekly(supa, batch_size: int, dry: bool, tickers: list[str] | None, since_iso: str | None, workers: int = 10) -> int:
    log.info("=== W breakers (breaks_weekly) ===")
    rows = _fetch_all(supa, "breaks_weekly", "ticker,year_month,week_num,bw_en", "bw_en", "bw_fr", tickers, "condensed_at", since_iso)
    log.info(f"  {len(rows)} lignes à traduire")
    if dry or not rows:
        return len(rows)

    done = 0
    errors = 0
    for i in range(0, len(rows), batch_size):
        chunk = rows[i : i + batch_size]
        translated = _translate_batch([r["bw_en"] for r in chunk], workers)
        batch_err = sum(1 for t in translated if t is None)
        for row, fr in zip(chunk, translated):
            if fr is not None:
                (
                    supa.table("breaks_weekly")
                    .update({"bw_fr": fr})
                    .eq("ticker", row["ticker"])
                    .eq("year_month", row["year_month"])
                    .eq("week_num", row["week_num"])
                    .execute()
                )
        done += len(chunk) - batch_err
        errors += batch_err
        tickers_str = " ".join(r["ticker"] for r in chunk)
        err_str = f" | ✗ {batch_err}" if batch_err else ""
        log.info(f"  W {done}/{len(rows)}{err_str} — {tickers_str}")
    if errors:
        log.warning(f"  W total erreurs : {errors}/{len(rows)}")
    return done


STEPS = [
    ("bub", process_bub),
    ("y",   process_yearly),
    ("m",   process_monthly),
    ("w",   process_weekly),
]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--region", choices=["US", "EU", "ASIA"], help="Filtre par région")
    parser.add_argument("--ticker", help="Ticker unique (override --region)")
    parser.add_argument("--since", metavar="DURATION", help="Ex: 30m, 2h, 3d — ne traduit que les lignes mises à jour depuis")
    parser.add_argument("--dry-run", action="store_true", help="Compte sans écrire")
    parser.add_argument("--only", default="", help="Tables : bub,y,m,w (défaut: tous)")
    parser.add_argument("--batch", type=int, default=20, help="Taille batch (défaut: 20)")
    parser.add_argument("--workers", type=int, default=10, help="Traductions en parallèle (défaut: 10)")
    args = parser.parse_args()

    since_iso = _parse_since(args.since) if args.since else None
    if since_iso:
        log.info(f"Filtre --since {args.since} : lignes >= {since_iso}")

    if args.ticker:
        tickers = [args.ticker.upper()]
        log.info(f"Mode ticker unique : {tickers[0]}")
    elif args.region:
        tickers = get_by_region(args.region)
        log.info(f"Région {args.region} : {len(tickers)} tickers")
    else:
        tickers = None
        log.info(f"Toutes régions : {len(ALL_TICKERS)} tickers")

    only = {s.strip() for s in args.only.split(",") if s.strip()} if args.only else None

    supa = create_client(SUPABASE_URL, SUPABASE_KEY)

    total = 0
    for key, fn in STEPS:
        if only and key not in only:
            continue
        count = fn(supa, args.batch, args.dry_run, tickers, since_iso, args.workers)
        total += count

    mode = "DRY-RUN" if args.dry_run else "DONE"
    log.info(f"=== {mode} | total traité: {total} ===")


if __name__ == "__main__":
    main()
