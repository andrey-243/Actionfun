"""
Helper R2 — stockage externe des champs lourds articles (summary, dumb_en, dumb_fr)
pour les year_month > 6 mois.

Format R2 : {R2_BUCKET}/{ticker}/{year_month}.json.gz
Structure  : [{"url": "...", "summary": "...", "dumb_en": "...", "dumb_fr": "..."}, ...]

Non activé en prod tant que la migration n'est pas lancée.
Vars d'env requises : R2_ENDPOINT_URL · R2_ACCESS_KEY_ID · R2_SECRET_ACCESS_KEY · R2_BUCKET
"""

import gzip
import json
import logging
import os

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)

HEAVY_FIELDS = ("summary", "dumb_en", "dumb_fr")


def _client():
    return boto3.client(
        "s3",
        endpoint_url=os.environ["R2_ENDPOINT_URL"],
        aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
        region_name="auto",
    )


def _key(ticker: str, year_month: str) -> str:
    return f"{ticker}/{year_month}.json.gz"


def write_heavy(ticker: str, year_month: str, articles: list[dict]) -> None:
    """Écrit les champs lourds sur R2. articles = liste complète (avec tous les champs)."""
    payload = [
        {"url": a.get("url", ""), **{f: a.get(f, "") for f in HEAVY_FIELDS}}
        for a in articles
        if any(a.get(f) for f in HEAVY_FIELDS)
    ]
    if not payload:
        return
    data = gzip.compress(json.dumps(payload).encode())
    _client().put_object(
        Bucket=os.environ["R2_BUCKET"],
        Key=_key(ticker, year_month),
        Body=data,
        ContentType="application/json",
        ContentEncoding="gzip",
    )
    logger.info(f"[r2] wrote {ticker}/{year_month} ({len(payload)} articles)")


def fetch_heavy(ticker: str, year_month: str) -> dict[str, dict]:
    """
    Retourne {url: {summary, dumb_en, dumb_fr}} depuis R2.
    Retourne {} si le fichier n'existe pas (row non migrée ou pas de contenu).
    """
    try:
        resp = _client().get_object(
            Bucket=os.environ["R2_BUCKET"],
            Key=_key(ticker, year_month),
        )
        payload: list[dict] = json.loads(gzip.decompress(resp["Body"].read()))
        return {item["url"]: {f: item.get(f, "") for f in HEAVY_FIELDS} for item in payload}
    except ClientError as e:
        if e.response["Error"]["Code"] in ("NoSuchKey", "404"):
            return {}
        raise


def delete_heavy(ticker: str, year_month: str) -> None:
    """Supprime le fichier R2 d'un year_month (appelé lors de la purge > 13 mois)."""
    try:
        _client().delete_object(Bucket=os.environ["R2_BUCKET"], Key=_key(ticker, year_month))
        logger.info(f"[r2] deleted {ticker}/{year_month}")
    except ClientError as e:
        if e.response["Error"]["Code"] not in ("NoSuchKey", "404"):
            raise


def merge_heavy_into_articles(articles: list[dict], heavy: dict[str, dict]) -> list[dict]:
    """
    Merge les champs lourds R2 dans les articles Supabase (lightweight).
    Appelé dans bubble_service avant envoi au LLM.
    """
    if not heavy:
        return articles
    merged = []
    for a in articles:
        url = a.get("url", "")
        if url in heavy:
            merged.append({**a, **heavy[url]})
        else:
            merged.append(a)
    return merged
