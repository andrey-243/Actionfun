"""
refresh_breakers.py — Scrape articles + génère weekly/monthly breakers par ticker.

Pour chaque ticker (région) :
  1. Fetch Finnhub + Yahoo en // pour le mois courant → dedup → upsert articles_merged_finn_yahoo
  2. Détecte le skew Finnhub (cluster de dates en W4)
  3. Génère les weekly summaries manquants sur la fenêtre glissante des 3 derniers mois
     - Semaine skewed (cluster) → skip
     - < 2 articles dans la semaine → skip
  4. Génère les monthly summaries manquants sur les 6 derniers mois
     - Skew détecté  → gen depuis raw articles directement
     - ≥ 3 weeklies  → gen depuis weeklies
     - sinon         → gen depuis raw articles
  5. Checkpoint JSON après chaque ticker

Usage :
  python scripts/refresh_breakers.py --region USA --lang en
  python scripts/refresh_breakers.py --region EU  --lang fr
  python scripts/refresh_breakers.py --region ASIA --lang en
  python scripts/refresh_breakers.py --region USA --lang en --ticker AAPL
  python scripts/refresh_breakers.py --region USA --lang en --resume  # ignore checkpoint
"""

import argparse
import json
import logging
import os
import re
import sys
import time
import threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from calendar import monthrange
from datetime import date, datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parents[1] / ".env")

os.environ["BATCH_MODE"] = "1"

import httpx
import yfinance as yf
from supabase import create_client

SCRIPTS_DIR = Path(__file__).parent
LOG_FILE = SCRIPTS_DIR / "refresh_breakers.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)

NOTIFY_EVERY = 30
FINNHUB_KEY = os.environ["FINNHUB_API_KEY"]
MIN_ARTICLES_FOR_WEEKLY = 2   # seuil min pour générer un weekly summary
MIN_ARTICLES_FOR_MONTHLY = 3  # seuil min pour générer un monthly summary
SKEW_RATIO = 2.5              # cluster si max_week > SKEW_RATIO * median des autres semaines


# ─── Telegram ─────────────────────────────────────────────────────────────────

def _telegram(msg: str):
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        return
    html = re.sub(r'\*([^*]+)\*', r'<b>\1</b>', msg)
    html = re.sub(r'(?<!\w)_([^_]+)_(?!\w)', r'<i>\1</i>', html)
    html += "\n📍 <i>refresh_breakers</i>"
    try:
        httpx.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": html, "parse_mode": "HTML"},
            timeout=8,
        )
    except Exception:
        pass


# ─── Supabase ─────────────────────────────────────────────────────────────────

def _supa():
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])


# ─── Checkpoint ───────────────────────────────────────────────────────────────

def _checkpoint_path(region: str, lang: str) -> Path:
    return SCRIPTS_DIR / f"refresh_breakers_{region.lower()}_{lang}.json"


def _load_checkpoint(path: Path) -> set[str]:
    if path.exists():
        try:
            return set(json.loads(path.read_text()).get("done", []))
        except Exception:
            pass
    return set()


def _save_checkpoint(path: Path, done: set[str]):
    path.write_text(json.dumps({"done": sorted(done)}, indent=2))


# ─── Helpers date ─────────────────────────────────────────────────────────────

def _week_num(day: int) -> int:
    if day <= 7:   return 1
    if day <= 14:  return 2
    if day <= 21:  return 3
    return 4


def _sliding_weeks(n: int = 5) -> list[tuple[str, int]]:
    """Retourne les n dernières semaines (year_month, week_num) en ordre décroissant."""
    now = datetime.now(timezone.utc)
    current_ym = now.strftime("%Y-%m")
    current_wnum = _week_num(now.day)
    weeks = []
    ym = current_ym
    wnum = current_wnum
    for _ in range(n):
        weeks.append((ym, wnum))
        wnum -= 1
        if wnum == 0:
            y, m = int(ym[:4]), int(ym[5:7])
            m -= 1
            if m == 0:
                m, y = 12, y - 1
            ym = f"{y:04d}-{m:02d}"
            wnum = 4
    return weeks


