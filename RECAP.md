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
| Cron | APScheduler intégré dans FastAPI — email 18h30 · cold_start FR 19h · re-enrich 22h30 (+ retry :35 toutes les heures, actif si pending) · weekly lun 3h30 · monthly 1er 4h · yearly 1er jan 5h · **yahoo */3h** (tous pinnés ≥3, merge articles — si nouveaux articles : gen_dumb + OW 1D+7D immédiatement) · **finnhub */6h** (current month, merge — si nouveaux articles : gen_dumb + OW 1D+7D immédiatement) · **marketaux */6h** (EU+ASIA pinnés uniquement — routing région via `_detect_region(ticker)`) · **newsapi */6h** (EU+ASIA pinnés uniquement) · **google_rss */3h** (tous pinnés — complément Yahoo, feed ~2 semaines) · **hourly_news_refresh :15** (pinned + visités récemment avec no_24h_news → `force_24h=True`) · **purge DB** lun 3h (bub_cache >1 an). TG: tous les crons 1x/j envoient ▶ start + ✅ end avec durée. Yahoo/Finnhub: ✅ avec résumé par ticker (Yahoo: `AAPL +5 · AMZN +3` · Finnhub: `AAPL 45 · AMZN 23`). Hourly: TG uniquement si des tickers sortent du pool no_24h_news (`✅ News refresh — AAPL · AMZN sortis du pool`). Re-enrich retry : freeze jusqu'à 2h si FreeTheAI down à 22h30, puis check toutes les 1h, freeze 1h si toujours indisponible. |
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
│   │   ├── batch_fill_db.py            ← stateless (DB source of truth) · phases: scrape/enrich/bubbles/translate-fr/translate-fr-dumb
│   │   ├── refresh_breakers.py         ← scrape P1+P2 + DUMB + W + M breakers
│   │   ├── zen_watcher.py              ← probe ZenLLM toutes les 5 min → lance batch bubbles si UP
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
| `get_or_gen_bub()` | `bub_prod` Gemini→Hapuppy | `bub_prod` Gemini→Hapuppy | params : `cascade`, `prefetched_cache`, `force_regen` (bypass cache), `in_1w/1m/6m/1y` |
| `send_daily_digest()` | Lit `ow_cache` · Azure si FR manquant · envoie Resend | — |
| `send_weekly_digest()` | Lit `ow_cache` · Azure si FR manquant · envoie Resend | — |

---

### LLM — Cascade & modèles

Tous les appels LLM passent par `services/llm_service.py` qui gère la cascade automatiquement.

**Cascade par mode (`cascade` param de `llm_call()`) :**

| Mode | Providers (ordre) | Qui l'utilise | Notes |
|---|---|---|---|
| `default` | FreeTheAI → Hapuppy → Groq | live OW, BUB si non précisé | |
| `zen` | ZenLLM → raise | refresh_breakers.py (toutes phases) · regen_bubs.py · batch_fill_db.py (bubbles) | **ZEN PROMO actif** — waterfall `free/gpt-5.5` → `free/claude-sonnet-4.6` · clé `ZENLLM_API_KEY` · bloc dédié · `BUBBLES_WORKERS=1` forcé (zen_watcher) · **fin promo** : scripts → `promo`/`bub_batch`, retirer `BUBBLES_WORKERS=1` de zen_watcher |
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

**Règle FreeTheAI** : restreint aux cascades `monthly_prod`, `yearly_prod`, `monthly_scrap`, `monthly_scrap_ext` uniquement. Toutes les autres cascades (prod temps réel, batch W/DUMB/promo) n'appellent jamais FreeTheAI.

**Cascade default :**
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

