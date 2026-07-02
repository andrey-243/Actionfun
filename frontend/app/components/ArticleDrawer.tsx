"use client";

import { useEffect } from "react";

export interface Article {
  id: string;
  type: string;
  title: string;
  summary: string;
  dumb_en?: string;
  dumb_fr?: string;
  source: string;
  url: string;
  thumbnail: string;
  pub_date: string;
  extra_sources?: { name: string; url: string }[];
}

const SOURCE_COLORS: Record<string, { bg: string; text: string }> = {
  "yahoo":        { bg: "#6001D2", text: "#fff" },
  "bloomberg":    { bg: "#1B1B1B", text: "#fff" },
  "reuters":      { bg: "#FF6200", text: "#fff" },
  "benzinga":     { bg: "#F79122", text: "#fff" },
  "zacks":        { bg: "#0066CC", text: "#fff" },
  "marketwatch":  { bg: "#00AC4E", text: "#fff" },
  "cnbc":         { bg: "#003087", text: "#fff" },
  "seekingalpha": { bg: "#1F7A4A", text: "#fff" },
  "24/7 wall st.":{ bg: "#8B0000", text: "#fff" },
  "oilprice.com": { bg: "#2E6B57", text: "#fff" },
  "barrons":      { bg: "#C8102E", text: "#fff" },
  "wsj":          { bg: "#004B87", text: "#fff" },
  "ft":           { bg: "#FCD0B1", text: "#000" },
};

export function sourceStyle(name: string): { bg: string; text: string } {
  const key = name.toLowerCase().replace(/\s+/g, "");
  for (const [k, v] of Object.entries(SOURCE_COLORS)) {
    if (key.includes(k.replace(/\s+/g, "").replace(/[./]/g, ""))) return v;
  }
  // Deterministic HSL hash — 360 hues, no collisions in practice
  const hash = name.split("").reduce((acc, c) => (acc * 31 + c.charCodeAt(0)) & 0xffffffff, 0);
  const hue = Math.abs(hash) % 360;
  return { bg: `hsl(${hue}, 55%, 38%)`, text: "#fff" };
}

function SourceBadge({ name }: { name: string }) {
  const { bg, text } = sourceStyle(name);
  return (
    <span style={{
      padding: "2px 7px", borderRadius: "3px", fontSize: "10px", fontWeight: 700,
      letterSpacing: "0.05em", textTransform: "uppercase",
      background: bg, color: text,
    }}>
      {name}
    </span>
  );
}

function ReadLink({ url, source, primary }: { url: string; source: string; primary?: boolean }) {
  const { bg, text } = sourceStyle(source);
  return (
    <a
      href={url}
      target="_blank"
      rel="noopener noreferrer"
      style={{
        display: "inline-flex", alignItems: "center", gap: "8px",
        padding: "8px 14px", borderRadius: "6px", textDecoration: "none",
        fontSize: "12px", fontWeight: 600,
        background: primary ? bg : "var(--color-surface-2)",
        color: primary ? text : "var(--color-text-secondary)",
        border: primary ? "none" : "1px solid var(--color-border)",
      }}
    >
      <span>{source}</span>
      <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
        <path d="M18 13v6a2 2 0 01-2 2H5a2 2 0 01-2-2V8a2 2 0 012-2h6" />
        <polyline points="15 3 21 3 21 9" /><line x1="10" y1="14" x2="21" y2="3" />
      </svg>
    </a>
  );
}

interface Props {
  article: Article | null;
  onClose: () => void;
}

