# StockVibe — Errors & Solutions

_Format : date · contexte · symptôme · cause · fix_

---

## Règles de travail Claude

- **Jamais de `git push`** sans confirmation explicite dans le message courant. Commit seul ne vaut pas autorisation de push.
- **Jamais de `Co-Authored-By: Claude`** dans les messages de commit.

---

## 2026-06-04 · digest — Management API 404 sur auth/users/{uid}

**Symptôme** : `_send_emails` appelait `https://api.supabase.com/v1/projects/{ref}/auth/users/{uid}` pour récupérer l'email — 404 systématique. Aucun email envoyé, aucun log d'erreur visible (le `except` catchait silencieusement).

**Cause** : Le token `SUPABASE_MANAGEMENT_TOKEN` n'a pas les droits sur l'endpoint `GET /auth/users/{id}` de la Management API.

**Fix** : Remplacé par `supa.auth.admin.get_user_by_id(uid)` — utilise le service role key via le client Supabase Python, fonctionne correctement.

**Fichier** : `services/digest_service.py` — `_send_emails()`.

---

## 2026-06-04 · digest — user_preferences vide = emails silencieusement skippés

**Symptôme** : Les deux digests tournaient sans erreur (4 tickers trouvés, summaries chargés) mais aucun email envoyé. Aucun log indiquant un problème.

**Cause** : La table `user_preferences` était vide — aucun utilisateur n'avait de row. `notify_email=eq.True` retournait 0 résultats, la boucle `_send_emails` ne tournait jamais.

**Fix** : Insérer une row `user_preferences` depuis l'UI Profile (les toggles Daily/Weekly upsertent la row) ou manuellement en DB. Le user s'inscrit implicitement à son premier toggle.

**Règle** : Toujours vérifier `user_preferences` non vide avant de conclure qu'un digest ne fonctionne pas. Logger explicitement `"0 users to notify"` si la query retourne vide.

**Fichier** : `services/digest_service.py` — `_send_emails()`.

---

## 2026-06-04 · digest — race condition Finnhub cron (18h30) vs Daily digest (18h30)

**Symptôme** : Le daily digest pouvait snapshot un OW en cours de refresh par Finnhub (même heure exacte), potentiellement lire `ai_summary_24h_en` avant qu'il soit écrit.

**Cause** : `finnhub_refresh` (APScheduler) tourne à `*/6h :30` → tombe à 18h30. Daily digest était aussi à 18h30 → overlap possible.

**Fix** : Daily digest décalé à 19h15 — après Yahoo (18h00) + Finnhub (18h30) + cold_start FR (19h00). Buffer de 45 min sur le dernier refresh avant snapshot.

**Fichier** : `backend/main.py` — `daily_digest_email` cron.

---

## 2026-06-04 · digest — `user_preferences.lang` DEFAULT 'fr' au lieu de 'en'

**Symptôme** : Tout nouveau user sans préférence langue explicite recevait ses digests en français, même sans traduction FR disponible — risque d'emails vides si `ai_summary_fr` est null.

**Cause** : Colonne `lang` créée avec `DEFAULT 'fr'` dans un commit précédent.

**Fix** : `ALTER TABLE user_preferences ALTER COLUMN lang SET DEFAULT 'en'`. Fallback EN garanti pour tout user sans préférence.

**Règle** : EN est toujours la langue primaire (générée en premier). DEFAULT doit être 'en', pas 'fr'.

---

## 2026-06-04 · refresh_breakers — Breakers générés sans DUMB (qualité dégradée US/EU/ASIA)

**Symptôme** : Les weekly/monthly summaries générés par `refresh_breakers.py` utilisaient le fallback `summary → headline` au lieu des `dumb_en` enrichis, produisant une qualité de condensation moindre.

**Cause** : `refresh_breakers.py` ne appelait jamais `brutalise_new_articles` — le DUMB était généré uniquement par les crons Yahoo/Finnhub (`min_users=3`). Les ~700 tickers batch n'atteignent pas ce seuil en setup single-user → zéro `dumb_en` en DB au moment de la condensation.

**Fix** : `_process_ticker` appelle `brutalise_new_articles(ticker, lang)` en step 1b (après scrape, avant weeklies). Non-bloquant : une erreur DUMB ne stoppe pas le ticker.

**Re-run** : Tickers déjà generés (US: 260, EU: 79) → auto-marqués `legacy` au prochain run normal → `_process_weeklies`/`_process_monthlies` avec `force_regen=True` (bypass existence check) → re-générés avec DUMB. Commande : `python scripts/refresh_breakers.py --region US --lang en` suffit.

**Fichier** : `scripts/refresh_breakers.py` — `_process_ticker`, `_process_weeklies`, `_process_monthlies`, `main()`.

---

## 2026-06-04 · `_is_ticker_related` — company_name absent dans `_fetch_news` et `digest.py`

**Symptôme** : Des articles pertinents passaient le fetch Yahoo/Finnhub mais étaient filtrés dans `_fetch_news` — DUMB et OW générés depuis un set d'articles réduit. Exemple : articles "Amazon Announces..." filtrés pour AMZN si "amzn" n'apparaît pas littéralement.

**Cause** : `_fetch_news` et `_week_articles_from_archive` appelaient `_is_ticker_related(a, ticker)` sans `company_name`. Les mots-clés société ("amazon", "toyota", "nokia") n'étaient jamais testés → seul le ticker brut était cherché dans le texte.

**Fix** : `_lookup_company_name(ticker, supa)` lookup depuis `pinned_stocks` dans `_fetch_news`. Même lookup dans `routers/digest.py` weekly endpoint. `company_name` maintenant passé partout.

**Fichier** : `services/overview_service.py` — `_lookup_company_name()` + `_fetch_news()` · `routers/digest.py` — `weekly()` + `_week_articles_from_archive()`.

---

## 2026-06-04 · llm_service — 3 notifs TG en one shot sur quota Hapuppy

**Symptôme** : FreeTheAI quota + Hapuppy 403 → 3 notifs simultanées : `hapuppy_quota` + `both_quota` + `hapuppy_down`. Message `both_quota` affichait "reset à minuit UTC" pour Hapuppy (faux — Hapuppy reset à 18h Paris).

