from fastapi import APIRouter, BackgroundTasks
from services.cold_start_service import (
    get_init_status, needs_cold_start, trigger_cold_start,
    PHASE_ETA_SECONDS, PHASE_LABEL, PHASE_LABEL_EN,
)
import os

router = APIRouter()


@router.get("/ticker/{ticker}/init-status")
async def init_status(ticker: str, lang: str = None):
    lang = lang or os.environ.get("LLM_LANG", "fr")
    status = get_init_status(ticker.upper())
    phase = status.get("phase", "uninitialized")
    current_status = status.get("status", "idle")

    labels = PHASE_LABEL_EN if lang == "en" else PHASE_LABEL
    eta = PHASE_ETA_SECONDS.get(phase, 0) if current_status == "running" else 0
    label = labels.get(phase, "") if current_status == "running" else ""

    return {
        "ticker": ticker.upper(),
        "phase": phase,
        "status": current_status,
        "pending_fr": status.get("pending_fr", False),
        "label": label,
        "eta_seconds": eta,
        "done": phase == "1y" and current_status == "done",
        "error": status.get("error"),
    }


@router.post("/ticker/{ticker}/initialize")
async def initialize(ticker: str, background_tasks: BackgroundTasks, lang: str = None):
    lang = lang or os.environ.get("LLM_LANG", "fr")
    ticker = ticker.upper()

    if not needs_cold_start(ticker):
        return {"ticker": ticker, "message": "already_initialized"}

    status = get_init_status(ticker)
    if status.get("status") == "running":
        return {"ticker": ticker, "message": "already_running", **status}

    background_tasks.add_task(trigger_cold_start, ticker, lang)

    return {
        "ticker": ticker,
        "message": "cold_start_triggered",
        "phase": "1m",
        "eta_seconds": PHASE_ETA_SECONDS["1m"],
        "label": PHASE_LABEL_EN.get("1m") if lang == "en" else PHASE_LABEL.get("1m"),
    }
