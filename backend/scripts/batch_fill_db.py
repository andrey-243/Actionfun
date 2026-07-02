"""
Batch scraper — remplit articles_merged_finn_yahoo pour 717 tickers (US + EU + Asie).

Usage :
  python scripts/batch_fill_db.py --phase scrape          # articles seulement
  python scripts/batch_fill_db.py --phase enrich          # AI news cache
  python scripts/batch_fill_db.py --phase all             # les deux
  python scripts/batch_fill_db.py --phase bubbles         # génère causes BUB manquantes
  python scripts/batch_fill_db.py --phase bubbles --force # régénère toutes les causes BUB
  python scripts/batch_fill_db.py --region US             # région spécifique
  python scripts/batch_fill_db.py --ticker AAPL           # ticker unique

Progress : basé sur l'état Supabase — idempotent, relançable sans état local.
"""

import argparse
import os
import sys
import time
import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timezone, timedelta
from pathlib import Path

# Chemin backend
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parents[1] / ".env")

os.environ["BATCH_MODE"] = "1"

import httpx
import yfinance as yf
from supabase import create_client
from data.tickers import US, EU, ASIA, ALL_TICKERS, get_by_region

SCRIPTS_DIR = Path(__file__).parent
LOG_FILE = SCRIPTS_DIR / "batch_fill_db.log"

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
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

NOTIFY_EVERY = 25  # notif Telegram tous les N tickers


def _telegram(msg: str):
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        return
    # Convertir *bold* → <b>bold</b> et _italic_ → <i>italic</i> pour HTML
    import re
    html = re.sub(r'\*([^*]+)\*', r'<b>\1</b>', msg)
    html = re.sub(r'(?<!\w)_([^_]+)_(?!\w)', r'<i>\1</i>', html)
    html += "\n📍 <i>batch_fill_db</i>"
    try:
        httpx.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": html, "parse_mode": "HTML"},
            timeout=8,
        )
    except Exception:
        pass


def _notify_checkpoint(phase: str, i: int, total: int, region: str,
                        done_batch: list[str], errors_batch: list[str],
                        articles_batch: int, start_time: float,
                        llm_sources: dict[str, int] | None = None,
                        skipped_info: str = "",
                        windows_info: str = ""):
    elapsed = int(time.time() - start_time)
    mins, secs = divmod(elapsed, 60)
    remaining = int((elapsed / i) * (total - i)) if i else 0
    r_mins, r_secs = divmod(remaining, 60)

    err_line = f"❌ Errors: {', '.join(errors_batch)}" if errors_batch else "✅ 0 erreurs"
    art_line = f"📰 Articles: +{articles_batch:,}" if articles_batch else ""
    skip_line = f"⏭ Skipped (no news): {skipped_info}" if skipped_info else ""
    win_line = f"🪟 Fenêtres: {windows_info}" if windows_info else ""

    llm_line = ""
    if llm_sources:
        _ICONS = {"freetheai": "🟢", "hapuppy": "🟡", "agentrouter": "🔵", "groq": "🟠", "cache": "💾", "unknown": "⚪"}
        parts = [f"{_ICONS.get(k, '⚪')} {k}: {v}" for k, v in sorted(llm_sources.items(), key=lambda x: -x[1])]
        llm_line = "🤖 LLM: " + " · ".join(parts)

    msg = (
        f"📊 *Batch {phase.upper()} — {region}*\n"
        f"Checkpoint {i}/{total} ({i*100//total}%)\n"
        f"⏱ {mins}m{secs:02d}s écoulé · ~{r_mins}m{r_secs:02d}s restant\n"
        f"{err_line}\n"
        f"{art_line}\n"
        f"{skip_line}\n"
        f"{win_line}\n"
        f"{llm_line}"
    ).strip()

    _telegram(msg)
    logger.info(f"[NOTIF] {i}/{total} — {len(errors_batch)} erreurs · LLM: {llm_sources}")

FINNHUB_KEY = os.environ["FINNHUB_API_KEY"]
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_KEY"]

# Rate limits
FINNHUB_DELAY = 1.1      # 60 req/min → 1s entre calls
YAHOO_DELAY = 6.0        # 10 req/min safe
FREETHEAI_DELAY = 6.0    # 60s / 10 RPM — on attend la réponse du call (sync) puis 6s


def _supa():
    return create_client(SUPABASE_URL, SUPABASE_KEY)


def _in_chunks(supa, table: str, select: str, tickers: list[str], filter_fn, chunk_size: int = 100) -> list[dict]:
    """Query chunked by ticker to avoid PostgREST URL limit (>2000 chars with 250 tickers)."""
    result: list[dict] = []
    for i in range(0, len(tickers), chunk_size):
        chunk = tickers[i:i + chunk_size]
        q = supa.table(table).select(select).in_("ticker", chunk)
        r = filter_fn(q).execute()
        result.extend(r.data or [])
    return result


