"use client";

import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import { createClient } from "../lib/supabase";
import type { User } from "../lib/supabase";

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

interface DigestArticle {
  headline: string;
  source: string;
  url: string;
  datetime: number;
}

interface Digest {
  ticker: string;
  date: string;
  summary: string;
  sentiment: string;
  articles: DigestArticle[];
  website?: string | null;
}

interface WeeklyArticle {
  title: string;
  url: string;
  source?: string;
  date?: string;
}

interface Weekly {
  ticker: string;
  year_month: string | null;
  week_num: number | null;
  ai_summary: string;
  top_articles: WeeklyArticle[];
  website?: string | null;
}

const SENTIMENT_STYLE: Record<string, { bg: string; color: string }> = {
  BULLISH: { bg: "var(--color-positive-bg)", color: "var(--color-positive)" },
  BEARISH: { bg: "var(--color-negative-bg)", color: "var(--color-negative)" },
  NEUTRAL: { bg: "var(--color-surface-2)", color: "var(--color-text-muted)" },
};

const LOGODEV_TOKEN = process.env.NEXT_PUBLIC_LOGODEV_TOKEN ?? "";

function TickerLogo({ ticker, website }: { ticker?: string | null; website?: string | null }) {
  const [failed, setFailed] = useState(false);
  const src = !failed && website
    ? `https://img.logo.dev/${website}?token=${LOGODEV_TOKEN}&size=40`
    : null;

  const fallbackLabel = ticker ? ticker.slice(0, 3) : "?";

  if (!src) {
    return (
      <div style={{
        width: "32px", height: "32px", borderRadius: "8px", flexShrink: 0,
        background: "var(--color-surface-2)", border: "1px solid var(--color-border)",
        display: "flex", alignItems: "center", justifyContent: "center",
        fontFamily: "var(--font-mono)", fontWeight: 700, fontSize: "10px",
        color: "var(--color-text-muted)", letterSpacing: "0.02em",
      }}>
        {fallbackLabel}
      </div>
    );
  }
  return (
    <img
      src={src}
      alt={ticker}
      onError={() => setFailed(true)}
      style={{ width: "32px", height: "32px", borderRadius: "8px", flexShrink: 0, objectFit: "contain", background: "#fff" }}
    />
  );
}

