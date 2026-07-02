"""
translate_bulk.py — Traduction bulk EN→FR.

Usage :
  python scripts/translate_bulk.py --region EU
  python scripts/translate_bulk.py --region US --dry-run
  python scripts/translate_bulk.py --region ASIA --only bub,y,m
  python scripts/translate_bulk.py --ticker AAPL
  python scripts/translate_bulk.py --since 2h   # uniquement les lignes récentes
  python scripts/translate_bulk.py --since 30m  # 30 minutes
  python scripts/translate_bulk.py --since 3d   # 3 jours
  python scripts/translate_bulk.py --batch 10   # taille batch (défaut: 20)
  python scripts/translate_bulk.py --cascade zen  # override cascade LLM (défaut: promo)
  python scripts/translate_bulk.py              # toutes régions

--region  : US | EU | ASIA (défaut: toutes)
--ticker  : ticker unique (override --region)
--since   : filtre sur cached_at/condensed_at — ex: 30m, 2h, 1d (défaut: tout)
--only    : bub,y,m,w (défaut: tous dans cet ordre)
--cascade : cascade LLM (promo, zen, bub_batch…) — défaut: promo

Ordre de priorité : BUB → Y breakers → M breakers → W breakers
"""

import argparse
import logging
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parents[1] / ".env")

import httpx
from supabase import create_client
from data.tickers import get_by_region, ALL_TICKERS
from services.llm_service import llm_call, _gemini_daily_exhausted, _N_GEMINI
from services.translation_service import TRANSLATE_PROMPT

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
log = logging.getLogger("translate_bulk")

_CASCADE = "promo"


def _telegram(msg: str) -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        return
    html = re.sub(r'\*([^*]+)\*', r'<b>\1</b>', msg)
    html = re.sub(r'(?<!\w)_([^_]+)_(?!\w)', r'<i>\1</i>', html)
    html += "\n📍 <i>translate_bulk</i>"
    try:
        httpx.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": html, "parse_mode": "HTML"},
            timeout=8,
        )
    except Exception as e:
        log.warning(f"TG send failed: {e}")

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_KEY"]

ZEN_PROBE_INTERVAL = 300  # 5 min entre probes
ZEN_MODELS = ["free/gpt-5.5", "free/claude-sonnet-4.6"]
ZENLLM_URL = "https://api.zenllm.org/v1/chat/completions"


def _probe_zen() -> str | None:
    """Retourne le nom du premier modèle ZenLLM qui répond 200, ou None."""
    key = os.environ.get("ZENLLM_API_KEY", "")
    if not key:
        return None
    for model in ZEN_MODELS:
        try:
            r = httpx.post(
                ZENLLM_URL,
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json={"model": model, "messages": [{"role": "user", "content": "Reply OK"}], "max_tokens": 5, "temperature": 1},
                timeout=120,
            )
            if r.status_code == 200:
                return model
        except Exception:
            pass
    return None


def _wait_zen_up(label: str, done: int, total: int) -> None:
    """Bloque jusqu'à ce que ZenLLM réponde. Probe toutes les 5min."""
    _telegram(
        f"🟡 *translate_bulk {label} — ZenLLM down*\n"
        f"Progression : {done}/{total} ({done * 100 // total if total else 0}%)\n"
        f"Probe toutes les {ZEN_PROBE_INTERVAL // 60} min — reprise automatique."
    )
    log.warning(f"  {label} — ZenLLM down, probe toutes les {ZEN_PROBE_INTERVAL // 60} min")
    while True:
        time.sleep(ZEN_PROBE_INTERVAL)
        model = _probe_zen()
        if model:
            log.info(f"  ZenLLM UP : {model} — reprise {label}")
            _telegram(f"🟢 *ZenLLM UP — {model}*\nReprise translate_bulk {label} ({done}/{total})")
            return
        log.info(f"  ZenLLM toujours down — retry dans {ZEN_PROBE_INTERVAL // 60} min")

PAGE_SIZE = 1000

_SINCE_RE = re.compile(r"^(\d+)(m|h|d)$")


def _parse_since(value: str) -> str:
    m = _SINCE_RE.match(value.strip())
    if not m:
        raise argparse.ArgumentTypeError(f"Format --since invalide : '{value}' (ex: 30m, 2h, 3d)")
    n, unit = int(m.group(1)), m.group(2)
    delta = {"m": timedelta(minutes=n), "h": timedelta(hours=n), "d": timedelta(days=n)}[unit]
    cutoff = datetime.now(timezone.utc) - delta
    return cutoff.isoformat()