**Cause** :
1. `hapuppy_quota` part dans le bloc Hapuppy (avant Groq).
2. `both_quota` partait systématiquement avant Groq si les deux flags sont non-null, sans vérifier si `hapuppy_quota` venait de partir.
3. `groq_reason == "hapuppy_down"` (set unconditionnellement après le bloc Hapuppy) déclenchait `hapuppy_down` au succès Groq même si la cause était un quota.
4. Headers `_CASCADE_HEADERS` hardcodés avec des heures figées incorrectes.

**Fix** :
- `_hapuppy_quota_notif_sent` (local `llm_call`) : set à True quand `hapuppy_quota` part → supprime `both_quota` et `hapuppy_down` dans le même call.
- `hapuppy_down` à succès Groq : condition `and not _hapuppy_quota_notif_sent and _hapuppy_quota_skip_until is None`.
- `header_override` dans `_send_cascade_notif` : `hapuppy_quota` et `both_quota` calculent les heures de reset dynamiquement. Format : `Reset 18h00 Paris (dans 1h58m)`.

**Fichier** : `services/llm_service.py` — `llm_call()`, `_send_cascade_notif()`.

---

## 2026-06-04 · my-news — tickers < 3 pins jamais rafraîchis par Yahoo cron

**Symptôme** : Un ticker personnel (ex: 7203.T) affiche "NO NEWS 3j" dans my-news alors que Yahoo Finance montre des articles du jour. Articles présents sur Yahoo mais absents de `articles_merged_finn_yahoo`.

**Cause** : `run_yahoo_accumulation` (cron */3h) utilise `min_users=3` — seuls les tickers pinnés par ≥ 3 users reçoivent des articles Yahoo. `run_hourly_news_refresh` (cron :15) lit la DB existante sans fetcher Yahoo, donc pour les tickers personnels (1 user), la DB stagne indéfiniment.

**Fix** : Dans `run_hourly_news_refresh`, appeler `_accumulate_yahoo(ticker, current_ym, supa)` avant de lire la DB — les tickers no_24h_news avec < 3 pins reçoivent maintenant un fetch Yahoo à chaque cycle horaire.

**Fichier** : `services/overview_service.py` — boucle `run_hourly_news_refresh`.

---

## 2026-06-04 · `_is_ticker_related` — faux négatif sur tickers avec suffixe exchange

**Symptôme** : Des articles clairement liés à Toyota (headline "The Bull Case For Toyota Motor (TSE:7203)") passent le fetch Yahoo mais sont filtrés par `_is_ticker_related` → disparaissent de `_fetch_news` → `no_24h_news=True` malgré des articles récents.

**Cause** : `_is_ticker_related` cherche `ticker.lower()` = `"7203.t"` dans le texte. Les headlines utilisent le format `TSE:7203` (exchange:numéro) pas `7203.T` (numéro.exchange). Le match échoue. Affecte tous les tickers non-US avec suffixe (7203.T, 005930.KS, 1299.HK, etc.).

**Fix** : Extraire la base du ticker (avant le `.`) et l'ajouter aux keywords si différente du ticker complet et ≥ 3 chars :
```python
base = ticker.split(".")[0].lower()
if base != ticker.lower() and len(base) >= 3:
    keywords.append(base)
```
`"7203"` matche `"tse:7203"` → articles correctement inclus.

**Fichier** : `services/breakers_articles_service.py` — `_is_ticker_related()`.

---

## 2026-06-04 · refresh_breakers — tickers marqués done malgré des échecs LLM

**Symptôme** : 63 tickers US marqués `done` dans le checkpoint mais avec des trous en DB (0 weekly ou monthly summaries sur 2026-01→06). Détectés via SQL :
```sql
SELECT d.ticker, COUNT(*) as mois_manquants
FROM done_tickers d
JOIN articles_merged_finn_yahoo a ON a.ticker = d.ticker AND a.year_month BETWEEN '2026-01' AND '2026-06'
WHERE NOT EXISTS (SELECT 1 FROM article_month_summaries m WHERE m.ticker = d.ticker AND m.year_month = a.year_month AND m.ai_summary_en IS NOT NULL)
GROUP BY d.ticker HAVING COUNT(*) > 0;
```

**Cause** : pendant le batch US EN, creux où FreeTheAI (circuit breaker) + Hapuppy (timeout ×3) + Groq (quota daily épuisé) étaient tous indisponibles simultanément. `_gen_weekly`/`_gen_monthly` catchaient `GroqExhaustedError` → retournaient `None` → aucun write DB. Mais `done.add(ticker)` était appelé inconditionnellement.

**Fix code** (`refresh_breakers.py`) : `_process_weeklies` et `_process_monthlies` remontent maintenant un compteur `failures`. `done.add(ticker)` est conditionnel : uniquement si `failures == 0 and error is None`. Log explicite si ticker non marqué done.

**Fix données** : retirer manuellement les tickers affectés du checkpoint JSON (`done` array) → ils sont retraités au prochain run (le script skip les rows déjà en DB, ne génère que les manquants).

---

## 2026-06-04 · stock page 1M — count articles incohérent entre header et CTA

**Symptôme** : Header de section affichait "· 3 articles" mais le bouton CTA indiquait "2 articles >". L'user voyait deux chiffres contradictoires sur la même ligne.

**Cause** : `s.article_count` (champ brut du backend, total non filtré) était affiché dans le header, tandis que `sectionArticles.length` (longueur du tableau `top_articles` filtré retourné par le backend) alimentait le CTA `ExpandButton`. Les deux ne correspondent pas : le backend peut envoyer un `article_count` total > le nombre d'articles dans `top_articles` après filtrage `_is_ticker_related`.

**Fix** : Suppression du span `· {s.article_count} articles` dans le header de section (`renderSection` dans `stock/[ticker]/page.tsx`). Le CTA seul affiche le vrai nombre d'articles accessibles.

**Fichier** : `frontend/app/stock/[ticker]/page.tsx` — `renderSection`.