def _last_n_months(n: int = 6) -> list[str]:
    """Retourne les n derniers year_month en ordre décroissant."""
    now = datetime.now(timezone.utc)
    months = []
    y, m = now.year, now.month
    for _ in range(n):
        months.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            m, y = 12, y - 1
    return months


# ─── Scrape step ──────────────────────────────────────────────────────────────

def _fetch_finnhub_month(ticker: str, year_month: str) -> list[dict]:
    from calendar import monthrange as _mr
    year, month = int(year_month[:4]), int(year_month[5:7])
    from_date = date(year, month, 1)
    to_date = date(year, month, _mr(year, month)[1])
    r = httpx.get(
        "https://finnhub.io/api/v1/company-news",
        params={"symbol": ticker, "from": from_date.isoformat(),
                "to": to_date.isoformat(), "token": FINNHUB_KEY},
        timeout=15,
    )
    r.raise_for_status()
    return [
        {"headline": a["headline"], "source": a["source"], "url": a["url"],
         "datetime": a["datetime"], "summary": a.get("summary", ""), "image": a.get("image", "")}
        for a in r.json() if a.get("headline")
    ]


def _fetch_yahoo_month(ticker: str) -> list[dict]:
    t = yf.Ticker(ticker)
    raw = t.get_news(count=100)
    articles = []
    for n in raw:
        c = n.get("content", {})
        pub_date = c.get("pubDate", "")
        try:
            ts = int(datetime.fromisoformat(pub_date.replace("Z", "+00:00")).timestamp()) if pub_date else 0
        except Exception:
            ts = 0
        url = (c.get("clickThroughUrl") or {}).get("url", "") or (c.get("canonicalUrl") or {}).get("url", "")
        if not url or not ts:
            continue
        articles.append({
            "headline": c.get("title", ""),
            "source":   c.get("provider", {}).get("displayName", "Yahoo Finance"),
            "url":      url,
            "datetime": ts,
            "summary":  c.get("summary", ""),
            "image":    (c.get("thumbnail") or {}).get("resolutions", [{}])[-1].get("url", "") if c.get("thumbnail") else "",
        })
    return articles


def _dedup_by_headline(articles: list[dict]) -> list[dict]:
    seen, result = set(), []
    for a in articles:
        key = (a.get("headline") or "").strip().lower()
        if key and key not in seen:
            seen.add(key)
            result.append(a)
    return result


def _scrape_and_upsert(ticker: str, year_month: str, supa) -> int:
    """Fetch Finnhub + Yahoo en //, dedup, upsert. Retourne nb nouveaux articles."""
    finn_articles, yahoo_articles = [], []
    errors = []

    def _get_finn():
        nonlocal finn_articles
        try:
            finn_articles = _fetch_finnhub_month(ticker, year_month)
        except Exception as e:
            errors.append(f"finnhub: {e}")

    def _get_yahoo():
        nonlocal yahoo_articles
        try:
            yahoo_articles = _fetch_yahoo_month(ticker)
        except Exception as e:
            errors.append(f"yahoo: {e}")

    t1 = threading.Thread(target=_get_finn)
    t2 = threading.Thread(target=_get_yahoo)
    t1.start(); t2.start()
    t1.join(); t2.join()

    if errors:
        logger.warning(f"  {ticker} scrape errors: {errors}")

    new_articles = _dedup_by_headline(yahoo_articles + finn_articles)
    if not new_articles:
        return 0

    row = supa.table("articles_merged_finn_yahoo") \
        .select("articles, source") \
        .eq("ticker", ticker) \
        .eq("year_month", year_month) \
        .limit(1) \
        .execute()

    existing_row = row.data[0] if row.data else {}
    existing = existing_row.get("articles") or []
    existing_source = existing_row.get("source", "yahoo")

    merged = _dedup_by_headline(new_articles + existing)
    added = len(merged) - len(existing)
    if added <= 0:
        return 0

    new_source = "mixed" if existing_source == "finnhub" else "yahoo"
    supa.table("articles_merged_finn_yahoo").upsert({
        "ticker":     ticker,
        "year_month": year_month,
        "articles":   merged,
        "source":     new_source,
    }).execute()
    return added


