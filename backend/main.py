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
        hour=18,
        minute=30,
        id="eod_digest_email",
    )
    scheduler.add_job(
        _run_weekly_condensation,
        trigger="cron",
        day_of_week="mon",
        hour=3,
        minute=30,
        id="weekly_condensation",
    )
    scheduler.add_job(
        _run_monthly_condensation,
        trigger="cron",
        day=1,
        hour=4,
        minute=0,
        id="monthly_condensation",
    )
    scheduler.add_job(
        _run_yearly_condensation,
        trigger="cron",
        month=1,
        day=1,
        hour=5,
        minute=0,
        id="yearly_condensation",
    )
    scheduler.add_job(
        _run_yahoo_accumulation,
        trigger="cron",
        hour="*/3",
        minute=0,
        id="yahoo_accumulation",
    )
    scheduler.add_job(
        _run_finnhub_refresh,
        trigger="cron",
        hour="*/6",
        minute=30,
        id="finnhub_refresh",
    )
    scheduler.add_job(
        _run_hourly_news_refresh,
        trigger="cron",
        minute=15,
        id="hourly_news_refresh",
    )
    scheduler.add_job(
        _run_cold_start_translate_fr,
        trigger="cron",
        hour=19,
        minute=0,
        id="cold_start_translate_fr",
    )
    scheduler.add_job(
        _run_reenrich,
        trigger="cron",
        hour=22,
        minute=30,
        id="reenrich_groq_to_freetheai",
    )
    scheduler.start()
    logger.info("Crons démarrés — email 18h30, weekly lun 3h30, monthly 1er 4h, yearly 1er jan 5h, yahoo */3h, finnhub */6h, cold_start FR 19h, re-enrich 22h30, hourly_news :15")
    yield
    scheduler.shutdown()


def _run_digest_email():
    import time
    from services.digest_service import run_eod_digest_email
    _tg("▶ *EOD Digest — envoi email* (18h30)")
    t = time.time()
    try:
        run_eod_digest_email()
        _tg(f"✅ *EOD Digest — email envoyé* ({int(time.time()-t)}s)")
    except Exception as e:
        logger.error(f"EOD digest email failed: {e}")
        _tg(f"❌ *EOD Digest — email* : `{e}`")


def _run_weekly_condensation():
    import time
    from services.breakers_articles_service import run_weekly_condensation
    _tg("▶ *Weekly condensation* (lun 3h30)")
    t = time.time()
    try:
        run_weekly_condensation()
        _tg(f"✅ *Weekly condensation terminée* ({int(time.time()-t)}s)")
    except Exception as e:
        logger.error(f"Weekly condensation failed: {e}")
        _tg(f"❌ *Weekly condensation* : `{e}`")


def _run_monthly_condensation():
    import time
    from services.breakers_articles_service import run_monthly_condensation
    _tg("▶ *Monthly condensation* (1er du mois 4h)")
    t = time.time()
    try:
        run_monthly_condensation()
        _tg(f"✅ *Monthly condensation terminée* ({int(time.time()-t)}s)")
    except Exception as e:
        logger.error(f"Monthly condensation failed: {e}")
        _tg(f"❌ *Monthly condensation* : `{e}`")


def _run_yearly_condensation():
    import time
    from services.breakers_articles_service import run_yearly_condensation
    _tg("▶ *Yearly condensation* (1er jan 5h)")
    t = time.time()
    try:
        run_yearly_condensation()
        _tg(f"✅ *Yearly condensation terminée* ({int(time.time()-t)}s)")
    except Exception as e:
        logger.error(f"Yearly condensation failed: {e}")
        _tg(f"❌ *Yearly condensation* : `{e}`")


def _run_yahoo_accumulation():
    import time
    from services.breakers_articles_service import run_yahoo_accumulation
    _tg("▶ *Yahoo accumulation* (*/3h)")
    t = time.time()
    try:
        results = run_yahoo_accumulation()
        detail = " · ".join(f"{tk} +{n}" for tk, n in sorted(results.items())) if results else "0 nouveaux articles"
        _tg(f"✅ *Yahoo accumulation terminée* ({int(time.time()-t)}s)\n{detail}")
    except Exception as e:
        logger.error(f"Yahoo accumulation failed: {e}")
        _tg(f"❌ *Yahoo accumulation* : `{e}`")


def _run_finnhub_refresh():
    import time
    from services.breakers_articles_service import run_finnhub_refresh
    _tg("▶ *Finnhub refresh* (*/6h)")
    t = time.time()
    try:
        results = run_finnhub_refresh()
        detail = " · ".join(f"{tk} {n}" for tk, n in sorted(results.items())) if results else "aucun ticker"
        _tg(f"✅ *Finnhub refresh terminé* ({int(time.time()-t)}s)\n{detail}")
    except Exception as e:
        logger.error(f"Finnhub refresh failed: {e}")
        _tg(f"❌ *Finnhub refresh* : `{e}`")


def _run_reenrich():
    import time
    from services.reenrich_service import run_reenrich
    _tg("▶ *Re-enrich Groq→FreeTheAI* (22h30)")
    t = time.time()
    try:
        run_reenrich()
        _tg(f"✅ *Re-enrich terminé* ({int(time.time()-t)}s)")
    except Exception as e:
        logger.error(f"Re-enrich failed: {e}")
        _tg(f"❌ *Re-enrich* : `{e}`")


def _run_hourly_news_refresh():
    from services.overview_service import run_hourly_news_refresh
    try:
        refreshed = run_hourly_news_refresh()
        if refreshed:
            from datetime import datetime, timezone
            now_utc = datetime.now(timezone.utc).strftime("%H:%M UTC")
            _tg(f"✅ *News refresh* — {' · '.join(sorted(refreshed))} sortis du pool no\\_news · {now_utc} · 📍 cron `hourly_news_refresh` (:15)")
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
        r2 = supa.table("event_causes").delete().lt("date", cutoff_1y).execute()
        n2 = len(r2.data or [])
        logger.info(f"Purge event_causes > 1 an : {n2} rows supprimées")
        _tg(f"✅ *Purge DB terminée* ({int(time.time()-t)}s)\nevent_causes: {n2}")
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