---

## 2026-06-04 · my-news W/D async — W affichait moins d'articles que D sur 24h

**Symptôme** : WeeklyCard AMZN affichait 9 articles sur 7 jours alors que DigestCard en affichait 21 sur 24h — incohérent (fenêtre plus large = moins d'articles).

**Cause** : `_articles_from_archive` dans `routers/digest.py` cappait à `[:15]` avant le filtre `_is_ticker_related`. Pour un ticker très couvert (AMZN), les 15 candidats retenus ne survivaient pas tous au filtre — résultat : 9. Pendant ce temps `_fetch_news` retournait `[:100]` articles.

**Fix** : Cap passé de `[:15]` à `[:100]` dans `_articles_from_archive`.

**Fichier** : `backend/routers/digest.py` — `_articles_from_archive`.

---

## 2026-06-04 · my-news — doublons de cartes (React StrictMode)

**Symptôme** : Chaque ticker apparaissait deux fois dans la liste daily et weekly de my-news.

**Cause** : React 18 StrictMode (développement) exécute les `useEffect` deux fois — le fetch individuel par ticker et le `setDigests((prev) => [...prev, d])` cumulatif tournait donc deux fois, doublant tous les éléments.

**Fix** : Déduplication au render via `.filter((d, i, arr) => arr.findIndex(x => x.ticker === d.ticker) === i)` sur `digests` et `weeklies` avant le map.

**Fichier** : `frontend/app/my-news/page.tsx` — render daily + weekly.

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

**Symptôme** : Après modification du prompt Groq dans `bubble_service.py`, les anciennes réponses courtes continuent d'être servies.

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

**Fix** : Les trois corrections dans `services/overview_service.py` + guard `if existing and existing.data` dans `digest_service.py`.

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
1. `maybe_single()` dans `overview_service.py` → même bug 406 que `article_archive`.
2. FreeTheAI retourne parfois un JSON vide ou wrappé en markdown code block → `json.loads()` crash.

**Fix** :
1. `overview_service.py` : `.maybe_single().execute()` → `.limit(1).execute()` + `cache.data[0]`.
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

---

## 2026-05-30 · Batch EN enrich — FreeTheAI 429 systématique

**Symptôme** : FreeTheAI retourne 429 sur chaque appel pendant le batch EN enrich, même avec `FREETHEAI_DELAY=17s`. Groq prend le relais mais la qualité est moindre.

**Causes identifiées** :
1. **Processus concurrents** — `litellm` (PID 797, configuré pour router vers FreeTheAI) et `free_api_llm/dashboard_server.py` (PID 4744) tournaient en parallèle et consommaient le quota RPM de la même clé API.
2. **Modifications `llm_service.py` inutiles** — ajout de `_freetheai_available()` avec sleep MIN_INTERVAL, `_freetheai_last_call`, et `break` sur 429 (empêchait d'essayer le secondary model) — ces changements n'aidaient pas et introduisaient de la complexité.

**Fix** :
1. Killer les processus concurrents avant tout batch : `kill $(pgrep -f litellm) $(pgrep -f free_api_llm)`.
2. Revenir au comportement original de `llm_service.py` : `continue` sur 429 (essaie le secondary), pas de MIN_INTERVAL custom.
3. `FREETHEAI_DELAY=17s` dans le batch est suffisant pour respecter le RPM (10 RPM, 1 concurrent).

**Règle** : Avant tout batch FreeTheAI, vérifier `ps aux | grep python` et killer tout processus concurrent utilisant la même clé API.

---

## 2026-05-31 · Batch bubbles — cause=null comptée comme succès

**Symptôme** : Le batch affichait `✓ ABBV — 20 bubbles générées` et marquait le ticker `done` dans `batch_progress.json`, mais les 264 rows en DB avaient `cause=null`. Hapuppy était en 502, Groq reçu comme down → `get_event_news()` upsertait la row avec `cause=null` et retournait `{"cause": None}`. Le batch ne vérifiait pas `result.get("cause")`.

**Tickers affectés** : ABBV, AMGN, APO, ARES, BMY, BR, CME, FDS, GILD, ICE, JNJ, LLY, MCO, MRK, MSCI, NDAQ, NET, PFE, REGN, SPGI, TSLA, UNH (22 tickers, 264 rows).

**Fix batch** : `phase_bubbles` compte maintenant `ticker_total` (tous les appels) et `ticker_events` (cause non-null seulement). Si `null_count > 0` → log `⚠` + ticker **pas** ajouté à `bubbles_done` → sera retenté au prochain run.

**Fix DB** : 264 rows null supprimées + 21 tickers retirés de `bubbles_done` via script one-shot.

**Règle** : Toujours vérifier `result.get("cause")` avant de compter un succès LLM dans un batch.

---

## 2026-06-01 · Graphique 24H vide — `idx.date()` supprime l'heure

**Symptôme** : Le graphique en vue 24H est complètement vide pour toutes les actions.

**Cause** : `yfinance_service.py` formattait chaque point comme `str(idx.date())` pour toutes les périodes. Pour `1d` (bougies 5-min), `idx` est un datetime avec heure, mais `.date()` le tronque → tous les ~78 points du jour obtiennent la même label `"2026-06-01"` → Chart.js ne peut pas tracer de courbe avec des labels identiques.

**Fix** : Formater en `HH:MM` pour `1d`, garder `YYYY-MM-DD` pour les autres périodes :
```python
def _fmt(idx):
    if period == "1d":
        return idx.strftime("%H:%M")
    return str(idx.date())
```

**Règle** : Pour les périodes intraday (interval < 1j), toujours inclure l'heure dans le label, sinon tous les points se superposent.

---

## 2026-06-11 · gen_dumb(cutoff_days=35) — KeyError 'title' dans _brutalise

**Symptôme** : `✗ ECL: 'title'` / `✗ IFF: 'title'` — fix_missing DUMB MISS échouait silencieusement.

**Cause** : `_fetch_news` normalise les articles et renomme `headline` → `title`. Quand `cutoff_days != 7`, `gen_dumb` bypasse `_fetch_news` et fetch les articles bruts depuis la table `articles` (JSONB) — ces articles gardent `headline`. `_brutalise` accédait à `a['title']` → KeyError.

**Fix** : `_brutalise` utilise `a.get('title') or a.get('headline', '')` — couvre les deux formats.

**Règle** : Les articles bruts DB ont `headline`. Les articles normalisés par `_fetch_news` ont `title`. Ne jamais accéder à `a['title']` sans fallback `a.get('headline')`.

**Fichier** : `services/overview_service.py` — `_brutalise()`.

---

## 2026-05-31 · Nouveau champ upsert Supabase — colonne manquante en DB

**Symptôme** : `PGRST204 — Could not find the 'articles_hash' column of 'stock_news_cache' in the schema cache` → 500 sur `/api/news/{ticker}` → "Failed to fetch" côté frontend.

**Cause** : LLM2 ajoute `articles_hash` dans le payload upsert de `_call1_dumb_ow24h`, mais la colonne n'avait pas été créée dans la table `stock_news_cache`. PostgREST rejette tout upsert qui référence un champ absent du schéma.

**Fix** :
```sql
ALTER TABLE stock_news_cache ADD COLUMN IF NOT EXISTS articles_hash TEXT;
NOTIFY pgrst, 'reload schema';
```

Le `NOTIFY` peut être envoyé via l'API management Supabase si le SQL Editor ne suffit pas :
```python
httpx.post(f'https://api.supabase.com/v1/projects/{ref}/database/query',
           headers={'Authorization': f'Bearer {mgmt_token}'},
           json={'query': "NOTIFY pgrst, 'reload schema';"})
```

**Règle** : Quand on ajoute un champ à un upsert Supabase, créer la colonne DB en même temps (`ALTER TABLE ... ADD COLUMN IF NOT EXISTS`). Ne pas laisser la migration DB pour après.

---

## 2026-06-12 · llm_service — `_gemini_daily_exhausted` levé sur 429 RPM

**Symptôme** : Après quelques 429 consécutifs sur Gemini (RPM), `_gemini_daily_exhausted=True` était posé en mémoire → Gemini skipé pour toute la session, même après restart VPN. Redémarrage du process requis pour récupérer Gemini.

**Cause** : La vérification `_gemini_daily_exhausted` cherchait `"exhausted"` dans le message d'erreur Google. Google retourne `"RESOURCE_EXHAUSTED"` pour les 429 RPM → le keyword matchait → flag daily posé à tort.

**Fix** : Keywords réduits à `("daily", "per_day", "per_day_free_tier")` — ne matchent que la vraie limite daily (500 RPD), pas les erreurs RPM.

**Fichier** : `services/llm_service.py` — `_try_gemini()`.

---

## 2026-06-12 · llm_service — Gemini throttle absent + 3x429 sans freeze

**Symptôme** : Gemini recevait des requêtes en rafale depuis les workers ThreadPoolExecutor → 429 RPM en boucle. RECAP mentionnait un min_interval de 6.5s mais rien n'était implémenté. Pas de mécanisme de freeze non plus après plusieurs 429 consécutifs.

**Cause** : Throttle et freeze jamais codés malgré la note RECAP.

**Fix** :
- `threading.Lock(_gemini_lock)` + `GEMINI_MIN_INTERVAL=4.1s` — sérialise les appels Gemini entre workers. Sleep sorti du `with` pour ne pas bloquer tous les threads pendant l'attente.
- `_gemini_consecutive_429` + `_gemini_freeze_until` : après 3× 429 RPM consécutifs → freeze jusqu'à minuit UTC. Probe 1h après expiry (si OK → reset ; sinon → +1h). Reset à 0 sur succès.
- Notif TG `gemini_frozen` déclenchée à la pose du freeze.

**Règle** : Sleep à l'intérieur d'un Lock bloque tous les autres threads qui attendent ce Lock — toujours sortir le sleep du bloc `with`.

**Fichier** : `services/llm_service.py` — `_try_gemini()`, globals `_gemini_lock/_gemini_last_call/_gemini_freeze_until/_gemini_consecutive_429`.

---

## 2026-06-12 · llm_service — `_freetheai_exhausted` permanent bloquait monthly_scrap après VPN reset

**Symptôme** : Après restart VPN (nouvelle IP), Flow M (`monthly_scrap`) ne tentait plus FreeTheAI — aucun call visible dans les logs, même après restart Python du batch.

**Cause** : `_freetheai_exhausted` était un flag booléen permanent posé en mémoire et jamais réinitialisé dans la session. L'early-exit `monthly_scrap` vérifiait ce flag → skip systématique.

**Fix** : Remplacé par `_freetheai_skip_until: datetime | None`. L'early-exit `monthly_scrap` vérifie `_freetheai_skip_until is not None and datetime.now(utc) < _freetheai_skip_until` — expiré après le délai configuré. Reset naturel sans restart.

**Fichier** : `services/llm_service.py` — early-exit `monthly_scrap`, variable `_freetheai_skip_until`.

---

## 2026-06-12 · llm_service — Hapuppy 403 freeze agressif même sur erreurs RPM

**Symptôme** : Hapuppy recevait un 403, freezait immédiatement jusqu'à 18h Paris — même si la cause était un rate-limit RPM et non un quota épuisé. Hapuppy est en crédits illimités : un 403 peut être un simple RPM.

**Cause** : Code précédent freezait sur tout 403 sans lire le body.

**Fix** : À la réception d'un 403, lire le body et vérifier les `_HAPUPPY_QUOTA_KEYWORDS`. Si keywords présents → freeze 18h. Sinon → backoff 12s (RPM, retry possible).

**Fichier** : `services/llm_service.py` — bloc Hapuppy dans `_try_hapuppy()`.

---

## 2026-06-12 · refresh_breakers — Ctrl+C bloquant malgré except KeyboardInterrupt

**Symptôme** : Ctrl+C pendant le batch → traceback `KeyboardInterrupt` dans `t.join()` → deuxième Ctrl+C requis → checkpoint jamais sauvegardé.

**Cause** : Les executors étaient dans un `with ThreadPoolExecutor(...) as ex:`. Quand `KeyboardInterrupt` est levé à l'intérieur, Python appelle `__exit__` qui appelle `shutdown(wait=True)` → bloque sur `t.join()` en attendant que les threads sleeping terminent → deuxième Ctrl+C interrompt ce join → le `except KeyboardInterrupt` n'est jamais atteint.

**Fix** : Créer les executors hors du `with` (`ex_ab = ThreadPoolExecutor(...)`). Dans `except KeyboardInterrupt` : `ex_ab.shutdown(wait=False, cancel_futures=True)` + `_save_checkpoint()` + `os._exit(0)`. Dans `else` (chemin normal) : `ex_ab.shutdown(wait=False)` (futures déjà terminées).

**Règle** : Ne jamais utiliser `with ThreadPoolExecutor` quand un handler Ctrl+C propre est requis — `__exit__` appelle `shutdown(wait=True)` inconditionnellement.

**Fichier** : `scripts/refresh_breakers.py` — boucle `as_completed` + handler `KeyboardInterrupt`.

---

## 2026-06-12 · refresh_breakers — `_build_state_from_db` : 0 M générés pour tickers sans articles

**Symptôme** : `--phase m` avec VPN ON → 0 M générés pour 186 tickers en 7m35s. `todo_monthly` = 186 mais aucun monthly produit.

**Cause** : `_build_state_from_db` calculait `monthly_done` uniquement depuis `breaks_monthly`. Les tickers sans articles dans `articles` (mois anciens, tickers ajoutés récemment) n'étaient pas marqués done → inclus dans `todo_monthly` → `_gen_monthly_from_raw` retournait `None` immédiatement (pas d'articles) sans LLM call. 186 tickers réels à générer étaient ~8, les 178 restants n'avaient simplement pas d'articles.

**Fix** : `monthly_done` = tickers avec `breaks_monthly` OU sans articles pour ce mois (`have_articles - have_monthly` = les vraiment générables). Intersection sur tous les mois → seuls les tickers générables ET manquants entrent dans `todo_monthly`.

**Fichier** : `scripts/refresh_breakers.py` — `_build_state_from_db()`.

---

## 2026-06-12 · refresh_breakers — `FreeTheAIExhaustedError` swallowé dans `_gen_monthly_from_raw`

**Symptôme** : Flow M continuait à tourner après épuisement FreeTheAI au lieu de s'arrêter proprement. `_freetheai_monthly_stop` jamais posé → 186 tickers tentés inutilement.

**Cause** : `_gen_monthly_from_raw` et `_gen_monthly_from_weeklies` avaient un `except Exception as e:` générique qui catchait `FreeTheAIExhaustedError` → `_freetheai_monthly_stop.set()` dans `_process_ticker_monthly` jamais atteint.

**Fix** : Dans les deux fonctions, avant le handler générique : `if isinstance(e, FreeTheAIExhaustedError): raise` — laisse remonter pour que `_process_ticker_monthly` catch et pose le stop event.

**Fichier** : `scripts/refresh_breakers.py` — `_gen_monthly_from_raw()`, `_gen_monthly_from_weeklies()`.

---

## 2026-06-12 · refresh_breakers — `done` check OR sur 2 semaines → W2 jamais généré

**Symptôme** : Tickers avec W1 skippés pour W2 → `todo_ab` = ~45 au lieu de 253.

**Cause** : `done` = ticker a un weekly pour l'une OU l'autre des 2 semaines glissantes. Un ticker avec W1 était marqué done → W2 jamais déclenché.

**Fix** : `done` vérifie **uniquement `sliding_weeks[0]`** (la semaine la plus récente). Un ticker sans W2 est systématiquement retenu dans `todo_ab` même s'il a W1.

**Fichier** : `scripts/refresh_breakers.py` — `_build_state_from_db()`.

---

## 2026-06-12 · llm_service — Hapuppy rate-limit 403 interprété comme quota épuisé sous charge

**Symptôme** : Sous VPN (Groq 403 réseau → tous les D basculent en `hapuppy_only`), 253 threads frappent Hapuppy simultanément → Hapuppy retourne 403 avec "daily limit" dans le body (rate-limit) → `_hapuppy_quota_skip_until` posé → toutes les threads suivantes sautent Hapuppy → "hapuppy_only cascade exhausted" pour tous les D. Crédits intacts.

**Cause** : Hapuppy retourne parfois "daily limit" dans les messages de rate-limit RPM, pas seulement sur les vrais épuisements de crédits. Sans throttle, 253 appels simultanés déclenchent ce comportement.

**Fix** : `_hapuppy_adaptive_interval()` — rate-limiter global adaptatif (partagé tous cascades via `_hapuppy_rate_lock`) :
- Groq down OU Gemini down → 0.25s entre chaque call (~4 req/s)
- Les deux down → 0.5s (~2 req/s)
- Aucun down → 0.0s (full speed)
`HAPUPPY_MIN_INTERVAL` env var = override manuel prioritaire.

**Règle** : Quand Groq est injoignable (IP block VPN), tous les D basculent en `hapuppy_only` — le throttle adaptatif empêche la saturation Hapuppy.

**Fichier** : `services/llm_service.py` — `_hapuppy_adaptive_interval()`, bloc Hapuppy dans `llm_call()`.

---

## 2026-06-12 · breakers_articles_service — `_purge_excess_weeklies` et `run_monthly_calendaire` : status non sélectionné

**Symptôme** : `_purge_excess_weeklies` sélectionnait `"year_month, week_num, condensed_at"` sans `status`. Le filtre `w.get("status") is None` retournait toujours `True` (clé absente = None) → les rows sparse/skew/error étaient incluses dans `all_weeks` et pouvaient être purgées. `run_monthly_calendaire` avait le même problème : select sans `status`, `len(week_rows.data) < 2` comptait toutes les rows, et `weeks_text` était buildé depuis les rows skew/error avec du texte vide/None.

**Cause** : Colonne `status` ajoutée après coup. Seuls les flux principaux (`gen_breaks_weekly`, `gen_breaks_monthly`, `_build_state_from_db`) avaient été mis à jour.

**Fix** :
- `_purge_excess_weeklies` : ajout de `status` dans le select.
- `run_monthly_calendaire` : ajout de `status` dans le select + `ok_weeks = [r for r in ... if r.get("status") is None]` + `len(ok_weeks) < 2` + boucle sur `ok_weeks`.

**Règle** : Quand on ajoute une colonne gate (filtre EXISTS ou count), vérifier toutes les requêtes sur cette table, pas seulement les chemins principaux.

**Fichier** : `services/breakers_articles_service.py` — `_purge_excess_weeklies()`, `run_monthly_calendaire()`.

---

## 2026-06-12 · refresh_breakers — EU batch 0W 0M silencieux (sparse coverage)

**Symptôme** : Run EU (45 AB · 101 M tickers en todo) → 0 W générés, 0 M générés. Aucun log sur les skips Monthly.

**Cause (0W)** : ~45 tickers EU avec < 2 articles/semaine → skip logué mais rien en DB. Comportement attendu (EU = Yahoo uniquement, accumulation lente). Pas un bug.

**Cause (0M)** : `_gen_monthly_from_raw` retournait `None` silencieusement pour 101 tickers avec < 3 articles. Aucun log, aucune row DB. `_process_monthlies` comptait le None comme skipped sans remonter l'info dans le TG Done.

**Fix** :
- `_gen_monthly_from_raw` : log `logger.info(f"  ⏭ M {ticker} {year_month} — {len(raw)} arts < {MIN_ARTICLES_FOR_MONTHLY}")` + upsert `status="sparse"` (ou `"no_articles"` si row absente).
- `_process_monthlies` : 4-tuple `(generated, sources, failures, skipped)`.
- `main()` : `total_months_skip` accumulé. TG Done : `· N skipped (arts < 3)` si skipped > 0.

**Note** : ~116 tickers EU/ASIA avec sparse coverage permanente. Chaque run écrit/re-écrit `status="sparse"` en DB (cheap : 0 LLM call). Permet le retry automatique si de nouveaux articles arrivent.

**Fichier** : `scripts/refresh_breakers.py` — `_gen_monthly_from_raw()`, `_process_monthlies()`, `_process_ticker_monthly()`, `main()`.

---

## 2026-06-12 · breaks_monthly / breaks_weekly — upsert status='sparse' silencieusement ignoré (NOT NULL)

**Symptôme** : Après un run EU/ASIA, aucune row `status='sparse'` ou `status='no_articles'` en DB malgré le code qui les écrit. TG affiche "33 skipped" mais DB vide.

**Cause** : `ai_summary`, `article_count`, `condensed_at` étaient `NOT NULL` dans `breaks_monthly`. `ai_summary`, `top_articles`, `article_count`, `condensed_at` étaient `NOT NULL` dans `breaks_weekly`. Les upserts de status sans summary (sparse/no_articles/error/skew) violaient ces contraintes. supabase-py retourne l'erreur sans raise → ignoré silencieusement.

**Fix** : Migration `breaks_nullable_status_columns` — DROP NOT NULL sur ces colonnes dans les deux tables.

**Règle** : Quand on ajoute un upsert partiel (status only), vérifier que toutes les colonnes NOT NULL de la table sont fournies ou rendues nullable.

---

## 2026-06-12 · refresh_breakers — TG 400 silencieux sur runs avec skipped > 0

**Symptôme** : Zéro notification TG pendant les runs EU/ASIA. US OK. `except Exception: pass` dans `_telegram()` — aucun log de l'erreur.

**Cause** : `m_status` contenait `arts < {MIN_ARTICLES_FOR_MONTHLY}` (littéral `<`) quand `total_months_skip > 0`. En `parse_mode: HTML`, `<` doit être `&lt;` — Telegram retourne 400. Les runs US n'avaient pas de skipped, donc jamais de `<` dans le message, donc pas de 400.

**Fix** :
- `arts < N` → `arts &lt; N` dans `m_status` (checkpoint + Done).
- `except Exception: pass` dans `_telegram()` → `logger.warning(f"TG send failed: {e}")`.

**Règle** : Tout `<`, `>`, `&` dans le texte brut d'un message HTML Telegram doit être échappé. Tester les branches où des valeurs dynamiques sont injectées dans le message.

**Fichier** : `scripts/refresh_breakers.py` — `_telegram()`, `main()` (m_status + m_skip_line).

---

## 2026-06-13 · refresh_breakers — `_build_targets` : N×M requêtes DB au lieu de M bulk

**Symptôme** : Au lancement de `python scripts/refresh_breakers.py --region EU --lang en`, les logs montraient des centaines d'appels `articles?select=articles%2Ccached_at&ticker=eq.X&year_month=eq.Y` individuels avant le premier fetch d'article. EU RSS seul = 227 tickers × 12 mois = 2 724 requêtes. Total 4 sources = 7 224 requêtes DB juste pour le check freshness.

**Cause** : `_build_targets` dans `_scrap_phase` appelait `_is_month_fresh(ticker, ym, supa)` dans une double boucle (tickers × year_months) — 1 requête DB par paire (ticker, year_month).

**Fix** : Bulk query : 1 requête par `year_month` (`.eq("year_month", ym).execute()`) récupérant toutes les rows en une fois, puis filtrage en mémoire (`if t not in tickers_set: continue`). 28 requêtes total (12+1+3+12) au lieu de 7 224.

**Règle** : Dans `_build_targets`, toujours bulk-query par `year_month` d'abord, filtrer les tickers en mémoire. Ne jamais appeler `_is_month_fresh` dans une double boucle tickers × months.

**Fichier** : `scripts/refresh_breakers.py` — `_scrap_phase._build_targets()`.

---

## 2026-06-14 · refresh_breakers — "Server disconnected" persistant même à 2 workers (HTTP/2 GOAWAY)

**Symptôme** : `Supabase transient error: Server disconnected` en boucle pendant le post-scrap, même avec 2 workers. Réduction des workers (25→15→9→5→2) n'éliminait pas les erreurs.

**Cause** : postgrest-py hardcode `http2=True` dans `SyncPostgrestClient.create_session()`. PostgREST envoie des GOAWAY frames HTTP/2 pour fermer les connexions idle ou après N requêtes. httpx tente de réutiliser la connexion fermée → `RemoteProtocolError` / `ConnectionTerminated`. Pas lié à la concurrence, mais au protocole HTTP/2.

**Fix** : Dans `_supa()`, après `create_client()`, remplacer `client.postgrest.session` par un `httpx.Client(http2=False, ...)` copiant les headers/timeout/base_url de l'ancien. Force HTTP/1.1 sans désinstaller `h2`.

**Règle** : Si des "Server disconnected" / `ConnectionTerminated error_code:1` persistent peu importe le nombre de workers, vérifier HTTP/2. postgrest-py le force — toujours patcher `_supa()` avec `http2=False`.

**Fichier** : `scripts/refresh_breakers.py` — `_supa()`.

---

## 2026-06-14 · refresh_breakers — `_gen_weekly` upsert sans `status=None` → pending count jamais décroît

**Symptôme** : Pending W count identique entre deux runs successifs. Les `✓ W` apparaissaient dans les logs mais la DB conservait `status="less_10_articles"`. La même semaine était re-générée à chaque run indéfiniment.

**Cause** : En run 1, une semaine avec peu d'articles upsertait `status="less_10_articles"`. En run 2, après scraping additionnel (plus d'articles), la semaine passait le seuil → `_gen_weekly` appelait LLM → upsertait `bw_en`, `top_articles`... mais **sans** `"status": None` dans le dict. Supabase upsert préserve les colonnes absentes → `status` restait `"less_10_articles"`. Le pending checker (`WHERE status IS NULL`) voyait la row comme non-traitée → boucle infinie.

