"""
Analyse les mots les plus fréquents dans les headlines & summaries des articles
pour chaque ticker — aide à enrichir data/ticker_aliases.py.

Usage :
    python scripts/analyze_ticker_words.py --region US
    python scripts/analyze_ticker_words.py --region ALL --top 30 --min-count 3
    python scripts/analyze_ticker_words.py --ticker AAPL --top 50
    python scripts/analyze_ticker_words.py --region EU --output aliases_candidates.json

Lit uniquement les articles is_related=True (ou null en fallback).
Exclut les stop words, les termes financiers génériques,
et les mots déjà présents dans ticker_aliases.py + company name.
"""
import argparse
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path

# ─── Path setup ───────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from supabase import create_client
from data.tickers import get_by_region, ALL_TICKERS
from data.ticker_aliases import TICKER_ALIASES
from services.yfinance_service import bulk_fetch_company_names

# ─── Stop words ───────────────────────────────────────────────────────────────

STOP_WORDS: set[str] = {
    # Anglais génériques
    "the", "a", "an", "is", "was", "are", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "shall", "can", "need", "dare", "ought",
    "used", "to", "of", "in", "on", "at", "by", "for", "with", "about",
    "against", "between", "through", "during", "before", "after", "above",
    "below", "from", "up", "down", "out", "off", "over", "under", "again",
    "further", "then", "once", "and", "but", "or", "nor", "so", "yet",
    "both", "either", "neither", "not", "only", "own", "same", "than",
    "too", "very", "just", "because", "as", "until", "while", "that",
    "this", "these", "those", "which", "who", "whom", "what", "when",
    "where", "why", "how", "all", "each", "few", "more", "most", "other",
    "some", "such", "into", "its", "it", "he", "she", "they", "we", "his",
    "her", "their", "our", "your", "my", "its", "any", "if", "no",
    "new", "also", "said", "says", "say", "told", "per", "amid", "amid",
    "according", "including", "following", "since", "after", "despite",
    "within", "across", "among", "around", "without",
    # Financier générique
    "shares", "share", "stock", "stocks", "market", "markets", "company",
    "companies", "business", "revenue", "profit", "loss", "earnings",
    "quarter", "quarterly", "annual", "year", "years", "month", "months",
    "billion", "million", "percent", "growth", "decline", "rise", "fell",
    "rose", "dropped", "gained", "lost", "higher", "lower", "results",
    "report", "reported", "reports", "forecast", "guidance", "outlook",
    "investors", "investor", "analysts", "analyst", "trading", "trade",
    "price", "prices", "index", "sector", "industry", "firm", "firms",
    "ceo", "cfo", "chief", "executive", "officer", "president", "chairman",
    "board", "group", "corp", "inc", "ltd", "plc", "ag", "sa", "nv",
    "holdings", "holding", "international", "global", "financial", "fund",
    "management", "investment", "capital", "bank", "banking", "rate",
    "rates", "dividend", "buyback", "acquisition", "deal", "merger",
    "acquired", "agreed", "announced", "announced", "plans", "plan",
    "expected", "expects", "target", "targets", "estimate", "estimates",
    "raised", "cut", "increased", "decreased", "unchanged",
    "monday", "tuesday", "wednesday", "thursday", "friday",
    "january", "february", "march", "april", "june", "july",
    "august", "september", "october", "november", "december",
    "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "oct", "nov", "dec",
    # News générique
    "breaking", "update", "exclusive", "watch", "video", "read", "more",
    "click", "here", "view", "full", "story", "news", "today", "latest",
    "weekly", "daily", "monthly", "top", "best", "big", "key", "major",
    "first", "second", "third", "last", "next", "high", "low", "strong",
    "weak", "solid", "mixed", "positive", "negative",
}

# Termes financiers qui ne sont PAS des aliases utiles
GENERIC_FINANCIAL = {
    "q1", "q2", "q3", "q4", "fy", "fy2024", "fy2025", "fy2026",
    "eps", "ebitda", "ebit", "roi", "roe", "pe", "pb",
    "nyse", "nasdaq", "lse", "hkex", "tsx", "asx", "bse", "nse",
    "etf", "ipo", "spac", "sec", "ftc", "doj", "eu", "us", "uk",
}


# ─── Extraction de mots ───────────────────────────────────────────────────────

def _tokenize(text: str) -> list[str]:
    """Extrait les tokens significatifs d'un texte."""
    text = text.lower()
    # Conserver les apostrophes composés (don't → dont, j'ai → jai)
    text = re.sub(r"['’]", "", text)
    # Remplacer ponctuation par espace
    text = re.sub(r"[^a-z0-9\s\-]", " ", text)
    # Splitter sur espaces et tirets
    tokens = re.split(r"[\s\-]+", text)
    return [t for t in tokens if len(t) >= 3 and not t.isdigit()]