function DigestCard({ digest }: { digest: Digest }) {
  const [expanded, setExpanded] = useState(false);
  const s = SENTIMENT_STYLE[digest.sentiment] ?? SENTIMENT_STYLE.NEUTRAL;

  return (
    <div style={{
      background: "var(--color-surface)", border: "1px solid var(--color-border)",
      borderRadius: "10px", overflow: "hidden", marginBottom: "16px",
    }}>
      <div
        onClick={() => setExpanded((v) => !v)}
        style={{ padding: "16px 18px", cursor: "pointer", display: "flex", alignItems: "flex-start", gap: "12px" }}
      >
        <TickerLogo ticker={digest.ticker} website={digest.website} />
        <div style={{ flex: 1 }}>
          <div style={{ display: "flex", alignItems: "center", gap: "8px", marginBottom: "8px" }}>
            <span style={{ fontFamily: "var(--font-mono)", fontWeight: 800, fontSize: "15px", color: "var(--color-text-primary)" }}>
              {digest.ticker}
            </span>
            <span style={{
              padding: "2px 8px", borderRadius: "4px", fontSize: "10px", fontWeight: 700,
              fontFamily: "var(--font-mono)", letterSpacing: "0.06em",
              background: s.bg, color: s.color,
            }}>
              {digest.sentiment}
            </span>
          </div>
          <p style={{ fontSize: "13px", color: "var(--color-text-secondary)", lineHeight: 1.6, margin: 0 }}>
            {digest.summary || "Pas de résumé disponible."}
          </p>
        </div>
        <svg
          width="14" height="14" viewBox="0 0 24 24" fill="none"
          stroke="var(--color-text-muted)" strokeWidth="2"
          style={{ flexShrink: 0, marginTop: "4px", transform: expanded ? "rotate(180deg)" : "none", transition: "transform 0.2s" }}
        >
          <polyline points="6 9 12 15 18 9" />
        </svg>
      </div>

      {expanded && (digest.articles ?? []).length > 0 && (
        <div style={{ borderTop: "1px solid var(--color-border)" }}>
          {(digest.articles ?? []).map((a, i) => (
            <a
              key={i}
              href={a.url}
              target="_blank"
              rel="noopener noreferrer"
              style={{
                display: "block", padding: "11px 18px", textDecoration: "none",
                borderBottom: i < digest.articles.length - 1 ? "1px solid var(--color-border)" : "none",
              }}
              onMouseEnter={(e) => (e.currentTarget.style.background = "var(--color-surface-2)")}
              onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
            >
              <div style={{ fontSize: "13px", color: "var(--color-text-primary)", fontWeight: 500, marginBottom: "3px" }}>
                {a.headline}
              </div>
              <div style={{ fontSize: "11px", color: "var(--color-text-muted)" }}>
                {a.source} · {new Date(a.datetime * 1000).toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" })}
              </div>
            </a>
          ))}
        </div>
      )}
    </div>
  );
}

function WeeklyCard({ weekly }: { weekly: Weekly }) {
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
        onClick={() => setExpanded((v) => !v)}
        style={{ padding: "16px 18px", cursor: "pointer", display: "flex", alignItems: "flex-start", gap: "12px" }}
      >
        <TickerLogo ticker={weekly.ticker} website={weekly.website} />
        <div style={{ flex: 1 }}>
          <div style={{ display: "flex", alignItems: "center", gap: "8px", marginBottom: "8px" }}>
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
          </div>
          <p style={{ fontSize: "13px", color: "var(--color-text-secondary)", lineHeight: 1.6, margin: 0 }}>
            {weekly.ai_summary || "Résumé hebdomadaire non disponible."}
          </p>
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
              <div style={{ fontSize: "13px", color: "var(--color-text-primary)", fontWeight: 500, marginBottom: "3px" }}>
                {a.title}
              </div>
              {(a.source || a.date) && (
                <div style={{ fontSize: "11px", color: "var(--color-text-muted)" }}>
                  {[a.source, a.date].filter(Boolean).join(" · ")}
                </div>
              )}
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
  const [user, setUser] = useState<User | null>(null);
  const [pins, setPins] = useState<string[]>([]);
  const [tab, setTab] = useState<Tab>("daily");
  const [digests, setDigests] = useState<Digest[]>([]);
  const [weeklies, setWeeklies] = useState<Weekly[]>([]);
  const [loadingDaily, setLoadingDaily] = useState(true);
  const [loadingWeekly, setLoadingWeekly] = useState(false);
  const [weeklyFetched, setWeeklyFetched] = useState(false);

  useEffect(() => {
    const supa = createClient();
    if (!supa) { setLoadingDaily(false); return; }

    supa.auth.getUser().then(async ({ data }) => {
      const u = data.user;
      setUser(u);
      if (!u) { setLoadingDaily(false); return; }

      const { data: pinData } = await supa
        .from("pinned_stocks")
        .select("ticker")
        .eq("user_id", u.id);

      const tickers = (pinData ?? []).map((r: { ticker: string }) => r.ticker);
      setPins(tickers);

      if (tickers.length === 0) { setLoadingDaily(false); return; }

      const results = await Promise.all(
        tickers.map((t) =>
          fetch(`${API}/api/digest/${t}`)
            .then((r) => r.json())
            .catch(() => null)
        )
      );
      setDigests(results.filter((d): d is Digest => d !== null && !!d.ticker && d.summary !== ""));
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

  const TAB_STYLE = (active: boolean): React.CSSProperties => ({
    padding: "6px 16px",
    borderRadius: "6px",
    fontSize: "13px",
    fontWeight: 600,
    cursor: "pointer",
    border: "none",
    background: active ? "var(--color-accent)" : "transparent",
    color: active ? "#fff" : "var(--color-text-muted)",
    transition: "background 0.15s, color 0.15s",
  });

  return (
    <div style={{ minHeight: "100vh", background: "var(--color-bg)" }}>
      <header style={{
        background: "var(--color-surface)", borderBottom: "1px solid var(--color-border)",
        position: "sticky", top: 0, zIndex: 10,
      }}>
        <div style={{
          maxWidth: "720px", margin: "0 auto", padding: "0 20px",
          height: "52px", display: "flex", alignItems: "center", gap: "12px",
        }}>
          <button
            onClick={() => router.push("/")}
            style={{ background: "none", border: "none", cursor: "pointer", color: "var(--color-text-muted)", padding: "4px", display: "flex" }}
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
              <path d="M19 12H5M12 19l-7-7 7-7" />
            </svg>
          </button>
          <span style={{ fontWeight: 700, fontSize: "15px", color: "var(--color-text-primary)", letterSpacing: "-0.02em" }}>
            Mes News
          </span>
          <div style={{ display: "flex", gap: "4px", marginLeft: "8px" }}>
            <button style={TAB_STYLE(tab === "daily")} onClick={() => handleTab("daily")}>Daily</button>
            <button style={TAB_STYLE(tab === "weekly")} onClick={() => handleTab("weekly")}>Weekly</button>
          </div>
          {!loadingDaily && tab === "daily" && digests.length > 0 && (
            <span style={{
              marginLeft: "auto", fontSize: "11px", color: "var(--color-text-muted)",
              fontFamily: "var(--font-mono)",
            }}>
              {new Date().toLocaleDateString("fr-FR", { weekday: "long", day: "numeric", month: "long" })}
            </span>
          )}
        </div>
      </header>

      <main style={{ maxWidth: "720px", margin: "0 auto", padding: "24px 20px 80px" }}>
        {!user ? (
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
              <div style={{ fontSize: "11px", fontWeight: 700, letterSpacing: "0.06em", textTransform: "uppercase", color: "var(--color-text-muted)", marginBottom: "16px" }}>
                {digests.length} action{digests.length > 1 ? "s" : ""} · digest du jour
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
              <div style={{ fontSize: "11px", fontWeight: 700, letterSpacing: "0.06em", textTransform: "uppercase", color: "var(--color-text-muted)", marginBottom: "16px" }}>
                {weeklies.length} action{weeklies.length > 1 ? "s" : ""} · recap de la semaine
              </div>
              {weeklies.map((w, i) => <WeeklyCard key={w.ticker ?? i} weekly={w} />)}
            </>
          )
        )}
      </main>
    </div>
  );
}
