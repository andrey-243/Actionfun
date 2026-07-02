"""
Stockage externe des champs articles vers Turso (libSQL edge DB).
Migration progressive depuis Supabase JSONB :
  Phase 1 (>1M)  : summary, dumb_en, dumb_fr
  Phase 2 (>7M)  : + url
  Phase 3 (>13M) : + headline, source

Ce qui reste dans Supabase après Phase 3 : id, datetime, is_related (image)

Table Turso :
    article_content (id TEXT PRIMARY KEY,
                     ticker TEXT, year_month TEXT,
                     url TEXT, summary TEXT, dumb_en TEXT, dumb_fr TEXT,
                     headline TEXT, source TEXT,
                     is_related INTEGER)

Vars d'env requises : TURSO_URL · TURSO_AUTH_TOKEN
Non activé tant que TURSO_URL n'est pas dans .env.
"""

import hashlib
import logging
import os

import httpx

logger = logging.getLogger(__name__)

# Champs stripés de Supabase par phase (cutoffs : 1M / 7M / 13M)
STRIP_PHASE1 = ("summary", "dumb_en", "dumb_fr")
STRIP_PHASE2 = STRIP_PHASE1 + ("url",)
STRIP_PHASE3 = STRIP_PHASE2 + ("headline", "source")

# Compat legacy (migrate_articles_cold.py l'importe encore)
HEAVY_FIELDS = STRIP_PHASE1


def _article_id(url: str) -> str:
    """ID stable 16-char depuis l'URL (SHA-1 tronqué). Identique Supabase et Turso."""
    if not url:
        return ""
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]


def _execute(stmts: list[dict]) -> list[dict]:
    """Envoie un batch de statements au pipeline Turso HTTP API."""
    url   = os.environ["TURSO_URL"].rstrip("/") + "/v2/pipeline"
    token = os.environ["TURSO_AUTH_TOKEN"]
    resp  = httpx.post(
        url,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"requests": [{"type": "execute", "stmt": s} for s in stmts]},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json().get("results", [])


def _arg(value: str) -> dict:
    return {"type": "text", "value": value or ""}


def _is_related_int(val) -> str | None:
    if val is True:
        return "1"
    if val is False:
        return "0"
    return None


def init_db() -> None:
    """Setup Turso : table + index. Silencieux si déjà existant."""
    _execute([{
        "sql": (
            "CREATE TABLE IF NOT EXISTS article_content ("
            "id TEXT NOT NULL PRIMARY KEY, "
            "ticker TEXT NOT NULL, "
            "year_month TEXT NOT NULL, "
            "url TEXT DEFAULT '', "
            "summary TEXT DEFAULT '', "
            "dumb_en TEXT DEFAULT '', "
            "dumb_fr TEXT DEFAULT '', "
            "headline TEXT DEFAULT '', "
            "source TEXT DEFAULT '', "
            "is_related INTEGER DEFAULT NULL"
            ")"
        )
    }])
    _execute([{
        "sql": "CREATE INDEX IF NOT EXISTS idx_art_ym ON article_content(ticker, year_month)"
    }])
    # Migrations silencieuses sur table existante (ancienne version sans id/headline/source)
    for col, typedef in [
        ("id",       "TEXT NOT NULL DEFAULT ''"),
        ("headline", "TEXT DEFAULT ''"),
        ("source",   "TEXT DEFAULT ''"),
    ]:
        try:
            _execute([{"sql": f"ALTER TABLE article_content ADD COLUMN {col} {typedef}"}])
        except Exception:
            pass
    logger.info("[turso] table article_content prête")


def write_heavy(ticker: str, year_month: str, articles: list[dict]) -> None:
    """
    Écrit tous les champs disponibles vers Turso.
    Génère l'id depuis url si absent dans le dict.
    INSERT OR REPLACE — idempotent.
    """
    stmts = []
    for a in articles:
        url = a.get("url", "")
        art_id = a.get("id") or _article_id(url)
        if not art_id:
            continue
        ir = _is_related_int(a.get("is_related"))
        stmts.append({
            "sql": (
                "INSERT OR REPLACE INTO article_content "
                "(id, ticker, year_month, url, summary, dumb_en, dumb_fr, headline, source, is_related) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
            ),
            "args": [
                _arg(art_id),
                _arg(ticker), _arg(year_month),
                _arg(url),
                _arg(a.get("summary", "")),
                _arg(a.get("dumb_en", "")),
                _arg(a.get("dumb_fr", "")),
                _arg(a.get("headline", "")),
                _arg(a.get("source", "")),
                {"type": "integer", "value": ir} if ir is not None else {"type": "null"},
            ],
        })
    if not stmts:
        return
    _execute(stmts)
    logger.info(f"[turso] wrote {ticker}/{year_month} ({len(stmts)} articles)")


def fetch_heavy(ticker: str, year_month: str) -> dict[str, dict]:
    """
    Retourne {id: {url, summary, dumb_en, dumb_fr, headline, source, is_related}} depuis Turso.
    Retourne {} si aucune row (year_month non migré).
    """
    results = _execute([{
        "sql": (
            "SELECT id, url, summary, dumb_en, dumb_fr, headline, source, is_related "
            "FROM article_content WHERE ticker = ? AND year_month = ?"
        ),
        "args": [_arg(ticker), _arg(year_month)],
    }])
    try:
        rows = results[0]["response"]["result"]["rows"]

        def _parse_ir(v):
            return None if v is None else bool(int(v))

        return {
            r[0]: {
                "url":        r[1] or "",
                "summary":    r[2] or "",
                "dumb_en":    r[3] or "",
                "dumb_fr":    r[4] or "",
                "headline":   r[5] or "",
                "source":     r[6] or "",
                "is_related": _parse_ir(r[7]),
            }
            for r in rows
            if r[0]  # ignore rows sans id (ancienne table)
        }
    except (KeyError, IndexError):
        return {}


def merge_heavy_into_articles(articles: list[dict], heavy: dict[str, dict]) -> list[dict]:
    """
    Merge les champs Turso dans les articles Supabase (lean).
    Match sur id (prioritaire) puis url (fallback pour articles sans id encore).
    """
    if not heavy:
        return articles
    heavy_by_url = {v["url"]: v for v in heavy.values() if v.get("url")}
    result = []
    for a in articles:
        art_id = a.get("id")
        if art_id and art_id in heavy:
            result.append({**a, **heavy[art_id]})
        elif a.get("url") and a.get("url") in heavy_by_url:
            result.append({**a, **heavy_by_url[a["url"]]})
        else:
            result.append(a)
    return result


def delete_heavy(ticker: str, year_month: str) -> None:
    """Supprime les rows Turso d'un year_month."""
    _execute([{
        "sql": "DELETE FROM article_content WHERE ticker = ? AND year_month = ?",
        "args": [_arg(ticker), _arg(year_month)],
    }])
    logger.info(f"[turso] deleted {ticker}/{year_month}")
