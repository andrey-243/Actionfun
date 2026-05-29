# StockVibe — Errors & Solutions

_Format : date · contexte · symptôme · cause · fix_

---

## 2026-05-29 · yfinance rate limit

**Symptôme** : `YFRateLimitError: Too Many Requests` sur toutes les routes stock/news, même après 20h sans calls.

**Cause** : Yahoo Finance rate-limite par IP. yfinance 0.2.x n'a pas de mécanisme de bypass. L'IP WSL peut partager un NAT.

**Fix** : Upgrade yfinance `0.2.54` → `1.4.1` (utilise `curl_cffi` pour imiter un vrai browser). Aussi migré de `t.info` vers `t.fast_info` pour réduire le nombre de requêtes.

**Attributs changés** : `fiftyTwoWeekHigh/Low` → `year_high/year_low` dans `fast_info`.

---

## 2026-05-29 · Frankfurter API — URL obsolète

**Symptôme** : Le toggle EUR/USD change le symbole mais pas la valeur (€274 au lieu de ~€236).

**Cause** : `api.frankfurter.app` retourne un 301 vers `api.frankfurter.dev`. Le fetch browser bloquait la redirection cross-origin silencieusement → `rates` restait `null` → `convert()` retournait la valeur brute.

**Fix** : URL mise à jour vers `https://api.frankfurter.dev/v1/latest?from=USD&to=EUR`.

---

## 2026-05-29 · Supabase — permission denied sur stock_news_cache

**Symptôme** : `Internal Server Error` sur `/api/news/{ticker}` — `permission denied for table stock_news_cache`.

**Cause** : Table créée via migration SQL manuelle → grants Postgres non appliqués automatiquement. Le `service_role` n'avait pas les permissions table-level (distinct du RLS).

**Fix** :
```sql
GRANT ALL ON public.stock_news_cache TO service_role;
```

---

## 2026-05-29 · Cache events in-memory — résumés IA périmés après changement de prompt

**Symptôme** : Après modification du prompt Groq dans `events_service.py`, les anciennes réponses courtes continuent d'être servies.

**Cause** : Cache `_cache` dict en mémoire Python — persiste pendant toute la durée de vie du process uvicorn.

**Fix** : Restart uvicorn (`pkill -f uvicorn` + relance) pour vider le cache. À faire après chaque changement de prompt.

---

## 2026-05-29 · Pins non sauvegardés en Supabase

**Symptôme** : Stars ajoutées sur la home, mais `pinned_stocks` vide en DB → "Mes news" vide.

**Cause** : `handlePin`/`handleUnpin` ne mettaient à jour que le state React local, sans appel Supabase.

**Fix** : Ajout `supabase.from("pinned_stocks").upsert()` dans `handlePin` et `.delete()` dans `handleUnpin`. Chargement initial depuis Supabase au login.

---

## 2026-05-29 · Resend sandbox — emails reçus uniquement par le compte Resend

**Symptôme** : En sandbox (`onboarding@resend.dev`), Resend n'envoie qu'à l'adresse du compte Resend, pas aux vraies adresses users.

**Cause** : Limitation sandbox Resend — domaine non vérifié.

**Fix prod** : Vérifier un domaine dans Resend Dashboard → Domains → changer `RESEND_FROM_EMAIL` dans `.env`.

---

## 2026-05-29 · Cache Supabase — news jamais écrites, route news cassée

**Symptôme** : `/api/news/{ticker}` retourne une erreur 500, `stock_news_cache` reste vide.

**Causes** :
1. `.single()` de supabase-py lève `PGRST116` quand 0 lignes → remplacé par `.maybe_single()`.
2. `.maybe_single().execute()` retourne `None` (pas un APIResponse) quand 0 lignes → guard `cache.data if cache else None`.
3. yfinance retourne `clickThroughUrl: null` (pas absent) → `.get("clickThroughUrl", {})` retournait `None`, `.get("url","")` crashait → remplacé par `(c.get("clickThroughUrl") or {}).get("url", "")`.

**Fix** : Les trois corrections dans `services/news_service.py` + guard `if existing and existing.data` dans `digest_service.py`.

---

## 2026-05-29 · Supabase 403 sur toutes les requêtes table depuis le frontend

**Symptôme** : `GET /rest/v1/pinned_stocks 403`, `GET /rest/v1/user_preferences 403` — les RLS policies existaient pourtant.