def _build_exclude_set(ticker: str, company_name: str) -> set[str]:
    """Mots à exclure : stop words + ticker + company keywords + aliases connus."""
    exclude = set(STOP_WORDS) | GENERIC_FINANCIAL

    # Ticker lui-même (ex: "aapl", "7203")
    exclude.add(ticker.lower())
    base = ticker.split(".")[0].lower()
    if base != ticker.lower():
        exclude.add(base)

    # Mots du company name
    if company_name:
        for w in _tokenize(company_name):
            exclude.add(w)

    # Aliases déjà connus
    for alias in TICKER_ALIASES.get(ticker, []):
        for w in _tokenize(alias):
            exclude.add(w)

    return exclude


# ─── Fetch articles ───────────────────────────────────────────────────────────

def _fetch_articles_for_ticker(ticker: str, supa) -> list[dict]:
    """Retourne tous les articles de la DB pour ce ticker (tous les year_months)."""
    rows = supa.table("articles") \
        .select("articles") \
        .eq("ticker", ticker) \
        .execute()
    articles = []
    for row in (rows.data or []):
        for a in (row.get("articles") or []):
            # Garder : is_related=True, ou null (pas encore évalué)
            if a.get("is_related") is False:
                continue
            articles.append(a)
    return articles


# ─── Analyse ─────────────────────────────────────────────────────────────────

def analyze_ticker(ticker: str, company_name: str, supa, top: int, min_count: int) -> dict:
    articles = _fetch_articles_for_ticker(ticker, supa)
    if not articles:
        return {"ticker": ticker, "article_count": 0, "top_words": []}

    exclude = _build_exclude_set(ticker, company_name)
    counter: Counter = Counter()

    for a in articles:
        headline = a.get("headline") or a.get("title") or ""
        summary  = a.get("summary") or ""

        # Headline poids x3, summary poids x1
        for w in _tokenize(headline):
            if w not in exclude:
                counter[w] += 3
        for w in _tokenize(summary):
            if w not in exclude:
                counter[w] += 1

    top_words = [
        {"word": w, "score": s}
        for w, s in counter.most_common(top * 3)
        if s >= min_count
    ][:top]

    return {
        "ticker":        ticker,
        "company_name":  company_name,
        "article_count": len(articles),
        "known_aliases": TICKER_ALIASES.get(ticker, []),
        "top_words":     top_words,
    }


# ─── Affichage ────────────────────────────────────────────────────────────────

def _print_result(r: dict) -> None:
    if r["article_count"] == 0:
        print(f"  {r['ticker']}: 0 articles — skip")
        return

    known = {w.lower() for alias in r["known_aliases"] for w in _tokenize(alias)}
    words_display = []
    for entry in r["top_words"]:
        w = entry["word"]
        marker = "" if w in known else " ★"  # ★ = mot non encore dans aliases
        words_display.append(f"{w}({entry['score']}){marker}")

    print(f"\n{'─'*60}")
    print(f"  {r['ticker']}  |  {r['company_name']}  |  {r['article_count']} articles")
    print(f"  {' · '.join(words_display)}")
    if r["known_aliases"]:
        print(f"  aliases connus : {', '.join(r['known_aliases'][:8])}")


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Analyse les mots les plus fréquents par ticker.")
    parser.add_argument("--region",    default="US", help="US | EU | ASIA | ALL")
    parser.add_argument("--ticker",    default=None,  help="Ticker unique (override --region)")
    parser.add_argument("--top",       type=int, default=20,  help="Top N mots par ticker (défaut 20)")
    parser.add_argument("--min-count", type=int, default=5,   help="Score minimum pour apparaître (défaut 5)")
    parser.add_argument("--output",    default=None,  help="Fichier JSON output (optionnel)")
    args = parser.parse_args()

    supa = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])

    if args.ticker:
        tickers = [args.ticker.upper()]
    elif args.region.upper() == "ALL":
        tickers = ALL_TICKERS
    else:
        tickers = get_by_region(args.region)

    if not tickers:
        print(f"Région inconnue : {args.region}")
        sys.exit(1)

    print(f"Analyse de {len(tickers)} tickers (top={args.top}, min_count={args.min_count})")

    # Fetch company names en bulk pour tous les tickers
    company_names: dict[str, str] = bulk_fetch_company_names(tickers)

    results = []
    for ticker in tickers:
        company_name = company_names.get(ticker, "")
        r = analyze_ticker(ticker, company_name, supa, top=args.top, min_count=args.min_count)
        _print_result(r)
        results.append(r)

    # Stats globales
    total_articles = sum(r["article_count"] for r in results)
    tickers_with_data = sum(1 for r in results if r["article_count"] > 0)
    print(f"\n{'='*60}")
    print(f"Total : {tickers_with_data}/{len(tickers)} tickers avec articles · {total_articles} articles analysés")
    print("★ = mot non encore dans ticker_aliases.py — candidats à ajouter")

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        print(f"\nOutput JSON : {args.output}")


if __name__ == "__main__":
    main()