**Fix** : Ajouter `"status": None` dans le dict upsert de `_gen_weekly` pour forcer le reset à null après génération réussie.

**Règle** : Tout upsert "succès" sur `breaks_weekly` doit inclure `"status": None` explicitement pour écraser un éventuel statut d'erreur antérieur.

**Fichier** : `scripts/refresh_breakers.py` — `_gen_weekly()`.

---

## 2026-06-14 · llm_service — Hapuppy SSL EOF sur mass reset (promo gpt-5.5)

**Symptôme** : `EOF occurred in violation of protocol (_ssl.c:2406)` sur plusieurs tickers simultanément à 00:26:50, puis `post-scrap error X: EOF`. Les tickers dont l'exception remontait jusqu'à `_process_ticker` perdaient tout leur traitement (pas de W ni M).

**Cause** : Hapuppy droppait toutes les connexions SSL simultanément (mass reset côté serveur, probablement rate-limit ou restart infra). Le bloc `except Exception` dans la boucle Hapuppy ne retentait qu'1 fois (`range(2)`). `scrap_gen_monthly` et `gen_dumb` n'avaient pas de protection try/except → exception remontait au niveau ticker.

**Fix** :
1. Hapuppy retry : `range(2)` → `range(4)`, backoff `2^attempt + jitter` (1s→2s→4s→8s).
2. `scrap_gen_monthly` : wrappé dans `try/except Exception` dans `_process_ticker` — un M raté ne tue plus le ticker.
3. `_retry_call` : helper qui enveloppe `gen_dumb` et `scrap_gen_monthly` avec retry ×5 sur disconnect Supabase.

