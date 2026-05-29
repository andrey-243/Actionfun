# StockVibe — Recap

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
| Cron | APScheduler intégré dans FastAPI — digest gen 18h00 · email 18h30 · weekly lun 3h30 · monthly 1er 4h · yearly 1er jan 5h · yahoo 2h |
| Git | github.com/andrey-243/Actionfun — repo privé |
| Déploiement | local → Railway/Render (backend) · Vercel (frontend) · Supabase (DB) |

## DA

Theme **dark earthy** — bg `#141412` · surfaces `#1E1D1B` · accent vert menthe `#4CAF82` · texte crème `#E8E5DF`.
Vert `#4CAF82` / rouge `#E05C4B` pour les variations de cours.

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
│   │   ├── news_service.py       ← Yahoo news + Groq + cache Supabase
│   │   ├── events_service.py     ← Finnhub news par date + Groq cause (cache mémoire 1 semaine)
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
| Page stock — prix + stats | ✅ | fast_info + t.info (name, PE) |
| Page stock — chart Chart.js | ✅ | |
| Chart — sélecteur de période | ✅ | 24H / 1W / 1M / 6M / 1Y / 5Y (ALL supprimé, 5Y = interval 1wk) |
| Toggle USD ↔ EUR | ✅ | Prix, 52W High/Low, tooltip, axes Y |
| Préférence devise | ✅ | localStorage (non-connecté) · Supabase (connecté) |
| Page profile | ✅ | Devise + compte + sign out |
| News — fetch + drawer + vidéo | ✅ | |
| News — résumés IA | ✅ | Cascade FreeTheAI → Groq (voir section LLM) |
| Cache news Supabase | ✅ | TTL 12h, refresh si nouvelles news |
| Annotations chart (events) | ✅ | Dots peaks/valleys → EventDrawer Finnhub + cause IA. 24H/5Y sans dots. 1W=top 5, 1M=8, 6M/1Y=10 |
| Chart — vertical grid lines | ✅ | Tirets par période : 24H=horaire, 1W=jour, 1M=lundi, 6M/1Y=mois, 5Y=année |
| Lazy loading page stock | ✅ | Default 24H — seul /api/news charge au mount. Articles 1M+ et causes events : on-demand uniquement |
| Cascade LLM FreeTheAI→Groq | ✅ | gpt-4.1 → gpt-5.4-mini → Groq. Notif Telegram si checkin Discord manquant |
| EOD digest — backend | ✅ | gen 18h00 (Finnhub → fallback Yahoo cache) · email 18h30 · 2 jobs APScheduler séparés |
| EOD digest — email | ✅ | Resend — clé configurée, sandbox `onboarding@resend.dev` (passer à domaine custom en prod) |
| EOD digest — onglet "Mes news" | ✅ | Page /my-news : 2 tabs Daily (daily_digest) + Weekly (article_week_summaries) · logo Logo.dev |
| Préférences notif (email on/off + par ticker) | ✅ | Dans /profile : toggle global + chips par action |
| Batch DB filler (717 tickers) | 🔄 | Scrape US+EU+Asia terminé (717 tickers). Enrich US en cours (FreeTheAI). Phase enrich populate aussi `website` pour Logo.dev. Progress dans `batch_progress.json`. Notifs Telegram tous les 25 tickers |
| Loading animation — DogLoader | ✅ | Sleeping dog SVG, 2 comètes sens inverse, rotation 3D lente. Actif sur newsLoading + periodLoading (page stock) + EventDrawer |
| DB articles — rotation & purge | ✅ | 5W max, monthly calendaire 1er du mois, yearly purge monthlies |
| Monthly on-demand (premium) | ✅ | `get_monthly_ondemand()` — rolling 4 weeklies → LLM, pas de cache persistant |
| Articles 1M/6M/1Y — sections par période | ✅ | 1M=semaines (skew detection), 6M/1Y=mois. Rendu avec résumé AI si dispo + articles compacts |
| Articles multi-source | ✅ | Dedup par headline à chaque fetch Finnhub. `extra_sources` stockés. Badges colorés + multi-CTA dans drawer |
| Reddit sentiment | ⬜ | À venir |
| Refresh proactif (cron) | ⬜ | Pour tickers populaires/pinnés |

## LLM — Cascade & modèles

Tous les appels LLM passent par `services/llm_service.py` qui gère la cascade automatiquement.

**Cascade :**
```
FreeTheAI: bbl/gpt-4.1          → primary (JSON propre, qualité max)
FreeTheAI: bbl/gpt-5.4-mini     → secondary si 429/502/timeout sur primary
Groq: voir tableau ci-dessous    → fallback si 403 checkin Discord manquant
```

**Comportement 403 (check-in Discord manquant) :**
- FreeTheAI désactivé jusqu'au prochain minuit UTC
- Notif Telegram automatique → @mcorp1290_bot
- Faire `/checkin` sur le serveur Discord FreeTheAI pour débloquer

**Mapping modèles par usage :**

| Usage | FreeTheAI | Groq fallback |
|---|---|---|
| BRUTALIZE (30 articles → résumé 7J) | `bbl/gpt-4.1` | `llama-4-scout-17b` |
| SUMMARY_24H | `bbl/gpt-4.1` | `llama-3.1-8b-instant` |
| EVENT_CAUSE (dot chart) | `bbl/gpt-4.1` | `llama-3.1-8b-instant` |
| DIGEST EOD | `bbl/gpt-4.1` | `llama-4-scout-17b` |
| WEEKLY / MONTHLY / YEARLY condensation | `bbl/gpt-4.1` | `llama-3.3-70b-versatile` |

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

