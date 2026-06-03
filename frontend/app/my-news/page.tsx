"use client";

import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import { createClient } from "../lib/supabase";
import { sourceStyle } from "../components/ArticleDrawer";
import { NavBrand, BackButton, HEADER_STYLE, HEADER_INNER } from "../components/Navbar";

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
interface DigestArticle {
  title: string;
  source: string;
  url: string;
  pub_date: string;
}

interface Digest {
  ticker: string;
  summary_24h: string;
  sentiment_24h: string;
  articles?: DigestArticle[];
  website?: string | null;
  cached_at?: string | null;
}

interface WeeklyArticle {
  title: string;
  url: string;
  source?: string;
  pub_date?: string;
}

interface Weekly {
  ticker: string;
  year_month: string | null;
  week_num: number | null;
  ai_summary: string;
  top_articles: WeeklyArticle[];
  website?: string | null;
  condensed_at?: string | null;
  generating?: boolean;
}

const SENTIMENT_STYLE: Record<string, { bg: string; color: string }> = {
  BULLISH: { bg: "var(--color-positive-bg)", color: "var(--color-positive)" },
  BEARISH: { bg: "var(--color-negative-bg)", color: "var(--color-negative)" },
  NEUTRAL: { bg: "var(--color-surface-2)", color: "var(--color-text-muted)" },
};

const LOGODEV_TOKEN = process.env.NEXT_PUBLIC_LOGODEV_TOKEN ?? "";

function formatAge(isoTimestamp: string | null | undefined): string | null {
  if (!isoTimestamp) return null;
  const diff = Date.now() - new Date(isoTimestamp).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 60) return `il y a ${mins}min`;
  const hrs = Math.floor(diff / 3600000);
  if (hrs < 24) return `il y a ${hrs}h`;
  const days = Math.floor(diff / 86400000);
  return `il y a ${days}j`;
}

function TickerLogo({ ticker, website, onLogoClick }: { ticker?: string | null; website?: string | null; onLogoClick?: () => void }) {
  const [failed, setFailed] = useState(false);
  const src = !failed && website
    ? `https://img.logo.dev/${website}?token=${LOGODEV_TOKEN}&size=40`
    : null;

  const fallbackLabel = ticker ? ticker.slice(0, 3) : "?";
  const handleClick = onLogoClick
    ? (e: React.MouseEvent) => { e.stopPropagation(); onLogoClick(); }
    : undefined;

  const style: React.CSSProperties = {
    width: "32px", height: "32px", borderRadius: "8px", flexShrink: 0,
    cursor: onLogoClick ? "pointer" : "default",
  };

  if (!src) {
    return (
      <div
        onClick={handleClick}
        style={{
          ...style,
          background: "var(--color-surface-2)", border: "1px solid var(--color-border)",
          display: "flex", alignItems: "center", justifyContent: "center",
          fontFamily: "var(--font-mono)", fontWeight: 700, fontSize: "10px",
          color: "var(--color-text-muted)", letterSpacing: "0.02em",
        }}
      >
        {fallbackLabel}
      </div>
    );
  }
  return (
    <img
      src={src}
      alt={ticker ?? undefined}
      onClick={handleClick}
      onError={() => setFailed(true)}
      style={{ ...style, objectFit: "contain", background: "#fff" }}
    />
  );
}

