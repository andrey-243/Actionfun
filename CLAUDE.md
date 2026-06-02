# StockVibe — Instructions Claude

## Carte des MDs — où lire, où écrire

| Fichier | Lire quand... | Écrire quand... |
|---|---|---|
| `RECAP.md ## Architecture` | stack, DB schema, LLM cascade, articles flow, chart config, conventions nommage | décision architecturale prise (nouvelle table, nouveau service, changement de flow) |
| `RECAP.md ## État courant` | statut features, séquence batch en cours, roadmap | feature change de statut · batch avance · roadmap mise à jour |
| `ERRORS.md` | bug similaire à ce qu'on a déjà rencontré | approche nécessite > 2 tentatives |
| `~/.claude/projects/.../memory/reference_supabase.md` | project ref Supabase, URL, curl DDL | jamais (info stable) |
| `~/.claude/projects/.../memory/naming_conventions.md` | règle noms produit vs fichiers | jamais |

**Règle** : ne jamais écrire dans un autre `.md` que `RECAP.md` et `ERRORS.md`.
Ne pas créer de fichiers mémoire dans `~/.claude/projects/` pour l'état du projet — `RECAP.md` est la source de vérité.

---

## Stack — points critiques

- **Backend** : FastAPI · uvicorn · port 8000 · `backend/.venv`
- **Frontend** : Next.js 15 · React 19 · TypeScript · port 3000
- **Auth + DB** : Supabase — `@supabase/supabase-js` singleton (pas `@supabase/ssr`)
- **IA** : cascade FreeTheAI → Hapuppy → Groq via `llm_service.py` (jamais OpenAI direct, jamais LiteLLM)
- **Source articles** : toujours `articles_merged_finn_yahoo` — jamais Finnhub/Yahoo directement pour le LLM
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
