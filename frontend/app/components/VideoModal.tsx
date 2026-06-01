"use client";

import { useEffect } from "react";
import type { Article } from "./ArticleDrawer";

interface Props {
  article: Article | null;
  onClose: () => void;
}

export default function VideoModal({ article, onClose }: Props) {
  useEffect(() => {
    if (!article) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [article, onClose]);

  const visible = !!article;

  return (
    <div
      onClick={onClose}
      style={{
        position: "fixed",
        inset: 0,
        zIndex: 60,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        padding: "20px",
        background: "rgba(0,0,0,0.65)",
        opacity: visible ? 1 : 0,
        pointerEvents: visible ? "auto" : "none",
        transition: "opacity 0.2s",
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          background: "var(--color-surface)",
          border: "1px solid var(--color-border)",
          borderRadius: "12px",
          width: "min(560px, 100%)",
          overflow: "hidden",
          transform: visible ? "scale(1)" : "scale(0.92)",
          transition: "transform 0.25s cubic-bezier(0.32,0,0.15,1)",
        }}
      >
        {article && (
          <>
            {/* Thumbnail */}
            {article.thumbnail ? (
              <div style={{ position: "relative", aspectRatio: "16/9", background: "#000" }}>
                <img
                  src={article.thumbnail}
                  alt={article.title}
                  style={{ width: "100%", height: "100%", objectFit: "cover", opacity: 0.85 }}
                />
                {/* Play button overlay */}
                <div
                  style={{
                    position: "absolute",
                    inset: 0,
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                  }}
                >
                  <div
                    style={{
                      width: "56px",
                      height: "56px",
                      borderRadius: "50%",
                      background: "rgba(0,0,0,0.65)",
                      border: "2px solid rgba(255,255,255,0.7)",
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "center",
                    }}
                  >
                    <svg width="20" height="20" viewBox="0 0 24 24" fill="#fff">
                      <polygon points="5 3 19 12 5 21 5 3" />
                    </svg>
                  </div>
                </div>
                {/* Close */}
                <button
                  onClick={onClose}
                  style={{
                    position: "absolute",
                    top: "10px",
                    right: "10px",
                    background: "rgba(0,0,0,0.5)",
                    border: "none",
                    cursor: "pointer",
                    color: "#fff",
                    borderRadius: "50%",
                    width: "28px",
                    height: "28px",
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                  }}
                >
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
                    <path d="M18 6L6 18M6 6l12 12" />
                  </svg>
                </button>
              </div>
            ) : (
              <div style={{ height: "200px", background: "var(--color-surface-2)", display: "flex", alignItems: "center", justifyContent: "center", position: "relative" }}>
                <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="var(--color-text-muted)" strokeWidth="1.5">
                  <polygon points="5 3 19 12 5 21 5 3" />
                </svg>
                <button
                  onClick={onClose}
                  style={{
                    position: "absolute",
                    top: "10px",
                    right: "10px",
                    background: "none",
                    border: "none",
                    cursor: "pointer",
                    color: "var(--color-text-muted)",
                  }}
                >
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
                    <path d="M18 6L6 18M6 6l12 12" />
                  </svg>
                </button>
              </div>
            )}

            {/* Content */}
            <div style={{ padding: "20px" }}>
              <div style={{ fontSize: "11px", fontWeight: 700, letterSpacing: "0.06em", textTransform: "uppercase", color: "var(--color-text-muted)", marginBottom: "8px" }}>
                {article.source || "Video"}
              </div>
              <h3
                style={{
                  fontSize: "15px",
                  fontWeight: 700,
                  color: "var(--color-text-primary)",
                  lineHeight: 1.4,
                  marginBottom: "14px",
                  letterSpacing: "-0.02em",
                }}
              >
                {article.title}
              </h3>

              {/* AI Dumb */}
              {(article.dumb_en || article.dumb_fr) && (
                <div
                  style={{
                    background: "var(--color-accent-light)",
                    border: "1px solid rgba(76,175,130,0.25)",
                    borderRadius: "6px",
                    padding: "12px 14px",
                    marginBottom: "16px",
                  }}
                >
                  <span style={{ fontSize: "10px", fontWeight: 700, letterSpacing: "0.08em", textTransform: "uppercase", color: "var(--color-accent)", display: "block", marginBottom: "6px" }}>
                    AI Analysis
                  </span>
                  <p style={{ fontSize: "13px", color: "var(--color-text-primary)", lineHeight: 1.6, margin: 0 }}>
                    {article.dumb_en || article.dumb_fr}
                  </p>
                </div>
              )}

              {/* CTA */}
              {article.url && (
                <a
                  href={article.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  style={{
                    display: "inline-flex",
                    alignItems: "center",
                    gap: "6px",
                    padding: "9px 16px",
                    borderRadius: "6px",
                    background: "var(--color-accent)",
                    color: "#fff",
                    fontSize: "12px",
                    fontWeight: 600,
                    textDecoration: "none",
                  }}
                >
                  Watch on Yahoo Finance
                  <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
                    <path d="M18 13v6a2 2 0 01-2 2H5a2 2 0 01-2-2V8a2 2 0 012-2h6" />
                    <polyline points="15 3 21 3 21 9" />
                    <line x1="10" y1="14" x2="21" y2="3" />
                  </svg>
                </a>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