**Cause** : `createBrowserClient` de `@supabase/ssr` crée une nouvelle instance à chaque appel `createClient()`. Chaque instance démarre sans session restaurée → `auth.uid()` retourne `null` → RLS bloque. `getUser()` fonctionnait car il valide le token côté serveur, mais les requêtes table n'incluaient pas le JWT.

**Fix** : Remplacé `@supabase/ssr` par `@supabase/supabase-js` avec un singleton (instance créée une seule fois, stockage session via localStorage). Voir `lib/supabase.ts`.

---

## 2026-05-29 · Watchlist vide au retour sur la homepage

**Symptôme** : Les pins disparaissent quand on navigue vers une page stock puis qu'on revient sur la home.

**Cause** : `user` est `null` au premier render (auth pas encore résolue) → `useEffect` déclenché avec `user=null` → `setPins([])`. Les pins se rechargent ensuite mais avec un flash vide perceptible.

**Fix** : Ajout d'un state `authReady` — l'effect de chargement des pins ne se déclenche qu'après la résolution de `getUser()`.

---

## 2026-05-29 · Carré blanc dans le tooltip Chart.js

**Symptôme** : Un carré blanc apparaît à gauche du prix dans le tooltip au survol du graph.

**Cause** : Chart.js affiche par défaut un color box (couleur du dataset) dans le tooltip.

**Fix** : Ajouter `displayColors: false` dans la config `plugins.tooltip`.

---

## 2026-05-29 · Auth OAuth — rien ne se passe au clic

**Symptôme** : Boutons Google/Discord dans le modal ne déclenchent aucune action.

**Cause** : `.env.local` contenait encore les placeholders `your_supabase_url` / `your_supabase_anon_key` → `createClient()` retournait `null` → tous les appels Supabase silencieusement ignorés.

**Fix** : Remplir `.env.local` avec les vraies valeurs Supabase (URL + anon key). Redémarrer `npm run dev` obligatoire après changement de `.env.local`.

---

## 2026-05-29 · Batch scraper — 406 Not Acceptable sur `maybe_single()` (supabase-py backend)

**Symptôme** : `HTTP/2 406 Not Acceptable` sur `GET article_archive` → `'NoneType' object has no attribute 'data'` → 15 tickers failed sur les 18 premiers.

**Cause** : `maybe_single()` envoie `Accept: application/vnd.pgrst.object+json` — PostgREST retourne 406 quand la row n'existe pas encore (table vide pour ce ticker). Même bug que le `.single()` frontend, version backend.

**Fix** : Remplacer `.maybe_single().execute()` par `.limit(1).execute()` + lire `row.data[0]` si présent. Pattern à appliquer partout en backend quand la row peut ne pas exister.

---

## 2026-05-30 · Batch enrich — 406 sur `stock_news_cache` + JSON vide de FreeTheAI

**Symptôme** : `HTTP/2 406` sur `GET stock_news_cache` + `Expecting value: line 1 column 1 (char 0)` sur NVDA, GOOGL, META.

**Causes** :
1. `maybe_single()` dans `news_service.py` → même bug 406 que `article_archive`.
2. FreeTheAI retourne parfois un JSON vide ou wrappé en markdown code block → `json.loads()` crash.

**Fix** :
1. `news_service.py` : `.maybe_single().execute()` → `.limit(1).execute()` + `cache.data[0]`.
2. `llm_service.py` : `llm_json()` strip les ``` markdown avant parsing + raise si content vide.

---

## 2026-05-30 · `pinned_stocks` — 403 malgré RLS policies correctes

**Symptôme** : Toutes les requêtes `pinned_stocks` retournent 403, même avec RLS policies SELECT/INSERT/UPDATE/DELETE en place.

**Cause** : Le rôle `authenticated` n'avait pas les grants PostgreSQL (SELECT, INSERT, UPDATE, DELETE) sur la table — seulement REFERENCES/TRIGGER/TRUNCATE. Les RLS policies filtrent les lignes mais les grants autorisent l'accès à la table : les deux sont nécessaires.

**Fix** :
```sql
GRANT SELECT, INSERT, UPDATE, DELETE ON public.pinned_stocks TO authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.pinned_stocks TO anon;
```

**Règle** : Toute nouvelle table nécessite `GRANT ... TO authenticated` en plus des RLS policies.
