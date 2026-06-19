"""
Stockage externe des champs lourds articles (summary, dumb_en, dumb_fr)
pour les year_month > 6 mois — backend Turso (libSQL edge DB).

Table Turso :
    article_content (ticker TEXT, year_month TEXT, url TEXT,
                     summary TEXT, dumb_en TEXT, dumb_fr TEXT,
                     PRIMARY KEY (ticker, year_month, url))

Vars d'env requises : TURSO_URL · TURSO_AUTH_TOKEN

Non activé tant que TURSO_URL n'est pas dans .env.
"""

import logging
import os

import httpx

logger = logging.getLogger(__name__)

HEAVY_FIELDS = ("summary", "dumb_en", "dumb_fr")


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


def init_db() -> None:
    """Crée la table si elle n'existe pas (one-shot setup)."""
    _execute([{
        "sql": (
            "CREATE TABLE IF NOT EXISTS article_content ("
            "ticker TEXT NOT NULL, year_month TEXT NOT NULL, url TEXT NOT NULL, "
            "summary TEXT DEFAULT '', dumb_en TEXT DEFAULT '', dumb_fr TEXT DEFAULT '', "
            "PRIMARY KEY (ticker, year_month, url)"
            ")"
        )
    }])
    logger.info("[turso] table article_content prête")


def write_heavy(ticker: str, year_month: str, articles: list[dict]) -> None:
    """Écrit les champs lourds sur Turso. articles = liste complète."""
    stmts = []
    for a in articles:
        if not any(a.get(f) for f in HEAVY_FIELDS):
            continue
        stmts.append({
            "sql": "INSERT OR REPLACE INTO article_content (ticker, year_month, url, summary, dumb_en, dumb_fr) VALUES (?, ?, ?, ?, ?, ?)",
            "args": [
                _arg(ticker), _arg(year_month),
                _arg(a.get("url", "")),
                _arg(a.get("summary", "")),
                _arg(a.get("dumb_en", "")),
                _arg(a.get("dumb_fr", "")),
            ],
        })
    if not stmts:
        return
    _execute(stmts)
    logger.info(f"[turso] wrote {ticker}/{year_month} ({len(stmts)} articles)")


def fetch_heavy(ticker: str, year_month: str) -> dict[str, dict]:
    """
    Retourne {url: {summary, dumb_en, dumb_fr}} depuis Turso.
    Retourne {} si aucune row (year_month non migré).
    """
    results = _execute([{
        "sql": "SELECT url, summary, dumb_en, dumb_fr FROM article_content WHERE ticker = ? AND year_month = ?",
        "args": [_arg(ticker), _arg(year_month)],
    }])
    try:
        rows = results[0]["response"]["result"]["rows"]
        return {
            r[0]: {"summary": r[1], "dumb_en": r[2], "dumb_fr": r[3]}
            for r in rows
        }
    except (KeyError, IndexError):
        return {}


def delete_heavy(ticker: str, year_month: str) -> None:
    """Supprime les rows Turso d'un year_month (appelé lors de la purge > 13 mois)."""
    _execute([{
        "sql": "DELETE FROM article_content WHERE ticker = ? AND year_month = ?",
        "args": [_arg(ticker), _arg(year_month)],
    }])
    logger.info(f"[turso] deleted {ticker}/{year_month}")


def merge_heavy_into_articles(articles: list[dict], heavy: dict[str, dict]) -> list[dict]:
    """
    Merge les champs lourds Turso dans les articles Supabase (lightweight).
    Appelé dans bubble_service avant envoi au LLM.
    """
    if not heavy:
        return articles
    return [
        {**a, **heavy[a.get("url", "")]} if a.get("url", "") in heavy else a
        for a in articles
    ]
