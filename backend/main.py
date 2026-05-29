from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from apscheduler.schedulers.background import BackgroundScheduler
from dotenv import load_dotenv
import logging
import os

load_dotenv()

from routers import stock, news, search, events, digest, articles

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler = BackgroundScheduler(timezone="Europe/Paris")
    scheduler.add_job(
        _run_digest_generation,
        trigger="cron",
        hour=18,
        minute=0,
        id="eod_digest_generation",
    )
    scheduler.add_job(
        _run_digest_email,
        trigger="cron",
        hour=18,
        minute=30,
        id="eod_digest_email",
    )
    scheduler.add_job(
        _purge_old_digests,
        trigger="cron",
        day_of_week="mon",
        hour=3,
        minute=0,
        id="purge_digests",
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
        hour=2,
        minute=0,
        id="yahoo_accumulation",
    )
    scheduler.start()
    logger.info("Crons démarrés — digest gen 18h + email 18h30, weekly lun 3h30, monthly 1er 4h, yearly 1er jan 5h, yahoo 2h")
    yield
    scheduler.shutdown()


def _run_digest_generation():
    from services.digest_service import run_eod_digest_generation
    try:
        run_eod_digest_generation()
    except Exception as e:
        logger.error(f"EOD digest generation failed: {e}")


def _run_digest_email():
    from services.digest_service import run_eod_digest_email
    try:
        run_eod_digest_email()
    except Exception as e:
        logger.error(f"EOD digest email failed: {e}")


def _run_weekly_condensation():
    from services.period_articles_service import run_weekly_condensation
    try:
        run_weekly_condensation()
    except Exception as e:
        logger.error(f"Weekly condensation failed: {e}")


def _run_monthly_condensation():
    from services.period_articles_service import run_monthly_condensation
    try:
        run_monthly_condensation()
    except Exception as e:
        logger.error(f"Monthly condensation failed: {e}")


def _run_yearly_condensation():
    from services.period_articles_service import run_yearly_condensation
    try:
        run_yearly_condensation()
    except Exception as e:
        logger.error(f"Yearly condensation failed: {e}")


def _run_yahoo_accumulation():
    from services.period_articles_service import run_yahoo_accumulation
    try:
        run_yahoo_accumulation()
    except Exception as e:
        logger.error(f"Yahoo accumulation failed: {e}")


def _purge_old_digests():
    from supabase import create_client
    from datetime import date, timedelta
    try:
        supa = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])
        cutoff = (date.today() - timedelta(days=30)).isoformat()
        result = supa.table("daily_digest").delete().lt("date", cutoff).execute()
        logger.info(f"Purge digests > 30J (cutoff {cutoff}) : {len(result.data or [])} rows supprimées")
    except Exception as e:
        logger.error(f"Purge failed: {e}")


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


@app.get("/health")
def health():
    return {"status": "ok"}
