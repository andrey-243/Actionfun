"""
Batch scraper — remplit article_archive pour 717 tickers (US + EU + Asie).

Usage :
  python scripts/batch_fill_db.py --phase scrape          # articles seulement
  python scripts/batch_fill_db.py --phase enrich          # AI news cache
  python scripts/batch_fill_db.py --phase all             # les deux
  python scripts/batch_fill_db.py --region US             # région spécifique
  python scripts/batch_fill_db.py --ticker AAPL           # ticker unique

Reprend là où il s'est arrêté (progress.json).
"""

import argparse
import json
import os
import sys
import time
import logging
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

NOTIFY_EVERY = 25  # notif Telegram tous les N tickers


def _telegram(msg: str):
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


def _notify_checkpoint(phase: str, i: int, total: int, region: str,
                        done_batch: list[str], errors_batch: list[str],
                        articles_batch: int, start_time: float,
                        llm_sources: dict[str, int] | None = None):
    elapsed = int(time.time() - start_time)
    mins, secs = divmod(elapsed, 60)
    remaining = int((elapsed / i) * (total - i)) if i else 0
    r_mins, r_secs = divmod(remaining, 60)

    err_line = f"❌ Errors: {', '.join(errors_batch)}" if errors_batch else "✅ 0 erreurs"
    art_line = f"📰 Articles: +{articles_batch:,}" if articles_batch else ""

    llm_line = ""
    if llm_sources:
        _ICONS = {"freetheai": "🟢", "hapuppy": "🟡", "agentrouter": "🔵", "groq": "🟠", "unknown": "⚪"}
        parts = [f"{_ICONS.get(k, '⚪')} {k}: {v}" for k, v in sorted(llm_sources.items(), key=lambda x: -x[1])]
        llm_line = "🤖 LLM: " + " · ".join(parts)

    msg = (
        f"📊 *Batch {phase.upper()} — {region}*\n"
        f"Checkpoint {i}/{total} ({i*100//total}%)\n"
        f"⏱ {mins}m{secs:02d}s écoulé · ~{r_mins}m{r_secs:02d}s restant\n"
        f"{err_line}\n"
        f"{art_line}\n"
        f"{llm_line}"
    ).strip()

    _telegram(msg)
    logger.info(f"[NOTIF] {i}/{total} — {len(errors_batch)} erreurs · LLM: {llm_sources}")

PROGRESS_FILE = Path(__file__).parent / "batch_progress.json"  # overridden in main() per phase+region
FINNHUB_KEY = os.environ["FINNHUB_API_KEY"]
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_KEY"]

# Rate limits
FINNHUB_DELAY = 1.1      # 60 req/min → 1s entre calls
YAHOO_DELAY = 6.0        # 10 req/min safe
FREETHEAI_DELAY = 6.0    # 60s / 10 RPM — on attend la réponse du call (sync) puis 6s


def _supa():
    return create_client(SUPABASE_URL, SUPABASE_KEY)


# ─── Progress tracking ────────────────────────────────────────────────────────

def load_progress() -> dict:
    if PROGRESS_FILE.exists():
        return json.loads(PROGRESS_FILE.read_text())
    return {"scraped": [], "enriched": [], "failed_scrape": [], "failed_enrich": []}


