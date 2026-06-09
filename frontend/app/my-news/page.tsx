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
  dumb_en?: string;
  dumb_fr?: string;
}

interface Digest {
  ticker: string;
  summary_24h: string;
  sentiment_24h: string;
  summary?: string;
  no_24h_news?: boolean;
  articles?: DigestArticle[];
  website?: string | null;
  cached_at?: string | null;
}

interface WeeklyArticle {
  title: string;
  url: string;
  source?: string;
  pub_date?: string;
  dumb_en?: string;
  dumb_fr?: string;
}

interface Weekly {
  ticker: string;
  ai_summary: string;
  top_articles: WeeklyArticle[];
  website?: string | null;
  cached_at?: string | null;
  generating?: boolean;
}

const SENTIMENT_STYLE: Record<string, { bg: string; color: string }> = {
  BULLISH: { bg: "var(--color-positive-bg)", color: "var(--color-positive)" },
  BEARISH: { bg: "var(--color-negative-bg)", color: "var(--color-negative)" },
  NEUTRAL: { bg: "var(--color-surface-2)", color: "var(--color-text-muted)" },
};

const LOGODEV_TOKEN = process.env.NEXT_PUBLIC_LOGODEV_TOKEN ?? "";


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

function ArticleRow({ title, source, pub_date, url, dumb }: {
  title: string;
  source?: string;
  pub_date?: string;
  url?: string;
  dumb?: string;
}) {
  const [expanded, setExpanded] = useState(false);
  const [btnHovered, setBtnHovered] = useState(false);
  const hasDumb = !!dumb;

  const ago = pub_date ? (() => {
    const d = new Date(pub_date);
    const diff = Date.now() - d.getTime();
    const h = Math.floor(diff / 3600000);
    if (h < 1) return `${Math.floor(diff / 60000)}m`;
    if (h < 24) return `${h}h`;
    return `${Math.floor(diff / 86400000)}j`;
  })() : null;

  const sourceBadge = source ? (() => {
    const st = sourceStyle(source);
    return (
      <span style={{ padding: "1px 6px", borderRadius: "3px", fontSize: "10px", fontWeight: 700, letterSpacing: "0.04em", background: st.bg, color: st.text, whiteSpace: "nowrap" }}>
        {source.length > 12 ? source.slice(0, 12) + "…" : source}
      </span>
    );
  })() : null;

  return (
    <div style={{ borderBottom: "1px solid var(--color-border)" }}>
      <div style={{ display: "flex", alignItems: "center" }}>
        {/* titre + méta inline */}
        <a
          href={url}
          target="_blank"
          rel="noopener noreferrer"
          style={{ flex: 1, minWidth: 0, padding: "8px 12px", textDecoration: "none", display: "flex", alignItems: "center", gap: "8px" }}
        >
          <span style={{ flex: 1, minWidth: 0, fontSize: "11px", fontWeight: 500, color: "var(--color-text-primary)", lineHeight: 1.35 }}>
            {title}
          </span>
          <div style={{ display: "flex", alignItems: "center", gap: "5px", flexShrink: 0 }}>
            {sourceBadge}
            {ago && <span style={{ fontSize: "10px", color: "var(--color-text-muted)" }}>{ago}</span>}
          </div>
        </a>
        {hasDumb && (
          <button
            onClick={(e) => { e.preventDefault(); setExpanded(v => !v); }}
            onMouseEnter={() => setBtnHovered(true)}
            onMouseLeave={() => setBtnHovered(false)}
            style={{
              flexShrink: 0, marginRight: "12px",
              display: "flex", alignItems: "center", justifyContent: "center",
              width: "22px", height: "22px", borderRadius: "50%",
              background: btnHovered ? "var(--color-accent)" : "rgba(76,175,130,0.15)",
              border: "none",
              cursor: "pointer", transition: "background 0.15s",
            }}
          >
            <svg
              width="10" height="10" viewBox="0 0 24 24" fill="none"
              stroke={btnHovered ? "#fff" : "var(--color-accent)"} strokeWidth="2.5"
              style={{ transform: expanded ? "rotate(90deg)" : "none", transition: "transform 0.15s" }}
            >
              <polyline points="9 18 15 12 9 6" />
            </svg>
          </button>
        )}
      </div>
      {expanded && dumb && (
        <div style={{ padding: "6px 12px 10px", borderTop: "1px solid var(--color-border)", background: "rgba(76,175,130,0.04)" }}>
          <p style={{ margin: 0, fontSize: "12px", color: "var(--color-accent)", lineHeight: 1.5 }}>
            {dumb}
          </p>
        </div>
      )}
    </div>
  );
}

