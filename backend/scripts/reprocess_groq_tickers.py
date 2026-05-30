"""
Re-process tickers qui ont été générés via Groq fallback (pas FreeTheAI).
Remet ai_summary_en à NULL en DB et retire du progress file → relance l'enrich EN.

Usage:
    python scripts/reprocess_groq_tickers.py --dry-run   # voir sans modifier
    python scripts/reprocess_groq_tickers.py             # reset + relance
"""
import os, sys, json, subprocess, argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parents[1] / ".env")
from supabase import create_client

GROQ_TICKERS = [
    "HAL","SLB","BKR","HES","OXY","MPC","PSX","VLO","FANG","CTRA","APA","AR","EQT","RRC",
    "GE","BA","RTX","LMT","NOC","GD","HII","TDG","HWM","SPR","CAT","DE","HON","ETN","EMR",
    "ROK","PH","ITW","CARR","OTIS","UPS","FDX","CHRW","EXPD","JBHT","XPO","ODFL","SAIA",
    "NSC","UNP","NEE","DUK","SO","AEP","EXC","SRE","D","PCG","XEL","ES","AMT","PLD",
    "EQIX","DLR","SPG","O","WY","PSA","AVB","EQR","ABNB",
    "EDEN.PA","SGRO.L","TKWY.AS","IF.MI","INVE-B.ST","ACKB.BR","SEM.LS",
    "9020.T","9107.T","0960.HK","0066.HK","009150.KS","010950.KS","POWERGRID.NS","MOMO"
]

PROGRESS_FILES = [
    Path(__file__).parent / "progress_en_us.json",
    Path(__file__).parent / "progress_en_eu.json",
    Path(__file__).parent / "progress_en_asia.json",
]

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    supa = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])

    print(f"{len(GROQ_TICKERS)} tickers à re-processer via FreeTheAI")

    if args.dry_run:
        print("DRY RUN — aucune modification")
        for t in GROQ_TICKERS:
            print(f"  {t}")
        return

    # 1. Reset ai_summary_en dans DB
    print("\n[1/3] Reset ai_summary_en dans stock_news_cache...")
    supa.table("stock_news_cache").update({"ai_summary_en": None, "ai_summary_24h_en": None}) \
        .in_("ticker", GROQ_TICKERS).execute()
    print(f"  ✓ {len(GROQ_TICKERS)} tickers resetés")

    # 2. Retirer des progress files
    print("\n[2/3] Retrait des progress files...")
    for pf in PROGRESS_FILES:
        if not pf.exists():
            continue
        data = json.loads(pf.read_text())
        before = len(data.get("enriched", []))
        data["enriched"] = [t for t in data.get("enriched", []) if t not in GROQ_TICKERS]
        after = len(data["enriched"])
        pf.write_text(json.dumps(data, indent=2))
        print(f"  {pf.name}: {before} → {after} ({before-after} retirés)")

    # 3. Relancer l'enrich EN par région (un seul subprocess par région
    #    pour que _freetheai_last_call soit partagé en mémoire)
    print("\n[3/3] Relance enrich EN par région...")
    env = os.environ.copy()
    env["LLM_LANG"] = "en"
    env["FREETHEAI_MIN_INTERVAL"] = "6"
    env["LLM_INTER_CALL_DELAY"] = "6"

    script = Path(__file__).parent / "batch_fill_db.py"

    for region, pf in [
        ("US",   Path(__file__).parent / "progress_en_us.json"),
        ("EU",   Path(__file__).parent / "progress_en_eu.json"),
        ("ASIA", Path(__file__).parent / "progress_en_asia.json"),
    ]:
        print(f"\n  ── {region} ──")
        subprocess.run([
            sys.executable, str(script),
            "--phase", "enrich",
            "--region", region,
            "--lang", "en",
            "--progress-file", str(pf),
        ], env=env)

    print("\n✅ Re-process terminé")

if __name__ == "__main__":
    main()