# ─── Finnhub fetch ────────────────────────────────────────────────────────────

def fetch_finnhub_12months(ticker: str) -> tuple[list[dict], bool]:
    """
    Tente de récupérer 12 mois d'articles Finnhub.
    Retourne (articles, has_data).
    """
    today = date.today()
    from_date = (today - timedelta(days=365)).isoformat()
    to_date = today.isoformat()

    try:
        r = httpx.get(
            "https://finnhub.io/api/v1/company-news",
            params={"symbol": ticker, "from": from_date, "to": to_date, "token": FINNHUB_KEY},
            timeout=15,
        )
        r.raise_for_status()
        raw = r.json()
        if not isinstance(raw, list):
            return [], False

        from services.articles_store import _article_id
        articles = [
            {
                "id":       _article_id(a["url"]),
                "headline": a["headline"],
                "source":   a["source"],
                "url":      a["url"],
                "datetime": a["datetime"],
                "summary":  a.get("summary", ""),
                "image":    a.get("image", ""),
            }
            for a in raw
            if a.get("headline") and a.get("url")
        ]
        return articles, len(articles) > 0
    except Exception as e:
        logger.warning(f"  Finnhub error {ticker}: {e}")
        return [], False


def fetch_yahoo_news(ticker: str) -> list[dict]:
    """Récupère les articles récents Yahoo Finance."""
    from services.articles_store import _article_id
    try:
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
            if not url or not c.get("title"):
                continue
            articles.append({
                "id":       _article_id(url),
                "headline": c.get("title", ""),
                "source":   c.get("provider", {}).get("displayName", "Yahoo Finance"),
                "url":      url,
                "datetime": ts,
                "summary":  c.get("summary", ""),
                "image":    (c.get("thumbnail") or {}).get("resolutions", [{}])[-1].get("url", "") if c.get("thumbnail") else "",
            })
        return articles
    except Exception as e:
        logger.warning(f"  Yahoo error {ticker}: {e}")
        return []


# ─── Stockage articles_merged_finn_yahoo ─────────────────────────────────────────────────

def store_articles(ticker: str, articles: list[dict], source: str, supa):
    """
    Répartit les articles par year_month et upsert dans articles_merged_finn_yahoo.
    Garde max 50 articles par year_month (les plus récents).
    """
    by_month: dict[str, list[dict]] = {}
    for a in articles:
        ts = a.get("datetime", 0)
        if not ts:
            continue
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        ym = f"{dt.year:04d}-{dt.month:02d}"
        by_month.setdefault(ym, []).append(a)

    inserted = 0
    for ym, month_articles in by_month.items():
        # Lire existant pour dédupliquer
        row = supa.table("articles") \
            .select("articles") \
            .eq("ticker", ticker) \
            .eq("year_month", ym) \
            .limit(1) \
            .execute()

        existing = row.data[0]["articles"] if row.data else []
        existing_urls = {a.get("url") for a in existing}

        new_ones = [a for a in month_articles if a.get("url") not in existing_urls]
        if not new_ones and existing:
            continue

        merged = sorted(existing + new_ones, key=lambda a: a.get("datetime", 0), reverse=True)[:50]

        supa.table("articles").upsert({
            "ticker":     ticker,
            "year_month": ym,
            "articles":   merged,
            "source":     source,
            "cached_at":  datetime.now(timezone.utc).isoformat(),
        }).execute()
        inserted += len(new_ones)

    return inserted


# ─── Phase scrape ────────────────────────────────────────────────────────────

def phase_scrape(tickers: list[str], region: str = "all"):
    supa = _supa()
    current_ym = date.today().strftime("%Y-%m")

    # Skip tickers qui ont déjà des articles pour le mois courant
    rows = _in_chunks(supa, "articles", "ticker", tickers, lambda q: q.eq("year_month", current_ym))
    done = {row["ticker"] for row in rows}
    todo = [t for t in tickers if t not in done]
    total = len(todo)
    logger.info(f"SCRAPE: {total} tickers à traiter ({len(done)} déjà en DB pour {current_ym})")

    if not todo:
        return

    _telegram(f"🚀 *Batch SCRAPE démarré — {region}*\n{total} tickers à traiter\nCheckpoint toutes les {NOTIFY_EVERY} tickers")

    start_time = time.time()
    batch_errors: list[str] = []
    batch_articles = 0
    n_done = 0

    for i, ticker in enumerate(todo, 1):
        logger.info(f"[{i}/{total}] {ticker}")
        ticker_articles = 0

        try:
            finnhub_articles, has_finnhub = fetch_finnhub_12months(ticker)
            time.sleep(FINNHUB_DELAY)

            if has_finnhub:
                n = store_articles(ticker, finnhub_articles, "finnhub", supa)
                ticker_articles += n
                logger.info(f"  Finnhub: {len(finnhub_articles)} articles, {n} nouveaux stockés")
            else:
                logger.info(f"  Finnhub: 0 articles → Yahoo fallback")

            yahoo_articles = fetch_yahoo_news(ticker)
            time.sleep(YAHOO_DELAY)

            source = "finnhub" if has_finnhub else "yahoo"
            if yahoo_articles:
                n = store_articles(ticker, yahoo_articles, source, supa)
                ticker_articles += n
                logger.info(f"  Yahoo: {len(yahoo_articles)} articles, {n} nouveaux stockés")

            batch_articles += ticker_articles
            n_done += 1

        except Exception as e:
            logger.error(f"  FAILED {ticker}: {e}")
            batch_errors.append(ticker)

        if i % NOTIFY_EVERY == 0:
            _notify_checkpoint("scrape", i, total, region, batch_errors, [], batch_articles, start_time)
            batch_errors = []
            batch_articles = 0

    elapsed = int(time.time() - start_time)
    mins, secs = divmod(elapsed, 60)
    _telegram(
        f"✅ *Batch SCRAPE terminé — {region}*\n"
        f"{n_done} tickers done · {len(batch_errors)} failed\n"
        f"⏱ Durée totale : {mins}m{secs:02d}s"
    )
    logger.info(f"SCRAPE terminé: {n_done} done, {len(batch_errors)} failed")