**Fichier** : `services/llm_service.py` — boucle Hapuppy · `scripts/refresh_breakers.py` — `_process_ticker`, `_retry_call`.

---

## 2026-06-14 · scrap_gen_monthly — upsert sans `status=None` → M reprocessé à l'infini

**Symptôme** : `M scrap ORA.PA 2026-05 (30 arts · ...)` identique dans deux runs successifs. Pending M count invariant entre runs malgré des `✓ M` dans les logs.

**Cause** : Même bug que `_gen_weekly` (voir entrée précédente), mais pour les monthlies. `scrap_gen_monthly` check : `status IS NULL` → skip si déjà généré. En run 1, une row avec peu de W upsertait `status='less_3_articles'`. En run 2, après génération réussie, les deux chemins (`_gen_monthly_from_weeklies` et chemin raw) upserteaient `bm_en` sans inclure `"status": None` → `status` restait `'less_3_articles'` → check `status IS NULL` échouait → reprocessé à l'infini. Confirmé en DB : `bm_en IS NOT NULL` ET `status='less_3_articles'` sur 2 rows monthly + 1 row weekly.

**Fix code** : `"status": None` ajouté dans les upserts de `_gen_monthly_from_weeklies` et `scrap_gen_monthly` (chemin raw).

**Fix DB** : `UPDATE breaks_monthly SET status = NULL WHERE status IS NOT NULL AND bm_en IS NOT NULL` (2 rows) + idem `breaks_weekly` (1 row).

