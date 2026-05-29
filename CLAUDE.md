# StockVibe — Instructions Claude

## Règle MDs

**Ne jamais écrire dans aucun fichier `.md` sauf :**
- `ERRORS.md` — uniquement quand une approche a nécessité > 2 tentatives
- `RECAP.md` — quand l'user demande explicitement une mise à jour

Ne pas créer de fichiers mémoire dans `~/.claude/projects/` pour l'état du projet. RECAP.md est la source de vérité.

## Stack réelle

- **Backend** : FastAPI (Python) · uvicorn · port 8000 · `backend/.venv`
- **Frontend** : Next.js 15 · React 19 · TypeScript · port 3000
- **Auth + DB** : Supabase (`@supabase/supabase-js` singleton, pas `@supabase/ssr`)
- **Données** : yfinance 1.4.1 + curl_cffi · Finnhub · Frankfurter (taux)
- **IA** : Groq llama-3.3-70b-versatile (pas LiteLLM, pas OpenAI direct)
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
- Groq pour tous les appels LLM
- Voir RECAP.md pour l'état complet, ERRORS.md pour les bugs résolus
