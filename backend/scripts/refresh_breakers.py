"""
refresh_breakers.py — Scrape articles + génère weekly/monthly breakers par ticker.
# ZEN PROMO | original: cascade="zen" (Gemini → raise)

Pour chaque ticker (région) :
  Phase 1 : RSS (12m) + Yahoo (mois courant) + Finnhub (US, 12m) — upserts séparés
  Gate fraicheur : si frais après P1 → skip P2
  Phase 2 : NewsAPI (1m) + Marketaux (3m) + AV (12m) si budget dispo
  Post-scrap par ticker : DUMB → W (5 semaines glissantes, skew Finnhub géré) → M

Usage :
  python scripts/refresh_breakers.py --region US --lang en
  python scripts/refresh_breakers.py --region EU  --lang fr
  python scripts/refresh_breakers.py --region ASIA --lang en
  python scripts/refresh_breakers.py --region US --lang en --ticker AAPL
  python scripts/refresh_breakers.py --region US --lang en --force-regen
"""

import argparse
import json
import logging
import os
import re
import sys
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import defaultdict
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
from services.yfinance_service import bulk_fetch_company_names as _bulk_fetch_company_names
from services.breakers_articles_service import (
    _detect_region, _build_rss_query, _is_month_fresh,
    _fetch_rss_month, _fetch_newsapi_month, _fetch_marketaux_month, _fetch_av_month,
    _normalize_google_rss, _normalize_newsapi, _normalize_marketaux, _normalize_av,
    _upsert_articles_merge, scrap_gen_weekly, scrap_gen_monthly,
    _article_text, MIN_W_SCRAP, MIN_M_SCRAP,
)

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
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

FINNHUB_KEY = os.environ["FINNHUB_API_KEY"]
SKEW_RATIO = 2.5

# Désactivé pendant promo hapuppy — réactiver post-promo pour monthlies FreeTheAI
_freetheai_monthly_stop = threading.Event()


# ─── Telegram ─────────────────────────────────────────────────────────────────

def _telegram(msg: str):
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        logger.warning(f"TG skip — token={'set' if token else 'MISSING'} chat_id={'set' if chat_id else 'MISSING'}")
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
    except Exception as e:
        logger.warning(f"TG send failed: {e}")


# ─── Supabase ─────────────────────────────────────────────────────────────────

_thread_local = threading.local()


def _supa():
    """Client Supabase isolé par thread — HTTP/1.1 forcé (postgrest-py hardcode http2=True)."""
    if not getattr(_thread_local, "client", None):
        client = create_client(
            os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"]
        )
        old = client.postgrest.session
        client.postgrest.session = httpx.Client(
            base_url=str(old.base_url),
            headers=dict(old.headers),
            timeout=old.timeout,
            follow_redirects=True,
            http2=False,
        )
        _thread_local.client = client
    return _thread_local.client


def _in_ticker_chunks(supa, table: str, select: str, tickers: list[str], filter_fn, chunk_size: int = 100) -> list[dict]:
    """Query chunked by ticker to avoid PostgREST URL limit (>2000 chars with 250 tickers)."""
    result: list[dict] = []
    for i in range(0, len(tickers), chunk_size):
        chunk = tickers[i:i + chunk_size]
        q = supa.table(table).select(select).in_("ticker", chunk)
        r = _exec(filter_fn(q))
        result.extend(r.data or [])
    return result


def _exec(query, retries: int = 5):
    """Retry Supabase .execute() sur transient disconnects — backoff 3s × 3^attempt."""
    delay = 3.0
    for attempt in range(retries + 1):
        try:
            return query.execute()
        except (httpx.RemoteProtocolError, httpx.ReadError, httpx.ConnectError) as e:
            if attempt == retries:
                raise
            logger.warning(f"  Supabase transient error (attempt {attempt + 1}/{retries}): {e} — retry in {delay:.0f}s")
            time.sleep(delay)
            delay *= 3


# ─── Helpers date ─────────────────────────────────────────────────────────────

def _week_num(day: int) -> int:
    if day <= 7:  return 1
    if day <= 14: return 2
    if day <= 21: return 3
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


# ─── Fetch helpers ─────────────────────────────────────────────────────────────

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


def _parse_yahoo_news_item(n: dict) -> dict | None:
    c = n.get("content", {})
    pub_date = c.get("pubDate", "")
    try:
        ts = int(datetime.fromisoformat(pub_date.replace("Z", "+00:00")).timestamp()) if pub_date else 0
    except Exception:
        ts = 0
    url = (c.get("clickThroughUrl") or {}).get("url", "") or (c.get("canonicalUrl") or {}).get("url", "")
    if not url or not ts:
        return None
    return {
        "headline": c.get("title", ""),
        "source":   c.get("provider", {}).get("displayName", "Yahoo Finance"),
        "url":      url,
        "datetime": ts,
        "summary":  c.get("summary", ""),
        "image":    (c.get("thumbnail") or {}).get("resolutions", [{}])[-1].get("url", "") if c.get("thumbnail") else "",
    }


def _fetch_yahoo_month(ticker: str, name_search: bool = True, company_name: str = "") -> list[dict]:
    t = yf.Ticker(ticker)
    raw = t.get_news(count=100)
    articles = [a for n in raw if (a := _parse_yahoo_news_item(n)) is not None]

    if name_search:
        try:
            if not company_name:
                info = t.info or {}
                company_name = info.get("shortName") or info.get("longName") or ""
            if company_name and company_name.lower() != ticker.lower():
                search_raw = yf.Search(company_name, news_count=30).news or []
                extra = [a for n in search_raw if (a := _parse_yahoo_news_item(n)) is not None]
                before = len(articles)
                if extra:
                    seen_urls = {a["url"] for a in articles}
                    articles += [a for a in extra if a["url"] not in seen_urls]
                added = len(articles) - before
                logger.info(f"  Yahoo name_search {ticker} ({company_name!r}): ticker={before} search={len(extra)} +{added} new")
        except Exception as e:
            logger.debug(f"  Yahoo name_search {ticker}: skipped ({e})")

    return articles


def _dedup_by_headline(articles: list[dict]) -> list[dict]:
    seen, result = set(), []
    for a in articles:
        key = (a.get("headline") or "").strip().lower()
        if key and key not in seen:
            seen.add(key)
            result.append(a)
    return result


# ─── Skew detection ───────────────────────────────────────────────────────────

