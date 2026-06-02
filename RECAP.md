# StockVibe — Recap

---

## Architecture

### Règles & nommage

**Source de données — règle absolue :**
Toute génération IA (OW, BUB, Breaks, EOD Digest) doit lire depuis `articles_merged_finn_yahoo` (merge Finn+Yahoo).
Ne jamais appeler Finnhub ou Yahoo directement pour alimenter le LLM — les crons maintiennent `articles_merged_finn_yahoo` à jour.
Si une feature bypass `articles_merged_finn_yahoo` c'est un bug architectural à corriger.

**Nommage — toujours utiliser les termes suivants :**
| Terme | Signifie |
|---|---|
| OW | AI Overview (24H / 7J) — `overview_service.py` |
| DUMB | Résumé AI 2-3 phrases par article — champs `dumb_en`/`dumb_fr` dans les objets article |
| BUB / bubble | Event cause sur un dot du chart — `bubble_service.py` |
| Breaks | Résumés W1/W2/W3/W4 (1M) ou mensuels (6M/1Y) — `breakers_articles_service.py` |
| EOD Digest | Digest end-of-day — page my-news + email 18h |
| articles_merged_finn_yahoo | DB mergée Finn+Yahoo — source de vérité unique pour tous les articles |

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
| News | yfinance `get_news()` → Groq llama-3.3-70b → cache Supabase |
| Taux de change | Frankfurter API (`api.frankfurter.dev/v1`) — gratuit, taux BCE |
| Events chart | Finnhub company-news (free, 1 an d'historique) + Groq cause IA |
| Email digest | Resend (3 000 emails/mois gratuit) — clé configurée, sandbox actif |
| Logos | Logo.dev free (500k req/mois, attribution requise) — domaine depuis yfinance `t.info["website"]`, stocké dans `stock_news_cache.website` · `NEXT_PUBLIC_LOGODEV_TOKEN` dans `frontend/.env.local` |
| Cron | APScheduler intégré dans FastAPI — digest gen 18h00 · email 18h30 · cold_start FR 19h · re-enrich 22h30 · weekly lun 3h30 · monthly 1er 4h · yearly 1er jan 5h · **yahoo */6h** (tous pinnés ≥3, merge articles_merged_finn_yahoo + trigger OW si nouveaux articles) · **finnhub */12h** (current month, merge + trigger OW si nouveaux articles) · **hourly_news_refresh :15** (pinned + visités récemment avec no_24h_news → `force_24h=True`) · **purge DB** lun 3h (daily_digest >30J + event_causes >1 an). TG: tous les crons 1x/j envoient ▶ start + ✅ end avec durée. Yahoo/Finnhub: ✅ avec résumé par ticker (Yahoo: `AAPL +5 · AMZN +3` · Finnhub: `AAPL 45 · AMZN 23`). Hourly: TG uniquement si des tickers sortent du pool no_24h_news (`✅ News refresh — AAPL · AMZN sortis du pool`). |
| Git | github.com/andrey-243/Actionfun — repo privé |
| Déploiement | local → Railway/Render (backend) · Vercel (frontend) · Supabase (DB) |

---

### Design (DA)

Theme **dark earthy** — bg `#141412` · surfaces `#1E1D1B` · accent vert menthe `#4CAF82` · texte crème `#E8E5DF`.
Vert `#4CAF82` / rouge `#E05C4B` pour les variations de cours.

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

### Structure fichiers

```
stock_app/
├── backend/
│   ├── main.py
│   ├── routers/
│   │   ├── stock.py       ← GET /api/stock/{ticker}?period=
│   │   ├── news.py        ← GET /api/news/{ticker}
│   │   ├── search.py      ← GET /api/search
│   │   └── events.py      ← GET /api/events/{ticker}?date=
│   ├── services/
│   │   ├── yfinance_service.py
│   │   ├── overview_service.py
│   │   ├── bubble_service.py
│   │   ├── breakers_articles_service.py
│   │   ├── digest_service.py
│   │   ├── email_service.py
│   │   ├── llm_service.py
│   │   └── cold_start_service.py
│   ├── scripts/
│   │   ├── batch_fill_db.py
│   │   ├── break_view_resume.py
│   │   ├── refetch_finnhub.py
│   │   ├── sync_progress.py
│   │   └── migrate_dumb_to_archive.py
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
│   │       └── useExchangeRate.ts
│   ├── .env.local
│   └── package.json
├── CLAUDE.md
├── RECAP.md
└── ERRORS.md
```

---

### LLM — Cascade & modèles

Tous les appels LLM passent par `services/llm_service.py` qui gère la cascade automatiquement.

**Cascade :**
```
FreeTheAI: bbl/gpt-4.1      → primary
  ✗ 502/503/timeout          → try secondary
  ✗ 429 (RPM) ×3 consécutifs → freeze 3h (FREETHEAI_429_FREEZE_SECONDS=10800)
  ✗ 403/checkin              → break, skip jusqu'à minuit UTC
  ✗ daily_limit              → break, skip jusqu'à minuit UTC
  Concurrence : Semaphore(FREETHEAI_CONCURRENCY=1) non-bloquant — 1 seul appel simultané,
                les autres sautent directement à Hapuppy

FreeTheAI: bbl/gpt-5.4-mini → secondary (seulement si primary = 502/503/timeout)
  mêmes règles d'erreur
  → 3 rounds échoués consécutifs (502/503/timeout) → circuit breaker 30min → probe auto
  → reset sur succès · 429/checkin/daily ne comptent pas comme "round échoué"

Hapuppy: gpt-4.1-mini       → 3 retries avant de passer à Groq
                               429 quota : freeze ~4h (HAPUPPY_QUOTA_FREEZE_SECONDS=14400) — body keywords ("quota","credit","budget"...)
                               429 rate-limit : sleep 5s local + retry dans le même thread (pas de freeze global) — max 3 tentatives
                               timeout/502/connexion → backoff 1s/2s, retry
                               Concurrence : Semaphore(HAPUPPY_CONCURRENCY=3) — max 3 appels httpx simultanés

Groq: voir tableau ci-dessous → fallback final (uniquement si Hapuppy quota épuisé ou down)
                                ⚠️ TODO PROD : désactiver en BATCH_MODE=1 pour préserver le quota site
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
- **timeout/502/connexion** : backoff 1s/2s, 3 retries, puis Groq
- **429 quota épuisé** (body contient "quota"/"credit"/"budget"...) : freeze ~4h, notif TG `hapuppy_quota`
- **429 rate-limit** (body sans keyword quota) : sleep 5s local + retry Hapuppy dans le même thread — pas de freeze global, Groq non déclenché
- **Unfreeze manuel** : `POST /admin/hapuppy/unfreeze` — reset freeze dans le process FastAPI (⚠ n'affecte pas un batch standalone)

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
- Chaque notif TG porte un label source en footer : `📍 llm_service` · `📍 translation_service` · `📍 cron` · `📍 batch_fill_db`

**FreeTheAI — quotas journaliers (rôle Discord) :**
| Rôle | Requêtes/jour | Images/jour |
|---|---|---|
| Seems Legit | 400–500 | 30 |
| Sans rôle | 250 | 5 |
| Suspicious | 50 | 2 |

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
- Trail + notif TG throttlée 30min — notif si Azure down, provider fallback utilisé, ou tous down
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

---

### Supabase — Tables actives

```
stock_news_cache           — cache OW live (ticker PK, ai_summary, ai_summary_24h, ai_summary_en, ai_summary_24h_en,
                             ai_sentiment, ai_sentiment_24h, cached_at, ow7d_cached_at, website, generated_by_en,
                             generated_by_fr, no_24h_news, articles_hash, exchange) — TTL 3h (24H) / 24h (7J). EN served first.
                             Zéro articles stockés ici. `exchange` = code yfinance (NMS, NYQ, KSC…) — peuplé à la 1ère
                             visite via trigger dans `get_news_stale()`. One-shot initial : `scripts/populate_exchange.py`.
                             Lignes sans `cached_at` (exchange-only) traitées comme cache absent → appel bloquant.
user_preferences           — préférences (user_id PK, currency, notify_email, notify_weekly, digest_tickers text[])
pinned_stocks              — watchlist (user_id, ticker, company_name, pinned_at) — unique(user_id, ticker)
daily_digest               — digest EOD (ticker, date, summary, summary_en, sentiment) — purge auto 30J
event_causes               — causes AI événements chart (ticker, date PK, articles, cause, cause_en) — permanent
articles_merged_finn_yahoo — source de vérité articles (ticker, year_month PK, articles JSONB avec dumb_en/dumb_fr
                             par article, source: "finnhub"|"yahoo"|"mixed") — mois actif refresh 12h, passés permanent.
article_week_summaries     — résumé AI + top 10 articles par semaine (ticker, year_month, week_num PK, ai_summary, top_articles)
article_month_summaries    — résumé AI par mois (ticker, year_month PK, ai_summary)
article_year_summaries     — résumé AI par année (ticker, year PK, ai_summary)
ticker_init_status         — cold start tickers rares (ticker PK, phase TEXT, status TEXT, pending_fr BOOL,
                             started_at, completed_at, error TEXT, updated_at)
```

RLS activé sur toutes les tables. `service_role` a GRANT ALL explicite.
`authenticated` + `anon` ont GRANT SELECT/INSERT/UPDATE/DELETE explicite (requis en plus des RLS policies).

**Règle** : Toute nouvelle table nécessite `GRANT ... TO authenticated` en plus des RLS policies.

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
| 1D | `stock_news_cache` (summary_24h) | `articles_merged_finn_yahoo[].dumb_en` | — | — |
| 1W | `stock_news_cache` (summary 7J) | `articles_merged_finn_yahoo[].dumb_en` | on-demand | — |
| 1M | — | — | on-demand | Weekly breaks `article_week_summaries` (W1/W2/W3/W4) |
| 6M | — | — | pre-cache `event_causes` | Monthly breaks `article_month_summaries` |
| 1Y | — | — | pre-cache `event_causes` | Monthly breaks `article_month_summaries` |
| 5Y | — | — | — | `article_year_summaries` |

**Source articles par période :**
| Période | Source |
|---|---|
| 1D/1W | `articles_merged_finn_yahoo` (7 derniers jours) |
| 1M/6M/1Y | `articles_merged_finn_yahoo` · sections W1-W4 ou mois. Si skewed → merged + warning |
| 5Y | `article_year_summaries` |

**Semaines fixes intra-mois :** W1 = jours 1–7 · W2 = jours 8–14 · W3 = jours 15–21 · W4 = jours 22–fin du mois

**Problème connu — Finnhub date clustering :**
Finnhub indexe les articles avec sa date de crawl, pas la date de publication. Pour ~35% des mois, 80%+ des articles tombent en W4. Détection automatique côté backend : si skewed → section merged "WX→WY · pas de récap hebdomadaire disponible".

**Script condensation manuelle :** `backend/scripts/break_view_resume.py`
```bash
python scripts/break_view_resume.py AAPL --months 6  # weeklies + monthlies
python scripts/break_view_resume.py AAPL --weekly    # weeklies seulement
```
⚠ Inutilisable pour générer des weekly summaries fiables tant que Finnhub clustère les dates en W4.

---

### Articles — Doublons & Multi-source

Finnhub indexe le même article depuis plusieurs outlets. Contenu identique à 100%, seule la source diffère.

**Architecture :**
- Finnhub raw → `_dedup_by_headline()` → stockage propre en DB avec `extra_sources: [{name, url}]`
- `_get_or_fetch_month()` appelle `_dedup_by_headline()` avant chaque upsert

**Affichage multi-source :**
- Badges colorés par source (Yahoo=violet, Bloomberg=noir, Reuters=orange...) dans `NewsCard` et `CompactArticleRow`
- `ArticleDrawer` : un bouton "Read" par source disponible
- **Scope** : uniquement 1M/6M/1Y. 24H/1W utilise `stock_news_cache` (Yahoo) — pas de badges multi-source.

**Script refetch :** `backend/scripts/refetch_finnhub.py`
```bash
python scripts/refetch_finnhub.py --months 3   # re-fetch 3 derniers mois avec dedup+merge
python scripts/refetch_finnhub.py --dry-run    # liste les rows sans fetch
```

---

### Rotation des données

```
articles_merged_finn_yahoo (raw bodies)
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
| On-demand (rolling) | 4 derniers weeklies | UI "1M" — premium CTA | Pas de cache |
| Calendaire | S1+S2+S3+S4 du mois écoulé | Source pour 6M / 1Y | 12 mois glissants |
| Yearly input | 12 monthlies calendaires | Yearly summary | Purgé après yearly |

**Chaîne de condensation :**
```
Lundi 3h    : weekly summary → purge si > 5 weeklies
1er du mois : monthly calendaire (S1+S2+S3+S4) → purge weeklies du mois passé
1er janvier : yearly (12 monthly calendaires) → purge ces 12 monthlies
```

**Source par ticker :**
- Finnhub retourne des articles → `source: "finnhub"` → bootstrap immédiat 12 mois
- Finnhub retourne 0 articles → `source: "yahoo"` → accumulation daily via cron

**Cron Yahoo */6h** : fetch `get_news(count=100)` → `_dedup_by_headline` merge → `source: "mixed"` si Finnhub déjà présent → si nouveaux articles : `get_news(ticker, force_24h=True)` (dumb + OW 24H régénérés)

**Cron Finnhub */12h** : `_get_or_fetch_month()` → merge Finnhub + existant Yahoo → `ACTIVE_MONTH_TTL_HOURS = 12` → si nouveaux articles : `get_news(ticker, force_24h=True)`

---

### News — Architecture

- **Source** : `yfinance.Ticker(ticker).get_news(count=100)` · filtre summaries < 80 chars
- **Filtre pertinence** : le LLM marque les articles hors-sujet "SKIP" → exclus du cache. Si **tous** sont SKIP → fallback articles bruts sans abrutis.
- **2 TTL indépendants — `overview_service.py` :**
  - **Call 1 — OW 24H + OW 7J (TTL 3h)** : fetch Yahoo → LLM2 : skip si hash IDs inchangé → brutalise nouveaux articles → OW 24H depuis abrutis **depuis minuit UTC** → LLM4 : stocke aussi `ai_summary_en` (7J) depuis BRUTALIZE → `ow7d_cached_at = now`. Si `articles_24h` vide → fallback "No news 24h" + `_bench_context_line()` + OW 7J existant.
  - **Call 2 — OW 7J (BATCH_MODE=1 uniquement)** : lit abrutis en DB → `_ow7d_from_abrutis()`. Inactif en mode site.
  - **Cold start** : Call 1 seul → BRUTALIZE → stocke dumb_en/dumb_fr + OW 24H + OW 7J en un call.
- **Stale-while-revalidate** : `GET /api/news/{ticker}` retourne le cache immédiatement (même stale) + flag `is_refreshing: true` → background task `get_news()`. Premier load (aucun cache ou `cached_at` NULL) = appel bloquant. Re-fetch frontend auto après 20s si `is_refreshing: true`. Implémenté via `get_news_stale()` + FastAPI `BackgroundTasks` dans `routers/news.py`.
- **`force_24h=True`** : param de `get_news()` pour bypasser le TTL et forcer un refresh (utilisé par les crons et le hourly_news_refresh).
- **`_get_stock_context()` dédupliqué** : calculé une seule fois dans `get_news()` avant les deux calls, passé en paramètre — évite 4 appels yfinance redondants.
- **Cron trigger OW** : Yahoo (*/6h) et Finnhub (*/12h) appellent `get_news(ticker, force_24h=True)` après chaque ticker avec nouveaux articles → `no_24h_news` stale flag corrigé immédiatement.
- **Cutoff articles 24H** : minuit UTC (pas fenêtre roulante 24h) — frontend `getDisplayedArticles()` + backend `_filter_24h()`.
- **AI Overview affiché selon la période** : 24H → `summary_24h` (+ badge `AUCUNE NEWS 24H` si `no_24h_news` · badge `↻ mise à jour...` si `is_refreshing`) · 1W → `summary` (7J) · 1M+ → pas d'overview Yahoo.
- **Hourly refresh** (cron :15) : pinned + tickers visités 6h avec `no_24h_news=True` → fetch Yahoo sans LLM → si ≥1 article depuis minuit UTC trouvé → `get_news(force_24h=True)`
- **`exchange` dans `stock_news_cache`** : code yfinance (NMS, NYQ, KSC…). Peuplé à la 1ère visite via trigger dans `get_news_stale()`. One-shot initial : `scripts/populate_exchange.py` (source `articles_merged_finn_yahoo`). Prévu pour gate market-hours dans `run_hourly_news_refresh()`.

---

### Events (dots chart)

Quand l'user clique un dot → `EventDrawer` → `GET /api/events/{ticker}?date=&change_pct=`

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

**Cache** : `event_causes` table — permanent.

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
- **2 jobs séparés** : `eod_digest_generation` 18h00 · `eod_digest_email` 18h30
- **Fallback daily** : Finnhub today → si vide → `stock_news_cache.ai_summary_24h` (Yahoo) → si absent → `get_news()` à la volée

---

### Tickers — Coverage

| Région | Count | Source articles | Historique |
|---|---|---|---|
| US (S&P500 + NASDAQ) | 260 | Finnhub 12 mois + Yahoo | 12 mois complets |
| EU (Paris/Frankfurt/LSE/Amsterdam/Zurich...) | 227 | Yahoo principalement | Accumulation daily |
| Asie (Japon/HK/Corée/Inde/Taiwan/Australie...) | 230 | Yahoo principalement | Accumulation daily |
| **Total** | **717** | | |

Fichier de référence : `backend/data/tickers.py`

---

### Batch scraper — `backend/scripts/batch_fill_db.py`

```bash
python scripts/batch_fill_db.py --phase scrape --region US
python scripts/batch_fill_db.py --phase enrich --region US
python scripts/batch_fill_db.py --phase bubbles --region US
python scripts/batch_fill_db.py --phase all --region US
python scripts/batch_fill_db.py --phase translate-fr --region US
python scripts/batch_fill_db.py --phase translate-fr-dumb --region US
```

**Progress files** — `backend/scripts/batch_progress_{phase}_{region}[_{lang}].json`
- Un fichier par combo phase+region+lang → pas de conflit si 2 process parallèles
- `scrape` : pas de lang · `enrich`/`bubbles` : suffixe `_en` ou `_fr` · `translate-fr*` : pas de lang

**Sync progress depuis Supabase** : `backend/scripts/sync_progress.py`
```bash
python scripts/sync_progress.py           # recrée tous les fichiers progress depuis la DB
python scripts/sync_progress.py --dry-run
```
Threshold bubbles_done = 8 causes non-null.

**Pool `bubbles_no_data`** : tickers avec 0 articles dans `articles_merged_finn_yahoo` (détecté phase bubbles) → skipper dans les runs suivants, ne pas compter dans `bubbles_done`. Clé `bubbles_no_data` dans le progress file.

**Fenêtre articles bubbles** : `bubble_service.py` cherche d'abord T-3→T+3. Si 0 articles → fallback T-6→T+6 (year_months supplémentaires seulement). S'applique batch + live (`/api/events`).

**Re-process tickers skippés** : retirer le ticker de `bubbles_done` dans le progress file → sera re-traité au prochain run avec le fallback ±6j.

**Concurrence phase bubbles** : `ThreadPoolExecutor(BUBBLES_WORKERS=3)` — 3 tickers en parallèle. Semaphore Hapuppy (3) dans `llm_service.py` limite les appels HTTP concurrents. Progress mis à jour dans le thread principal via `as_completed` (thread-safe sans Lock). Ctrl+C propre : `os._exit(0)` après sauvegarde progress.

**Save throttle** : `save_progress()` n'écrit sur disque que tous les `BATCH_SAVE_EVERY=5` appels. `force=True` aux checkpoints TG → perte max 5 tickers en cas de crash.

**Notifs TG batch** : `parse_mode: HTML` (Markdown v1 causait des 400 sur les underscores/accents). Conversion `*bold*` → `<b>bold</b>` et `_italic_` → `<i>italic</i>` dans `_telegram()`.

**Script one-shot websites** : `backend/scripts/populate_websites.py`
```bash
python scripts/populate_websites.py            # tous les tickers en stock_news_cache
python scripts/populate_websites.py --ticker AMZN
```

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
| Page profile | ✅ | Devise + compte + sign out |
| News — fetch + drawer + vidéo | ✅ | |
| News — résumés IA | ✅ | Cascade FreeTheAI → Groq |
| Cache news Supabase | ✅ | TTL 3h (24H) · 24h (7J). DUMB dans articles_merged_finn_yahoo. |
| EN-first migration | ✅ | `LLM_LANG=en`. Tous les services génèrent EN par défaut. Colonnes DB : `daily_digest.summary_en` · `event_causes.cause_en`. |
| Annotations chart (events) | ✅ | Dots peaks/valleys → EventDrawer Finnhub + cause IA |
| Chart — vertical grid lines | ✅ | Tirets par période |
| Lazy loading page stock | ✅ | Default 24H — articles 1M+ et causes events : on-demand |
| Cascade LLM FreeTheAI→Groq | ✅ | gpt-4.1 → gpt-5.4-mini → Hapuppy gpt-4.1-mini → Groq |
| EOD digest — backend | ✅ | gen 18h00 · email 18h30 · 2 jobs APScheduler |
| EOD digest — email | ✅ | Resend sandbox actif |
| EOD digest — onglet "Mes news" | ✅ | Daily/Weekly pills · auto-refresh si digest > 3h |
| Digest/Weekly router — source articles | ✅ | `routers/digest.py` lit depuis `articles_merged_finn_yahoo` (plus `stock_news_cache.news_raw`). Complète l'architecture zero-articles dans `stock_news_cache`. |
| Préférences notif | ✅ | Toggle Daily (PREMIUM jaune) + Weekly (PREMIUM vert) + chips par ticker · `notify_weekly` en DB |
| Loading animation — DogLoader | ✅ | Sleeping dog SVG, 2 comètes sens inverse, rotation 3D lente |
| DB articles — rotation & purge | ✅ | 5W max, monthly calendaire 1er du mois, yearly purge monthlies |
| Monthly on-demand (premium) | ✅ | `get_monthly_ondemand()` — rolling 4 weeklies → LLM, pas de cache persistant |
| Articles 1M/6M/1Y — sections par période | ✅ | 1M=semaines, 6M/1Y=mois. Résumé AI si dispo + articles compacts |
| Articles multi-source | ✅ | Dedup par headline. Badges colorés + multi-CTA dans drawer |
| LLM source tracking | ✅ | `llm_source` persisté dans stock_news_cache, summaries, event_causes, daily_digest |
| Re-enrich Groq→FreeTheAI | ✅ | Cron 22h30 — scan `llm_source='groq'` + `ai_summary_en IS NULL`. Stop si quota atteint. |
| Cold start ticker rare | ✅ | Scrape progressif background (1M→6M→1Y). `ColdStartBanner` poll 10s. |
| 24h vide — fallback 0 token | ✅ | Pas d'appel LLM si 0 articles 24h. Badge `AUCUNE NEWS 24H`. `no_24h_news` en DB. |
| Architecture DB — refacto | ✅ | `stock_news_cache` zéro articles. DUMB dans `articles_merged_finn_yahoo`. Migration one-shot : 4 148 `dumb_en` copiés. |
| **Batch DB filler (717 tickers)** | 🔄 | Voir séquence ci-dessous |
| Reddit sentiment | ⬜ | À venir |
| Refresh proactif OW (crons) | ✅ | Yahoo/Finnhub trigger `get_news(force_24h=True)` si nouveaux articles |
| Stale-while-revalidate OW | ✅ | Cache retourné immédiatement + background refresh + badge `↻ mise à jour...` |

---

### Séquence batch — EN first (relance 2026-06-01)

| # | Phase | Région | Statut |
|---|---|---|---|
| 1 | Bubbles 6M+1Y | US | 🔄 en cours — 137 todo restants après sync (73 done EN en DB · 2026-06-01) |
| 2 | Bubbles 6M+1Y | EU | ⏳ |
| 3 | Bubbles 6M+1Y | ASIA | ⏳ |
| 4 | Monthly summaries (breaks) | US+EU+ASIA | ⏳ après bubbles |
| — | OW enrich (ai_summary_en) | EU · ASIA | ⏭ skip — se remplit via cron 3h + reenrich 22h30 |

**Conso réelle — nuit 29-30 mai 2026 (261 tickers US enrichis) :**
| Provider | Modèle | Requests | Tokens input | Tokens output | Coût |
|---|---|---|---|---|---|
| FreeTheAI | `bbl/gpt-5.4-mini` | ~522 | 51.9M | 269K | $0 |
| Groq | `llama-4-scout-17b` | 200 | 495.7K | 221.3K | $0 |
| Groq | `llama-3.3-70b-versatile` | 139 | 83.1K | 29.4K | $0 |
| Groq | `llama-3.1-8b-instant` | 107 | 39.5K | 20K | $0 |

⚠️ FreeTheAI avg **99K tokens input / call**. Un batch complet (717 tickers × 2 calls) = ~1400 calls → dépasse tous les tiers. Prévoir plusieurs nuits ou alterner avec Groq.

**Avant tout batch FreeTheAI :** vérifier `ps aux | grep python` et killer tout processus concurrent utilisant la même clé API.

---

### Optimisations réalisées ✅

| # | Décision | Fichier |
|---|---|---|
| **articles_merged_finn_yahoo pipeline** | Yahoo */6h + Finnhub */12h → merge. `_get_or_fetch_month` merge au lieu d'écraser. | `breakers_articles_service.py`, `main.py` |
| **DB2** | `daily_digest` ne stocke plus `articles` | `digest_service.py` |
| **DB3** | Index `idx_article_week_summaries_ticker_date` — à exécuter en SQL Editor Supabase | — |
| **DB4** | Purge `event_causes > 1 an` dans cron Monday 3h | `main.py` |
| **LLM2** | `_articles_hash()` — skip brutalise si IDs inchangés. Stocke `articles_hash` | `overview_service.py` |
| **LLM4** | BRUTALIZE retourne `summary` (7J) → stocké directement comme `ai_summary` + `ow7d_cached_at`. Call 2 actif uniquement en `BATCH_MODE=1` | `overview_service.py` |
| **LLM5** | `_get_stock_context()` dédupliqué — calculé une fois dans `get_news()`, passé aux deux calls (4→2 appels yfinance) | `overview_service.py` |
| **Chart1** | Labels 24H en `HH:MM` au lieu de `YYYY-MM-DD` — bougies 5-min tracées correctement | `yfinance_service.py` |
| **Perf1** | Stale-while-revalidate sur `GET /api/news/` — `get_news_stale()` + `BackgroundTasks`. Premier load bloquant uniquement. | `routers/news.py`, `overview_service.py` |
| **Cron1** | Yahoo + Finnhub crons triggèrent `get_news(force_24h=True)` après nouveaux articles — corrige `no_24h_news` stale | `breakers_articles_service.py` |
| **Weekly Pool 1** | `_maybe_regen_weekly()` dans cron hourly | `overview_service.py` |
| **Weekly Pool 2** | `run_weekly_condensation` (lundi 3h30) | `breakers_articles_service.py` |
| **Perf2** | Re-fetch frontend auto 20s après `is_refreshing: true` — spinner disparaît sans reload manuel | `page.tsx` |
| **DB5** | Colonne `exchange` dans `stock_news_cache` — peuplée au trigger visite + one-shot `populate_exchange.py`. Prévu pour gate market-hours cron. | `overview_service.py` |
| **OW1** | Cutoff 24H = minuit UTC (était fenêtre roulante 24h) — frontend + `_filter_24h()` backend | `page.tsx`, `overview_service.py` |

---

### Roadmap post-beta

| Feature | Priorité | Notes |
|---|---|---|
| Descriptions intelligentes des tickers | 🔜 | Table `ticker_descriptions` (ticker PK, description_fr, category, generated_at) · batch depuis `longBusinessSummary` · carte pédagogique sur page stock |
| AI Overview toggle FR/EN | 🔜 | EN par défaut · toggle FR dans profile |
| LLM5 routing par complexité | ⬜ | Calls légers → modèle rapide |
| FE1 ArticleDrawer lien direct | ⬜ | `window.open(url)` si pas de `dumb_en/dumb_fr` |
