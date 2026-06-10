"""
Re-fetch Finnhub pour tous les rows source='finnhub' dans la fenêtre 12 mois.
Stocke le raw sans dedup pour préserver les infos multi-source.

Usage:
    python scripts/refetch_finnhub.py
    python scripts/refetch_finnhub.py --dry-run   # liste les rows sans fetch
    python scripts/refetch_finnhub.py --delay 2   # secondes entre chaque call (défaut: 1.5)
"""
import sys, os, time, argparse
from datetime import date, datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv()

import httpx
from services.breakers_articles_service import _fetch_finnhub_month, _dedup_by_headline, _get_supabase


def _tg(msg: str):
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        return
    try:
        httpx.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": msg, "parse_mode": "Markdown"},
            timeout=8,
        )
    except Exception:
        pass

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--delay", type=float, default=1.5)
    parser.add_argument("--months", type=int, default=12, help="Fenêtre en mois (défaut: 12)")
    args = parser.parse_args()

    supa = _get_supabase()
    today = date.today()

    # Fenêtre valide Finnhub : 12 mois en arrière
    cutoff_ym = f"{today.year - 1 if today.month > 1 else today.year - 2:04d}-{today.month - 1 if today.month > 1 else 12:02d}"
    # Plus simple : string comparison suffit (format YYYY-MM)
    months_ago = today.replace(day=1) - timedelta(days=args.months * 30)
    cutoff_ym = f"{months_ago.year:04d}-{months_ago.month:02d}"

    # Charger tous les rows finnhub dans la fenêtre
    rows = supa.table("articles") \
        .select("ticker, year_month, source") \
        .eq("source", "finnhub") \
        .gte("year_month", cutoff_ym) \
        .order("year_month", desc=True) \
        .execute()

    targets = rows.data or []
    total = len(targets)
    eta_min = int(total * args.delay / 60)
    print(f"Rows à refetcher : {total} (finnhub, >= {cutoff_ym})")
    print(f"Delay entre calls : {args.delay}s — ETA ~{eta_min} min\n")

    if args.dry_run:
        for r in targets:
            print(f"  {r['ticker']} {r['year_month']}")
        return

    _tg(f"🔄 *Refetch Finnhub démarré*\n{total} rows · ETA ~{eta_min} min")

    ok = 0
    errors = 0
    errors_list: list[str] = []
    start = time.time()
    CHECKPOINT = 50

    for i, row in enumerate(targets, 1):
        ticker = row["ticker"]
        ym = row["year_month"]
        print(f"[{i}/{total}] {ticker} {ym} ...", end=" ", flush=True)

        try:
            raw = _fetch_finnhub_month(ticker, ym)
            articles = _dedup_by_headline(raw)  # merge extra_sources, pas de doublons en DB
            supa.table("articles").upsert({
                "ticker":     ticker,
                "year_month": ym,
                "articles":   articles,
                "source":     "finnhub",
                "cached_at":  datetime.now(timezone.utc).isoformat(),
            }).execute()
            print(f"✓ {len(raw)}→{len(articles)} articles")
            ok += 1
        except Exception as e:
            print(f"✗ {e}")
            errors += 1
            errors_list.append(f"{ticker}/{ym}")

        # Checkpoint TG tous les 50
        if i % CHECKPOINT == 0:
            elapsed = int(time.time() - start)
            remaining = int((elapsed / i) * (total - i))
            m, s = divmod(elapsed, 60)
            rm, rs = divmod(remaining, 60)
            _tg(
                f"📊 *Refetch Finnhub* [{i}/{total}]\n"
                f"✅ {ok} OK · ❌ {errors} erreurs\n"
                f"⏱ {m}m{s:02d}s écoulées · ~{rm}m{rs:02d}s restantes"
            )

        if i < total:
            time.sleep(args.delay)

    elapsed = int(time.time() - start)
    m, s = divmod(elapsed, 60)
    err_detail = "\n".join(errors_list[:10]) + ("…" if len(errors_list) > 10 else "")
    _tg(
        f"✅ *Refetch Finnhub terminé*\n"
        f"{ok} OK · {errors} erreurs · {m}m{s:02d}s\n"
        f"{err_detail if err_detail else ''}"
    )
    print(f"\n✅ Terminé — {ok} OK · {errors} erreurs · {m}m{s:02d}s")

if __name__ == "__main__":
    main()
