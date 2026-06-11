"""
One-shot : reconstruit le checkpoint refresh_breakers depuis la DB.

Checks (fenêtres configurables) :
  1. Articles présents ce mois-ci                           → sinon : RESCRAP
  2. Chaque semaine du mois courant avec articles a un breaks_weekly → sinon : W_MISS
  3. Articles de la semaine courante avec dumb_en           → sinon : DUMB_MISS
  4. breaks_monthly présent pour le mois courant            → sinon : MONTHLY_MISS

Actions checkpoint selon catégorie :
  RESCRAP        → retirer de done + monthly_done + legacy
  W_MISS         → retirer de done
  DUMB_MISS      → ajouter à legacy (keep in done)
  W_MISS+DUMB    → retirer de done + ajouter à legacy
  MONTHLY_MISS   → retirer de monthly_done (keep in done)

Usage :
  python scripts/fix_checkpoint_from_db.py --region US --lang en [--dry-run]
  python scripts/fix_checkpoint_from_db.py --region US --lang en --month 2026-05 --week-start 2026-05-05
"""
import argparse
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv
load_dotenv()

from supabase import create_client


def _current_ym() -> str:
    today = date.today()
    return f"{today.year:04d}-{today.month:02d}"


def _this_monday() -> date:
    today = date.today()
    return today - timedelta(days=today.weekday())


def _month_bounds(ym: str) -> tuple[int, int]:
    year, month = int(ym[:4]), int(ym[5:7])
    start = int(datetime(year, month, 1, tzinfo=timezone.utc).timestamp())
    if month == 12:
        end = int(datetime(year + 1, 1, 1, tzinfo=timezone.utc).timestamp())
    else:
        end = int(datetime(year, month + 1, 1, tzinfo=timezone.utc).timestamp())
    return start, end


