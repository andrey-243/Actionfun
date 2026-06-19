"""
Point d'entrée FastAPI.

Enregistre tous les routers (/api/stock, /api/news, /api/search, /api/events,
/api/articles, /api/weekly, /api/ticker) et démarre APScheduler avec les crons :
  - yahoo */3h      : merge articles + trigger OW si nouveaux articles
  - finnhub */6h    : idem côté Finnhub
  - google_rss */3h : merge Google RSS (tous pinnés) + trigger OW
  - marketaux */6h  : merge Marketaux EU+ASIA + trigger OW
  - newsapi */6h    : merge NewsAPI EU+ASIA + trigger OW
  - hourly :15      : refresh OW pour tickers no_24h_news
  - email 19h15     : Daily digest Resend (1J)
  - email sam 7h00  : Weekly digest Resend (7J)
  - weekly lun 3h30 : condensation weekly summaries
  - monthly 1er 4h  : condensation monthly calendaire
  - yearly 1er jan  : condensation yearly summaries
  - re-enrich 22h30 : re-process records Groq → FreeTheAI
  - purge lun 3h    : bub_cache > 1 an
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from apscheduler.schedulers.background import BackgroundScheduler
from dotenv import load_dotenv
import logging
import os

load_dotenv()

from routers import stock, news, search, events, digest, articles, cold_start

logger = logging.getLogger(__name__)


def _tg(msg: str):
    import httpx
    from datetime import datetime, timezone
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        return
    now = datetime.now(timezone.utc).strftime("%H:%M UTC")
    try:
        httpx.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": f"{msg} · _{now}_ · 📍 _cron_", "parse_mode": "Markdown"},
            timeout=5,
        )
    except Exception:
        pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler = BackgroundScheduler(timezone="Europe/Paris")
    scheduler.add_job(
        _run_digest_email,
        trigger="cron",
        hour=19,
        minute=15,
        id="daily_digest_email",
    )
    scheduler.add_job(
        _send_weekly_digest,
        trigger="cron",
        day_of_week="sat",
        hour=7,
        minute=0,
        id="weekly_digest_email",
    )
    scheduler.add_job(
        _gen_breaks_weekly,
        trigger="cron",
        day_of_week="mon",
        hour=3,
        minute=30,
        id="gen_breaks_weekly",
    )
    scheduler.add_job(
        _gen_breaks_monthly,
        trigger="cron",
        day=1,
        hour=4,
        minute=0,
        id="gen_breaks_monthly",
    )
    scheduler.add_job(
        _gen_breaks_yearly,
        trigger="cron",
        month=1,
        day=1,
        hour=5,
        minute=0,
        id="gen_breaks_yearly",
    )
    scheduler.add_job(
        _yahoo_articles_scrap,
        trigger="cron",
        hour="*/3",
        minute=0,
        id="yahoo_articles_scrap",
    )
    scheduler.add_job(
        _finnhub_articles_scrap,
        trigger="cron",
        hour="*/6",
        minute=30,
        id="finnhub_articles_scrap",
    )
    scheduler.add_job(
        _google_rss_articles_scrap,
        trigger="cron",
        hour="*/3",
        minute=45,
        id="google_rss_articles_scrap",
    )
    scheduler.add_job(
        _marketaux_articles_scrap,
        trigger="cron",
        hour="*/6",
        minute=15,
        id="marketaux_articles_scrap",
    )
    scheduler.add_job(
        _newsapi_articles_scrap,
        trigger="cron",
        hour="*/6",
        minute=20,
        id="newsapi_articles_scrap",
    )
    scheduler.add_job(
        _hourly_ow_regen,
        trigger="cron",
        minute=15,
        id="hourly_ow_regen",
    )
    scheduler.add_job(
        _run_cold_start_translate_fr,
        trigger="cron",
        hour=19,
        minute=0,
        id="cold_start_translate_fr",
    )
    scheduler.add_job(
        _reenrich_groq_to_better,
        trigger="cron",
        hour=22,
        minute=30,
        id="reenrich_groq_to_better",
    )
    scheduler.add_job(
        _reenrich_retry,
        trigger="cron",
        minute=35,
        id="reenrich_retry",
    )
    scheduler.start()
    logger.info("Crons démarrés — daily_digest 19h15, weekly_digest sam 7h, gen_breaks_weekly lun 3h30, gen_breaks_monthly 1er 4h, gen_breaks_yearly 1er jan 5h, yahoo_articles_scrap */3h, finnhub_articles_scrap */6h, google_rss_articles_scrap */3h:45, marketaux_articles_scrap */6h:15, newsapi_articles_scrap */6h:20, cold_start_translate_fr 19h, reenrich_groq_to_better 22h30, reenrich_retry :35 (pending), hourly_ow_regen :15")
    yield
    scheduler.shutdown()


def _run_digest_email():
    import time
    from services.digest_service import send_daily_digest
    _tg("▶ *Daily Digest (1J) — envoi email* (18h30)")
    t = time.time()
    try:
        send_daily_digest()
        _tg(f"✅ *Daily Digest (1J) — email envoyé* ({int(time.time()-t)}s)")
    except Exception as e:
        logger.error(f"Daily digest email failed: {e}")
        _tg(f"❌ *Daily Digest (1J) — email* : `{e}`")


def _send_weekly_digest():
    import time
    from services.digest_service import send_weekly_digest
    _tg("▶ *Weekly Digest (7J) — envoi email* (sam 7h)")
    t = time.time()
    try:
        send_weekly_digest()
        _tg(f"✅ *Weekly Digest (7J) — email envoyé* (sam 7h · {int(time.time()-t)}s)")
    except Exception as e:
        logger.error(f"Weekly digest email failed: {e}")
        _tg(f"❌ *Weekly Digest (7J) — email* : `{e}`")


def _gen_breaks_weekly():
    import time
    from services.breakers_articles_service import gen_breaks_weekly
    _tg("▶ *Breaks weekly — gen* (lun 3h30)")
    t = time.time()
    try:
        r = gen_breaks_weekly()
        llm = " · ".join(f"{k}:{v}" for k, v in r["llm"].items()) if r["llm"] else "—"
        _tg(f"✅ *Breaks weekly — gen* ({int(time.time()-t)}s)\ndone:{r['done']} p2:{r['p2_done']} skip:{r['skipped']} err:{r['errors']} | {llm}")
    except Exception as e:
        logger.error(f"Breaks weekly gen failed: {e}")
        _tg(f"❌ *Breaks weekly — gen* : `{e}`")


def _gen_breaks_monthly():
    import time
    from services.breakers_articles_service import gen_breaks_monthly
    _tg("▶ *Breaks monthly — gen* (1er du mois 4h)")
    t = time.time()
    try:
        r = gen_breaks_monthly()
        llm = " · ".join(f"{k}:{v}" for k, v in r["llm"].items()) if r["llm"] else "—"
        _tg(f"✅ *Breaks monthly — gen* ({int(time.time()-t)}s)\ndone:{r['done']} skip:{r['skipped']} err:{r['errors']} | {llm}")
    except Exception as e:
        logger.error(f"Breaks monthly gen failed: {e}")
        _tg(f"❌ *Breaks monthly — gen* : `{e}`")


def _gen_breaks_yearly():
    import time
    from services.breakers_articles_service import gen_breaks_yearly
    _tg("▶ *Breaks yearly — gen* (1er jan 5h)")
    t = time.time()
    try:
        r = gen_breaks_yearly()
        llm = " · ".join(f"{k}:{v}" for k, v in r["llm"].items()) if r["llm"] else "—"
        _tg(f"✅ *Breaks yearly — gen* ({int(time.time()-t)}s)\ndone:{r['done']} skip:{r['skipped']} err:{r['errors']} | {llm}")
    except Exception as e:
        logger.error(f"Breaks yearly gen failed: {e}")
        _tg(f"❌ *Breaks yearly — gen* : `{e}`")


def _yahoo_articles_scrap():
    import time
    from services.breakers_articles_service import yahoo_articles_scrap
    _tg("▶ *Yahoo — scrap + OW regen* (*/3h)")
    t = time.time()
    try:
        results = yahoo_articles_scrap()
        n_tickers = len(results)
        n_articles = sum(results.values())
        detail = " · ".join(f"{tk} +{n}" for tk, n in sorted(results.items())) if results else "rien de nouveau"
        _tg(f"✅ *Yahoo — scrap + OW regen* ({int(time.time()-t)}s)\n{n_tickers} tickers · {n_articles} articles\n{detail}")
    except Exception as e:
        logger.error(f"Yahoo articles scrap failed: {e}")
        _tg(f"❌ *Yahoo — scrap + OW regen* : `{e}`")


def _finnhub_articles_scrap():
    import time
    from services.breakers_articles_service import finnhub_articles_scrap
    _tg("▶ *Finnhub — scrap + OW regen* (*/6h)")
    t = time.time()
    try:
        results = finnhub_articles_scrap()
        n_tickers = len(results)
        n_articles = sum(results.values())
        detail = " · ".join(f"{tk} {n}" for tk, n in sorted(results.items())) if results else "rien de nouveau"
        _tg(f"✅ *Finnhub — scrap + OW regen* ({int(time.time()-t)}s)\n{n_tickers} tickers · {n_articles} articles\n{detail}")
    except Exception as e:
        logger.error(f"Finnhub articles scrap failed: {e}")
        _tg(f"❌ *Finnhub — scrap + OW regen* : `{e}`")


def _google_rss_articles_scrap():
    import time
    from services.breakers_articles_service import google_rss_articles_scrap
    _tg("▶ *Google RSS — scrap + OW regen* (*/3h)")
    t = time.time()
    try:
        results = google_rss_articles_scrap()
        n_tickers = len(results)
        n_articles = sum(results.values())
        detail = " · ".join(f"{tk} +{n}" for tk, n in sorted(results.items())) if results else "rien de nouveau"
        _tg(f"✅ *Google RSS — scrap + OW regen* ({int(time.time()-t)}s)\n{n_tickers} tickers · {n_articles} articles\n{detail}")
    except Exception as e:
        logger.error(f"Google RSS articles scrap failed: {e}")
        _tg(f"❌ *Google RSS — scrap + OW regen* : `{e}`")


def _marketaux_articles_scrap():
    import time
    from services.breakers_articles_service import marketaux_articles_scrap
    _tg("▶ *Marketaux — scrap EU+ASIA* (*/6h)")
    t = time.time()
    try:
        results = marketaux_articles_scrap()
        n_tickers = len(results)
        n_articles = sum(results.values())
        detail = " · ".join(f"{tk} +{n}" for tk, n in sorted(results.items())) if results else "rien de nouveau"
        _tg(f"✅ *Marketaux — scrap EU+ASIA* ({int(time.time()-t)}s)\n{n_tickers} tickers · {n_articles} articles\n{detail}")
    except Exception as e:
        logger.error(f"Marketaux articles scrap failed: {e}")
        _tg(f"❌ *Marketaux — scrap EU+ASIA* : `{e}`")


def _newsapi_articles_scrap():
    import time
    from services.breakers_articles_service import newsapi_articles_scrap
    _tg("▶ *NewsAPI — scrap EU+ASIA* (*/6h)")
    t = time.time()
    try:
        results = newsapi_articles_scrap()
        n_tickers = len(results)
        n_articles = sum(results.values())
        detail = " · ".join(f"{tk} +{n}" for tk, n in sorted(results.items())) if results else "rien de nouveau"
        _tg(f"✅ *NewsAPI — scrap EU+ASIA* ({int(time.time()-t)}s)\n{n_tickers} tickers · {n_articles} articles\n{detail}")
    except Exception as e:
        logger.error(f"NewsAPI articles scrap failed: {e}")
        _tg(f"❌ *NewsAPI — scrap EU+ASIA* : `{e}`")


_reenrich_pending: bool = False


def _run_reenrich(label: str):
    """Lance reenrich_groq_to_better, met à jour _reenrich_pending. Retourne True si terminé."""
    global _reenrich_pending
    import time
    from services.reenrich_service import reenrich_groq_to_better
    _tg(f"▶ *Re-enrich — Groq → Better* ({label})")
    t = time.time()
    try:
        r = reenrich_groq_to_better()
        elapsed = int(time.time() - t)
        if r["stopped_at"]:
            _reenrich_pending = True
            _tg(f"⏸ *Re-enrich* stoppé à `{r['stopped_at']}` ({elapsed}s) — retry à 2h+")
            return False
        _reenrich_pending = False
        _tg(f"✅ *Re-enrich* ({elapsed}s) ow_cache:{r['ow_cache']} weekly:{r['weekly']} monthly:{r['monthly']}")
        return True
    except Exception as e:
        _reenrich_pending = True
        logger.error(f"Re-enrich failed: {e}")
        _tg(f"❌ *Re-enrich* : `{e}` — retry à 2h+")
        return False


def _reenrich_groq_to_better():
    global _reenrich_pending
    from services.reenrich_service import _freetheai_ok
    if not _freetheai_ok():
        _reenrich_pending = True
        _tg("⏸ *Re-enrich* — FreeTheAI indisponible à 22h30. Retry à partir de 2h.")
        return
    _run_reenrich("22h30")


def _reenrich_retry():
    """Tourne toutes les heures (:35). Agit uniquement si _reenrich_pending et heure >= 2h."""
    global _reenrich_pending
    if not _reenrich_pending:
        return
    from datetime import datetime
    from services.reenrich_service import _freetheai_ok
    now = datetime.now()
    if now.hour < 2:
        return  # freeze jusqu'à 2h du matin
    if not _freetheai_ok():
        logger.info(f"[reenrich_retry] FreeTheAI indisponible à {now.strftime('%Hh%M')} — retry dans 1h")
        return  # freeze 1h naturel (prochain :35)
    _run_reenrich(f"retry {now.strftime('%Hh%M')}")


def _hourly_ow_regen():
    from services.overview_service import hourly_ow_regen
    try:
        refreshed = hourly_ow_regen()
        if refreshed:
            from datetime import datetime, timezone
            now_utc = datetime.now(timezone.utc).strftime("%H:%M UTC")
            _tg(f"✅ *News refresh* — {' · '.join(sorted(refreshed))} sortis du pool no\\_news · {now_utc} · 📍 cron `hourly_ow_regen` (:15)")
    except Exception as e:
        logger.error(f"Hourly news refresh failed: {e}")
        _tg(f"❌ *Hourly news refresh* (:15) : `{e}`")


def _run_cold_start_translate_fr():
    import time
    from services.cold_start_service import translate_pending_fr
    _tg("▶ *Cold start translate FR* (19h)")
    t = time.time()
    try:
        translate_pending_fr()
        _tg(f"✅ *Cold start translate FR terminé* ({int(time.time()-t)}s)")
    except Exception as e:
        logger.error(f"Cold start translate_fr failed: {e}")
        _tg(f"❌ *Cold start translate FR* : `{e}`")


def _purge_old_digests():
    import time
    from supabase import create_client
    from datetime import date, timedelta
    _tg("▶ *Purge DB* (lun 3h)")
    t = time.time()
    try:
        supa = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])
        cutoff_1y = (date.today() - timedelta(days=365)).isoformat()
        r2 = supa.table("bub_cache").delete().lt("date", cutoff_1y).execute()
        n2 = len(r2.data or [])
        logger.info(f"Purge bub_cache > 1 an : {n2} rows supprimées")
        _tg(f"✅ *Purge DB terminée* ({int(time.time()-t)}s)\nbub_cache: {n2}")
    except Exception as e:
        logger.error(f"Purge failed: {e}")
        _tg(f"❌ *Purge DB* : `{e}`")


app = FastAPI(title="StockVibe API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[os.getenv("FRONTEND_URL", "http://localhost:3000"), "https://*.vercel.app"],
    allow_credentials=True,
    allow_methods=["GET"],
    allow_headers=["*"],
)

app.include_router(stock.router, prefix="/api")
app.include_router(news.router, prefix="/api")
app.include_router(search.router, prefix="/api")
app.include_router(events.router, prefix="/api")
app.include_router(digest.router, prefix="/api")
app.include_router(articles.router, prefix="/api")
app.include_router(cold_start.router, prefix="/api")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/admin/hapuppy/unfreeze")
def hapuppy_unfreeze():
    import services.llm_service as _llm
    was_frozen = _llm._hapuppy_quota_skip_until is not None
    _llm._hapuppy_quota_skip_until = None
    return {"unfrozen": was_frozen}