**Règle** : Tout upsert "succès" sur `breaks_monthly` (les deux chemins) doit inclure `"status": None` explicitement.

**Fichier** : `services/breakers_articles_service.py` — `_gen_monthly_from_weeklies()`, `scrap_gen_monthly()`.

---

## 2026-06-14 · refresh_breakers — Finnhub 429 en rafale + second passage sans skip

**Symptôme** : Sur `--skip-rss-yahoo`, Finnhub recevait les 240 cibles US même si certaines avaient déjà été traitées au run précédent → 429 immédiat sur les mêmes tickers. "0 déjà taggés skippés" à chaque run.

**Cause** : Finnhub n'avait pas de mécanisme de tracking cross-run (contrairement à P2 avec `p2_sources`). De plus, aucun throttle entre les calls → dépassement du rate limit free tier (60 req/min) sur les runs longs.

**Fix** :
- `_fetch_finnhub_month` : retourne `None` sur 429 (au lieu de raise).
- Loop Finnhub : abort après 3 × 429 consécutives. Tag `"finnhub"` dans `p2_sources` après chaque call réussi (ou vide). Pas de tag sur 429 → retry au prochain run.
- `time.sleep(1.0)` entre chaque call Finnhub → ~60 req/min, dans la limite free tier.

**Règle** : Finnhub free = 60 calls/min. Sans throttle, 240 cibles déclenchent le rate limit en moins d'une minute. Le tag `p2_sources["finnhub"]` permet de skipper les paires déjà traitées sur un second passage.