Gemini: gemini-3.1-flash-lite → selon mode (avant Hapuppy pour ow24h_prod/dumb_prod/weekly_scrap · après pour bub_prod/weekly_prod) — OUT OF CREDITS (19/06/2026)
                                 API native Google (pas OpenAI-compatible) : POST generativelanguage.googleapis.com/v1beta/models/{model}:generateContent
                                 3 clés en rotation : GEMINI_API_KEY · GEMINI_API_KEY_2 · GEMINI_API_KEY_3 (comptes Google indépendants)
                                 État per-clé : bad_key · daily_exhausted · consecutive_429 · freeze_until · ratelimit_until · last_call
                                 Sur daily épuisé ou 3x429 freeze → rotation immédiate vers la clé suivante (continue dans la boucle)
                                 Throttle : Lock(_gemini_lock) + GEMINI_MIN_INTERVAL=4.1s par clé — sleep sorti du lock
                                 429 RPM × 3 consécutifs → freeze_until = minuit UTC · probe auto 1h après expiry
                                 429 quota daily → daily_exhausted (keywords stricts : "daily"/"per_day"/"per_day_free_tier")
                                 400/403 → bad_key · sur succès → consecutive_429 = 0, freeze_until = None

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
| BRUTALIZE (30 articles → résumé 7J) | site + batch | `bbl/gpt-4.1` | `llama-4-scout-17b` ¹ |
| SUMMARY_24H | site + batch | `bbl/gpt-4.1` | `llama-3.1-8b-instant` ¹ |
| EVENT_CAUSE (dot chart) | site | `bbl/gpt-4.1` | `llama-3.1-8b-instant` |
| DIGEST DAILY (1J) | email 19h15 | — (lit `ow_cache.ow1d_en/ow1d_fr` selon `user_preferences.lang` · Azure pre-flight si FR manquant · snapshot mémoire · zéro LLM) | — |
| DIGEST WEEKLY (7J) | email sam 7h | — (lit `ow_cache.ow7d_en/ow7d_fr` selon `user_preferences.lang` · 5 articles 7j · même pre-flight Azure) | — |
| WEEKLY / MONTHLY condensation | site + batch | `bbl/gpt-4.1` | `llama-3.3-70b-versatile` ¹ |
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
Utilise `llm_call(cascade="promo")` (Gemini→raise) — pas encore migré `zen` (Gemini out of credits, à faire si nécessaire). `translation_service.py` inchangé comme backup prod. `TRANSLATE_PROMPT` importé depuis `translation_service.py` (source unique).
- Filtre : `_en` non-null ET `_fr` null uniquement — idempotent, relançable sans risque
- Ordre de priorité : BUB → Y breakers → M breakers → W breakers
- Filtres : `--region US|EU|ASIA`, `--ticker TICKER`, `--since 2h|30m|3d`, `--only bub,y,m,w`
- `--workers N` (défaut 10) : traductions en parallèle via ThreadPoolExecutor — chaque row assignée à un seul worker, pas de doublon possible
- Erreurs par traduction catchées individuellement (None) : la row reste `_fr=null` → reprocessée au prochain run automatiquement
- `--dry-run` : compte les lignes sans écrire
- `max_tokens=2000` non envoyé à Gemini (Gemini ignore ce param dans cascade `promo`)

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
R2_ENDPOINT_URL=...                # Cloudflare R2 S3-compatible endpoint
R2_ACCESS_KEY_ID=...               # R2 API token ID
R2_SECRET_ACCESS_KEY=...           # R2 API token secret
R2_BUCKET=stockvibe-articles       # bucket R2 (à créer dans dashboard Cloudflare)
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
                             website, no_24h_news, articles_hash, exchange) — pas de TTL, refresh sur nouveaux articles uniquement. EN served first.
                             Zéro articles stockés ici. `exchange` = code yfinance (NMS, NYQ, KSC…) — peuplé à la 1ère
                             visite via trigger dans `get_news_stale()`. One-shot initial : `scripts/populate_exchange.py`.
                             Lignes sans `ow1d_cached_at_en` (exchange-only) traitées comme cache absent → appel bloquant.
user_preferences           — préférences (user_id PK, currency, notify_email, notify_weekly, digest_tickers text[], lang text DEFAULT 'en')
pinned_stocks              — watchlist (user_id, ticker, company_name, pinned_at) — unique(user_id, ticker)
~~daily_digest~~           — **supprimée** (remplacée par lecture directe `ow_cache`)
bub_cache               — causes AI événements chart (ticker, date PK, bub_en, bub_fr,
                             bub_cached_at_en, bub_cached_at_fr, llm_source_en, llm_source_fr,
                             in_1w, in_1m, in_6m, in_1y) — permanent · articles JSONB droppé (DT9 ✅)
articles — source de vérité articles (ticker, year_month PK, articles JSONB avec dumb_en/dumb_fr
                             par article, source: "finnhub"|"yahoo"|"mixed") — mois actif refresh 12h, passés permanent.
breaks_weekly     — résumé AI + top 10 articles par semaine (ticker, year_month, week_num PK, bw_en, bw_fr, top_articles,
                             llm_source_en, llm_source_fr, condensed_at, status TEXT) — NULL = généré ok · "skew" = semaine cluster Finnhub skippée ·
                             "less_3_articles" = articles insuffisants (< seuil) · "no_articles" = scrapé mais 0 articles ·
                             "no_processed" = jamais scrapé (aucune row dans articles) · "error" = exception LLM.
                             Exists checks filtrent sur status IS NULL (les rows non-null permettent le retry après nouveau scrape).
                             _purge_excess_weeklies ne purge que les rows status IS NULL.