function DigestCard({ digest }: { digest: Digest }) {
  const router = useRouter();
  const [expanded, setExpanded] = useState(false);
  const s = SENTIMENT_STYLE[digest.sentiment_24h] ?? SENTIMENT_STYLE.NEUTRAL;

  const cutoff24h = Date.now() - 24 * 3600 * 1000;
  const articles24h = (digest.articles ?? []).filter((a) => {
    try { return new Date(a.pub_date).getTime() >= cutoff24h; } catch { return false; }
  });
  const hasArticles = articles24h.length > 0;

  const owAgeMs = digest.cached_at ? Date.now() - new Date(digest.cached_at).getTime() : Infinity;
  const owAgeLabel = formatAge(digest.cached_at);
  const dotClass = owAgeMs < 6 * 3600000 ? "fresh" : owAgeMs < 24 * 3600000 ? "medium" : "stale";

  return (
    <div style={{
      background: "var(--color-surface)", border: "1px solid var(--color-border)",
      borderRadius: "10px", overflow: "hidden", marginBottom: "16px",
    }}>
      <div
        onClick={() => hasArticles && setExpanded((v) => !v)}
        style={{ padding: "16px 18px", cursor: hasArticles ? "pointer" : "default", display: "flex", alignItems: "flex-start", gap: "12px" }}
      >
        <TickerLogo
          ticker={digest.ticker}
          website={digest.website}
          onLogoClick={() => router.push(`/stock/${digest.ticker}`)}
        />
        <div style={{ flex: 1 }}>
          <div style={{ display: "flex", alignItems: "center", gap: "8px", marginBottom: "8px", flexWrap: "wrap" }}>
            <span style={{ fontFamily: "var(--font-mono)", fontWeight: 800, fontSize: "15px", color: "var(--color-text-primary)" }}>
              {digest.ticker}
            </span>
            <span style={{
              padding: "2px 8px", borderRadius: "4px", fontSize: "10px", fontWeight: 700,
              fontFamily: "var(--font-mono)", letterSpacing: "0.06em",
              background: s.bg, color: s.color,
            }}>
              {digest.sentiment_24h}
            </span>
            {hasArticles && (
              <span style={{ fontSize: "11px", color: "var(--color-text-muted)", fontFamily: "var(--font-mono)" }}>
                {articles24h.length} article{articles24h.length > 1 ? "s" : ""} 24h
              </span>
            )}
            {owAgeLabel && (
              <span style={{ display: "flex", alignItems: "center", gap: "5px", marginLeft: "auto" }}>
                <span className={`freshness-dot ${dotClass}`} />
                <span style={{ fontSize: "11px", color: "var(--color-text-muted)" }}>{owAgeLabel}</span>
              </span>
            )}
          </div>
          <p style={{ fontSize: "13px", color: "var(--color-text-secondary)", lineHeight: 1.6, margin: 0 }}>
            {digest.summary_24h || "Pas de résumé disponible."}
          </p>
        </div>
        {hasArticles && (
          <svg
            width="14" height="14" viewBox="0 0 24 24" fill="none"
            stroke="var(--color-text-muted)" strokeWidth="2"
            style={{ flexShrink: 0, marginTop: "4px", transform: expanded ? "rotate(180deg)" : "none", transition: "transform 0.2s" }}
          >
            <polyline points="6 9 12 15 18 9" />
          </svg>
        )}
      </div>

      {expanded && hasArticles && (
        <div style={{ borderTop: "1px solid var(--color-border)" }}>
          {articles24h.map((a, i) => (
            <a
              key={i}
              href={a.url}
              target="_blank"
              rel="noopener noreferrer"
              style={{
                display: "block", padding: "11px 18px", textDecoration: "none",
                borderBottom: i < articles24h.length - 1 ? "1px solid var(--color-border)" : "none",
              }}
              onMouseEnter={(e) => (e.currentTarget.style.background = "var(--color-surface-2)")}
              onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
            >
              <div style={{ fontSize: "13px", color: "var(--color-text-primary)", fontWeight: 500, marginBottom: "3px" }}>
                {a.title}
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: "6px", marginTop: "3px" }}>
                {a.source && (() => { const st = sourceStyle(a.source); return (
                  <span style={{ padding: "1px 6px", borderRadius: "3px", fontSize: "10px", fontWeight: 700, letterSpacing: "0.04em", background: st.bg, color: st.text }}>
                    {a.source}
                  </span>
                ); })()}
                <span style={{ fontSize: "11px", color: "var(--color-text-muted)" }}>
                  {new Date(a.pub_date).toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" })}
                </span>
              </div>
            </a>
          ))}
        </div>
      )}
    </div>
  );
}

