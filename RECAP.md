# StockVibe — Recap

## Règles architecture & nommage

**Source de données — règle absolue :**
Toute génération IA (OW, BUB, Breaks, EOD Digest) doit lire depuis `article_archive` (merge Finn+Yahoo).
Ne jamais appeler Finnhub ou Yahoo directement pour alimenter le LLM — les crons maintiennent `article_archive` à jour.
Si une feature bypass `article_archive` c'est un bug architectural à corriger.

**Nommage — toujours utiliser les termes suivants :**
| Terme | Signifie |
|---|---|
| OW | AI Overview (24H / 7J) — `overview_service.py` |
| DUMB / abrutis | Articles brutalised (résumé 2-3 phrases par article) |
| BUB / bubble | Event cause sur un dot du chart — `bubble_service.py` |
| Breaks | Résumés W1/W2/W3/W4 (1M) ou mensuels (6M/1Y) |
| EOD Digest | Digest end-of-day — page my-news + email 18h |
| article_archive | DB mergée Finn+Yahoo — source de vérité pour le LLM |

## Stack

| Layer | Tech ||---|---|
| Frontend | Next.js 15 · React 19 · TypeScript · Tailwind v4 |
| Backend | FastAPI (Python) · uvicorn · port 8000 |
| Auth | Supabase (`@supabase/supabase-js` singleton browser) · email/password + Google + Discord OAuth |
| Fonts | Inter (UI) · Space Mono (tickers/numbers) |
| Charts | Chart.js · react-chartjs-2 |
| Données bours. | yfinance 1.4.1 + curl_cffi (bypass Yahoo rate limit) |
| News | yfinance `get_news()` → Groq llama-3.3-70b → cache Supabase |
| Taux de change | Frankfurter API (`api.frankfurter.dev/v1`) — gratuit, taux BCE |
| Events chart | Finnhub company-news (free, 1 an d'historique) + Groq cause IA |
| Email digest | Resend (3 000 emails/mois gratuit) — clé configurée, sandbox actif |
| Logos | Logo.dev free (500k req/mois, attribution requise) — domaine depuis yfinance `t.info["website"]`, stocké dans `stock_news_cache.website` · `NEXT_PUBLIC_LOGODEV_TOKEN` dans `frontend/.env.local` |
| Cron | APScheduler intégré dans FastAPI — digest gen 18h00 · email 18h30 · cold_start FR 19h · re-enrich 22h30 · weekly lun 3h30 · monthly 1er 4h · yearly 1er jan 5h · **yahoo */6h** (tous pinnés ≥3, merge article_archive) · **finnhub */12h** (current month, merge) · **hourly_news_refresh :15** (pinned + visités récemment avec no_24h_news) |
| Git | github.com/andrey-243/Actionfun — repo privé |
| Déploiement | local → Railway/Render (backend) · Vercel (frontend) · Supabase (DB) |

## DA

Theme **dark earthy** — bg `#141412` · surfaces `#1E1D1B` · accent vert menthe `#4CAF82` · texte crème `#E8E5DF`.
Vert `#4CAF82` / rouge `#E05C4B` pour les variations de cours.

**Navbar** — frosted glass `rgba(14,14,12,0.45)` + `blur(22px)` · hauteur 66px · logo chien SVG (tracé crème, rotation anti-horaire 60s) dans container menthe · `NavBrand` + `BackButton` + `Avatar` (exported) réutilisables sur toutes les pages · avatar OAuth rond (Google/Discord) · pill "Mes news" accent (homepage uniquement) · pages secondaires (stock, my-news) : avatar seulement ou rien.

## Lancer le projet

```bash
# Backend
cd backend
source .venv/bin/activate
uvicorn main:app --reload --port 8000

# Frontend
cd frontend
npm run dev  # http://localhost:3000
```

## Structure

```
stock_app/
├── backend/
│   ├── main.py
│   ├── routers/
│   │   ├── stock.py       ← GET /api/stock/{ticker}?period=
│   │   ├── news.py        ← GET /api/news/{ticker}
│   │   └── search.py      ← GET /api/search
│   ├── services/
│   │   ├── yfinance_service.py   ← fast_info + t.info fallback
│   │   ├── overview_service.py       ← Yahoo news + Groq + cache Supabase
│   │   ├── bubble_service.py     ← Finnhub news par date + Groq cause (cache mémoire 1 semaine)
│   │   ├── digest_service.py     ← EOD digest : news du jour par ticker pinné + résumé Groq
│   │   ├── email_service.py      ← envoi email via Resend
│   │   └── llm_service.py
│   ├── routers/
│   │   └── events.py             ← GET /api/events/{ticker}?date=
│   ├── .env               ← toutes les clés (gitignored)
│   ├── .venv/             ← venv unique pour ce projet
│   └── requirements.txt
├── .gitignore
├── frontend/
│   ├── app/
│   │   ├── page.tsx              ← Home : search + recent + watchlist
│   │   ├── stock/[ticker]/
│   │   │   └── page.tsx          ← Chart + stats + news IA
│   │   ├── profile/
│   │   │   └── page.tsx          ← Préférences + compte
│   │   ├── components/
│   │   │   ├── Navbar.tsx        ← Navbar (home) + NavBrand + BackButton + HEADER_STYLE/INNER exports
│   │   │   ├── AuthModal.tsx     ← email/password + Google + Discord
│   │   │   ├── ArticleDrawer.tsx
│   │   │   ├── VideoModal.tsx
│   │   │   ├── EventDrawer.tsx   ← drawer clic sur dot chart (cause IA + articles Finnhub)
│   │   │   └── DogLoader.tsx     ← loading animation (sleeping dog SVG, 2 comètes, rotation 3D)
│   │   ├── my-news/
│   │   │   └── page.tsx          ← onglet digest EOD
│   │   └── lib/
│   │       ├── supabase.ts           ← createClient() null-safe
│   │       ├── useCurrencyPref.ts    ← localStorage + Supabase si connecté
│   │       └── useExchangeRate.ts    ← taux USD↔EUR (Frankfurter, cache mémoire)
│   ├── .env.local          ← NEXT_PUBLIC_SUPABASE_URL/ANON_KEY/API_URL — remplis et fonctionnels
│   └── package.json
├── CLAUDE.md
├── RECAP.md
└── ERRORS.md
```

## État par feature

| Feature | Statut | Notes |
|---|---|---|
| Home — search + recent + watchlist | ✅ | Supabase pinned_stocks branché |
| Auth email/password | ✅ | |
| Auth Google OAuth | ✅ | Configuré dans Supabase |
| Auth Discord OAuth | ✅ | Configuré dans Supabase |
| Page stock — prix + stats | ✅ | fast_info + t.info (name, PE, website) |
| Page stock — header | ✅ | Logo Logo.dev en haut à droite · toggle EUR/USD dans header · étoile pin près du prix · lien website |
| Page stock — chart Chart.js | ✅ | |
| Chart — sélecteur de période | ✅ | 24H / 1W / 1M / 6M / 1Y / 5Y (ALL supprimé, 5Y = interval 1wk) |
| Chart — % adaptatif | ✅ | 24H = change_pct yfinance · autres périodes = (last-first)/first calculé depuis chart data |
| Toggle USD ↔ EUR | ✅ | Dans header page stock · Prix, 52W High/Low, tooltip, axes Y |
| Préférence devise | ✅ | localStorage (non-connecté) · Supabase (connecté) |
| Page profile | ✅ | Devise + compte + sign out |
| News — fetch + drawer + vidéo | ✅ | |
| News — résumés IA | ✅ | Cascade FreeTheAI → Groq (voir section LLM) |
| Cache news Supabase | ✅ | Deux TTL indépendants : OW 24H = **3h** (Call 1) · OW 7J = 24h (Call 2). Cold start = 1 call lourd (DUMB+OW7J+OW24H). Refresh = Call 1 brutalise les nouveaux, stocke 7J depuis BRUTALIZE (LLM4 — évite Call 2). LLM2 : skip si hash IDs inchangé. Call 2 actif uniquement BATCH_MODE=1. TTL checks vérifient la colonne EN ou FR selon `LLM_LANG`. |
| **EN-first migration** | ✅ | `LLM_LANG=en` dans `.env`. Tous les services (overview, digest, bubble, period_articles) génèrent EN par défaut. `_build_response()` sert `ai_summary_en` en premier. Routers normalisent `summary`/`ai_summary` EN first. Reenrich étendu : cible aussi `ai_summary_en IS NULL` (pas seulement `llm_source='groq'`). Colonnes DB ajoutées : `daily_digest.summary_en` · `event_causes.cause_en`. État DB : stock_news_cache 256/269 EN · weekly 0/10 · monthly 0/2 · bubbles 0/812 (se refill au fur et à mesure). |
| Annotations chart (events) | ✅ | Dots peaks/valleys → EventDrawer Finnhub + cause IA. 24H/5Y sans dots. 1W=top 5, 1M=8, 6M/1Y=10 |
| Chart — vertical grid lines | ✅ | Tirets par période : 24H=horaire, 1W=jour, 1M=lundi, 6M/1Y=mois, 5Y=année |
| Lazy loading page stock | ✅ | Default 24H — seul /api/news charge au mount. Articles 1M+ et causes events : on-demand uniquement |
| Cascade LLM FreeTheAI→Groq | ✅ | gpt-4.1 → gpt-5.4-mini → Hapuppy gpt-4.1-mini → Groq. Circuit breakers indépendants : FreeTheAI (3 rounds 502/503/timeout → freeze 30min → probe auto) + Hapuppy (3 échecs → freeze 3h → probe auto). deepseek-v3.2 retiré (timeouts constants). `HAPUPPY_MIN_INTERVAL=0` (pas de throttle). Notif TG throttlée 30min par raison. |
| EOD digest — backend | ✅ | gen 18h00 (Yahoo cache stock_news_cache en primary, Finnhub+LLM seulement si cache vide) · email 18h30 · 2 jobs APScheduler séparés |
| EOD digest — email | ✅ | Resend — clé configurée, sandbox `onboarding@resend.dev` (passer à domaine custom en prod) |
| EOD digest — onglet "Mes news" | ✅ | Page /my-news : toggle Daily/Weekly **pill style vert** (même style que bouton "Mes news" navbar) · auto-refresh si digest > 3h (`?force=1` backend) · `generated_at` dans réponse digest · `condensed_at` dans réponse weekly · DigestCard : chevron conditionnel (articles seulement), logo cliquable → stock page, freshness par carte · WeeklyCard : source+date alignés à droite du titre · top_articles triées par date desc |
| Préférences notif (email on/off + par ticker) | ✅ | Dans /profile : toggle Daily (badge PREMIUM jaune) + toggle Weekly (badge PREMIUM vert accent) + chips par action · `notify_weekly` en DB |
| Batch DB filler (717 tickers) | 🔄 | Scrape : US ✅ · EU ✅ · ASIA 🔄. OW enrich : **EN par défaut** (`LLM_LANG=en`). US EN ✅ (256/269 `ai_summary_en`) · EU ⏳ · ASIA ⏳. BUB 6M+1Y US : 🔄 en cours. Fix batch : dates sans articles (Finnhub 0) → skip LLM + log + notif TG `⏭ Skipped (no news)`. Progress files nommés par phase+région+lang (isolation concurrence). **Séquence en cours** → voir ci-dessous. |

**Séquence batch — EN first (2026-06-01)**

| # | Phase | Région | Statut |
|---|---|---|---|
| 1 | Bubbles 6M+1Y | US | 🔄 en cours — 28/260 done EN · 59/260 done FR, 232 restants EN |
| 2 | Bubbles 6M+1Y | EU | ⏳ |
| 3 | Bubbles 6M+1Y | ASIA | ⏳ |
| 4 | Monthly summaries (breaks) | US+EU+ASIA | ⏳ après bubbles |
| — | OW enrich (ai_summary_en) | EU · ASIA | ⏭ skip — se remplit via cron 3h + reenrich 22h30 |
| Loading animation — DogLoader | ✅ | Sleeping dog SVG, 2 comètes sens inverse, rotation 3D lente. Actif sur newsLoading + periodLoading (page stock) + EventDrawer |
| DB articles — rotation & purge | ✅ | 5W max, monthly calendaire 1er du mois, yearly purge monthlies. Purge event_causes > 1 an dans cron lundi 3h. |
| Monthly on-demand (premium) | ✅ | `get_monthly_ondemand()` — rolling 4 weeklies → LLM, pas de cache persistant |
| Articles 1M/6M/1Y — sections par période | ✅ | 1M=semaines (skew detection), 6M/1Y=mois. Rendu avec résumé AI si dispo + articles compacts |
| Articles multi-source | ✅ | Dedup par headline à chaque fetch Finnhub. `extra_sources` stockés. Badges colorés + multi-CTA dans drawer |
| Reddit sentiment | ⬜ | À venir |
| Refresh proactif (cron) | ⬜ | Pour tickers populaires/pinnés |
| LLM source tracking | ✅ | `llm_source` persisté dans stock_news_cache, article_week/month/year_summaries, event_causes, daily_digest |
| Re-enrich Groq→FreeTheAI | ✅ | Cron 22h30 — scan `llm_source='groq'` **+ `ai_summary_en IS NULL`** (news_cache + weekly + monthly), re-enrich FreeTheAI only (BATCH_MODE). Stop si quota atteint, retry J+1. |
| Cold start ticker rare | ✅ | Tickers hors top 250 : scrape + enrich progressif en background (1M → 6M → 1Y). EN first, FR traduit EOD si pas de clic FR. Banner `ColdStartBanner` avec poll 10s, steps visuels, reload articles au completion. `init-status` retourne `done:true` immédiatement si le ticker a des données (évite faux positif). |
| 24h vide — fallback 0 token | ✅ | Si aucun article dans les 24h (ticker peu couvert) : pas d'appel LLM, résumé composé de "Aucune actualité 24h" + perf benchmark du jour + résumé 7J. Badge `AUCUNE NEWS 24H` + note italique dans l'UI. `no_24h_news` persisté en DB. |

## LLM — Cascade & modèles

Tous les appels LLM passent par `services/llm_service.py` qui gère la cascade automatiquement.

**Cascade :**
```
FreeTheAI: bbl/gpt-4.1      → primary
  ✗ 502/503/timeout          → try secondary
  ✗ 429 (RPM)                → break, skip FreeTheAI 6s
  ✗ 403/checkin              → break, skip FreeTheAI jusqu'à minuit UTC
  ✗ daily_limit              → break, skip FreeTheAI jusqu'à minuit UTC

FreeTheAI: bbl/gpt-5.4-mini → secondary (seulement si primary = 502/503/timeout)
  mêmes règles d'erreur
  → 3 rounds échoués consécutifs (502/503/timeout) → circuit breaker 30min → probe auto
  → reset sur succès · 429/checkin/daily ne comptent pas comme "round échoué"

Hapuppy: gpt-4.1-mini       → circuit breaker : 3 échecs → freeze 3h → probe auto
                               HAPUPPY_MIN_INTERVAL=0 (pas de throttle — 100 RPM Hapuppy)

Groq: voir tableau ci-dessous → fallback final
                                ⚠️ TODO PROD : désactiver en BATCH_MODE=1 pour préserver le quota site
```
deepseek-v3.2 retiré — timeouts constants (endpoint deprecated chez Hapuppy).
AgentRouter (`agentrouter.org`) retiré — clé `unauthorized_client`.

**Comportement erreurs FreeTheAI :**
- **403 / checkin manquant** : faire `/checkin` sur le Discord FreeTheAI pour débloquer
- **Daily limit** (`"daily successful request limit exceeded"`) : reset automatique à minuit UTC
- **429 (RPM)** : skip vers étape suivante, retry FreeTheAI après 6s (10 RPM)
- **3 rounds échoués consécutifs** (502/503/timeout sur primary+secondary) : circuit breaker 30min, probe auto après

**Comportement erreurs Hapuppy :**
- **3 échecs consécutifs** : freeze 3h, probe auto → reset si succès
- **Crédits épuisés** : notif TG `hapuppy_down` throttlée 30min

**Notifs Telegram — trail complet de la cascade :**
```
🟠 Hapuppy `gpt-4.1-mini` — down (502/timeout)

FreeTheAI `bbl/gpt-4.1` : ✗ 503
FreeTheAI `bbl/gpt-5.4-mini` : ✗ 503
Hapuppy `gpt-4.1-mini` : ✗ timeout/connexion
Groq `llama-3.1-8b-instant` : ✓
```
- Une notif par raison primaire, throttle 30min — `_send_cascade_notif(reason, trail)`
- Trail buildé au fil de la cascade dans `llm_call`, envoyé quand un non-primary répond (✓ ou ✗)
- Si tous les LLM échouent → notif `🔴 Tous les LLM en échec` avec trail complet

**FreeTheAI — quotas journaliers (rôle Discord) :**
| Rôle | Requêtes/jour | Images/jour |
|---|---|---|
| Seems Legit | 400–500 | 30 |
| Sans rôle | 250 | 5 |
| Suspicious | 50 | 2 |

⚠️ Rôle actuel inconnu — vérifier sur le Discord FreeTheAI. Un batch complet (717 tickers × 2 calls) = ~1400 calls, **dépasse tous les tiers**. Prévoir plusieurs nuits ou alterner avec Groq.

**Conso réelle — nuit 29-30 mai 2026 (261 tickers US enrichis) :**

| Provider | Modèle | Requests | Tokens input | Tokens output | Cache hit | Coût |
|---|---|---|---|---|---|---|
| FreeTheAI | `bbl/gpt-5.4-mini` | ~522 | **51.9M** | 269K | n/a | $0 |
| Groq | `llama-4-scout-17b` | 200 | 495.7K | 221.3K | 0% | $0 |
| Groq | `llama-3.3-70b-versatile` | 139 | 83.1K | 29.4K | 0% | $0 |
| Groq | `llama-3.1-8b-instant` | 107 | 39.5K | 20K | 0% | $0 |
| **Total** | | **~968** | **52.5M** | **540K** | | **$0** |

Notes :
- FreeTheAI avg **99K tokens input / call** (varie 24K→151K selon volume articles du ticker)
- Cache hit = 0% sur tous les calls batch — chaque ticker envoie des articles uniques, aucune réutilisation possible
- Groq fallback = ~446 calls sur les tickers où FreeTheAI avait atteint son quota RPM/daily
- Données loggées dans `~/llm-stack/token_usage.jsonl` (dashboard [localhost:5555](http://localhost:5555))

**Mapping modèles par usage :**

| Usage | Contexte | FreeTheAI/Hapuppy | Groq fallback |
|---|---|---|---|
| BRUTALIZE (30 articles → résumé 7J) | site + batch | `bbl/gpt-4.1` | `llama-4-scout-17b` ¹ |
| SUMMARY_24H | site + batch | `bbl/gpt-4.1` | `llama-3.1-8b-instant` ¹ |
| EVENT_CAUSE (dot chart) | site | `bbl/gpt-4.1` | `llama-3.1-8b-instant` |
| DIGEST EOD | site | — (lit stock_news_cache, LLM seulement si cache vide) | `llama-4-scout-17b` (fallback rare) |
| WEEKLY / MONTHLY condensation | site + batch | `bbl/gpt-4.1` | `llama-3.3-70b-versatile` ¹ |
| TRADUCTION EN→FR | batch + site | `bbl/gpt-5.4-mini` (trad légère) | Azure → Yandex (TODO) → FreeTheAI `gpt-5.4-mini` → Hapuppy `gpt-4.1-mini` → Groq `llama-3.1-8b-instant` |

¹ Groq désactivé quand `BATCH_MODE=1` — le batch échoue proprement sur le ticker et skip.

**Règle invariante traduction :** EN est toujours généré en premier par le LLM principal. FR = traduction de l'EN uniquement, jamais généré directement. Si tout le waterfall de traduction échoue → EN conservé en DB, FR vide, retry au prochain batch. On ne peut pas avoir `FR filled + EN empty`.

**`translation_service.py` — waterfall :**
```
Azure Translator (batch natif 25 items/appel, ~200ms)
  → Yandex (TODO — clés configurées, pas encore intégré)
  → FreeTheAI bbl/gpt-5.4-mini (circuit breaker partagé avec llm_service)
  → Hapuppy gpt-4.1-mini (circuit breaker partagé avec llm_service)
  → Groq llama-3.1-8b-instant
```
- Circuit breakers FreeTheAI + Hapuppy partagés avec `llm_service` (même état en mémoire)
- Trail + notif TG throttlée 30min (même style que cascade LLM) — notif si Azure down, provider fallback utilisé, ou tous down
- Azure 2M chars/mois free tier — OW non inclus dans batch (trop volatile, refresh 3h)

**FreeTheAI — modèles actifs (testés 29/05/2026) :**

| Modèle | JSON propre | Latence | Notes |
|---|---|---|---|
| `bbl/gpt-4.1` | ✅ | ~16s | Meilleure qualité analytique |
| `bbl/gpt-5.4-mini` | ✅ | ~19s | Qualité proche, bon fallback |
| `bbl/gemini-2.5-flash` | ❌ | ~17s | Wrap markdown, inutilisable pour JSON |
| `wsf/swe-1.6` | ❌ | ~5s | Wrap markdown, résumés courts |

**Groq — limites free tier :**

| Modèle | TPM | TPD | Usage |
|---|---|---|---|
| `llama-4-scout-17b` | 30K | 500K | Calls lourds |
| `llama-3.1-8b-instant` | 6K | 500K | Calls légers/fréquents |
| `llama-3.3-70b-versatile` | 12K | 100K | Condensations crons (rares) |

⚠️ **TODO PROD** : réactiver la restriction `BATCH_MODE=1` dans `llm_service.py` pour bloquer Groq en batch et préserver le quota site. Actuellement ouvert (pas encore de version live).

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

- Les appels StockVibe (FreeTheAI/Groq/Hapuppy) passent **directement via httpx**, pas par le proxy RTK
- `_log_usage()` écrit dans le même format que RTK → Claude Code + StockVibe agrégés dans le même dashboard
- Noms de modèles loggués sans prefix (`bbl/gpt-5.4-mini`, `llama-4-scout-17b`...) → normalisés automatiquement par le dashboard via `_ALIAS_MAP`
- Champs loggués : `ts`, `model`, `tokens_in`, `tokens_in_cached` (0 en batch), `tokens_in_uncached`, `tokens_out`, `total_tokens`, `cost`

**Vars d'env requises :**
```
GROQ_API_KEY=...
FREETHEAI_API_KEY=sta_...          # /checkin Discord chaque jour UTC
TELEGRAM_BOT_TOKEN=...             # @mcorp1290_bot
TELEGRAM_CHAT_ID=...               # notifs cascade LLM
AZURE_TRANSLATOR_KEY=...           # F0 free tier 2M chars/mois (traduction EN→FR)
AZURE_TRANSLATOR_REGION=...        # ex: eastus
YANDEX_API_KEY_ID=...              # Yandex Translate — ID de la clé IAM
YANDEX_SECRET_KEY=...              # Yandex Translate — clé secrète (configuré, pas encore intégré dans translation_service.py)
```

## Optimisations — État d'implémentation

### Fait ✅

| # | Décision | Fichier |
|---|---|---|
| **article_archive pipeline** | Yahoo */6h + Finnhub */12h → merge dans `article_archive`. `_get_or_fetch_month` merge au lieu d'écraser. `_accumulate_yahoo` pour tous les pinnés (plus filtre source='yahoo'). `run_finnhub_refresh()` nouveau cron. | `period_articles_service.py`, `main.py` |
| **DB2** | `daily_digest` ne stocke plus `articles` — lu depuis `stock_news_cache.news_raw` via fallback router | `digest_service.py` |
| **DB3** | `CREATE INDEX IF NOT EXISTS idx_article_week_summaries_ticker_date ON article_week_summaries (ticker, year_month DESC, week_num DESC)` — à exécuter en SQL Editor Supabase | — |
| **DB4** | Purge `event_causes > 1 an` dans cron Monday 3h (`_purge_old_digests`) | `main.py` |
| **LLM2** | `_articles_hash()` — skip brutalise si IDs inchangés. Stocke `articles_hash` dans `stock_news_cache` | `overview_service.py` |
| **LLM4** | BRUTALIZE retourne déjà `summary` (7J) → stocké directement comme `ai_summary` + `ow7d_cached_at`. Call 2 (`_call2_ow7d`) actif uniquement en `BATCH_MODE=1` | `overview_service.py` |
| **Weekly Pool 1** | `_maybe_regen_weekly()` dans cron hourly — Pool 1 (tickers `no_24h_news`) | `overview_service.py` |
| **Weekly Pool 2** | `run_weekly_condensation` (lundi 3h30) génère semaine courante pour tous les pinnés sans summary via `get_weekly_ondemand()` | `period_articles_service.py` |

### À faire — sources articles ⬜

| Feature | Situation actuelle | Cible |
|---|---|---|
| **OW** (`overview_service._fetch_news`) | ✅ `article_archive` 7 derniers jours (merge Finn+Yahoo) — prod + batch | — |
| **BUB** (`bubble_service.get_event_news`) | ✅ `article_archive` fenêtre T-3→T+3 (merge Finn+Yahoo) — batch + prod | — |

Toutes les features lisent maintenant depuis `article_archive`. EOD Digest et Breaks l'étaient déjà.

### Post-beta ⬜

| # | Décision | Notes |
|---|---|---|
| LLM5 | Routing par complexité | Calls légers → modèle rapide, calls lourds → modèle principal |
| LLM6 | ~~Dedup cross-tickers~~ | Non pertinent : `brutalised` est ticker-spécifique. LLM2 couvre déjà l'intra-ticker. |
| FE1 | `ArticleDrawer` → lien direct si pas de `brutalised` | `window.open(url)` au lieu du drawer si pas d'analyse IA |

### Weekly regen — flow implémenté

**Pool 1 (cron 1h)** : tickers `no_24h_news=True` → si news trouvée → `get_news()` (Call 1) → `_maybe_regen_weekly()` → `get_weekly_ondemand()`. ✅

**Pool 2 (lundi 3h30)** : `run_weekly_condensation` → condense W-1 (article_archive) PUIS itère tous pinnés (min_users=1) → `get_weekly_ondemand()` pour ceux sans weekly semaine courante. ✅

**Monthly** : `article_week_summaries` accumule W1→W4. Cron 1er du mois → monthly → purge weeklies du mois passé. ✅

### Frontend

| # | Décision | Notes |
|---|---|---|
| FE1 | `ArticleDrawer` → lien direct si pas de `brutalised` | Articles sans AI analysis → `window.open(url)` au lieu du drawer. Libère de l'espace DB |

## Post-beta — Roadmap

| Feature | Priorité | Notes |
|---|---|---|
| Descriptions intelligentes des tickers | 🔜 | Voir détail ci-dessous |
| AI Overview toggle FR/EN | 🔜 | EN affiché par défaut. Toggle FR dans profile : post-beta |

### Descriptions intelligentes des tickers (post-beta)

Après la beta, processer toutes les descriptions yfinance (`t.info["longBusinessSummary"]`) via Groq pour générer une fiche pédagogique par ticker.

**Objectif :** quand un user cherche "DJI", "JPM", "BRK-B" etc., lui expliquer en français ce que c'est et ce que ça implique en tant qu'investisseur.

**Exemples :**
- `DJI` → "Le Dow Jones Industrial Average est un indice des 30 plus grandes entreprises américaines. Investir dessus c'est parier sur l'économie US au sens large..."
- `JPM` → "JPMorgan Chase est la plus grande banque américaine par actifs. Très sensible aux taux d'intérêt et aux cycles de crédit..."

**Architecture envisagée :**
- Table Supabase `ticker_descriptions` (ticker PK, description_fr TEXT, category TEXT, generated_at)
- Cron batch : parcourir les tickers pinnés + les plus recherchés → appel Groq sur `longBusinessSummary` → stocker
- Affichage : carte pédagogique sur la page stock, sous le header (collapsible)
- Prompt : demander catégorie (action, ETF, indice, REIT...) + résumé pédagogique + implications investisseur en 3-5 phrases

## Supabase — Tables actives

```
stock_news_cache        — cache news IA Yahoo (ticker PK, news_raw, ai_summary, ai_summary_24h, **ai_summary_en**, **ai_summary_24h_en**, ai_sentiment, ai_sentiment_24h, cached_at, ow7d_cached_at, website, generated_by_en, generated_by_fr, no_24h_news, articles_hash) — TTL **3h** (24H) / 24h (7J). EN served first.
user_preferences        — préférences (user_id PK, currency, notify_email, notify_weekly, digest_tickers text[])
pinned_stocks           — watchlist (user_id, ticker, company_name, pinned_at) — unique(user_id, ticker)
daily_digest            — digest EOD (ticker, date, summary, **summary_en**, sentiment) — purge auto 30J
event_causes            — causes AI événements chart (ticker, date PK, articles, cause, **cause_en**) — permanent
article_archive         — raw articles (ticker, year_month PK, articles JSONB, source: "finnhub"|"yahoo"|"mixed") — mois actif refresh 12h, passés permanent
article_week_summaries  — résumé AI + top 10 articles par semaine (ticker, year_month, week_num PK, ai_summary, top_articles)
article_month_summaries — résumé AI par mois (ticker, year_month PK, ai_summary) — généré depuis weekly summaries
article_year_summaries  — résumé AI par année (ticker, year PK, ai_summary) — généré depuis monthly summaries
ticker_init_status      — cold start tickers rares (ticker PK, phase TEXT, status TEXT, pending_fr BOOL, started_at, completed_at, error TEXT, updated_at)
```

### Cold start — phases & timings

Pour les tickers hors top 250 (jamais scrappés), le premier clic déclenche un cold start progressif en background :

| Phase | Contenu | ETA estimé | Banner |
|---|---|---|---|
| `1m` | Scrape Finnhub 1M + weekly condensation | ~2 min | "Chargement du dernier mois de données..." |
| `6m` | Scrape Finnhub 6M + weeklies + monthlies | ~7 min | "Chargement des 6 derniers mois..." |
| `1y` | Scrape Finnhub 1Y + weeklies + monthlies + yearly | ~15 min | "Chargement de l'année complète..." |

- Scrape toujours en **EN** (plus rapide, pas de coût translation au cold start)
- Si user FR : contenu EN affiché pendant cold start, flag `pending_fr = true`
- Cron EOD : traduit les tickers `pending_fr` si aucun clic FR depuis le cold start
- Si user FR clique → translation immédiate (prioritaire sur le cron)
- Chaque phase complète → banner update automatique (poll 10s frontend) → phase suivante auto

RLS activé sur toutes les tables. `service_role` a GRANT ALL explicite.
`authenticated` + `anon` ont GRANT SELECT/INSERT/UPDATE/DELETE explicite (requis en plus des RLS policies).

## Tickers — Coverage DB

| Région | Count | Source articles | Historique |
|---|---|---|---|
| US (S&P500 + NASDAQ) | 260 | Finnhub 12 mois + Yahoo | 12 mois complets |
| EU (Paris/Frankfurt/LSE/Amsterdam/Zurich...) | 227 | Yahoo principalement | Accumulation daily |
| Asie (Japon/HK/Corée/Inde/Taiwan/Australie...) | 230 | Yahoo principalement | Accumulation daily |
| **Total** | **717** | | |

Fichier de référence : `backend/data/tickers.py`

**Batch scraper** : `backend/scripts/batch_fill_db.py`
```bash
# Scraping articles seulement (pas de LLM)
python scripts/batch_fill_db.py --phase scrape --region US

# OW + DUMB (Call 2 seulement en BATCH_MODE — skip OW 24H)
python scripts/batch_fill_db.py --phase enrich --region US

# Bubbles 6M+1Y (event_causes pré-générées — cause_en si LLM_LANG=en)
python scripts/batch_fill_db.py --phase bubbles --region US

# Tout (scrape + enrich)
python scripts/batch_fill_db.py --phase all --region US

# Traduction BUBBLES EN→FR (event_causes.cause_en → cause, skip si cause déjà présent)
python scripts/batch_fill_db.py --phase translate-fr --region US

# Traduction DUMB EN→FR (news_raw[].brutalised → brutalised_fr, Azure batch 25/appel)
python scripts/batch_fill_db.py --phase translate-fr-dumb --region US

# Reprend automatiquement — fichier auto-nommé par phase+region[+lang]
# scrape           → batch_progress_scrape_{region}.json
# enrich           → batch_progress_enrich_{region}_{lang}.json
# bubbles          → batch_progress_bubbles_{region}_{lang}.json
# translate-fr     → batch_progress_translate_fr_{region}.json
# translate-fr-dumb → batch_progress_translate_fr_dumb_{region}.json
```

**Progress files** — `backend/scripts/batch_progress_{phase}_{region}[_{lang}].json`
- Un fichier par combo phase+region+lang → pas de conflit si 2 process tournent en parallèle
- Créé automatiquement au premier ticker complété si inexistant
- `scrape` : pas de lang dans le nom · `enrich` et `bubbles` : suffixe `_en` ou `_fr` · `translate-fr*` : pas de lang (toujours → FR)
- Write direct dans Supabase à chaque event/ticker — le progress file sert uniquement à savoir où reprendre

**Sync progress depuis Supabase** : `backend/scripts/sync_progress.py`
```bash
python scripts/sync_progress.py           # recrée tous les fichiers progress depuis la DB
python scripts/sync_progress.py --dry-run # affiche les counts sans écrire
```
Utile après une interruption ou si les fichiers locaux sont désynchronisés. Threshold bubbles_done = 8 causes non-null (min observé ~11 sur les tickers US FR)..

**Script one-shot websites** : `backend/scripts/populate_websites.py`
```bash
python scripts/populate_websites.py            # tous les tickers en stock_news_cache
python scripts/populate_websites.py --ticker AMZN  # un seul ticker
```
À relancer après chaque phase enrich si besoin de rattraper des tickers.

## Articles par période

| Période | OW | DUMB (abrutis) | BUB | Breaks |
|---|---|---|---|---|
| 1D | ✅ `stock_news_cache` (summary_24h) | ✅ `news_raw.brutalised` | — | — |
| 1W | ✅ `stock_news_cache` (summary 7J) | ✅ `news_raw.brutalised` (= 1D) | on-demand | — |
| 1M | — | — | on-demand | Weekly breaks `article_week_summaries` (W1/W2/W3/W4) |
| 6M | — | — | pre-cache `event_causes` | Monthly breaks `article_month_summaries` |
| 1Y | — | — | pre-cache `event_causes` | Monthly breaks `article_month_summaries` |
| 5Y | — | — | — | `article_year_summaries` |

**Nomenclature :**
- **OW** = AI Overview (résumé + sentiment) — `overview_service.py`
- **DUMB / abrutis** = résumé AI 2-3 phrases par article — stocké dans `news_raw.brutalised`
- **BUB / bubbles** = causes AI des peaks/valleys sur le chart — `bubble_service.py` · `event_causes` table
- **Breaks** = résumés de période (weekly/monthly) séparant les sections d'articles — `break_view_resume.py`

**Source articles par période :**
| Période | Source |
|---|---|
| 1D/1W | Yahoo (`stock_news_cache.news_raw`) |
| 1M/6M/1Y | Finnhub (`article_archive`) · sections W1-W4 ou mois. Si skewed → merged + warning |
| 5Y | `article_year_summaries` |

**Problème connu — Finnhub date clustering :**
Finnhub indexe les articles avec sa date de crawl, pas la date de publication. Pour ~35% des mois, 80%+ des articles tombent en W4. Détection automatique côté backend : si skewed → section merged "WX→WY · pas de récap hebdomadaire disponible". Fiable par mois (year_month défini par nous), pas par semaine.

**Semaines fixes intra-mois :**
- W1 = jours 1–7 · W2 = jours 8–14 · W3 = jours 15–21 · W4 = jours 22–fin du mois

**Script condensation manuelle :** `backend/scripts/break_view_resume.py`
```bash
python scripts/break_view_resume.py AAPL --months 6  # weeklies + monthlies
python scripts/break_view_resume.py AAPL --weekly    # weeklies seulement
```
⚠ Inutilisable pour générer des weekly summaries fiables tant que Finnhub clustère les dates en W4.

## Articles — Doublons & Multi-source

Finnhub indexe le même article depuis plusieurs outlets (Yahoo proxy + source originale). Contenu identique à 100%, seule la source diffère.

**Architecture retenue :**
- Finnhub raw → `_dedup_by_headline()` → stockage propre en DB avec `extra_sources: [{name, url}]`
- Pas de doublons en DB — juste l'article le meilleur (URL directe > proxy, summary le plus long) + liste légère des sources alternatives
- `_get_or_fetch_month()` appelle `_dedup_by_headline()` avant chaque upsert → permanent, automatique

**Affichage multi-source :**
- `_normalize_finnhub()` passe `extra_sources` dans chaque article retourné à l'UI
- Badges colorés par source (Yahoo=violet, Bloomberg=noir, Reuters=orange...) dans `NewsCard` et `CompactArticleRow`
- `ArticleDrawer` : un bouton "Read" par source disponible (principal coloré, alternatives neutres)
- **Scope** : uniquement pour 1M/6M/1Y (article_archive Finnhub). 24H/1W utilise `stock_news_cache` (Yahoo) — pas de `extra_sources`, pas de badges multi-source sur ce path.

**Script refetch :** `backend/scripts/refetch_finnhub.py`
```bash
python scripts/refetch_finnhub.py --months 3   # re-fetch 3 derniers mois avec dedup+merge
python scripts/refetch_finnhub.py --dry-run    # liste les rows sans fetch
```
Notifs Telegram tous les 50 rows + bilan final.

## Rotation des données — Architecture

```
article_archive (raw bodies)
  → supprimé après weekly condensation ✅

article_week_summaries
  → max 5 weeklies stockés par ticker (purge auto à chaque nouveau weekly)
  → supprimé le 1er du mois suivant (après monthly calendaire généré)

article_month_summaries (calendaire : S1+S2+S3+S4 dans l'ordre)
  → gardé 12 mois — source pour vues 6M / 1Y (premium)
  → supprimé le 1er janvier après génération du yearly

article_year_summaries
  → permanent
```

**3 types de monthly :**
| Type | Source | Usage | TTL |
|---|---|---|---|
| On-demand (rolling) | 4 derniers weeklies (peu importe l'ordre) | UI "1M" — premium CTA | Pas de cache, généré à la demande |
| Calendaire | S1+S2+S3+S4 du mois écoulé | Source pour 6M / 1Y | 12 mois glissants |
| Yearly input | 12 monthlies calendaires | Yearly summary | Purgé après yearly |

**Chaîne de condensation :**
```
Lundi 3h    : weekly summary généré → purge si > 5 weeklies
1er du mois : monthly calendaire généré (S1+S2+S3+S4) → purge weeklies du mois passé
1er janvier : yearly généré (12 monthly calendaires) → purge ces 12 monthlies
```

**Monthly on-demand (premium) :**
- `get_monthly_ondemand(ticker)` → dernier 4 weeklies → LLM → texte retourné (pas de cache persistant)
- 6M on-demand → 6 derniers monthly calendaires → LLM
- 1Y on-demand → 12 derniers monthly calendaires → LLM

**Source par ticker — détection automatique :**
- Finnhub retourne des articles → `source: "finnhub"` → bootstrap immédiat 12 mois
- Finnhub retourne 0 articles (hors US, small cap) → `source: "yahoo"` → accumulation daily via cron, historique construit sur 12 mois

**Cron Yahoo */6h** (tous pinnés ≥ 3 users) :
- Fetch `get_news(count=100)` → `_dedup_by_headline` merge avec existant → source `"mixed"` si Finnhub déjà présent

**Cron Finnhub */12h** (mois courant, tous pinnés ≥ 3 users) :
- `_get_or_fetch_month()` → merge Finnhub + existant Yahoo → dedup headline → `ACTIVE_MONTH_TTL_HOURS = 12`

**Cold start non-US :** 12 mois de run nécessaires avant d'avoir une année complète. Inévitable sans API payante.

**Limites :** ~1 Groq call/ticker/semaine. 50 tickers → 50 calls/semaine (free tier OK). Condensation = 0 nouveaux appels Finnhub/Yahoo (tout depuis `article_archive`).

## Resend — Email

- Sandbox : `RESEND_FROM_EMAIL=onboarding@resend.dev` → emails envoyés uniquement vers l'adresse du compte Resend
- Production : vérifier un domaine dans Resend dashboard → changer `RESEND_FROM_EMAIL=digest@tondomaine.com`
- **2 jobs séparés** : `eod_digest_generation` 18h00 (génère pour tous les tickers pinnés) · `eod_digest_email` 18h30 (lit `daily_digest` du jour → envoie aux users `notify_email=true`)
- **Fallback daily** : Finnhub today → si vide → `stock_news_cache.ai_summary_24h` (Yahoo) → si absent → `get_news()` déclenché à la volée
- Weekly email : à venir (lundi après condensation)

## News — Architecture

- **Source** : `yfinance.Ticker(ticker).get_news(count=100)` · filtre summaries < 80 chars
- **Filtre pertinence** : le LLM marque les articles hors-sujet "SKIP" → exclus du cache avant stockage. Si **tous** sont SKIP (ticker peu couvert) → fallback articles bruts (sans abrutis) plutôt qu'afficher 0 news.
- **2 TTL indépendants — `overview_service.py` :**
  - **Call 1 — OW 24H + OW 7J (TTL 3h)** : fetch Yahoo → **LLM2** : skip si hash IDs inchangé (bump `cached_at`, 0 LLM) → brutalise nouveaux articles → OW 24H depuis abrutis <24h → **LLM4** : stocke aussi `ai_summary_en` (7J) depuis BRUTALIZE directement → `ow7d_cached_at = now`. Si `articles_24h` vide → fallback "No news 24h" + `_bench_context_line()` + OW 7J existant. TTL checks vérifient `ai_summary_24h_en` (EN) ou `ai_summary_24h` (FR) selon `LLM_LANG`.
  - **Call 2 — OW 7J (BATCH_MODE=1 uniquement)** : lit abrutis en DB → `_ow7d_from_abrutis()`. Inactif en mode site (Call 1 couvre déjà le 7J).
  - **Cold start (aucun cache)** : Call 1 seul → BRUTALIZE → stocke news_raw brutalised + OW 24H + OW 7J en un call.
- **AI Overview affiché selon la période** : 24H → `summary_24h` (+ badge `AUCUNE NEWS 24H` si `no_24h_news`) · 1W → `summary` (7J) · 1M+ → pas d'overview Yahoo
- **Cache logic** : `cached_at` = timestamp Call 1 · `ow7d_cached_at` = timestamp Call 2 — deux TTL indépendants dans `stock_news_cache`
- **Hourly refresh** (cron :15 chaque heure) : pinned tickers + tickers visités dans les 6h avec `no_24h_news=True` → fetch Yahoo sans LLM → si ≥1 article 24h trouvé → `get_news()` complet (Call 1)

## Events (dots chart) — Architecture

Quand l'user clique un dot → `EventDrawer` → `GET /api/events/{ticker}?date=&change_pct=`

**Contexte envoyé à Groq (cible) :**
```
Ticker : AAPL | Date : 2026-04-15 | Mouvement : ▼ -4.2%

Prix T-5 → T+3 (daily) :
  10 avr : $198.20 → $201.40 (+1.6%)
  ...
  15 avr : $203.10 → $194.60 (-4.2%)  ← événement
  16 avr : $194.60 → $193.20 (-0.7%)
  17 avr : $193.20 → $196.80 (+1.9%)

Benchmark (S&P 500) même période :
  15 avr : -0.8%  ← move spécifique au titre, pas macro

Headlines Finnhub T-3 → T+3 : [12 max]
```

**Benchmark par exchange (détection auto) :**
| Exchange | Index | Ticker yfinance |
|---|---|---|
| NMS / NYQ (US) | S&P 500 | `^GSPC` |
| PAR (Paris) | CAC 40 | `^FCHI` |
| GER / XETRA | DAX | `^GDAXI` |
| LSE (Londres) | FTSE 100 | `^FTSE` |
| Défaut / autre | Euro Stoxx 50 | `^STOXX50E` |

**Cache** : `event_causes` table — permanent (les causes historiques ne changent pas). Invalidation manuelle si besoin.

## Chart — Périodes & intervalles yfinance

| Bouton | period | interval | Dots events |
|---|---|---|---|
| 24H | 1d | 5m | aucun (trop bruité) |
| 1W | 5d | 1h | top 5 peaks/valleys |
| 1M | 1mo | 1d | top 8 peaks/valleys |
| 6M | 6mo | 1d | top 10 peaks/valleys |
| 1Y | 1y | 1d | top 10 peaks/valleys |
| ALL | max | 1mo | aucun (Finnhub = 1 an max) |

