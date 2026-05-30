"""
Script master — enchaîne les enrichs overnight:
  1. Attend la fin du FR enrich US en cours
  2. EN enrich US   (ai_summary_en, pas de traduction FR — FR existe déjà)
  3. EN+FR enrich EU  (génère EN + traduit FR)
  4. EN+FR enrich ASIA (génère EN + traduit FR)

Ctrl+C à tout moment = arrêt propre (le run en cours sauvegarde son progress)
Relancer le script = reprend là où ça s'est arrêté.

Usage:
    python scripts/enrich_tonight.py
    python scripts/enrich_tonight.py --skip-wait   # si FR enrich US déjà terminé
"""
import subprocess
import sys
import signal
import time
import json
import argparse
from pathlib import Path
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger(__name__)

SCRIPT_DIR = Path(__file__).parent
BATCH_SCRIPT = SCRIPT_DIR / "batch_fill_db.py"
FR_PROGRESS = SCRIPT_DIR / "batch_progress.json"

# Nombre de tickers US à enrichir en FR (pour détecter la fin)
try:
    sys.path.insert(0, str(SCRIPT_DIR.parent))
    from data.tickers import get_by_region
    US_COUNT = len(get_by_region("US"))
except Exception:
    US_COUNT = 260  # fallback

current_proc = None


def handle_sigint(sig, frame):
    signal.signal(signal.SIGINT, signal.SIG_DFL)  # restaure le handler par défaut
    if current_proc and current_proc.poll() is None:
        logger.info("\n⏹ Ctrl+C — arrêt du run en cours (progress sauvegardé)...")
        current_proc.send_signal(signal.SIGINT)
        current_proc.wait()
    logger.info("Arrêt. Relance le script pour continuer.")
    sys.exit(0)


signal.signal(signal.SIGINT, handle_sigint)


def run(label: str, args: list[str]) -> bool:
    """Lance une commande. Retourne True si succès, False si échec."""
    global current_proc
    cmd = [sys.executable, str(BATCH_SCRIPT)] + args
    logger.info(f"\n{'='*60}")
    logger.info(f"▶ {label}")
    logger.info(f"  Commande : {' '.join(cmd)}")
    logger.info(f"{'='*60}")
    current_proc = subprocess.Popen(cmd)
    ret = current_proc.wait()
    current_proc = None
    if ret != 0:
        logger.error(f"✗ ÉCHEC (code {ret}) — {label}")
        logger.error("Arrêt. Corrige l'erreur et relance le script.")
        sys.exit(ret)
    return True


def wait_for_fr_us_enrich():
    logger.info(f"⏳ En attente de la fin du FR enrich US ({US_COUNT} tickers attendus)...")
    while True:
        if FR_PROGRESS.exists():
            data = json.loads(FR_PROGRESS.read_text())
            done = len(data.get("enriched", []))
            total_done = done
            logger.info(f"  Progress FR US : {total_done}/{US_COUNT} tickers enrichis")
            if total_done >= US_COUNT:
                logger.info("✅ FR enrich US terminé !")
                return
        time.sleep(60)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-wait", action="store_true",
                        help="Ne pas attendre la fin du FR enrich US (déjà terminé)")
    args = parser.parse_args()

    if not args.skip_wait:
        wait_for_fr_us_enrich()
    else:
        logger.info("--skip-wait : on suppose le FR enrich US terminé")

    import os as _os
    # 10 RPM FreeTheAI → 6s min entre appels (géré dans llm_service._freetheai_available)
    _os.environ["FREETHEAI_MIN_INTERVAL"] = "6"
    # Délai inter-calls dans _generate_and_cache (brutalise → summary_24h)
    _os.environ["LLM_INTER_CALL_DELAY"] = "6"

    # ── Pass 1 : EN enrich — tous les tickers ───────────────────────────────────
    run(
        "EN enrich US — génère ai_summary_en",
        ["--phase", "enrich", "--region", "US", "--lang", "en",
         "--progress-file", str(SCRIPT_DIR / "progress_en_us.json")]
    )
    run(
        "EN enrich EU — génère ai_summary_en",
        ["--phase", "enrich", "--region", "EU", "--lang", "en",
         "--progress-file", str(SCRIPT_DIR / "progress_en_eu.json")]
    )
    run(
        "EN enrich ASIA — génère ai_summary_en",
        ["--phase", "enrich", "--region", "ASIA", "--lang", "en",
         "--progress-file", str(SCRIPT_DIR / "progress_en_asia.json")]
    )

    # ── Pass 2 : FR translation — tous les tickers sans ai_summary ──────────────
    run(
        "FR translation EU — traduit ai_summary_en → ai_summary",
        ["--phase", "translate-fr", "--region", "EU",
         "--progress-file", str(SCRIPT_DIR / "progress_fr_eu.json")]
    )
    run(
        "FR translation ASIA — traduit ai_summary_en → ai_summary",
        ["--phase", "translate-fr", "--region", "ASIA",
         "--progress-file", str(SCRIPT_DIR / "progress_fr_asia.json")]
    )
    # US : 211 tickers ont déjà le FR natif, seulement les 49 restants seront traduits
    run(
        "FR translation US — traduit les 49 tickers sans ai_summary",
        ["--phase", "translate-fr", "--region", "US",
         "--progress-file", str(SCRIPT_DIR / "progress_fr_us.json")]
    )

    logger.info("\n✅ Tous les enrichs terminés !")


if __name__ == "__main__":
    main()