function WeeklyCard({ weekly }: { weekly: Weekly }) {
  const router = useRouter();
  const [expanded, setExpanded] = useState(false);
  const articles = weekly.top_articles ?? [];

  const label = weekly.year_month && weekly.week_num != null
    ? `S${weekly.week_num} · ${weekly.year_month}`
    : null;

  return (
    <div style={{
      background: "var(--color-surface)", border: "1px solid var(--color-border)",
      borderRadius: "10px", overflow: "hidden", marginBottom: "16px",
    }}>
      <div
        onClick={() => articles.length > 0 && setExpanded((v) => !v)}
        style={{ padding: "16px 18px", cursor: articles.length > 0 ? "pointer" : "default", display: "flex", alignItems: "flex-start", gap: "12px" }}
      >
        <TickerLogo
          ticker={weekly.ticker}
          website={weekly.website}
          onLogoClick={() => router.push(`/stock/${weekly.ticker}`)}
        />
        <div style={{ flex: 1 }}>
          <div style={{ display: "flex", alignItems: "center", gap: "8px", marginBottom: "8px", flexWrap: "wrap" }}>
            <span style={{ fontFamily: "var(--font-mono)", fontWeight: 800, fontSize: "15px", color: "var(--color-text-primary)" }}>
              {weekly.ticker}
            </span>
            {label && (
              <span style={{
                padding: "2px 8px", borderRadius: "4px", fontSize: "10px", fontWeight: 700,
                fontFamily: "var(--font-mono)", letterSpacing: "0.06em",
                background: "var(--color-surface-2)", color: "var(--color-text-muted)",
              }}>
                {label}
              </span>
            )}
            {weekly.condensed_at && (() => {
              const ageMs = Date.now() - new Date(weekly.condensed_at).getTime();
              const dot = ageMs < 6 * 3600000 ? "fresh" : ageMs < 7 * 86400000 ? "medium" : "stale";
              const label = formatAge(weekly.condensed_at);
              return (
                <span style={{ display: "flex", alignItems: "center", gap: "5px", marginLeft: "auto" }}>
                  <span className={`freshness-dot ${dot}`} />
                  <span style={{ fontSize: "11px", color: "var(--color-text-muted)" }}>{label}</span>
                </span>
              );
            })()}
          </div>
          {weekly.generating ? (
            <p style={{ fontSize: "13px", color: "var(--color-text-muted)", lineHeight: 1.6, margin: 0, fontStyle: "italic" }}>
              Résumé IA en cours de génération…
            </p>
          ) : (
            <p style={{ fontSize: "13px", color: "var(--color-text-secondary)", lineHeight: 1.6, margin: 0 }}>
              {weekly.ai_summary || "Résumé hebdomadaire non disponible."}
            </p>
          )}
        </div>
        {articles.length > 0 && (
          <svg
            width="14" height="14" viewBox="0 0 24 24" fill="none"
            stroke="var(--color-text-muted)" strokeWidth="2"
            style={{ flexShrink: 0, marginTop: "4px", transform: expanded ? "rotate(180deg)" : "none", transition: "transform 0.2s" }}
          >
            <polyline points="6 9 12 15 18 9" />
          </svg>
        )}
      </div>

      {expanded && articles.length > 0 && (
        <div style={{ borderTop: "1px solid var(--color-border)" }}>
          {articles.map((a, i) => (
            <a
              key={i}
              href={a.url}
              target="_blank"
              rel="noopener noreferrer"
              style={{
                display: "block", padding: "11px 18px", textDecoration: "none",
                borderBottom: i < articles.length - 1 ? "1px solid var(--color-border)" : "none",
              }}
              onMouseEnter={(e) => (e.currentTarget.style.background = "var(--color-surface-2)")}
              onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
            >
              <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                <span style={{ fontSize: "13px", color: "var(--color-text-primary)", fontWeight: 500, flex: 1 }}>
                  {a.title}
                </span>
                <div style={{ display: "flex", alignItems: "center", gap: "6px", flexShrink: 0 }}>
                  {a.source && (() => { const st = sourceStyle(a.source); return (
                    <span style={{ padding: "1px 6px", borderRadius: "3px", fontSize: "10px", fontWeight: 700, letterSpacing: "0.04em", background: st.bg, color: st.text }}>
                      {a.source}
                    </span>
                  ); })()}
                  {a.pub_date && (
                    <span style={{ fontSize: "11px", color: "var(--color-text-muted)" }}>
                      {new Date(a.pub_date).toLocaleDateString("fr-FR", { day: "numeric", month: "short" })}
                    </span>
                  )}
                </div>
              </div>
            </a>
          ))}
        </div>
      )}
    </div>
  );
}