function shortAge(ms: number): string {
  if (ms === Infinity || ms < 0) return "—";
  if (ms < 3600000) return `${Math.floor(ms / 60000)}m`;
  if (ms < 86400000) return `${Math.floor(ms / 3600000)}h`;
  return `${Math.floor(ms / 86400000)}j`;
}

const DAILY_LIMIT = 5;
const WEEKLY_LIMIT = 10;

function DigestCard({ digest }: { digest: Digest }) {
  const router = useRouter();
  const [expanded, setExpanded] = useState(false);
  const [showAll, setShowAll] = useState(false);
  const s = SENTIMENT_STYLE[digest.sentiment_24h] ?? SENTIMENT_STYLE.NEUTRAL;

  const cutoff24h = Date.now() - 26 * 3600 * 1000;
  const articles24h = (digest.articles ?? []).filter((a) => {
    try { return new Date(a.pub_date).getTime() >= cutoff24h; } catch { return false; }
  });
  const hasArticles = articles24h.length > 0;

  const owAgeMs = digest.cached_at ? Date.now() - new Date(digest.cached_at).getTime() : Infinity;
  const noNews = digest.no_24h_news ?? !hasArticles;

  const lastArticle = (digest.articles ?? [])[0];
  const lastNewsMs = lastArticle ? Date.now() - new Date(lastArticle.pub_date).getTime() : Infinity;
  const staleLevel = noNews
    ? lastNewsMs < 3 * 3600000 ? "warn"
    : lastNewsMs < 6 * 3600000 ? "alert"
    : "critical"
    : null;

  const STALE_COLORS: Record<string, { glow: string; text: string; bg: string }> = {
    warn:     { glow: "rgba(245,197,24,0.9)",  text: "#F5C518", bg: "rgba(245,197,24,0.1)"  },
    alert:    { glow: "rgba(255,140,0,0.9)",   text: "#FF8C00", bg: "rgba(255,140,0,0.1)"   },
    critical: { glow: "rgba(224,92,75,0.9)",   text: "#E05C4B", bg: "rgba(224,92,75,0.1)"   },
  };
  const sc = staleLevel ? STALE_COLORS[staleLevel] : null;

  // Freshness du check : vert < 6h · amber 6-24h · rouge > 24h
  const syncColor = owAgeMs < 6 * 3600000
    ? { bg: "rgba(76,175,130,0.12)",  color: "#4CAF82", border: "1px solid rgba(76,175,130,0.2)" }
    : owAgeMs < 24 * 3600000
    ? { bg: "rgba(245,197,24,0.08)",  color: "#C8960C", border: "1px solid rgba(245,197,24,0.15)" }
    : { bg: "rgba(224,92,75,0.08)",   color: "#E05C4B", border: "1px solid rgba(224,92,75,0.15)" };

  const chipBase: React.CSSProperties = {
    padding: "2px 7px", borderRadius: "4px", fontSize: "9px",
    fontWeight: 600, fontFamily: "var(--font-mono)", letterSpacing: "0.04em",
  };

  return (
    <div
      className={staleLevel ? "neon-border-rotate" : ""}
      style={{
        background: staleLevel ? undefined : "var(--color-surface)",
        border: staleLevel ? undefined : "1px solid var(--color-border)",
        borderRadius: "10px", overflow: "hidden", marginBottom: "16px",
        ...(sc ? { "--glow-color": sc.glow } as React.CSSProperties : {}),
      }}
    >
      <div
        onClick={() => hasArticles && setExpanded((v) => !v)}
        style={{ padding: "16px 18px", cursor: hasArticles ? "pointer" : "default", display: "flex", alignItems: "flex-start", gap: "12px" }}
      >
        <TickerLogo
          ticker={digest.ticker}
          website={digest.website}
          onLogoClick={() => router.push(`/stock/${digest.ticker}`)}
        />
        <div style={{ flex: 1, minWidth: 0 }}>
          {/* Ticker · badge sentiment/NO NEWS · chips poussées à droite */}
          <div style={{ display: "flex", alignItems: "center", gap: "7px", marginBottom: "8px" }}>
            <span style={{ fontFamily: "var(--font-mono)", fontWeight: 800, fontSize: "15px", color: "var(--color-text-primary)" }}>
              {digest.ticker}
            </span>
            {hasArticles ? (
              <span style={{ ...chipBase, fontSize: "10px", background: s.bg, color: s.color }}>
                {digest.sentiment_24h}
              </span>
            ) : (
              <span style={{
                ...chipBase, fontSize: "10px",
                background: sc?.bg ?? "rgba(128,128,128,0.1)",
                border: `1px solid ${sc?.text ?? "var(--color-text-muted)"}`,
                color: sc?.text ?? "var(--color-text-muted)",
              }}>
                NO NEWS
              </span>
            )}
            <div style={{ flex: 1 }} />
            {/* count — secondary */}
            <span style={{ ...chipBase, background: "var(--color-surface-2)", color: "var(--color-text-secondary)", border: "1px solid var(--color-border)" }}>
              {hasArticles ? `${articles24h.length} news` : "0 news"}
            </span>
            {/* dernier article — bleu */}
            <span style={{ ...chipBase, background: "rgba(90,140,220,0.10)", color: "#7BA4E0", border: "1px solid rgba(90,140,220,0.18)" }}>
              last news {shortAge(lastNewsMs)} ago
            </span>
            {/* check — freshness coloré */}
            <span style={{ ...chipBase, ...syncColor }}>
              ↻ {shortAge(owAgeMs)}
            </span>
          </div>

          {/* Résumé */}
          <p style={{ fontSize: "13px", color: "var(--color-text-secondary)", lineHeight: 1.6, margin: 0 }}>
            {noNews ? (digest.summary || "") : (digest.summary_24h || "Pas de résumé disponible.")}
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
          {(showAll ? articles24h : articles24h.slice(0, DAILY_LIMIT)).map((a, i) => (
            <ArticleRow
              key={i}
              title={a.title}
              source={a.source}
              pub_date={a.pub_date}
              url={a.url}
              dumb={a.dumb_en || a.dumb_fr}
            />
          ))}
          {articles24h.length > DAILY_LIMIT && (
            <button
              onClick={() => setShowAll(v => !v)}
              style={{
                width: "100%", padding: "8px", border: "none", background: "transparent",
                color: "var(--color-text-muted)", fontSize: "11px", fontWeight: 600,
                cursor: "pointer", letterSpacing: "0.04em",
              }}
            >
              {showAll ? "Voir moins ↑" : `+${articles24h.length - DAILY_LIMIT} articles`}
            </button>
          )}
        </div>
      )}
    </div>
  );
}