def _detect_finn_skew(ticker: str, months: list[str], supa) -> tuple[bool, int]:
    """
    Retourne (is_skewed, cluster_wnum).
    Skew = une semaine a > SKEW_RATIO * médiane des autres semaines.
    """
    week_totals: dict[int, int] = defaultdict(int)

    for ym in months:
        row = _exec(supa.table("articles")
            .select("articles")
            .eq("ticker", ticker)
            .eq("year_month", ym)
            .limit(1))
        if not row.data:
            continue
        for a in (row.data[0].get("articles") or []):
            ts = a.get("datetime", 0)
            if ts:
                day = datetime.fromtimestamp(ts, tz=timezone.utc).day
                week_totals[_week_num(day)] += 1

    if len(week_totals) < 2:
        return False, 4

    max_wnum = max(week_totals, key=week_totals.get)
    max_count = week_totals[max_wnum]
    others = [v for w, v in week_totals.items() if w != max_wnum]
    if not others:
        return False, 4
    others_median = sorted(others)[len(others) // 2] or 1

    is_skewed = (max_count > SKEW_RATIO * others_median) and (max_count > 8)
    return is_skewed, max_wnum


# ─── LLM + W generation ───────────────────────────────────────────────────────

def _llm_call(prompt: str, max_tokens: int = 400, cascade: str = "default", item_id: str = "") -> tuple[str, str]:
    """Retourne (summary_text, llm_source)."""
    from services.llm_service import llm_call
    kw: dict = dict(groq_model="llama-3.3-70b-versatile", timeout=45, cascade=cascade)
    if cascade != "promo":
        kw["max_tokens"] = max_tokens
    resp = llm_call([{"role": "user", "content": prompt}], **kw)
    src = resp.get("_llm_source", "unknown")
    model = resp.get("model", "")
    tag = f" [{item_id}]" if item_id else ""
    logger.info(f"    LLM{tag} {cascade} → {src}" + (f" ({model})" if model else ""))
    return resp["choices"][0]["message"]["content"].strip(), src


def _week_label(year_month: str, week_num: int) -> str:
    year, month = int(year_month[:4]), int(year_month[5:7])
    month_names_fr = ["", "Jan", "Fév", "Mar", "Avr", "Mai", "Jun", "Jul", "Aoû", "Sep", "Oct", "Nov", "Déc"]
    month_names_en = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
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


def _summary_col(table: str = "breaks_weekly") -> str:
    lang = os.environ.get("LLM_LANG", "en")
    cols = {
        "breaks_weekly":  ("bw_en", "bw_fr"),
        "breaks_monthly": ("bm_en", "bm_fr"),
        "breaks_yearly":  ("by_en", "by_fr"),
    }
    en_col, fr_col = cols.get(table, ("bw_en", "bw_fr"))
    return en_col if lang == "en" else fr_col


def _llm_source_col() -> str:
    return "llm_source_en" if os.environ.get("LLM_LANG", "en") == "en" else "llm_source_fr"


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


def _gen_weekly(ticker: str, year_month: str, week_num: int,
                articles: list[dict], supa, done_status: str = "done_more_10") -> str | bool:
    """str=source LLM si succès · False=échec."""
    label = _week_label(year_month, week_num)
    articles_text = "\n".join(_article_text(a) for a in articles)
    try:
        summary, src = _llm_call(_week_prompt(ticker, label, articles_text), cascade="promo", item_id=ticker)
        sc = _summary_col()
        top = sorted(articles, key=lambda x: x.get("datetime", 0), reverse=True)[:10]
        _exec(supa.table("breaks_weekly").upsert({
            "ticker":        ticker,
            "year_month":    year_month,
            "week_num":      week_num,
            sc:              summary,
            "top_articles":  top,
            "article_count": len(articles),
            _llm_source_col(): src,
            "condensed_at":  datetime.now(timezone.utc).isoformat(),
            "status":        done_status,
        }))
        logger.info(f"  ✓ W {ticker} {year_month} W{week_num} ({len(articles)} arts) [{src}]")
        return src
    except Exception as e:
        logger.error(f"  ✗ W {ticker} {year_month} W{week_num}: {e}")
        _exec(supa.table("breaks_weekly").upsert({
            "ticker": ticker, "year_month": year_month, "week_num": week_num,
            "status": "error", "article_count": len(articles),
        }))
        return False


# ─── M from raw (gardé en référence) ──────────────────────────────────────────

def _gen_monthly_from_raw(ticker: str, year_month: str, supa) -> str | None | bool:
    """str=succès · None=skip (pas de data) · False=échec LLM."""
    row = _exec(supa.table("articles")
        .select("articles")
        .eq("ticker", ticker)
        .eq("year_month", year_month)
        .limit(1))
    if not row.data:
        _exec(supa.table("breaks_monthly").upsert({
            "ticker": ticker, "year_month": year_month, "status": "no_articles_row",
        }))
        return None
    raw = row.data[0].get("articles") or []
    if len(raw) == 0:
        _exec(supa.table("breaks_monthly").upsert({
            "ticker": ticker, "year_month": year_month, "status": "no_articles", "article_count": 0,
        }))
        return None

    label = _month_label(year_month)
    articles_text = "\n".join(_article_text(a) for a in raw)
    try:
        summary, src = _llm_call(_month_prompt(ticker, label, articles_text), cascade="bub_batch", item_id=ticker)
        sc = _summary_col("breaks_monthly")
        _exec(supa.table("breaks_monthly").upsert({
            "ticker":        ticker,
            "year_month":    year_month,
            sc:              summary,
            "article_count": len(raw),
            _llm_source_col(): src,
            "condensed_at":  datetime.now(timezone.utc).isoformat(),
        }))
        logger.info(f"  ✓ M {ticker} {year_month} raw ({len(raw)} arts) [{src}]")
        return src
    except Exception as e:
        logger.error(f"  ✗ M {ticker} {year_month} raw: {e}")
        _exec(supa.table("breaks_monthly").upsert({
            "ticker": ticker, "year_month": year_month,
            "status": "error", "article_count": len(raw),
        }))
        return False


# ─── Scrap phase ──────────────────────────────────────────────────────────────

def _fmt_sources(sources: list[str]) -> str:
    if not sources:
        return "0"
    counts: dict[str, int] = {}
    for s in sources:
        counts[s] = counts.get(s, 0) + 1
    return " · ".join(f"{c} {s}" for s, c in sorted(counts.items()))


def _build_qty_gate(
    candidates: list[tuple[str, str]],
    today_ym: str,
    current_wnum: int,
    supa,
) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """
    Retourne (w_deficit, m_deficit) après lecture des comptes articles en DB.

    w_deficit : paires où semaine courante < MIN_W_SCRAP (mois courant)
                ou mois < MIN_M_SCRAP (mois passés).
    m_deficit : paires où mois < MIN_M_SCRAP, toutes périodes.
    """
    if not candidates:
        return [], []

    candidate_set = set(candidates)
    all_tickers = list({t for t, _ in candidates})
    all_yms     = list({ym for _, ym in candidates})

    rows_data = _in_ticker_chunks(supa, "articles", "ticker, year_month, articles", all_tickers,
                                   lambda q: q.in_("year_month", all_yms))

    month_cnt: dict[tuple, int] = {}
    week_cnt:  dict[tuple, int] = {}

    for row in rows_data:
        key = (row["ticker"], row["year_month"])
        if key not in candidate_set:
            continue
        arts = row.get("articles") or []
        month_cnt[key] = len(arts)
        if row["year_month"] == today_ym:
            week_cnt[key] = sum(
                1 for a in arts
                if _week_num(
                    datetime.fromtimestamp(a.get("datetime", 0), tz=timezone.utc).day
                ) == current_wnum
            )

    w_deficit, m_deficit = [], []
    for t, ym in candidates:
        key = (t, ym)
        mc = month_cnt.get(key, 0)
        wc = week_cnt.get(key, 0)
        if ym == today_ym:
            if wc < MIN_W_SCRAP:
                w_deficit.append(key)
        else:
            if mc < MIN_M_SCRAP:
                w_deficit.append(key)
        if mc < MIN_M_SCRAP:
            m_deficit.append(key)

    return w_deficit, m_deficit


def _fetch_tried(pairs: list[tuple[str, str]], supa) -> dict[tuple[str, str], set[str]]:
    """Retourne {(ticker, ym): set_of_apis} depuis articles.tried_sources."""
    if not pairs:
        return {}
    tickers = list({t for t, _ in pairs})
    yms = list({ym for _, ym in pairs})
    rows_data = _in_ticker_chunks(supa, "articles", "ticker, year_month, tried_sources", tickers,
                                   lambda q: q.in_("year_month", yms))
    result: dict[tuple[str, str], set[str]] = {}
    for row in rows_data:
        key = (row["ticker"], row["year_month"])
        result[key] = set(row.get("tried_sources") or [])
    return result


_P2_ORDER = ["finnhub", "newsapi", "marketaux", "av"]

def _mark_tried(ticker: str, ym: str, api: str, p2_done: dict, supa) -> None:
    """Marque (ticker, ym) comme traité par api dans DB et dans le dict local."""
    current = p2_done.setdefault((ticker, ym), set())
    current.add(api)
    ordered = [s for s in _P2_ORDER if s in current] + [s for s in current if s not in _P2_ORDER]
    resp = supa.table("articles").update({"tried_sources": ordered}) \
        .eq("ticker", ticker).eq("year_month", ym).execute()
    if not resp.data:
        supa.table("articles").insert({
            "ticker": ticker, "year_month": ym,
            "articles": [], "sources": [], "tried_sources": ordered,
        }).execute()
    # P2 a potentiellement ajouté des articles : invalider les sentinels bub_cache de ce mois
    # pour que les dots soient retentés avec les nouveaux articles.
    supa.table("bub_cache").delete() \
        .eq("ticker", ticker) \
        .gte("date", f"{ym}-01") \
        .lte("date", f"{ym}-31") \
        .in_("bub_en", ["", "NO_ARTICLES"]) \
        .execute()


def _scrap_phase(tickers: list[str], supa, company_names: dict[str, str], lang: str,
                 region: str = "", force_regen: bool = False, skip_p1: bool = False, skip_p2: bool = False,
                 skip_rss_yahoo: bool = False, force_pairs: set[tuple[str, str]] | None = None) -> dict:
    """Phase 1 (RSS + Yahoo + Finnhub) → gate qty → Phase 2 (payantes) → post-scrap W/M.

    force_pairs : (ticker, ym) dont la fraîcheur est ignorée — toujours inclus même si cached_at < 3j.
    """
    tickers_set = set(tickers)
    force_pairs = force_pairs or set()
    now_utc = datetime.now(timezone.utc)
    today_ym = now_utc.strftime("%Y-%m")
    sliding = _sliding_weeks(n=5)
    months_6 = _last_n_months(6)

    def _build_targets(months_back: int) -> list[tuple[str, str]]:
        """Retourne les (ticker, ym) non-frais en ordre de priorité.

        Utilise une seule requête (ticker+year_month+cached_at uniquement, sans
        JSONB articles) pour éviter le statement timeout. Le compte exact d'articles
        est re-vérifié par _is_month_fresh dans chaque loop de scrape.
        force_tickers : bypass fraîcheur — toujours inclus même si cached_at < 3j.
        """
        yms = _last_n_months(months_back)
        today_ym_str = now_utc.strftime("%Y-%m")

        fresh: set[tuple[str, str]] = set()
        rows_data = _in_ticker_chunks(supa, "articles", "ticker, year_month, cached_at", list(tickers_set),
                                       lambda q: q.in_("year_month", yms))
        for row in rows_data:
            t = row.get("ticker", "")
            ym = row.get("year_month", "")
            cached = row.get("cached_at", "")
            if not cached:
                continue
            if (t, ym) in force_pairs:
                continue  # bypass freshness pour ces (ticker, ym)
            try:
                dt = datetime.fromisoformat(cached.replace("Z", "+00:00"))
                if (now_utc - dt).days < 3:
                    fresh.add((t, ym))
            except Exception:
                pass

        p1, p2, p3, p4 = [], [], [], []
        for t in tickers:
            r = _detect_region(t)
            for ym in yms:
                if (t, ym) in fresh:
                    continue
                if r in ("EU", "ASIA"):
                    (p1 if ym == today_ym_str else p2).append((t, ym))
                else:
                    (p3 if ym == today_ym_str else p4).append((t, ym))
        return p1 + p2 + p3 + p4

    total_added = 0
    rss_zero: set[tuple[str, str]] = set()
    budget_skipped_pairs: dict[tuple[str, str], set[str]] = {}

    targets_12 = _build_targets(12)

    if skip_p1:
        logger.info("scrap_phase --skip-p1 : RSS + Yahoo + Finnhub ignorés")
        rss_arts = yahoo_arts = finn_arts = 0
    elif skip_rss_yahoo:
        logger.info("scrap_phase --skip-rss-yahoo : RSS + Yahoo ignorés, Finnhub maintenu")
        rss_arts = yahoo_arts = 0

    if not skip_p1 and not skip_rss_yahoo:
        # ── Phase 1 : RSS (12m, toutes régions) ──────────────────────────────────
        logger.info(f"scrap_phase RSS: {len(targets_12)} cibles")
        rss_updated, rss_arts = 0, 0
        rss_start = time.time()
        rss_total = len(targets_12)
        rss_notify_at = {max(1, rss_total * p // 5) for p in range(1, 5)}
        for i, (ticker, ym) in enumerate(targets_12):
            if i > 0:
                time.sleep(0.5)
            logger.info(f"  RSS {ticker} {ym}")
            query = _build_rss_query(ticker, company_names.get(ticker, ""))
            raw = _fetch_rss_month(query, ym)
            if not raw:
                rss_zero.add((ticker, ym))
            else:
                added, _ = _upsert_articles_merge(ticker, ym, [_normalize_google_rss(a) for a in raw], "google_rss", supa)
                if added > 0:
                    rss_updated += 1
                    rss_arts += added
                else:
                    rss_zero.add((ticker, ym))
                total_added += added
            pos = i + 1
            if pos in rss_notify_at:
                elapsed = time.time() - rss_start
                rate = pos / elapsed if elapsed > 0 else 1
                eta = int((rss_total - pos) / rate)
                em, es = divmod(int(elapsed), 60)
                rm, rs = divmod(eta, 60)
                logger.info(f"  RSS [{pos}/{rss_total}] · +{rss_arts} arts · ~{rm}m{rs:02d}s restant")
                _telegram(
                    f"📡 *RSS {pos * 100 // rss_total}% — {region} {lang.upper()}*\n"
                    f"🎯 {pos}/{rss_total} (ticker, ym)\n"
                    f"⏱ {em}m{es:02d}s · ~{rm}m{rs:02d}s restant\n"
                    f"📰 +{rss_arts} articles"
                )
        logger.info(f"  RSS done: {rss_updated} tickers maj · +{rss_arts} articles · {len(rss_zero)} sans résultat")

        # ── Phase 1 : Yahoo (mois courant, toutes régions) ────────────────────────
        yahoo_tickers = sorted({t for t, ym in targets_12 if ym == today_ym})
        logger.info(f"scrap_phase Yahoo: {len(yahoo_tickers)} tickers")
        yahoo_updated, yahoo_arts = 0, 0
        yahoo_total = len(yahoo_tickers)
        yahoo_notify_at = {max(1, yahoo_total * p // 5) for p in range(1, 5)}
        yahoo_start = time.time()
        for yahoo_idx, ticker in enumerate(yahoo_tickers, 1):
            try:
                articles = _fetch_yahoo_month(ticker, name_search=True, company_name=company_names.get(ticker, ""))
                if not articles:
                    pass
                else:
                    added, _ = _upsert_articles_merge(ticker, today_ym, articles, "yahoo", supa)
                    if added > 0:
                        yahoo_updated += 1
                        yahoo_arts += added
                    total_added += added
            except Exception as e:
                logger.warning(f"  Yahoo {ticker}: {e}")
            if yahoo_idx in yahoo_notify_at:
                elapsed = time.time() - yahoo_start
                rate = yahoo_idx / elapsed if elapsed > 0 else 1
                eta = int((yahoo_total - yahoo_idx) / rate)
                em, es = divmod(int(elapsed), 60)
                rm, rs = divmod(eta, 60)
                _telegram(
                    f"📰 *Yahoo {yahoo_idx * 100 // yahoo_total}% — {region} {lang.upper()}*\n"
                    f"🎯 {yahoo_idx}/{yahoo_total} tickers\n"
                    f"⏱ {em}m{es:02d}s · ~{rm}m{rs:02d}s restant\n"
                    f"📰 +{yahoo_arts} articles"
                )
        logger.info(f"  Yahoo done: {yahoo_updated} tickers maj · +{yahoo_arts} articles")

    # ── Phase 1 : Finnhub (12m, US uniquement) ────────────────────────────────
    if not skip_p1:
        us_targets_all = [(t, ym) for t, ym in targets_12 if _detect_region(t) == "US"]
        finn_tried = _fetch_tried(us_targets_all, supa)
        us_targets = [(t, ym) for t, ym in us_targets_all if "finnhub" not in finn_tried.get((t, ym), set())]
        skipped_finn = len(us_targets_all) - len(us_targets)
        logger.info(f"scrap_phase Finnhub: {len(us_targets)} cibles US ({skipped_finn} déjà taggés skippés)")
        finn_updated, finn_arts = 0, 0
        finn_consec_429 = 0
        finn_total = len(us_targets)
        finn_notify_at = {max(1, finn_total * p // 5) for p in range(1, 5)}
        finn_start = time.time()
        for finn_idx, (ticker, ym) in enumerate(us_targets, 1):
            if finn_idx > 1:
                time.sleep(1.0)
            try:
                raw = _fetch_finnhub_month(ticker, ym)
                if raw is None:
                    finn_consec_429 += 1
                    if finn_consec_429 >= 3:
                        logger.warning(f"  Finnhub: 3 x 429 consécutives — abandon ({finn_idx} req)")
                        break
                    continue
                finn_consec_429 = 0
                _mark_tried(ticker, ym, "finnhub", finn_tried, supa)
                if not raw:
                    pass
                else:
                    added, _ = _upsert_articles_merge(ticker, ym, raw, "finnhub", supa)
                    if added > 0:
                        finn_updated += 1
                        finn_arts += added
                    total_added += added
            except Exception as e:
                logger.warning(f"  Finnhub {ticker} {ym}: {e}")
            if finn_idx in finn_notify_at:
                elapsed = time.time() - finn_start
                rate = finn_idx / elapsed if elapsed > 0 else 1
                eta = int((finn_total - finn_idx) / rate)
                em, es = divmod(int(elapsed), 60)
                rm, rs = divmod(eta, 60)
                _telegram(
                    f"🇺🇸 *Finnhub {finn_idx * 100 // finn_total}% — {region} {lang.upper()}*\n"
                    f"🎯 {finn_idx}/{finn_total} (ticker, ym)\n"
                    f"⏱ {em}m{es:02d}s · ~{rm}m{rs:02d}s restant\n"
                    f"📰 +{finn_arts} articles"
                )
        logger.info(f"  Finnhub done: {finn_updated} tickers maj · +{finn_arts} articles")

    # ── Gate qty P1 → P2 ─────────────────────────────────────────────────────────
    current_wnum = _week_num(now_utc.day)
    if force_pairs:
        # force_pairs = paires déjà identifiées comme bloquées (retry_skipped) → gate redondante
        targets_p2_w = list(force_pairs)
        logger.info(f"scrap_phase P2 W-gate: bypassed ({len(targets_p2_w)} force_pairs)")
    else:
        targets_p2_w, _ = _build_qty_gate(targets_12, today_ym, current_wnum, supa)
        logger.info(f"scrap_phase P2 W-gate: {len(targets_p2_w)} cibles < {MIN_W_SCRAP} arts/semaine")
    _telegram(
        f"📡 *Phase 1 terminée — {region} {lang.upper()}*\n"
        f"📰 RSS +{rss_arts} · Yahoo +{yahoo_arts}"
        + (f" · Finnhub +{finn_arts}" if finn_arts else "")
        + f"\n🔄 {len(targets_p2_w)} (ticker, ym) en W-gate pour P2"
    )

    na_arts = mx_arts = av_arts = 0

    if skip_p2:
        logger.info("scrap_phase --skip-scrape : Phase 2 (NewsAPI + Marketaux + AV) ignorée")
        p2_done = {}
    else:
        p2_done = _fetch_tried(targets_p2_w, supa)

    # ── Phase 2 : NewsAPI + Marketaux + AV ───────────────────────────────────────
    newsapi_key = os.environ.get("NEWSAPI_API_KEY", "") if not skip_p2 else ""
    if newsapi_key:
        cutoff_1m = _last_n_months(1)[-1]
        targets_1m = [(t, ym) for t, ym in targets_p2_w if ym >= cutoff_1m and "newsapi" not in p2_done.get((t, ym), set())]
        skipped_na = len([p for p in targets_p2_w if p[1] >= cutoff_1m]) - len(targets_1m)
        budget = 0
        logger.info(f"scrap_phase NewsAPI: {len(targets_1m)} cibles (budget=100, {skipped_na} déjà taggés skippés)")
        na_updated = 0
        na_consec_429 = 0
        for i, (ticker, ym) in enumerate(targets_1m):
            if budget >= 100:
                for t2, ym2 in targets_1m[i:]:
                    budget_skipped_pairs.setdefault((t2, ym2), set()).add("newsapi")
                logger.info("  Budget NewsAPI épuisé")
                break
            if budget > 0:
                time.sleep(1.0)
            logger.info(f"  NewsAPI {ticker} {ym}")
            query = _build_rss_query(ticker, company_names.get(ticker, ""))
            raw = _fetch_newsapi_month(query, ym, newsapi_key)
            budget += 1
            if raw is None:
                na_consec_429 += 1
                if na_consec_429 >= 3:
                    logger.warning(f"  NewsAPI: 3 x 429 consécutives — abandon ({budget} req)")
                    break
                continue
            na_consec_429 = 0
            _mark_tried(ticker, ym, "newsapi", p2_done, supa)
            if not raw:
                continue
            normalized = [_normalize_newsapi(a) for a in raw if a.get("title") and a.get("url")]
            added, _ = _upsert_articles_merge(ticker, ym, normalized, "newsapi", supa)
            if added > 0:
                na_updated += 1
                na_arts += added
            total_added += added
        logger.info(f"  NewsAPI: {na_updated} tickers · +{na_arts} articles ({budget} req)")

    # ── Phase 2 : Marketaux (3m, budget 100) ─────────────────────────────────────
    mx_token = os.environ.get("MARKETAUX_API_TOKEN", "") if not skip_p2 else ""
    if mx_token:
        cutoff_3m = _last_n_months(3)[-1]
        targets_3m = [(t, ym) for t, ym in targets_p2_w if ym >= cutoff_3m and "marketaux" not in p2_done.get((t, ym), set())]
        skipped_mx = len([p for p in targets_p2_w if p[1] >= cutoff_3m]) - len(targets_3m)
        budget = 0
        logger.info(f"scrap_phase Marketaux: {len(targets_3m)} cibles (budget=100, {skipped_mx} déjà taggés skippés)")
        mx_updated = 0
        mx_consec_429 = 0
        for i, (ticker, ym) in enumerate(targets_3m):
            if budget >= 100:
                for t2, ym2 in targets_3m[i:]:
                    budget_skipped_pairs.setdefault((t2, ym2), set()).add("marketaux")
                logger.info("  Budget Marketaux épuisé")
                break
            if budget > 0:
                time.sleep(1.0)
            logger.info(f"  Marketaux {ticker} {ym}")
            query = _build_rss_query(ticker, company_names.get(ticker, ""))
            raw = _fetch_marketaux_month(query, ym, mx_token)
            budget += 1
            if raw is None:
                mx_consec_429 += 1
                if mx_consec_429 >= 3:
                    logger.warning(f"  Marketaux: 3 x 429 consécutives — abandon ({budget} req)")
                    break
                continue
            mx_consec_429 = 0
            _mark_tried(ticker, ym, "marketaux", p2_done, supa)
            if not raw:
                continue
            normalized = [_normalize_marketaux(a) for a in raw if a.get("title") and a.get("url")]
            added, _ = _upsert_articles_merge(ticker, ym, normalized, "marketaux", supa)
            if added > 0:
                mx_updated += 1
                mx_arts += added
            total_added += added
        logger.info(f"  Marketaux: {mx_updated} tickers · +{mx_arts} articles ({budget} req)")

    # ── Gate qty M (après NewsAPI + Marketaux) → AV ──────────────────────────────
    _, targets_p2_m = _build_qty_gate(targets_p2_w, today_ym, current_wnum, supa) if not skip_p2 else ([], [])
    if not skip_p2:
        logger.info(f"scrap_phase AV M-gate: {len(targets_p2_m)} cibles < {MIN_M_SCRAP} arts/mois")

    # ── Phase 2 : Alpha Vantage (12m, budget 25, 1 call/ticker) ─────────────────
    # 1 call par ticker (pas par mois) : time_from/time_to sur 12m, split par year_month en mémoire.
    # Budget 25 req/j → 25 tickers, vs N_mois × N_tickers avant.
    av_key = os.environ.get("ALPHAVANTAGE_API_KEY", "") if not skip_p2 else ""
    if av_key:
        # Tickers US avec au moins un (ticker, ym) non-tagué AV dans les cibles M
        av_tickers_all = sorted({t for t, ym in targets_p2_m if _detect_region(t) == "US"})
        av_tickers = [t for t in av_tickers_all
                      if any("av" not in p2_done.get((t, ym), set()) for _, ym in targets_p2_m if _ == t)]
        skipped_av = len(av_tickers_all) - len(av_tickers)
        budget = 0
        logger.info(f"scrap_phase AV: {len(av_tickers)} tickers (budget=25, {skipped_av} déjà taggés skippés)")
        av_updated = 0
        av_consec_429 = 0

        # Fenêtre 12 mois
        oldest_ym = _last_n_months(12)[-1]
        oldest_year, oldest_month = int(oldest_ym[:4]), int(oldest_ym[5:7])
        now_utc_av = datetime.now(timezone.utc)
        time_from = f"{oldest_year}{oldest_month:02d}01T0000"
        time_to   = f"{now_utc_av.year}{now_utc_av.month:02d}{now_utc_av.day:02d}T2359"
        av_yms    = set(_last_n_months(12))

        for i, ticker in enumerate(av_tickers):
            if budget >= 25:
                for t2 in av_tickers[i:]:
                    for t3, ym3 in targets_p2_m:
                        if t3 == t2:
                            budget_skipped_pairs.setdefault((t2, ym3), set()).add("av")
                logger.info("  Budget AV épuisé")
                break
            if budget > 0:
                time.sleep(12.0)
            logger.info(f"  AV {ticker} (12m)")
            try:
                r = httpx.get(
                    "https://www.alphavantage.co/query",
                    params={"function": "NEWS_SENTIMENT", "tickers": ticker,
                            "time_from": time_from, "time_to": time_to,
                            "limit": 1000, "apikey": av_key},
                    timeout=30,
                )
                if r.status_code == 429:
                    logger.warning(f"  AV {ticker}: 429 rate limit")
                    av_consec_429 += 1
                    if av_consec_429 >= 2:
                        logger.warning(f"  AV: 2 x rate limit consécutives — abandon ({budget} req)")
                        break
                    budget += 1
                    continue
                r.raise_for_status()
                data = r.json()
                if "Information" in data:
                    logger.warning(f"  AV rate limit: {data['Information'][:80]}")
                    av_consec_429 += 1
                    budget += 1
                    if av_consec_429 >= 2:
                        break
                    continue
                feed = data.get("feed") or []
            except Exception as e:
                logger.warning(f"  AV {ticker}: {e}")
                budget += 1
                continue
            av_consec_429 = 0
            budget += 1

            # Grouper par year_month et upsert chaque mois séparément
            by_ym: dict[str, list[dict]] = defaultdict(list)
            for a in feed:
                raw_time = a.get("time_published", "")
                if raw_time and len(raw_time) >= 6:
                    ym_art = f"{raw_time[:4]}-{raw_time[4:6]}"
                    if ym_art in av_yms:
                        by_ym[ym_art].append(a)

            ticker_added = 0
            for ym, arts in by_ym.items():
                if "av" in p2_done.get((ticker, ym), set()):
                    continue
                normalized = [_normalize_av(a) for a in arts if a.get("title") and a.get("url")]
                if not normalized:
                    _mark_tried(ticker, ym, "av", p2_done, supa)
                    continue
                added, _ = _upsert_articles_merge(ticker, ym, normalized, "alphavantage", supa)
                _mark_tried(ticker, ym, "av", p2_done, supa)
                ticker_added += added
                total_added += added
            if ticker_added > 0:
                av_updated += 1
                av_arts += ticker_added
                logger.info(f"  AV {ticker}: +{ticker_added} articles ({len(by_ym)} mois)")
        logger.info(f"  AV: {av_updated} tickers · +{av_arts} articles ({budget} req)")

    # ── Calcul scrap_budget ────────────────────────────────────────────────────────
    active_paid: set[str] = set()
    if newsapi_key: active_paid.add("newsapi")
    if mx_token:    active_paid.add("marketaux")
    if av_key:      active_paid.add("av")

    scrap_budget: set[tuple[str, str]] = set()
    if active_paid:
        for pair, skipped in budget_skipped_pairs.items():
            if skipped >= active_paid:
                scrap_budget.add(pair)

    if scrap_budget:
        logger.info(f"  scrap_budget: {len(scrap_budget)} (ticker, ym) — toutes sources payantes épuisées")

    if newsapi_key or mx_token or av_key:
        p2_lines = []
        if newsapi_key: p2_lines.append(f"NewsAPI +{na_arts}")
        if mx_token:    p2_lines.append(f"Marketaux +{mx_arts}")
        if av_key:      p2_lines.append(f"AV +{av_arts}")
        sb_line = f"\n⚠ scrap_budget: {len(scrap_budget)} (ticker, ym)" if scrap_budget else ""
        _telegram(
            f"💳 *Phase 2 terminée — {region} {lang.upper()}*\n"
            f"📰 {' · '.join(p2_lines)}{sb_line}"
        )

    missed = [(t, ym) for (t, ym) in rss_zero if (t, ym) in scrap_budget]
    if missed:
        cp = Path(__file__).parent / "scrap_missed_checkpoint.json"
        cp.write_text(json.dumps({"date": date.today().isoformat(), "missed": missed}, indent=2))
        logger.info(f"  scrap checkpoint: {len(missed)} (ticker, ym) sans couverture → scrap_missed_checkpoint.json")

    # ── Post-scrap par ticker : DUMB → W (5 semaines) → M ────────────────────────
    from services.overview_service import gen_dumb

    total_tickers = len(tickers)

    # Compte W/M pending avant de démarrer
    _sliding_yms = list({ym for ym, _ in sliding})
    _w_done_rows = _in_ticker_chunks(supa, "breaks_weekly", "ticker,year_month,week_num", tickers,
                                      lambda q: q.in_("year_month", _sliding_yms).in_("status", ["done_more_10", "done_less_10"]))
    _w_done = {(r["ticker"], r["year_month"], r["week_num"]) for r in _w_done_rows}
    _w_total = len(tickers) * len(sliding)
    _w_pending = _w_total - len(_w_done)

    _w_less10_rows = _in_ticker_chunks(supa, "breaks_weekly", "ticker,year_month,week_num", tickers,
                                        lambda q: q.in_("year_month", _sliding_yms).eq("status", "less_10_articles"))
    _w_blocked = len(_w_less10_rows)
    _w_generable = _w_pending - _w_blocked

    _m_col = _summary_col("breaks_monthly")
    _m_done_rows = _in_ticker_chunks(supa, "breaks_monthly", "ticker,year_month", tickers,
                                      lambda q: q.in_("year_month", months_6).not_.is_(_m_col, "null"))
    _m_done = {(r["ticker"], r["year_month"]) for r in _m_done_rows}
    _m_total = len(tickers) * len(months_6)
    _m_pending = _m_total - len(_m_done)

    logger.info(
        f"scrap_phase post-scrap: {total_tickers} tickers · "
        f"W pending: {_w_pending}/{_w_total} ({_w_generable} générables · {_w_blocked} bloques <10 arts) · "
        f"M pending: {_m_pending}/{_m_total}"
    )
    total_w_gen = 0
    total_m_gen = 0
    all_w_sources: list[str] = []
    all_m_sources: list[str] = []
    post_scrap_start = time.time()
    notify_at = {max(1, total_tickers * p // 5) for p in range(1, 5)}
    results_lock = threading.Lock()
    completed_count = 0

    def _retry_call(fn, *args, retries: int = 5, **kwargs):
        """Retry un appel de service sur disconnect Supabase — même backoff que _exec."""
        delay = 3.0
        for attempt in range(retries + 1):
            try:
                return fn(*args, **kwargs)
            except (httpx.RemoteProtocolError, httpx.ReadError, httpx.ConnectError) as e:
                if attempt == retries:
                    raise
                logger.warning(f"  Supabase transient error (attempt {attempt + 1}/{retries}): {e} — retry in {delay:.0f}s")
                time.sleep(delay)
                delay *= 3

    def _process_ticker(ticker: str, start_delay: float = 0.0) -> tuple[int, int, list[str], list[str]]:
        if start_delay:
            time.sleep(start_delay)
        w_gen, m_gen, n_dumb = 0, 0, 0
        w_sources: list[str] = []
        wid = threading.current_thread().name.split("_")[-1]
        logger.info(f"  → {ticker} [W{wid}]")

        # 1. DUMB
        try:
            n_dumb = _retry_call(gen_dumb, ticker, lang, cascade="promo")
            if n_dumb:
                logger.info(f"  DUMB {ticker}: {n_dumb} arts [W{wid}]")
        except Exception as e:
            logger.warning(f"  gen_dumb {ticker}: {e}")

        # 2. Détection skew sur les 3 derniers mois
        is_skewed, cluster_wnum = _detect_finn_skew(ticker, months_6[:3], supa)
        t_region = _detect_region(ticker)
        if is_skewed:
            logger.info(f"  ⚠ {ticker}: skew W{cluster_wnum} détecté (US={t_region == 'US'})")

        # 3. W sur 5 semaines glissantes
        for ym, wnum in sliding:
            if not force_regen:
                exists = _exec(supa.table("breaks_weekly").select("status")
                    .eq("ticker", ticker).eq("year_month", ym).eq("week_num", wnum).limit(1))
                if exists.data and exists.data[0].get("status") in ("done_more_10", "done_less_10"):
                    continue

            row = _exec(supa.table("articles").select("articles")
                .eq("ticker", ticker).eq("year_month", ym).limit(1))
            if not row.data:
                continue
            all_arts = row.data[0].get("articles") or []
            week_arts = [
                a for a in all_arts
                if _week_num(datetime.fromtimestamp(a.get("datetime", 0), tz=timezone.utc).day) == wnum
                and datetime.fromtimestamp(a.get("datetime", 0), tz=timezone.utc).strftime("%Y-%m") == ym
            ]

            if t_region == "US" and is_skewed and wnum == cluster_wnum:
                week_no_finn = [a for a in week_arts if a.get("source", "").lower() not in ("finnhub.io", "finnhub")]
                logger.info(
                    f"  ⚠ W {ticker} {ym} W{wnum}: skew — "
                    f"{len(week_arts)} arts total, {len(week_no_finn)} sans Finn"
                )
                week_arts = week_no_finn

            pair = (ticker, ym)
            if len(week_arts) < MIN_W_SCRAP:
                status = "scrap_budget" if pair in scrap_budget else "less_10_articles"
                if status == "scrap_budget":
                    logger.info(f"  ⏭ W {ticker} {ym} W{wnum} — scrap_budget")
                _exec(supa.table("breaks_weekly").upsert({
                    "ticker": ticker, "year_month": ym, "week_num": wnum,
                    "status": status, "article_count": len(week_arts),
                }))
                continue

            src = _gen_weekly(ticker, ym, wnum, week_arts, supa)
            if isinstance(src, str):
                w_gen += 1
                w_sources.append(src)

        # 4. M sur 6 mois
        m_sources: list[str] = []
        for ym in months_6:
            if (ticker, ym) in scrap_budget:
                logger.info(f"  ⏭ M {ticker} {ym} — scrap_budget")
                continue
            try:
                src = _retry_call(scrap_gen_monthly, ticker, ym, supa, caller="refresh_scrap", cascade="bub_batch")
                if src:
                    m_gen += 1
                    if isinstance(src, str):
                        m_sources.append(src)
            except Exception as e:
                logger.warning(f"  M scrap {ticker} {ym}: {e}")

        if w_gen == 0 and m_gen == 0 and not n_dumb:
            logger.info(f"  ← {ticker} [W{wid}]: skip (arts insuffisants)")
        return w_gen, m_gen, w_sources, m_sources

    with ThreadPoolExecutor(max_workers=15) as ex:
        futs = {ex.submit(_process_ticker, t, i * 0.3): t for i, t in enumerate(tickers)}
        for fut in as_completed(futs):
            try:
                w, m, srcs, m_srcs = fut.result()
            except Exception as e:
                logger.warning(f"  post-scrap error {futs[fut]}: {e}")
                w, m, srcs, m_srcs = 0, 0, [], []
            with results_lock:
                total_w_gen += w
                total_m_gen += m
                all_w_sources.extend(srcs)
                all_m_sources.extend(m_srcs)
                completed_count += 1
                done = completed_count
            if done in notify_at:
                elapsed = int(time.time() - post_scrap_start)
                eta = int(elapsed * (total_tickers - done) / done) if done else 0
                em, es = divmod(elapsed, 60)
                rm, rs = divmod(eta, 60)
                _telegram(
                    f"📊 *Post-scrap {done * 100 // total_tickers}% — {region} {lang.upper()}*\n"
                    f"🎯 {done}/{total_tickers} tickers\n"
                    f"⏱ {em}m{es:02d}s · ~{rm}m{rs:02d}s restant\n"
                    f"📅 W: {total_w_gen} [{_fmt_sources(all_w_sources)}]\n"
                    f"📆 M: {total_m_gen} [{_fmt_sources(all_m_sources)}]"
                )

    logger.info(
        f"scrap_phase done: +{total_added} arts · "
        f"{total_w_gen}W [{_fmt_sources(all_w_sources)}] · {total_m_gen}M [{_fmt_sources(all_m_sources)}]"
    )
    return {
        "total_added":       total_added,
        "total_w_gen":       total_w_gen,
        "total_m_gen":       total_m_gen,
        "scrap_budget_count": len(scrap_budget),
        "all_w_sources":     all_w_sources,
        "all_m_sources":     all_m_sources,
    }


_SCRAP_MISSED_CP = Path(__file__).parent / "scrap_missed_checkpoint.json"


def _scrap_retry_missed(supa, company_names: dict[str, str], lang: str) -> int:
    """Reprocess les (ticker, ym) du checkpoint scrap_missed — APIs payantes uniquement."""
    if not _SCRAP_MISSED_CP.exists():
        logger.info("scrap_retry: aucun checkpoint trouvé")
        return 0

    data = json.loads(_SCRAP_MISSED_CP.read_text())
    missed_raw = data.get("missed", [])
    if not missed_raw:
        logger.info("scrap_retry: checkpoint vide")
        return 0

    missed: list[tuple[str, str]] = [tuple(x) for x in missed_raw]  # type: ignore[misc]
    logger.info(f"scrap_retry: {len(missed)} (ticker, ym) à reprocesser (date checkpoint: {data.get('date')})")

    total_added = 0
    covered: set[tuple[str, str]] = set()

    def _post_scrap(ticker: str, ym: str, added: int) -> None:
        if added <= 0:
            return
        try:
            from services.overview_service import gen_dumb
            gen_dumb(ticker, lang, cascade="promo")
        except Exception as e:
            logger.warning(f"  gen_dumb {ticker}: {e}")
        scrap_gen_weekly(ticker, ym, supa, caller="retry_scrap", cascade="promo")
        scrap_gen_monthly(ticker, ym, supa, caller="retry_scrap", cascade="bub_batch")

    cutoff_1m = _last_n_months(1)[-1]
    cutoff_3m = _last_n_months(3)[-1]

    newsapi_key = os.environ.get("NEWSAPI_API_KEY", "")
    if newsapi_key:
        targets = [(t, ym) for t, ym in missed if ym >= cutoff_1m]
        budget = 0
        na_updated, na_arts = 0, 0
        logger.info(f"scrap_retry NewsAPI: {len(targets)} cibles")
        for i, (ticker, ym) in enumerate(targets):
            if budget >= 100:
                logger.info("  Budget NewsAPI épuisé")
                break
            if budget > 0:
                time.sleep(1.0)
            query = _build_rss_query(ticker, company_names.get(ticker, ""))
            raw = _fetch_newsapi_month(query, ym, newsapi_key)
            budget += 1
            if not raw:
                continue
            normalized = [_normalize_newsapi(a) for a in raw if a.get("title") and a.get("url")]
            added, _ = _upsert_articles_merge(ticker, ym, normalized, "newsapi", supa)
            if added > 0:
                na_updated += 1
                na_arts += added
                covered.add((ticker, ym))
            total_added += added
            _post_scrap(ticker, ym, added)
        logger.info(f"  NewsAPI retry: {na_updated} tickers · +{na_arts} articles ({budget} req)")

    mx_token = os.environ.get("MARKETAUX_API_TOKEN", "")
    if mx_token:
        targets = [(t, ym) for t, ym in missed if ym >= cutoff_3m]
        budget = 0
        mx_updated, mx_arts = 0, 0
        logger.info(f"scrap_retry Marketaux: {len(targets)} cibles")
        for i, (ticker, ym) in enumerate(targets):
            if budget >= 100:
                logger.info("  Budget Marketaux épuisé")
                break
            if budget > 0:
                time.sleep(1.0)
            query = _build_rss_query(ticker, company_names.get(ticker, ""))
            raw = _fetch_marketaux_month(query, ym, mx_token)
            budget += 1
            if not raw:
                continue
            normalized = [_normalize_marketaux(a) for a in raw if a.get("title") and a.get("url")]
            added, _ = _upsert_articles_merge(ticker, ym, normalized, "marketaux", supa)
            if added > 0:
                mx_updated += 1
                mx_arts += added
                covered.add((ticker, ym))
            total_added += added
            _post_scrap(ticker, ym, added)
        logger.info(f"  Marketaux retry: {mx_updated} tickers · +{mx_arts} articles ({budget} req)")

    av_key = os.environ.get("ALPHAVANTAGE_API_KEY", "")
    if av_key:
        targets = list(missed)
        budget = 0
        av_updated, av_arts = 0, 0
        logger.info(f"scrap_retry AV: {len(targets)} cibles")
        for i, (ticker, ym) in enumerate(targets):
            if budget >= 25:
                logger.info("  Budget AV épuisé")
                break
            if budget > 0:
                time.sleep(12.0)
            raw = _fetch_av_month(ticker, ym, av_key)
            budget += 1
            if not raw:
                continue
            normalized = [_normalize_av(a) for a in raw if a.get("title") and a.get("url")]
            added, _ = _upsert_articles_merge(ticker, ym, normalized, "alphavantage", supa)
            if added > 0:
                av_updated += 1
                av_arts += added
                covered.add((ticker, ym))
            total_added += added
            _post_scrap(ticker, ym, added)
        logger.info(f"  AV retry: {av_updated} tickers · +{av_arts} articles ({budget} req)")

    still_missed = [(t, ym) for (t, ym) in missed if (t, ym) not in covered]
    if still_missed:
        _SCRAP_MISSED_CP.write_text(json.dumps({"date": date.today().isoformat(), "missed": still_missed}, indent=2))
        logger.info(f"  checkpoint mis à jour: {len(still_missed)} encore manquants")
    else:
        _SCRAP_MISSED_CP.unlink(missing_ok=True)
        logger.info("  checkpoint effacé — tous couverts")

    logger.info(f"scrap_retry done: +{total_added} articles")
    return total_added


# ─── Retry skipped ────────────────────────────────────────────────────────────

def _retry_skipped_phase(
    region_tickers: list[str],
    supa,
    company_names: dict[str, str],
    lang: str,
    region: str,
    skip_p1: bool = False,
) -> dict:
    """Re-scrape uniquement les tickers avec W 'less_10_articles' ou M absent (mois courant)."""
    sliding = _sliding_weeks(n=5)
    sliding_yms = list({ym for ym, _ in sliding})
    current_ym = _last_n_months(1)[0]
    m_col = _summary_col("breaks_monthly")

    # W skipped dans la fenêtre glissante
    w_rows_all = _in_ticker_chunks(supa, "breaks_weekly", "ticker,year_month,status", region_tickers,
                                   lambda q: q.in_("year_month", sliding_yms).in_("status", ["less_10_articles", "scrap_budget"]))

    n_budget   = sum(1 for r in w_rows_all if r.get("status") == "scrap_budget")
    n_less10   = sum(1 for r in w_rows_all if r.get("status") == "less_10_articles")
    logger.info(
        f"retry_skipped W trouvés : {len(w_rows_all)} rows "
        f"({n_budget} scrap_budget → à traiter · {n_less10} less_10_articles)"
    )

    w_rows = w_rows_all

    # Exclure les (ticker, ym) anciens déjà passés par tous les scrapers P2 actifs :
    # rien de plus à scraper, le less_10 est définitif pour ces paires.
    active_paid: set[str] = set()
    if os.environ.get("NEWSAPI_API_KEY"):                          active_paid.add("newsapi")
    if os.environ.get("MARKETAUX_API_TOKEN"):                      active_paid.add("marketaux")
    if os.environ.get("ALPHAVANTAGE_API_KEY") and region == "US": active_paid.add("av")

    if active_paid and w_rows:
        candidate_pairs = list({(r["ticker"], r["year_month"]) for r in w_rows})
        p2_done_map = _fetch_tried(candidate_pairs, supa)
        exhausted_pairs = {
            p for p in candidate_pairs
            if active_paid.issubset(p2_done_map.get(p, set()))
        }
        if exhausted_pairs:
            logger.info(f"  retry_skipped W: {len(exhausted_pairs)} (ticker, ym) P2-exhausted ignorés")
        w_rows = [r for r in w_rows if (r["ticker"], r["year_month"]) not in exhausted_pairs]

    tickers_w = {r["ticker"] for r in w_rows}
    # force_pairs W : uniquement les paires non-exhaustées (pas toute la fenêtre glissante)
    w_force_pairs: set[tuple[str, str]] = {(r["ticker"], r["year_month"]) for r in w_rows}

    # M absent pour le mois courant
    m_done = _in_ticker_chunks(supa, "breaks_monthly", "ticker", region_tickers,
                                lambda q: q.eq("year_month", current_ym).not_.is_(m_col, "null"))
    tickers_m_done = {r["ticker"] for r in m_done}
    tickers_m = set(region_tickers) - tickers_m_done

    targets = sorted(tickers_w | tickers_m)
    only_w  = tickers_w - tickers_m
    only_m  = tickers_m - tickers_w
    both    = tickers_w & tickers_m

    logger.info(
        f"retry_skipped: {len(targets)} tickers cibles "
        f"(W less_10 only={len(only_w)} · M manquant only={len(only_m)} · les deux={len(both)})"
    )
    if only_w:
        logger.info(f"  W less_10 only : {' · '.join(sorted(only_w))}")
    if only_m:
        logger.info(f"  M manquant only: {' · '.join(sorted(only_m))}")
    if both:
        logger.info(f"  W+M            : {' · '.join(sorted(both))}")
    _telegram(
        f"🔁 *retry_skipped démarré — {region} {lang.upper()}*\n"
        f"{len(targets)} tickers (W={len(tickers_w)} · M={len(tickers_m)})\n"
        f"W rows: {len(w_rows_all)} ({n_budget} budget · {n_less10} less10)\n"
        + (f"W: {' '.join(sorted(tickers_w))}\n" if tickers_w else "")
        + (f"M: {' '.join(sorted(tickers_m))}" if tickers_m else "")
    )

    if not targets:
        logger.info("retry_skipped: rien à faire")
        return {"total_added": 0, "total_w_gen": 0, "total_m_gen": 0, "scrap_budget_count": 0, "all_w_sources": []}

    # Paires exactes à forcer (bypass freshness) : paires W non-exhaustées + mois courant pour M
    force_pairs: set[tuple[str, str]] = w_force_pairs
    for t in tickers_m:
        force_pairs.add((t, current_ym))

    return _scrap_phase(targets, supa, company_names, lang, region=region, force_pairs=force_pairs, skip_p1=skip_p1)


def _gen_lesser_phase(region_tickers: list[str], supa, lang: str, region: str = "") -> dict:
    """Génère done_less_10 pour les W less_10_articles dont tous les P2 actifs ont tourné — sans scrape."""
    from services.breakers_articles_service import _is_p2_exhausted, MIN_W_SCRAP

    sliding = _sliding_weeks(n=5)
    sliding_yms = list({ym for ym, _ in sliding})

    rows = _in_ticker_chunks(supa, "breaks_weekly", "ticker,year_month,week_num,article_count", region_tickers,
                             lambda q: q.in_("year_month", sliding_yms).eq("status", "less_10_articles"))
    if not rows:
        logger.info("gen_lesser: aucun W less_10_articles dans la fenêtre glissante")
        return {"total_w_gen": 0}

    candidates = [(r["ticker"], r["year_month"], r["week_num"]) for r in rows]
    pairs = list({(t, ym) for t, ym, _ in candidates})
    p2_done_map = _fetch_tried(pairs, supa)
    active_paid = {"newsapi", "marketaux"}
    if os.environ.get("ALPHAVANTAGE_API_KEY") and region == "US":
        active_paid.add("av")

    total_gen, total_skip = 0, 0
    for ticker, ym, wnum in candidates:
        tried = p2_done_map.get((ticker, ym), set())
        if not active_paid.issubset(tried):
            total_skip += 1
            continue

        row = _exec(supa.table("articles").select("articles").eq("ticker", ticker).eq("year_month", ym).limit(1))
        if not row.data:
            total_skip += 1
            continue
        all_arts = row.data[0].get("articles") or []
        week_arts = [
            a for a in all_arts
            if _week_num(datetime.fromtimestamp(a.get("datetime", 0), tz=timezone.utc).day) == wnum
            and datetime.fromtimestamp(a.get("datetime", 0), tz=timezone.utc).strftime("%Y-%m") == ym
        ]
        if not week_arts:
            total_skip += 1
            continue

        logger.info(f"  gen_lesser W {ticker} {ym} W{wnum} ({len(week_arts)} arts)")
        src = _gen_weekly(ticker, ym, wnum, week_arts, supa, done_status="done_less_10")
        if src:
            total_gen += 1
        else:
            total_skip += 1

    logger.info(f"gen_lesser done: {total_gen} générés · {total_skip} skippés")
    return {"total_w_gen": total_gen}


# ─── W rescue ─────────────────────────────────────────────────────────────────

def _w_rescue_phase(region_tickers: list[str], supa, company_names: dict[str, str],
                    lang: str, region: str = "") -> dict:
    """Cascade P2 complète (newsapi→marketaux→AV) sur les W bloqués (<10 arts, 6m) — P1 ignoré.

    Sélection : breaks_weekly.article_count < MIN_W_SCRAP ET status pas done_*.
    Idempotent via tried_sources : chaque source skippée si déjà taggée.
    AV : pattern ticker-level (1 req/ticker, 12m), toutes régions (pas US-only).
    """
    months_6 = _last_n_months(6)
    done_statuses = ["done_more_10", "done_less_10"]

    blocked_rows = _in_ticker_chunks(
        supa, "breaks_weekly", "ticker,year_month",
        region_tickers,
        lambda q: q.in_("year_month", months_6)
                   .lt("article_count", MIN_W_SCRAP)
                   .not_.in_("status", done_statuses),
    )
    pairs = list({(r["ticker"], r["year_month"]) for r in blocked_rows})

    if not pairs:
        logger.info("w_rescue: aucun W bloqué (<10 arts) dans la fenêtre 6m")
        return {"total_added": 0, "total_w_gen": 0}

    n_tickers = len({t for t, _ in pairs})
    logger.info(f"w_rescue: {len(pairs)} (ticker, year_month) cibles · {n_tickers} tickers distincts")
    _telegram(
        f"🔧 *w_rescue démarré — {region} {lang.upper()}*\n"
        f"{n_tickers} tickers · {len(pairs)} (ticker, ym)"
    )

    pairs_set = set(pairs)
    p2_done = _fetch_tried(pairs, supa)
    cutoff_1m = _last_n_months(1)[-1]
    cutoff_3m = _last_n_months(3)[-1]

    total_added = 0
    total_w_gen = 0

    def _post_scrap_w(ticker: str, ym: str, added: int) -> None:
        nonlocal total_w_gen
        if added <= 0:
            return
        n = scrap_gen_weekly(ticker, ym, supa, caller="w_rescue", cascade="promo") or 0
        total_w_gen += n

    # ── NewsAPI (1m) ──────────────────────────────────────────────────────────
    newsapi_key = os.environ.get("NEWSAPI_API_KEY", "")
    na_arts = 0
    if newsapi_key:
        targets_na = [(t, ym) for t, ym in pairs
                      if ym >= cutoff_1m and "newsapi" not in p2_done.get((t, ym), set())]
        skipped_na = len([p for p in pairs if p[1] >= cutoff_1m]) - len(targets_na)
        budget = 0
        na_updated = 0
        na_consec_429 = 0
        logger.info(f"w_rescue NewsAPI: {len(targets_na)} cibles (budget=100, {skipped_na} déjà taggés skippés)")
        for ticker, ym in targets_na:
            if budget >= 100:
                logger.info("  Budget NewsAPI épuisé")
                break
            if budget > 0:
                time.sleep(1.0)
            logger.info(f"  NewsAPI {ticker} {ym}")
            query = _build_rss_query(ticker, company_names.get(ticker, ""))
            raw = _fetch_newsapi_month(query, ym, newsapi_key)
            budget += 1
            if raw is None:
                na_consec_429 += 1
                if na_consec_429 >= 3:
                    logger.warning(f"  NewsAPI: 3 x 429 consécutives — abandon ({budget} req)")
                    break
                continue
            na_consec_429 = 0
            _mark_tried(ticker, ym, "newsapi", p2_done, supa)
            if not raw:
                continue
            normalized = [_normalize_newsapi(a) for a in raw if a.get("title") and a.get("url")]
            added, _ = _upsert_articles_merge(ticker, ym, normalized, "newsapi", supa)
            if added > 0:
                na_updated += 1
                na_arts += added
                total_added += added
            _post_scrap_w(ticker, ym, added)
        logger.info(f"  NewsAPI: {na_updated} tickers · +{na_arts} articles ({budget} req)")

    # ── Marketaux (3m) ────────────────────────────────────────────────────────
    mx_token = os.environ.get("MARKETAUX_API_TOKEN", "")
    mx_arts = 0
    if mx_token:
        targets_mx = [(t, ym) for t, ym in pairs
                      if ym >= cutoff_3m and "marketaux" not in p2_done.get((t, ym), set())]
        skipped_mx = len([p for p in pairs if p[1] >= cutoff_3m]) - len(targets_mx)
        budget = 0
        mx_updated = 0
        mx_consec_429 = 0
        logger.info(f"w_rescue Marketaux: {len(targets_mx)} cibles (budget=100, {skipped_mx} déjà taggés skippés)")
        for ticker, ym in targets_mx:
            if budget >= 100:
                logger.info("  Budget Marketaux épuisé")
                break
            if budget > 0:
                time.sleep(1.0)
            logger.info(f"  Marketaux {ticker} {ym}")
            query = _build_rss_query(ticker, company_names.get(ticker, ""))
            raw = _fetch_marketaux_month(query, ym, mx_token)
            budget += 1
            if raw is None:
                mx_consec_429 += 1
                if mx_consec_429 >= 3:
                    logger.warning(f"  Marketaux: 3 x 429 consécutives — abandon ({budget} req)")
                    break
                continue
            mx_consec_429 = 0
            _mark_tried(ticker, ym, "marketaux", p2_done, supa)
            if not raw:
                continue
            normalized = [_normalize_marketaux(a) for a in raw if a.get("title") and a.get("url")]
            added, _ = _upsert_articles_merge(ticker, ym, normalized, "marketaux", supa)
            if added > 0:
                mx_updated += 1
                mx_arts += added
                total_added += added
            _post_scrap_w(ticker, ym, added)
        logger.info(f"  Marketaux: {mx_updated} tickers · +{mx_arts} articles ({budget} req)")

    # ── AV (12m, ticker-level, toutes régions) ────────────────────────────────
    av_key = os.environ.get("ALPHAVANTAGE_API_KEY", "")
    av_arts = 0
    if av_key:
        av_tickers = sorted({t for t, ym in pairs
                             if _detect_region(t) == "US"
                             and "av" not in p2_done.get((t, ym), set())})
        budget = 0
        av_updated = 0
        av_consec_429 = 0
        logger.info(f"w_rescue AV: {len(av_tickers)} tickers (budget=25)")

        oldest_ym = _last_n_months(12)[-1]
        oldest_year, oldest_month = int(oldest_ym[:4]), int(oldest_ym[5:7])
        now_utc_av = datetime.now(timezone.utc)
        time_from = f"{oldest_year}{oldest_month:02d}01T0000"
        time_to   = f"{now_utc_av.year}{now_utc_av.month:02d}{now_utc_av.day:02d}T2359"
        av_yms    = set(_last_n_months(12))

        for ticker in av_tickers:
            if budget >= 25:
                logger.info("  Budget AV épuisé")
                break
            if budget > 0:
                time.sleep(12.0)
            logger.info(f"  AV {ticker} (12m)")
            try:
                r = httpx.get(
                    "https://www.alphavantage.co/query",
                    params={"function": "NEWS_SENTIMENT", "tickers": ticker,
                            "time_from": time_from, "time_to": time_to,
                            "limit": 1000, "apikey": av_key},
                    timeout=30,
                )
                if r.status_code == 429:
                    logger.warning(f"  AV {ticker}: 429 rate limit")
                    av_consec_429 += 1
                    if av_consec_429 >= 2:
                        logger.warning(f"  AV: 2 x rate limit consécutives — abandon ({budget} req)")
                        break
                    budget += 1
                    continue
                r.raise_for_status()
                data = r.json()
                if "Information" in data:
                    logger.warning(f"  AV rate limit: {data['Information'][:80]}")
                    av_consec_429 += 1
                    budget += 1
                    if av_consec_429 >= 2:
                        break
                    continue
                feed = data.get("feed") or []
            except Exception as e:
                logger.warning(f"  AV {ticker}: {e}")
                budget += 1
                continue
            av_consec_429 = 0
            budget += 1

            by_ym: dict[str, list[dict]] = defaultdict(list)
            for a in feed:
                raw_time = a.get("time_published", "")
                if raw_time and len(raw_time) >= 6:
                    ym_art = f"{raw_time[:4]}-{raw_time[4:6]}"
                    if ym_art in av_yms:
                        by_ym[ym_art].append(a)

            ticker_added = 0
            for ym, arts in by_ym.items():
                if (ticker, ym) not in pairs_set:
                    continue  # hors scope W-rescue
                if "av" in p2_done.get((ticker, ym), set()):
                    continue
                normalized = [_normalize_av(a) for a in arts if a.get("title") and a.get("url")]
                if not normalized:
                    _mark_tried(ticker, ym, "av", p2_done, supa)
                    continue
                added, _ = _upsert_articles_merge(ticker, ym, normalized, "alphavantage", supa)
                _mark_tried(ticker, ym, "av", p2_done, supa)
                ticker_added += added
                total_added += added
                _post_scrap_w(ticker, ym, added)

            if ticker_added > 0:
                av_updated += 1
                av_arts += ticker_added
                logger.info(f"  AV {ticker}: +{ticker_added} articles ({len(by_ym)} mois)")
        logger.info(f"  AV: {av_updated} tickers · +{av_arts} articles ({budget} req)")

    _telegram(
        f"✅ *w_rescue terminé — {region} {lang.upper()}*\n"
        f"📰 NewsAPI +{na_arts} · Marketaux +{mx_arts} · AV +{av_arts}\n"
        f"📅 W générés: {total_w_gen}"
    )
    logger.info(f"w_rescue done: +{total_added} arts · {total_w_gen} W générés")
    return {"total_added": total_added, "total_w_gen": total_w_gen}


# ─── Entry point ──────────────────────────────────────────────────────────────

def _run_single_region(args) -> None:
    """Exécute le run complet pour une région. args.region doit être US/EU/ASIA."""
    os.environ["LLM_LANG"] = args.lang

    from data.tickers import get_by_region
    tickers = [args.ticker.upper()] if args.ticker else get_by_region(args.region)

    supa = _supa()

    # Reset scrap_budget en run normal : recalculé en mémoire, le statut DB doit être nettoyé.
    # En --retry-skipped : on garde le statut pour que _retry_skipped_phase puisse les cibler.
    if not args.retry_skipped:
        for tbl in ("breaks_weekly", "breaks_monthly"):
            supa.table(tbl).update({"status": None}).eq("status", "scrap_budget").execute()
        logger.info("scrap_budget reset en DB (breaks_weekly + breaks_monthly)")

    company_names: dict[str, str] = _bulk_fetch_company_names(tickers)
    missing_names = [t for t in tickers if "." in t and t not in company_names]
    if missing_names:
        missing_path = SCRIPTS_DIR / f"missing_names_{args.region}.txt"
        missing_path.write_text("\n".join(missing_names))
        logger.info(f"  {len(missing_names)} tickers sans nom → {missing_path.name}")

    sliding = _sliding_weeks(n=5)
    months = _last_n_months(n=6)

    # Compte W/M pending pour affichage départ
    _s_yms = list({ym for ym, _ in sliding})
    _w_done_r = _in_ticker_chunks(supa, "breaks_weekly", "ticker,year_month,week_num", tickers,
                                   lambda q: q.in_("year_month", _s_yms).in_("status", ["done_more_10", "done_less_10"]))
    _w_pending_start = len(tickers) * len(sliding) - len(_w_done_r)
    _m_col_s = _summary_col("breaks_monthly")
    _m_done_r = _in_ticker_chunks(supa, "breaks_monthly", "ticker,year_month", tickers,
                                   lambda q: q.in_("year_month", months).not_.is_(_m_col_s, "null"))
    _m_pending_start = len(tickers) * len(months) - len(_m_done_r)

    logger.info(
        f"refresh_breakers — {args.region} {args.lang.upper()} — {len(tickers)} tickers\n"
        f"Fenêtre W: {sliding[-1][0]} W{sliding[-1][1]} → {sliding[0][0]} W{sliding[0][1]}\n"
        f"Fenêtre M: {months[-1]} → {months[0]}\n"
        f"Pending : {_w_pending_start} W · {_m_pending_start} M"
    )
    _telegram(
        f"🔄 *refresh_breakers démarré — {args.region} {args.lang.upper()}*\n"
        f"{len(tickers)} tickers\n"
        f"Fenêtre : {sliding[-1][0]} W{sliding[-1][1]} → {sliding[0][0]} W{sliding[0][1]}\n"
        f"Mois : {months[-1]} → {months[0]}\n"
        f"Pending : {_w_pending_start} W · {_m_pending_start} M"
    )
    start_time = time.time()
    phase_result: dict = {}

    if args.retry_missed:
        total_added = _scrap_retry_missed(supa, company_names, args.lang)
        phase_result = {"total_added": total_added}
    elif args.retry_skipped:
        phase_result = _retry_skipped_phase(tickers, supa, company_names, args.lang, region=args.region, skip_p1=args.skip_p1)
    elif args.gen_lesser:
        phase_result = _gen_lesser_phase(tickers, supa, args.lang, region=args.region)
    elif args.w_rescue:
        phase_result = _w_rescue_phase(tickers, supa, company_names, args.lang, region=args.region)
    else:
        phase_result = _scrap_phase(
            tickers, supa, company_names, args.lang,
            region=args.region, force_regen=args.force_regen,
            skip_p1=args.skip_scrape or args.skip_p1,
            skip_p2=args.skip_scrape,
            skip_rss_yahoo=args.skip_rss_yahoo,
        )

    elapsed = int(time.time() - start_time)
    mins, secs = divmod(elapsed, 60)
    arts      = phase_result.get("total_added", 0)
    w_gen     = phase_result.get("total_w_gen", 0)
    m_gen     = phase_result.get("total_m_gen", 0)
    sb_count  = phase_result.get("scrap_budget_count", 0)
    w_sources = phase_result.get("all_w_sources", [])
    m_sources = phase_result.get("all_m_sources", [])
    sb_line   = f"\n⚠ scrap_budget: {sb_count} (ticker, ym)" if sb_count else ""
    _telegram(
        f"✅ *refresh_breakers terminé — {args.region} {args.lang.upper()}*\n"
        f"⏱ {mins}m{secs:02d}s\n"
        f"📰 +{arts} articles\n"
        f"📅 W: {w_gen} [{_fmt_sources(w_sources)}]\n"
        f"📆 M: {m_gen} [{_fmt_sources(m_sources)}]{sb_line}"
    )
    logger.info(f"Done — {mins}m{secs:02d}s · +{arts} arts · {w_gen}W [{_fmt_sources(w_sources)}] · {m_gen}M [{_fmt_sources(m_sources)}]")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--region", required=True, choices=["US", "EU", "ASIA", "ALL"])
    parser.add_argument("--lang", required=True, choices=["en", "fr"])
    parser.add_argument("--ticker", default=None, help="Ticker unique (debug)")
    parser.add_argument("--force-regen", action="store_true",
                        help="Régénère W/M même s'ils existent déjà en DB")
    parser.add_argument("--skip-scrape", action="store_true",
                        help="Saute tout le scrape articles")
    parser.add_argument("--skip-p1", action="store_true",
                        help="Saute RSS + Yahoo + Finnhub (Phase 1 gratuite) mais garde Phase 2 payante")
    parser.add_argument("--skip-rss-yahoo", action="store_true",
                        help="Saute RSS + Yahoo mais exécute Finnhub (reprendre après interruption Finn)")
    parser.add_argument("--retry-missed", action="store_true",
                        help="Reprocess uniquement les (ticker, ym) du scrap_missed_checkpoint.json")
    parser.add_argument("--retry-skipped", action="store_true",
                        help="Re-scrape tickers avec W less_10_articles ou M absent (mois courant) puis chaîne W→M")
    parser.add_argument("--gen-lesser", action="store_true",
                        help="Génère done_less_10 pour les W less_10_articles dont tous les P2 actifs ont tourné (US: newsapi+marketaux+av · EU/ASIA: newsapi+marketaux) — sans scrape")
    parser.add_argument("--w-rescue", action="store_true",
                        help="Cascade P2 complète (newsapi→marketaux→AV) sur W bloqués <10 arts (6m) — idempotent via tried_sources")
    args = parser.parse_args()

    if args.region == "ALL":
        for region in ["US", "EU", "ASIA"]:
            args.region = region
            _run_single_region(args)
    else:
        _run_single_region(args)



if __name__ == "__main__":
    main()