type Tab = "daily" | "weekly";

export default function MyNewsPage() {
  const router = useRouter();
  const [pins, setPins] = useState<string[]>([]);
  const [tab, setTab] = useState<Tab>("daily");
  const [digests, setDigests] = useState<Digest[]>([]);
  const [weeklies, setWeeklies] = useState<Weekly[]>([]);
  const [loadingDaily, setLoadingDaily] = useState(true);
  const [loadingWeekly, setLoadingWeekly] = useState(false);
  const [weeklyFetched, setWeeklyFetched] = useState(false);
  const [authed, setAuthed] = useState<boolean | null>(null);

  useEffect(() => {
    const supa = createClient();
    if (!supa) { setAuthed(false); setLoadingDaily(false); return; }

    supa.auth.getUser().then(async ({ data }) => {
      const u = data.user;
      if (!u) { setAuthed(false); setLoadingDaily(false); return; }
      setAuthed(true);

      const { data: pinData } = await supa
        .from("pinned_stocks")
        .select("ticker")
        .eq("user_id", u.id);

      const tickers = (pinData ?? []).map((r: { ticker: string }) => r.ticker);
      setPins(tickers);

      if (tickers.length === 0) { setLoadingDaily(false); return; }

      const results = await Promise.all(
        tickers.map((t) =>
          fetch(`${API}/api/news/${t}`)
            .then((r) => r.json())
            .catch(() => null)
        )
      );
      const valid = results.filter((d): d is Digest => d !== null && !!d.ticker && !!d.summary_24h);
      setDigests(valid);

      setLoadingDaily(false);
    });
  }, []);

  async function fetchWeeklies() {
    if (weeklyFetched || pins.length === 0) return;
    setLoadingWeekly(true);
    const results = await Promise.all(
      pins.map((t) =>
        fetch(`${API}/api/weekly/${t}`)
          .then((r) => r.json())
          .catch(() => null)
      )
    );
    setWeeklies(results.filter((w): w is Weekly => w !== null && !!w.ticker && !!w.ai_summary));
    setLoadingWeekly(false);
    setWeeklyFetched(true);
  }

  function handleTab(t: Tab) {
    setTab(t);
    if (t === "weekly") fetchWeeklies();
  }

  const loading = tab === "daily" ? loadingDaily : loadingWeekly;

  const TAB_ITEM = (active: boolean): React.CSSProperties => ({
    padding: "5px 14px",
    borderRadius: "16px",
    fontSize: "12px",
    fontWeight: 600,
    background: active ? "var(--color-accent)" : "transparent",
    color: active ? "#fff" : "var(--color-accent)",
    transition: "background 0.15s, color 0.15s",
    pointerEvents: "none",
  });

  function getCurrentWeekLabel(): string {
    const now = new Date();
    const day = now.getDay();
    const diffToMon = (day === 0 ? -6 : 1 - day);
    const mon = new Date(now); mon.setDate(now.getDate() + diffToMon);
    const sun = new Date(mon); sun.setDate(mon.getDate() + 6);
    const fmt = (d: Date) => d.toLocaleDateString("fr-FR", { day: "numeric", month: "long" });
    if (mon.getMonth() === sun.getMonth()) {
      return `${mon.getDate()}–${fmt(sun)}`;
    }
    return `${fmt(mon)} – ${fmt(sun)}`;
  }

  return (
    <div style={{ minHeight: "100vh", background: "var(--color-bg)" }}>
      <header style={HEADER_STYLE}>
        <div style={{ ...HEADER_INNER, justifyContent: "space-between" }}>
          <div style={{ display: "flex", alignItems: "center", gap: "4px" }}>
            <BackButton />
            <NavBrand />
          </div>
          <div
            onClick={() => handleTab(tab === "daily" ? "weekly" : "daily")}
            style={{
              display: "flex", gap: "2px",
              background: "rgba(76,175,130,0.10)",
              border: "1px solid rgba(76,175,130,0.28)",
              borderRadius: "20px", padding: "3px",
              cursor: "pointer", userSelect: "none",
            }}
          >
            <span style={TAB_ITEM(tab === "daily")}>Daily</span>
            <span style={TAB_ITEM(tab === "weekly")}>Weekly</span>
          </div>
        </div>
      </header>

      <main style={{ maxWidth: "880px", margin: "0 auto", padding: "24px 20px 80px" }}>
        {authed === false ? (
          <div style={{ textAlign: "center", padding: "60px 0" }}>
            <p style={{ fontSize: "14px", color: "var(--color-text-secondary)", marginBottom: "16px" }}>
              Connecte-toi pour voir le digest de tes actions suivies.
            </p>
            <button
              onClick={() => router.push("/")}
              style={{
                padding: "8px 20px", borderRadius: "6px", fontSize: "13px", fontWeight: 600,
                background: "var(--color-accent)", color: "#fff", border: "none", cursor: "pointer",
              }}
            >
              Retour à l'accueil
            </button>
          </div>
        ) : loading ? (
          <div style={{ textAlign: "center", padding: "60px 0", color: "var(--color-text-muted)", fontSize: "13px" }}>
            Chargement…
          </div>
        ) : pins.length === 0 ? (
          <div style={{ textAlign: "center", padding: "60px 0" }}>
            <p style={{ fontSize: "14px", color: "var(--color-text-secondary)" }}>
              Tu ne suis aucune action pour l'instant.
            </p>
          </div>
        ) : tab === "daily" ? (
          digests.length === 0 ? (
            <div style={{ textAlign: "center", padding: "60px 0", color: "var(--color-text-muted)", fontSize: "13px" }}>
              Pas de news disponible aujourd'hui pour tes actions.
            </div>
          ) : (
            <>
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "16px" }}>
                <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                  <span style={{ fontSize: "11px", fontWeight: 700, letterSpacing: "0.06em", textTransform: "uppercase", color: "var(--color-text-muted)" }}>
                    {digests.length} action{digests.length > 1 ? "s" : ""} · digest du jour
                  </span>
                  <span style={{
                    padding: "1px 6px", borderRadius: "4px", fontSize: "9px", fontWeight: 700,
                    fontFamily: "var(--font-mono)", letterSpacing: "0.06em", textTransform: "uppercase",
                    background: "rgba(255,180,0,0.10)", color: "#C8960C",
                  }}>Premium</span>
                </div>
                <span style={{ fontSize: "11px", color: "var(--color-text-muted)", fontFamily: "var(--font-mono)" }}>
                  {new Date().toLocaleDateString("fr-FR", { weekday: "long", day: "numeric", month: "long" })}
                </span>
              </div>
              {digests.map((d, i) => <DigestCard key={d.ticker ?? i} digest={d} />)}
            </>
          )
        ) : (
          weeklies.length === 0 ? (
            <div style={{ textAlign: "center", padding: "60px 0", color: "var(--color-text-muted)", fontSize: "13px" }}>
              Pas de résumé hebdomadaire disponible pour tes actions.
            </div>
          ) : (
            <>
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "16px" }}>
                <span style={{ fontSize: "11px", fontWeight: 700, letterSpacing: "0.06em", textTransform: "uppercase", color: "var(--color-text-muted)" }}>
                  {weeklies.length} action{weeklies.length > 1 ? "s" : ""} · recap de la semaine
                </span>
                <span style={{ fontSize: "11px", color: "var(--color-text-muted)", fontFamily: "var(--font-mono)" }}>
                  {getCurrentWeekLabel()}
                </span>
              </div>
              {weeklies.map((w, i) => <WeeklyCard key={w.ticker ?? i} weekly={w} />)}
            </>
          )
        )}
      </main>
    </div>
  );
}
