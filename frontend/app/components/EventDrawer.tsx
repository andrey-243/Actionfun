"use client";

import { useState, useEffect } from "react";
import { createClient } from "../lib/supabase";
import DogLoader from "./DogLoader";

interface EventArticle {
  headline: string;
  source: string;
  url: string;
  datetime: number;
  summary: string;
}

interface EventData {
  date: string;
  articles: EventArticle[];
  cause: string | null;
}

interface Props {
  ticker: string;
  date: string;
  changePct: number;
  onClose: () => void;
  apiUrl: string;
}

export default function EventDrawer({ ticker, date, changePct, onClose, apiUrl }: Props) {
  const [data, setData] = useState<EventData | null>(null);
  const [loading, setLoading] = useState(true);
  const [isPinned, setIsPinned] = useState(false);
  const [pinLoading, setPinLoading] = useState(false);
  const [userId, setUserId] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    fetch(`${apiUrl}/api/events/${ticker}?date=${date}&change_pct=${changePct}`)
      .then((r) => r.json())
      .then((d) => { setData(d); setLoading(false); })
      .catch(() => setLoading(false));

    // Check pin status
    const supa = createClient();
    if (!supa) return;
    supa.auth.getUser().then(async ({ data: authData }) => {
      const uid = authData.user?.id;
      if (!uid) return;
      setUserId(uid);
      const { data: pin } = await supa.from("pinned_stocks").select("ticker").eq("user_id", uid).eq("ticker", ticker).maybeSingle();
      setIsPinned(!!pin);
    });
  }, [ticker, date]);

  async function handlePin() {
    const supa = createClient();
    if (!supa || !userId) return;
    const newPinned = !isPinned;
    setIsPinned(newPinned); // optimistic
    setPinLoading(true);
    if (!newPinned) {
      const { error } = await supa.from("pinned_stocks").delete().eq("user_id", userId).eq("ticker", ticker);
      if (error) setIsPinned(!newPinned);
    } else {
      const { error } = await supa.from("pinned_stocks").upsert(
        { user_id: userId, ticker, company_name: ticker, pinned_at: new Date().toISOString() },
        { onConflict: "user_id,ticker" }
      );
      if (error) setIsPinned(!newPinned);
    }
    setPinLoading(false);
  }

  const isPositive = changePct >= 0;

  return (
    <>
      {/* Backdrop */}
      <div
        onClick={onClose}
        style={{
          position: "fixed", inset: 0, zIndex: 40,
          background: "rgba(0,0,0,0.4)", backdropFilter: "blur(2px)",
        }}
      />

      {/* Drawer */}
      <div style={{
        position: "fixed", top: 0, right: 0, bottom: 0, zIndex: 50,
        width: "min(420px, 100vw)",
        background: "var(--color-surface)",
        borderLeft: "1px solid var(--color-border)",
        display: "flex", flexDirection: "column",
        boxShadow: "-20px 0 60px rgba(0,0,0,0.3)",
      }}>
        {/* Header */}
        <div style={{
          padding: "18px 20px", borderBottom: "1px solid var(--color-border)",
          display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: "12px",
        }}>
          <div>
            <div style={{ fontSize: "11px", color: "var(--color-text-muted)", letterSpacing: "0.04em", marginBottom: "4px" }}>
              {date}
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
              <span style={{ fontSize: "15px", fontWeight: 700, fontFamily: "var(--font-mono)", color: "var(--color-text-primary)" }}>
                {ticker}
              </span>
              <span style={{
                padding: "2px 8px", borderRadius: "4px", fontSize: "12px", fontWeight: 700,
                fontFamily: "var(--font-mono)",
                background: isPositive ? "var(--color-positive-bg)" : "var(--color-negative-bg)",
                color: isPositive ? "var(--color-positive)" : "var(--color-negative)",
              }}>
                {isPositive ? "▲" : "▼"} {Math.abs(changePct).toFixed(2)}%
              </span>
            </div>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
            {userId && (
              <button
                onClick={handlePin}
                disabled={pinLoading}
                title={isPinned ? "Retirer des favoris" : "Ajouter aux favoris"}
                style={{
                  background: "none", border: "none", cursor: "pointer", padding: "4px",
                  color: isPinned ? "var(--color-accent)" : "var(--color-text-muted)",
                  opacity: pinLoading ? 0.5 : 1,
                }}
              >
                <svg width="18" height="18" viewBox="0 0 24 24" fill={isPinned ? "currentColor" : "none"} stroke="currentColor" strokeWidth="2">
                  <polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2" />
                </svg>
              </button>
            )}
            <button
              onClick={onClose}
              style={{ background: "none", border: "none", cursor: "pointer", color: "var(--color-text-muted)", padding: "4px" }}
            >
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M18 6L6 18M6 6l12 12" />
              </svg>
            </button>
          </div>
        </div>

        {/* Content */}
        <div style={{ flex: 1, overflowY: "auto", padding: "16px 20px" }}>
          {loading ? (
            <DogLoader text="Analyse en cours…" />
          ) : !data || !data.articles || data.articles.length === 0 ? (
            <div style={{ textAlign: "center", padding: "40px 0", color: "var(--color-text-muted)", fontSize: "13px" }}>
              Pas de news disponible pour cette période.
            </div>
          ) : (
            <>
              {/* Cause IA */}
              {data.cause && (
                <div style={{
                  background: "var(--color-surface-2)", border: "1px solid var(--color-border)",
                  borderRadius: "8px", padding: "14px 16px", marginBottom: "20px",
                }}>
                  <p style={{ fontSize: "13px", color: "var(--color-text-primary)", lineHeight: 1.6, margin: "0 0 10px" }}>
                    {data.cause}
                  </p>
                  <span style={{ fontSize: "10px", color: "var(--color-text-muted)", fontStyle: "italic" }}>
                    Analyse IA — pas un conseil d'investissement
                  </span>
                </div>
              )}

              {/* Articles */}
              <div style={{ fontSize: "11px", fontWeight: 700, letterSpacing: "0.06em", textTransform: "uppercase", color: "var(--color-text-muted)", marginBottom: "10px" }}>
                {data.articles.length} articles
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: "1px" }}>
                {data.articles.map((a, i) => (
                  <a
                    key={i}
                    href={a.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    style={{
                      display: "block", padding: "12px 14px", borderRadius: "6px",
                      textDecoration: "none", transition: "background 0.1s",
                    }}
                    onMouseEnter={(e) => (e.currentTarget.style.background = "var(--color-surface-2)")}
                    onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
                  >
                    <div style={{ fontSize: "13px", fontWeight: 500, color: "var(--color-text-primary)", lineHeight: 1.4, marginBottom: "4px" }}>
                      {a.headline}
                    </div>
                    <div style={{ fontSize: "11px", color: "var(--color-text-muted)" }}>
                      {a.source} · {new Date(a.datetime * 1000).toLocaleDateString("fr-FR", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })}
                    </div>
                  </a>
                ))}
              </div>
            </>
          )}
        </div>
      </div>
    </>
  );
}
