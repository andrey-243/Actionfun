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
from concurrent.futures import ThreadPoolExecutor, as_completed, wait, FIRST_COMPLETED
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

# Event partagé entre les threads : set quand FreeTheAI épuise son quota daily
_freetheai_monthly_stop = threading.Event()
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


def _load_checkpoint(path: Path) -> tuple[set[str], set[str], set[str]]:
    """Retourne (done, legacy, monthly_done)."""
    if path.exists():
        try:
            data = json.loads(path.read_text())
            done = set(data.get("done", []))
            legacy = set(data.get("legacy", []))
            monthly_done = set(data.get("monthly_done", []))
            return done, legacy, monthly_done
        except Exception:
            pass
    return set(), set(), set()


def _save_checkpoint(path: Path, done: set[str], legacy: set[str], monthly_done: set[str]):
    path.write_text(json.dumps({
        "done":         sorted(done),
        "legacy":       sorted(legacy),
        "monthly_done": sorted(monthly_done),
    }, indent=2))


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

    row = supa.table("articles") \
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
    supa.table("articles").upsert({
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
        row = supa.table("articles") \
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

def _llm_call(prompt: str, max_tokens: int = 400, cascade: str = "default") -> tuple[str, str]:
    """Retourne (summary_text, llm_source)."""
    from services.llm_service import llm_call
    resp = llm_call(
        [{"role": "user", "content": prompt}],
        groq_model="llama-3.3-70b-versatile",
        max_tokens=max_tokens,
        timeout=45,
        cascade=cascade,
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
            f"Write a concise weekly recap (3-5 sentences) covering the main news and their market impact.\n"
            f"Ignore generic market roundups or articles not specifically about {ticker}.\n\n"
            f"{articles_text}"
        )
    return (
        f"Tu es un analyste financier senior. Voici des articles sur {ticker} pour {label}.\n"
        f"Rédige un récap hebdomadaire concis (3-5 phrases) couvrant les principales nouvelles et leur impact marché.\n"
        f"Ignore les articles génériques (top marchés, classements) non spécifiques à {ticker}.\n\n"
        f"{articles_text}"
    )


def _month_prompt(ticker: str, label: str, content: str) -> str:
    if os.environ.get("LLM_LANG") == "en":
        return (
            f"You are a senior financial analyst. Here is the news summary for {ticker} — {label}.\n"
            f"Write a concise monthly recap (4-6 sentences) synthesizing the key themes and market impact.\n"
            f"Ignore generic market roundups or articles not specifically about {ticker}.\n\n"
            f"{content}"
        )
    return (
        f"Tu es un analyste financier senior. Voici l'actualité de {ticker} pour {label}.\n"
        f"Rédige un récap mensuel concis (4-6 phrases) synthétisant les thèmes clés et l'impact marché.\n"
        f"Ignore les articles génériques (top marchés, classements) non spécifiques à {ticker}.\n\n"
        f"{content}"
    )


# ─── Weekly generation ────────────────────────────────────────────────────────

def _gen_weekly(ticker: str, year_month: str, week_num: int,
                articles: list[dict], supa) -> str | bool:
    """str=succès · False=échec LLM. Pas de skip possible (articles déjà vérifiés)."""
    label = _week_label(year_month, week_num)
    articles_text = "\n".join(f"- {a['headline']}" for a in articles[:30])
    try:
        summary, src = _llm_call(_week_prompt(ticker, label, articles_text), max_tokens=400, cascade="weekly_scrap")
        sc = _summary_col()
        top = sorted(articles, key=lambda x: x.get("datetime", 0), reverse=True)[:10]
        supa.table("breaks_weekly").upsert({
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
        return src
    except Exception as e:
        logger.error(f"  ✗ W {ticker} {year_month} W{week_num}: {e}")
        return False


def _process_weeklies(ticker: str, finn_skewed: bool, cluster_wnum: int,
                      sliding_weeks: list[tuple[str, int]], supa,
                      force_regen: bool = False) -> tuple[int, list[str], int]:
    """Génère les weekly summaries manquants. Retourne (nb généré, sources LLM, nb échecs)."""
    generated = 0
    sources: list[str] = []
    failures = 0
    for year_month, week_num in sliding_weeks:
        # Skip la semaine cluster si skew détecté
        if finn_skewed and week_num == cluster_wnum:
            logger.info(f"  ⏭ W {ticker} {year_month} W{week_num} — skew cluster, skip")
            continue

        # Déjà existant ? (sauf si force_regen)
        if not force_regen:
            exists = supa.table("breaks_weekly") \
                .select("week_num") \
                .eq("ticker", ticker) \
                .eq("year_month", year_month) \
                .eq("week_num", week_num) \
                .limit(1) \
                .execute()
            if exists.data:
                continue

        # Charger les articles de cette semaine
        row = supa.table("articles") \
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

        src = _gen_weekly(ticker, year_month, week_num, week_articles, supa)
        if isinstance(src, str):
            generated += 1
            sources.append(src)
        else:
            failures += 1  # False = LLM exception
        time.sleep(0.3)

    return generated, sources, failures


# ─── Monthly generation ───────────────────────────────────────────────────────

def _gen_monthly_from_raw(ticker: str, year_month: str, supa) -> str | None | bool:
    """str=succès · None=skip (pas de data) · False=échec LLM."""
    row = supa.table("articles") \
        .select("articles") \
        .eq("ticker", ticker) \
        .eq("year_month", year_month) \
        .limit(1) \
        .execute()
    if not row.data:
        return None
    raw = row.data[0].get("articles") or []
    if len(raw) < MIN_ARTICLES_FOR_MONTHLY:
        return None

    label = _month_label(year_month)
    articles_text = "\n".join(f"- {a['headline']}" for a in raw[:40])
    try:
        summary, src = _llm_call(_month_prompt(ticker, label, articles_text), max_tokens=500, cascade="monthly_scrap")
        sc = _summary_col()
        supa.table("breaks_monthly").upsert({
            "ticker":        ticker,
            "year_month":    year_month,
            sc:              summary,
            "article_count": len(raw),
            "llm_source":    src,
            "condensed_at":  datetime.now(timezone.utc).isoformat(),
        }).execute()
        logger.info(f"  ✓ M {ticker} {year_month} raw ({len(raw)} arts) [{src}]")
        return src
    except Exception as e:
        logger.error(f"  ✗ M {ticker} {year_month} raw: {e}")
        return False


def _gen_monthly_from_weeklies(ticker: str, year_month: str,
                                week_rows: list[dict], supa) -> str | bool:
    sc = _summary_col()
    src_col = "ai_summary_en" if os.environ.get("LLM_LANG") == "en" else "ai_summary_fr"
    weeks_text = "\n".join(
        f"W{r['week_num']}: {r.get(src_col) or r.get('ai_summary', '')}"
        for r in week_rows
    )
    article_count = sum(r.get("article_count", 0) for r in week_rows)
    label = _month_label(year_month)
    try:
        summary, src = _llm_call(_month_prompt(ticker, label, weeks_text), max_tokens=500, cascade="monthly_scrap")
        supa.table("breaks_monthly").upsert({
            "ticker":        ticker,
            "year_month":    year_month,
            sc:              summary,
            "article_count": article_count,
            "llm_source":    src,
            "condensed_at":  datetime.now(timezone.utc).isoformat(),
        }).execute()
        logger.info(f"  ✓ M {ticker} {year_month} weeklies ({len(week_rows)} W) [{src}]")
        return src
    except Exception as e:
        logger.error(f"  ✗ M {ticker} {year_month} weeklies: {e}")
        return False


def _process_monthlies(ticker: str, finn_skewed: bool,
                        months: list[str], supa,
                        force_regen: bool = False) -> tuple[int, list[str], int]:
    """Génère les monthly summaries (FreeTheAI only). Retourne (nb généré, sources LLM, nb échecs)."""
    from services.llm_service import FreeTheAIExhaustedError
    generated = 0
    sources: list[str] = []
    failures = 0

    if _freetheai_monthly_stop.is_set():
        return 0, [], 0

    for year_month in months:
        if _freetheai_monthly_stop.is_set():
            break

        # Déjà existant ? (sauf si force_regen)
        if not force_regen:
            exists = supa.table("breaks_monthly") \
                .select("year_month") \
                .eq("ticker", ticker) \
                .eq("year_month", year_month) \
                .limit(1) \
                .execute()
            if exists.data:
                continue

        try:
            if finn_skewed:
                src = _gen_monthly_from_raw(ticker, year_month, supa)
            else:
                week_rows = supa.table("breaks_weekly") \
                    .select("week_num, ai_summary, ai_summary_en, article_count") \
                    .eq("ticker", ticker) \
                    .eq("year_month", year_month) \
                    .order("week_num") \
                    .execute()

                if week_rows.data and len(week_rows.data) >= 3:
                    src = _gen_monthly_from_weeklies(ticker, year_month, week_rows.data, supa)
                else:
                    src = _gen_monthly_from_raw(ticker, year_month, supa)
        except FreeTheAIExhaustedError:
            _freetheai_monthly_stop.set()
            logger.warning(f"  FreeTheAI épuisé — monthlies arrêtés ({ticker} {year_month})")
            _telegram("🔴 *FreeTheAI quota daily épuisé*\nMonthlies arrêtés pour ce batch. Relancer demain avec la même commande.")
            break

        if isinstance(src, str):
            generated += 1
            sources.append(src)
        elif src is False:
            failures += 1
        time.sleep(0.3)

    return generated, sources, failures


# ─── Main per-ticker (deux flows séparés) ─────────────────────────────────────

def _fmt_sources(sources: list[str]) -> str:
    if not sources:
        return "0"
    counts: dict[str, int] = {}
    for s in sources:
        counts[s] = counts.get(s, 0) + 1
    return " · ".join(f"{c} {s}" for s, c in sorted(counts.items()))


def _process_ticker_ab(ticker: str, year_month: str, sliding_weeks: list[tuple[str, int]],
                       months: list[str], supa, lang: str = "en",
                       force_regen: bool = False) -> dict:
    """Flow A+B : scrape + DUMB (Groq→Hapuppy) + weeklies (Hapuppy only)."""
    result = {
        "ticker": ticker, "flow": "ab",
        "new_articles": 0, "weeklies": 0,
        "failures": 0, "skewed": False, "error": None,
    }
    try:
        added = _scrape_and_upsert(ticker, year_month, supa)
        result["new_articles"] = added
        if added > 0:
            logger.info(f"  + {ticker}: {added} nouveaux articles")

        try:
            from services.overview_service import gen_dumb
            n_dumb = gen_dumb(ticker, lang, cascade="dumb_scrap")
            if n_dumb > 0:
                logger.info(f"  DUMB {ticker}: {n_dumb} articles brutalised")
        except Exception as dumb_err:
            logger.warning(f"  DUMB {ticker} failed (non bloquant): {dumb_err}")

        skew_months = months[:3]
        is_skewed, cluster_wnum = _detect_finn_skew(ticker, skew_months, supa)
        result["skewed"] = is_skewed
        if is_skewed:
            logger.info(f"  ⚠ {ticker}: Finnhub skew détecté W{cluster_wnum}")

        w_count, w_sources, w_failures = _process_weeklies(ticker, is_skewed, cluster_wnum, sliding_weeks, supa, force_regen=force_regen)
        result["weeklies"] = w_count
        result["failures"] = w_failures

        if w_count:
            logger.info(f"  ↳ {ticker} AB — {w_count}W [{_fmt_sources(w_sources)}]")

    except Exception as e:
        import traceback
        result["error"] = str(e)
        logger.error(f"  ✗ {ticker} AB: {e}\n{traceback.format_exc()}")

    return result


def _process_ticker_monthly(ticker: str, months: list[str], supa,
                             force_regen: bool = False) -> dict:
    """Flow M : monthlies (FreeTheAI only). Skip si _freetheai_monthly_stop est set."""
    result = {
        "ticker": ticker, "flow": "monthly",
        "monthlies": 0, "failures": 0, "error": None,
    }
    try:
        # Détection skew (nécessaire pour choisir la stratégie monthly)
        skew_months = months[:3]
        # supa est thread-safe via le SDK Supabase
        is_skewed, _ = _detect_finn_skew(ticker, skew_months, supa)

        m_count, m_sources, m_failures = _process_monthlies(ticker, is_skewed, months, supa, force_regen=force_regen)
        result["monthlies"] = m_count
        result["failures"] = m_failures

        if m_count:
            logger.info(f"  ↳ {ticker} M — {m_count}M [{_fmt_sources(m_sources)}]")

    except Exception as e:
        import traceback
        result["error"] = str(e)
        logger.error(f"  ✗ {ticker} M: {e}\n{traceback.format_exc()}")

    return result


# ─── Entry point ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--region", required=True, choices=["US", "EU", "ASIA"])
    parser.add_argument("--lang", required=True, choices=["en", "fr"])
    parser.add_argument("--ticker", default=None, help="Ticker unique (debug)")
    parser.add_argument("--no-resume", action="store_true", help="Ignore le checkpoint")
    parser.add_argument("--re-enrich-legacy", action="store_true", help="Re-traite les tickers legacy (pré-dumb enrichment)")
    parser.add_argument("--force-regen", action="store_true", help="Régénère weeklies/monthlies même s'ils existent déjà en DB")
    args = parser.parse_args()

    os.environ["LLM_LANG"] = args.lang

    from data.tickers import get_by_region
    tickers = [args.ticker.upper()] if args.ticker else get_by_region(args.region)

    cp_path = _checkpoint_path(args.region, args.lang)
    if args.no_resume:
        done, legacy, monthly_done = set(), set(), set()
    else:
        done, legacy, monthly_done = _load_checkpoint(cp_path)

    # Tickers traités avant l'enrichissement dumb+summary → marquer comme legacy
    if done and not legacy:
        legacy = set(done)
        _save_checkpoint(cp_path, done, legacy, monthly_done)
        logger.info(f"{len(legacy)} tickers marqués legacy (pré-dumb enrichment)")

    if args.re_enrich_legacy:
        todo_ab = [t for t in tickers if t in legacy]
        logger.info(f"Re-enrich legacy : {len(todo_ab)} tickers")
    else:
        todo_ab = [t for t in tickers if t not in done or t in legacy]

    todo_monthly = [t for t in tickers if t not in monthly_done]
    total_ab = len(todo_ab)
    total_m = len(todo_monthly)

    now = datetime.now(timezone.utc)
    year_month = now.strftime("%Y-%m")
    sliding = _sliding_weeks(n=5)
    months = _last_n_months(n=6)

    logger.info(
        f"refresh_breakers — {args.region} {args.lang.upper()} — "
        f"AB: {total_ab} tickers ({len(done)} done) | M: {total_m} tickers ({len(monthly_done)} done)"
    )
    _telegram(
        f"🔄 *refresh_breakers démarré — {args.region} {args.lang.upper()}*\n"
        f"Flow AB: {total_ab} tickers | Flow M: {total_m} tickers\n"
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
    cp_lock = threading.Lock()
    completed_count = 0

    def _save_cp():
        with cp_lock:
            _save_checkpoint(cp_path, done, legacy, monthly_done)

    # ── Deux flux parallèles ─────────────────────────────────────────────────
    # Flow AB (max 2 workers) : scrape + DUMB + weeklies
    # Flow M  (max 1 worker)  : monthlies FreeTheAI only
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="ab") as ex_ab, \
         ThreadPoolExecutor(max_workers=1, thread_name_prefix="m") as ex_m:

        futures_ab: dict = {
            ex_ab.submit(
                _process_ticker_ab, t, year_month, sliding, months, supa,
                args.lang, args.force_regen or (t in legacy)
            ): t
            for t in todo_ab
        }
        futures_m: dict = {
            ex_m.submit(_process_ticker_monthly, t, months, supa, args.force_regen): t
            for t in todo_monthly
        }

        all_futures = {**futures_ab, **futures_m}

        for f in as_completed(all_futures):
            ticker = all_futures[f]
            flow = "ab" if f in futures_ab else "monthly"
            completed_count += 1

            try:
                res = f.result()
            except Exception as e:
                import traceback
                logger.error(f"  ✗ {ticker} ({flow}) exception: {e}\n{traceback.format_exc()}")
                batch_errors.append(ticker)
                continue

            if res.get("error"):
                batch_errors.append(ticker)
            if res.get("skewed"):
                batch_skewed.append(ticker)

            total_new_arts  += res.get("new_articles", 0)
            total_weeks_gen += res.get("weeklies", 0)
            total_months_gen += res.get("monthlies", 0)

            if flow == "ab":
                if not res["failures"] and not res["error"]:
                    done.add(ticker)
                    legacy.discard(ticker)
                    _save_cp()
                else:
                    logger.warning(f"  ⚠ {ticker} AB non marqué done ({res['failures']} échec(s)) — retenté au prochain batch")
            else:  # monthly
                if not res["failures"] and not res["error"]:
                    monthly_done.add(ticker)
                    _save_cp()
                elif res["failures"]:
                    logger.warning(f"  ⚠ {ticker} M non marqué done ({res['failures']} échec(s)) — retenté au prochain batch")

            if completed_count % NOTIFY_EVERY == 0:
                elapsed = int(time.time() - start_time)
                mins, secs = divmod(elapsed, 60)
                total_all = total_ab + total_m
                remaining = int((elapsed / completed_count) * (total_all - completed_count)) if completed_count else 0
                r_mins, r_secs = divmod(remaining, 60)
                _telegram(
                    f"📊 *refresh_breakers — {args.region} {args.lang.upper()}*\n"
                    f"Checkpoint {completed_count}/{total_all} ({completed_count*100//total_all}%)\n"
                    f"⏱ {mins}m{secs:02d}s · ~{r_mins}m{r_secs:02d}s restant\n"
                    f"📰 +{total_new_arts} articles · 📅 {total_weeks_gen}W · 📆 {total_months_gen}M\n"
                    f"{'⚠ Skewed: ' + ', '.join(batch_skewed[-5:]) if batch_skewed else '✅ 0 skew'}\n"
                    f"{'❌ Errors: ' + ', '.join(batch_errors[-5:]) if batch_errors else '✅ 0 erreurs'}"
                )

    elapsed = int(time.time() - start_time)
    mins, secs = divmod(elapsed, 60)
    m_status = "arrêtés (FreeTheAI épuisé)" if _freetheai_monthly_stop.is_set() else f"{total_months_gen} générés"
    _telegram(
        f"✅ *refresh_breakers terminé — {args.region} {args.lang.upper()}*\n"
        f"⏱ {mins}m{secs:02d}s\n"
        f"📰 +{total_new_arts} articles · 📅 {total_weeks_gen}W · 📆 {total_months_gen}M ({m_status})\n"
        f"⚠ Skewed: {len(batch_skewed)} tickers\n"
        f"{'❌ Errors: ' + ', '.join(batch_errors) if batch_errors else '✅ 0 erreurs'}"
    )
    logger.info(
        f"Done — {mins}m{secs:02d}s · +{total_new_arts} arts · "
        f"{total_weeks_gen}W · {total_months_gen}M · {len(batch_skewed)} skewed"
    )


if __name__ == "__main__":
    main()
