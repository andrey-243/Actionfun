"""
fix_missing.py — Répare les données manquantes sans passer par le full batch.

Analyse les tickers dans 'done' et exécute uniquement les étapes manquantes :
  RESCRAP   → retire de done + message : lancer refresh_breakers.py
  W MISS    → scrap + gen_dumb (35j depuis now) + gen_weekly (semaines manquantes only)
  DUMB MISS → scrap + gen_dumb (35j depuis now)

Usage :
  python scripts/fix_missing.py --region US --lang en --dry-run
  python scripts/fix_missing.py --region EU --lang en
  python scripts/fix_missing.py --region US --lang en --month 2026-06 --week-start 2026-06-09
"""

import argparse
import json
import logging
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from dotenv import load_dotenv
load_dotenv()

os.environ["BATCH_MODE"] = "1"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger(__name__)

from supabase import create_client
import refresh_breakers as rb


def _supa():
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])


def _current_ym() -> str:
    today = date.today()
    return f"{today.year:04d}-{today.month:02d}"


def _this_monday() -> date:
    today = date.today()
    return today - timedelta(days=today.weekday())


def _month_bounds(ym: str) -> tuple[int, int]:
    year, month = int(ym[:4]), int(ym[5:7])
    start = int(datetime(year, month, 1, tzinfo=timezone.utc).timestamp())
    end_m = month + 1 if month < 12 else 1
    end_y = year if month < 12 else year + 1
    return start, int(datetime(end_y, end_m, 1, tzinfo=timezone.utc).timestamp())