# ─── Phase enrich (AI news cache) ────────────────────────────────────────────

def phase_enrich(tickers: list[str], region: str = "all"):
    """
    Génère le news cache (24H + 7J summaries) pour chaque ticker.
    Respecte le concurrent limit FreeTheAI (séquentiel + délai).
    """
    from services.overview_service import gen_ow_both
    from services.llm_service import GroqExhaustedError

    supa = _supa()
    # Skip tickers qui ont déjà un OW EN en cache
    rows = _in_chunks(supa, "ow_cache", "ticker", tickers, lambda q: q.not_.is_("ow7d_en", "null"))
    done = {row["ticker"] for row in rows}
    todo = [t for t in tickers if t not in done]
    total = len(todo)

    logger.info(f"ENRICH: {total} tickers à traiter ({len(done)} déjà en DB)")
    if not todo:
        return

    _telegram(f"🤖 *Batch ENRICH démarré — {region}*\n{total} tickers à traiter\nCheckpoint toutes les {NOTIFY_EVERY} tickers")

    start_time = time.time()
    batch_errors: list[str] = []
    batch_llm_sources: dict[str, int] = {}
    delisted_tickers: list[str] = []
    n_done = 0

    for i, ticker in enumerate(todo, 1):
        logger.info(f"[{i}/{total}] {ticker} — génération AI summary")
        try:
            result = gen_ow_both(ticker)
            summary_ok = bool(result.get("summary") or result.get("summary_24h"))
            src = result.get("llm_source", "unknown")
            batch_llm_sources[src] = batch_llm_sources.get(src, 0) + 1
            logger.info(f"  {'✓' if summary_ok else '⚠'} {ticker} — sentiment: {result.get('sentiment','?')} · llm: {src}")
            time.sleep(FREETHEAI_DELAY)

            # Populate website domain for Logo.dev + détection delisted
            try:
                from urllib.parse import urlparse
                info = yf.Ticker(ticker).info or {}
                raw_url = info.get("website", "") or ""
                if raw_url:
                    domain = urlparse(raw_url).netloc.lstrip("www.")
                    if domain:
                        supa.table("ow_cache").update({"website": domain}).eq("ticker", ticker).execute()
                        logger.info(f"  🌐 {ticker} → {domain}")
                if info.get("regularMarketPrice") is None and info.get("currentPrice") is None:
                    delisted_tickers.append(ticker)
                    logger.warning(f"  ⚠ {ticker} — possibly delisted (no price data)")
            except Exception as we:
                err = str(we).lower()
                if "delisted" in err or "no data found" in err or "not found" in err:
                    delisted_tickers.append(ticker)
                    logger.warning(f"  ⚠ {ticker} — delisted: {we}")
                else:
                    logger.warning(f"  website fetch failed for {ticker}: {we}")

            n_done += 1

        except GroqExhaustedError:
            elapsed = int(time.time() - start_time)
            mins, secs = divmod(elapsed, 60)
            remaining = total - i + 1
            msg = (
                f"🛑 *Batch ENRICH arrêté — quota Groq épuisé*\n"
                f"{n_done} done · {remaining} restants\n"
                f"⏱ {mins}m{secs:02d}s · Relancer après minuit UTC"
            )
            _telegram(msg)
            logger.error(f"GroqExhaustedError — batch stoppé à {i}/{total}, {remaining} tickers restants")
            return

        except Exception as e:
            logger.error(f"  ✗ {ticker}: {e}")
            batch_errors.append(ticker)

        if i % NOTIFY_EVERY == 0:
            _notify_checkpoint("enrich", i, total, region, batch_errors, [], 0, start_time, batch_llm_sources)
            batch_errors = []
            batch_llm_sources = {}

    elapsed = int(time.time() - start_time)
    mins, secs = divmod(elapsed, 60)
    delisted_line = f"\n⚠️ Delisted ({len(delisted_tickers)}) : {', '.join(delisted_tickers)}" if delisted_tickers else ""
    _telegram(
        f"✅ *Batch ENRICH terminé — {region}*\n"
        f"{n_done} tickers done · {len(batch_errors)} failed{delisted_line}\n"
        f"⏱ Durée totale : {mins}m{secs:02d}s"
    )
    logger.info(f"ENRICH terminé: {n_done} done, {len(batch_errors)} failed, {len(delisted_tickers)} delisted: {delisted_tickers}")