# ─── Skew detection ───────────────────────────────────────────────────────────

def _detect_finn_skew(ticker: str, months: list[str], supa) -> tuple[bool, int]:
    """
    Retourne (is_skewed, cluster_wnum).
    Skew = une semaine a > SKEW_RATIO * médiane des autres semaines.
    """
    week_totals: dict[int, int] = defaultdict(int)

    for ym in months:
        row = supa.table("articles_merged_finn_yahoo") \
            .select("articles") \
            .eq("ticker", ticker) \
            .eq("year_month", ym) \
            .limit(1) \
            .execute()
        if not row.data:
            continue
        for a in (row.data[0].get("articles") or []):
            ts = a.get("datetime", 0)
            if ts:
                day = datetime.fromtimestamp(ts, tz=timezone.utc).day
                week_totals[_week_num(day)] += 1

    if len(week_totals) < 2:
        return False, 4

    counts = sorted(week_totals.values())
    median = counts[len(counts) // 2]
    max_wnum = max(week_totals, key=week_totals.get)
    max_count = week_totals[max_wnum]

    # Les autres semaines (sans le max)
    others = [v for w, v in week_totals.items() if w != max_wnum]
    if not others:
        return False, 4
    others_median = sorted(others)[len(others) // 2] or 1

    is_skewed = (max_count > SKEW_RATIO * others_median) and (max_count > 8)
    return is_skewed, max_wnum


# ─── LLM wrappers (reuse breakers_articles_service internals) ─────────────────

def _llm_call(prompt: str, max_tokens: int = 400) -> tuple[str, str]:
    """Retourne (summary_text, llm_source)."""
    from services.llm_service import llm_call
    resp = llm_call(
        [{"role": "user", "content": prompt}],
        groq_model="llama-3.3-70b-versatile",
        max_tokens=max_tokens,
        timeout=45,
    )
    src = resp.get("_llm_source", "unknown")
    return resp["choices"][0]["message"]["content"].strip(), src


def _week_label(year_month: str, week_num: int) -> str:
    year, month = int(year_month[:4]), int(year_month[5:7])
    month_names_fr = ["", "Jan", "Fév", "Mar", "Avr", "Mai", "Jun",
                      "Jul", "Aoû", "Sep", "Oct", "Nov", "Déc"]
    month_names_en = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
                      "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    names = month_names_en if os.environ.get("LLM_LANG") == "en" else month_names_fr
    last_day = monthrange(year, month)[1]
    ranges = {1: "1–7", 2: "8–14", 3: "15–21", 4: f"22–{last_day}"}
    return f"W{week_num} {names[month]} {year} ({ranges[week_num]})"


def _month_label(year_month: str) -> str:
    year, month = int(year_month[:4]), int(year_month[5:7])
    if os.environ.get("LLM_LANG") == "en":
        names = ["", "January", "February", "March", "April", "May", "June",
                 "July", "August", "September", "October", "November", "December"]
    else:
        names = ["", "Janvier", "Février", "Mars", "Avril", "Mai", "Juin",
                 "Juillet", "Août", "Septembre", "Octobre", "Novembre", "Décembre"]
    return f"{names[month]} {year}"


def _summary_col() -> str:
    return "ai_summary_en" if os.environ.get("LLM_LANG") == "en" else "ai_summary_fr"


def _week_prompt(ticker: str, label: str, articles_text: str) -> str:
    if os.environ.get("LLM_LANG") == "en":
        return (
            f"You are a senior financial analyst. Here are articles about {ticker} for {label}.\n"
            f"Write a concise weekly recap (3-5 sentences) covering the main news and their market impact.\n\n"
            f"{articles_text}"
        )
    return (
        f"Tu es un analyste financier senior. Voici des articles sur {ticker} pour {label}.\n"
        f"Rédige un récap hebdomadaire concis (3-5 phrases) couvrant les principales nouvelles et leur impact marché.\n\n"
        f"{articles_text}"
    )


def _month_prompt(ticker: str, label: str, content: str) -> str:
    if os.environ.get("LLM_LANG") == "en":
        return (
            f"You are a senior financial analyst. Here is the news summary for {ticker} — {label}.\n"
            f"Write a concise monthly recap (4-6 sentences) synthesizing the key themes and market impact.\n\n"
            f"{content}"
        )
    return (
        f"Tu es un analyste financier senior. Voici l'actualité de {ticker} pour {label}.\n"
        f"Rédige un récap mensuel concis (4-6 phrases) synthétisant les thèmes clés et l'impact marché.\n\n"
        f"{content}"
    )


# ─── Weekly generation ────────────────────────────────────────────────────────

def _gen_weekly(ticker: str, year_month: str, week_num: int,
                articles: list[dict], supa) -> bool:
    """Génère et upserte un weekly summary. Retourne True si succès."""
    label = _week_label(year_month, week_num)
    articles_text = "\n".join(f"- {a['headline']}" for a in articles[:30])
    try:
        summary, src = _llm_call(_week_prompt(ticker, label, articles_text), max_tokens=400)
        sc = _summary_col()
        top = sorted(articles, key=lambda x: x.get("datetime", 0), reverse=True)[:10]
        supa.table("article_week_summaries").upsert({
            "ticker":        ticker,
            "year_month":    year_month,
            "week_num":      week_num,
            sc:              summary,
            "top_articles":  top,
            "article_count": len(articles),
            "llm_source":    src,
            "condensed_at":  datetime.now(timezone.utc).isoformat(),
        }).execute()
        logger.info(f"  ✓ W {ticker} {year_month} W{week_num} ({len(articles)} arts) [{src}]")
        return True
    except Exception as e:
        logger.error(f"  ✗ W {ticker} {year_month} W{week_num}: {e}")
        return False


def _process_weeklies(ticker: str, finn_skewed: bool, cluster_wnum: int,
                      sliding_weeks: list[tuple[str, int]], supa) -> int:
    """Génère les weekly summaries manquants. Retourne le nb généré."""
    generated = 0
    for year_month, week_num in sliding_weeks:
        # Skip la semaine cluster si skew détecté
        if finn_skewed and week_num == cluster_wnum:
            logger.info(f"  ⏭ W {ticker} {year_month} W{week_num} — skew cluster, skip")
            continue

        # Déjà existant ?
        exists = supa.table("article_week_summaries") \
            .select("week_num") \
            .eq("ticker", ticker) \
            .eq("year_month", year_month) \
            .eq("week_num", week_num) \
            .limit(1) \
            .execute()
        if exists.data:
            continue

        # Charger les articles de cette semaine
        row = supa.table("articles_merged_finn_yahoo") \
            .select("articles") \
            .eq("ticker", ticker) \
            .eq("year_month", year_month) \
            .limit(1) \
            .execute()
        if not row.data:
            continue

        week_articles = [
            a for a in (row.data[0].get("articles") or [])
            if _week_num(datetime.fromtimestamp(a.get("datetime", 0), tz=timezone.utc).day) == week_num
        ]
        if len(week_articles) < MIN_ARTICLES_FOR_WEEKLY:
            logger.info(f"  ⏭ W {ticker} {year_month} W{week_num} — {len(week_articles)} arts < {MIN_ARTICLES_FOR_WEEKLY}")
            continue

        if _gen_weekly(ticker, year_month, week_num, week_articles, supa):
            generated += 1
        time.sleep(0.3)

    return generated


# ─── Monthly generation ───────────────────────────────────────────────────────

def _gen_monthly_from_raw(ticker: str, year_month: str, supa) -> bool:
    row = supa.table("articles_merged_finn_yahoo") \
        .select("articles") \
        .eq("ticker", ticker) \
        .eq("year_month", year_month) \
        .limit(1) \
        .execute()
    if not row.data:
        return False
    raw = row.data[0].get("articles") or []
    if len(raw) < MIN_ARTICLES_FOR_MONTHLY:
        return False

    label = _month_label(year_month)
    articles_text = "\n".join(f"- {a['headline']}" for a in raw[:40])
    try:
        summary, src = _llm_call(_month_prompt(ticker, label, articles_text), max_tokens=500)
        sc = _summary_col()
        supa.table("article_month_summaries").upsert({
            "ticker":        ticker,
            "year_month":    year_month,
            sc:              summary,
            "article_count": len(raw),
            "llm_source":    src,
            "condensed_at":  datetime.now(timezone.utc).isoformat(),
        }).execute()
        logger.info(f"  ✓ M {ticker} {year_month} raw ({len(raw)} arts) [{src}]")
        return True
    except Exception as e:
        logger.error(f"  ✗ M {ticker} {year_month} raw: {e}")
        return False


def _gen_monthly_from_weeklies(ticker: str, year_month: str,
                                week_rows: list[dict], supa) -> bool:
    sc = _summary_col()
    src_col = "ai_summary_en" if os.environ.get("LLM_LANG") == "en" else "ai_summary_fr"
    weeks_text = "\n".join(
        f"W{r['week_num']}: {r.get(src_col) or r.get('ai_summary', '')}"
        for r in week_rows
    )
    article_count = sum(r.get("article_count", 0) for r in week_rows)
    label = _month_label(year_month)
    try:
        summary, src = _llm_call(_month_prompt(ticker, label, weeks_text), max_tokens=500)
        supa.table("article_month_summaries").upsert({
            "ticker":        ticker,
            "year_month":    year_month,
            sc:              summary,
            "article_count": article_count,
            "llm_source":    src,
            "condensed_at":  datetime.now(timezone.utc).isoformat(),
        }).execute()
        logger.info(f"  ✓ M {ticker} {year_month} weeklies ({len(week_rows)} W) [{src}]")
        return True
    except Exception as e:
        logger.error(f"  ✗ M {ticker} {year_month} weeklies: {e}")
        return False


def _process_monthlies(ticker: str, finn_skewed: bool,
                        months: list[str], supa) -> int:
    """Génère les monthly summaries manquants. Retourne le nb généré."""
    generated = 0
    for year_month in months:
        # Déjà existant ?
        exists = supa.table("article_month_summaries") \
            .select("year_month") \
            .eq("ticker", ticker) \
            .eq("year_month", year_month) \
            .limit(1) \
            .execute()
        if exists.data:
            continue

        if finn_skewed:
            # Skew → gen depuis raw directement
            if _gen_monthly_from_raw(ticker, year_month, supa):
                generated += 1
        else:
            # Essayer depuis les weeklies d'abord
            week_rows = supa.table("article_week_summaries") \
                .select("week_num, ai_summary, ai_summary_en, article_count") \
                .eq("ticker", ticker) \
                .eq("year_month", year_month) \
                .order("week_num") \
                .execute()

            if week_rows.data and len(week_rows.data) >= 3:
                if _gen_monthly_from_weeklies(ticker, year_month, week_rows.data, supa):
                    generated += 1
            else:
                # Pas assez de weeklies → gen depuis raw
                if _gen_monthly_from_raw(ticker, year_month, supa):
                    generated += 1
        time.sleep(0.3)

    return generated


# ─── Main per-ticker ──────────────────────────────────────────────────────────

def _process_ticker(ticker: str, year_month: str, sliding_weeks: list[tuple[str, int]],
                    months: list[str], supa) -> dict:
    result = {
        "ticker": ticker,
        "new_articles": 0,
        "weeklies": 0,
        "monthlies": 0,
        "skewed": False,
        "error": None,
    }
    try:
        # 1. Scrape
        added = _scrape_and_upsert(ticker, year_month, supa)
        result["new_articles"] = added
        if added > 0:
            logger.info(f"  + {ticker}: {added} nouveaux articles")

        # 2. Détection skew sur les 3 derniers mois
        skew_months = months[:3]
        is_skewed, cluster_wnum = _detect_finn_skew(ticker, skew_months, supa)
        result["skewed"] = is_skewed
        if is_skewed:
            logger.info(f"  ⚠ {ticker}: Finnhub skew détecté W{cluster_wnum}")

        # 3. Weeklies
        result["weeklies"] = _process_weeklies(ticker, is_skewed, cluster_wnum, sliding_weeks, supa)

        # 4. Monthlies
        result["monthlies"] = _process_monthlies(ticker, is_skewed, months, supa)

    except Exception as e:
        result["error"] = str(e)
        logger.error(f"  ✗ {ticker}: {e}")

    return result


# ─── Entry point ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--region", required=True, choices=["US", "EU", "ASIA"])
    parser.add_argument("--lang", required=True, choices=["en", "fr"])
    parser.add_argument("--ticker", default=None, help="Ticker unique (debug)")
    parser.add_argument("--no-resume", action="store_true", help="Ignore le checkpoint")
    args = parser.parse_args()

    os.environ["LLM_LANG"] = args.lang

    from data.tickers import get_by_region
    tickers = [args.ticker.upper()] if args.ticker else get_by_region(args.region)

    cp_path = _checkpoint_path(args.region, args.lang)
    done = set() if args.no_resume else _load_checkpoint(cp_path)
    todo = [t for t in tickers if t not in done]
    total = len(todo)

    now = datetime.now(timezone.utc)
    year_month = now.strftime("%Y-%m")
    sliding = _sliding_weeks(n=5)    # 5 dernières semaines glissantes
    months = _last_n_months(n=6)     # 6 derniers mois

    logger.info(f"refresh_breakers — {args.region} {args.lang.upper()} — {total} tickers ({len(done)} done)")
    _telegram(
        f"🔄 *refresh_breakers démarré — {args.region} {args.lang.upper()}*\n"
        f"{total} tickers à traiter\n"
        f"Fenêtre : {sliding[-1][0]} W{sliding[-1][1]} → {sliding[0][0]} W{sliding[0][1]}\n"
        f"Mois : {months[-1]} → {months[0]}"
    )

    supa = _supa()
    start_time = time.time()
    batch_errors: list[str] = []
    batch_skewed: list[str] = []
    total_new_arts = 0
    total_weeks_gen = 0
    total_months_gen = 0

    for i, ticker in enumerate(todo, 1):
        logger.info(f"[{i}/{total}] {ticker}")
        res = _process_ticker(ticker, year_month, sliding, months, supa)

        if res["error"]:
            batch_errors.append(ticker)
        if res["skewed"]:
            batch_skewed.append(ticker)

        total_new_arts  += res["new_articles"]
        total_weeks_gen += res["weeklies"]
        total_months_gen += res["monthlies"]

        done.add(ticker)
        _save_checkpoint(cp_path, done)

        if i % NOTIFY_EVERY == 0:
            elapsed = int(time.time() - start_time)
            mins, secs = divmod(elapsed, 60)
            remaining = int((elapsed / i) * (total - i))
            r_mins, r_secs = divmod(remaining, 60)
            _telegram(
                f"📊 *refresh_breakers — {args.region} {args.lang.upper()}*\n"
                f"Checkpoint {i}/{total} ({i*100//total}%)\n"
                f"⏱ {mins}m{secs:02d}s · ~{r_mins}m{r_secs:02d}s restant\n"
                f"📰 +{total_new_arts} articles · 📅 {total_weeks_gen}W · 📆 {total_months_gen}M\n"
                f"{'⚠ Skewed: ' + ', '.join(batch_skewed[-5:]) if batch_skewed else '✅ 0 skew'}\n"
                f"{'❌ Errors: ' + ', '.join(batch_errors[-5:]) if batch_errors else '✅ 0 erreurs'}"
            )

    elapsed = int(time.time() - start_time)
    mins, secs = divmod(elapsed, 60)
    _telegram(
        f"✅ *refresh_breakers terminé — {args.region} {args.lang.upper()}*\n"
        f"{total} tickers · ⏱ {mins}m{secs:02d}s\n"
        f"📰 +{total_new_arts} articles · 📅 {total_weeks_gen}W · 📆 {total_months_gen}M\n"
        f"⚠ Skewed: {len(batch_skewed)} tickers\n"
        f"{'❌ Errors: ' + ', '.join(batch_errors) if batch_errors else '✅ 0 erreurs'}"
    )
    logger.info(f"Done — {total} tickers · {mins}m{secs:02d}s · +{total_new_arts} arts · "
                f"{total_weeks_gen}W · {total_months_gen}M · {len(batch_skewed)} skewed")


if __name__ == "__main__":
    main()