def _translate_one(text: str, cascade: str) -> tuple[str, str]:
    """Retourne (contenu traduit, source LLM)."""
    _timeout = 60 if cascade == "bub_batch" else 30
    resp = llm_call(
        [{"role": "user", "content": TRANSLATE_PROMPT.format(text=text)}],
        max_tokens=2000,
        temperature=0.1,
        timeout=_timeout,
        caller="translate_bulk",
        cascade=cascade,
    )
    content = resp["choices"][0]["message"]["content"].strip()
    source = resp.get("_llm_source", "?")
    return content, source


def _gemini_available() -> bool:
    """True si au moins une clé Gemini n'est pas daily-exhausted."""
    return not all(_gemini_daily_exhausted[:_N_GEMINI])


def _translate_batch(texts: list[str], workers: int = 10) -> list[tuple[str, str] | None]:
    """
    Pass 1 : FreeTheAI (item 0 réservé) + Gemini (items 1..N) en parallèle.
             Si Gemini daily-exhausted : tous les items sur bub_batch (FreeTheAI).
    Pass 2 : FreeTheAI séquentiel pour les échecs restants.
    """
    results: list[tuple[str, str] | None] = [None] * len(texts)
    if not texts:
        return results

    gemini_ok = _gemini_available()
    if not gemini_ok:
        log.info("  Gemini daily-exhausted : tous les items → bub_batch (FreeTheAI)")

    with ThreadPoolExecutor(max_workers=workers) as ex:
        if gemini_ok:
            # FreeTheAI : item 0 garanti, tourne en parallèle avec Gemini
            fte_fut = ex.submit(_translate_one, texts[0], "bub_batch")

            # Gemini : items 1..N
            gemini_futs = {
                ex.submit(_translate_one, t, _CASCADE): i
                for i, t in enumerate(texts[1:], start=1)
            }
            for fut in as_completed(gemini_futs):
                idx = gemini_futs[fut]
                try:
                    results[idx] = fut.result()
                except Exception as e:
                    log.warning(f"  ✗ traduction idx={idx} [{_CASCADE}] : {e}")

            try:
                results[0] = fte_fut.result()
            except Exception as e:
                log.warning(f"  ✗ traduction idx=0 [bub_batch] : {e}")
        else:
            # Gemini exhausted : tous les items en parallèle via FreeTheAI
            all_futs = {
                ex.submit(_translate_one, t, "bub_batch"): i
                for i, t in enumerate(texts)
            }
            for fut in as_completed(all_futs):
                idx = all_futs[fut]
                try:
                    results[idx] = fut.result()
                except Exception as e:
                    log.warning(f"  ✗ traduction idx={idx} [bub_batch] : {e}")

    # Pass 2 — FreeTheAI séquentiel pour les échecs restants
    failed = [(i, texts[i]) for i, r in enumerate(results) if r is None]
    if failed:
        log.info(f"  FreeTheAI retry : {len(failed)} items")
        for idx, text in failed:
            try:
                results[idx] = _translate_one(text, "bub_batch")
            except Exception as e:
                log.warning(f"  ✗ traduction idx={idx} [bub_batch] : {e}")

    return results


def _fetch_all(
    supa,
    table: str,
    select: str,
    en_col: str,
    fr_col: str,
    tickers: list[str] | None,
    since_col: str | None = None,
    since_iso: str | None = None,
) -> list[dict]:
    ticker_chunks = (
        [tickers[i:i + 100] for i in range(0, len(tickers), 100)]
        if tickers and len(tickers) > 100
        else [tickers]
    )
    rows = []
    for chunk in ticker_chunks:
        offset = 0
        while True:
            q = (
                supa.table(table)
                .select(select)
                .not_.is_(en_col, "null")
                .is_(fr_col, "null")
            )
            if chunk:
                q = q.in_("ticker", chunk)
            if since_col and since_iso:
                q = q.gte(since_col, since_iso)
            res = q.range(offset, offset + PAGE_SIZE - 1).execute()
            batch = res.data or []
            rows.extend(batch)
            if len(batch) < PAGE_SIZE:
                break
            offset += PAGE_SIZE
    return rows


def _fmt_elapsed(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.0f}s"
    return f"{int(seconds // 60)}m{int(seconds % 60):02d}s"


def _log_batch(label: str, done: int, total: int, batch_err: int, sources: list[str], tickers_str: str, elapsed: float) -> None:
    pct = done * 100 // total if total else 0
    src = sources[0] if len(set(sources)) == 1 else ",".join(sorted(set(sources)))
    err_str = f" | ✗ {batch_err}" if batch_err else ""
    speed = done / elapsed * 60 if elapsed > 0 else 0
    log.info(f"  {label} {done}/{total} ({pct}%) [{src}] {_fmt_elapsed(elapsed)} ~{speed:.0f}/min{err_str} — {tickers_str}")


ZEN_FAIL_ABORT = 3  # échecs consécutifs → abort (même logique que zen_watcher)