**Fichier** : `services/breakers_articles_service.py` — `_fetch_finnhub_month()` · `scripts/refresh_breakers.py` — loop Finnhub.

---

## 2026-06-14 · refresh_breakers — APIs P2 (NewsAPI/Marketaux/AV) : tag `p2_sources` posé même sur 429

**Symptôme** : Après un run avec 429 massifs sur NewsAPI (97 targets, toutes 429), le prochain run affichait "97 déjà taggés skippés" — aucune retry malgré 0 articles ajoutés. Même comportement attendu pour Marketaux et AV.

**Cause** : `_mark_p2_done` était appelé immédiatement après `_fetch_*_month()`, sans distinction entre "0 articles trouvés" et "429 rate-limit". Les fonctions `_fetch_*_month` catchaient la 429 dans un `except Exception` et retournaient `[]` — indiscernable d'un résultat vide.

**Fix** :
- `_fetch_newsapi_month`, `_fetch_marketaux_month`, `_fetch_av_month` : détection explicite de la 429 (HTTP) et du rate-limit AV (corps JSON `"Information"`) → retour `None` au lieu de `[]`.
- Loop P2 dans `refresh_breakers.py` : `if raw is None: continue` (pas de `_mark_p2_done`) + compteur consécutif → abort après 3 × 429 pour NewsAPI/Marketaux, 2 × pour AV.
- Fix DB one-shot : `UPDATE articles SET p2_sources = array_remove(p2_sources, 'newsapi') WHERE year_month = '2026-06' AND 'newsapi' = ANY(p2_sources)` — 96 rows nettoyées.