# ─── Phase bubbles (pre-gen event_causes 6M+1Y) ──────────────────────────────

def _detect_peaks_valleys(closes: list[float], window: int, max_dots: int) -> list[tuple[int, float]]:
    """Reproduit la logique frontend. Retourne [(idx, pct), ...] triés par abs(pct)."""
    candidates = []
    for i in range(window, len(closes) - window):
        cur = closes[i]
        before = closes[i - window:i]
        after  = closes[i + 1:i + window + 1]
        is_peak   = cur > max(before) and cur > max(after)
        is_valley = cur < min(before) and cur < min(after)
        if not is_peak and not is_valley:
            continue
        neighbors = before + after
        avg = sum(neighbors) / len(neighbors)
        pct = (cur - avg) / avg * 100
        candidates.append((i, pct))
    candidates.sort(key=lambda x: -abs(x[1]))
    return candidates[:max_dots]


def phase_bubbles(tickers: list[str], region: str = "all", empty_only: bool = False,
                  cleanup: bool = False, force: bool = False, cascade: str = "zen"):
    """
    Pré-génère event_causes pour les dots 6M+1Y de chaque ticker.
    empty_only : tickers ayant ≥1 bub_en NULL en DB.
    force      : régénère tous les dots existants dans bub_cache (depuis DB, sans re-détecter yfinance).
    """
    from services.bubble_service import get_or_gen_bub

    supa = _supa()
    lang_col = "bub_en" if os.environ.get("LLM_LANG", "en") == "en" else "bub_fr"

    def _query_bub_cache(ticker_list: list[str], extra_filter) -> set[str]:
        """Requête bub_cache en chunks de 100 pour éviter la limite URL PostgREST."""
        result: set[str] = set()
        for i in range(0, len(ticker_list), 100):
            chunk = ticker_list[i:i + 100]
            q = supa.table("bub_cache").select("ticker").in_("ticker", chunk)
            r = extra_filter(q).limit(10000).execute()
            result.update(row["ticker"] for row in (r.data or []))
        return result

    if force:
        tickers_with_dots = _query_bub_cache(tickers, lambda q: q)
        todo = [t for t in tickers if t in tickers_with_dots]
        logger.info(f"BUBBLES force: {len(todo)} tickers avec dots en bub_cache")
    elif empty_only:
        tickers_with_empty = _query_bub_cache(tickers, lambda q: q.is_(lang_col, "null"))
        todo = [t for t in tickers if t in tickers_with_empty]
        logger.info(f"BUBBLES empty-only: {len(todo)} tickers avec ≥1 {lang_col} NULL")
    else:
        done = _query_bub_cache(tickers, lambda q: q.not_.is_(lang_col, "null"))
        todo = [t for t in tickers if t not in done]
        logger.info(f"BUBBLES: {len(todo)} tickers à traiter ({len(done)} déjà complets en DB)")
    total = len(todo)

    logger.info(f"BUBBLES: {total} tickers à traiter")
    if not todo:
        return

    mode_tag = "force" if force else ("empty-only" if empty_only else "normal")
    lang_tag = os.environ.get("LLM_LANG", "en").upper()
    n_done = len(tickers) - total
    done_line = f"({n_done} déjà complets)" if n_done > 0 else ""
    _telegram(
        f"🫧 *Batch BUBBLES démarré — {region} [{mode_tag}]*\n"
        f"{total} tickers à traiter {done_line}\n"
        f"Lang : {lang_tag} | Périodes : 1W · 1M · 6M · 1Y\n"
        f"📍 batch_fill_db"
    )

    # (period, interval, window, max_dots, col_key) — aligné frontend
    PERIODS = [
        ("5d",  "1h", 3,  5,  "1w"),
        ("1mo", "1d", 2,  8,  "1m"),
        ("6mo", "1d", 4, 10, "6m"),
        ("1y",  "1d", 5, 10, "1y"),
    ]

    start_time = time.time()
    batch_errors: list[str] = []
    total_events = 0
    total_cached = 0
    batch_llm_sources: dict[str, int] = {}
    batch_skipped = 0
    batch_skipped_tickers: list[str] = []
    total_win_counts: dict[str, int] = {}  # "1w", "1m", "6m", "1y" → total
    detected_dates_by_ticker: dict[str, set] = {}  # pour cleanup

    BUBBLES_WORKERS = int(os.environ.get("BUBBLES_WORKERS", "3"))

    def _process_ticker(ticker: str) -> dict:
        t_events, t_total, t_skipped = 0, 0, []
        t_cached = 0
        t_llm_sources: dict[str, int] = {}
        t_win_counts: dict[str, int] = {}  # "1w", "1m", "6m", "1y" → count

        # Pre-fetch tous les BUBs du ticker en 1 SELECT
        prefetch_r = _supa().table("bub_cache").select("date,bub_en,bub_fr,in_1w,in_1m,in_6m,in_1y").eq("ticker", ticker).execute()
        cache_by_date: dict[str, dict] = {r["date"]: r for r in (prefetch_r.data or [])}

        if force:
            # Mode force : dots depuis bub_cache, change_pct recalculé depuis yfinance
            try:
                hist = yf.Ticker(ticker).history(period="2y", interval="1d")
            except Exception:
                hist = None

            def _change_pct_from_hist(dt_str: str) -> float:
                if hist is None or hist.empty:
                    return 0.0
                try:
                    closes = hist["Close"].tolist()
                    dates = [d.strftime("%Y-%m-%d") for d in hist.index]
                    idx = dates.index(dt_str)
                    return round((closes[idx] - closes[idx - 1]) / closes[idx - 1] * 100 if idx > 0 else 0.0, 2)
                except (ValueError, IndexError):
                    return 0.0

            all_dots = [
                (r["date"], _change_pct_from_hist(r["date"]),
                 {k.replace("in_", "") for k in ("in_1w", "in_1m", "in_6m", "in_1y") if r.get(k)})
                for r in cache_by_date.values()
            ]
            logger.info(f"  {ticker} — {len(all_dots)} dots (force depuis bub_cache)")
        else:
            # Mode normal : détection dots depuis yfinance
            dot_windows: dict[str, dict] = {}
            for period, interval, window, max_dots, period_key in PERIODS:
                try:
                    hist = yf.Ticker(ticker).history(period=period, interval=interval)
                    if len(hist) < window * 2 + 1:
                        continue
                    closes = hist["Close"].tolist()
                    dates  = [d.strftime("%Y-%m-%d") for d in hist.index]
                    for idx, _ in _detect_peaks_valleys(closes, window, max_dots):
                        dt = dates[idx]
                        change_pct = round((closes[idx] - closes[idx - 1]) / closes[idx - 1] * 100 if idx > 0 else 0.0, 2)
                        if dt not in dot_windows:
                            dot_windows[dt] = {"change_pct": change_pct, "windows": set()}
                        dot_windows[dt]["windows"].add(period_key)
                except Exception as e:
                    logger.warning(f"  period {period} {ticker}: {e}")

            all_dots = [(dt, info["change_pct"], info["windows"]) for dt, info in dot_windows.items()]
            n_already_cached = sum(1 for dt, _, _ in all_dots if cache_by_date.get(dt, {}).get(lang_col))
            logger.info(f"  {ticker} — {len(all_dots)} dots · {n_already_cached}/{len(all_dots)} cached")

            if n_already_cached == len(all_dots) and all_dots and not force:
                return {"ticker": ticker, "events": 0, "total": 0, "skipped": [], "cached": len(all_dots), "llm_sources": {}, "detected_dates": set(), "win_counts": {}}

        n_dots = len(all_dots)
        for dot_idx, (dt, change_pct, windows) in enumerate(all_dots, 1):
            sign = "+" if change_pct >= 0 else ""
            win_flags = {f"in_{w}": (w in windows) for w in ("1w", "1m", "6m", "1y")} if windows else {}
            win_tag = ",".join(sorted(windows)) if windows else "?"
            cached_row = cache_by_date.get(dt)
            try:
                res = get_or_gen_bub(ticker, dt, change_pct, cascade=cascade,
                                     prefetched_cache=cached_row, force_regen=force, **win_flags)
                if not res.get("articles") and not res.get("cause"):
                    t_skipped.append(dt)
                    continue
                t_total += 1
                llm = res.get("llm_source")
                if res.get("cause"):
                    t_events += 1
                    for w in windows:
                        t_win_counts[w] = t_win_counts.get(w, 0) + 1
                    if llm:
                        t_llm_sources[llm] = t_llm_sources.get(llm, 0) + 1
                        logger.info(f"  {ticker} [bub {dot_idx}/{n_dots}] {dt} {sign}{change_pct:.1f}% → ✓ {llm} [{win_tag}]")
                    else:
                        t_cached += 1
                else:
                    logger.warning(f"  {ticker} [bub {dot_idx}/{n_dots}] {dt} {sign}{change_pct:.1f}% → ✗ LLM failed ({llm})")
                if llm:
                    time.sleep(0.5)
                elif cascade == "zen" and not res.get("cause"):
                    time.sleep(8)
            except Exception as e:
                logger.warning(f"  {ticker} [bub {dot_idx}/{n_dots}] {dt} {sign}{change_pct:.1f}% → ✗ {e}")
                if cascade == "zen":
                    time.sleep(8)

        return {
            "ticker": ticker, "events": t_events, "total": t_total, "skipped": t_skipped,
            "cached": t_cached, "llm_sources": t_llm_sources,
            "detected_dates": set(r["date"] for r in cache_by_date.values()) if force else set(dot_windows.keys() if not force else []),
            "win_counts": t_win_counts,
        }

    executor = ThreadPoolExecutor(max_workers=BUBBLES_WORKERS)
    futures = {executor.submit(_process_ticker, ticker): ticker for ticker in todo}
    try:
        for i, future in enumerate(as_completed(futures), 1):
            ticker = futures[future]
            logger.info(f"[ticker {i}/{total}] {ticker} — résultat reçu")
            try:
                r = future.result()
                ticker_events, ticker_total = r["events"], r["total"]
                ticker_skipped = r["skipped"]

                total_events += ticker_events
                total_cached += r.get("cached", 0)
                for src, cnt in r.get("llm_sources", {}).items():
                    batch_llm_sources[src] = batch_llm_sources.get(src, 0) + cnt
                for w, cnt in r.get("win_counts", {}).items():
                    total_win_counts[w] = total_win_counts.get(w, 0) + cnt
                detected_dates_by_ticker[ticker] = r.get("detected_dates", set())
                null_count = ticker_total - ticker_events

                if ticker_skipped:
                    batch_skipped += len(ticker_skipped)
                    batch_skipped_tickers.append(f"{ticker}×{len(ticker_skipped)}")
                    logger.info(f"  ⏭ {ticker} — {len(ticker_skipped)} dates sans articles")

                if ticker_total == 0:
                    logger.info(f"  ⏭ {ticker} — 0 bubbles avec articles")
                elif null_count == 0:
                    logger.info(f"  ✓ {ticker} — {ticker_events} bubbles avec cause")
                else:
                    logger.warning(f"  ⚠ {ticker} — {ticker_events} avec cause, {null_count} cause=null")

            except Exception as e:
                logger.error(f"  ✗ {ticker}: {e}")
                batch_errors.append(ticker)

            if i % NOTIFY_EVERY == 0:
                skip_info = f"{batch_skipped} bubbles — {', '.join(batch_skipped_tickers)}" if batch_skipped else ""
                tg_llm = {**batch_llm_sources, "cache": total_cached} if (batch_llm_sources or total_cached) else None
                win_info = " | ".join(f"{w}: {total_win_counts.get(w, 0)}" for w in ("1w", "1m", "6m", "1y"))
                logger.info(f"  Fenêtres: {win_info}")
                _notify_checkpoint("bubbles", i, total, region, batch_errors, [], 0, start_time,
                                   llm_sources=tg_llm, skipped_info=skip_info, windows_info=win_info)
                batch_errors = []
                batch_skipped = 0
                batch_skipped_tickers = []

    except KeyboardInterrupt:
        logger.info("Interruption — annulation des tâches en attente...")
        executor.shutdown(wait=False, cancel_futures=True)
        os._exit(0)
    finally:
        executor.shutdown(wait=False)

    # Cleanup : supprimer les rows bub_cache qui ne sont plus dans aucune fenêtre
    if cleanup and detected_dates_by_ticker:
        logger.info("BUBBLES cleanup — suppression des rows orphelines (in_6m=false AND in_1y=false)...")
        supa_clean = _supa()
        cleaned_total = 0
        for t, active_dates in detected_dates_by_ticker.items():
            try:
                existing_r = supa_clean.table("bub_cache").select("date").eq("ticker", t).execute()
                all_dates = {row["date"] for row in (existing_r.data or [])}
                orphan_dates = list(all_dates - active_dates)
                if orphan_dates:
                    supa_clean.table("bub_cache").delete().eq("ticker", t).in_("date", orphan_dates).execute()
                    cleaned_total += len(orphan_dates)
                    logger.info(f"  {t} — {len(orphan_dates)} rows orphelines supprimées")
            except Exception as e:
                logger.warning(f"  cleanup {t}: {e}")
        logger.info(f"BUBBLES cleanup terminé — {cleaned_total} rows supprimées")
        _telegram(f"🧹 *Cleanup bub_cache* — {cleaned_total} rows orphelines supprimées")

    elapsed = int(time.time() - start_time)
    mins, secs = divmod(elapsed, 60)
    win_summary = " | ".join(f"{w}: {total_win_counts.get(w, 0)}" for w in ("1w", "1m", "6m", "1y"))
    mode_tag = "force" if force else ("empty-only" if empty_only else "normal")
    _telegram(
        f"✅ *Batch BUBBLES terminé — {region} [{mode_tag}]*\n"
        f"{total_events} bubbles générés · {total} tickers\n"
        f"🪟 {win_summary}\n"
        f"⏱ {mins}m{secs:02d}s"
    )
    logger.info(f"BUBBLES terminé: {total_events} events générés | {win_summary}")