def _process_step(
    label: str,
    supa,
    rows: list[dict],
    texts_fn,
    update_fn,
    batch_size: int,
    workers: int,
) -> int:
    """Logique commune : traduction par batch + logs + notifs TG."""
    total = len(rows)
    notify_at = {max(1, total * p // 4) for p in range(1, 4)}  # 25%, 50%, 75%
    done = 0
    errors = 0
    all_sources: list[str] = []
    consecutive_failures = 0
    t0 = time.monotonic()

    for i in range(0, total, batch_size):
        chunk = rows[i : i + batch_size]
        translated = _translate_batch(texts_fn(chunk), workers)
        batch_err = sum(1 for t in translated if t is None)
        sources = [src for res in translated if res is not None for _, src in [res]]
        all_sources.extend(sources)
        for row, res in zip(chunk, translated):
            if res is not None:
                fr, _ = res
                update_fn(supa, row, fr)
        done += len(chunk) - batch_err
        errors += batch_err
        tickers_str = " ".join(r["ticker"] for r in chunk)
        _log_batch(label, done, total, batch_err, sources, tickers_str, time.monotonic() - t0)

        if batch_err == len(chunk):
            consecutive_failures += batch_err
        else:
            consecutive_failures = 0

        exhausted = (
            (_CASCADE == "zen" and consecutive_failures >= ZEN_FAIL_ABORT) or
            (_CASCADE != "zen" and batch_err >= len(chunk) * 3 // 4)
        )
        if exhausted:
            if _CASCADE == "zen":
                consecutive_failures = 0
                _wait_zen_up(label, done, total)
            else:
                _telegram(
                    f"🔴 *translate_bulk {label} — Gemini exhausted*\n"
                    f"✗ {batch_err}/{len(chunk)} échecs sur ce batch\n"
                    f"Progression : {done}/{total} ({done * 100 // total if total else 0}%)\n"
                    f"Les rows restantes seront retentées au prochain run."
                )
                log.warning(f"  {label} — Gemini exhausted ({batch_err}/{len(chunk)} échecs) — arrêt")
                break

        if done in notify_at:
            elapsed = time.monotonic() - t0
            eta = elapsed * (total - done) / done if done else 0
            src_str = all_sources[0] if len(set(all_sources)) == 1 else ",".join(sorted(set(all_sources)))
            _telegram(
                f"📊 *translate_bulk {label} {done * 100 // total}%*\n"
                f"🎯 {done}/{total} lignes\n"
                f"⏱ {_fmt_elapsed(elapsed)} · ~{_fmt_elapsed(eta)} restant\n"
                f"🤖 [{src_str}]" + (f"\n⚠ {errors} erreurs" if errors else "")
            )

    elapsed = time.monotonic() - t0
    if errors:
        log.warning(f"  {label} total erreurs : {errors}/{total}")
    log.info(f"  {label} done : {done}/{total} en {_fmt_elapsed(elapsed)}")
    return done


def process_bub(supa, batch_size: int, dry: bool, tickers: list[str] | None, since_iso: str | None, workers: int = 10) -> int:
    log.info("=== BUB (bub_cache) ===")
    rows = _fetch_all(supa, "bub_cache", "ticker,date,bub_en", "bub_en", "bub_fr", tickers, "cached_at", since_iso)
    log.info(f"  {len(rows)} lignes à traduire")
    if dry or not rows:
        return len(rows)

    def _update(supa, row, fr):
        supa.table("bub_cache").update({"bub_fr": fr}).eq("ticker", row["ticker"]).eq("date", row["date"]).execute()

    return _process_step("BUB", supa, rows, lambda chunk: [r["bub_en"] for r in chunk], _update, batch_size, workers)


def process_yearly(supa, batch_size: int, dry: bool, tickers: list[str] | None, since_iso: str | None, workers: int = 10) -> int:
    log.info("=== Y breakers (breaks_yearly) ===")
    rows = _fetch_all(supa, "breaks_yearly", "ticker,year,by_en", "by_en", "by_fr", tickers, "condensed_at", since_iso)
    log.info(f"  {len(rows)} lignes à traduire")
    if dry or not rows:
        return len(rows)

    def _update(supa, row, fr):
        supa.table("breaks_yearly").update({"by_fr": fr}).eq("ticker", row["ticker"]).eq("year", row["year"]).execute()

    return _process_step("Y", supa, rows, lambda chunk: [r["by_en"] for r in chunk], _update, batch_size, workers)


def process_monthly(supa, batch_size: int, dry: bool, tickers: list[str] | None, since_iso: str | None, workers: int = 10) -> int:
    log.info("=== M breakers (breaks_monthly) ===")
    rows = _fetch_all(supa, "breaks_monthly", "ticker,year_month,bm_en", "bm_en", "bm_fr", tickers, "condensed_at", since_iso)
    log.info(f"  {len(rows)} lignes à traduire")
    if dry or not rows:
        return len(rows)

    def _update(supa, row, fr):
        supa.table("breaks_monthly").update({"bm_fr": fr}).eq("ticker", row["ticker"]).eq("year_month", row["year_month"]).execute()

    return _process_step("M", supa, rows, lambda chunk: [r["bm_en"] for r in chunk], _update, batch_size, workers)


def process_weekly(supa, batch_size: int, dry: bool, tickers: list[str] | None, since_iso: str | None, workers: int = 10) -> int:
    log.info("=== W breakers (breaks_weekly) ===")
    rows = _fetch_all(supa, "breaks_weekly", "ticker,year_month,week_num,bw_en", "bw_en", "bw_fr", tickers, "condensed_at", since_iso)
    log.info(f"  {len(rows)} lignes à traduire")
    if dry or not rows:
        return len(rows)

    def _update(supa, row, fr):
        (
            supa.table("breaks_weekly")
            .update({"bw_fr": fr})
            .eq("ticker", row["ticker"])
            .eq("year_month", row["year_month"])
            .eq("week_num", row["week_num"])
            .execute()
        )

    return _process_step("W", supa, rows, lambda chunk: [r["bw_en"] for r in chunk], _update, batch_size, workers)


STEPS = [
    ("m",   process_monthly),
    ("y",   process_yearly),
    ("bub", process_bub),
    ("w",   process_weekly),
]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--region", choices=["US", "EU", "ASIA"], help="Filtre par région")
    parser.add_argument("--ticker", help="Ticker unique (override --region)")
    parser.add_argument("--since", metavar="DURATION", help="Ex: 30m, 2h, 3d — ne traduit que les lignes mises à jour depuis")
    parser.add_argument("--dry-run", action="store_true", help="Compte sans écrire")
    parser.add_argument("--only", default="", help="Tables : bub,y,m,w (défaut: tous)")
    parser.add_argument("--batch", type=int, default=20, help="Taille batch (défaut: 20)")
    parser.add_argument("--workers", type=int, default=10, help="Traductions en parallèle (défaut: 10)")
    parser.add_argument("--cascade", type=str, default="promo",
                        help="Cascade LLM (promo, zen, bub_batch…). Défaut: promo")
    args = parser.parse_args()

    global _CASCADE
    _CASCADE = args.cascade
    if _CASCADE == "zen" and args.workers != 1:
        log.info(f"Cascade LLM : {_CASCADE} — workers forcé à 1 (ZEN_MIN_INTERVAL={10}s)")
        args.workers = 1
    else:
        log.info(f"Cascade LLM : {_CASCADE}")

    since_iso = _parse_since(args.since) if args.since else None
    if since_iso:
        log.info(f"Filtre --since {args.since} : lignes >= {since_iso}")

    if args.ticker:
        tickers = [args.ticker.upper()]
        log.info(f"Mode ticker unique : {tickers[0]}")
    elif args.region:
        tickers = get_by_region(args.region)
        log.info(f"Région {args.region} : {len(tickers)} tickers")
    else:
        tickers = None
        log.info(f"Toutes régions : {len(ALL_TICKERS)} tickers")

    only = {s.strip() for s in args.only.split(",") if s.strip()} if args.only else None
    steps_active = [k for k, _ in STEPS if not only or k in only]

    supa = create_client(SUPABASE_URL, SUPABASE_KEY)

    scope_str = args.ticker or args.region or "toutes régions"
    if not args.dry_run:
        _telegram(
            f"🔄 *translate_bulk démarré*\n"
            f"Tables : {', '.join(steps_active).upper()} · cascade : {_CASCADE}\n"
            f"Scope : {scope_str}"
            + (f" · since {args.since}" if args.since else "")
        )

    t0_total = time.monotonic()
    total = 0
    for key, fn in STEPS:
        if only and key not in only:
            continue
        count = fn(supa, args.batch, args.dry_run, tickers, since_iso, args.workers)
        total += count

    elapsed_total = time.monotonic() - t0_total
    mode = "DRY-RUN" if args.dry_run else "DONE"
    log.info(f"=== {mode} | total traité: {total} en {_fmt_elapsed(elapsed_total)} ===")
    if not args.dry_run:
        _telegram(
            f"✅ *translate_bulk terminé*\n"
            f"⏱ {_fmt_elapsed(elapsed_total)}\n"
            f"🎯 {total} lignes traduites\n"
            f"Tables : {', '.join(steps_active).upper()} · {scope_str}"
        )


if __name__ == "__main__":
    main()