breaks_monthly    — résumé AI par mois (ticker, year_month PK, bm_en, bm_fr,
                             llm_source_en, llm_source_fr, condensed_at, status TEXT) — NULL = généré ok · "less_3_articles" = articles insuffisants (< 3) ·
                             "no_articles" = scrapé mais 0 articles · "no_processed" = jamais scrapé · "error" = exception LLM.
                             Exists checks filtrent sur status IS NULL. _build_state_from_db compte uniquement les rows status IS NULL.
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
**Display filter 1M+ ✅** : `_is_ticker_related(a, ticker, company_name)` — passe si `dumb_en/dumb_fr` présent OU si ticker OU n'importe quel mot-clé du `company_name` apparaît dans `headline + summary`. Mots-clés extraits par `_company_keywords()` : split du nom + strip des suffixes génériques (Corporation, Inc, Ltd, Group…) + mots ≤ 2 chars. Ex : "Apple Inc." → `["apple"]` · "Toyota Motor Corporation" → `["toyota", "motor"]`. `company_name` passé en query param `?company_name=` par le frontend (vient de `stock.name`).

**Semaines fixes intra-mois :** W1 = jours 1–7 · W2 = jours 8–14 · W3 = jours 15–21 · W4 = jours 22–fin du mois

**Problème connu — Finnhub date clustering :**
Finnhub indexe les articles avec sa date de crawl, pas la date de publication. Pour ~35% des mois, 80%+ des articles tombent en W4. Détection automatique côté backend : si skewed → section merged "WX→WY · pas de récap hebdomadaire disponible".

**Script batch refresh breakers :** `backend/scripts/refresh_breakers.py`
```bash
python scripts/refresh_breakers.py --region US --lang en                               # run complet
python scripts/refresh_breakers.py --region US --lang en --force-regen                # régénère W+M même s'ils existent déjà
python scripts/refresh_breakers.py --region US --lang en --skip-scrape                # DUMB+W+M seulement, sans scrap
python scripts/refresh_breakers.py --region US --lang en --skip-p1                    # saute RSS+Yahoo+Finnhub, garde P2+génération
python scripts/refresh_breakers.py --region US --lang en --skip-rss-yahoo             # saute RSS+Yahoo, exécute Finnhub+P2+génération (reprise mid-Finn)
python scripts/refresh_breakers.py --region US --lang en --retry-missed               # retraite les (ticker, ym) du scrap_missed_checkpoint.json
python scripts/refresh_breakers.py --region US --lang en --retry-skipped              # re-scrape tickers avec W less_10_articles ou M absent (mois courant) → chaîne W→M
python scripts/refresh_breakers.py --region US --lang en --ticker AAPL                # ticker unique (debug)
```
Flags supprimés (refacto 2026-06) : `--phase ab`, `--phase m`, `--no-resume`, `--regen-bubs`, `--translate`.

**Status column — écriture par script :**
| Script | W writes | M writes |
|---|---|---|
| `refresh_breakers.py` | skew · less_10_articles · no_articles · error | no_articles · error |
| `breakers_articles_service.py` (cron) | no_processed · no_articles · less_10_articles · error | no_processed · no_articles · error |
| `cold_start_service.py` | error | error |
| `batch_fill_db.py` | non concerné (traduction uniquement) | non concerné |

`less_10_articles` remplace `less_3_articles` (seuil W passé à `MIN_W_SCRAP = 10`).

