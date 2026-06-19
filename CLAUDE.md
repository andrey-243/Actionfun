# StockVibe — Instructions Claude

## Carte des MDs — où lire, où écrire

**Règle** : ne jamais écrire dans un autre `.md` que `RECAP.md` et `ERRORS.md`.
Ne jamais créer ni écrire dans `~/.claude/projects/` — sans exception. Toute information persistante va uniquement dans `RECAP.md` ou `ERRORS.md`.

### RECAP.md — pointeurs par thème

| Thème | Section à lire | Écrire quand... |
|---|---|---|
| Termes produit (OW/DUMB/BUB/Breaks) | `## Architecture > Règles & nommage` | nouveau terme produit |
| Stack, ports, libs | `## Architecture > Stack` | nouvelle dépendance majeure |
| Schéma DB, tables, colonnes | `## Architecture > Supabase — Tables actives` | nouvelle table ou colonne |
| LLM cascade, quotas, fallbacks, prompts | `## Architecture > LLM — Cascade & modèles` | changement cascade ou nouveau modèle |
| Flow articles, scraping, merge sources | `## Architecture > Articles — Architecture par période` | nouveau source ou changement de flow |
| Dédup, conflits multi-source | `## Architecture > Articles — Doublons & Multi-source` | nouvelle règle de dédup |
| Digest (daily/weekly), my-news | `## Architecture > News — Architecture` | changement de logique digest |
| Events / dots chart, BUB | `## Architecture > Events (dots chart)` | nouveau type d'event |
| Chart périodes, intervalles yfinance | `## Architecture > Chart — Périodes & intervalles yfinance` | nouvelle période ou intervalle |
| Emails Resend | `## Architecture > Resend — Email` | changement template ou config |
| Tickers couverts, marchés | `## Architecture > Tickers — Coverage` | nouveau marché ou ticker list |
| Batch fill DB | `## Architecture > Batch scraper` | changement logique batch |
| Batch breakers | `## Architecture > Batch breakers` | changement logique breakers |
| Statut features, roadmap | `## État courant > Features` | feature change de statut |
| Séquence batch en cours | `## État courant > Séquence batch` | batch avance ou bloqué |
| Optimisations passées | `## État courant > Optimisations réalisées` | nouvelle optim réalisée |

### ERRORS.md

| Lire quand... | Écrire quand... |
|---|---|
| bug similaire à ce qu'on a déjà rencontré | approche nécessite > 2 tentatives |

---

## Stack — points critiques

- **Backend** : FastAPI · uvicorn · port 8000 · `backend/.venv`
- **Frontend** : Next.js 15 · React 19 · TypeScript · port 3000
- **Auth + DB** : Supabase — `@supabase/supabase-js` singleton (pas `@supabase/ssr`)
- **IA** : cascade FreeTheAI → Hapuppy → Groq via `llm_service.py` (jamais OpenAI direct, jamais LiteLLM)
- **Source articles** : toujours `articles` — jamais Finnhub/Yahoo directement pour le LLM
- **Données bours.** : yfinance 1.4.1 + curl_cffi · Frankfurter (taux)
- **Crons** : APScheduler dans FastAPI

## Lancer

```bash
# Backend
cd backend && source .venv/bin/activate
uvicorn main:app --reload --port 8000

# Frontend
cd frontend && npm run dev  # http://localhost:3000
```

## Conventions

- Pas de mock — appels réels aux APIs
- Variables d'env via python-dotenv, jamais hardcodées
- Routes FastAPI async par défaut
- Noms produit : OW / DUMB / BUB / Breaks (pas les noms de fichiers seuls)
- **Git** : ne jamais inclure `Co-Authored-By: Claude` dans les commits — aucune exception
- **Colonnes LLM bilingues** : toute colonne DB contenant du texte généré par LLM **doit** avoir un suffixe de langue : `_fr` ou `_en`. Ne jamais créer/utiliser un nom générique sans suffixe (`ai_summary`, `cause`, `summary`…). En Python, `_summary_col()` retourne `"ai_summary_fr"` ou `"ai_summary_en"` selon `LLM_LANG`. Idem pour toute nouvelle colonne de ce type.

## RTK — Token Efficiency

Toujours préfixer les commandes shell par `rtk` :

| Commande native | Utiliser |
|---|---|
| `grep` / `grep -n` / `grep -rn` | `rtk grep` |
| `find` | `rtk find` |
| `cat` | `rtk read` |
| `ls` | `rtk ls` |
| `git status` / `git diff` / `git log` | `rtk git` |
| `curl -s` | `rtk curl` |
| `wc -l` | `rtk wc` |
| `ps aux` | `rtk ps` |
| `pip install` / `pip freeze` | `rtk pip` |
| `npm run` / `npx tsc` | `rtk npm` / `rtk tsc` |

Ne pas utiliser `rtk` pour : `uvicorn`, `python3`, `pkill`, `lsof`, `pgrep` — pas de filtre applicable.
