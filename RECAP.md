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
| Cron | APScheduler intégré dans FastAPI — email 18h30 · cold_start FR 19h · re-enrich 22h30 · weekly lun 3h30 · monthly 1er 4h · yearly 1er jan 5h · **yahoo */3h** (tous pinnés ≥3, merge articles_merged_finn_yahoo + trigger OW si nouveaux articles) · **finnhub */6h** (current month, merge + trigger OW si nouveaux articles) · **hourly_news_refresh :15** (pinned + visités récemment avec no_24h_news → `force_24h=True`) · **purge DB** lun 3h (event_causes >1 an). TG: tous les crons 1x/j envoient ▶ start + ✅ end avec durée. Yahoo/Finnhub: ✅ avec résumé par ticker (Yahoo: `AAPL +5 · AMZN +3` · Finnhub: `AAPL 45 · AMZN 23`). Hourly: TG uniquement si des tickers sortent du pool no_24h_news (`✅ News refresh — AAPL · AMZN sortis du pool`). |
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
│   │   ├── refresh_breakers.py
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
                               Call en streaming httpx : read=15s par chunk (trous noirs détectés en 15s,
                               réponses lentes mais réelles préservées si chunks arrivent progressivement)
                               403/429 quota : freeze jusqu'au prochain 18h00 Paris (reset Hapuppy) — 403=crédits épuisés, 429=body keywords ("quota","credit","budget"...)
                               429 rate-limit : sleep 5s local + retry dans le même thread (pas de freeze global) — max 3 tentatives
                               timeout/502/connexion → backoff 1s/2s, retry
                               Concurrence : Semaphore(HAPUPPY_CONCURRENCY=5) — max 5 appels httpx simultanés

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
- **timeout/connexion** : call en streaming, read=15s/chunk — trous noirs coupés en 15s, vraies réponses lentes préservées. Backoff 1s/2s, 3 retries, puis Groq
- **403 crédits épuisés** : freeze jusqu'au prochain 18h00 Paris (reset Hapuppy), notif TG `hapuppy_quota`
- **429 quota épuisé** (body contient "quota"/"credit"/"budget"...) : freeze jusqu'au prochain 18h00 Paris
- **429 rate-limit** (body sans keyword quota) : sleep 5s local + retry Hapuppy dans le même thread — pas de freeze global, Groq non déclenché
- **Unfreeze manuel** : `POST /admin/hapuppy/unfreeze` — reset freeze dans le process FastAPI (⚠ n'affecte pas un batch standalone)

**Notifs Telegram — trail complet de la cascade :**
```
🟠 Hapuppy `gpt-4.1-mini` — down (502/timeout)

FreeTheAI `bbl/gpt-4.1` : ✗ 503
FreeTheAI `bbl/gpt-5.4-mini` : ✗ 503
Hapuppy `gpt-4.1-mini` : ✗ timeout/connexion
Groq `llama-3.1-8b-instant` : ✓
📍 OW
```
- Une notif par raison primaire, throttle 30min — `_send_cascade_notif(reason, trail, caller="")`
- Trail buildé au fil de la cascade dans `llm_call`, envoyé quand un non-primary répond (✓ ou ✗)
- Si tous les LLM échouent → notif `🔴 Tous les LLM en échec` avec trail complet
- **`caller` param** — `llm_call()`, `llm_text()`, `llm_json()` acceptent `caller: str = ""`, propagé au footer : `📍 OW` · `📍 BUB` · `📍 Breaks`. Si non fourni : `📍 llm_service`. `translation_service` : `📍 translation_service` (sa propre cascade, pas de param caller).
- **Trigger `both_quota`** — juste avant le bloc Groq dans `llm_call()` : si `_freetheai_skip_until is not None` ET `_hapuppy_quota_skip_until is not None` → notif `🔴 FreeTheAI + Hapuppy hors quota — Groq seul actif`, throttle 30min.

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
| BRUTALIZE (30 articles → résumé 7J) | site + batch | `bbl/gpt-4.1` | `llama-4-scout-17b` ¹ |
| SUMMARY_24H | site + batch | `bbl/gpt-4.1` | `llama-3.1-8b-instant` ¹ |
| EVENT_CAUSE (dot chart) | site | `bbl/gpt-4.1` | `llama-3.1-8b-instant` |
| DIGEST EOD | email | — (lit `stock_news_cache.ai_summary_24h_en` + articles `articles_merged_finn_yahoo` 24h) | — |
| WEEKLY / MONTHLY condensation | site + batch | `bbl/gpt-4.1` | `llama-3.3-70b-versatile` ¹ |
| TRADUCTION EN→FR | batch + site | `bbl/gpt-5.4-mini` (trad légère) | Azure → Yandex (TODO) → FreeTheAI `gpt-5.4-mini` → Hapuppy `gpt-4.1-mini` → Groq `llama-3.1-8b-instant` |

¹ Groq désactivé quand `BATCH_MODE=1` — le batch échoue proprement sur le ticker et skip.

**Règle invariante traduction :** EN est toujours généré en premier par le LLM principal. FR = traduction de l'EN uniquement, jamais généré directement. Si tout le waterfall de traduction échoue → EN conservé en DB, FR vide, retry au prochain batch. On ne peut pas avoir `FR filled + EN empty`.

**Règle nommage colonnes bilingues :** tout champ pouvant exister en EN et FR doit avoir le suffixe `_en` ou `_fr`. Jamais de colonne vanilla `ai_summary` ou `cause` si un double contenu langue peut exister.

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
stock_news_cache           — cache OW live (ticker PK, ai_summary_fr, ai_summary_24h_fr, ai_summary_en, ai_summary_24h_en,
                             ai_sentiment, ai_sentiment_24h, cached_at, ow7d_cached_at, website, generated_by_en,
                             generated_by_fr, no_24h_news, articles_hash, exchange) — pas de TTL, refresh sur nouveaux articles uniquement. EN served first.
                             Zéro articles stockés ici. `exchange` = code yfinance (NMS, NYQ, KSC…) — peuplé à la 1ère
                             visite via trigger dans `get_news_stale()`. One-shot initial : `scripts/populate_exchange.py`.
                             Lignes sans `cached_at` (exchange-only) traitées comme cache absent → appel bloquant.
user_preferences           — préférences (user_id PK, currency, notify_email, notify_weekly, digest_tickers text[])
pinned_stocks              — watchlist (user_id, ticker, company_name, pinned_at) — unique(user_id, ticker)
~~daily_digest~~           — **supprimée** (remplacée par lecture directe `stock_news_cache`)
event_causes               — causes AI événements chart (ticker, date PK, articles, cause_fr, cause_en) — permanent
articles_merged_finn_yahoo — source de vérité articles (ticker, year_month PK, articles JSONB avec dumb_en/dumb_fr
                             par article, source: "finnhub"|"yahoo"|"mixed") — mois actif refresh 12h, passés permanent.
article_week_summaries     — résumé AI + top 10 articles par semaine (ticker, year_month, week_num PK, ai_summary_en, ai_summary_fr, top_articles) — jamais ai_summary vanilla
article_month_summaries    — résumé AI par mois (ticker, year_month PK, ai_summary_en, ai_summary_fr) — jamais ai_summary vanilla
article_year_summaries     — résumé AI par année (ticker, year PK, ai_summary_en, ai_summary_fr) — jamais ai_summary vanilla
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
| 1D | `stock_news_cache` (summary_24h) | `articles_merged_finn_yahoo[].dumb_en/fr` — filtre : affiché si dumb présent | — | — |
| 1W | `stock_news_cache` (summary 7J) | idem | on-demand | — |
| 1M | — | affiché en sidebar au click | on-demand | Weekly `article_week_summaries` (W1/W2/W3/W4) |
| 6M | — | — | pre-cache `event_causes` | Monthly `article_month_summaries` |
| 1Y | — | — | pre-cache `event_causes` | Monthly `article_month_summaries` |
| 5Y | — | — | — | Year headers `article_year_summaries` + Monthly `article_month_summaries` — pas d'articles bruts |

**Source articles & display par période :**
| Période | Source articles | Click article |
|---|---|---|
| 1D/1W | `articles_merged_finn_yahoo` 7j (Finn+Yahoo) — filtre `dumb_en\|dumb_fr` | sidebar : title · source · summary brut · dumb |
| 1M | `articles_merged_finn_yahoo` — sections W1-W4. Si skewed → merged + warning | sidebar |
| 6M/1Y | `articles_merged_finn_yahoo` — sections par mois, collapsées par défaut, ExpandButton pill, pagination +10 | direct URL (no sidebar, no thumbnail) |
| 5Y | `article_year_summaries` (year dropdown) + `article_month_summaries` (mois indentés) — zéro article brut | direct URL |

**ExpandButton (6M/1Y/5Y mois) :** pill `N articles ›` avec border accent-color, fond plein au hover. Sections collapsées par défaut (0 articles affichés). Clic → 10 articles, bouton `↓ N de plus` en bas du bloc pour +10 jusqu'au max. `expandedSections: Record<string, number>` dans le state page. Reset au changement de période.

**5Y — year dropdown :** `expandedYears: Record<string, boolean>`. Header année = bouton cliquable (violet `#9B77CF`), chevron `›` tourne à 90°. Mois indentés `paddingLeft: 16px` avec bordure gauche violette. Année la plus récente ouverte automatiquement au chargement. Résumé annuel affiché si disponible, au-dessus des mois.

**top_articles non tronqués :** `breakers_articles_service.py` retourne tous les articles filtrés (plus de `[:MAX_PER_MONTH]` / `[:10]` / `[:15]`). La pagination est côté frontend uniquement.

**Chaîne de condensation — inputs LLM :**
```
Weekly  : dumb_en/dumb_fr de chaque article  (fallback : headline + summary brut si pas de dumb)
Monthly : textes W1+W2+W3+W4                 (fallback < 3 weeklies : dumb + headline + summary brut)
Yearly  : textes M1→M12
```

**Condensation enrichie ✅** : `_article_text()` → dumb > summary > headline. Appliqué W prompt + fallback M.
**Display filter 1M+ ✅** : `_is_ticker_related(a, ticker, company_name)` — passe si `dumb_en/dumb_fr` présent OU si ticker OU n'importe quel mot-clé du `company_name` apparaît dans `headline + summary`. Mots-clés extraits par `_company_keywords()` : split du nom + strip des suffixes génériques (Corporation, Inc, Ltd, Group…) + mots ≤ 2 chars. Ex : "Apple Inc." → `["apple"]` · "Toyota Motor Corporation" → `["toyota", "motor"]`. `company_name` passé en query param `?company_name=` par le frontend (vient de `stock.name`).

**Semaines fixes intra-mois :** W1 = jours 1–7 · W2 = jours 8–14 · W3 = jours 15–21 · W4 = jours 22–fin du mois

**Problème connu — Finnhub date clustering :**
Finnhub indexe les articles avec sa date de crawl, pas la date de publication. Pour ~35% des mois, 80%+ des articles tombent en W4. Détection automatique côté backend : si skewed → section merged "WX→WY · pas de récap hebdomadaire disponible".

**Script condensation manuelle :** `backend/scripts/break_view_resume.py`
```bash
python scripts/break_view_resume.py AAPL --months 6  # weeklies + monthlies
python scripts/break_view_resume.py AAPL --weekly    # weeklies seulement
```
⚠ Inutilisable pour générer des weekly summaries fiables tant que Finnhub clustère les dates en W4.

**Script batch refresh breakers :** `backend/scripts/refresh_breakers.py`
```bash
python scripts/refresh_breakers.py --region US --lang en           # run normal (resume auto)
python scripts/refresh_breakers.py --region US --lang en --no-resume  # repart de zéro
python scripts/refresh_breakers.py --region US --lang en --re-enrich-legacy  # re-traite les tickers legacy
```
Checkpoint JSON : `scripts/refresh_breakers_{region}_{lang}.json` — champs `done` + `legacy`.
`legacy` = tickers traités **avant** l'enrichissement dumb+summary (`_article_text()`) — prompts générés avec headlines seuls.
Au premier run post-enrichissement, les tickers `done` existants sont auto-marqués `legacy`.
`--re-enrich-legacy` pour les re-passer avec la nouvelle logique dumb+summary.

---

### Articles — Doublons & Multi-source

Finnhub indexe le même article depuis plusieurs outlets. Contenu identique à 100%, seule la source diffère.

**Architecture :**
- Finnhub raw → `_dedup_by_headline()` → stockage propre en DB avec `extra_sources: [{name, url}]`
- `_get_or_fetch_month()` appelle `_dedup_by_headline()` avant chaque upsert

**Affichage multi-source :**
- Badges colorés par source (Yahoo=violet, Bloomberg=noir, Reuters=orange...) dans `NewsCard` et `CompactArticleRow`
- `ArticleDrawer` : un bouton "Read" par source disponible
- **Scope** : 1M/6M/1Y. 1D/1W utilise `articles_merged_finn_yahoo` (Finn+Yahoo) — multi-source possible mais pas de badges supplémentaires dans ce mode.

**Script refetch :** `backend/scripts/refetch_finnhub.py`
```bash
python scripts/refetch_finnhub.py --months 3   # re-fetch 3 derniers mois avec dedup+merge
python scripts/refetch_finnhub.py --dry-run    # liste les rows sans fetch
```

---

### Rotation des données

```
articles_merged_finn_yahoo (raw bodies)
  → source de vérité unique — article_archive supprimé ✅ (code + DB)
  → JAMAIS purgé automatiquement · 4 478 rows · 687 tickers · 122 121 articles (juin 2026)
  → cappé à 50 articles par (ticker, year_month)
  → TODO lifecycle : strip champ "summary" sur articles > 35 jours (sidebar plus affichée) via cron nettoyage
  → TODO lifecycle : delete rows entières après ~13 mois (aucune vue n'affiche des articles > 1Y) via même cron
  → TODO lifecycle : supprimer le cap 50 une fois la purge 13 mois en place

article_week_summaries
  → max 5 weeklies stockés par ticker (purge auto à chaque nouveau weekly)
  → supprimé le 1er du mois suivant (après monthly calendaire généré)

article_month_summaries (calendaire : S1+S2+S3+S4 dans l'ordre)
  → gardé 12 mois — source pour vues 6M / 1Y
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

**Cron Yahoo */3h** : fetch `get_news(count=100)` → `_dedup_by_headline` merge → `source: "mixed"` si Finnhub déjà présent → si nouveaux articles : `brutalise_new_articles(ticker)` (DUMB immédiat) → `get_news(ticker, force_24h=True)` (OW depuis dumb)

**Cron Finnhub */6h** : `_get_or_fetch_month()` → merge Finnhub + existant Yahoo → `ACTIVE_MONTH_TTL_HOURS = 12` → si nouveaux articles : `brutalise_new_articles(ticker)` → `get_news(ticker, force_24h=True)`

---

### News — Architecture

- **Source** : `yfinance.Ticker(ticker).get_news(count=100)` · filtre summaries < 80 chars
- **Filtre pertinence** : le LLM marque les articles hors-sujet "SKIP" → valeur `"SKIP"` écrite dans `articles_merged_finn_yahoo[].dumb_en` → filtrés dans `_fetch_news()` (jamais affichés ni retraités). Si **tous** sont SKIP → fallback articles bruts sans abrutis.
- **`_is_ticker_related` — suffixes exchange** : pour les tickers non-US avec suffixe (7203.T, 005930.KS, 1299.HK…), la base numérique est ajoutée aux keywords (`"7203"` depuis `"7203.T"`) — les headlines utilisent le format `TSE:7203` pas `7203.T`. Sans ce fix, tous les articles de ces tickers sont filtrés malgré leur pertinence. `services/breakers_articles_service.py`.
- **Pipeline DUMB + OW — `overview_service.py` :**
  - **DUMB découplé de l'OW** : `brutalise_new_articles(ticker)` — fonction standalone appelée par les crons dès qu'un nouvel article est détecté. Génère `dumb_en` pour tous les articles sans DUMB dans `fresh_articles[:30]`. Indépendant de la regen OW.
  - **Call 1 — OW 24H + OW 7J** : déclenché par `force_24h=True` (crons après `brutalise_new_articles`) ou cache absent. LLM2 : skip si hash IDs inchangé → si articles sans dumb → BRUTALIZE → OW 7J depuis `ai_result["summary"]`. Si tous ont déjà leur dumb → `_ow7d_from_abrutis()` directement (pas de re-brutalisation). OW 24H via `_ow24h_from_abrutis()`. Si `articles_24h` vide → fallback "No news 24h" + `_bench_context_line()`.
  - **Call 2 — OW 7J (BATCH_MODE=1 uniquement)** : lit abrutis en DB → `_ow7d_from_abrutis()`. Inactif en mode site.
  - **Pas de TTL** : `_needs_24h_refresh` et `_needs_7d_refresh` retournent `True` uniquement si le summary est absent — jamais sur critère temporel. `_is_stale_6h()` = filet sécurité visite page.
- **Stale-while-revalidate** : `GET /api/news/{ticker}` retourne le cache immédiatement (même stale) + flag `is_refreshing: true` → background task `get_news(force_24h=True)`. Premier load (aucun cache ou `cached_at` NULL) = appel bloquant. Re-fetch frontend auto après 20s si `is_refreshing: true`. Implémenté via `get_news_stale()` + FastAPI `BackgroundTasks` dans `routers/news.py`.
- **Filet de sécurité 6h** : `_is_stale_6h()` dans `get_news_stale()` — si `cached_at > 6h`, pose `is_refreshing: true` même si les summaries sont présents. La visite page déclenche alors un refresh complet (articles + DUMB + OW 1D + 7D) en arrière-plan. Les crons Yahoo */3h / Finnhub */6h font normalement le travail avant ; ce TTL n'est qu'un fallback si les crons ont raté (backend off, etc.).
- **`force_24h=True`** : param de `get_news()` qui bypasse `_needs_24h_refresh` (qui skip si la colonne est non-vide). Utilisé par les crons après détection de nouveaux articles pour régénérer l'OW avec le contenu frais — c'est le comportement normal des crons, pas une exception. Sans ce param, `get_news()` ne régénère que si la colonne est vide (premier chargement). Nom trompeur : `regen_24h` serait plus clair, mais non prioritaire à renommer.
- **`_get_stock_context()` dédupliqué** : calculé une seule fois dans `get_news()` avant les deux calls, passé en paramètre — évite 4 appels yfinance redondants.
- **Cron trigger OW** : Yahoo (*/3h) et Finnhub (*/6h) appellent `get_news(ticker, force_24h=True)` après chaque ticker avec nouveaux articles → `no_24h_news` stale flag corrigé immédiatement.
- **Cutoff articles 24H** : fenêtre glissante **26h** (`now - 26h`) partout — backend `_filter_24h()` (génération OW) + frontend `getDisplayedArticles()`. Couvre les sources de toutes timezones (max UTC-8 = 8h de décalage) sans complexifier le cache. OW et liste affichée sont cohérents : même set d'articles.
- **Filtre DUMB en vue 24H** : `getDisplayedArticles()` en mode `1d` n'affiche que les articles avec `dumb_en || dumb_fr`. Les articles LLM-SKIPped (hors sujet) sont exclus. Règle EN-first : `dumb_en` toujours généré en premier — ne jamais conditionner sur `dumb_fr` seul.
- **AI Overview affiché selon la période** : 24H → `summary_24h` (+ badge `AUCUNE NEWS 24H` si `no_24h_news` · badge `↻ mise à jour...` si `is_refreshing`) · 1W → `summary` (7J) · 1M+ → pas d'overview Yahoo.
- **Hourly refresh** (cron :15) : pinned + tickers visités 6h avec `no_24h_news=True` → **`_accumulate_yahoo` d'abord** (fetch Yahoo fresh, couvre tickers < 3 pins non couverts par `run_yahoo_accumulation`) → `_fetch_news` DB → si ≥1 article 26h trouvé → `get_news(force_24h=True)`
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
- **1 job** : `eod_digest_email` 18h30 — lit `stock_news_cache.ai_summary_24h_en` + articles 24h depuis `articles_merged_finn_yahoo`. Zéro LLM.

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

---

### Batch breakers — `backend/scripts/refresh_breakers.py`

Scrape articles + génère weekly/monthly breakers pour tous les tickers d'une région.

```bash
python scripts/refresh_breakers.py --region US   --lang en
python scripts/refresh_breakers.py --region EU   --lang en
python scripts/refresh_breakers.py --region ASIA --lang en
python scripts/refresh_breakers.py --region US   --lang fr   # après migration colonnes _fr
python scripts/refresh_breakers.py --region US   --lang en --ticker AAPL   # ticker unique
python scripts/refresh_breakers.py --region US   --lang en --no-resume     # ignore checkpoint
```

**Flow par ticker :**
1. Fetch Finnhub + Yahoo en // pour le mois courant → dedup → upsert `articles_merged_finn_yahoo`
2. **DUMB** → `brutalise_new_articles(ticker, lang)` — génère `dumb_en` pour les articles sans DUMB avant condensation (non-bloquant)
3. Détection skew Finnhub : si une semaine > `SKEW_RATIO × médiane` des autres → `finn_skewed=True`
4. Weeklies manquants sur les 5 dernières semaines glissantes :
   - Semaine cluster (skew) → skip
   - < 2 articles dans la semaine → skip
5. Monthlies manquants sur les 6 derniers mois :
   - `finn_skewed` → gen depuis raw articles directement
   - ≥ 3 weeklies existants → gen depuis weeklies
   - sinon → gen depuis raw articles
6. Checkpoint JSON après chaque ticker

**Filtrage pertinence (prompts)** : les prompts weekly et monthly demandent explicitement au LLM d'ignorer les articles génériques (top marchés, classements) non spécifiques au ticker. Pas de filtre DUMB/SKIP (90% des articles historiques n'ont pas de `dumb_en`) — le filtre est dans l'instruction prompt.

**Checkpoints** — `backend/scripts/refresh_breakers_{region}_{lang}.json` (un par combo région+langue)
- Un ticker est marqué `done` **uniquement si aucun échec LLM** (`_process_weeklies` + `_process_monthlies` retournent 0 failures)
- Si failures > 0 → ticker NON ajouté au checkpoint → retenté automatiquement au prochain run
- Log : `⚠ TICKER non marqué done (N échec(s) LLM) — retenté au prochain batch`
- **Legacy auto-regen** : si `done` non-vide + `legacy` vide au démarrage → tous les `done` auto-marqués `legacy`. Les tickers `legacy` sont inclus dans `todo` même s'ils sont dans `done` et regénérés avec `force_regen=True` (bypass existence check DB). Un run normal suffit — pas de flag supplémentaire. Après re-gen réussi, ticker retiré de `legacy`.
- **`--force-regen`** : flag CLI pour forcer la réécriture des weeklies/monthlies existants pour tous les tickers du run (pas seulement legacy).

**Notifs TG** — départ · checkpoint tous les 30 tickers (%, ETA, +articles, W/M générés, skewed, erreurs) · fin

**Note lang** — `--lang en` peut tourner immédiatement. `--lang fr` requiert la migration colonnes `ai_summary_fr` (ADD sur `article_week/month/year_summaries`).

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
| Cache news Supabase | ✅ | Pas de TTL — refresh sur nouveaux articles uniquement (crons Yahoo */3h / Finnhub */6h). DUMB dans articles_merged_finn_yahoo. |
| EN-first migration | ✅ | `LLM_LANG=en`. Tous les services génèrent EN par défaut. Colonnes DB : `event_causes.cause_en`. |
| Annotations chart (events) | ✅ | Dots peaks/valleys → EventDrawer Finnhub + cause IA |
| Chart — vertical grid lines | ✅ | Tirets par période |
| Lazy loading page stock | ✅ | Default 24H — articles 1M+ et causes events : on-demand |
| Cascade LLM FreeTheAI→Groq | ✅ | gpt-4.1 → gpt-5.4-mini → Hapuppy gpt-4.1-mini → Groq |
| EOD digest — backend | ✅ | email 18h30 · 1 job APScheduler · lit `stock_news_cache` + `articles_merged_finn_yahoo`, zéro LLM |
| EOD digest — email | ✅ | Resend sandbox actif |
| EOD digest — onglet "Mes news" | ✅ | **DigestCard** : cartes avec news = normal. Cartes sans news (`no_24h_news=True`) = border neon rotatif (conic-gradient counter-clockwise, 3s) + badge split `[NO NEWS]\|[Xj]` colorés jaune/orange/rouge selon ancienneté du dernier article (<3h/<6h/≥6h). Badge sentiment masqué si no-news. Un seul bloc texte (OW 7J). **ArticleRow** : date relative — `<1h` = `Xm`, `<24h` = `Xh`, `≥24h` = `Xj` (plus de `toLocaleTimeString` qui affichait l'heure trompeuse). **Loading** : progressive — cartes apparaissent au fil des fetches (dès la 1ère réponse, spinner disparaît). |
| Weekly router | ✅ | `/api/weekly/{ticker}` — `routers/digest.py` lit depuis **`stock_news_cache`** (`ai_summary_en` = même OW 7J que la page stock + `cached_at`). Plus d'`article_week_summaries`. Timestamps D et W cohérents (même `cached_at`). Badge `7J` au lieu de `S1·2026-06`. `_week_articles_from_archive` inclut `dumb_en`/`dumb_fr` + filtre `_is_ticker_related`. |
| Préférences notif | ✅ | Toggle Daily (PREMIUM jaune) + Weekly (PREMIUM vert) + chips par ticker · `notify_weekly` en DB |
| Loading animation — DogLoader | ✅ | Sleeping dog SVG, 2 comètes sens inverse, rotation 3D lente |
| DB articles — rotation & purge | ✅ | 5W max, monthly calendaire 1er du mois, yearly purge monthlies |
| Monthly on-demand (premium) | ✅ | `get_monthly_ondemand()` — rolling 4 weeklies → LLM, pas de cache persistant |
| Articles 1M/6M/1Y/5Y — sections par période | ✅ | 1M=semaines W1-W4 · 6M/1Y=mois collapsés avec ExpandButton pill · 5Y=year dropdown (violet) + mois indentés. Pagination +10 par clic. Tous les articles retournés (pas de troncature backend). |
| Articles multi-source | ✅ | Dedup par headline. Badges colorés + multi-CTA dans drawer |
| LLM source tracking | ✅ | `llm_source` persisté dans stock_news_cache, summaries, event_causes |
| Re-enrich Groq→FreeTheAI | ✅ | Cron 22h30 — scan `llm_source='groq'` + `ai_summary_en IS NULL`. Stop si quota atteint. |
| Cold start ticker rare | ✅ | Scrape progressif background (1M→6M→1Y). `ColdStartBanner` poll 10s. |
| 24h vide — fallback 0 token | ✅ | Pas d'appel LLM si 0 articles 24h. Badge `AUCUNE NEWS 24H` + note italique (le texte fallback moche n'est plus affiché). `no_24h_news` en DB. |
| Architecture DB — refacto | ✅ | `stock_news_cache` zéro articles. DUMB dans `articles_merged_finn_yahoo`. Migration one-shot : 4 148 `dumb_en` copiés. |
| **Batch DB filler (717 tickers)** | 🔄 | Voir séquence ci-dessous |
| Reddit sentiment | ⬜ | À venir |
| Refresh proactif OW (crons) | ✅ | Yahoo/Finnhub trigger `get_news(force_24h=True)` si nouveaux articles |
| Stale-while-revalidate OW | ✅ | Cache retourné immédiatement + background `get_news(force_24h=True)` + badge `↻ mise à jour...` (vert + spin). Filet 6h : visite page trigger refresh si `cached_at > 6h` même sans nouveaux articles. |

---

### Séquence batch — EN first (relance 2026-06-01)

| # | Phase | Région | Statut |
|---|---|---|---|
| 1 | Bubbles 6M+1Y | US | ✅ |
| 2 | Bubbles 6M+1Y | EU | ✅ |
| 3 | Bubbles 6M+1Y | ASIA | ✅ (208 tickers · 2136 bubbles · 2026-06-03) |
| 4 | Breakers weekly+monthly EN | US | 🔄 en cours — `refresh_breakers.py --region US --lang en` |
| 5 | Breakers weekly+monthly EN | EU | ⏳ |
| 6 | Breakers weekly+monthly EN | ASIA | ⏳ |
| 7 | Breakers weekly+monthly FR | US+EU+ASIA | ⏳ après migration colonnes `_fr` |
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
| **articles_merged_finn_yahoo pipeline** | Yahoo */3h + Finnhub */6h → merge. `_get_or_fetch_month` merge au lieu d'écraser. | `breakers_articles_service.py`, `main.py` |
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
| **Perf2** | Re-fetch frontend auto 20s après `is_refreshing: true` — polling récursif (active flag) : continue toutes les 20s tant que `is_refreshing: true`, s'arrête dès `false` ou démontage composant. Était un setTimeout one-shot (spinner bloqué si LLM > 20s). | `page.tsx` |
| **DB5** | Colonne `exchange` dans `stock_news_cache` — peuplée au trigger visite + one-shot `populate_exchange.py`. Prévu pour gate market-hours cron. | `overview_service.py` |
| **UX2** | Articles 24h filtrés sur présence DUMB — `getDisplayedArticles()` (view 1d) exclut les articles sans `dumb_en \|\| dumb_fr`. Les articles hors-sujet (SKIP LLM) disparaissent de la liste. Filtre EN-first : `dumb_en` est toujours généré en premier, `dumb_fr` n'est qu'un fallback — ne jamais conditionner l'affichage sur `dumb_fr` seul. **Card layout** : pic 64×48px à gauche couvrant 2 lignes (source+date / title), DUMB pleine largeur en dessous. | `page.tsx` |
| **UX3** | OW card — date `cached_at` affichée en vert accent (monospace, `DD/MM HH:MM`) à droite du label "AI OVERVIEW", visible en 24H et 7D. Padding réduit (`14px 18px`). BULLISH/BEARISH badge déplacé en `position: absolute` top-right du bloc stock header (à côté du nom d'action), visible en 24H et 7D. Header "NEWS & ANALYSIS" n'affiche plus la période en vue 24H et 7D (la date dans l'OW card suffit). | `page.tsx` |
| **OW1** | Cutoff 24H = minuit UTC du jour courant (`now.replace(hour=0,...)`). Articles du jour uniquement — pas un créneau glissant de 24h. Frontend `getDisplayedArticles()` + backend `_filter_24h()`. | `page.tsx`, `overview_service.py` |
| **Cron2** | Yahoo */3h + Finnhub */6h (était */6h + */12h). TTL supprimé — `_needs_24h_refresh` et `_needs_7d_refresh` retournent `True` uniquement si summary absent. `_is_stale_6h()` = filet sécurité visite page. | `overview_service.py`, `main.py` |
| **DB6** | `daily_digest` supprimée — my-news lit `/api/news/{ticker}` · email EOD lit `stock_news_cache` directement, zéro LLM | `digest_service.py`, `main.py`, `my-news/page.tsx` |
| **UX1** | Weekly freshness dot dans `WeeklyCard` (my-news) — vert <6h · orange <24h · rouge >24h (aligne sur `cached_at` depuis `stock_news_cache`). Badge `↻ mise à jour...` visible (vert + spin). Bug "None" dans fallback no-news corrigé. | `my-news/page.tsx`, `globals.css`, `overview_service.py` |
| **UX8** | **DigestCard no-news redesign** — border neon rotatif conic-gradient (3s, counter-clockwise, `@property --border-angle`). Badge split `[NO NEWS]\|[Xj]` coloré selon ancienneté du dernier article : jaune warn <3h, orange alert <6h, rouge critical ≥6h. Sentiment badge masqué si no-news. Un seul texte (OW 7J depuis `stock_news_cache.summary`). | `my-news/page.tsx`, `globals.css` |
| **UX9** | **ArticleRow** — date `≥24h` = `Xj` au lieu de `HH:MM` (l'heure-du-jour trompait l'utilisateur sur l'ancienneté réelle). | `my-news/page.tsx` |
| **Perf3** | **Loading progressif my-news** — `Promise.all` → fetch individuel + `setDigests/setWeeklies` incrémental. Spinner s'arrête dès la 1ère réponse reçue, cards suivantes apparaissent au fil de l'eau. | `my-news/page.tsx` |
| **Data1** | **Hourly refresh fetch Yahoo avant lecture DB** — `run_hourly_news_refresh` appelle `_accumulate_yahoo` en début de boucle. Corrige le cas tickers < 3 pins (ex: 7203.T) dont la DB ne se mettait jamais à jour car `run_yahoo_accumulation` exige ≥ 3 users. | `services/overview_service.py` |
| **Data2** | **`_is_ticker_related` — base ticker sans suffixe exchange** — ajoute `"7203"` (base de `"7203.T"`) aux keywords. Corrige le faux-négatif sur tous les tickers non-US dont les headlines utilisent le format `TSE:7203` / `KSC:005930`. Affecte JP, KR, HK, etc. | `services/breakers_articles_service.py` |
| **Data3** | **`_is_ticker_related` + company_name partout** — `_fetch_news` et `_week_articles_from_archive` appelaient le filtre sans `company_name` → articles liés à la société filtrés si le ticker ne figurait pas tel quel dans le texte. Fix : `_lookup_company_name(ticker, supa)` lookup depuis `pinned_stocks` dans `_fetch_news` ; idem dans `routers/digest.py` weekly endpoint. Couvre tous les appelants sans `company_name` explicite (OW, DUMB, my-news weekly). | `services/overview_service.py`, `routers/digest.py` |
| **Breaks4** | **`refresh_breakers.py` — DUMB avant condensation** — `_process_ticker` appelle `brutalise_new_articles(ticker, lang)` en step 1b (après scrape, avant weeklies). Sans ce step, tous les Breakers étaient générés depuis `summary→headline` fallback car `brutalise_new_articles` n'était jamais appelé pour les tickers batch (`min_users=3` dans les crons Yahoo/Finnhub excluait les ~700 tickers batch). US/EU/ASIA Breakers déjà générés = qualité dégradée. | `scripts/refresh_breakers.py` |
| **Breaks5** | **`refresh_breakers.py` — re-run auto des Breakers pré-DUMB** — `_process_weeklies`/`_process_monthlies` acceptent `force_regen=True` (bypass `if exists.data: continue`). Les tickers `legacy` sont inclus dans `todo` et force-regénérés même s'ils sont dans `done`. Auto-marquage : si `done` non-vide + `legacy` vide au démarrage → tous `done` → `legacy` → un run normal suffit pour regen. Commande : `python scripts/refresh_breakers.py --region US/EU/ASIA --lang en`. | `scripts/refresh_breakers.py` |
| **LLM6** | DUMB découplé de l'OW — `brutalise_new_articles(ticker)` standalone. Appelé par crons Yahoo/Finnhub avant `get_news(force_24h=True)`. `_call1_dumb_ow24h` : si tous les articles ont déjà leur dumb → `_ow7d_from_abrutis()` au lieu de re-brutaliser (suppression fallback `to_brutalise = fresh_articles[:30]`). | `overview_service.py`, `breakers_articles_service.py` |
| **Breaks1** | **Bug cold_start purge prématurée** — `_purge_excess_weeklies(max_weeks=5)` était appelé à l'intérieur de la boucle weeklies (après chaque mois), avant la boucle monthlies. Les weeklies des vieux mois étaient purgés avant d'être utilisés pour générer les monthly summaries → `len(week_rows.data) < 2` → aucun monthly généré pour les mois anciens. Fix : purge déplacée après les deux boucles (W → M → purge). | `cold_start_service.py` |
| **Breaks2** | **Bug lang hardcodé FR-first dans `get_period_articles`** — 4 appels `ai.get("ai_summary_fr") or ai.get("ai_summary_en")` ignoraient `LLM_LANG`. Remplacés par `ai.get(_summary_col())` qui respecte `LLM_LANG`. Affecte vues 1M/6M/1Y/5Y (weeks, months, years). | `breakers_articles_service.py` |
| **Breaks3** | **Filtre `_is_ticker_related` élargi** — ancienne version : `ticker.lower() in headline` seulement → ratait tous les articles qui écrivent le nom ("Apple" au lieu de "AAPL"). Nouvelle version : OR sur `dumb_en/dumb_fr` + ticker + mots-clés du `company_name` (via `_company_keywords()`) dans `headline + summary`. `company_name` transmis par le frontend via `?company_name=` (vient de `stock.name`). Appliqué uniformément en 1M/6M/1Y. | `breakers_articles_service.py`, `routers/articles.py`, `page.tsx` |
| **UX4** | **Articles view 6M/1Y/5Y — compact sans thumbnail.** `CompactArticleRow` avec `directLink=true` (6M/1Y) : thumbnail supprimée, rendu compact identique à 1M (titre 11px + source badge + heure + icône lien externe). 5Y idem. Cohérence visuelle sur toutes les vues longues. | `page.tsx` |
| **UX5** | **ExpandButton pill sections.** Remplace le chevron discret par une pill `N articles ›` (border accent, fond plein au hover). Sections collapsées par défaut, pagination +10 par clic, bouton `↓ N de plus`. 5Y : year dropdown avec `expandedYears` state, année la plus récente ouverte par défaut, mois indentés avec bordure violette. `top_articles` non tronqué côté backend. | `page.tsx`, `breakers_articles_service.py` |
| **UX6** | **`_is_ticker_related` appliqué dans `overview_service.py`** — articles `articles_merged_finn_yahoo` filtrés avant envoi au frontend (daily news) → articles hors-sujet exclus. Même filtre dans `digest.py` pour le weekly. `dumb_en`/`dumb_fr` passés dans `_articles_from_archive` → bouton dumb visible dans `ArticleRow` weekly. | `overview_service.py`, `routers/digest.py` |
| **UX7** | **`ArticleRow` my-news — compact + dumb expand.** Layout : titre + source badge + heure sur une seule ligne (source à droite). Bouton circle 22×22px vert, fond pâle → plein au hover, `›` tourne 90° au clic → dumb inline fond vert léger. Visible uniquement si `dumb_en \|\| dumb_fr`. `WeeklyCard` redesigné pour matcher `DigestCard` : même padding/typo, count `X articles 7j`, freshness dot. | `my-news/page.tsx` |

---

### Roadmap post-beta

| Feature | Priorité | Notes |
|---|---|---|
| Descriptions intelligentes des tickers | 🔜 | Table `ticker_descriptions` (ticker PK, description_fr, category, generated_at) · batch depuis `longBusinessSummary` · carte pédagogique sur page stock |
| AI Overview toggle FR/EN | 🔜 | EN par défaut · toggle FR dans profile |
| LLM5 routing par complexité | ⬜ | Calls légers → modèle rapide |
| FE1 ArticleDrawer lien direct | ⬜ | `window.open(url)` si pas de `dumb_en/dumb_fr` |