# ─── Phase translate-fr ───────────────────────────────────────────────────────

def phase_translate_fr(tickers: list[str], region: str = "all"):
    """
    event_causes : bub_en → bub_fr (skip si bub_fr déjà présent)
    OW retiré — trop volatile (refresh 3h), traduit à la demande au launch.
    """
    from services.translation_service import translate_to_fr

    supa = _supa()

    # ── BUBBLES (event_causes) ─────────────────────────────────────────────────
    from services.bubble_service import BUB_NO_ARTICLES as _BUB_NO_ARTICLES
    cause_rows_data = _in_chunks(supa, "bub_cache", "ticker, date, bub_en", tickers,
                                  lambda q: q.not_.is_("bub_en", "null").is_("bub_fr", "null"))
    cause_rows_data = [r for r in cause_rows_data if r.get("bub_en") not in ("", _BUB_NO_ARTICLES)]

    causes_by_ticker: dict[str, list[dict]] = {}
    for r in cause_rows_data:
        causes_by_ticker.setdefault(r["ticker"], []).append(r)

    total_bub = sum(len(v) for v in causes_by_ticker.values())
    logger.info(f"TRANSLATE-FR BUBBLES: {total_bub} event_causes sur {len(causes_by_ticker)} tickers")

    if causes_by_ticker:
        _telegram(f"🌐 *Batch TRANSLATE-FR BUBBLES démarré — {region}*\n{total_bub} events sur {len(causes_by_ticker)} tickers")

        start_time = time.time()
        bub_errors = []
        bub_done = 0
        for ticker, events in causes_by_ticker.items():
            ticker_ok = True
            for ev in events:
                try:
                    cause_fr = translate_to_fr(ev["bub_en"])
                    supa.table("bub_cache").update({"bub_fr": cause_fr}) \
                        .eq("ticker", ticker).eq("date", ev["date"]).execute()
                    bub_done += 1
                    logger.info(f"  ✓ {ticker} {ev['date']}")
                except Exception as e:
                    logger.error(f"  ✗ {ticker} {ev['date']}: {e}")
                    bub_errors.append(f"{ticker}:{ev['date']}")
                    ticker_ok = False


        elapsed = int(time.time() - start_time)
        mins, secs = divmod(elapsed, 60)
        _telegram(
            f"✅ *Batch TRANSLATE-FR BUBBLES terminé — {region}*\n"
            f"{bub_done} events · {len(bub_errors)} erreurs\n"
            f"⏱ {mins}m{secs:02d}s"
        )
    else:
        logger.info("TRANSLATE-FR BUBBLES: rien à traduire.")

    # ── BREAKS (week / month / year summaries) ────────────────────────────────
    from services.translation_service import translate_batch_to_fr

    for table, pk_fields, en_field, fr_field in [
        ("breaks_weekly",  ["year_month", "week_num"], "ai_summary", "bw_fr"),
        ("breaks_monthly", ["year_month"],             "ai_summary", "bm_fr"),
        ("breaks_yearly",  ["year"],                   "ai_summary", "by_fr"),
    ]:
        todo_breaks = _in_chunks(supa, table, ", ".join(["ticker"] + pk_fields + [en_field]), tickers,
                                  lambda q, ef=en_field, ff=fr_field: q.not_.is_(ef, "null").is_(ff, "null"))
        logger.info(f"TRANSLATE-FR {table}: {len(todo_breaks)} lignes à traduire")
        if not todo_breaks:
            continue

        texts = [r[en_field] for r in todo_breaks]
        try:
            translations = translate_batch_to_fr(texts)
        except Exception as e:
            logger.error(f"  ✗ batch translate {table}: {e}")
            continue

        for r, fr in zip(todo_breaks, translations):
            try:
                q = supa.table(table).update({fr_field: fr})
                for pk in pk_fields:
                    q = q.eq(pk, r[pk])
                q.eq("ticker", r["ticker"]).execute()
            except Exception as e:
                logger.error(f"  ✗ {table} {r}: {e}")

        logger.info(f"  ✓ {table} — {len(todo_breaks)} lignes traduites")