**Règle** : `_mark_p2_done` ne doit être appelé que sur succès ou résultat vide légitime. Retourner `None` (pas `[]`) sur rate-limit pour que le caller puisse distinguer.

**Fichier** : `services/breakers_articles_service.py` — `_fetch_newsapi_month()`, `_fetch_marketaux_month()`, `_fetch_av_month()` · `scripts/refresh_breakers.py` — loops NewsAPI, Marketaux, AV.

---

## 2026-06-12 · refresh_breakers — RemoteProtocolError HTTP/2 sur appels Supabase

**Symptôme** : `httpx.RemoteProtocolError: peer closed connection without sending complete message body` sur certains appels `.execute()` Supabase dans `refresh_breakers.py`. Crash non retryé → ticker non traité.

**Cause** : Supabase HTTP/2 ferme parfois la connexion entre deux calls en rafale (TCP keepalive). httpx ne retry pas automatiquement les erreurs de protocole.

**Fix** : Wrapper `_exec(query, retries=2)` — retry jusqu'à 2× avec backoff 0.3s × (attempt+1) sur `RemoteProtocolError`. Toutes les `.execute()` dans le script passent par ce wrapper.

**Fichier** : `scripts/refresh_breakers.py` — `_exec()`, tous les appels Supabase.

---

## 2026-06-15 · scrap scripts standalone — crash `len(None)` sur 429

**Symptôme** : `TypeError: object of type 'NoneType' has no len()` dans `scrap_newsapi.py`, `scrap_marketaux.py`, `scrap_alphavantage.py` quand les APIs retournent un 429.

**Cause** : Les fonctions de fetch (`_fetch_newsapi_month`, `_fetch_marketaux_month`, `_fetch_av_month`) retournent `None` sur 429 (comportement requis par `refresh_breakers.py` — voir entrée 2026-06-14). Les scrap scripts standalone faisaient `len(raw)` dans un `logger.info` avant le check `if not raw: continue` → crash sur `len(None)`.

**Fix** : Guard `if raw is None: continue` ajouté dans les 3 scripts avant le `len()`. Le comportement `return None` du service est inchangé (requis pour la logique `p2_sources` de `refresh_breakers.py`).

**Règle** : Les fonctions de fetch retournent `None` (429/rate-limit) OU `[]` (résultat vide légitime). Toujours distinguer les deux : `if raw is None` avant `len(raw)`, puis `if not raw` pour le skip liste vide.

**Fichiers** : `scripts/scrap_newsapi.py` · `scripts/scrap_marketaux.py` · `scripts/scrap_alphavantage.py`.

---

## 2026-06-15 · `_write_dumb_to_archive` — DUMB non persisté pour articles sans `pub_date`

**Symptôme** : `gen_dumb(ticker, cutoff_days=35)` (path batch) génère le DUMB en mémoire mais ne l'écrit jamais en DB. Les articles concernés n'ont jamais `dumb_en`/`dumb_fr` en DB malgré des runs répétés.

**Cause** : `_write_dumb_to_archive` calcule le `year_month` depuis `a.get("pub_date", "")`. Les articles stockés dans la table `articles` (format interne) ont `datetime` (unix int), pas `pub_date`. `pub_date` est un champ du format frontend, reconstruit à la volée par `_fetch_news`. Dans le path `cutoff_days != 7`, les articles viennent directement de `articles` sans passer par `_fetch_news` → pas de `pub_date` → la fonction skippe tous les articles silencieusement.

**Note** : Dans le path prod (cutoff_days=7), `_fetch_news` reconstruit `pub_date` depuis `datetime` avant de retourner les articles → pas impacté.

**Fix** : Fallback sur `datetime` dans `_write_dumb_to_archive` :
```python
pub_date = a.get("pub_date", "")
if not pub_date and a.get("datetime"):
    pub_date = datetime.fromtimestamp(a["datetime"], tz=timezone.utc).isoformat()
```

**Règle** : `pub_date` (ISO string) n'existe que dans les articles normalisés par `_fetch_news`. Les articles bruts DB ont `datetime` (unix int). Ne jamais requérir `pub_date` sans fallback `datetime`.

**Fichier** : `services/overview_service.py` — `_write_dumb_to_archive()`.