**Flow (tout séquentiel) :**
- **Phase 1** : RSS 12m → Yahoo mois courant → Finnhub 12m US — sources gratuites
- **Gate fraicheur** : `_build_targets` (1 req/year_month) → skip P2 si ≥25 arts mois ET ≥10 arts semaine courante
- **Phase 2** : NewsAPI 1m → Marketaux 3m → AV 12m US-only — payant, budget-limité
  - `scrap_budget` : tag `(ticker, ym)` uniquement si les 3 sources payantes ont toutes été skippées faute de budget
  - **p2_sources** : colonne `p2_sources text[]` sur `articles` — tracker quels APIs ont déjà hit chaque (ticker, ym). Couvre **P1 Finnhub** + **P2** (newsapi · marketaux · av). Skip si déjà taggé. Tag posé **uniquement sur succès ou résultat vide** — un 429 ne pose PAS le tag (ticker retenté au prochain run). Abort automatique après 3 × 429 consécutifs (2 pour AV). Pour les crons prod : tag P2 uniquement en fin de mois (J+3). Reset naturel : nouvelle row par mois.
  - **Finnhub throttle** : `time.sleep(1.0)` entre chaque call — ~60 req/min (limite free tier). Sur second passage (`--skip-rss-yahoo`), les paires déjà taggées `"finnhub"` dans `p2_sources` sont skippées.
  - **AV : US uniquement** — `_detect_region(t) == "US"` filtré avant le loop. AV ne couvre pas les tickers EU/ASIA (suffixes `.DE`, `.L`, `.AS` non reconnus → `feed = []` → budget grillé pour rien). **1 call/ticker** (fenêtre 12m, limit=1000) — résultats splittés par `year_month` en mémoire puis upsertés mois par mois. Budget 25 req/j → 25 tickers/j au lieu de N_mois × N_tickers.
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

  **Lifecycle articles — architecture décidée :**

  0 → 6 mois (Supabase, full content) :
    → champs complets : headline · url · source · datetime · extra_sources · summary · dumb_en · dumb_fr
    → display 1D/1W/1M/6M avec drawer · BUB gen · Breaks gen : tout disponible

  6 → 13 mois (Supabase lightweight + externe R2/Turso) :
    → Supabase garde : headline · url · source · datetime · extra_sources
    → Externe (clé `{ticker}/{year_month}.json.gz`) : summary · dumb_en · dumb_fr
    → display 6M/1Y directLink=true : lightweight Supabase suffit, externe jamais touché
    → BUB sur dot déjà cached (bub_en non-null) : externe jamais touché
    → BUB sur nouveau dot jamais cliqué : fetch externe → LLM avec contenu complet
    → Breaks : déjà générés pour ces mois, externe jamais touché
    → Gain estimé : 150-200 MB libérés sur Supabase

  > 13 mois : purge total
    → DELETE row Supabase + DELETE fichier externe
    → bub_cache a déjà purgé les dots > 1 an → aucun BUB possible
    → aucune vue n'affiche des articles > 12 mois

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
- Finnhub retourne des articles → `source: "finnhub"` → bootstrap immédiat 12 mois
- Finnhub retourne 0 articles → `source: "yahoo"` → accumulation daily via cron
- `source` valeurs valides : `"finnhub"` · `"yahoo"` · `"marketaux"` · `"newsapi"` · `"google_rss"` · `"alphavantage"` · `"mixed"` (dès qu'il y a merge multi-source)

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
| Source | Scrap | Prod (au lancement) |
|---|---|---|
| Yahoo | déjà en place | */3h tous pinnés ≥3 — inchangé |
| Finnhub | déjà en place | */6h tous pinnés — inchangé |
| Marketaux | SKWD prio → M vides EU+ASIA | */6h EU+ASIA pinnés |
| NewsAPI | P1 SKWD EU+ASIA → P2 M vides EU+ASIA → P3 SKWD US → P4 M vides US | */6h EU+ASIA pinnés |
| Google RSS | **top 1 scrap** : after/before par mois · tous tickers (SKWD + vides US+EU+ASIA) · ~46 art/mois · gratuit/illimité | */3h tous tickers (complément Yahoo) |
| NewsAPI | si M < 25 art après RSS · EU+ASIA (tous) + US SKWD ou vides · 100 req/j | */6h EU+ASIA pinnés |
| Marketaux | si M < 25 art après RSS · EU+ASIA (tous) + US SKWD ou vides · SKWD prio · 100 req/j | */6h EU+ASIA pinnés |
| Alpha Vantage | last resort si M encore < 25 art · EU+ASIA (tous) + US SKWD ou vides · 25 req/j | scrap only (25/j trop limité) |

**Cron Yahoo */3h** : fetch `get_news(count=100)` → pour les tickers EU/ASIA (`.` dans le ticker) : + `yf.Search(company_name)` via `t.info.get("shortName")` (dedup par URL) → `_dedup_by_headline` merge → `source: "mixed"` si Finnhub déjà présent → si nouveaux articles : `brutalise_new_articles(ticker)` (DUMB immédiat) → `get_news(ticker, force_24h=True)` (OW depuis dumb)

**Cron Google RSS */3h** (`google_rss_articles_scrap()` dans `main.py`) : tous pinnés · `_fetch_rss_month` par mois · `_build_rss_query(ticker, company_name)` · merge → dedup headline → upsert · post-scrap : gen_dumb + scrap_gen_weekly + scrap_gen_monthly

**Cron Marketaux */6h** (`marketaux_articles_scrap()`) : tous pinnés · P1→P4 · `_detect_region` · `_fetch_marketaux_month` · `_normalize_marketaux()` · budget 100 req/j · post-scrap complet

**Cron NewsAPI */6h** (`newsapi_articles_scrap()`) : tous pinnés · P1→P4 · `_fetch_newsapi_month` · `_normalize_newsapi()` · budget 100 req/j · 1 mois max (plan Dev gratuit) · post-scrap complet

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

**Cron Finnhub */6h** : `_get_or_fetch_month()` → merge Finnhub + existant Yahoo → `ACTIVE_MONTH_TTL_HOURS = 12` → si nouveaux articles : `brutalise_new_articles(ticker)` → `get_news(ticker, force_24h=True)`

---

### News — Architecture

- **Source** : `yfinance.Ticker(ticker).get_news(count=100)` · filtre summaries < 80 chars
- **Filtre pertinence** : le LLM marque les articles hors-sujet "SKIP" → valeur `"SKIP"` écrite dans `articles[].dumb_en` → filtrés dans `_fetch_news()` (jamais affichés ni retraités). Si **tous** sont SKIP → fallback articles bruts sans abrutis.
- **`_is_ticker_related` — suffixes exchange** : pour les tickers non-US avec suffixe (7203.T, 005930.KS, 1299.HK…), la base numérique est ajoutée aux keywords (`"7203"` depuis `"7203.T"`) — les headlines utilisent le format `TSE:7203` pas `7203.T`. Sans ce fix, tous les articles de ces tickers sont filtrés malgré leur pertinence. `services/breakers_articles_service.py`.
- **Pipeline DUMB + OW — `overview_service.py` :**
  - **DUMB découplé de l'OW** : `gen_dumb(ticker)` — fonction standalone appelée par les crons dès qu'un nouvel article est détecté. Génère `dumb_en` pour tous les articles sans DUMB dans `fresh_articles[:30]`. Indépendant de la regen OW.
  - **Call 1 — OW 24H + OW 7J** : déclenché par `force_24h=True` (crons après `gen_dumb`) ou cache absent. LLM2 : skip si hash IDs inchangé → si articles sans dumb → BRUTALIZE → OW 7J depuis `ai_result["summary"]`. Si tous ont déjà leur dumb → `_ow7d_from_abrutis()` directement (pas de re-brutalisation). OW 24H via `_ow24h_from_abrutis()`. Si `articles_24h` vide → fallback "No news 24h" + `_bench_context_line()`.
  - **Call 2 — OW 7J (BATCH_MODE=1 uniquement)** : lit abrutis en DB → `_ow7d_from_abrutis()`. Inactif en mode site.
  - **Pas de TTL** : `_needs_24h_refresh` et `_needs_7d_refresh` retournent `True` uniquement si le summary est absent — jamais sur critère temporel. `_is_stale_6h()` = filet sécurité visite page.
- **Stale-while-revalidate** : `GET /api/news/{ticker}` retourne le cache immédiatement (même stale) + flag `is_refreshing: true` → background task `gen_ow_both(force_24h=True)`. Premier load (aucun cache ou `cached_at` NULL) = appel bloquant. Re-fetch frontend auto après 20s si `is_refreshing: true`. Implémenté via `get_or_refresh_ow_both()` + FastAPI `BackgroundTasks` dans `routers/news.py`.
- **Filet de sécurité 6h** : `_is_stale_6h()` dans `get_or_refresh_ow_both()` — si `cached_at > 6h`, pose `is_refreshing: true` même si les summaries sont présents. La visite page déclenche alors un refresh complet (articles + DUMB + OW 1D + 7D) en arrière-plan. Les crons Yahoo */3h / Finnhub */6h font normalement le travail avant ; ce TTL n'est qu'un fallback si les crons ont raté (backend off, etc.).
- **`force_24h=True`** : param de `gen_ow_both()` qui bypasse `_needs_24h_refresh` (qui skip si la colonne est non-vide). Utilisé par les crons après détection de nouveaux articles pour régénérer l'OW avec le contenu frais — c'est le comportement normal des crons, pas une exception. Sans ce param, `gen_ow_both()` ne régénère que si la colonne est vide (premier chargement).
- **`_get_stock_context()` dédupliqué** : calculé une seule fois dans `gen_ow_both()` avant les deux calls, passé en paramètre — évite 4 appels yfinance redondants.
- **Cron trigger OW** : Yahoo (*/3h) et Finnhub (*/6h) appellent `gen_ow_both(ticker, force_24h=True)` après chaque ticker avec nouveaux articles → `no_24h_news` stale flag corrigé immédiatement.
- **Cutoff articles 24H** : fenêtre glissante **26h** (`now - 26h`) partout — backend `_filter_24h()` (génération OW) + frontend `getDisplayedArticles()`. Couvre les sources de toutes timezones (max UTC-8 = 8h de décalage) sans complexifier le cache. OW et liste affichée sont cohérents : même set d'articles.
- **Filtre DUMB en vue 24H** : `getDisplayedArticles()` en mode `1d` n'affiche que les articles avec `dumb_en || dumb_fr`. Les articles LLM-SKIPped (hors sujet) sont exclus. Règle EN-first : `dumb_en` toujours généré en premier — ne jamais conditionner sur `dumb_fr` seul.
- **AI Overview affiché selon la période** : 24H → `summary_24h` (+ badge `AUCUNE NEWS 24H` si `no_24h_news` · badge `↻ mise à jour...` si `is_refreshing`) · 1W → `summary` (7J) · 1M+ → pas d'overview Yahoo.
- **Hourly refresh** (cron :15) : pinned + tickers visités 6h avec `no_24h_news=True` → **`_accumulate_yahoo` d'abord** (fetch Yahoo fresh, couvre tickers < 3 pins non couverts par `run_yahoo_accumulation`) → `_fetch_news` DB → si ≥1 article 26h trouvé → `gen_ow_both(force_24h=True)`
- **`exchange` dans `ow_cache`** : code yfinance (NMS, NYQ, KSC…). Peuplé à la 1ère visite via trigger dans `get_or_refresh_ow_both()`. One-shot initial : `scripts/populate_exchange.py` (source `articles`). Prévu pour gate market-hours dans `run_hourly_news_refresh()`.

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
| US (S&P500 + NASDAQ) | 260 | Finnhub 12 mois + Yahoo | 12 mois complets |
| EU (Paris/Frankfurt/LSE/Amsterdam/Zurich...) | 227 | Yahoo + Marketaux + NewsAPI (prod) · Marketaux scrap P1→P4 + NewsAPI scrap P1→P4 · Google RSS + AV last resort | Accumulation daily + backfill 3 mois |
| Asie (Japon/HK/Corée/Inde/Taiwan/Australie...) | 230 | Yahoo + Marketaux + NewsAPI (prod) · Marketaux scrap P1→P4 + NewsAPI scrap P1→P4 · Google RSS + AV last resort | Accumulation daily + backfill 3 mois |
| **Total** | **717** | | |

Fichier de référence : `backend/data/tickers.py`

---

### Batch scraper — `backend/scripts/batch_fill_db.py`

Stateless : aucun fichier JSON local. Source de vérité = Supabase (check DB par phase au démarrage).

```bash
python scripts/batch_fill_db.py --phase scrape --region US
python scripts/batch_fill_db.py --phase enrich --region US
python scripts/batch_fill_db.py --phase bubbles --region US --lang en              # normal : skip tickers complets en DB
python scripts/batch_fill_db.py --phase bubbles --region US --lang en --empty-only # tickers ayant ≥1 bub_en NULL
python scripts/batch_fill_db.py --phase bubbles --region US --lang en --force      # regen tous les dots de bub_cache (remplace regen_bubs)
python scripts/batch_fill_db.py --phase bubbles --region all --lang en --force --ticker AAPL
python scripts/batch_fill_db.py --phase all --region US
python scripts/batch_fill_db.py --phase translate-fr --region US
python scripts/batch_fill_db.py --phase translate-fr-dumb --region US
```

**Checks DB par phase (skip logic) :**
| Phase | Condition skip ticker |
|---|---|
| `scrape` | row `articles` existe pour ce ticker + mois courant |
| `enrich` | `ow_cache.ow7d_en IS NOT NULL` |
| `bubbles` normal | `SELECT DISTINCT ticker FROM bub_cache WHERE bub_en IS NOT NULL` |
| `bubbles --empty-only` | tickers avec `bub_en IS NULL` dans bub_cache |
| `bubbles --force` | aucun skip — traite tous les dots existants en bub_cache |
| `translate-fr` | `bub_cache.bub_fr IS NOT NULL` |
| `translate-fr-dumb` | `articles.dumb_fr IS NOT NULL` |

---

### Traduction bulk — `backend/scripts/translate_bulk.py`

```bash
python scripts/translate_bulk.py --region EU           # BUB → Y → M → W, EU uniquement
python scripts/translate_bulk.py --region EU --only y,m  # Y + M seulement
python scripts/translate_bulk.py --ticker AXA.PA       # ticker unique
python scripts/translate_bulk.py --since 2h            # lignes mises à jour dans les 2h
python scripts/translate_bulk.py --dry-run             # compte sans écrire
```

Utilise cascade `promo` (Gemini → raise) via `llm_call` — 3 clés Gemini en rotation.

---

### Batch breakers — `backend/scripts/refresh_breakers.py`

Scrape articles + génère weekly/monthly breakers pour tous les tickers d'une région.

```bash
python scripts/refresh_breakers.py --region US --lang en                           # run complet — scrape P1+P2 → DUMB → W → M
python scripts/refresh_breakers.py --region US --lang en --retry-skipped           # re-scrape ciblé : tickers W less_10_articles ou M absent (mois courant)
python scripts/refresh_breakers.py --region US --lang en --retry-missed            # retraite les (ticker, ym) du scrap_missed_checkpoint.json
python scripts/refresh_breakers.py --region US --lang en --skip-scrape             # DUMB+W+M uniquement, articles déjà frais
python scripts/refresh_breakers.py --region US --lang en --skip-p1                 # saute RSS+Yahoo+Finnhub, garde P2+génération
python scripts/refresh_breakers.py --region US --lang en --force-regen             # régénère W+M même s'ils existent déjà
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
  - < 10 arts → `status=less_10_articles`
  - ≥ 10 arts → LLM → `status=null`
  - Input LLM : **tous les articles de la semaine sans cap** via `_article_text` (headline seul pour RSS, headline + summary pour Yahoo/payants)
- **Gen M** :
  - Skip si un W du mois a `status=scrap_budget`
  - ≥ 3 W `status=null` → `_gen_monthly_from_weeklies` — respecte le `cascade` de l'appelant (était hardcodé `monthly_scrap` → FreeTheAI, corrigé)
  - < 3 W `status=null` → `_gen_monthly_from_raw` (tous les articles du mois sans cap)
  - Cascade M : `promo` dans refresh_breakers — aucun appel FreeTheAI, chemin weeklies inclus

**Input LLM W/M** : `_article_text(a)` — `dumb_en/fr` si disponible, sinon `summary`, sinon `headline` seul. Aucun cap sur le nombre d'articles envoyés. RSS = headline seul (pas de summary), Yahoo/payants = headline + summary. La qty compense le manque de profondeur RSS.

**Parallélisme post-scrap** : `ThreadPoolExecutor(10)` workers · stagger démarrage `index × 0.5s` · `_exec` retry ×5 sur disconnect Supabase (`RemoteProtocolError`, `ReadError`, `ConnectError`) avec backoff exponentiel 3s → 9s → 27s → 81s → 243s → raise · `_retry_call(fn, *args)` : même logique que `_exec` mais pour les fonctions de service (`gen_dumb`, `scrap_gen_monthly`) qui appellent supa directement · Client Supabase HTTP/1.1 forcé dans `_supa()` (postgrest-py hardcode `http2=True` → GOAWAY frames → "Server disconnected" même à 2 workers — fix : remplacer `client.postgrest.session` par `httpx.Client(http2=False)` après création) · Logs : `→ {ticker} [Wx]` au démarrage de chaque worker (Wx = thread index) · Ctrl+C : les threads en cours finissent proprement, les W/M déjà loggés `✓` sont en DB, le reste est retryé au prochain `--skip-scrape`.

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
- **Statuts W** : `null` = généré OK · `less_10_articles` = < 10 arts semaine · `skew` = généré sans articles Finnhub · `scrap_budget` = sources payantes non atteintes
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
| Page profile | ✅ | Devise + compte + sign out |
| News — fetch + drawer + vidéo | ✅ | |
| News — résumés IA | ✅ | Cascade FreeTheAI → Groq |
| Cache news Supabase | ✅ | Pas de TTL — refresh sur nouveaux articles uniquement (crons Yahoo */3h / Finnhub */6h). DUMB dans articles. |
| EN-first migration | ✅ | `LLM_LANG=en`. Tous les services génèrent EN par défaut. Colonnes DB : `bub_cache.cause_en`. |
| Annotations chart (events) | ✅ | Dots peaks/valleys → EventDrawer Finnhub + cause IA |
| Chart — vertical grid lines | ✅ | Tirets par période |
| Lazy loading page stock | ✅ | Default 24H — articles 1M+ et causes events : on-demand |
| Cascade LLM FreeTheAI→Groq | ✅ | gpt-4.1 → gpt-5.4-mini → Hapuppy gpt-4.1-mini → Groq |
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
| Consolidation ticker→name | ✅ | `get_company_name` + `bulk_fetch_company_names` dans `yfinance_service.py` · `_KNOWN_NAMES` 28 tickers EU · `breakers_articles_service` redirige via alias · 7 fichiers mis à jour |
| Reddit sentiment | ⬜ | À venir |
| Refresh proactif OW (crons) | ✅ | Yahoo/Finnhub trigger `gen_ow_both(force_24h=True)` si nouveaux articles |
| Stale-while-revalidate OW | ✅ | Cache retourné immédiatement + background `gen_ow_both(force_24h=True)` + badge `↻ mise à jour...` (vert + spin). Filet 6h : visite page trigger refresh si `cached_at > 6h` même sans nouveaux articles. |

---

### Séquence batch — EN first (relance 2026-06-01)

| # | Phase | Région | Statut |
|---|---|---|---|
| 1 | Bubbles 6M+1Y | US | ✅ |
| 2 | Bubbles 6M+1Y | EU | ✅ |
| 3 | Bubbles 6M+1Y | ASIA | ✅ (208 tickers · 2136 bubbles · 2026-06-03) |
| 4 | Breakers weekly+monthly EN | US | 🔄 AB en cours (237/257 done) · M: 254/257 pour 2026-06 · mois anciens: 76→248/257 · --phase ab à relancer pour W2 2026-06 (243 tickers sans W2) |
| 5 | Breakers weekly+monthly EN | EU | 🔄 en cours · M: 176/227 pour 2026-06 · 46 tickers sans W2 · --phase ab à relancer |
| 6 | Breakers weekly+monthly EN | ASIA | ⏳ |
| 7 | Breakers weekly+monthly FR | US+EU+ASIA | ⏳ après migration colonnes `_fr` |
| — | OW enrich (ow7d_en) | EU · ASIA | ⏭ skip — se remplit via cron 3h + reenrich 22h30 |

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
| **DT1** | `breaks_weekly`, `breaks_monthly`, `breaks_yearly`, `bub_cache` | DONE | `llm_source` → `llm_source_en` + `llm_source_fr` · helper `_llm_source_col()` ajouté dans `breakers_articles_service.py` |
| **DT2** | `breaks_weekly`, `breaks_monthly`, `breaks_yearly` | SKIP | `ai_summary` legacy — DROP quand couverture `bw_en` 100% (dépend DT11) |
| **DT3** | `ow_cache` | DONE | DROP `llm_source` DB + retiré de `_build_response` |
| **DT4** | `ow_cache` | DONE | DROP `ai_sentiment_reason` DB + retiré upserts FR + `_build_response` + type TS |
| **DT5** | `ow_cache` | DONE | DROP `ai_sentiment_reason_24h` idem |
| **DT6** | `ow_cache` | DONE | `cached_at` → `ow1d_cached_at_en` · `ow7d_cached_at` → `ow7d_cached_at_en` · `_fr` ajoutés · MAJ `_is_stale_6h`, upserts, `_build_response`, cron |
| **DT7** | `ow_cache` | DONE | DROP `latest_news_id` DB |
| **DT8** | `bub_cache` | DONE | `cached_at` → `bub_cached_at_en` + `bub_cached_at_fr` ajouté |
| **DT9** | `bub_cache` | DONE | DROP `articles` jsonb + VACUUM FULL · 115 MB → 12 MB · 103 MB libérés |
| **DT10** | `articles` | TODO | `source` + `p2_sources` → `sources[]` · backfill + DROP `source` · MAJ code · non urgent |
| **DT11** | `breaks_weekly` (1041 rows) | SKIP | Rows legacy `bw_en IS NULL` — regen via `refresh_breakers.py --force` (cascade existante) quand opportun |

### Storage DB — état au 2026-06-19

| Table | Avant | Après | Gain |
|---|---|---|---|
| `bub_cache` | 115 MB | 12 MB | -103 MB |
| `articles` | 292 MB | 292 MB | 289 MB de TOAST (vraie donnée JSONB, pas du bloat) |
| Total DB | ~499 MB | ~396 MB | -103 MB |

**Prochaine optimisation : purger `articles` > 12 mois**
- `bub_cache` est purgé > 1 an → articles > 12 mois ne sont plus utilisés pour les BUBs
- BUBs : 12 131 total, 8 598 avec `bub_en` (71%) — couverture à compléter avant purge
- Cutoff recommandé : **12 mois** (pas 18 — trop conservateur)

**Contraintes bloquantes avant toute purge `articles` :**
1. **BUBs** : s'assurer que tous les dots actifs ont un `bub_en` — sinon perte de source. Compléter la génération BUB (3 533 manquants) avant de purger.
2. **W avant digest** : weekly digest (sam 7h) lit `ow_cache.ow7d_en/fr`, pas `breaks_weekly` — pas de conflit. Safe.
3. **Cutoff articles** : **13 mois** (pas 12) — BUBs actifs jusqu'à 12 mois ont besoin d'articles T±6j comme source, buffer 1 mois nécessaire.
4. **Fenêtres display** : 1D = 26h (pas 24h) pour absorber les décalages horaires. 6M/1Y = `directLink=true` (titre + lien direct, pas de drawer, pas de summary). 5Y = zéro article brut.

**Fixes purge W→M et M→Y — logique décidée (à coder) :**

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
