-- ─── Cache news IA par ticker ────────────────────────────────────────────────
create table if not exists stock_news_cache (
  ticker              text primary key,
  news_raw            jsonb not null default '[]',
  ai_summary          text not null default '',
  ai_sentiment        text not null default 'NEUTRAL',
  ai_sentiment_reason text not null default '',
  latest_news_id      text not null default '',
  cached_at           timestamptz not null default now()
);

-- ─── Favoris (pins) ──────────────────────────────────────────────────────────
create table if not exists pinned_stocks (
  id           uuid default gen_random_uuid() primary key,
  user_id      uuid references auth.users not null,
  ticker       text not null,
  company_name text not null,
  pinned_at    timestamptz default now(),
  unique(user_id, ticker)
);

alter table pinned_stocks enable row level security;
create policy "Users manage own pins"
  on pinned_stocks for all
  using (auth.uid() = user_id);

-- ─── Recherches récentes ─────────────────────────────────────────────────────
create table if not exists recent_searches (
  id           uuid default gen_random_uuid() primary key,
  user_id      uuid references auth.users not null,
  ticker       text not null,
  company_name text not null,
  searched_at  timestamptz default now(),
  unique(user_id, ticker)
);

alter table recent_searches enable row level security;
create policy "Users manage own searches"
  on recent_searches for all
  using (auth.uid() = user_id);

-- ─── Stats tickers (refresh proactif plus tard) ───────────────────────────────
create table if not exists ticker_stats (
  ticker       text primary key,
  view_count   integer not null default 0,
  pin_count    integer not null default 0,
  last_refresh timestamptz default now()
);