# ─── Phase translate-fr-dumb ──────────────────────────────────────────────────

def phase_translate_fr_dumb(tickers: list[str], region: str = "all"):
    """
    Traduit news_raw[].brutalised → ajoute brutalised_fr dans chaque article.
    Utilise Azure batch (25 articles/appel) pour ne pas exploser le quota FreeTheAI.
    Skip les articles qui ont déjà brutalised_fr.
    """
    from services.translation_service import translate_batch_to_fr, DUMB_BATCH_SIZE

    supa = _supa()

    rows_data = _in_chunks(supa, "ow_cache", "ticker, news_raw", tickers,
                            lambda q: q.not_.is_("news_raw", "null"))

    todo = [r for r in rows_data if r.get("news_raw")]
    total = len(todo)
    logger.info(f"TRANSLATE-FR DUMB: {total} tickers à traiter")

    if not todo:
        logger.info("Rien à traduire.")
        return

    _telegram(f"📝 *Batch TRANSLATE-FR DUMB démarré — {region}*\n{total} tickers")

    start_time = time.time()
    batch_errors = []
    total_articles = 0

    for i, row in enumerate(todo, 1):
        ticker = row["ticker"]
        articles = row["news_raw"] or []

        # Articles avec brutalised mais sans brutalised_fr
        to_translate = [
            (idx, a) for idx, a in enumerate(articles)
            if a.get("brutalised") and a["brutalised"].upper() != "SKIP" and not a.get("brutalised_fr")
        ]

        if not to_translate:
            continue

        logger.info(f"[DUMB {i}/{total}] {ticker} — {len(to_translate)} articles")
        try:
            indices = [idx for idx, _ in to_translate]
            texts = [a["brutalised"] for _, a in to_translate]

            for chunk_start in range(0, len(texts), DUMB_BATCH_SIZE):
                chunk_texts = texts[chunk_start:chunk_start + DUMB_BATCH_SIZE]
                chunk_indices = indices[chunk_start:chunk_start + DUMB_BATCH_SIZE]
                translations = translate_batch_to_fr(chunk_texts)
                for idx, fr_text in zip(chunk_indices, translations):
                    articles[idx]["brutalised_fr"] = fr_text

            supa.table("ow_cache").update({"news_raw": articles}).eq("ticker", ticker).execute()
            total_articles += len(to_translate)
            logger.info(f"  ✓ {ticker} ({len(to_translate)} articles traduits)")
        except Exception as e:
            logger.error(f"  ✗ {ticker}: {e}")
            batch_errors.append(ticker)

        if i % NOTIFY_EVERY == 0:
            _notify_checkpoint("translate-fr-dumb", i, total, region, batch_errors, [], 0, start_time)
            batch_errors = []

    elapsed = int(time.time() - start_time)
    mins, secs = divmod(elapsed, 60)
    n_done = total - len(batch_errors)
    _telegram(
        f"✅ *Batch TRANSLATE-FR DUMB terminé — {region}*\n"
        f"{n_done} tickers · {total_articles} articles\n"
        f"⏱ {mins}m{secs:02d}s"
    )


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Batch DB filler pour StockVibe")
    parser.add_argument("--phase", choices=["scrape", "enrich", "all", "translate-fr", "translate-fr-dumb", "bubbles"], default="scrape")
    parser.add_argument("--region", choices=["US", "EU", "ASIA", "all"], default="all")
    parser.add_argument("--ticker", type=str, default=None, help="Ticker unique")
    parser.add_argument("--lang", choices=["fr", "en"], default=None,
                        help="Langue de génération LLM (fr=default, en=EN-first)")
    parser.add_argument("--translate-fr", action="store_true",
                        help="Traduit EN→FR après génération")
    parser.add_argument("--empty-only", action="store_true",
                        help="Bubbles : tickers ayant ≥1 BUB vide en DB")
    parser.add_argument("--force", action="store_true",
                        help="Bubbles : régénère toutes les causes existantes dans bub_cache")
    parser.add_argument("--cleanup", action="store_true",
                        help="Bubbles : supprime les rows bub_cache orphelines après le run")
    parser.add_argument("--cascade", type=str, default=None,
                        help="Cascade LLM à utiliser (zen, promo, bub_batch…). Override le défaut hardcodé.")
    args = parser.parse_args()

    if args.lang:
        os.environ["LLM_LANG"] = args.lang
        logger.info(f"LLM_LANG={args.lang}")

    if args.translate_fr:
        os.environ["TRANSLATE_TO_FR"] = "true"
        logger.info("TRANSLATE_TO_FR=true — traduction EN→FR activée")

    if args.ticker:
        tickers = [args.ticker.upper()]
    elif args.region == "all":
        tickers = ALL_TICKERS
    else:
        tickers = get_by_region(args.region)

    logger.info(f"Phase: {args.phase} | Région: {args.region} | Lang: {os.environ.get('LLM_LANG','fr')} | {len(tickers)} tickers")

    if args.phase in ("scrape", "all"):
        phase_scrape(tickers, region=args.region)

    if args.phase in ("enrich", "all"):
        phase_enrich(tickers, region=args.region)

    if args.phase == "translate-fr":
        phase_translate_fr(tickers, region=args.region)

    if args.phase == "translate-fr-dumb":
        phase_translate_fr_dumb(tickers, region=args.region)

    if args.phase == "bubbles":
        phase_bubbles(tickers, region=args.region, empty_only=args.empty_only,
                      cleanup=args.cleanup, force=args.force, cascade=args.cascade)


if __name__ == "__main__":
    main()