def _week_num(ts: int) -> int:
    day = datetime.fromtimestamp(ts, tz=timezone.utc).day
    return min((day - 1) // 7 + 1, 5)


def _load_checkpoint(cp_path: Path) -> dict:
    with open(cp_path) as f:
        return json.load(f)


def _save_checkpoint(cp_path: Path, cp: dict):
    with open(cp_path, "w") as f:
        json.dump(cp, f, indent=2)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--region", required=True, choices=["US", "EU", "ASIA"])
    parser.add_argument("--lang", required=True, choices=["en", "fr"])
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--month", default=None, help="Mois à checker ex: 2026-06 (défaut: mois courant)")
    parser.add_argument("--week-start", default=None, help="Début semaine DUMB ex: 2026-06-09 (défaut: lundi courant)")
    args = parser.parse_args()

    ym = args.month or _current_ym()
    if args.week_start:
        monday = date.fromisoformat(args.week_start)
    else:
        monday = _this_monday()

    cp_path = Path(__file__).parent / f"refresh_breakers_{args.region.lower()}_{args.lang}.json"
    if not cp_path.exists():
        print(f"Checkpoint introuvable : {cp_path}")
        sys.exit(1)

    cp = _load_checkpoint(cp_path)
    done = set(cp.get("done", []))
    legacy = set(cp.get("legacy", []))
    monthly_done = set(cp.get("monthly_done", []))

    print(f"Checkpoint : {len(done)} done · {len(legacy)} legacy · {len(monthly_done)} monthly_done")
    print(f"Mois : {ym} | Semaine DUMB depuis : {monday}")

    supa = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])

    month_start, month_end = _month_bounds(ym)
    week_cutoff = int(datetime(monday.year, monday.month, monday.day, tzinfo=timezone.utc).timestamp())

    cat_rescrap = []
    cat_weekly = []
    cat_dumb = []
    cat_both = []
    cat_monthly = []
    keep = []

    for ticker in sorted(done):
        # Charger articles du mois
        res = supa.table("articles") \
            .select("articles") \
            .eq("ticker", ticker) \
            .eq("year_month", ym) \
            .execute()

        article_list = []
        for row in (res.data or []):
            article_list.extend(row.get("articles") or [])

        # Filtrer sur le mois courant (timestamps réels)
        articles_this_month = [
            a for a in article_list
            if month_start <= (a.get("datetime") or 0) < month_end
        ]

        # Check 1 : articles présents ce mois
        if not articles_this_month:
            cat_rescrap.append(ticker)
            print(f"  RESCRAP   : {ticker} (aucun article ce mois)")
            continue

        w_missing = []
        dumb_missing = 0

        # Check 2 : weeklies couverts (par semaine avec articles)
        weeks_with_articles = set(_week_num(a["datetime"]) for a in articles_this_month if a.get("datetime"))
        for wnum in sorted(weeks_with_articles):
            w = supa.table("breaks_weekly") \
                .select("ticker") \
                .eq("ticker", ticker) \
                .eq("year_month", ym) \
                .eq("week_num", wnum) \
                .limit(1) \
                .execute()
            if not w.data:
                w_missing.append(f"W{wnum}")

        # Check 3 : DUMBs sur la semaine courante
        week_articles = [a for a in articles_this_month if (a.get("datetime") or 0) >= week_cutoff]
        dumb_missing = sum(1 for a in week_articles if not a.get("dumb_en"))

        # Check 4 : monthly présent
        m = supa.table("breaks_monthly") \
            .select("year_month") \
            .eq("ticker", ticker) \
            .eq("year_month", ym) \
            .limit(1) \
            .execute()
        monthly_missing = not m.data

        has_w = bool(w_missing)
        has_d = bool(dumb_missing)
        has_m = monthly_missing

        if has_w and has_d:
            cat_both.append(ticker)
            print(f"  W+DUMB    : {ticker} ({' '.join(w_missing)} | {dumb_missing} sans dumb_en)")
        elif has_w:
            cat_weekly.append(ticker)
            print(f"  W MISS    : {ticker} ({' '.join(w_missing)})")
        elif has_d:
            cat_dumb.append(ticker)
            print(f"  DUMB MISS : {ticker} ({dumb_missing} sans dumb_en)")
        elif has_m:
            cat_monthly.append(ticker)
            print(f"  M MISS    : {ticker} (pas de breaks_monthly {ym})")
        else:
            keep.append(ticker)

    total_remove = len(cat_rescrap) + len(cat_weekly) + len(cat_both)
    total_legacy = len(cat_dumb) + len(cat_both)

    print(f"\n── Résultat : {len(keep)} gardés · {total_remove} retirés de done · {total_legacy} → legacy · {len(cat_monthly)} M MISS ──")
    if cat_rescrap:
        print(f"\n[RESCRAP — aucun article ce mois] ({len(cat_rescrap)})")
        print("  " + " · ".join(cat_rescrap))
    if cat_weekly:
        print(f"\n[W MISS only — retirer de done] ({len(cat_weekly)})")
        print("  " + " · ".join(cat_weekly))
    if cat_dumb:
        print(f"\n[DUMB MISS only — → legacy] ({len(cat_dumb)})")
        print("  " + " · ".join(cat_dumb))
    if cat_both:
        print(f"\n[W MISS + DUMB MISS — retirer de done + → legacy] ({len(cat_both)})")
        print("  " + " · ".join(cat_both))
    if cat_monthly:
        print(f"\n[MONTHLY MISS — retirer de monthly_done] ({len(cat_monthly)})")
        print("  " + " · ".join(cat_monthly))

    if args.dry_run:
        print("\n(dry-run — aucune modification)")
        return

    # Appliquer les changements
    for t in cat_rescrap:
        done.discard(t)
        monthly_done.discard(t)
        legacy.discard(t)

    for t in cat_weekly:
        done.discard(t)

    for t in cat_dumb:
        legacy.add(t)  # reste dans done

    for t in cat_both:
        done.discard(t)
        legacy.add(t)

    for t in cat_monthly:
        monthly_done.discard(t)

    cp["done"] = sorted(done)
    cp["legacy"] = sorted(legacy)
    cp["monthly_done"] = sorted(monthly_done)
    _save_checkpoint(cp_path, cp)
    print(f"\nCheckpoint mis à jour : {cp_path}")
    print(f"done: {len(done)} · legacy: {len(legacy)} · monthly_done: {len(monthly_done)}")


if __name__ == "__main__":
    main()