function WeeklyCard({ weekly }: { weekly: Weekly }) {
  const router = useRouter();
  const [expanded, setExpanded] = useState(false);
  const [showAll, setShowAll] = useState(false);
  const articles = weekly.top_articles ?? [];
  const hasArticles = articles.length > 0;

  const ageMs = weekly.cached_at ? Date.now() - new Date(weekly.cached_at).getTime() : Infinity;
  const dotClass = ageMs < 6 * 3600000 ? "fresh" : ageMs < 24 * 3600000 ? "medium" : "stale";

  const lastArticle = articles[0];
  const lastNewsMs = lastArticle?.pub_date
    ? Date.now() - new Date(lastArticle.pub_date).getTime()
    : Infinity;
  const metaLastArticle = shortAge(lastNewsMs);
  const metaChecked = shortAge(ageMs);

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
          ticker={weekly.ticker}
          website={weekly.website}
          onLogoClick={() => router.push(`/stock/${weekly.ticker}`)}
        />
        <div style={{ flex: 1, minWidth: 0 }}>
          {/* Ticker · badge 7J · chips */}
          <div style={{ display: "flex", alignItems: "center", gap: "7px", marginBottom: "8px" }}>
            <span style={{ fontFamily: "var(--font-mono)", fontWeight: 800, fontSize: "15px", color: "var(--color-text-primary)" }}>
              {weekly.ticker}
            </span>
            <div style={{ flex: 1 }} />
            {hasArticles && (
              <span style={{
                padding: "2px 7px", borderRadius: "4px", fontSize: "9px", fontWeight: 600,
                fontFamily: "var(--font-mono)", letterSpacing: "0.04em",
                background: "var(--color-surface-2)", color: "var(--color-text-secondary)", border: "1px solid var(--color-border)",
              }}>
                {articles.length} news
              </span>
            )}
            {hasArticles && (
              <span style={{
                padding: "2px 7px", borderRadius: "4px", fontSize: "9px", fontWeight: 600,
                fontFamily: "var(--font-mono)", letterSpacing: "0.04em",
                background: "rgba(90,140,220,0.10)", color: "#7BA4E0", border: "1px solid rgba(90,140,220,0.18)",
              }}>
                last news {metaLastArticle} ago
              </span>
            )}
            <span style={{
              padding: "2px 7px", borderRadius: "4px", fontSize: "9px", fontWeight: 600,
              fontFamily: "var(--font-mono)", letterSpacing: "0.04em",
              ...(ageMs < 6 * 3600000
                ? { background: "rgba(76,175,130,0.12)",  color: "#4CAF82", border: "1px solid rgba(76,175,130,0.2)" }
                : ageMs < 24 * 3600000
                ? { background: "rgba(245,197,24,0.08)",  color: "#C8960C", border: "1px solid rgba(245,197,24,0.15)" }
                : { background: "rgba(224,92,75,0.08)",   color: "#E05C4B", border: "1px solid rgba(224,92,75,0.15)" }),
            }}>
              ↻ {metaChecked}
            </span>
          </div>
          {/* Résumé */}
          <p style={{ fontSize: "13px", color: weekly.generating ? "var(--color-text-muted)" : "var(--color-text-secondary)", lineHeight: 1.6, margin: 0, fontStyle: weekly.generating ? "italic" : "normal" }}>
            {weekly.generating ? "Résumé IA en cours de génération…" : (weekly.ai_summary || "Résumé hebdomadaire non disponible.")}
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
          {(showAll ? articles : articles.slice(0, WEEKLY_LIMIT)).map((a, i) => (
            <ArticleRow
              key={i}
              title={a.title}
              source={a.source}
              pub_date={a.pub_date}
              url={a.url}
              dumb={a.dumb_en || a.dumb_fr}
            />
          ))}
          {articles.length > WEEKLY_LIMIT && (
            <button
              onClick={() => setShowAll(v => !v)}
              style={{
                width: "100%", padding: "8px", border: "none", background: "transparent",
                color: "var(--color-text-muted)", fontSize: "11px", fontWeight: 600,
                cursor: "pointer", letterSpacing: "0.04em",
              }}
            >
              {showAll ? "Voir moins ↑" : `+${articles.length - WEEKLY_LIMIT} articles`}
            </button>
          )}
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

      let firstArrived = false;
      await Promise.all(
        tickers.map((t) =>
          fetch(`${API}/api/news/${t}`)
            .then((r) => r.json())
            .then((d) => {
              if (d && d.ticker && d.summary_24h) {
                setDigests((prev) => [...prev, d]);
                if (!firstArrived) { firstArrived = true; setLoadingDaily(false); }
              }
            })
            .catch(() => null)
        )
      );
      setLoadingDaily(false);
    });
  }, []);

  async function fetchWeeklies() {
    if (weeklyFetched || pins.length === 0) return;
    setLoadingWeekly(true);
    let firstArrived = false;
    await Promise.all(
      pins.map((t) =>
        fetch(`${API}/api/weekly/${t}`)
          .then((r) => r.json())
          .then((w) => {
            if (w && w.ticker) {
              setWeeklies((prev) => [...prev, w]);
              if (!firstArrived) { firstArrived = true; setLoadingWeekly(false); }
            }
          })
          .catch(() => null)
      )
    );
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
              background: "var(--color-surface-2)",
              border: "1px solid var(--color-border)",
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
                    {new Set(digests.map(d => d.ticker)).size} action{new Set(digests.map(d => d.ticker)).size > 1 ? "s" : ""} · digest du jour
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
              {[...new Map(digests.map(d => [d.ticker, d])).values()]
                .sort((a, b) => {
                  const aMs = a.articles?.[0]?.pub_date ? new Date(a.articles[0].pub_date).getTime() : 0;
                  const bMs = b.articles?.[0]?.pub_date ? new Date(b.articles[0].pub_date).getTime() : 0;
                  return bMs - aMs;
                })
                .map((d, i) => <DigestCard key={d.ticker ?? i} digest={d} />)}
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
                  {new Set(weeklies.map(w => w.ticker)).size} action{new Set(weeklies.map(w => w.ticker)).size > 1 ? "s" : ""} · recap de la semaine
                </span>
                <span style={{ fontSize: "11px", color: "var(--color-text-muted)", fontFamily: "var(--font-mono)" }}>
                  {getCurrentWeekLabel()}
                </span>
              </div>
              {[...new Map(weeklies.map(w => [w.ticker, w])).values()]
                .sort((a, b) => {
                  const aMs = a.top_articles?.[0]?.pub_date ? new Date(a.top_articles[0].pub_date).getTime() : 0;
                  const bMs = b.top_articles?.[0]?.pub_date ? new Date(b.top_articles[0].pub_date).getTime() : 0;
                  return bMs - aMs;
                })
                .map((w, i) => <WeeklyCard key={w.ticker ?? i} weekly={w} />)}
            </>
          )
        )}
      </main>
    </div>
  );
}