export default function ArticleDrawer({ article, onClose }: Props) {
  useEffect(() => {
    if (!article) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [article, onClose]);

  const visible = !!article;

  return (
    <>
      {/* Backdrop */}
      <div
        onClick={onClose}
        style={{
          position: "fixed",
          inset: 0,
          background: "rgba(0,0,0,0.45)",
          zIndex: 40,
          opacity: visible ? 1 : 0,
          pointerEvents: visible ? "auto" : "none",
          transition: "opacity 0.25s",
        }}
      />

      {/* Drawer */}
      <div
        style={{
          position: "fixed",
          top: 0,
          right: 0,
          bottom: 0,
          width: "min(480px, 100vw)",
          background: "var(--color-surface)",
          borderLeft: "1px solid var(--color-border)",
          zIndex: 50,
          transform: visible ? "translateX(0)" : "translateX(100%)",
          transition: "transform 0.3s cubic-bezier(0.32,0,0.15,1)",
          display: "flex",
          flexDirection: "column",
          overflowY: "auto",
        }}
      >
        {article && (
          <>
            {/* Header */}
            <div
              style={{
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                padding: "16px 20px",
                borderBottom: "1px solid var(--color-border)",
                position: "sticky",
                top: 0,
                background: "var(--color-surface)",
                zIndex: 1,
              }}
            >
              <div style={{ display: "flex", alignItems: "center", gap: "6px", flexWrap: "wrap" }}>
                <SourceBadge name={article.source} />
                {(article.extra_sources || []).filter(s => s.name !== article.source).map((s, i) => (
                  <SourceBadge key={i} name={s.name} />
                ))}
              </div>
              <button
                onClick={onClose}
                style={{ background: "none", border: "none", cursor: "pointer", color: "var(--color-text-muted)", padding: "4px" }}
              >
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
                  <path d="M18 6L6 18M6 6l12 12" />
                </svg>
              </button>
            </div>

            <div style={{ padding: "24px 20px", flex: 1 }}>
              {/* Title */}
              <h2
                style={{
                  fontSize: "16px",
                  fontWeight: 700,
                  color: "var(--color-text-primary)",
                  lineHeight: 1.4,
                  marginBottom: "20px",
                  letterSpacing: "-0.02em",
                }}
              >
                {article.title}
              </h2>

              {/* AI Dumb */}
              {(article.dumb_en || article.dumb_fr) && (
                <div
                  style={{
                    background: "var(--color-accent-light)",
                    border: "1px solid rgba(76,175,130,0.25)",
                    borderRadius: "8px",
                    padding: "14px 16px",
                    marginBottom: "20px",
                  }}
                >
                  <div style={{ display: "flex", alignItems: "center", gap: "6px", marginBottom: "8px" }}>
                    <div
                      style={{
                        width: "16px",
                        height: "16px",
                        borderRadius: "3px",
                        background: "var(--color-accent)",
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "center",
                      }}
                    >
                      <svg width="9" height="9" viewBox="0 0 24 24" fill="none" stroke="#fff" strokeWidth="3">
                        <path d="M12 2L2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5" />
                      </svg>
                    </div>
                    <span style={{ fontSize: "10px", fontWeight: 700, letterSpacing: "0.08em", textTransform: "uppercase", color: "var(--color-accent)" }}>
                      AI Analysis
                    </span>
                  </div>
                  <p style={{ fontSize: "13px", color: "var(--color-text-primary)", lineHeight: 1.6, margin: 0 }}>
                    {article.dumb_en || article.dumb_fr}
                  </p>
                </div>
              )}

              {/* Raw summary */}
              <div style={{ marginBottom: "24px" }}>
                <span style={{ fontSize: "11px", fontWeight: 700, letterSpacing: "0.06em", textTransform: "uppercase", color: "var(--color-text-muted)", display: "block", marginBottom: "8px" }}>
                  Source summary
                </span>
                <p style={{ fontSize: "13px", color: "var(--color-text-secondary)", lineHeight: 1.7, margin: 0 }}>
                  {article.summary}
                </p>
              </div>

              {/* Date */}
              {article.pub_date && (
                <div style={{ fontSize: "11px", color: "var(--color-text-muted)", marginBottom: "24px" }}>
                  {new Date(article.pub_date).toLocaleDateString("en-US", { year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })}
                </div>
              )}

              {/* CTA — source principale + sources alternatives */}
              {article.url && (
                <div style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
                  <ReadLink url={article.url} source={article.source} primary />
                  {(article.extra_sources || []).filter(s => s.name !== article.source).map((s, i) => (
                    <ReadLink key={i} url={s.url} source={s.name} />
                  ))}
                </div>
              )}
            </div>
          </>
        )}
      </div>
    </>
  );
}
