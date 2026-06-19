"""
zen_watcher.py — Probe ZenLLM free models toutes les 5 min.
Cycle : probe → UP → batch → probe → UP → batch --empty-only → repeat
S'arrête quand il n'y a plus rien à traiter (0 ticker sur les 3 régions).

Usage :
  python scripts/zen_watcher.py
  nohup python scripts/zen_watcher.py > /tmp/zen_watcher.log 2>&1 &
"""
import os
import sys
import time
import subprocess
import logging
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger(__name__)

ZENLLM_URL     = "https://api.zenllm.org/v1/chat/completions"
ZENLLM_KEY     = os.environ.get("ZENLLM_API_KEY", "")
ZEN_MODELS     = ["free/gpt-5.5", "free/claude-sonnet-4.6"]
PROBE_INTERVAL = 300  # 5 min

BATCH_SCRIPT = Path(__file__).resolve().parent / "batch_fill_db.py"
PYTHON       = sys.executable


def _notify_telegram(msg: str) -> None:
    token   = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        return
    try:
        httpx.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": msg},
            timeout=5,
        )
    except Exception:
        pass


def _probe() -> str | None:
    """Retourne le nom du premier modèle qui répond 200, ou None."""
    for model in ZEN_MODELS:
        try:
            r = httpx.post(
                ZENLLM_URL,
                headers={"Authorization": f"Bearer {ZENLLM_KEY}", "Content-Type": "application/json"},
                json={"model": model, "messages": [{"role": "user", "content": "Reply OK"}], "max_tokens": 5, "temperature": 1},
                timeout=20,
            )
            if r.status_code == 200:
                return model
        except Exception:
            pass
    return None


def _launch_bubs(model: str, empty_only: bool) -> int:
    """Lance le batch sur les 3 régions. Retourne le nb total de régions avec tickers traités."""
    flag = "--empty-only" if empty_only else ""
    label = "(reprise)" if empty_only else "(full)"
    _notify_telegram(f"ZenLLM UP — {model}\nLancement BUB {label} US / EU / ASIA (EN)")
    regions_done = 0
    for region in ["US", "EU", "ASIA"]:
        logger.info(f"Lancement BUB {region} {label}...")
        cmd = [PYTHON, str(BATCH_SCRIPT), "--phase", "bubbles", "--region", region, "--lang", "en"]
        if empty_only:
            cmd.append("--empty-only")
        result = subprocess.run(
            cmd,
            cwd=Path(__file__).resolve().parents[1],
            env={**os.environ, "BUBBLES_WORKERS": "1"},
        )
        status = "OK" if result.returncode == 0 else f"exit {result.returncode}"
        logger.info(f"BUB {region} : {status}")
        _notify_telegram(f"BUB {region} {label} — {status}")
        if result.returncode == 0:
            regions_done += 1
    return regions_done


def main() -> None:
    if not ZENLLM_KEY:
        logger.error("ZENLLM_API_KEY manquant")
        sys.exit(1)

    logger.info(f"Probe ZenLLM toutes les {PROBE_INTERVAL // 60} min — {ZEN_MODELS}")
    first_run = True
    while True:
        model = _probe()
        if model:
            logger.info(f"ZenLLM UP : {model}")
            _launch_bubs(model, empty_only=not first_run)
            first_run = False
            logger.info("Batch termine — reprobe dans 5 min pour eventuels trous")
            time.sleep(PROBE_INTERVAL)
        else:
            logger.info(f"ZenLLM down — retry dans {PROBE_INTERVAL // 60} min")
            time.sleep(PROBE_INTERVAL)


if __name__ == "__main__":
    main()
