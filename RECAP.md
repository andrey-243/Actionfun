# StockVibe — Recap

## Navigation rapide

| Section | Contenu |
|---|---|
| [Règles & nommage](#règles--nommage) | Termes produit (OW/DUMB/BUB/Breaks), source articles |
| [Stack](#stack) | Backend, frontend, auth, DB, crons |
| [Design](#design-da) | Palette, typographie, composants |
| [Lancer](#lancer) | Commandes dev local |
| [Déploiement prod](#déploiement-prod) | Config VPS, env vars |
| [Structure fichiers](#structure-fichiers) | Arborescence backend/frontend |
| [Fonctions publiques](#fonctions-publiques--cascades--actions) | API interne, cascades services |
| [LLM Cascade](#llm--cascade--modèles) | FreeTheAI/Hapuppy/Groq, prompts, fallbacks, quotas |
| [Supabase Tables](#supabase--tables-actives) | Schéma DB, colonnes, RLS |
| [Articles — Architecture](#articles--architecture-par-période) | Flow scraping, périodes, merge multi-source |
| [Articles — Doublons](#articles--doublons--multi-source) | Dédup, conflits sources |
| [Rotation des données](#rotation-des-données) | TTL, purge, archivage |
| [News Architecture](#news--architecture) | my-news, daily/weekly digest |
| [Events / Dots chart](#events-dots-chart) | BUB, dots, chart events |
| [Chart — Périodes](#chart--périodes--intervalles-yfinance) | Intervalles yfinance par période |
| [Resend Email](#resend--email) | Config, templates, sandbox |
| [Tickers Coverage](#tickers--coverage) | Marchés couverts, suffixes |
| [Batch fill DB](#batch-scraper--backendscriptsbatch_fill_dbpy) | Script fill initial |
| [Batch breakers](#batch-breakers--backendscriptsrefresh_breakerspy) | Script refresh breakers |
| [État courant — Features](#features) | Statut de chaque feature |
| [Séquence batch](#séquence-batch--en-first-relance-2026-06-01) | Ordre et état du batch en cours |
| [Optimisations réalisées](#optimisations-réalisées-) | Historique des perf fixes |
| [Roadmap post-beta](#roadmap-post-beta) | Prochaines features |

---

## Architecture

### Règles & nommage

**Source de données — règle absolue :**
Toute génération IA (OW, BUB, Breaks, Daily Digest, Weekly Digest) doit lire depuis `articles` (merge Finn+Yahoo).
Ne jamais appeler Finnhub ou Yahoo directement pour alimenter le LLM — les crons maintiennent `articles` à jour.
Si une feature bypass `articles` c'est un bug architectural à corriger.

**Nommage — toujours utiliser les termes suivants :**
| Terme | Signifie |
|---|---|
| OW | AI Overview (24H / 7J) — `overview_service.py` |
| DUMB | Résumé AI 2-3 phrases par article — champs `dumb_en`/`dumb_fr` dans les objets article |
| BUB / bubble | Event cause sur un dot du chart — `bubble_service.py` |
| Breaks | Résumés W1/W2/W3/W4 (1M) ou mensuels (6M/1Y) — `breakers_articles_service.py` |
| Daily Digest (1J) | Digest end-of-day — page my-news + email 19h15 · langue selon `user_preferences.lang` |
| Weekly Digest (7J) | Digest hebdo — email sam 7h · OW 7J + 5 articles · langue selon `user_preferences.lang` |
| articles | DB mergée multi-sources — source de vérité unique pour tous les articles |

---

### Stack

| Layer | Tech |
|---|---|
| Frontend | Next.js 15 · React 19 · TypeScript · Tailwind v4 |
| Backend | FastAPI (Python) · uvicorn · port 8000 |
| Auth | Supabase (`@supabase/supabase-js` singleton browser) · email/password + Google + Discord OAuth |
| Fonts | Inter (UI) · Space Mono (tickers/numbers) |
| Charts | Chart.js · react-chartjs-2 |
| Données bours. | yfinance 1.4.1 + curl_cffi (bypass Yahoo rate limit) |
| News | yfinance `get_news()` → Groq llama-3.3-70b → cache Supabase · **Marketaux** (EU+ASIA prod cron */6h + scrap) · **NewsAPI** (EU+ASIA prod cron */6h + scrap P1→P4) · **Google News RSS** (tous tickers prod */3h + scrap fallback) · **Alpha Vantage NEWS_SENTIMENT** (scrap last resort P1→P4, 25 req/j) |
| Taux de change | Frankfurter API (`api.frankfurter.dev/v1`) — gratuit, taux BCE |
| Events chart | Finnhub company-news (free, 1 an d'historique) + Groq cause IA |
| Email digest | Resend (3 000 emails/mois gratuit) — clé configurée, sandbox actif · Daily 19h15 + Weekly sam 7h · langue via `user_preferences.lang` (DEFAULT 'en') · Azure batch amont avant snapshot |
| Logos | Logo.dev free (500k req/mois, attribution requise) — domaine depuis yfinance `t.info["website"]`, stocké dans `ow_cache.website` · `NEXT_PUBLIC_LOGODEV_TOKEN` dans `frontend/.env.local` |
| Cron | APScheduler intégré dans FastAPI — email 18h30 · cold_start FR 19h · re-enrich 22h30 (+ retry :35 toutes les heures, actif si pending) · weekly lun 3h30 · monthly 1er 4h · yearly 1er jan 5h · **yahoo `*/3h :00`** (tous pinnés ≥1 user, merge articles, gen_dumb inline, pose `needs_ow_regen=True` si ajout) · **finnhub `*/3h :05`** (current month, merge, gen_dumb inline, pose `needs_ow_regen=True` si ajout) · **google_rss `*/3h :10`** (tous pinnés ≥1 user, gen_dumb inline, pose `needs_ow_regen=True` si ajout) · **hourly_news_refresh :15** (pinned + visités 6h avec `no_24h_news=True` → `_accumulate_yahoo` puis `gen_ow_both(force_24h=True)` si articles 26h) · **hourly_ow_regen :20** (lit `ow_cache.needs_ow_regen=True` → `gen_ow_both(force_24h=True)` par ticker → clear flag) · **T1 `*/6h :55`** (EU+ASIA Marketaux+NewsAPI · US AV · filtre 0 article ce mois · check `tried_sources`) · **T2 `dim 22h00`** (tous pinnés < 10 arts/semaine · skip si T3 day) · **T3 `dernier j du mois 23h00`** (tous pinnés < 25 arts/mois) · **purge DB** lun 3h (bub_cache >1 an). TG: tous les crons 1x/j envoient ▶ start + ✅ end avec durée. Hourly: TG uniquement si des tickers sortent du pool no_24h_news. Re-enrich retry : freeze jusqu'à 2h si FreeTheAI down à 22h30, puis check toutes les 1h. |
| Git | github.com/andrey-243/Actionfun — repo privé |
| Déploiement | **Render** Web Service (backend, free 750h/mois) · **Vercel** (frontend, free) · **Supabase** (DB, déjà en place) · **UptimeRobot** (keep-alive ping /health toutes les 5 min) |

---

### Design (DA)

Theme **dark earthy** — bg `#141412` · surfaces `#1E1D1B` · accent vert menthe `#4CAF82` · texte crème `#E8E5DF`.
Vert `#4CAF82` / rouge `#E05C4B` pour les variations de cours.
**Largeur** : `maxWidth: 880px` sur toutes les pages (`page.tsx`, `stock/[ticker]/page.tsx`, `my-news/page.tsx`, `profile/page.tsx`, `Navbar.tsx`).
**Chart** : hauteur `240px` · `maintainAspectRatio: false`.
**StatBox** : padding `8/12px` · label `10px` · valeur `13px mono bold`.

**Navbar** — frosted glass `rgba(14,14,12,0.45)` + `blur(22px)` · hauteur 66px · logo chien SVG (tracé crème, rotation anti-horaire 60s) dans container menthe · `NavBrand` + `BackButton` + `Avatar` (exported) réutilisables sur toutes les pages · avatar OAuth rond (Google/Discord) · pill "Mes news" accent (homepage uniquement) · pages secondaires (stock, my-news) : avatar seulement ou rien.

---

### Lancer

```bash
# Backend
cd backend
source .venv/bin/activate
uvicorn main:app --reload --port 8000

# Frontend
cd frontend
npm run dev  # http://localhost:3000
```

---

### Déploiement prod

| Service | Plateforme | Config |
|---|---|---|
| Backend (FastAPI + APScheduler) | Render Web Service | Root dir: `backend/` · Start: `uvicorn main:app --host 0.0.0.0 --port $PORT` |
| Frontend (Next.js 15) | Vercel | Root dir: `frontend/` · auto-détecté |
| DB + Auth | Supabase | Déjà en place |
| Keep-alive crons | UptimeRobot | Ping `GET /health` toutes les 5 min → empêche le sleep Render (15 min inactivité) |

**Variables d'env** : copier le contenu de `backend/.env` dans Render → Environment Variables. Copier `frontend/.env.local` dans Vercel → Environment Variables.

**Endpoint à ajouter avant deploy** : `GET /health` → `{"ok": true}` dans `main.py` (requis pour UptimeRobot + Render health checks).

**Crons** : APScheduler tourne dans le process FastAPI — pas de Render Cron Jobs séparés. Le keep-alive UptimeRobot suffit à maintenir le scheduler actif.

---

### Structure fichiers

```
stock_app/
├── backend/
│   ├── main.py
│   ├── routers/
│   │   ├── stock.py       ← GET /api/stock/{ticker}?period=
│   │   ├── news.py        ← GET /api/news/{ticker}
│   │   ├── search.py      ← GET /api/search
│   │   └── events.py      ← GET /api/events/{ticker}?date=&lang= · GET /api/bub-dates/{ticker}?lang= · POST /api/translate-bub/{ticker}/{date}
│   ├── services/
│   │   ├── yfinance_service.py
│   │   ├── overview_service.py
│   │   ├── bubble_service.py
│   │   ├── breakers_articles_service.py
│   │   ├── digest_service.py
│   │   ├── email_service.py
│   │   ├── llm_service.py
│   │   ├── cold_start_service.py
│   │   └── articles_store.py           ← Turso HTTP API (write/fetch/delete/merge heavy fields) · inactif sans TURSO_URL
│   ├── scripts/
│   │   ├── batch_fill_db.py            ← stateless (DB source of truth) · phases: scrape/enrich/bubbles/translate-fr/translate-fr-dumb · génère article.id à la création
│   │   ├── refresh_breakers.py         ← scrape P1+P2 + DUMB + W + M breakers
│   │   ├── backfill_article_ids.py     ← one-shot backfill id=sha1(url)[:16] sur tous les articles Supabase existants
│   │   ├── migrate_articles_cold.py    ← migration Supabase→Turso 3 phases · --phase 1/2/3 · --init-db · --dry-run · --ticker
│   │   ├── zen_watcher.py              ← probe ZenLLM 5 min → batch bubbles UP · kill si 3 échecs consécutifs · reprobe · --cascade (défaut: zen)
│   │   ├── translate_bulk.py           ← bulk EN→FR : BUB → Y → M → W · cascade promo (Gemini)
│   │   ├── populate_logos.py
│   │   ├── scrap_google_rss.py         ← top 1 scrap · 12 mois · max 100 art/call · tous tickers · skip is_fresh · cron */3h · TG fin de run + source_counts
│   │   ├── scrap_newsapi.py            ← P1→P4 · 1 mois (plan Dev gratuit) · 100 req/j · cron */6h · TG fin de run + source_counts
│   │   ├── scrap_marketaux.py          ← P1→P4 · 3 mois (plan Basic) · 100 req/j · cron */6h · TG fin de run + source_counts
│   │   └── scrap_alphavantage.py       ← last resort · P1→P4 · 12 mois · 25 req/j · cron indirect via refresh_breakers · TG fin de run + source_counts
│   ├── .env
│   ├── .venv/
│   └── requirements.txt
├── frontend/
│   ├── app/
│   │   ├── page.tsx
│   │   ├── stock/[ticker]/page.tsx
│   │   ├── profile/page.tsx
│   │   ├── my-news/page.tsx
│   │   ├── components/
│   │   │   ├── Navbar.tsx
│   │   │   ├── AuthModal.tsx
│   │   │   ├── ArticleDrawer.tsx
│   │   │   ├── VideoModal.tsx
│   │   │   ├── EventDrawer.tsx
│   │   │   └── DogLoader.tsx
│   │   └── lib/
│   │       ├── supabase.ts
│   │       ├── useCurrencyPref.ts
│   │       ├── useLangPref.ts
│   │       └── useExchangeRate.ts
│   ├── .env.local
│   └── package.json
├── CLAUDE.md
├── RECAP.md
└── ERRORS.md
```

---

### Fonctions publiques — cascades & actions

| Fonction | Cascade prod | Cascade batch/scrap |
|---|---|---|
| `gen_ow_both()` | DUMB: `dumb_prod` Hapuppy→Gemini→Groq · OW 24H: `ow24h_prod` Gemini→Hapuppy · OW 7J: `ow7j_prod` Gemini→Hapuppy | — |
| `get_or_refresh_ow_both()` | → appelle `gen_ow_both()` si stale | — |
| `gen_dumb(cutoff_days=7)` | `dumb_prod` Gemini→Hapuppy→Groq | `zen` ZenLLM→raise (original: `promo`). `cutoff_days` : fenêtre articles (7j prod/crons · 35j batch fix scripts) |
| `read_breaks_by_period()` | Lit `breaks_weekly/monthly/yearly` + `articles` | idem |
| `read_breaks_periods_available()` | Lit `breaks_monthly/yearly` | idem |
| `gen_breaks_weekly()` | `weekly_prod` Hapuppy→Gemini | `zen` ZenLLM→raise (original: `promo`) |
| `gen_breaks_monthly()` | `monthly_prod` FreeTheAI→Hapuppy | `zen` ZenLLM→raise (original: `promo`) |
| `gen_breaks_yearly()` | `yearly_prod` FreeTheAI seul | — |
| `get_or_gen_breaks_monthly_ondemand()` | `monthly_prod` FreeTheAI→Hapuppy | — |
| `get_or_gen_bub()` | `bub_prod` Gemini→Hapuppy | `bub_prod` Gemini→Hapuppy | params : `cascade`, `prefetched_cache`, `force_regen` (bypass cache), `in_1w/1m/6m/1y`, `lang` (override `LLM_LANG` · si `lang=fr` et `bub_fr` null → fallback `bub_en` avec `is_fallback=True` dans la réponse) · interne : `_call_llm_for_bub()` |
| `send_daily_digest()` | Lit `ow_cache` · Azure si FR manquant · envoie Resend | — |
| `send_weekly_digest()` | Lit `ow_cache` · Azure si FR manquant · envoie Resend | — |

---

### LLM — Cascade & modèles

Tous les appels LLM passent par `services/llm_service.py` qui gère la cascade automatiquement.

**Cascade par mode (`cascade` param de `llm_call()`) :**

| Mode | Providers (ordre) | Qui l'utilise | Notes |
|---|---|---|---|
| `default` | FreeTheAI → Hapuppy → Groq | live OW, BUB si non précisé | |
| `zen` | ZenLLM → raise | refresh_breakers.py (toutes phases) · regen_bubs.py · batch_fill_db.py (bubbles) | **ZEN PROMO actif** — waterfall `free/gpt-5.5` → `free/claude-sonnet-4.6` · clé `ZENLLM_API_KEY` · bloc dédié · `BUBBLES_WORKERS=1` forcé (zen_watcher) · `ZEN_MIN_INTERVAL=10s` entre calls · après timeout client-side : `ZEN_TIMEOUT_COOLDOWN=50s` additionnel (slot serveur occupé → 429 concurrence sinon) · kill batch si 3 échecs LLM consécutifs ("LLM failed" par bubble, pas par modèle) → reprobe 5 min · régions configurables `--region US,EU,ASIA` · **fin promo** : passer `--cascade promo` ou `bub_batch` au watcher/script |
| `promo` | Gemini → raise | scripts ponctuels / debug | `max_tokens` non envoyé (défaut Gemini 2000) |
| `dumb_prod` | Hapuppy → Gemini → Groq | DUMB prod (crons) | |
| `ow24h_prod` | Gemini → Hapuppy | OW 24H prod | |
| `ow7j_prod` | Gemini → Hapuppy | OW 7J prod | |
| `bub_prod` | Gemini → Hapuppy | BUB prod (event dots) | |
| `weekly_prod` | Hapuppy → Gemini | Breaks W prod · cold_start · reenrich | |
| `monthly_prod` | FreeTheAI → Hapuppy | Breaks M prod · cold_start · reenrich | |
| `yearly_prod` | FreeTheAI → raise | Breaks Y prod | lève `RuntimeError` si quota |
| `weekly_scrap` | Gemini → Hapuppy → raise | scrap scripts standalone (google_rss · newsapi · marketaux · alphavantage) | défaut de `scrap_gen_weekly` |
| `monthly_scrap` | FreeTheAI → raise | scrap scripts standalone — mois fresh (≥25 arts, <3j) | lève `FreeTheAIExhaustedError` si quota |
| `monthly_scrap_ext` | FreeTheAI → Hapuppy | scrap scripts standalone — mois non-fresh | défaut de `scrap_gen_monthly` si non-fresh |
| `dumb_scrap` | Groq → Hapuppy → raise | scrap scripts standalone (gen_dumb post-scrap) | |
| `bub_batch` | FreeTheAI → Gemini → raise | — (temporairement remplacé par `zen`) | comportement original batch_fill_db.py -- phase bubbles |

**Règle FreeTheAI** : restreint aux cascades `monthly_prod`, `yearly_prod`, `monthly_scrap`, `monthly_scrap_ext`, `bub_batch` uniquement. Toutes les autres cascades (prod temps réel, batch W/DUMB/promo) n'appellent jamais FreeTheAI.
Invocations réelles : cold_start_service.py:289 · reenrich_service.py:181 · breakers_articles_service.py:1248/1278/1356/1864/1972/2112 · refresh_breakers.py:439/1106/1190 · translate_bulk.py:140

**Cascade default :**
```
FreeTheAI: bbl/grok-4.1-fast-non-reasoning → primary (remplacé 23/06/2026 — anciens alias bbl/gpt-4.1/gpt-5.4-mini invalides)
  ✗ 502/503/timeout          → try secondary
  ✗ 429 (RPM) ×3 consécutifs → freeze 3h (FREETHEAI_429_FREEZE_SECONDS=10800)
  ✗ 403/checkin              → break, skip jusqu'à minuit UTC
  ✗ daily_limit              → break, skip jusqu'à minuit UTC
  Concurrence : Semaphore(FREETHEAI_CONCURRENCY=1) non-bloquant — 1 seul appel simultané,
                les autres sautent directement à Hapuppy

FreeTheAI: bbl/gemini-3.5-flash → secondary (seulement si primary = 502/503/timeout)
  mêmes règles d'erreur
  → 3 rounds échoués consécutifs (502/503/timeout) → circuit breaker 30min → probe auto
  → reset sur succès · 429/checkin/daily ne comptent pas comme "round échoué"

Gemini: gemini-3.1-flash-lite → selon mode (avant Hapuppy pour ow24h_prod/dumb_prod/weekly_scrap · après pour bub_prod/weekly_prod)
                                 API native Google (pas OpenAI-compatible) : POST generativelanguage.googleapis.com/v1beta/models/{model}:generateContent
                                 3 clés en rotation : GEMINI_API_KEY · GEMINI_API_KEY_2 · GEMINI_API_KEY_3 (comptes Google indépendants)
                                 État per-clé : bad_key · daily_exhausted · consecutive_429 · freeze_until · ratelimit_until · last_call
                                 Sélection : _pick_best_key(tried: set[int]) → clé avec minimum wait, skip clés déjà tentées — 3 clés utilisées en parallèle (~43/min)
                                 Throttle : Lock(_gemini_lock) + GEMINI_MIN_INTERVAL=4.1s par clé — réservation virtuelle dans le lock, sleep sorti du lock
                                 429 RPM × 3 consécutifs → freeze_until = now+15min · probe auto 1h après expiry
                                 429 RPM → body contient "resource has been exhausted" → consecutive_429 += 1 (pas daily_exhausted)
                                 429 daily → body contient "exceeded your current quota" → daily_exhausted=True (keyword exact — "quota" seul trop générique, les RPM l'incluent aussi)
                                 ⚠️ Piège : "quota" apparaît dans LES DEUX types de 429 Google. Seul "exceeded your current quota" est spécifique au daily. Fix 23/06/2026.
                                 400/403 → bad_key · timeout/ConnectError/statut inattendu → continue (rotation) · sur succès → consecutive_429 = 0, freeze_until = None

Hapuppy: hapuppy/gpt-5.5 → hapuppy/gpt-5.4 (promo temp — gratuit sans crédits · à remettre sur gpt-4.1-mini après) → 3 retries avant de passer à Groq
                               Call en streaming httpx : read=15s par chunk (trous noirs détectés en 15s,
                               réponses lentes mais réelles préservées si chunks arrivent progressivement)
                               403 : lit le body d'abord — si keywords quota/credit/budget → freeze 18h Paris · sinon → backoff 12s (RPM, crédits illimités)
                               429 quota : body keywords ("quota","credit","budget"...) → freeze jusqu'au prochain 18h00 Paris (reset Hapuppy)
                               429 rate-limit : sleep 5s local + retry dans le même thread (pas de freeze global) — max 3 tentatives
                               timeout/502/SSL EOF/connexion → retry x4, backoff 1s→2s→4s→8s + jitter (was x2)
                               Concurrence : Semaphore(HAPUPPY_CONCURRENCY=25) — max 25 appels httpx simultanés (testé : 25/25 succès, avg 2.8s)
                               Limite : 100 req/min · Rate-limiter adaptatif (_hapuppy_adaptive_interval) selon état Groq + Gemini
                                 Groq down OU Gemini down → 0.8s (~75 RPM) · les deux down → 1.2s (~50 RPM) · aucun down → 0.0s
                                 HAPUPPY_MIN_INTERVAL env var = override manuel prioritaire
                                 Sérialisation via _hapuppy_rate_lock (threading.Lock) — partagé tous cascades

Groq: voir tableau ci-dessous → fallback final (uniquement si Hapuppy quota épuisé ou down)
                                Bloqué pour tous les modes prod sauf dumb_prod (qualité insuffisante)
```
deepseek-v3.2 retiré — timeouts constants (endpoint deprecated chez Hapuppy).
AgentRouter (`agentrouter.org`) retiré — clé `unauthorized_client`.

**Comportement erreurs FreeTheAI :**
- **403 / checkin manquant** : faire `/checkin` sur le Discord FreeTheAI pour débloquer
- **Daily limit** : reset automatique à minuit UTC
- **429 × 3 consécutifs** : freeze 3h — typique en batch concurrent (10 RPM limit, 1 concurrent)
- **3 rounds échoués consécutifs** (502/503/timeout) : circuit breaker 30min, probe auto après
- **Semaphore(1) non-bloquant** : si FreeTheAI occupé, le thread saute à Hapuppy sans attendre

**Comportement erreurs Hapuppy :**
- **timeout/connexion** : call en streaming, read=15s/chunk — trous noirs coupés en 15s, vraies réponses lentes préservées. Backoff 1s/2s, 3 retries, puis Groq
- **403** : lit le body — si keywords quota/credit/budget → freeze 18h Paris + notif TG `hapuppy_quota` · si pas de keyword → backoff 12s (erreur RPM, crédits illimités)
- **429 quota épuisé** (body contient "quota"/"credit"/"budget"...) : freeze jusqu'au prochain 18h00 Paris
- **429 rate-limit** (body sans keyword quota) : sleep 5s local + retry Hapuppy dans le même thread — pas de freeze global, Groq non déclenché
- **Unfreeze manuel** : `POST /admin/hapuppy/unfreeze` — reset freeze dans le process FastAPI (⚠ n'affecte pas un batch standalone)

**Notifs Telegram — trail complet de la cascade :**
```
🟠 Hapuppy `gpt-4.1-mini` — down (502/timeout)

FreeTheAI `bbl/grok-4.1-fast-non-reasoning` : ✗ 503
FreeTheAI `bbl/gemini-3.5-flash` : ✗ 503
Hapuppy `gpt-4.1-mini` : ✗ timeout/connexion
Groq `llama-3.1-8b-instant` : ✓
📍 OW
```
- Une notif par raison primaire, throttle 30min — `_send_cascade_notif(reason, trail, caller="")`
- Trail buildé au fil de la cascade dans `llm_call`, envoyé quand un non-primary répond (✓ ou ✗)
- Si tous les LLM échouent → notif `🔴 Tous les LLM en échec` avec trail complet
- **`caller` param** — `llm_call()`, `llm_text()`, `llm_json()` acceptent `caller: str = ""`, propagé au footer : `📍 OW` · `📍 BUB` · `📍 Breaks`. Si non fourni : `📍 llm_service`. `translation_service` : `📍 translation_service` (sa propre cascade, pas de param caller).
- **`item_id` param** — `llm_call()`, `llm_text()`, `llm_json()` acceptent `item_id: str = ""` (ticker en pratique). Utilisé uniquement pour le log d'échec complet — pas affiché dans les notifs Telegram.
- **Log échecs complets** — `~/llm-stack/logs/llm_cascade_failures.jsonl`. Entrée écrite seulement pour `groq_exhausted` et `all_failed` (= aucun provider n'a répondu, item non traité). Format : `{"ts", "reason", "caller", "item_id"}`. Pour retrouver les tickers à rejouer : `jq -r 'select(.reason == "all_failed" or .reason == "groq_exhausted") | .item_id' ~/llm-stack/logs/llm_cascade_failures.jsonl | sort -u`
- **Trigger `both_quota`** — juste avant le bloc Groq dans `llm_call()` : si `_freetheai_skip_until is not None` ET `_hapuppy_quota_skip_until is not None` ET `not _hapuppy_quota_notif_sent` → notif `🔴 FreeTheAI + Hapuppy hors quota` avec heures de reset dynamiques (Paris pour Hapuppy, UTC pour FreeTheAI), throttle 30min. Supprimé si `hapuppy_quota` vient de partir dans ce même call.
- **Dédup 3→1 notif** — `_hapuppy_quota_notif_sent` (local à `llm_call`) : quand `hapuppy_quota` part, ce flag supprime `both_quota` ET `hapuppy_down` au succès Groq pour éviter les doublons. `hapuppy_down` aussi supprimé si `_hapuppy_quota_skip_until is not None` (quota ≠ down).
- **Headers dynamiques** — `hapuppy_quota` et `both_quota` calculent les heures de reset réelles au moment de l'envoi via `header_override` dans `_send_cascade_notif`. Format : `Reset 18h00 Paris (dans 1h58m)`. `_CASCADE_HEADERS` garde les fallbacks.

**Raisons notifs cascade (`_CASCADE_HEADERS`) :**
| Raison | Déclencheur |
|---|---|
| `checkin` | FreeTheAI 403 / type `daily_checkin_required` |
| `daily_limit` | FreeTheAI `daily successful request limit exceeded` |
| `rpm_429` | FreeTheAI 429 (< seuil freeze) |
| `freetheai_down_frozen` | 3 rounds 502/timeout → circuit breaker 30min |
| `freetheai_429_frozen` | 3 × 429 consécutifs → freeze 3h |
| `hapuppy_down` | Hapuppy 502/timeout → Groq utilisé |
| `hapuppy_quota` | Hapuppy 403 ou 429 quota → freeze 18h Paris |
| `both_quota` | FreeTheAI quota + Hapuppy quota simultanément épuisés |
| `groq_exhausted` | Groq 3 × 429 consécutifs → quota daily épuisé |
| `all_failed` | Tous les LLM en échec |

**FreeTheAI — quotas journaliers (rôle Discord) :**
| Rôle | Requêtes/jour | Images/jour |
|---|---|---|
| Seems Legit | 400–500 | 30 |
| Sans rôle | 250 | 5 |
| Suspicious | 50 | 2 |

**Mapping modèles par usage :**
| Usage | Contexte | FreeTheAI/Hapuppy | Groq fallback |
|---|---|---|---|
| BRUTALIZE (30 articles → résumé 7J) | site + batch | `bbl/grok-4.1-fast-non-reasoning` | `llama-4-scout-17b` ¹ |
| SUMMARY_24H | site + batch | `bbl/grok-4.1-fast-non-reasoning` | `llama-3.1-8b-instant` ¹ |
| EVENT_CAUSE (dot chart) | site | `bbl/grok-4.1-fast-non-reasoning` | `llama-3.1-8b-instant` |
| DIGEST DAILY (1J) | email 19h15 | — (lit `ow_cache.ow1d_en/ow1d_fr` selon `user_preferences.lang` · Azure pre-flight si FR manquant · snapshot mémoire · zéro LLM) | — |
| DIGEST WEEKLY (7J) | email sam 7h | — (lit `ow_cache.ow7d_en/ow7d_fr` selon `user_preferences.lang` · 5 articles 7j · même pre-flight Azure) | — |
| WEEKLY / MONTHLY condensation | site + batch | `bbl/grok-4.1-fast-non-reasoning` | `llama-3.3-70b-versatile` ¹ |
| TRADUCTION EN→FR | batch + site | Azure primary | Azure → Groq `llama-3.1-8b-instant` → Hapuppy `gpt-4.1-mini` |

¹ Groq désactivé quand `BATCH_MODE=1` — le batch échoue proprement sur le ticker et skip.

**Règle invariante traduction :** EN est toujours généré en premier par le LLM principal. FR = traduction de l'EN uniquement, jamais généré directement. Si tout le waterfall de traduction échoue → EN conservé en DB, FR vide, retry au prochain batch. On ne peut pas avoir `FR filled + EN empty`.

**Règle nommage colonnes bilingues :** tout champ pouvant exister en EN et FR doit avoir le suffixe `_en` ou `_fr`. Jamais de colonne vanilla `ai_summary` ou `cause` si un double contenu langue peut exister.

**`translation_service.py` — waterfall :**
```
Azure Translator (batch natif 25 items/appel, ~200ms)
  → Groq llama-3.1-8b-instant
  → Hapuppy gpt-4.1-mini (quota skip partagé avec llm_service)
```
- Quota skip Hapuppy partagé avec `llm_service` (même état en mémoire — `_hapuppy_quota_skip_until`)
- FreeTheAI retiré du waterfall traduction (réservé aux cascades monthly_prod/yearly_prod/monthly_scrap)
- Trail + notif TG throttlée 30min — notif si Azure down, provider fallback utilisé, ou tous down
- Azure 2M chars/mois free tier — OW non inclus dans batch (trop volatile, refresh 3h)
- **Azure = primary pour digest** : 8 textes en 0.68s batch · qualité bonne sur financier standard · artefacts rares (`rassemblement` pour "rally", gérondif mal conjugué) non bloquants pour email
- **Hapuppy** : URL `beta.hapuppy.com` (pas `api.hapuppy.com` — 503) · latence ~10s individuel · instable en charge (timeouts fréquents) · légèrement meilleur sur termes techniques spécialisés (ex: "runtime" conservé vs Azure qui traduit "en temps réel")
- **Digest quota estimé** : ~500 chars × 20 tickers × 2 digests/semaine ≈ 80K chars/mois (4% du free tier Azure)

**`translate_bulk.py` — script local bulk traduction :**
Pass 1 : FreeTheAI (item 0 réservé, cascade="bub_batch") + Gemini (items 1..N, cascade="promo") en parallèle via ThreadPoolExecutor.
Pass 2 : FreeTheAI séquentiel pour les échecs Gemini restants (None → retry bub_batch).
`translation_service.py` inchangé comme backup prod. `TRANSLATE_PROMPT` importé depuis `translation_service.py` (source unique).
- Filtre : `_en` non-null ET `_fr` null uniquement — idempotent, relançable sans risque
- Ordre de priorité : BUB → Y breakers → M breakers → W breakers
- Filtres : `--region US|EU|ASIA`, `--ticker TICKER`, `--since 2h|30m|3d`, `--only bub,y,m,w`
- `--workers N` (défaut 10) : FreeTheAI garanti actif même si Gemini sature — ~43/min avec 3 clés Gemini
- Erreurs par traduction catchées individuellement (None) : la row reste `_fr=null` → reprocessée au prochain run automatiquement
- `--dry-run` : compte les lignes sans écrire

**FreeTheAI — modèles disponibles (23/06/2026) :**
Benchmark 23/06/2026 — podium qualité (traduction FR + condensation analytique) :
| Modèle | Latence | Qualité | Statut |
|---|---|---|---|
| `bbl/grok-4.1-fast-non-reasoning` | ~17s | ⭐ meilleure (vocab financier précis, "bénéfices") | PRIMARY |
| `bbl/gemini-3.5-flash` | ~18s | très bonne | SECONDARY |
| `olm/deepseek-v3.1` | ~7s | bonne | éliminé (condensation moins analytique) |
| `opc/deepseek-v4-flash-free` | ~5s | ❌ reasoning model, content vide | éliminé |
| `kai/stepfun/step-3.7-flash:free` | ~6s | ❌ content vide (finish=length) | éliminé |
| `glm/*` | varies | ❌ reasoning models, content vide | éliminés |
| `bbl/gpt-5.5-mini` | ~18s | bonne traduction FR | non retenu |
| `bbl/gemini-2.5-flash-lite` | ~15s | bonne traduction FR | non retenu |

**Groq — limites free tier :**
| Modèle | TPM | TPD | Usage |
|---|---|---|---|
| `llama-4-scout-17b` | 30K | 500K | Calls lourds |
| `llama-3.1-8b-instant` | 6K | 500K | Calls légers/fréquents |
| `llama-3.3-70b-versatile` | 12K | 100K | Condensations crons (rares) |

Groq bloqué en prod pour tous les modes sauf `dumb_prod` (qualité insuffisante pour summaries). Autorisé en batch (`BATCH_MODE=1`) via `groq_first` cascade.

**Token usage logging — dashboard local :**

Chaque call LLM réussi est loggué automatiquement dans `~/llm-stack/token_usage.jsonl` via `_log_usage()` dans `llm_service.py` (et `translation_service.py`). Le dashboard [localhost:5555](http://localhost:5555) lit ce fichier en temps réel.

```
llm_service.py / translation_service.py
    _log_usage(model, tokens_in, tokens_out)
        ↓ append JSON line
~/llm-stack/token_usage.jsonl       ← fichier partagé avec le projet free_api_llm
        ↑ GET /usage
~/Desktop/perso/Tools/free_api_llm/dashboard_server.py  (port 5555)
```

**Vars d'env requises :**
```
GROQ_API_KEY=...
FREETHEAI_API_KEY=sta_...          # /checkin Discord chaque jour UTC
GEMINI_API_KEY=...                 # Google AI Studio — gemini-3.1-flash-lite, 500 RPD, 250K TPM — OUT OF CREDITS 19/06/2026
ZENLLM_API_KEY=sk-zenith-...       # ZenLLM (OpenAI-compatible) — cascade "zen" batch promo
TELEGRAM_BOT_TOKEN=...             # @mcorp1290_bot
TELEGRAM_CHAT_ID=...               # notifs cascade LLM

**⚠ Règle TG (s'applique à TOUS les scripts)** : `parse_mode: HTML`. Tout `<` ou `>` dans le texte doit être échappé en `&lt;`/`&gt;` AVANT la conversion `*bold*`→`<b>`. Sinon Telegram retourne 400 silencieusement (pas d'exception, message jamais envoyé). Pattern correct :
```python
safe = msg.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
html = re.sub(r'\*([^*]+)\*', r'<b>\1</b>', safe)
```
TURSO_URL=https://stockvibe-articles-andrey-243.aws-eu-west-1.turso.io  # libSQL edge DB (champs lourds articles > 6 mois)
TURSO_AUTH_TOKEN=...               # généré depuis dashboard Turso → Create Token (rw)
AZURE_TRANSLATOR_KEY=...           # F0 free tier 2M chars/mois (traduction EN→FR)
AZURE_TRANSLATOR_REGION=...        # ex: eastus
YANDEX_API_KEY_ID=...              # Yandex Translate — ID de la clé IAM
YANDEX_SECRET_KEY=...              # Yandex Translate — clé secrète (configuré, pas encore intégré dans translation_service.py)
MARKETAUX_API_TOKEN=...            # marketaux.com — 100 req/j free · EU+ASIA prod cron + scrap
NEWSAPI_API_KEY=...                # newsapi.org — 100 req/j free · EU+ASIA prod cron + scrap P1→P4
ALPHAVANTAGE_API_KEY=...           # alphavantage.co — 25 req/j free · scrap last resort P1→P4 · ~3 ans historique
```

---

### Supabase — Tables actives

```
ow_cache           — cache OW live (ticker PK, ow7d_en, ow7d_fr, ow1d_en, ow1d_fr,
                             ow7d_sentiment, ow1d_sentiment,
                             ow1d_cached_at_en, ow1d_cached_at_fr, ow7d_cached_at_en, ow7d_cached_at_fr,
                             llm_source_en, llm_source_fr,
                             website, no_24h_news, articles_hash, exchange,
                             needs_ow_regen BOOLEAN DEFAULT FALSE) — pas de TTL, refresh sur nouveaux articles uniquement. EN served first.
                             `needs_ow_regen` : posé à True par les scrapers P1 si `added > 0`, clearé par `hourly_ow_regen :20` après `gen_ow_both` réussi.
                             Zéro articles stockés ici. `exchange` = code yfinance (NMS, NYQ, KSC…) — peuplé à la 1ère
                             visite via trigger dans `get_news_stale()`. One-shot initial : `scripts/populate_exchange.py`.
                             Lignes sans `ow1d_cached_at_en` (exchange-only) traitées comme cache absent → appel bloquant.
user_preferences           — préférences (user_id PK, currency, notify_email, notify_weekly, digest_tickers text[], lang text DEFAULT 'en')
pinned_stocks              — watchlist (user_id, ticker, company_name, pinned_at) — unique(user_id, ticker)
~~daily_digest~~           — **supprimée** (remplacée par lecture directe `ow_cache`)
bub_cache               — causes AI événements chart (ticker, date PK, bub_en, bub_fr,
                             bub_cached_at_en, bub_cached_at_fr, llm_source_en, llm_source_fr,
                             in_1w, in_1m, in_6m, in_1y, articles_used INT, articles_related INT) — permanent · articles JSONB droppé (DT9 ✅)
                             Sentinel `bub_en = 'NO_ARTICLES'` : écrit quand aucun article ne couvre le dot
                             au moment de la génération — empêche les retry infinis (IS NOT NULL = "traité").
                             Effacé automatiquement par `_mark_tried` si P2 passe sur ce (ticker, ym).
                             Constante Python : `bubble_service.BUB_NO_ARTICLES`.
articles — source de vérité articles (ticker, year_month PK, articles JSONB avec dumb_en/dumb_fr/is_related
                             par article, sources TEXT[] (ex: ["finnhub", "yahoo"]), tried_sources TEXT[]) — mois actif refresh 12h, passés permanent.
                             `sources` : liste des sources qui ont contribué des articles (union des apports). `tried_sources` : sources tentées sans résultat (ex: ["newsapi", "marketaux"] si scrape fait mais 0 ajout).
                             is_related: bool | null — null = pas encore évalué · true = pertinent · false = hors cible.
                             false : posé UNIQUEMENT par le dumb (_write_dumb_to_archive, quand dumb="SKIP") → article purgé du JSON + url_hash → articles_blocklist.
                             true : posé par le dumb (dumb réel) OU par _is_ticker_related heuristique (breaks 1M/6M/1Y, _fetch_news OW) via _persist_is_related_writeback.
                             null : passe partout (rétrocompat) — sera évalué au prochain dumb gen ou lecture break.
articles_blocklist — blacklist anti-re-scrape (ticker, url_hash TEXT 12 chars PK) — url_hash = MD5[:12] de l'URL.
                             Vérifié avant tout upsert d'article dans les crons de scraping.
breaks_weekly     — résumé AI + top 10 articles par semaine (ticker, year_month, week_num PK, bw_en, bw_fr, top_articles,
                             llm_source_en, llm_source_fr, condensed_at, status TEXT,
                             articles_used INT, articles_related INT)
                             Valeurs status : NULL = pas encore traité · "done_more_10" = généré >= 10 articles · "done_less_10" = généré < 10 articles (P2 épuisé) ·
                             "less_10_articles" = < 10 articles, P2 pas encore épuisé, skip · "scrap_budget" = toutes P2 épuisées, pas encore regénéré ·
                             "no_articles_row" = pas de row articles en DB pour ce (ticker, ym) · "no_articles" = row existe mais 0 articles pour cette semaine ·
                             "error" = exception LLM.
                             articles_used = nb articles passés au LLM · articles_related = nb avec is_related=True.
                             Exists checks filtrent sur status IN ('done_more_10','done_less_10').
                             _purge_excess_weeklies ne purge que les rows done_more_10/done_less_10.
breaks_monthly    — résumé AI par mois (ticker, year_month PK, bm_en, bm_fr,
                             llm_source_en, llm_source_fr, condensed_at, status TEXT,
                             articles_used INT, articles_related INT)
                             Mêmes valeurs status que breaks_weekly.
                             Exists checks filtrent sur status IN ('done_more_10','done_less_10'). _build_state_from_db compte uniquement ces rows.
breaks_yearly     — résumé AI par année (ticker, year PK, by_en, by_fr, llm_source_en, llm_source_fr) — jamais ai_summary vanilla
cold_start_status         — cold start tickers rares (ticker PK, phase TEXT, status TEXT, pending_fr BOOL,
                             started_at, completed_at, error TEXT, updated_at)
```

RLS activé sur toutes les tables. `service_role` a GRANT ALL explicite.
`authenticated` + `anon` ont GRANT SELECT/INSERT/UPDATE/DELETE explicite (requis en plus des RLS policies).

**Règle** : Toute nouvelle table nécessite `GRANT ... TO authenticated` en plus des RLS policies.

**Project ref** : `qxfbqzjkianxsuurgckd` · URL : `https://qxfbqzjkianxsuurgckd.supabase.co`
Management token : `backend/.env` → `SUPABASE_MANAGEMENT_TOKEN`

DDL direct (CREATE TABLE, ALTER, etc.) :
```bash
curl -s -X POST "https://api.supabase.com/v1/projects/qxfbqzjkianxsuurgckd/database/query" \
  -H "Authorization: Bearer $(grep SUPABASE_MANAGEMENT_TOKEN backend/.env | cut -d= -f2)" \
  -H "Content-Type: application/json" \
  -d '{"query": "<SQL>"}'
```

**Cold start — phases & timings :**

| Phase | Contenu | ETA estimé | Banner |
|---|---|---|---|
| `1m` | Scrape Finnhub 1M + weekly condensation | ~2 min | "Chargement du dernier mois de données..." |
| `6m` | Scrape Finnhub 6M + weeklies + monthlies | ~7 min | "Chargement des 6 derniers mois..." |
| `1y` | Scrape Finnhub 1Y + weeklies + monthlies + yearly | ~15 min | "Chargement de l'année complète..." |

- Scrape toujours en **EN** — FR traduit EOD si `pending_fr = true`
- Si user FR clique → translation immédiate (prioritaire sur le cron)

---

### Articles — Architecture par période

| Période | OW | DUMB | BUB | Breaks |
|---|---|---|---|---|
| 1D | `ow_cache` (summary_24h) | `articles[].dumb_en/fr` — filtre : affiché si dumb présent | — | — |
| 1W | `ow_cache` (summary 7J) | idem | on-demand | — |
| 1M | — | affiché en sidebar au click | on-demand | Weekly `breaks_weekly` (W1/W2/W3/W4) |
| 6M | — | — | pre-cache `bub_cache` | Monthly `breaks_monthly` |
| 1Y | — | — | pre-cache `bub_cache` | Monthly `breaks_monthly` |
| 5Y | — | — | — | Year headers `breaks_yearly` + Monthly `breaks_monthly` — pas d'articles bruts |

**Source articles & display par période :**
| Période | Source articles | Click article |
|---|---|---|
| 1D/1W | `articles` 7j (Finn+Yahoo) — filtre `dumb_en\|dumb_fr` | sidebar : title · source · summary brut · dumb |
| 1M | `articles` — sections W1-W4. Si skewed → merged + warning | sidebar |
| 6M/1Y | `articles` — sections par mois, collapsées par défaut, ExpandButton pill, pagination +10 | direct URL (no sidebar, no thumbnail) |
| 5Y | `breaks_yearly` (year dropdown) + `breaks_monthly` (mois indentés) — zéro article brut | direct URL |

**ExpandButton (6M/1Y/5Y mois) :** pill `N articles ›` avec border accent-color, fond plein au hover. Sections collapsées par défaut (0 articles affichés). Clic → 10 articles, bouton `↓ N de plus` en bas du bloc pour +10 jusqu'au max. `expandedSections: Record<string, number>` dans le state page. Reset au changement de période. Le header de section n'affiche **pas** `s.article_count` (count brut backend) — seul le CTA affiche le vrai count filtré (`sectionArticles.length`) pour éviter l'incohérence entre les deux valeurs.

**5Y — year dropdown :** `expandedYears: Record<string, boolean>`. Header année = bouton cliquable (violet `#9B77CF`), chevron `›` tourne à 90°. Mois indentés `paddingLeft: 16px` avec bordure gauche violette. Année la plus récente ouverte automatiquement au chargement. Résumé annuel affiché si disponible, au-dessus des mois.

**top_articles non tronqués :** `breakers_articles_service.py` retourne tous les articles filtrés (plus de `[:MAX_PER_MONTH]` / `[:10]` / `[:15]`). La pagination est côté frontend uniquement.

**Chaîne de condensation — inputs LLM :**
```
Weekly  : dumb_en/dumb_fr de chaque article  (fallback : headline + summary brut si pas de dumb)
Monthly : textes W1+W2+W3+W4                 (fallback < 3 weeklies : dumb + headline + summary brut)
Yearly  : textes M1→M12
```

**Condensation enrichie ✅** : `_article_text()` → dumb > summary > headline. Appliqué W prompt + fallback M.
**Display filter 1M+ ✅** : `_is_ticker_related(a, ticker, company_name)` — passe si `is_related=true` · ou si `is_related` null : dumb_en/dumb_fr présent OU ticker/company_keywords dans headline+summary (heuristique fallback). Écrit `is_related=true` dans le JSON si null et heuristique passe. Articles `is_related=false` exclus sans appel LLM.

**Sources de détection related :** 3 niveaux combinés :
1. Ticker ID (ex: `aapl`, base sans suffixe exchange)
2. `shortName`/`longName` yfinance via `bulk_fetch_company_names` + fallback `_KNOWN_NAMES` (28 tickers EU délisted)
3. `TICKER_ALIASES` (`data/ticker_aliases.py`) — ~400 tickers · noms de marque, filiales, acronymes · à brancher dans `_is_ticker_related`

**Score heuristique (à implémenter) :** `_is_ticker_related` → score 0.0–1.0 : match headline (poids fort) > match summary (poids faible) > fuzzy (poids faible) + aliases. Score=0 → `is_related=False` direct, pas de LLM. Score>0 → LLM dumb gen → décision finale. Mots-clés extraits par `_company_keywords()` : split du nom + strip des suffixes génériques (Corporation, Inc, Ltd, Group…) + mots ≤ 2 chars. Ex : "Apple Inc." → `["apple"]` · "Toyota Motor Corporation" → `["toyota", "motor"]`. `company_name` passé en query param `?company_name=` par le frontend (vient de `stock.name`).

**Semaines fixes intra-mois :** W1 = jours 1–7 · W2 = jours 8–14 · W3 = jours 15–21 · W4 = jours 22–fin du mois

**Problème connu — Finnhub date clustering :**
Finnhub indexe les articles avec sa date de crawl, pas la date de publication. Pour ~35% des mois, 80%+ des articles tombent en W4. Détection automatique côté backend : si skewed → section merged "WX→WY · pas de récap hebdomadaire disponible".

**Script batch refresh breakers :** `backend/scripts/refresh_breakers.py`
```bash
python scripts/refresh_breakers.py --region ALL --lang en                              # run complet toutes régions (US → EU → ASIA)
python scripts/refresh_breakers.py --region US  --lang en                              # run complet une région
python scripts/refresh_breakers.py --region ALL --lang en --force-regen               # régénère W+M même s'ils existent déjà
python scripts/refresh_breakers.py --region ALL --lang en --skip-scrape               # DUMB+W+M seulement, sans scrap (retente les scrap_budget)
python scripts/refresh_breakers.py --region ALL --lang en --skip-p1                   # saute RSS+Yahoo+Finnhub, garde P2+génération
python scripts/refresh_breakers.py --region ALL --lang en --skip-rss-yahoo            # saute RSS+Yahoo, exécute Finnhub+P2+génération (reprise mid-Finn)
python scripts/refresh_breakers.py --region ALL --lang en --retry-missed              # retraite les (ticker, ym) du scrap_missed_checkpoint.json
python scripts/refresh_breakers.py --region ALL --lang en --retry-skipped             # re-scrape W less_10_articles + scrap_budget → tente gen si >= 10 articles
python scripts/refresh_breakers.py --region EU  --lang en --gen-lesser                # gen done_less_10 pour W less_10_articles dont tous P2 actifs sont exhaustés (EU/ASIA: newsapi+marketaux · US: +av) — sans scrape
python scripts/refresh_breakers.py --region ASIA --lang en --gen-lesser               # idem ASIA
python scripts/refresh_breakers.py --region US  --lang en --ticker AAPL               # ticker unique (debug)
```
`--region ALL` boucle US → EU → ASIA séquentiellement avec le même set de flags.
Flags supprimés (refacto 2026-06) : `--phase ab`, `--phase m`, `--no-resume`, `--regen-bubs`, `--translate`.

**Status column — écriture par script :**
| Script | W writes | M writes |
|---|---|---|
| `refresh_breakers.py` | `done_more_10` · `less_10_articles` · `scrap_budget` · `skew` · `no_articles_row` · `no_articles` · `error` | `done_more_10` · `no_articles_row` · `no_articles` · `error` |
| `refresh_breakers.py --gen-lesser` | `done_less_10` | — |
| `breakers_articles_service.py` (cron) | `done_more_10` · `no_articles_row` · `no_articles` · `less_10_articles` · `error` | `done_more_10` · `no_articles_row` · `no_articles` · `error` |
| `cold_start_service.py` | `done_more_10` · `error` | `done_more_10` · `error` |
| `batch_fill_db.py` | non concerné (traduction uniquement) | non concerné |

**Règle `done_less_10`** : écrit UNIQUEMENT par `--gen-lesser`, jamais en run normal. Signifie "généré avec < 10 articles, P2 épuisé — définitif". P2 épuisé = newsapi+marketaux pour EU/ASIA, +av pour US (AV ne cible pas EU/ASIA). Bug corrigé 2026-07-02 : `active_paid` était hardcodé à 3 sources pour toutes régions → EU/ASIA jamais éligibles.

**Lifecycle `scrap_budget` :**
- Posé en run normal quand toutes les sources P2 ont été skippées faute de budget pour ce `(ticker, ym)`
- Reset vers `NULL` au début de chaque run normal (P2 budget rechargé → nouvelle tentative)
- En `--retry-skipped` : le reset est skippé → les rows `scrap_budget` sont ciblées directement et re-scrapées
- En `--skip-scrape` : reset vers `NULL` → tentative de gen sans re-scraper

`less_10_articles` remplace `less_3_articles` (seuil W passé à `MIN_W_SCRAP = 10`).

**Flow (tout séquentiel) :**
- **Phase 1** : RSS 12m → Yahoo mois courant → Finnhub 12m US — sources gratuites
- **Gate fraicheur** : `_build_targets` (1 req/year_month) → skip P2 si ≥25 arts mois ET ≥10 arts semaine courante
- **Phase 2** : NewsAPI 1m → Marketaux 3m → AV 12m US-only — payant, budget-limité
  - `scrap_budget` : tag `(ticker, ym)` uniquement si les 3 sources payantes ont toutes été skippées faute de budget
  - **tried_sources** : colonne `tried_sources text[]` sur `articles` — tracker quels APIs ont déjà hit chaque (ticker, ym) sans résultat. Couvre **P1 Finnhub** + **P2** (newsapi · marketaux · av). Skip si déjà taggé. Tag posé **uniquement sur succès ou résultat vide** — un 429 ne pose PAS le tag (ticker retenté au prochain run). Abort automatique après 3 × 429 consécutifs (2 pour AV). Reset naturel : nouvelle row par mois. **Ordre canonique** : `finnhub → newsapi → marketaux → av` — garanti par `_P2_ORDER` dans `_mark_tried`. État DB normalisé : `["finnhub"]` · `["newsapi", "marketaux"]` · `["newsapi", "marketaux", "av"]`. Granularité : par `(ticker, year_month)`. **Invalidation bub_cache** : `_mark_tried` supprime automatiquement les lignes `bub_cache` sentinel (`bub_en = 'NO_ARTICLES'`) du même (ticker, ym) → les dots seront retentés avec les nouveaux articles.
  - **Finnhub throttle** : `time.sleep(1.0)` entre chaque call — ~60 req/min (limite free tier). Sur second passage (`--skip-rss-yahoo`), les paires déjà taggées `"finnhub"` dans `tried_sources` sont skippées.
  - **AV : US uniquement — décision définitive** — `_detect_region(t) == "US"` filtré avant le loop. AV est fondamentalement une source US : leur `LISTING_STATUS` ne contient que NYSE/NASDAQ/BATS/AMEX (14k symboles, zéro EU/ASIA). Les tickers EU accessibles via `SYMBOL_SEARCH` sont des cross-listings Frankfurt/Londres avec des suffixes et racines différents (`.DE`→`.DEX`, `.L`→`.LON`, ticker root parfois changé) — couverture news EU via AV = pauvre ou redondante avec NewsAPI/Marketaux. Ne pas étendre AV à EU/ASIA. **`active_paid` dans `--retry-skipped`** : region-aware — AV exclu pour EU/ASIA (sinon les pairs `[newsapi, marketaux]` ne seraient jamais considérées P2-exhaustées). **1 call/ticker** (fenêtre 12m, limit=1000) — résultats splittés par `year_month` en mémoire puis upsertés mois par mois. Budget 25 req/j → 25 tickers/j.
- **Post-scrap** : DUMB → W (5 semaines glissantes) → M par ticker — **25 tickers en parallèle** via `ThreadPoolExecutor(25)` · semaphore Hapuppy(25) régule les LLM · compteurs protégés par lock
- **Flow M** : monthlies uniquement. Cascade : `promo` — lève `RuntimeError` si Hapuppy down. 1 worker.
- Deux `ThreadPoolExecutor` séparés (AB: max_workers=2, M: max_workers=1), unifiés via `as_completed`
- `_freetheai_monthly_stop = threading.Event()` — quand Flow M capte `FreeTheAIExhaustedError` : stop propre + notif TG
- Flow AB continue indépendamment même si FreeTheAI est épuisé (Hapuppy/Groq fallback)
- `--phase ab` : lance uniquement Flow AB — utile pour remplir les W sans consommer le quota FreeTheAI
- `--phase m` : lance uniquement Flow M — utile en soirée quand FreeTheAI est dispo
- `--skip-scrape` : saute P1 + P2 (tout le scrape) mais **exécute quand même la génération W/M** — utile si les articles sont déjà en DB
- `--skip-p1` : saute RSS + Yahoo + Finnhub (Phase 1 gratuite) mais garde Phase 2 payante + génération W/M — utile pour aller directement aux APIs payantes
- `--retry-missed` : lit `scrap_missed_checkpoint.json`, reprocess uniquement les paires ratées via APIs payantes (RSS skippé). Met à jour le checkpoint avec ce qui reste, ou le supprime si tout est couvert. Relançable plusieurs jours de suite.
- `--retry-skipped` : cible les W `less_10_articles` OU `scrap_budget` dans la fenêtre glissante, OU M absent pour le mois courant. **Ne reset PAS `scrap_budget`** au démarrage (contrairement au run normal) — les rows gardent leur status pour être trouvées. Après re-scrape : si >= 10 articles → gen → `done_more_10` ; si encore < 10 → `less_10_articles` (pas de gen). Ne génère JAMAIS `done_less_10` (c'est réservé à `--gen-lesser`). Construit des `force_pairs` (ticker, ym) qui bypass la garde fraîcheur 3j → P1 re-fetche ces mois même si récemment scrapés. **Granularité fetch = mois entier** (toutes les APIs travaillent par mois) : pour un W3 manquant, c'est tout le mois qui est re-scrapé, puis la `qty_gate` recompte au niveau semaine. Pour W-bloqués : force_pairs = uniquement les (ticker, ym) non encore exhaustés P2 (pas toute la fenêtre). Pour M-absent : force_pairs = mois courant uniquement. Complémentaire du run normal : run normal = "jamais vus" (aucune row en DB) | retry-skipped = "échecs connus" (less_10/scrap_budget ou M NULL). **Filtre P2-exhaust** : les (ticker, ym) `less_10_articles` dont `articles.tried_sources` couvre déjà tous les scrapers P2 actifs sont exclus du lot — rien de plus à scraper, utiliser `--gen-lesser` à la place.
- `--gen-lesser` : sans scrape. Cible les W `less_10_articles` dont `articles.tried_sources` contient les sources P2 actives pour la région (EU/ASIA: newsapi+marketaux · US: newsapi+marketaux+av) — P2 épuisé, rien de plus à espérer. Génère quand même le break avec les articles disponibles → `done_less_10`. Ordre logique : d'abord `--retry-skipped`, puis `--gen-lesser` sur ce qui reste bloqué.

**Retry scrap — `scrap_missed_checkpoint.json`** :
- Écrit à la fin de P2 pour les `(ticker, ym)` qui ont eu **zéro articles via RSS** ET dont **toutes les sources payantes ont été skippées faute de budget** (`scrap_budget`)
- Contient `{ "date": "YYYY-MM-DD", "missed": [[ticker, ym], ...] }`
- `--retry-missed` reprocess ces paires via NewsAPI (1m) → Marketaux (3m) → AV (12m US), puis appelle `gen_dumb + gen_weekly + gen_monthly` par ticker/ym si articles ajoutés
- Le checkpoint est mis à jour après chaque run : paires couvertes retirées, supprimé si vide
- Indépendant du run normal — lancer `--retry-missed` seul ne scrap pas les autres tickers

---

### Articles — Doublons & Multi-source

Finnhub indexe le même article depuis plusieurs outlets. Contenu identique à 100%, seule la source diffère.

**Architecture :**
- Finnhub raw → `_dedup_by_headline()` → stockage propre en DB avec `extra_sources: [{name, url}]`
- `_get_or_fetch_month()` appelle `_dedup_by_headline()` avant chaque upsert

**Affichage multi-source :**
- Badges colorés par source (Yahoo=violet, Bloomberg=noir, Reuters=orange...) dans `NewsCard` et `CompactArticleRow`
- `ArticleDrawer` : un bouton "Read" par source disponible
- **Scope** : 1M/6M/1Y. 1D/1W utilise `articles` (Finn+Yahoo) — multi-source possible mais pas de badges supplémentaires dans ce mode.


---

### Rotation des données

```
articles (raw bodies)
  → source de vérité unique — article_archive supprimé ✅ (code + DB)
  → JAMAIS purgé automatiquement · 5 616 rows · 216 341 articles · 289 MB TOAST (juin 2026)
  → sources : finnhub 97k | yahoo 64k | mixed 42k | google_rss 11k
  → PK = (ticker, year_month) — une seule source par cellule, pas de doublon cross-source possible
  → quasi zéro doublons URL intra-array (1 pair / 50k articles échantillonnés)

  **Lifecycle articles — migration progressive Supabase → Turso :**

  Clé de join : `id = sha1(url)[:16]` — stocké dans le JSONB Supabase ET dans Turso.
  Turso schema : `article_content(id PK, ticker, year_month, url, summary, dumb_en, dumb_fr, headline, source, is_related)`
  Index Turso : `idx_art_ym ON (ticker, year_month)`

  0 → 1 mois (Supabase full) :
    → champs complets : id · headline · url · source · datetime · summary · dumb_en · dumb_fr · is_related
    → display 1D/1W/1M avec drawer · BUB gen · Breaks gen : tout disponible

  Phase 1 (>1M) : strip summary + dumb_en + dumb_fr de Supabase → Turso
    → Supabase garde : id · headline · url · source · datetime · is_related
    → BUB sur dot ancien : gate Turso déclenché si summary absent → fetch_heavy → merge sur id
    → Gain estimé : ~15 MB / 6 mois

  Phase 2 (>7M) : strip url de Supabase → Turso
    → Supabase garde : id · headline · source · datetime · is_related
    → BUB : gate déclenché si url absent → fetch Turso complet (url + summary)
    → Gain estimé : ~30 MB supplémentaires

  Phase 3 (>13M) : strip headline + source de Supabase → Turso
    → Supabase garde uniquement : id · datetime · is_related (~27 chars/article)
    → Aucune vue n'affiche des articles > 12 mois → Turso consulté uniquement si regen forcée
    → Gain total cumulé : ~65 MB

  > 13 mois (futur) : purge Turso optionnelle
    → `delete_heavy(ticker, ym)` — à décider selon capacité Turso
    → bub_cache a déjà purgé les dots > 1 an → aucun BUB possible

  Scripts :
    → `python scripts/backfill_article_ids.py` : one-shot, ajoute id à tous les articles existants
    → `python scripts/migrate_articles_cold.py --phase 1 [--init-db] [--dry-run]`
    → `python scripts/migrate_articles_cold.py --phase 2`
    → `python scripts/migrate_articles_cold.py --phase 3`
    → Lire/écrire Turso : `services/articles_store.py` (write_heavy · fetch_heavy · merge_heavy_into_articles · delete_heavy)
    → bubble_service : gate `os.environ.get("TURSO_URL")` + déclenchement si summary OU url absent
    → Inactif jusqu'à `TURSO_URL` dans `.env` prod

bub_cache
  → 9 574 rows · 12 MB (après DROP articles + VACUUM FULL, -103 MB libérés le 2026-06-19)
  → colonne `articles` JSONB droppée — BUBs fetched fresh depuis `articles` à la demande (DT9 ✅)
  → purge auto lun 3h : rows > 1 an supprimées

breaks_weekly
  → 1 703 rows · 16 620 top_articles re-copiés depuis `articles` · 12 MB
  → top_articles stockés pour générer les Breaks, inutiles une fois bw_fr/bw_en rempli
  → max 5 weeklies stockés par ticker (purge auto à chaque nouveau weekly)
  → supprimé le 1er du mois suivant (après monthly calendaire généré)
  → TODO cleanup : vider breaks_weekly.top_articles pour les rows où bw_fr/bw_en IS NOT NULL (gain ~10 MB)

breaks_monthly (calendaire : S1+S2+S3+S4 dans l'ordre)
  → gardé 12 mois — source pour vues 6M / 1Y
  → supprimé le 1er janvier après génération du yearly

breaks_yearly
  → permanent
```

**3 types de monthly :**
| Type | Source | Usage | TTL |
|---|---|---|---|
| On-demand (rolling) | 4 derniers weeklies | UI "1M" — premium CTA | Pas de cache |
| Calendaire | S1+S2+S3+S4 du mois écoulé | Source pour 6M / 1Y | 12 mois glissants |
| Yearly input | 12 monthlies calendaires | Yearly summary | Purgé après yearly |

**Chaîne de condensation :**
```
Lundi 3h    : weekly summary → purge si > 5 weeklies
1er du mois : monthly calendaire (S1+S2+S3+S4) → purge W du mois passé (conditionnel)
1er janvier : yearly (12 monthly calendaires) → purge ces 12 monthlies (conditionnel)
```

**Conditions de purge (fixes à implémenter) :**
- W → purgés seulement si : (1) contenu M non-null en DB ET (2) W4 du mois courant existe (`status IS NULL`)
  → garantit 4 semaines visibles en vue 1M avant suppression du mois passé
- M → purgés seulement si : contenu Y non-null en DB après upsert yearly
  → évite perte de données si yearly upsert silencieux avec contenu vide

**Source par ticker :**
- Finnhub retourne des articles → `sources: ["finnhub"]` → bootstrap immédiat 12 mois
- Finnhub retourne 0 articles → `sources: ["yahoo"]` → accumulation daily via cron
- `sources` valeurs possibles par élément : `"finnhub"` · `"yahoo"` · `"marketaux"` · `"newsapi"` · `"google_rss"` · `"alphavantage"` — tableau cumulatif (une row multi-sources aura ex: `["finnhub", "yahoo", "google_rss"]`)
- `tried_sources` : sources tentées sans résultat pour ce (ticker, year_month) — pas de tag sur 429 (ticker retenté au prochain run)

**Routing région (`_detect_region(ticker)`) :**
- US : pas de `.` dans le ticker (AAPL, AMZN…)
- EU : suffixes `.PA .AS .L .MI .DE .MC .SW .CO .OL .ST` etc.
- ASIA : suffixes `.T .HK .KS .SS .AX .NS .BO .TW` etc.
- Fallback : US si inconnu · source : `ow_cache.exchange` peut affiner (NMS/NYQ = US, PAR = EU, KSC = ASIA…)

**File de scrap multi-sources (ordre d'exécution) :**

Seuils qualité : M breaker = 25 articles minimum · W breaker = 10 articles minimum.

1. **Google RSS** (`after:/before:` par mois) → gratuit · sans quota · ~2 ans d'historique · ~46 articles/mois
   Scope : **tous tickers** (US + EU + ASIA) pour les mois SKWD et vides

2. **Si M < 25 articles après RSS** → NewsAPI + Marketaux (100 req/j chacun)
   Scope : **EU+ASIA** (tous mois manquants) + **US SKWD ou vides uniquement**
   NewsAPI : mois courant prio, 1 mois max · Marketaux : SKWD prio → M vides, 3 mois max

3. **Si M encore < 25 articles** → Alpha Vantage (25 req/j · ~2 ans d'historique)
   Scope : **EU+ASIA** (tous mois manquants) + **US SKWD ou vides uniquement**
   Priorité interne : P1 SKWD EU+ASIA · P2 M vides EU+ASIA · P3 SKWD US · P4 M vides US

Même logique pour les W breakers avec seuil à 10 articles.

**Rôles par source — scrap vs prod :**
| Source | Scrap | Prod (cible) |
|---|---|---|
| Yahoo | déjà en place | `*/3h :00` · pinnés **≥ 1 user** · gen_dumb inline · pose `needs_ow_regen=True` |
| Finnhub | déjà en place | `*/3h :05` · pinnés **≥ 1 user** · TTL interne 12h (`ACTIVE_MONTH_TTL_HOURS`) → pas d'appel API si cache frais · gen_dumb inline · pose `needs_ow_regen=True` |
| Google RSS | **top 1 scrap** : after/before par mois · tous tickers · ~46 art/mois · gratuit/illimité | `*/3h :10` · pinnés **≥ 1 user** · gen_dumb inline · pose `needs_ow_regen=True` |
| Marketaux | SKWD prio → M vides EU+ASIA | **T1** `*/6h :55` EU+ASIA · tickers 0-article · budget cap 20 req/run · `tried_sources` check |
| NewsAPI | P1→P4 EU+ASIA | **T1** `*/6h :55` EU+ASIA · même logique que Marketaux · budget partagé avec Marketaux |
| Alpha Vantage | last resort · 25 req/j | **T1** `*/6h :55` US only · tickers 0-article · compteur journalier AV séparé (max 25/j) |
| Marketaux + NewsAPI + AV | idem | **T2** `dim 22h00` · tous pinnés `article_count < 10` cette semaine · skip si T3 day |
| Marketaux + NewsAPI + AV | idem | **T3** `dernier j du mois 23h00` · tous pinnés `article_count < 25` ce mois · skip T2 ce jour |

**gen_ow_both découplé des scrapers (Option A — actif depuis 2026-07-02) :** les scrapers P1 (Yahoo, Finnhub, Google RSS) appellent `gen_dumb()` inline si `added > 0`, puis posent `needs_ow_regen=True` dans `ow_cache`. L'`hourly_ow_regen :20` lit tous les tickers avec ce flag, lance `gen_ow_both(force_24h=True)` une seule fois par ticker, puis clear le flag. Évite N gen_ow_both redondants par fenêtre 3h. Le flag est clear APRÈS `gen_ow_both` (pas avant) pour retry automatique si le LLM échoue.

**Cron Yahoo `*/3h :00`** : fetch `get_news(count=100)` → EU/ASIA : + `yf.Search(company_name)` (dedup URL) → `_dedup_by_headline` merge → mise à jour `sources[]` → si `added > 0` : `gen_dumb(cascade="dumb_prod")` + `ow_cache.needs_ow_regen=True`

**Cron Finnhub `*/3h :05`** : `_get_or_fetch_month()` → TTL 12h (skip API si cache frais) → merge existant → mise à jour `sources[]` → si `added > 0` : `gen_dumb(cascade="dumb_prod")` + `ow_cache.needs_ow_regen=True`

**Cron Google RSS `*/3h :10`** (`google_rss_articles_scrap()`) : tous pinnés ≥1 user · `_build_rss_query(ticker, company_name)` · merge → dedup headline → upsert `sources[]` · si `added > 0` : `gen_dumb` + `needs_ow_regen=True`

**T1 `*/6h :55`** (`t1_p2_scrap()`) : EU+ASIA → Marketaux + NewsAPI · US → AV · filtre : `article_count = 0` pour `year_month` courant · check `tried_sources` avant fetch · budget cap par run (20 req Marketaux, 20 req NewsAPI, AV compteur journalier 25/j) · si `added > 0` : gen_dumb + needs_ow_regen · si `added = 0` : ajoute source dans `tried_sources`

**T2 `dim 22h00`** (`t2_w_threshold_scrap()`) : tous pinnés · filtre `article_count < 10` pour la semaine courante · skip si jour = T3 day (dernier j du mois) · même budget cap · gen_dumb + needs_ow_regen si ajout

**T3 `dernier j du mois 23h00`** (`t3_m_threshold_scrap()`) : tous pinnés · filtre `article_count < 25` pour le mois courant · T2 skippé ce jour · gen_dumb + needs_ow_regen si ajout

**`is_fresh(ticker, year_month)` — logique partagée** : `len(articles) >= 25 AND (now - cached_at).days < 3` · dérivé de `articles.cached_at` + count JSONB · zéro DDL · utilisé par : scrap scripts (skip) + M gen (choix cascade)

**Ticker → company name — source unique : `yfinance_service.py`**

- `get_company_name(ticker) -> str` : 1. `yf.Search(ticker).quotes[0]` (longname/shortname) · 2. `t.info` longName/shortName · 3. `_KNOWN_NAMES` fallback statique (28 tickers EU confirmés par recoupement multi-sources)
- `bulk_fetch_company_names(tickers) -> dict[str, str]` : ThreadPoolExecutor(8) sur `get_company_name` · filtre `"." in ticker` (EU/ASIA uniquement)
- `_KNOWN_NAMES` couvre les tickers défunts/fusionnés/délisted que Yahoo ne résout plus (Wirecard, Credit Suisse, FCA, Peugeot, Luxottica, etc.) — 3 tickers volontairement exclus faute de source fiable : BCMB.DE, GFJ.DE, AIRP.IR
- `breakers_articles_service.py` n'expose plus `_bulk_fetch_company_names` — tous les appelants (refresh_breakers, scrap_google_rss, scrap_newsapi, scrap_alphavantage, scrap_marketaux) importent depuis `yfinance_service`
- Les tickers sans nom trouvé sont écrits dans `scripts/missing_names_{REGION}.txt` (overwrite à chaque run) — fichier d'audit pour identifier les tickers que Yahoo ne connaît pas

**`_build_rss_query(ticker, company_name)`** dans `breakers_articles_service.py` :
- US : `_company_keywords(company_name)[:2]` + "stock"
- EU/ASIA : `company_name.strip().split()[:3]` brut (pas de filtre keyword) + "stock" — préserve "Holding", "Motors" etc. · fallback base ticker sans suffixe ("7203" depuis "7203.T")

**Fonctions fetch dans `breakers_articles_service.py`** (source unique, importées par scrap scripts + refresh_breakers) :
`_fetch_rss_month` · `_fetch_newsapi_month` · `_fetch_marketaux_month` · `_fetch_av_month` · `_normalize_av` · `scrap_ext_sources` · `_is_month_fresh` · `_upsert_articles_merge` · `_normalize_google_rss` · `_normalize_newsapi` · `_normalize_marketaux`

`_upsert_articles_merge` retourne `(added: int, effective_source: str)` — `effective_source` vaut `""` si rien ajouté, `source_tag` si nouveau ticker/mois, `"mixed"` si merge multi-source. Tous les callers unpackent le tuple (`added, _ = ...` ou `added, src = ...`).

`scrap_gen_weekly(ticker, year_month, supa, caller, cascade="weekly_scrap")` · `scrap_gen_monthly(ticker, year_month, supa, caller, cascade=None)` — le param `cascade` permet aux appelants de forcer une cascade différente (ex: `promo` depuis refresh_breakers). Défaut : `weekly_scrap` (Gemini→Hapuppy) pour W · auto-détecté `monthly_scrap`/`monthly_scrap_ext` pour M.

**Cascades LLM par script (DUMB · W · M) :**
| Script | DUMB | Weekly | Monthly |
|---|---|---|---|
| `refresh_breakers.py` (toutes phases) | `promo` | `promo` | `promo` |
| `scrap_google_rss.py` | `dumb_scrap` Groq→Hapuppy | `weekly_scrap` Gemini→Hapuppy | `monthly_scrap`/`ext` FreeTheAI→Hapuppy |
| `scrap_newsapi.py` | `dumb_scrap` | `weekly_scrap` Gemini | `monthly_scrap`/`ext` FreeTheAI |
| `scrap_marketaux.py` | `dumb_scrap` | `weekly_scrap` Gemini | `monthly_scrap`/`ext` FreeTheAI |
| `scrap_alphavantage.py` | `dumb_scrap` | `weekly_scrap` Gemini | `monthly_scrap`/`ext` FreeTheAI |

**Crons prod — implémentés (2026-07-02) :** Yahoo `*/3h :00` `min_users=1` · Finnhub `*/3h :05` `min_users=1` · Google RSS `*/3h :10` · OW regen découplé → Option A `needs_ow_regen=True` posé par scrapers + `hourly_ow_regen :20` · T1/T2/T3 P2 tiered (Marketaux+NewsAPI EU/ASIA · AV US seul)

---

### News — Architecture

- **Source** : `yfinance.Ticker(ticker).get_news(count=100)`. Filtre articles dans `_fetch_news()` : headline non vide uniquement (filtre `summary >= 80 chars` supprimé en 2026-07, il causait une dissonance daily/weekly).
- **Filtre pertinence** : le LLM marque les articles hors-sujet "SKIP" → valeur `"SKIP"` écrite dans `articles[].dumb_en` → filtrés dans `_fetch_news()` (jamais affichés ni retraités). Si **tous** sont SKIP → fallback articles bruts sans abrutis.
- **`_is_ticker_related` — suffixes exchange** : pour les tickers non-US avec suffixe (7203.T, 005930.KS, 1299.HK…), la base numérique est ajoutée aux keywords (`"7203"` depuis `"7203.T"`) — les headlines utilisent le format `TSE:7203` pas `7203.T`. Sans ce fix, tous les articles de ces tickers sont filtrés malgré leur pertinence. `services/breakers_articles_service.py`.
- **Pipeline DUMB + OW — `overview_service.py` :**
  - **DUMB découplé de l'OW** : `gen_dumb(ticker)` — fonction standalone appelée par les crons dès qu'un nouvel article est détecté. Génère `dumb_en` pour tous les articles sans DUMB dans `fresh_articles[:30]`. Indépendant de la regen OW.
  - **Call 1 — OW 24H + OW 7J** : déclenché par `force_24h=True` (crons après `gen_dumb`) ou cache absent. LLM2 : skip si hash IDs inchangé → si articles sans dumb → BRUTALIZE → OW 7J depuis `ai_result["summary"]`. Si tous ont déjà leur dumb → `_ow7d_from_abrutis()` directement (pas de re-brutalisation). OW 24H via `_ow24h_from_abrutis()`. Si `articles_24h` vide → fallback "No news 24h" + `_bench_context_line()`.
  - **Call 2 — OW 7J (BATCH_MODE=1 uniquement)** : lit abrutis en DB → `_ow7d_from_abrutis()`. Inactif en mode site.
  - **Pas de TTL** : `_needs_24h_refresh` et `_needs_7d_refresh` retournent `True` uniquement si le summary est absent — jamais sur critère temporel. `_is_stale_6h()` = filet sécurité visite page.
- **Stale-while-revalidate** : `GET /api/news/{ticker}` retourne le cache immédiatement (même stale) + flag `is_refreshing: true` → background task `gen_ow_both(force_24h=True)`. Premier load (aucun cache ou `cached_at` NULL) = appel bloquant. Re-fetch frontend auto après 20s si `is_refreshing: true`. Implémenté via `get_or_refresh_ow_both()` + FastAPI `BackgroundTasks` dans `routers/news.py`. Routes `news` et `weekly` déclarées `def` (pas `async def`) : FastAPI les exécute dans le threadpool (40 threads) — N requêtes parallèles depuis /my-news au lieu d'être sérialisées dans l'event loop asyncio.
- **Filet de sécurité 6h** : `_is_stale_6h()` dans `get_or_refresh_ow_both()` — si `cached_at > 6h`, pose `is_refreshing: true` même si les summaries sont présents. La visite page déclenche alors un refresh complet (articles + DUMB + OW 1D + 7D) en arrière-plan. Les crons Yahoo */3h / Finnhub */6h font normalement le travail avant ; ce TTL n'est qu'un fallback si les crons ont raté (backend off, etc.).
- **`force_24h=True`** : param de `gen_ow_both()` qui bypasse `_needs_24h_refresh` (qui skip si la colonne est non-vide). Utilisé par les crons après détection de nouveaux articles pour régénérer l'OW avec le contenu frais — c'est le comportement normal des crons, pas une exception. Sans ce param, `gen_ow_both()` ne régénère que si la colonne est vide (premier chargement).
- **`_get_stock_context()` dédupliqué** : calculé une seule fois dans `gen_ow_both()` avant les deux calls, passé en paramètre — évite 4 appels yfinance redondants.
- **Cron trigger OW (Option A — actif) :** scrapers P1 posent `needs_ow_regen=True` dans `ow_cache` après ajout d'articles (plus d'appel `gen_ow_both` inline). `hourly_ow_regen :20` lit tous les tickers avec ce flag → `gen_ow_both(force_24h=True)` une fois par ticker → clear flag. Flag clearé APRÈS gen, pas avant (retry auto si LLM fail). Déduplique les appels OW quand plusieurs scrapers touchent le même ticker dans la même fenêtre 3h.
- **Cutoff articles 24H** : fenêtre glissante **26h** (`now - 26h`) partout — backend `_filter_24h()` (génération OW) + frontend `getDisplayedArticles()`. Couvre les sources de toutes timezones (max UTC-8 = 8h de décalage) sans complexifier le cache. OW et liste affichée sont cohérents : même set d'articles.
- **Filtre DUMB en vue 24H** : `getDisplayedArticles()` en mode `1d` n'affiche que les articles avec `dumb_en || dumb_fr`. Les articles LLM-SKIPped (hors sujet) sont exclus. Règle EN-first : `dumb_en` toujours généré en premier — ne jamais conditionner sur `dumb_fr` seul.
- **AI Overview affiché selon la période** : 24H → `summary_24h` (+ badge `AUCUNE NEWS 24H` si `no_24h_news` · badge `↻ mise à jour...` si `is_refreshing`) · 1W → `summary` (7J) · 1M+ → pas d'overview Yahoo.
- **Hourly refresh** (cron :15) : pinned + tickers visités 6h avec `no_24h_news=True` → **`_accumulate_yahoo` d'abord** (fetch Yahoo fresh, couvre tickers < 3 pins non couverts par `run_yahoo_accumulation`) → `_fetch_news` DB → si ≥1 article 26h trouvé → `gen_ow_both(force_24h=True)`
- **Refresh `no_24h_news` stale** : `get_or_refresh_ow_both()` vérifie, après lecture DB, si `no_24h_news=True` en cache mais des articles 24h existent fraîchement en DB. Si oui, pose `needs_refresh=True` → background refresh sans attendre le cron. Couvre les cas où le cache est devenu stale après un fix de filtre.
- **`exchange` dans `ow_cache`** : code yfinance (NMS, NYQ, KSC…). One-shot initial : `scripts/populate_exchange.py` (source `articles`). Prévu pour gate market-hours dans `run_hourly_news_refresh()`. Le peuplement à la 1ère visite via `get_or_refresh_ow_both()` a été supprimé : c'était un appel `yf.fast_info.exchange` bloquant sur le hot-path de chaque requête.
- **Progressive loading /my-news** : après fetch pins (`pinsLoaded=true`), N `SkeletonCard` affichés immédiatement via compteurs `dailyPending` / `weeklyPending` (décrémentés à chaque réponse ticker). Plus de blocage global "Chargement...". `fetchWithTimeout(url, ms)` via AbortController : timeout 45s daily, 30s weekly — évite les skeletons bloqués indéfiniment.
- **Logique badge / texte no-news (DigestCard /my-news)** : `noNews = !hasArticles` (badge rouge NO NEWS, indépendant du cache DB). `noNewsText = (digest.no_24h_news ?? false) || !hasArticles` (texte affiché : résumé 7J si true, résumé 24H si false). Les deux logiques sont indépendantes : `no_24h_news` peut être stale en DB sans affecter le badge.

---

### Events (dots chart)

Quand l'user clique un dot → `EventDrawer` → `GET /api/events/{ticker}?date=&change_pct=&lang=`

**BUB hiding dynamique ✅** : avant d'afficher les dots, le frontend charge `GET /api/bub-dates/{ticker}?lang=` qui retourne les dates ayant une cause disponible en DB. Seuls les dots dont la date est dans ce Set sont affichés. Si `lang=fr` : inclut les dates où `bub_fr` OU `bub_en` est non-null (fallback EN visible). Si `lang=en` : dates avec `bub_en` non-null uniquement. Pendant le chargement, aucun dot n'est affiché (`bubDates === null`).

**Fallback FR→EN** : si `lang=fr` et `bub_fr` null, `get_or_gen_bub` retourne `bub_en` avec `is_fallback=True`. `EventDrawer` affiche la note "Version anglaise — traduction française en cours…" et fire-and-forget `POST /api/translate-bub/{ticker}/{date}` qui enqueue la traduction Azure/Groq/Hapuppy en background.

**Labels EventDrawer i18n** : `LABELS.en` / `LABELS.fr` dans `EventDrawer.tsx` — loading text, "no news", disclaimer, fallback note. `lang` prop transmis depuis `stock/[ticker]/page.tsx` via `useLangPref()`.

**Contexte envoyé au LLM :**
```
Ticker : AAPL | Date : 2026-04-15 | Mouvement : ▼ -4.2%

Prix T-5 → T+3 (daily) + benchmark (S&P 500) même période
Headlines Finnhub T-3 → T+3 : [12 max, fallback T-6 → T+6 si 0 articles]
```

**Benchmark par exchange :**
| Exchange | Index | Ticker yfinance |
|---|---|---|
| NMS / NYQ (US) | S&P 500 | `^GSPC` |
| PAR (Paris) | CAC 40 | `^FCHI` |
| GER / XETRA | DAX | `^GDAXI` |
| LSE (Londres) | FTSE 100 | `^FTSE` |
| Défaut / autre | Euro Stoxx 50 | `^STOXX50E` |

**Cache** : `bub_cache` table — permanent.

---

### Chart — Périodes & intervalles yfinance

Labels X : période `1d` → `HH:MM` (bougies 5-min). Autres périodes → `YYYY-MM-DD`.

| Bouton | period | interval | Dots events |
|---|---|---|---|
| 24H | 1d | 5m | aucun |
| 1W | 5d | 1h | top 5 peaks/valleys |
| 1M | 1mo | 1d | top 8 peaks/valleys |
| 6M | 6mo | 1d | top 10 peaks/valleys |
| 1Y | 1y | 1d | top 10 peaks/valleys |
| ALL | max | 1mo | aucun (Finnhub = 1 an max) |

---

### Resend — Email

- Sandbox : `RESEND_FROM_EMAIL=onboarding@resend.dev` → emails envoyés uniquement vers l'adresse du compte Resend
- Production : vérifier un domaine dans Resend dashboard → changer `RESEND_FROM_EMAIL=digest@tondomaine.com`
- **2 jobs APScheduler** :
  - `daily_digest_email` 19h15 — lit `ow1d_en/ow1d_fr` selon `user_preferences.lang` · Azure pre-flight pour manquants FR · snapshot mémoire · envoi via `supa.auth.admin.get_user_by_id()` (pas management API)
  - `weekly_digest_email` sam 7h — lit `ow7d_en/ow7d_fr` · 5 articles 7j · même flow
- **Langue** : `user_preferences.lang` ('en' | 'fr', DEFAULT 'en') — toggle dans Profile page · fallback EN si FR vide

---

### Tickers — Coverage

| Région | Count | Source articles | Historique |
|---|---|---|---|
| US (S&P500 + NASDAQ) | 252 | Finnhub 12 mois + Yahoo | 12 mois complets |
| EU (Paris/Frankfurt/LSE/Amsterdam/Zurich...) | 203 | Yahoo + Marketaux + NewsAPI (prod) · Marketaux scrap P1→P4 + NewsAPI scrap P1→P4 · Google RSS + AV last resort | Accumulation daily + backfill 3 mois |
| Asie (Japon/HK/Corée/Inde/Taiwan/Australie...) | 230 | Yahoo + Marketaux + NewsAPI (prod) · Marketaux scrap P1→P4 + NewsAPI scrap P1→P4 · Google RSS + AV last resort | Accumulation daily + backfill 3 mois |
| **Total** | **685** | | |

Fichier de référence : `backend/data/tickers.py`

**Tickers Yahoo Finance non-évidents (EU) :**
| Ticker affiché | Ticker Yahoo réel | Entreprise |
|---|---|---|
| CS.PA | CS.PA | AXA SA (pas "AXA.PA" — Yahoo utilise l'ancien code Euronext) |
| RO.SW | RO.SW | Roche Holding (pas "ROG.SW" — code Genussscheine) |
| GBLB.BR | GBLB.BR | GBL Groupe Bruxelles Lambert (pas "GBL.BR") |

**Règle yfinance EU** : `rows=0` sur `history(period='5d')` ne signifie pas forcément "délisted". Tester le ticker alternatif (code B, code ADR, suffix différent) avant de conclure. `yf.Ticker(t).info["longName"]` = `"?"` si Yahoo ne reconnaît pas le ticker.

---

### Maintenance tickers — Process

Procédure complète quand un ticker EU/ASIA reste en `todo` sur N cycles BUB consécutifs avec 0 dots.

**Étape 1 — Identifier les tickers problématiques**
```bash
# Lancer BUB sur la région et noter les "todo" avec 0 dots
python scripts/zen_watcher.py --region EU
# Les tickers 0 dots = yfinance retourne 0 rows → aucun pic/creux détecté
```

**Étape 2 — Tester yfinance (batch)**
```python
import yfinance as yf, time
tickers_todo = ['ROG.SW', 'AXA.PA', ...]
for t in tickers_todo:
    h = yf.download(t, period='5d', interval='1d', progress=False, auto_adjust=True)
    print(f'{t}: rows={len(h)}')
    time.sleep(0.3)
```
`rows=0` + message `"possibly delisted"` = Yahoo ne reconnaît pas le ticker.

**Étape 3 — Classifier**

| Diagnostic | Indice | Action |
|---|---|---|
| Délisted définitif | Entreprise dissoute/faillie (Wirecard, Credit Suisse) | Supprimer tickers.py + aliases |
| Fusionné/renommé | Acquisition (FCA→STLAM, Reckitt→Reckitt) | Vérifier si le nouveau ticker est déjà dans la liste |
| Mauvais symbole Yahoo | `yf.Ticker(t).info["longName"]` retourne le bon nom avec un autre code | Remplacer par le bon code |
| Duplicata | Même société, deux codes dans la liste (SG.PA et GLE.PA pour SocGen) | Supprimer le code mort |
| Index (pas une action) | AEX.AS = indice AEX Amsterdam | Supprimer |
| Marché wrong | BHVN.IR = Biohaven (US), pas irlandais | Supprimer ou déplacer région |

**Étape 4 — Vérifier les alternatives**
```python
# Tester variantes : sans suffixe, suffixe différent, ADR US
candidates = ['RO.SW', 'RHHBY', 'GBLB.BR', 'RKT.L']
for t in candidates:
    h = yf.download(t, period='5d', interval='1d', progress=False, auto_adjust=True)
    last = round(h['Close'].squeeze().iloc[-1], 2) if len(h) else 0
    print(f'{t}: rows={len(h)} last={last}')
```

**Étape 5 — Vérifier doublons dans la liste avant d'ajouter**
```python
# Avant d'ajouter STLAM.MI, vérifier qu'il n'est pas déjà là
grep STLAM backend/data/tickers.py
```

**Étape 6 — Inventaire DB avant migration (pour les renommages uniquement)**
```sql
SELECT ticker,
  (SELECT COUNT(*) FROM articles a WHERE a.ticker = t.ticker) AS articles,
  (SELECT COUNT(*) FROM ow_cache o WHERE o.ticker = t.ticker) AS ow_cache,
  (SELECT COUNT(*) FROM bub_cache b WHERE b.ticker = t.ticker) AS bub_cache,
  (SELECT COUNT(*) FROM breaks_weekly bw WHERE bw.ticker = t.ticker) AS breaks_w,
  (SELECT COUNT(*) FROM breaks_monthly bm WHERE bm.ticker = t.ticker) AS breaks_m
FROM (VALUES ('ROG.SW'), ('RB.L')) AS t(ticker);
```
Vérifier aussi que la destination est vide (même query avec les nouveaux codes).

**Étape 7 — Migration DB (renommages)**
```sql
BEGIN;
UPDATE articles       SET ticker = 'RO.SW' WHERE ticker = 'ROG.SW';
UPDATE ow_cache       SET ticker = 'RO.SW' WHERE ticker = 'ROG.SW';
UPDATE breaks_weekly  SET ticker = 'RO.SW' WHERE ticker = 'ROG.SW';
UPDATE breaks_monthly SET ticker = 'RO.SW' WHERE ticker = 'ROG.SW';
COMMIT;
```
Tables à couvrir systématiquement : `articles`, `ow_cache`, `bub_cache`, `breaks_weekly`, `breaks_monthly`.
`bub_cache` était souvent vide pour les tickers problématiques (0 dots = 0 rows) mais le vérifier quand même.

**Étape 8 — Mettre à jour les fichiers**
- `backend/data/tickers.py` : supprimer/remplacer les tickers
- `backend/data/ticker_aliases.py` : supprimer les entrées obsolètes + ajouter les nouvelles
  - Pour un renommage : renommer la clé et mettre à jour les noms si nécessaire
  - Pour une suppression avec doublon existant (ex: AB-INBEV.BR → ABI.BR déjà là) : déplacer les aliases sur la clé qui reste

**Étape 9 — Ce que le batch gère automatiquement (rien à faire manuellement)**
- Breaks manquants → `refresh_breakers.py` les génère au prochain run
- Bubs → `zen_watcher.py` traite les nouveaux tickers en `todo` au prochain cycle
- Translations → `translate_bulk.py` pickup `en IS NOT NULL AND fr IS NULL` automatiquement

---

### Scripts batch — renommage à faire
| Nom actuel | Futur nom |
|---|---|
| `batch_fill_db.py` | `batch_bub.py` |
| `refresh_breakers.py` | `batch_breakers.py` |
| `translate_bulk.py` | `batch_translate.py` |

---

### Analyse mots ticker — `backend/scripts/analyze_ticker_words.py`

Analyse les mots les plus fréquents dans headlines (poids x3) + summaries (poids x1) des articles en DB, par ticker.
Sert à valider et enrichir `data/ticker_aliases.py`. Marque ★ les candidats non encore dans les aliases.

```bash
# Toutes régions, export JSON complet
python scripts/analyze_ticker_words.py --region ALL --top 30 --min-count 2 --output aliases_candidates.json

# Région seule
python scripts/analyze_ticker_words.py --region US --top 20

# Ticker unique
python scripts/analyze_ticker_words.py --ticker AAPL --top 50
```

Lit uniquement les articles `is_related != False`. Exclut stop words EN, termes financiers génériques, ticker ID, company name, et aliases déjà connus dans `TICKER_ALIASES`.

---

### Batch scraper — `backend/scripts/batch_fill_db.py`

Stateless : aucun fichier JSON local. Source de vérité = Supabase (check DB par phase au démarrage).

```bash
python scripts/batch_fill_db.py --phase scrape --region US
python scripts/batch_fill_db.py --phase enrich --region US
python scripts/batch_fill_db.py --phase bubbles --region US --lang en                          # normal : skip tickers complets en DB
python scripts/batch_fill_db.py --phase bubbles --region US --lang en --empty-only             # tickers ayant ≥1 bub_en NULL
python scripts/batch_fill_db.py --phase bubbles --region US --lang en --force                  # regen tous les dots de bub_cache
python scripts/batch_fill_db.py --phase bubbles --region all --lang en --force --ticker AAPL
python scripts/batch_fill_db.py --phase bubbles --region US --lang en --cascade promo          # override cascade LLM (zen par défaut)
python scripts/batch_fill_db.py --phase all --region US
python scripts/batch_fill_db.py --phase translate-fr --region US
python scripts/batch_fill_db.py --phase translate-fr-dumb --region US
```

**Checks DB par phase (skip logic) :**
| Phase | Condition skip ticker |
|---|---|
| `scrape` | row `articles` existe pour ce ticker + mois courant |
| `enrich` | `ow_cache.ow7d_en IS NOT NULL` |
| `bubbles` normal | tickers dont `bub_en IS NOT NULL` dans bub_cache (au moins 1 row) |
| `bubbles --empty-only` | tickers ayant ≥1 row avec `bub_en IS NULL` dans bub_cache |
| `bubbles --force` | aucun skip — traite tous les dots existants en bub_cache |
| `translate-fr` | `bub_cache.bub_fr IS NOT NULL` ou `bub_en IN ('', 'NO_ARTICLES')` |
| `translate-fr-dumb` | `articles.dumb_fr IS NOT NULL` |

---

### Traduction bulk — `backend/scripts/translate_bulk.py`

```bash
python scripts/translate_bulk.py --only m              # M breakers uniquement
python scripts/translate_bulk.py --region EU --only y,m  # Y + M, EU uniquement
python scripts/translate_bulk.py --ticker AXA.PA       # ticker unique
python scripts/translate_bulk.py --since 2h            # lignes mises à jour dans les 2h
python scripts/translate_bulk.py --cascade zen         # override cascade (défaut: promo)
python scripts/translate_bulk.py --dry-run             # compte sans écrire
```

- Cible : `_en IS NOT NULL AND _fr IS NULL` — idempotent, relançable sans risque
- Cascade défaut : `promo` (Gemini → raise) · Override : `--cascade zen` (ZenLLM → raise)
- `--cascade zen` : workers forcé à 1 automatiquement (ZenLLM sérialisé via `_zen_lock` + `ZEN_MIN_INTERVAL=10s`)
- Abort si >75% d'échecs sur un batch (promo) ou 3 échecs consécutifs (zen) → notif TG + break
- Notifs TG : démarrage · progression 25/50/75% par step · fin · exhaustion provider
- `TRANSLATE_PROMPT` importé depuis `translation_service.py` (source unique)

---

### Batch breakers — `backend/scripts/refresh_breakers.py`

Scrape articles + génère weekly/monthly breakers pour tous les tickers d'une région.

```bash
python scripts/refresh_breakers.py --region US --lang en                           # run complet — scrape P1+P2 → DUMB → W → M
python scripts/refresh_breakers.py --region US --lang en --retry-skipped           # re-scrape ciblé : tickers W less_10_articles/scrap_budget ou M absent (mois courant)
python scripts/refresh_breakers.py --region US --lang en --retry-missed            # retraite les (ticker, ym) du scrap_missed_checkpoint.json
python scripts/refresh_breakers.py --region US --lang en --skip-scrape             # DUMB+W+M uniquement, articles déjà frais
python scripts/refresh_breakers.py --region US --lang en --skip-p1                 # saute RSS+Yahoo+Finnhub, garde P2+génération
python scripts/refresh_breakers.py --region US --lang en --force-regen             # régénère W+M même s'ils existent déjà
python scripts/refresh_breakers.py --region US --lang en --gen-lesser              # génère done_less_10 pour W less_10_articles dont tous P2 actifs ont tourné (US: newsapi+marketaux+av · EU/ASIA: newsapi+marketaux) — sans scrape
python scripts/refresh_breakers.py --region EU  --lang en --w-rescue               # cascade P2 complète (newsapi→marketaux→AV) sur W bloqués <10 arts (6m) — idempotent
python scripts/refresh_breakers.py --region US --lang en --ticker AAPL             # ticker unique (debug)
```

**Flow complet :**

*Phase 1 — sources gratuites (séquentiel) :*
- Google RSS (toutes régions, 12 mois · 0.5s entre calls)
- Yahoo (toutes régions, mois courant uniquement — API sans filtre date)
- Finnhub (US uniquement, 12 mois)

*Gate qty P1 → P2 (`_build_qty_gate`) :*
- **W-gate** : mois courant < `MIN_W_SCRAP` (10) arts sur la semaine courante, ou mois passé < `MIN_M_SCRAP` (25) arts → cascade NewsAPI + Marketaux
- **M-gate** : après NewsAPI+Marketaux, paires encore < `MIN_M_SCRAP` (25) arts/mois → cascade AV

*Phase 2 — sources payantes :*
- NewsAPI (budget 100 req/j · 1 mois · cibles W-gate) → Marketaux (budget 100 req/j · 3 mois · cibles W-gate) → AV (budget 25 req/j · **1 call/ticker** · fenêtre 12m · limit=1000 · split par year_month en mémoire · cibles M-gate)
- Tag `scrap_budget` uniquement si les 3 sources payantes ont été skippées faute de budget pour ce ticker/mois

*Post-scrap par ticker/mois (après cascade complète) :*
- **DUMB** → `gen_dumb(promo)` · `cutoff_days=7` (défaut) · cap 30 articles · 1 call LLM batch · log `DUMB {ticker}: N arts [Wx]` si N > 0
- **Skew Finnhub** (US uniquement) : si articles Finnhub clustérisés sur une semaine → W de cette semaine généré sans les articles Finnhub (Yahoo + RSS + sources payantes seulement)
- **Gen W** sur les 5 semaines glissantes :
  - < 10 arts ET P2 pas épuisé → `status=less_10_articles` (skip)
  - < 10 arts ET P2 épuisé (tried_sources ⊇ sources actives région) → LLM → `status=done_less_10`
  - ≥ 10 arts → LLM → `status=done_more_10`
  - Input LLM : **tous les articles de la semaine sans cap** via `_article_text` (headline seul pour RSS, headline + summary pour Yahoo/payants)
  - P2-exhaust check : lit `tried_sources` (pas `p2_sources`) pour décider si `done_less_10` est éligible
- **Gen M** :
  - Skip si un W du mois a `status=scrap_budget`
  - ≥ 3 W `status=done_more_10/done_less_10` → `_gen_monthly_from_weeklies`
  - < 3 W done → `_gen_monthly_from_raw` (tous les articles du mois sans cap)
  - Cascade M : `promo` dans refresh_breakers — aucun appel FreeTheAI, chemin weeklies inclus

**Input LLM W/M** : `_article_text(a)` — `dumb_en/fr` si disponible, sinon `summary`, sinon `headline` seul. Aucun cap sur le nombre d'articles envoyés. RSS = headline seul (pas de summary), Yahoo/payants = headline + summary. La qty compense le manque de profondeur RSS.

**Parallélisme post-scrap** : `ThreadPoolExecutor(10)` workers · stagger démarrage `index × 0.5s` · `_exec` retry ×5 sur disconnect Supabase (`RemoteProtocolError`, `ReadError`, `ConnectError`) avec backoff exponentiel 3s → 9s → 27s → 81s → 243s → raise · `_retry_call(fn, *args)` : même logique que `_exec` mais pour les fonctions de service (`gen_dumb`, `scrap_gen_monthly`) qui appellent supa directement · Client Supabase HTTP/1.1 forcé dans `_supa()` (postgrest-py hardcode `http2=True` → GOAWAY frames → "Server disconnected" même à 2 workers — fix : remplacer `client.postgrest.session` par `httpx.Client(http2=False)` après création) · Logs : `→ {ticker} [Wx]` au démarrage de chaque worker (Wx = thread index) · Ctrl+C : les threads en cours finissent proprement, les W/M déjà loggés `✓` sont en DB, le reste est retryé au prochain `--skip-scrape`.

**`--w-rescue`** : cascade P2 complète déclenchée par W-gate (<10 arts) au lieu du M-gate. Sélectionne tous les `(ticker, year_month)` avec `breaks_weekly.article_count < 10` ET `status NOT IN (done_more_10, done_less_10)` sur les 6 derniers mois. Pour chaque paire : lit `p2_sources` → newsapi si absent (1m, tous tickers) → marketaux si absent (3m, tous tickers) → AV si absent (12m, **US seulement** — AV n'a pas de coverage EU/ASIA). AV tourne en ticker-level (1 req/ticker, 12m groupés en mémoire). Idempotent : relancer plusieurs fois = 0 appel redondant. Après chaque upsert, `scrap_gen_weekly` re-évalue les W du mois → écrit `done_more_10` si ≥10 arts. Les W encore insuffisants après `--w-rescue` restent à `less_10_articles` et sont traités par `--gen-lesser`.

**Bulk qty check** : `_build_qty_gate` charge les articles (JSONB) en une seule requête batch pour toutes les paires candidates, compte en mémoire — jamais de double boucle avec requêtes individuelles. `_build_targets` (freshness via `cached_at`) reste utilisé pour construire la liste initiale des paires à scraper (P1).

**Filtrage pertinence (prompts)** : les prompts weekly et monthly demandent explicitement au LLM d'ignorer les articles génériques (top marchés, classements) non spécifiques au ticker. Pas de filtre DUMB/SKIP (90% des articles historiques n'ont pas de `dumb_en`) — le filtre est dans l'instruction prompt.

**Checkpoints** — `backend/scripts/refresh_breakers_{region}_{lang}.json` (un par combo région+langue)
- `done` et `monthly_done` reconstruits depuis la DB au démarrage — source de vérité, pas de divergence possible
- JSON conservé pour inspection (done + monthly_done), aucun champ n'est chargé depuis le JSON au démarrage
- Un ticker est marqué `done` **uniquement si aucun échec LLM** — si failures > 0, retenté au prochain run
- **`--no-resume`** : ignore la DB et le JSON, repart de zéro
- **`--force-regen`** : régénère W+M pour tous les tickers du run, même s'ils existent déjà en DB

**Notifs TG** — départ · checkpoint tous les 30 tickers (%, ETA, +articles, sources LLM W/M, DUMB ok/fail, skewed, erreurs) · fin
Format : `📅 W: 14 [14 hapuppy]` · `🤖 DUMB 18 ok · 2 fail` · `📆 M: 186 [186 hapuppy]`

**Logging console/fichier** (`refresh_breakers.log`) :
- httpx et httpcore silencés (`WARNING+` seulement) — zéro bruit réseau dans les logs normaux
- **Startup** : `_build_state_from_db` loggue l'état W (`done / sans row / by status`) + état M par mois (`done / à gen / sans articles`)
- **RSS progress** : log tous les 100 tickers avec ETA (`RSS [100/2601] · +X arts · ~Ymin restant`)
- **Per-source summary** : résumé à la fin de chaque source (`RSS done: N tickers maj · +X articles`). Pour les scripts scrap standalone : breakdown `source_counts` par effective source affiché en fin de log ET en notif TG (`google_rss: 45 | mixed: 48`)
- **Skips explicites** : `⏭ W ticker YM Wn — aucun article en DB` · `⏭ W ticker YM Wn — N arts < 10 (skip)`
- **Transitions de statut** : `↑ W ticker YM Wn: retry après less_10_articles` — loggué quand un ticker qui avait un statut non-null va être re-généré
- **Statuts W/M** : `NULL` = pas encore traité · `done_more_10` = généré >= 10 arts · `done_less_10` = généré < 10 arts (P2 épuisé) · `less_10_articles` = < 10 arts, P2 non épuisé · `scrap_budget` = toutes P2 épuisées, reset au prochain run · `no_articles_row` = pas de row articles en DB · `no_articles` = 0 articles pour la semaine · `error` = exception LLM
- **DUMB skip** : `DUMB ticker: skip (tous déjà brutalised)` quand `gen_dumb` retourne 0
- **LLM calls** (dans `breakers_articles_service._llm_call` et `refresh_breakers._llm_call`) : une seule ligne post-call `LLM [ticker] cascade → provider (model)` — ex: `LLM [SAN.PA] promo → hapuppy (gpt-5.5)`. `max_tokens` non loggué (et non envoyé à l'API pour `promo`)

**Note lang** — `--lang en` peut tourner immédiatement. `--lang fr` requiert que `bw_fr`/`bm_fr`/`by_fr` soient remplis (via `batch_fill_db.py --phase translate-fr`).


**Fenêtre articles bubbles** : `bubble_service.py` T-6→T+6 fixe. S'applique batch + live (`/api/events`).

**Prompt BUB** : tous les articles sans summary (headline seul, no limit) + tous les articles avec summary (no limit). W breaker hebdomadaire injecté si présent. Cascade `bub_prod` = Gemini → Hapuppy, `max_tokens=280`. Cascade `bub_batch` (batch local) = FreeTheAI → Gemini → raise, no `max_tokens`.

**`get_or_gen_bub()` params** : `prefetched_cache` (dict row, skip SELECT individuel), `in_1w`/`in_1m`/`in_6m`/`in_1y` (bool, inclus dans l'upsert ; sur cache-hit, update uniquement si valeur différente), `force_regen` (bool, bypass cache → re-génère même si bub_en déjà présent).

**`bub_cache` colonnes fenêtres** : `in_1w`, `in_1m`, `in_6m`, `in_1y` (BOOLEAN) — indiquent dans quelle(s) fenêtre(s) display le dot est actuellement affiché. Alignées sur le frontend (4 périodes avec dots) :
| yfinance period | interval | window | max_dots | col DB |
|---|---|---|---|---|
| `5d` | `1h` | 3 | 5 | `in_1w` |
| `1mo` | `1d` | 2 | 8 | `in_1m` |
| `6mo` | `1d` | 4 | 10 | `in_6m` |
| `1y` | `1d` | 5 | 10 | `in_1y` |
Pas de dots sur `1d` et `5y`. Un dot peut être dans plusieurs fenêtres simultanément.

**Phase bubbles batch** : détection dots par fenêtre → `dot_windows: {date: {windows}}` → passe `in_1w`/`in_1m`/`in_6m`/`in_1y` à `get_or_gen_bub`. Logs : `TICKER — N dots · X/N cached`, puis par BUB : `✓ gemini [1m,6m]`. TG checkpoint : stats LLM + cache + `1w: X | 1m: Y | 6m: Z | 1y: W`. Flag `--cleanup` : supprime les rows `bub_cache` orphelines (date hors détection actuelle) après le run.

**LLM failures** : une failure upsert quand même la row avec `bub_en=NULL` + `articles` rempli. `--empty-only` au run suivant capte ces tickers et retente le LLM. Pas besoin de flag séparé `--failed-only`.

**Couverture bub_en (juin 2026, batch initial)** :
| Région | Rows | Avec bub_en | % |
|---|---|---|---|
| US | 2 485 | 2 223 | 89.5% |
| EU | 1 426 | 1 158 | 81.2% |
| ASIA | 3 853 | 1 744 | 45.3% (batch en cours) |
NULL restants = LLM failures du jour, rattrapables via `--empty-only`. Pas de dates orphelines hors fenêtre.

**Cleanup bub_cache** : peu fréquent (fenêtres longues = dots stables). Via `--cleanup` sur le batch uniquement, jamais automatique.

**Re-process tickers skippés** : utiliser `--force --ticker TICKER` pour re-traiter un ticker spécifique, ou `--empty-only` pour attraper tous les tickers avec `bub_en NULL`.

**Concurrence phase bubbles** : `ThreadPoolExecutor(BUBBLES_WORKERS=3)` — 3 tickers en parallèle. `BUBBLES_WORKERS=1` forcé par `zen_watcher.py` (ZenLLM fragile sous concurrence). Ctrl+C propre : `os._exit(0)`.

**Sentinel bub_cache no-articles** : si un ticker a 0 articles en DB pour la fenêtre T-6→T+6, `get_or_gen_bub` écrit `bub_en=""` (chaîne vide) au lieu de NULL. `""` est IS NOT NULL en SQL → n'entre pas dans `--empty-only` → pas de retraitement infini. En Python `if cause:` est falsy pour `""` → si des articles arrivent plus tard, le LLM sera rappelé. Tickers delisted (0 dots yfinance) ne génèrent jamais de row bub_cache → toujours dans le `todo` de `--empty-only` (4s de 404 yfinance, aucun LLM call).

**PostgREST URL limit** : tout `in_("ticker", liste)` avec une liste région complète (>100 tickers) doit être chunké en 100 max — PostgREST tronque silencieusement au-delà, sans erreur. Helpers dédiés dans chaque script : `_in_chunks()` (batch_fill_db), `_in_ticker_chunks()` (refresh_breakers), chunking intégré dans `_fetch_all()` (translate_bulk). `in_("year_month", ...)` avec ≤12 mois est safe.

**Notifs TG batch** : `parse_mode: HTML` (Markdown v1 causait des 400 sur les underscores/accents). Conversion `*bold*` → `<b>bold</b>` et `_italic_` → `<i>italic</i>` dans `_telegram()`. Tout `<` ou `>` dans le texte doit être échappé `&lt;`/`&gt;` — sinon Telegram retourne 400 silencieusement (ex: `arts < 3` causait 400 sur les runs EU avec skipped > 0).


---

---

## État courant

### Features

| Feature | Statut | Notes |
|---|---|---|
| Home — search + recent + watchlist | ✅ | Supabase pinned_stocks branché |
| Auth email/password | ✅ | |
| Auth Google OAuth | ✅ | |
| Auth Discord OAuth | ✅ | |
| Page stock — prix + stats | ✅ | fast_info + t.info (name, PE, website) |
| Page stock — header | ✅ | Logo Logo.dev · toggle EUR/USD · étoile pin · lien website |
| Page stock — chart Chart.js | ✅ | |
| Chart — sélecteur de période | ✅ | 24H / 1W / 1M / 6M / 1Y / 5Y |
| Chart — % adaptatif | ✅ | 24H = change_pct yfinance · autres = (last-first)/first depuis chart data |
| Toggle USD ↔ EUR | ✅ | Prix, 52W High/Low, tooltip, axes Y |
| Préférence devise | ✅ | localStorage (non-connecté) · Supabase (connecté) |
| Préférence langue | ✅ | localStorage `sv_lang_pref` (non-connecté) · Supabase `user_preferences.lang` (connecté) · hook `useLangPref` · profile écrit les deux simultanément |
| Page profile | ✅ | Devise + compte + sign out |
| News — fetch + drawer + vidéo | ✅ | |
| News — résumés IA | ✅ | Cascade FreeTheAI → Groq |
| Cache news Supabase | ✅ | Pas de TTL — refresh sur nouveaux articles uniquement (crons Yahoo */3h / Finnhub */6h). DUMB dans articles. |
| EN-first migration | ✅ | `LLM_LANG=en`. Tous les services génèrent EN par défaut. Colonnes DB : `bub_cache.cause_en`. |
| Annotations chart (events) | ✅ | Dots peaks/valleys → EventDrawer Finnhub + cause IA |
| Chart — vertical grid lines | ✅ | Tirets par période |
| Lazy loading page stock | ✅ | Default 24H — articles 1M+ et causes events : on-demand |
| Cascade LLM FreeTheAI→Groq | ✅ | grok-4.1-fast-non-reasoning → gemini-3.5-flash → Hapuppy gpt-4.1-mini → Groq |
| Daily digest (1J) | ✅ | email 19h15 · lit `ow1d_en/ow1d_fr` selon `user_preferences.lang` + articles 24h · `_ensure_fr_translations` amont |
| Weekly digest (7J) | ✅ | email sam 7h · lit `ow7d_en/ow7d_fr` selon `user_preferences.lang` + 5 articles 7j · `_ensure_fr_translations` amont |
| Digest — email | ✅ | Resend sandbox actif |
| EOD digest — onglet "Mes news" | ✅ | **DigestCard** : cartes avec news = normal. Cartes sans news (`no_24h_news=True`) = border neon rotatif (conic-gradient counter-clockwise, 3s). **Header chips** (inline, poussés à droite) : `[N news]` muted · `[last news Xh ago]` bleu · `[↻ Xh]` freshness-coloré (vert <6h · ambre 6-24h · rouge >24h). Badge sentiment ou `[NO NEWS]` coloré selon ancienneté. Un seul texte (OW 7J si no-news, OW 24H sinon). **Dédup** au render (`.filter` par ticker) — protège contre React StrictMode double-invocation. **ArticleRow** : date relative — `<1h` = `Xm`, `<24h` = `Xh`, `≥24h` = `Xj`. **Loading** : progressive — cartes apparaissent au fil des fetches. |
| Weekly router | ✅ | `/api/weekly/{ticker}` — `routers/digest.py` lit depuis **`ow_cache`** (`ow7d_en` = même OW 7J que la page stock + `cached_at`). Plus d'`breaks_weekly`. Timestamps D et W cohérents (même `cached_at`). Badge `7J` au lieu de `S1·2026-06`. `_week_articles_from_archive` inclut `dumb_en`/`dumb_fr` + filtre `_is_ticker_related`. |
| Préférences notif | ✅ | Toggle Daily (PREMIUM jaune) + Weekly (PREMIUM vert) + chips par ticker · `notify_weekly` en DB · toggle langue EN/FR (`user_preferences.lang`) |
| Loading animation — DogLoader | ✅ | Sleeping dog SVG, 2 comètes sens inverse, rotation 3D lente |
| DB articles — rotation & purge | ✅ | 5W max, monthly calendaire 1er du mois, yearly purge monthlies |
| Monthly on-demand (premium) | ✅ | `get_or_gen_breaks_monthly_ondemand()` — rolling 4 weeklies → LLM, pas de cache persistant |
| Articles 1M/6M/1Y/5Y — sections par période | ✅ | 1M=semaines W1-W4 · 6M/1Y=mois collapsés avec ExpandButton pill · 5Y=year dropdown (violet) + mois indentés. Pagination +10 par clic. Tous les articles retournés (pas de troncature backend). |
| Articles multi-source | ✅ | Dedup par headline. Badges colorés + multi-CTA dans drawer |
| LLM source tracking | ✅ | `llm_source` persisté dans ow_cache, summaries, bub_cache |
| Re-enrich Groq→FreeTheAI | ✅ | Cron 22h30 — scan `llm_source='groq'` + `ow7d_en IS NULL`. Si FreeTheAI down → pending, retry à partir de 2h toutes les 1h. |
| Cold start ticker rare | ✅ | Scrape progressif background (1M→6M→1Y). `ColdStartBanner` poll 10s. |
| 24h vide — fallback 0 token | ✅ | Pas d'appel LLM si 0 articles 24h. Badge `AUCUNE NEWS 24H` + note italique (le texte fallback moche n'est plus affiché). `no_24h_news` en DB. |
| Architecture DB — refacto | ✅ | `ow_cache` zéro articles. DUMB dans `articles`. Migration one-shot : 4 148 `dumb_en` copiés. |
| **Batch DB filler (717 tickers)** | 🔄 | Voir séquence ci-dessous |
| **is_related + articles_blocklist** | ✅ | Champ `is_related` dans articles JSON · table `articles_blocklist` · purge articles hors cible · dumb = source de vérité (pose True/False) · breaks + `_fetch_news` posent True via heuristique (writeback persisté) |
| **Ticker aliases** | ✅ | `data/ticker_aliases.py` · ~400 tickers US/EU/ASIA · noms de marque, filiales, acronymes journalistiques · à brancher dans `_is_ticker_related` pour le scoring |
| **articles_used + articles_related sur breaks/bub** | ✅ | Colonnes ajoutées sur `breaks_weekly`, `breaks_monthly`, `bub_cache` · setters dans tous les chemins de gen (gen_breaks_weekly, scrap_gen_weekly, _gen_monthly_from_weeklies, scrap_gen_monthly, run_monthly_calendaire, get_or_gen_bub) · NULL sur les breaks pré-migration |
| **Score heuristique related + standalone batch** | ⬜ | `_is_ticker_related` → score 0.0–1.0 (headline > summary > fuzzy + aliases) · score=0 → False direct · score>0 → LLM arbitre · script `check_related.py` batch historique · flag `--regen-low-quality` dans `refresh_breakers.py` |
| Articles migration Turso (champs lourds > 6M) | ⬜ | Code sur `feat/r2-articles-migration` · PR → main à merger · inactif jusqu'à batch scrap terminé + BUBs complets + `TURSO_URL` en prod · migration : `migrate_articles_cold.py --init-db` |
| Compaction guards W→M et M→Y | ⬜ | À coder sur main · `breakers_articles_service.py` · 2 guards W + 1 guard Y · voir section "Fixes purge W→M" |
| Consolidation ticker→name | ✅ | `get_company_name` + `bulk_fetch_company_names` dans `yfinance_service.py` · `_KNOWN_NAMES` 28 tickers EU · `breakers_articles_service` redirige via alias · 7 fichiers mis à jour |
| BUB hiding dynamique (dots sans cause) | ✅ | `GET /api/bub-dates/{ticker}?lang=` + `POST /api/translate-bub/{ticker}/{date}` · stock page filtre `outlierIndices` par `bubDates` Set · EventDrawer : fallback EN si bub_fr null + `is_fallback` flag + trad async on-demand · labels i18n EN/FR |
| Reddit sentiment | ⬜ | À venir |
| **DUMB FR prod cron** | ⬜ | `dumb_fr` non généré en prod. `gen_dumb` génère uniquement `dumb_en` (cascade `dumb_prod`). La traduction `cold_start_translate_fr` (19h) couvre les OW FR mais PAS les `dumb_fr` des articles. Les users FR voient les articles en EN dans les drawers. À implémenter : cron `translate_dumb_fr` après chaque scrap Yahoo/RSS pour les tickers pinnés par des users avec `lang='fr'`. Prérequis : query `user_preferences` pour cibler les tickers concernés uniquement. |
| Refresh proactif OW (crons) | ✅ | Yahoo/Finnhub trigger `gen_ow_both(force_24h=True)` si nouveaux articles |
| Stale-while-revalidate OW | ✅ | Cache retourné immédiatement + background `gen_ow_both(force_24h=True)` + badge `↻ mise à jour...` (vert + spin). Filet 6h : visite page trigger refresh si `cached_at > 6h` même sans nouveaux articles. |

---

### Séquence batch — EN first (relance 2026-06-01)

| # | Phase | Région | Statut |
|---|---|---|---|
| 1 | Bubbles 6M+1Y | US | ✅ |
| 2 | Bubbles 6M+1Y | EU | ✅ |
| 3 | Bubbles 6M+1Y | ASIA | ✅ (208 tickers · 2136 bubbles · 2026-06-03) |
| 4 | Breakers weekly+monthly EN+FR | US | ✅ W: 1356/1425 (95%) · M: 100% · 69 W en less_10_articles (2026-05/06) · 21 done_more_10 sans bw_en à re-runner |
| 5 | Breakers weekly+monthly EN+FR | EU | 🔄 W: 812/1308 (62%) · M: 100% · 496 W en less_10_articles · --phase lesser à lancer |
| 6 | Breakers weekly+monthly EN+FR | ASIA | 🔄 W: 921/1397 (66%) · M: 100% · 476 W en less_10_articles · --phase lesser à lancer |
| 7 | OW 1D+7D EN | EU · ASIA | ⏳ se remplit via cron au fil des visites (ow7d_fr déjà peuplé via crons FR) |
| — | OW enrich (ow7d_en) | EU · ASIA | ⏭ skip — se remplit via cron 3h + reenrich 22h30 |

**Conso réelle — nuit 29-30 mai 2026 (261 tickers US enrichis) :**
| Provider | Modèle | Requests | Tokens input | Tokens output | Coût |
|---|---|---|---|---|---|
| FreeTheAI | `bbl/grok-4.1-fast-non-reasoning` | ~522 | 51.9M | 269K | $0 |
| Groq | `llama-4-scout-17b` | 200 | 495.7K | 221.3K | $0 |
| Groq | `llama-3.3-70b-versatile` | 139 | 83.1K | 29.4K | $0 |
| Groq | `llama-3.1-8b-instant` | 107 | 39.5K | 20K | $0 |

⚠️ FreeTheAI avg **99K tokens input / call**. Un batch complet (717 tickers × 2 calls) = ~1400 calls → dépasse tous les tiers. Prévoir plusieurs nuits ou alterner avec Groq.

**Avant tout batch FreeTheAI :** vérifier `ps aux | grep python` et killer tout processus concurrent utilisant la même clé API.

---

### Optimisations réalisées ✅

| # | Décision | Fichier |
|---|---|---|
| **articles pipeline** | Yahoo */3h + Finnhub */6h → merge. `_get_or_fetch_month` merge au lieu d'écraser. | `breakers_articles_service.py`, `main.py` |
| **DB2** | `daily_digest` ne stocke plus `articles` | `digest_service.py` |
| **DB3** | Index `idx_breaks_weekly_ticker_date` — à exécuter en SQL Editor Supabase | — |
| **DB4** | Purge `bub_cache > 1 an` dans cron Monday 3h | `main.py` |
| **LLM2** | `_articles_hash()` — skip brutalise si IDs inchangés. Stocke `articles_hash` | `overview_service.py` |
| **LLM4** | BRUTALIZE retourne `summary` (7J) → stocké directement comme `ai_summary` + `ow7d_cached_at`. Call 2 actif uniquement en `BATCH_MODE=1` | `overview_service.py` |
| **LLM5** | `_get_stock_context()` dédupliqué — calculé une fois dans `gen_ow_both()`, passé aux deux calls (4→2 appels yfinance) | `overview_service.py` |
| **Chart1** | Labels 24H en `HH:MM` au lieu de `YYYY-MM-DD` — bougies 5-min tracées correctement | `yfinance_service.py` |
| **Perf1** | Stale-while-revalidate sur `GET /api/news/` — `get_or_refresh_ow_both()` + `BackgroundTasks`. Premier load bloquant uniquement. | `routers/news.py`, `overview_service.py` |
| **Cron1** | Yahoo + Finnhub crons triggèrent `gen_ow_both(force_24h=True)` après nouveaux articles — corrige `no_24h_news` stale | `breakers_articles_service.py` |
| **Weekly Pool 1** | `_maybe_regen_weekly()` dans cron hourly | `overview_service.py` |
| **Weekly Pool 2** | `gen_breaks_weekly` (lundi 3h30) | `breakers_articles_service.py` |
| **Perf2** | Re-fetch frontend auto 20s après `is_refreshing: true` — polling récursif (active flag) : continue toutes les 20s tant que `is_refreshing: true`, s'arrête dès `false` ou démontage composant. Était un setTimeout one-shot (spinner bloqué si LLM > 20s). | `page.tsx` |
| **DB5** | Colonne `exchange` dans `ow_cache` — peuplée au trigger visite + one-shot `populate_exchange.py`. Prévu pour gate market-hours cron. | `overview_service.py` |
| **UX2** | Articles 24h filtrés sur présence DUMB — `getDisplayedArticles()` (view 1d) exclut les articles sans `dumb_en \|\| dumb_fr`. Les articles hors-sujet (SKIP LLM) disparaissent de la liste. Filtre EN-first : `dumb_en` est toujours généré en premier, `dumb_fr` n'est qu'un fallback — ne jamais conditionner l'affichage sur `dumb_fr` seul. **Card layout** : pic 64×48px à gauche couvrant 2 lignes (source+date / title), DUMB pleine largeur en dessous. | `page.tsx` |
| **UX3** | OW card — date `cached_at` affichée en vert accent (monospace, `DD/MM HH:MM`) à droite du label "AI OVERVIEW", visible en 24H et 7D. Padding réduit (`14px 18px`). BULLISH/BEARISH badge déplacé en `position: absolute` top-right du bloc stock header (à côté du nom d'action), visible en 24H et 7D. Header "NEWS & ANALYSIS" n'affiche plus la période en vue 24H et 7D (la date dans l'OW card suffit). | `page.tsx` |
| **OW1** | Cutoff 24H = minuit UTC du jour courant (`now.replace(hour=0,...)`). Articles du jour uniquement — pas un créneau glissant de 24h. Frontend `getDisplayedArticles()` + backend `_filter_24h()`. | `page.tsx`, `overview_service.py` |
| **Cron2** | Yahoo */3h + Finnhub */6h (était */6h + */12h). TTL supprimé — `_needs_24h_refresh` et `_needs_7d_refresh` retournent `True` uniquement si summary absent. `_is_stale_6h()` = filet sécurité visite page. | `overview_service.py`, `main.py` |
| **DB6** | `daily_digest` supprimée — my-news lit `/api/news/{ticker}` · email EOD lit `ow_cache` directement, zéro LLM | `digest_service.py`, `main.py`, `my-news/page.tsx` |
| **UX1** | Weekly freshness dot dans `WeeklyCard` (my-news) — vert <6h · orange <24h · rouge >24h (aligne sur `cached_at` depuis `ow_cache`). Badge `↻ mise à jour...` visible (vert + spin). Bug "None" dans fallback no-news corrigé. | `my-news/page.tsx`, `globals.css`, `overview_service.py` |
| **UX8** | **DigestCard no-news redesign** — border neon rotatif conic-gradient (3s, counter-clockwise, `@property --border-angle`). Badge split `[NO NEWS]\|[Xj]` coloré selon ancienneté du dernier article : jaune warn <3h, orange alert <6h, rouge critical ≥6h. Sentiment badge masqué si no-news. Un seul texte (OW 7J depuis `ow_cache.summary`). | `my-news/page.tsx`, `globals.css` |
| **UX9** | **ArticleRow** — date `≥24h` = `Xj` au lieu de `HH:MM` (l'heure-du-jour trompait l'utilisateur sur l'ancienneté réelle). | `my-news/page.tsx` |
| **Perf3** | **Loading progressif my-news** — `Promise.all` → fetch individuel + `setDigests/setWeeklies` incrémental. Spinner s'arrête dès la 1ère réponse reçue, cards suivantes apparaissent au fil de l'eau. | `my-news/page.tsx` |
| **Data1** | **Hourly refresh fetch Yahoo avant lecture DB** — `run_hourly_news_refresh` appelle `_accumulate_yahoo` en début de boucle. Corrige le cas tickers < 3 pins (ex: 7203.T) dont la DB ne se mettait jamais à jour car `run_yahoo_accumulation` exige ≥ 3 users. | `services/overview_service.py` |
| **Data2** | **`_is_ticker_related` — base ticker sans suffixe exchange** — ajoute `"7203"` (base de `"7203.T"`) aux keywords. Corrige le faux-négatif sur tous les tickers non-US dont les headlines utilisent le format `TSE:7203` / `KSC:005930`. Affecte JP, KR, HK, etc. | `services/breakers_articles_service.py` |
| **Data4** | **Yahoo company name search (EU/ASIA)** — `_yahoo_scrap_accumulate` (cron prod) + `_fetch_yahoo_month` (batch) : pour les tickers avec `.` dans le symbole, fetch `yf.Search(company_name, news_count=30)` en plus de `get_news(100)`. Dedup par URL. Apporte +8 à +10 articles supplémentaires sur les tickers ASIA (ex: 2382.HK Sunny Optical : 1→9). US inchangé (pas de `.`). `name_search` param dans `_fetch_yahoo_month`/`_scrape_and_upsert`/`_process_ticker_ab` — propagé depuis `main()` : `name_search = args.region != "US"`. | `scripts/refresh_breakers.py`, `services/breakers_articles_service.py` |
| **Data3** | **`_is_ticker_related` + company_name partout** — `_fetch_news` et `_week_articles_from_archive` appelaient le filtre sans `company_name` → articles liés à la société filtrés si le ticker ne figurait pas tel quel dans le texte. Fix : `_lookup_company_name(ticker, supa)` lookup depuis `pinned_stocks` dans `_fetch_news` ; idem dans `routers/digest.py` weekly endpoint. Couvre tous les appelants sans `company_name` explicite (OW, DUMB, my-news weekly). | `services/overview_service.py`, `routers/digest.py` |
| **Breaks4** | **`refresh_breakers.py` — DUMB avant condensation** — `_process_ticker` appelle `gen_dumb(ticker, lang)` en step 1b (après scrape, avant weeklies). Sans ce step, tous les Breakers étaient générés depuis `summary→headline` fallback car `brutalise_new_articles` n'était jamais appelé pour les tickers batch (`min_users=3` dans les crons Yahoo/Finnhub excluait les ~700 tickers batch). US/EU/ASIA Breakers déjà générés = qualité dégradée. | `scripts/refresh_breakers.py` |
| **UX10** | **DigestCard/WeeklyCard — chips header** — 3 chips inline dans la ligne ticker (poussés à droite via `flex: 1`) : count muted · `last news Xh ago` bleu · `↻ Xh` freshness-coloré (vert <6h · ambre 6-24h · rouge >24h). Supprime le footer meta-ligne. `CARD_STYLE` constant retiré. Dédup render (StrictMode). `_articles_from_archive` cap `[:15]` → `[:100]` pour corriger W < D articles count. | `my-news/page.tsx`, `routers/digest.py` |
| **Fix1** | **`_brutalise` — fallback `headline` pour articles bruts DB** — `a['title']` → `a.get('title') or a.get('headline', '')`. Articles normalisés par `_fetch_news` ont `title` ; articles bruts JSONB (chemin `cutoff_days != 7` dans `gen_dumb`) ont `headline`. | `services/overview_service.py` |
| **Breaks5** | **`refresh_breakers.py` — re-run auto des Breakers pré-DUMB** — `_process_weeklies`/`_process_monthlies` acceptent `force_regen=True` (bypass `if exists.data: continue`). Les tickers `legacy` sont inclus dans `todo` et force-regénérés même s'ils sont dans `done`. Auto-marquage : si `done` non-vide + `legacy` vide au démarrage → tous `done` → `legacy` → un run normal suffit pour regen. Commande : `python scripts/refresh_breakers.py --region US/EU/ASIA --lang en`. | `scripts/refresh_breakers.py` |
| **LLM6** | DUMB découplé de l'OW — `gen_dumb(ticker)` standalone. Appelé par crons Yahoo/Finnhub avant `gen_ow_both(force_24h=True)`. `_call1_dumb_ow24h` : si tous les articles ont déjà leur dumb → `_ow7d_from_abrutis()` au lieu de re-brutaliser (suppression fallback `to_brutalise = fresh_articles[:30]`). | `overview_service.py`, `breakers_articles_service.py` |
| **Breaks1** | **Bug cold_start purge prématurée** — `_purge_excess_weeklies(max_weeks=5)` était appelé à l'intérieur de la boucle weeklies (après chaque mois), avant la boucle monthlies. Les weeklies des vieux mois étaient purgés avant d'être utilisés pour générer les monthly summaries → `len(week_rows.data) < 2` → aucun monthly généré pour les mois anciens. Fix : purge déplacée après les deux boucles (W → M → purge). | `cold_start_service.py` |
| **Breaks2** | **Bug lang hardcodé FR-first dans `read_breaks_by_period`** — 4 appels `ai.get("ai_summary_fr") or ai.get("ai_summary_en")` ignoraient `LLM_LANG`. Remplacés par `ai.get(_summary_col())` qui respecte `LLM_LANG`. Affecte vues 1M/6M/1Y/5Y (weeks, months, years). | `breakers_articles_service.py` |
| **Breaks3** | **Filtre `_is_ticker_related` élargi** — ancienne version : `ticker.lower() in headline` seulement → ratait tous les articles qui écrivent le nom ("Apple" au lieu de "AAPL"). Nouvelle version : OR sur `dumb_en/dumb_fr` + ticker + mots-clés du `company_name` (via `_company_keywords()`) dans `headline + summary`. `company_name` transmis par le frontend via `?company_name=` (vient de `stock.name`). Appliqué uniformément en 1M/6M/1Y. | `breakers_articles_service.py`, `routers/articles.py`, `page.tsx` |
| **UX4** | **Articles view 6M/1Y/5Y — compact sans thumbnail.** `CompactArticleRow` avec `directLink=true` (6M/1Y) : thumbnail supprimée, rendu compact identique à 1M (titre 11px + source badge + heure + icône lien externe). 5Y idem. Cohérence visuelle sur toutes les vues longues. | `page.tsx` |
| **UX5** | **ExpandButton pill sections.** Remplace le chevron discret par une pill `N articles ›` (border accent, fond plein au hover). Sections collapsées par défaut, pagination +10 par clic, bouton `↓ N de plus`. 5Y : year dropdown avec `expandedYears` state, année la plus récente ouverte par défaut, mois indentés avec bordure violette. `top_articles` non tronqué côté backend. | `page.tsx`, `breakers_articles_service.py` |
| **UX6** | **`_is_ticker_related` appliqué dans `overview_service.py`** — articles `articles` filtrés avant envoi au frontend (daily news) → articles hors-sujet exclus. Même filtre dans `digest.py` pour le weekly. `dumb_en`/`dumb_fr` passés dans `_articles_from_archive` → bouton dumb visible dans `ArticleRow` weekly. | `overview_service.py`, `routers/digest.py` |
| **UX7** | **`ArticleRow` my-news — compact + dumb expand.** Layout : titre + source badge + heure sur une seule ligne (source à droite). Bouton circle 22×22px vert, fond pâle → plein au hover, `›` tourne 90° au clic → dumb inline fond vert léger. Visible uniquement si `dumb_en \|\| dumb_fr`. `WeeklyCard` redesigné pour matcher `DigestCard` : même padding/typo, count `X articles 7j`, freshness dot. | `my-news/page.tsx` |

---

---

### Dette technique — Backend & UX

| # | Scope | Statut | Action réalisée |
|---|---|---|---|
| **BUX1** | `digest.py` · my-news 1W + 1D | DONE | Filtre `dumb_en != "SKIP"` ajouté dans `_articles_from_archive` — couvre 1D et 1W via héritage |
| **BUX2** | my-news frontend | DONE | `.filter(w => top_articles.length > 0)` ajouté avant le render des `WeeklyCard` |
| **BUX3** | `stock/[ticker]/page.tsx` | DONE | `StockData` → `TickerInfosYfinance` |
| **BUX4** | `stock/[ticker]/page.tsx` | DONE | `NewsData` → `TickerFrontWrapper` |
| **BUX5** | `my-news/page.tsx` | DONE | `DigestArticle` → `MyNews1DArticle` · `Digest` → `MyNews1DTicker` · `WeeklyArticle` → `MyNews1WArticle` · `Weekly` → `MyNews1WTicker` |
| **BUX6** | `my-news/page.tsx` | DONE | `digests` → `myNews1DTickers` · `weeklies` → `myNews1WTickers` · setters idem |
| **BUX7** | `EventDrawer.tsx` | DONE | `EventArticle` → `BubArticle` · `EventData` → `BubData` |
| **BUX8** | `ow_cache` | DONE | `generated_by_en` → `llm_source_en` · `generated_by_fr` → `llm_source_fr` · convention unifiée avec breaks + bub_cache |

---

### Dette technique DB — à traiter progressivement

| # | Table | Problème | Action |
|---|---|---|---|
| **DT1** | `breaks_weekly`, `breaks_monthly`, `breaks_yearly`, `bub_cache` | DONE | `llm_source` → `llm_source_en` + `llm_source_fr` · helper `_llm_source_col()` ajouté dans `breakers_articles_service.py` + `refresh_breakers.py` (oublié lors du refactor initial — causait PGRST204 en prod) |
| **DT2** | `breaks_weekly`, `breaks_monthly`, `breaks_yearly` | DONE | `ai_summary` legacy droppée DB + retiré de tous les `.select()`, fallbacks et upserts (`cold_start_service.py`, `breakers_articles_service.py`). Les 1 019 rows `less_10_articles` retournent `null` jusqu'au passage de `--phase lesser`. |
| **DT3** | `ow_cache` | DONE | DROP `llm_source` DB + retiré de `_build_response` |
| **DT4** | `ow_cache` | DONE | DROP `ai_sentiment_reason` DB + retiré upserts FR + `_build_response` + type TS |
| **DT5** | `ow_cache` | DONE | DROP `ai_sentiment_reason_24h` idem |
| **DT6** | `ow_cache` | DONE | `cached_at` → `ow1d_cached_at_en` · `ow7d_cached_at` → `ow7d_cached_at_en` · `_fr` ajoutés · MAJ `_is_stale_6h`, upserts, `_build_response`, cron |
| **DT7** | `ow_cache` | DONE | DROP `latest_news_id` DB |
| **DT8** | `bub_cache` | DONE | `cached_at` → `bub_cached_at_en` + `bub_cached_at_fr` ajouté |
| **DT9** | `bub_cache` | DONE | DROP `articles` jsonb + VACUUM FULL · 115 MB → 12 MB · 103 MB libérés |
| **DT10** | `articles` | DONE ✅ | `source TEXT` droppé → `sources TEXT[]` · `p2_sources TEXT[]` droppé → `tried_sources TEXT[]` · backfills exécutés · DROP colonnes effectués 2026-07-02 · tous les callers mis à jour (prod: `breakers_articles_service.py`, `cold_start_service.py` · batch: `refresh_breakers.py`) |
| **DT13** | `ow_cache` | DONE ✅ | ADD COLUMN `needs_ow_regen BOOLEAN DEFAULT FALSE` effectué 2026-07-02 · Option A active : scrapers P1 posent le flag si `added > 0`, `hourly_ow_regen :20` clear après gen_ow_both |
| **DT11** | `breaks_weekly` (1041 rows) | DONE | Rows legacy `bw_en IS NULL` traitées : 1019 `less_10_articles` → `--phase lesser` (EU/ASIA pending batch) · 21 `done_more_10` → `--phase ab` re-run · 63 à 0 article → vides définitivement (ai_summary droppée, rien à gen). DT2 exécuté sans attendre DT11 complet — safe car les less_10 auraient été regénérés de toute façon. |
| **DT12** | `overview_service.py:754` | DONE | `_regen_break_weekly_if_missing` lisait `ai_summary` (supprimée) — plus de référence, colonne absente de DB donc plus de PGRST204. |

### Storage DB — état au 2026-06-19

| Table | Avant | Après | Gain |
|---|---|---|---|
| `bub_cache` | 115 MB | 12 MB | -103 MB |
| `articles` | 292 MB | 292 MB | 289 MB de TOAST (vraie donnée JSONB, pas du bloat) |
| Total DB | ~499 MB | ~396 MB | -103 MB |

**Prochaine optimisation : migration articles > 6 mois vers Turso, purge > 13 mois**
- `bub_cache` purgé > 1 an → articles > 13 mois inutilisables (buffer 1 mois pour BUBs sur dots à la limite 12M)
- BUBs : 12 131 total, 8 598 avec `bub_en` (71%) — compléter génération BUB avant purge totale
- Cutoff migration vers Turso : **6 mois** · cutoff purge totale : **13 mois**
- Turso configuré : `stockvibe-articles-andrey-243.aws-eu-west-1.turso.io` · token dans `backend/.env`

**Contraintes bloquantes avant migration Turso et purge :**
1. **BUBs** : compléter génération BUB (3 533 manquants) avant toute purge. Dots actifs jusqu'à 12 mois ont besoin d'articles T±6j comme source LLM — si article non migré ET bub_en NULL → perte de donnée irrécouvrable.
2. **Batch scrap** : terminer le batch scrap 717 tickers avant de migrer — les scripts écrivent encore `summary/dumb_en/dumb_fr` dans Supabase. Migrer après la fin du batch.
3. **W avant digest** : weekly digest (sam 7h) lit `ow_cache.ow7d_en/fr`, pas `breaks_weekly` — pas de conflit. Safe.
4. **Cutoff articles** : **13 mois** (pas 12) — BUBs actifs jusqu'à 12 mois ont besoin d'articles T±6j comme source, buffer 1 mois nécessaire.
5. **Fenêtres display** : 1D = 26h (pas 24h) pour absorber les décalages horaires. 6M/1Y = `directLink=true` (titre + lien direct, pas de drawer, pas de summary). 5Y = zéro article brut.

**⚠ PR pendante : `feat/r2-articles-migration` → `main`** (code Turso inactif, safe à merger maintenant)

**Fixes purge W→M et M→Y — logique décidée (à coder sur main) :**

`run_monthly_calendaire` — 2 guards avant `_purge_weeklies_for_month` :
- **Guard 1 — contenu M non-null** : vérifier que `bm_en` (ou `bm_fr`) est non-null en DB avant de purger. Couvre le cas upsert silencieux avec contenu vide.
- **Guard 2 — W4 mois courant existe** : helper `_current_month_has_w4(ticker, supa)` — vérifie qu'un W4 avec `status IS NULL` existe pour le mois courant. Garantit que la vue 1M affiche 4 semaines complètes avant de supprimer le mois passé. Naturellement satisfait début du mois suivant (W4 condensé le lundi après le 22).
- S'applique aux 2 cas : monthly déjà en DB (re-run) ET génération fraîche.

`gen_breaks_yearly` — 1 guard avant `DELETE breaks_monthly` :
- **Guard — contenu Y non-null** : vérifier que `by_en` (ou `by_fr`) est non-null en DB après l'upsert. Si vide : log warning, monthlies conservés.

**Display par période (référence pour les purges) :**
| Vue | Composant articles | Drawer ? | Summary nécessaire |
|---|---|---|---|
| 1D / 1W | `NewsCard` | ArticleDrawer | oui — `dumb_en/fr` |
| 1M | `CompactArticleRow` | ArticleDrawer au clic | oui — summary + dumb |
| 6M / 1Y | `CompactArticleRow` `directLink=true` | non — lien direct URL | non |
| 5Y | aucun article brut | — | — |

---

### À revoir — flows en attente

| Service | Statut | Notes |
|---|---|---|
| `cold_start_service.py` + `routers/cold_start.py` | A revoir | Flow cold start tickers rares (hors top 250) via Finnhub — scrape progressif 1m→6m→1y + condensation Breaks. Route GET/POST `/ticker/{ticker}/init-status` + `/initialize`. A revoir : pertinence Finnhub comme source cold start vs nouveaux scrapers (Google RSS, Marketaux...) |

---

### Refacto 2026-06 — BUBs + batch_fill_db stateless ✅

**Terminé :** `regen_bubs.py` mergé dans `batch_fill_db.py --phase bubbles --force`. Tous les progress JSON locaux supprimés. `bubble_service.py` enrichi (weekly_breaker injecté + `force_regen` param).

**Scripts supprimés :** `regen_bubs.py` · `sync_progress.py` · `fix_checkpoint_from_db.py` · `fix_missing.py` · `break_view_resume.py` · `refetch_finnhub.py` · `test_google_rss.py` · `enrich_tonight.py` · `reprocess_groq_tickers.py` · `retro_fill_bub_windows.py` · `migrate_dumb_to_archive.py` · `populate_websites.py` · `populate_exchange.py`

**Commandes BUBs désormais :**
```bash
python scripts/batch_fill_db.py --phase bubbles --region all --lang en --empty-only  # dots vides
python scripts/batch_fill_db.py --phase bubbles --region all --lang en --force       # regen tous (remplace regen_bubs)
```

---

### Roadmap post-beta

| Feature | Priorité | Notes |
|---|---|---|
| Descriptions intelligentes des tickers | 🔜 | Table `ticker_descriptions` (ticker PK, description_fr, category, generated_at) · batch depuis `longBusinessSummary` · carte pédagogique sur page stock |
| AI Overview toggle FR/EN | 🔜 | EN par défaut · toggle FR dans profile |
| LLM5 routing par complexité | ⬜ | Calls légers → modèle rapide |
| FE1 ArticleDrawer lien direct | ⬜ | `window.open(url)` si pas de `dumb_en/dumb_fr` |
| Stocker `week_num` dans les articles JSONB | ⬜ | Calculer `(day-1)//7+1` au moment du merge dans `articles` — évite de recalculer depuis `datetime` à chaque fois (checkpoint, scripts) |
| DUMB FR prod cron | ⬜ | Cron post-scrap qui traduit `dumb_en` → `dumb_fr` pour les tickers pinnés par des users `lang='fr'`. Priorité après les OW FR. Utilise `translation_service.py` (Azure). À grouper avec le post-scrap de `cold_start_translate_fr` ou sous un cron `translate_dumb_fr` dédié. |
| BUB détection en prod | ⬜ | Aujourd'hui les dots (dates) sont détectés uniquement en batch (yfinance). Le texte LLM peut être généré en prod au premier clic. À terme : détecter les dots en prod au chargement de la page stock (via yfinance live) pour que les nouveaux events apparaissent sans attendre le batch. |
| `--gen-lesser` prod trigger | ⬜ | Quand un ticker est visité ou pinné, vérifier s'il a des W en `less_10_articles` avec P2 complet et déclencher gen `done_less_10` en background. Nécessite de remplir `ticker_stats` (view_count) au passage en prod. |
| `scrap_budget` reset en DB | ✅ | Reset global au début de chaque run (`breaks_weekly` + `breaks_monthly`). Les rows repassent à NULL et sont retentées — re-marquées `scrap_budget` si le budget P2 est à nouveau épuisé ce run. |
| Rétro `status` breaks W/M | ✅ | Migré : 3024 W + 2752 M `NULL` → `done_more_10` · `no_processed` → `no_articles_row` en DB et dans tous les py. |