**Vars d'env requises :**
```
GROQ_API_KEY=...
FREETHEAI_API_KEY=sta_...     # /checkin Discord chaque jour UTC
TELEGRAM_BOT_TOKEN=...        # @mcorp1290_bot
TELEGRAM_CHAT_ID=...          # notifs check-in manquant
```

## Post-beta — Roadmap

| Feature | Priorité | Notes |
|---|---|---|
| Descriptions intelligentes des tickers | 🔜 | Voir détail ci-dessous |
| AI Overview toggle FR/EN | 🔜 | Préférence dans /profile, stockée en Supabase |

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
stock_news_cache        — cache news IA Yahoo (ticker PK, news_raw, ai_summary, ai_summary_24h, ai_sentiment, ai_sentiment_24h, cached_at, website) — TTL 12h, ~4 jours
user_preferences        — préférences (user_id PK, currency, notify_email, digest_tickers text[])
pinned_stocks           — watchlist (user_id, ticker, company_name, pinned_at) — unique(user_id, ticker)
daily_digest            — digest EOD (ticker, date, articles, summary, sentiment) — purge auto 30J
event_causes            — causes AI événements chart (ticker, date PK, articles, cause) — permanent
article_archive         — raw articles (ticker, year_month PK, articles JSONB, source: "finnhub"|"yahoo") — mois actif refresh 24h, passés permanent
article_week_summaries  — résumé AI + top 10 articles par semaine (ticker, year_month, week_num PK, ai_summary, top_articles)
article_month_summaries — résumé AI par mois (ticker, year_month PK, ai_summary) — généré depuis weekly summaries
article_year_summaries  — résumé AI par année (ticker, year PK, ai_summary) — généré depuis monthly summaries
```

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
python scripts/batch_fill_db.py --phase scrape

# Enrichissement AI (news cache 24H + 7J + website Logo.dev)
python scripts/batch_fill_db.py --phase enrich

# Tout (scrape + enrich)
python scripts/batch_fill_db.py --phase all --region US

# Reprend automatiquement où il s'est arrêté (batch_progress.json)
```

**Script one-shot websites** : `backend/scripts/populate_websites.py`
```bash
python scripts/populate_websites.py            # tous les tickers en stock_news_cache
python scripts/populate_websites.py --ticker AMZN  # un seul ticker
```
À relancer après chaque phase enrich si besoin de rattraper des tickers.

## Articles par période

| Période | Source | Format affiché |
|---|---|---|
| 1D | Yahoo (`stock_news_cache`) | Flat list articles < 24h + résumé AI 24h |
| 1W | Yahoo (`stock_news_cache`) | Max 3/jour, 7 jours + résumé AI 7J |
| 1M | `article_archive` (Finnhub) | Sections W1/W2/W3/W4 groupées par date réelle. Si skewed → section merged + warning "pas de récap hebdo" |
| 6M | `article_archive` (Finnhub) | Sections par mois (year_month) · résumé AI si dispo · articles compacts multi-source |
| 1Y | `article_archive` (Finnhub) | Sections par mois (year_month) · résumé AI si dispo · articles compacts multi-source |
| 5Y | `article_year_summaries` | Yearly summaries + mois sans yearly |

**Problème connu — Finnhub date clustering :**
Finnhub indexe les articles avec sa date de crawl, pas la date de publication. Pour ~35% des mois, 80%+ des articles tombent en W4. Détection automatique côté backend : si skewed → section merged "WX→WY · pas de récap hebdomadaire disponible". Fiable par mois (year_month défini par nous), pas par semaine.

**Semaines fixes intra-mois :**
- W1 = jours 1–7 · W2 = jours 8–14 · W3 = jours 15–21 · W4 = jours 22–fin du mois

**Script condensation manuelle :** `backend/scripts/condense_ticker.py`
```bash
python scripts/condense_ticker.py AAPL --months 6  # weeklies + monthlies
python scripts/condense_ticker.py AAPL --weekly    # weeklies seulement
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
- **Scope** : uniquement pour 1M/6M/1Y (article_archive). 24H/1W (stock_news_cache Yahoo) n'a pas de multi-source.

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

**Cron daily d'accumulation Yahoo** (tickers non couverts par Finnhub, pinnés ≥ 3 users) :
- Fetch `get_news(count=100)` → compare par URL avec `article_archive` → insert uniquement les nouveaux articles

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
- **Filtre pertinence** : le LLM marque les articles hors-sujet "SKIP" → exclus du cache avant stockage
- **2 calls Groq par refresh :**
  - **Call 1 (lourd)** — 30 articles bruts → brutalised par article (2-3 phrases, FR) + résumé 7J + sentiment. Contexte injecté : perf semaine, range 5J, volume vs moyenne + benchmark adapté (S&P / CAC 40 / DAX / FTSE selon l'exchange).
  - **Call 2 (léger)** — brutalised des articles <24h → résumé 24H + sentiment 24H. Contexte injecté : change %, range du jour, volume vs moyenne + benchmark adapté.
- **AI Overview affiché selon la période** : 24H → `summary_24h` · 1W → `summary` (7J) · 1M+ → pas d'overview Yahoo
- **Un seul bloc de texte** : `sentiment_reason` stocké en DB mais non affiché (badge suffit)
- **Cache logic** : TTL 12h · si `latest_news_id` inchangé → juste refresh timestamp · si `summary_24h` absent → force re-génération

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