def _analyze(args, supa) -> dict:
    """
    Retourne les catégories détectées.
    cat_rescrap  : list[str]
    cat_weekly   : dict[str, list[int]]  ticker → semaines manquantes
    cat_dumb     : list[str]
    cat_both     : dict[str, list[int]]  ticker → semaines manquantes
    keep         : list[str]
    """
    ym = args.month or _current_ym()
    monday = date.fromisoformat(args.week_start) if args.week_start else _this_monday()
    week_cutoff = int(datetime(monday.year, monday.month, monday.day, tzinfo=timezone.utc).timestamp())
    month_start, month_end = _month_bounds(ym)

    cp_path = Path(__file__).parent / f"refresh_breakers_{args.region.lower()}_{args.lang}.json"
    cp = json.loads(cp_path.read_text())
    done = set(cp.get("done", []))

    cat_rescrap, cat_weekly, cat_dumb, cat_both, cat_monthly, keep = [], {}, [], {}, [], []

    for ticker in sorted(done):
        res = supa.table("articles").select("articles") \
            .eq("ticker", ticker).eq("year_month", ym).execute()
        article_list = []
        for row in (res.data or []):
            article_list.extend(row.get("articles") or [])

        articles_this_month = [
            a for a in article_list
            if month_start <= (a.get("datetime") or 0) < month_end
        ]

        if not articles_this_month:
            cat_rescrap.append(ticker)
            continue

        # Semaines manquantes
        def _wnum(ts): return min((datetime.fromtimestamp(ts, tz=timezone.utc).day - 1) // 7 + 1, 5)

        weeks_with_articles = set(_wnum(a["datetime"]) for a in articles_this_month if a.get("datetime"))
        w_missing = []
        for wnum in sorted(weeks_with_articles):
            w = supa.table("breaks_weekly").select("ticker") \
                .eq("ticker", ticker).eq("year_month", ym).eq("week_num", wnum).limit(1).execute()
            if not w.data:
                w_missing.append(wnum)

        # DUMBs manquants cette semaine
        week_articles = [a for a in articles_this_month if (a.get("datetime") or 0) >= week_cutoff]
        dumb_missing = any(not a.get("dumb_en") for a in week_articles)

        # Monthly présent ?
        m = supa.table("breaks_monthly").select("year_month") \
            .eq("ticker", ticker).eq("year_month", ym).limit(1).execute()
        monthly_missing = not m.data

        if w_missing and dumb_missing:
            cat_both[ticker] = w_missing
        elif w_missing:
            cat_weekly[ticker] = w_missing
        elif dumb_missing:
            cat_dumb.append(ticker)
        elif monthly_missing:
            cat_monthly.append(ticker)
        else:
            keep.append(ticker)

    return {
        "ym": ym, "monday": monday,
        "cat_rescrap": cat_rescrap,
        "cat_weekly": cat_weekly,
        "cat_dumb": cat_dumb,
        "cat_both": cat_both,
        "cat_monthly": cat_monthly,
        "keep": keep,
    }


def _fix_dumb(ticker: str, lang: str, supa):
    """Scrap + gen_dumb (35j)."""
    ym = datetime.now(timezone.utc).strftime("%Y-%m")
    added = rb._scrape_and_upsert(ticker, ym, supa)
    logger.info(f"  {ticker} scrap: +{added} articles")
    from services.overview_service import gen_dumb
    n = gen_dumb(ticker, lang, cascade="dumb_scrap", cutoff_days=35)
    logger.info(f"  {ticker} gen_dumb: {n} articles")
    return n


def _fix_weekly(ticker: str, lang: str, missing_weeks: list[int], ym: str, supa):
    """Scrap + gen_dumb (35j si articles sans dumb) + gen_weekly (semaines manquantes only)."""
    added = rb._scrape_and_upsert(ticker, ym, supa)
    logger.info(f"  {ticker} scrap: +{added} articles")

    from services.overview_service import gen_dumb
    n_dumb = gen_dumb(ticker, lang, cascade="dumb_scrap", cutoff_days=35)
    if n_dumb:
        logger.info(f"  {ticker} gen_dumb: {n_dumb} articles")

    # gen_weekly uniquement sur les semaines manquantes
    sliding = [(ym, wnum) for wnum in missing_weeks]
    is_skewed, cluster_wnum = rb._detect_finn_skew(ticker, [ym], supa)
    w_done, _, w_fail = rb._process_weeklies(ticker, is_skewed, cluster_wnum, sliding, supa, force_regen=False)
    logger.info(f"  {ticker} gen_weekly: {w_done} générés, {w_fail} échecs")
    return w_done, w_fail


def _fix_monthly(ticker: str, ym: str, supa) -> bool:
    """gen_monthly : weeklies si ≥3, sinon raw. Retourne True si succès."""
    week_rows = supa.table("breaks_weekly") \
        .select("week_num, ai_summary, ai_summary_en, article_count") \
        .eq("ticker", ticker).eq("year_month", ym).order("week_num").execute()
    if week_rows.data and len(week_rows.data) >= 3:
        src = rb._gen_monthly_from_weeklies(ticker, ym, week_rows.data, supa)
    else:
        src = rb._gen_monthly_from_raw(ticker, ym, supa)
    if isinstance(src, str):
        logger.info(f"  {ticker} gen_monthly: ok [{src}]")
        return True
    logger.warning(f"  {ticker} gen_monthly: skip (src={src})")
    return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--region", required=True, choices=["US", "EU", "ASIA"])
    parser.add_argument("--lang", required=True, choices=["en", "fr"])
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--month", default=None)
    parser.add_argument("--week-start", default=None, dest="week_start")
    args = parser.parse_args()

    supa = _supa()
    result = _analyze(args, supa)

    ym = result["ym"]
    cat_rescrap = result["cat_rescrap"]
    cat_weekly = result["cat_weekly"]
    cat_dumb = result["cat_dumb"]
    cat_both = result["cat_both"]
    cat_monthly = result["cat_monthly"]
    keep = result["keep"]

    total_remove = len(cat_rescrap)
    total_fix = len(cat_weekly) + len(cat_dumb) + len(cat_both) + len(cat_monthly)

    print(f"\nRégion : {args.region} | Mois : {ym} | Semaine depuis : {result['monday']}")
    print(f"── {len(keep)} ok · {total_remove} RESCRAP · {len(cat_weekly)} W MISS · {len(cat_dumb)} DUMB MISS · {len(cat_both)} W+DUMB · {len(cat_monthly)} M MISS ──")

    if cat_rescrap:
        print(f"\n[RESCRAP — {len(cat_rescrap)}] → retirer de done, lancer refresh_breakers.py manuellement")
        print("  " + " · ".join(cat_rescrap))
    if cat_weekly:
        print(f"\n[W MISS — {len(cat_weekly)}] → scrap + gen_dumb(35j) + gen_weekly")
        for t, weeks in cat_weekly.items():
            print(f"  {t} (W{'/'.join(str(w) for w in weeks)})")
    if cat_dumb:
        print(f"\n[DUMB MISS — {len(cat_dumb)}] → scrap + gen_dumb(35j)")
        print("  " + " · ".join(cat_dumb))
    if cat_both:
        print(f"\n[W+DUMB — {len(cat_both)}] → scrap + gen_dumb(35j) + gen_weekly")
        for t, weeks in cat_both.items():
            print(f"  {t} (W{'/'.join(str(w) for w in weeks)})")
    if cat_monthly:
        print(f"\n[M MISS — {len(cat_monthly)}] → gen_monthly (weeklies si ≥3, sinon raw)")
        print("  " + " · ".join(cat_monthly))

    if args.dry_run:
        print("\n(dry-run — aucune modification)")
        return

    # ── Appliquer ──────────────────────────────────────────────────────────────
    cp_path = Path(__file__).parent / f"refresh_breakers_{args.region.lower()}_{args.lang}.json"
    cp = json.loads(cp_path.read_text())
    done = set(cp.get("done", []))
    legacy = set(cp.get("legacy", []))
    monthly_done = set(cp.get("monthly_done", []))

    # RESCRAP : retirer de done
    for t in cat_rescrap:
        done.discard(t)
        legacy.discard(t)
        monthly_done.discard(t)
        logger.info(f"RESCRAP {t} → retiré de done")

    # W MISS
    for ticker, weeks in cat_weekly.items():
        logger.info(f"W MISS {ticker} W{weeks} → fix")
        try:
            _fix_weekly(ticker, args.lang, weeks, ym, supa)
        except Exception as e:
            logger.error(f"  ✗ {ticker}: {e}")
        time.sleep(0.5)

    # DUMB MISS
    for ticker in cat_dumb:
        logger.info(f"DUMB MISS {ticker} → fix")
        try:
            _fix_dumb(ticker, args.lang, supa)
            legacy.discard(ticker)  # dumb fixé, plus besoin de legacy
        except Exception as e:
            logger.error(f"  ✗ {ticker}: {e}")
        time.sleep(0.5)

    # W+DUMB
    for ticker, weeks in cat_both.items():
        logger.info(f"W+DUMB {ticker} W{weeks} → fix")
        try:
            _fix_weekly(ticker, args.lang, weeks, ym, supa)
        except Exception as e:
            logger.error(f"  ✗ {ticker}: {e}")
        time.sleep(0.5)

    # MONTHLY MISS
    for ticker in cat_monthly:
        logger.info(f"M MISS {ticker} → fix")
        try:
            ok = _fix_monthly(ticker, ym, supa)
            if ok:
                monthly_done.add(ticker)
        except Exception as e:
            logger.error(f"  ✗ {ticker}: {e}")
        time.sleep(0.5)

    # Sauvegarder checkpoint
    rb._save_checkpoint(cp_path, done, legacy, monthly_done)
    print(f"\nCheckpoint mis à jour : done={len(done)} legacy={len(legacy)} monthly_done={len(monthly_done)}")


if __name__ == "__main__":
    main()