def save_progress(p: dict):
    PROGRESS_FILE.write_text(json.dumps(p, indent=2))


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

        articles = [
            {
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


# ─── Stockage article_archive ─────────────────────────────────────────────────

def store_articles(ticker: str, articles: list[dict], source: str, supa):
    """
    Répartit les articles par year_month et upsert dans article_archive.
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
        row = supa.table("article_archive") \
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

        supa.table("article_archive").upsert({
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
    progress = load_progress()
    done = set(progress["scraped"])
    failed = set(progress["failed_scrape"])

    todo = [t for t in tickers if t not in done and t not in failed]
    total = len(todo)
    logger.info(f"SCRAPE: {total} tickers à traiter ({len(done)} déjà done, {len(failed)} failed)")

    if not todo:
        return

    _telegram(f"🚀 *Batch SCRAPE démarré — {region}*\n{total} tickers à traiter\nCheckpoint toutes les {NOTIFY_EVERY} tickers")

    supa = _supa()
    start_time = time.time()
    batch_errors: list[str] = []
    batch_articles = 0

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
            progress["scraped"].append(ticker)

        except Exception as e:
            logger.error(f"  FAILED {ticker}: {e}")
            progress["failed_scrape"].append(ticker)
            batch_errors.append(ticker)

        save_progress(progress)

        if i % NOTIFY_EVERY == 0:
            _notify_checkpoint("scrape", i, total, region, batch_errors, [], batch_articles, start_time)
            batch_errors = []
            batch_articles = 0

    # Notif finale
    elapsed = int(time.time() - start_time)
    mins, secs = divmod(elapsed, 60)
    total_failed = len(progress["failed_scrape"])
    _telegram(
        f"✅ *Batch SCRAPE terminé — {region}*\n"
        f"{len(progress['scraped'])} tickers done · {total_failed} failed\n"
        f"⏱ Durée totale : {mins}m{secs:02d}s"
    )
    logger.info(f"SCRAPE terminé: {len(progress['scraped'])} done, {total_failed} failed")


# ─── Phase enrich (AI news cache) ────────────────────────────────────────────

def phase_enrich(tickers: list[str], region: str = "all"):
    """
    Génère le news cache (24H + 7J summaries) pour chaque ticker.
    Respecte le concurrent limit FreeTheAI (séquentiel + délai).
    """
    from services.overview_service import get_news
    from services.llm_service import GroqExhaustedError

    supa = _supa()
    progress = load_progress()
    done = set(progress["enriched"])
    todo = [t for t in tickers if t not in done]
    total = len(todo)

    logger.info(f"ENRICH: {total} tickers à traiter ({len(done)} déjà done)")
    if not todo:
        return

    _telegram(f"🤖 *Batch ENRICH démarré — {region}*\n{total} tickers à traiter\nCheckpoint toutes les {NOTIFY_EVERY} tickers")

    start_time = time.time()
    batch_errors: list[str] = []
    batch_llm_sources: dict[str, int] = {}
    delisted_tickers: list[str] = []

    for i, ticker in enumerate(todo, 1):
        logger.info(f"[{i}/{total}] {ticker} — génération AI summary")
        try:
            result = get_news(ticker)
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
                        supa.table("stock_news_cache").update({"website": domain}).eq("ticker", ticker).execute()
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

            progress["enriched"].append(ticker)

        except GroqExhaustedError:
            elapsed = int(time.time() - start_time)
            mins, secs = divmod(elapsed, 60)
            remaining = total - i + 1
            save_progress(progress)
            msg = (
                f"🛑 *Batch ENRICH arrêté — quota Groq épuisé*\n"
                f"Progress sauvegardé : {len(progress['enriched'])} done · {remaining} restants\n"
                f"⏱ {mins}m{secs:02d}s · Relancer après minuit UTC"
            )
            _telegram(msg)
            logger.error(f"GroqExhaustedError — batch stoppé à {i}/{total}, {remaining} tickers restants")
            return

        except Exception as e:
            logger.error(f"  ✗ {ticker}: {e}")
            progress.setdefault("failed_enrich", []).append(ticker)
            batch_errors.append(ticker)

        save_progress(progress)

        if i % NOTIFY_EVERY == 0:
            _notify_checkpoint("enrich", i, total, region, batch_errors, [], 0, start_time, batch_llm_sources)
            batch_errors = []
            batch_llm_sources = {}

    elapsed = int(time.time() - start_time)
    mins, secs = divmod(elapsed, 60)
    total_failed = len(progress.get("failed_enrich", []))
    delisted_line = f"\n⚠️ Delisted ({len(delisted_tickers)}) : {', '.join(delisted_tickers)}" if delisted_tickers else ""
    _telegram(
        f"✅ *Batch ENRICH terminé — {region}*\n"
        f"{len(progress['enriched'])} tickers done · {total_failed} failed{delisted_line}\n"
        f"⏱ Durée totale : {mins}m{secs:02d}s"
    )
    logger.info(f"ENRICH terminé: {len(progress['enriched'])} done, {total_failed} failed, {len(delisted_tickers)} delisted: {delisted_tickers}")


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


def phase_bubbles(tickers: list[str], region: str = "all"):
    """
    Pré-génère event_causes pour les dots 6M+1Y de chaque ticker.
    Stable dans le temps — ne regénère pas si déjà en cache.
    """
    from services.bubble_service import get_event_news

    supa = _supa()
    progress = load_progress()
    done = set(progress.get("bubbles_done", []))
    todo = [t for t in tickers if t not in done]
    total = len(todo)

    logger.info(f"BUBBLES: {total} tickers à traiter ({len(done)} déjà done)")
    if not todo:
        return

    _telegram(f"🫧 *Batch BUBBLES démarré — {region}*\n{total} tickers\nPériodes : 6M + 1Y")

    PERIODS = [
        ("6mo", "1d", 4, 10),  # (period, interval, window, max_dots)
        ("1y",  "1d", 5, 10),
    ]

    start_time = time.time()
    batch_errors: list[str] = []
    total_events = 0

    for i, ticker in enumerate(todo, 1):
        logger.info(f"[{i}/{total}] {ticker} — bubbles 6M+1Y")
        ticker_events = 0  # bubbles avec cause LLM réelle
        ticker_total = 0   # toutes les bubbles tentées
        try:
            for period, interval, window, max_dots in PERIODS:
                try:
                    hist = yf.Ticker(ticker).history(period=period, interval=interval)
                    if len(hist) < window * 2 + 1:
                        continue

                    closes = hist["Close"].tolist()
                    dates  = [d.strftime("%Y-%m-%d") for d in hist.index]

                    events = _detect_peaks_valleys(closes, window, max_dots)
                    for idx, _ in events:
                        date = dates[idx]
                        # change_pct = variation jour J vs J-1
                        change_pct = (closes[idx] - closes[idx - 1]) / closes[idx - 1] * 100 if idx > 0 else 0.0
                        try:
                            result = get_event_news(ticker, date, round(change_pct, 2))
                            ticker_total += 1
                            if result.get("cause"):
                                ticker_events += 1
                            else:
                                logger.warning(f"  bubble {ticker} {date}: cause=null (LLM failed)")
                            time.sleep(0.5)  # éviter burst Groq
                        except Exception as e:
                            logger.warning(f"  bubble {ticker} {date}: {e}")

                except Exception as e:
                    logger.warning(f"  period {period} {ticker}: {e}")

            total_events += ticker_events
            null_count = ticker_total - ticker_events
            if null_count == 0:
                logger.info(f"  ✓ {ticker} — {ticker_events} bubbles avec cause")
                progress.setdefault("bubbles_done", []).append(ticker)
            else:
                logger.warning(f"  ⚠ {ticker} — {ticker_events} avec cause, {null_count} cause=null (LLM down) → pas marqué done")

        except Exception as e:
            logger.error(f"  ✗ {ticker}: {e}")
            batch_errors.append(ticker)

        save_progress(progress)

        if i % NOTIFY_EVERY == 0:
            _notify_checkpoint("bubbles", i, total, region, batch_errors, [], 0, start_time)
            batch_errors = []

    elapsed = int(time.time() - start_time)
    mins, secs = divmod(elapsed, 60)
    _telegram(
        f"✅ *Batch BUBBLES terminé — {region}*\n"
        f"{len(progress.get('bubbles_done', []))} tickers · {total_events} bubbles\n"
        f"⏱ {mins}m{secs:02d}s"
    )
    logger.info(f"BUBBLES terminé: {total_events} events générés")


# ─── Phase translate-fr ───────────────────────────────────────────────────────

def phase_translate_fr(tickers: list[str], region: str = "all"):
    """
    Lit ai_summary_en depuis stock_news_cache et traduit vers ai_summary (FR).
    Ne traite que les tickers qui ont ai_summary_en mais pas ai_summary.
    """
    from services.translation_service import translate_to_fr

    supa = _supa()
    progress = load_progress()
    translated = set(progress.get("translated_fr", []))

    # Charger les tickers qui ont EN mais pas FR
    rows = supa.table("stock_news_cache") \
        .select("ticker, ai_summary_en, ai_summary_24h_en, ai_summary") \
        .in_("ticker", tickers) \
        .not_.is_("ai_summary_en", "null") \
        .execute()

    todo = [
        r for r in (rows.data or [])
        if r["ticker"] not in translated and not r.get("ai_summary")
    ]
    total = len(todo)
    logger.info(f"TRANSLATE-FR: {total} tickers à traduire ({len(translated)} déjà fait)")

    if not todo:
        logger.info("Rien à traduire.")
        return

    _telegram(f"🌐 *Batch TRANSLATE-FR démarré — {region}*\n{total} tickers à traduire")

    start_time = time.time()
    batch_errors = []

    for i, row in enumerate(todo, 1):
        ticker = row["ticker"]
        logger.info(f"[{i}/{total}] {ticker} — traduction FR")
        try:
            summary_fr = translate_to_fr(row["ai_summary_en"])
            summary_24h_fr = translate_to_fr(row.get("ai_summary_24h_en") or "") if row.get("ai_summary_24h_en") else ""

            supa.table("stock_news_cache").update({
                "ai_summary": summary_fr,
                "ai_summary_24h": summary_24h_fr,
            }).eq("ticker", ticker).execute()

            logger.info(f"  ✓ {ticker}")
            progress.setdefault("translated_fr", []).append(ticker)
        except Exception as e:
            logger.error(f"  ✗ {ticker}: {e}")
            batch_errors.append(ticker)

        save_progress(progress)
        time.sleep(FREETHEAI_DELAY)

        if i % NOTIFY_EVERY == 0:
            _notify_checkpoint("translate-fr", i, total, region, batch_errors, [], 0, start_time)
            batch_errors = []

    elapsed = int(time.time() - start_time)
    mins, secs = divmod(elapsed, 60)
    _telegram(f"✅ *Batch TRANSLATE-FR terminé — {region}*\n{len(progress.get('translated_fr',[]))} tickers · ⏱ {mins}m{secs:02d}s")


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Batch DB filler pour StockVibe")
    parser.add_argument("--phase", choices=["scrape", "enrich", "all", "translate-fr", "bubbles"], default="scrape")
    parser.add_argument("--region", choices=["US", "EU", "ASIA", "all"], default="all")
    parser.add_argument("--ticker", type=str, default=None, help="Ticker unique")
    parser.add_argument("--reset", action="store_true", help="Remet le progress à zéro")
    parser.add_argument("--lang", choices=["fr", "en"], default=None,
                        help="Langue de génération LLM (fr=default, en=EN-first)")
    parser.add_argument("--translate-fr", action="store_true",
                        help="Traduit EN→FR après génération (pour EU/ASIA qui n'ont pas encore de FR)")
    parser.add_argument("--progress-file", type=str, default=None,
                        help="Fichier de progress custom (défaut: batch_progress.json)")
    args = parser.parse_args()

    # Progress file : custom > auto-namé par phase+region pour éviter les dirty writes concurrents
    global PROGRESS_FILE
    if args.progress_file:
        PROGRESS_FILE = Path(args.progress_file)
    else:
        region_slug = args.region.lower()
        lang_slug = (args.lang or os.environ.get("LLM_LANG", "en")).lower()
        if args.phase in ("enrich", "bubbles"):
            PROGRESS_FILE = Path(__file__).parent / f"batch_progress_{args.phase}_{region_slug}_{lang_slug}.json"
        else:
            PROGRESS_FILE = Path(__file__).parent / f"batch_progress_{args.phase}_{region_slug}.json"

    # Appliquer la langue via env var (lu par tous les services)
    if args.lang:
        os.environ["LLM_LANG"] = args.lang
        logger.info(f"LLM_LANG={args.lang}")

    if args.translate_fr:
        os.environ["TRANSLATE_TO_FR"] = "true"
        logger.info("TRANSLATE_TO_FR=true — traduction EN→FR activée")

    if args.reset:
        PROGRESS_FILE.unlink(missing_ok=True)
        logger.info("Progress remis à zéro")

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

    if args.phase == "bubbles":
        phase_bubbles(tickers, region=args.region)


if __name__ == "__main__":
    main()
