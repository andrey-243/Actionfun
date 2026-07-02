"""
zen_watcher.py — Probe ZenLLM free models toutes les 5 min.
Cycle : probe → UP → batch → si zen tombe (N échecs consécutifs) → kill batch → probe → UP → reprise --empty-only → repeat

Usage :
  python scripts/zen_watcher.py
  nohup python scripts/zen_watcher.py > /tmp/zen_watcher.log 2>&1 &
"""
import argparse
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
PROBE_INTERVAL = 300   # 5 min entre probes
ZEN_FAIL_ABORT = 3     # nb d'échecs LLM consécutifs → kill batch

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
                timeout=120,
            )
            logger.info(f"  probe {model} → {r.status_code}")
            if r.status_code == 200:
                return model
            if r.status_code == 429:
                time.sleep(5)
        except Exception as e:
            logger.info(f"  probe {model} → timeout/err ({e})")
    return None


def _run_region(region: str, empty_only: bool, cascade: str) -> str:
    """
    Lance batch bubbles pour une région. Monitor stdout ligne par ligne.
    Kill le process si ZEN_FAIL_ABORT échecs LLM consécutifs.
    Retourne "ok" | "zen_down" | "error".
    """
    cmd = [PYTHON, str(BATCH_SCRIPT), "--phase", "bubbles", "--region", region, "--lang", "en", "--cascade", cascade]
    if empty_only:
        cmd.append("--empty-only")

    proc = subprocess.Popen(
        cmd,
        cwd=Path(__file__).resolve().parents[1],
        env={**os.environ, "BUBBLES_WORKERS": "1"},
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    consecutive_failures = 0

    for line in proc.stdout:
        line = line.rstrip()
        logger.info(line)

        if "LLM failed" in line:
            consecutive_failures += 1
            if consecutive_failures >= ZEN_FAIL_ABORT:
                logger.warning(f"  {consecutive_failures} bubbles LLM failed consécutifs — kill batch {region}")
                proc.kill()
                proc.wait()
                return "zen_down"
        elif "ZenLLM" not in line:
            consecutive_failures = 0

    proc.wait()
    return "ok" if proc.returncode == 0 else "error"


def _launch_bubs(model: str, empty_only: bool, cascade: str, regions: list[str]) -> bool:
    """
    Lance le batch sur les régions demandées.
    Retourne True si toutes OK, False si zen est tombé en cours de route.
    """
    label = "(reprise)" if empty_only else "(full)"
    _notify_telegram(f"ZenLLM UP — {model}\nLancement BUB {label} {' / '.join(regions)} (EN)")

    for region in regions:
        logger.info(f"--- BUB {region} {label} [{cascade}] ---")
        result = _run_region(region, empty_only, cascade)
        if result == "zen_down":
            logger.warning(f"ZenLLM tombé pendant BUB {region} — arrêt batch")
            _notify_telegram(f"ZenLLM DOWN pendant BUB {region} — retour probe dans {PROBE_INTERVAL // 60} min")
            return False
        status = "OK" if result == "ok" else "exit error"
        logger.info(f"BUB {region} : {status}")
        _notify_telegram(f"BUB {region} {label} — {status}")

    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Watcher ZenLLM — relance BUB batch dès que les modèles sont UP")
    parser.add_argument("--cascade", type=str, default="zen",
                        help="Cascade LLM à utiliser (zen, promo, bub_batch…). Défaut: zen")
    parser.add_argument("--region", type=str, default="US,EU,ASIA",
                        help="Régions à traiter, séparées par virgule. Défaut: US,EU,ASIA")
    args = parser.parse_args()

    regions = [r.strip().upper() for r in args.region.split(",")]

    if not ZENLLM_KEY:
        logger.error("ZENLLM_API_KEY manquant")
        sys.exit(1)

    logger.info(f"Probe ZenLLM toutes les {PROBE_INTERVAL // 60} min — {ZEN_MODELS} | cascade={args.cascade} | régions={regions}")
    first_run = True
    while True:
        model = _probe()
        if model:
            logger.info(f"ZenLLM UP : {model}")
            success = _launch_bubs(model, empty_only=not first_run, cascade=args.cascade, regions=regions)
            first_run = False
            if success:
                logger.info("Batch complet — reprobe dans 5 min pour eventuels trous")
            else:
                logger.info(f"Zen tombé — reprobe dans {PROBE_INTERVAL // 60} min")
            time.sleep(PROBE_INTERVAL)
        else:
            logger.info(f"ZenLLM down — retry dans {PROBE_INTERVAL // 60} min")
            time.sleep(PROBE_INTERVAL)


if __name__ == "__main__":
    main()
