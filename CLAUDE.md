# StockVibe — Instructions Claude

## Carte des MDs — où lire, où écrire

| Fichier | Lire quand... | Écrire quand... |
|---|---|---|
| `RECAP.md ## Architecture` | stack, DB schema, LLM cascade, articles flow, chart config, conventions nommage | décision architecturale prise (nouvelle table, nouveau service, changement de flow) |
| `RECAP.md ## État courant` | statut features, séquence batch en cours, roadmap | feature change de statut · batch avance · roadmap mise à jour |
| `ERRORS.md` | bug similaire à ce qu'on a déjà rencontré | approche nécessite > 2 tentatives |
**Règle** : ne jamais écrire dans un autre `.md` que `RECAP.md` et `ERRORS.md`.
Ne jamais créer ni écrire dans `~/.claude/projects/` — sans exception. Toute information persistante va uniquement dans `RECAP.md` ou `ERRORS.md`.

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
