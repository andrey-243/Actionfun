"""
Condensation manuelle pour un ticker donné.
Usage:
    python scripts/condense_ticker.py AAPL          # weekly + monthly pour les mois passés
    python scripts/condense_ticker.py AAPL --weekly # weeklies seulement
"""
import sys
import os
import argparse
from datetime import date, datetime, timezone, timedelta
from calendar import monthrange

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from services.period_articles_service import (
    _get_supabase, _week_num, _week_label, _month_label,
    _normalize_finnhub, _llm_call,
    WEEK_CONDENSE_PROMPT, MONTH_CONDENSE_PROMPT,
    _purge_excess_weeklies,
)


def condense_weeks_for_month(ticker: str, year_month: str, supa) -> int:
    """Génère les weekly summaries manquants pour year_month. Retourne le nombre générés."""
    row = supa.table("article_archive") \
        .select("articles") \
        .eq("ticker", ticker) \
        .eq("year_month", year_month) \
        .maybe_single() \
        .execute()
    if not row.data or not row.data["articles"]:
        print(f"  ⚠  Pas d'articles en archive pour {ticker} {year_month}")
        return 0

    all_articles = row.data["articles"]
    generated = 0

    for wnum in range(1, 5):
        exists = supa.table("article_week_summaries") \
            .select("week_num") \
            .eq("ticker", ticker) \
            .eq("year_month", year_month) \
            .eq("week_num", wnum) \
            .maybe_single() \
            .execute()
        if exists and exists.data:
            print(f"  ↳ W{wnum} {year_month} déjà condensée — skip")
            continue

        week_articles = [
            a for a in all_articles
            if _week_num(datetime.fromtimestamp(a.get("datetime", 0), tz=timezone.utc).day) == wnum
        ]
        if not week_articles:
            print(f"  ↳ W{wnum} {year_month} : aucun article")
            continue

        label = _week_label(year_month, wnum)
        articles_text = "\n".join(f"- {a['headline']}" for a in week_articles[:30])
        print(f"  Condensation W{wnum} {year_month} ({len(week_articles)} articles)…", end=" ", flush=True)
        try:
            summary = _llm_call(WEEK_CONDENSE_PROMPT.format(
                ticker=ticker, label=label, articles_text=articles_text,
            ))
            top_articles = [_normalize_finnhub(a) for a in sorted(
                week_articles, key=lambda x: len(x.get("summary", "")), reverse=True
            )[:10]]
            supa.table("article_week_summaries").upsert({
                "ticker":        ticker,
                "year_month":    year_month,
                "week_num":      wnum,
                "ai_summary":    summary,
                "top_articles":  top_articles,
                "article_count": len(week_articles),
                "condensed_at":  datetime.now(timezone.utc).isoformat(),
            }).execute()
            print("✓")
            generated += 1
        except Exception as e:
            print(f"✗ {e}")

    _purge_excess_weeklies(ticker, supa, max_weeks=5)
    return generated


def condense_month(ticker: str, year_month: str, supa) -> bool:
    """Génère le monthly summary depuis les weeklies. Retourne True si généré."""
    exists = supa.table("article_month_summaries") \
        .select("year_month") \
        .eq("ticker", ticker) \
        .eq("year_month", year_month) \
        .maybe_single() \
        .execute()
    if exists and exists.data:
        print(f"  Monthly {year_month} déjà condensé — skip")
        return False

    week_rows = supa.table("article_week_summaries") \
        .select("week_num, ai_summary, article_count") \
        .eq("ticker", ticker) \
        .eq("year_month", year_month) \
        .order("week_num") \
        .execute()

    if not week_rows.data or len(week_rows.data) < 2:
        print(f"  Monthly {year_month} : pas assez de weeklies ({len(week_rows.data or [])})")
        return False

    weeks_text = "\n".join(f"W{r['week_num']}: {r['ai_summary']}" for r in week_rows.data)
    article_count = sum(r["article_count"] for r in week_rows.data)
    print(f"  Condensation monthly {year_month} ({article_count} articles, {len(week_rows.data)} semaines)…", end=" ", flush=True)
    try:
        summary = _llm_call(MONTH_CONDENSE_PROMPT.format(
            ticker=ticker, label=_month_label(year_month), weeks_text=weeks_text,
        ), max_tokens=500)
        supa.table("article_month_summaries").upsert({
            "ticker":        ticker,
            "year_month":    year_month,
            "ai_summary":    summary,
            "article_count": article_count,
            "condensed_at":  datetime.now(timezone.utc).isoformat(),
        }).execute()
        print("✓")
        return True
    except Exception as e:
        print(f"✗ {e}")
        return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("ticker")
    parser.add_argument("--weekly", action="store_true", help="Weeklies seulement (pas de monthly)")
    parser.add_argument("--months", type=int, default=3, help="Nombre de mois en arrière (défaut: 3)")
    args = parser.parse_args()

    ticker = args.ticker.upper()
    supa = _get_supabase()

    today = date.today()
    year_months = []
    for i in range(1, args.months + 1):
        m = today.month - i
        y = today.year
        while m <= 0:
            m += 12
            y -= 1
        year_months.append(f"{y:04d}-{m:02d}")

    print(f"\n🔧 Condensation manuelle : {ticker} ({args.months} mois)")
    print(f"   Mois ciblés : {', '.join(year_months)}\n")

    for ym in year_months:
        print(f"── {_month_label(ym)} ({ym}) ──")
        condense_weeks_for_month(ticker, ym, supa)
        if not args.weekly:
            condense_month(ticker, ym, supa)
        print()

    print("✅ Terminé.")


if __name__ == "__main__":
    main()
